"""
媒体下载模块 - 下载图片 / live图 / 视频到本地

支持多线程下载（threads 参数，2/4/8/16/32/64）：
- 单文件且为大文件（视频/Live图）：threads = 分段数，文件拆 N 段各自独立
  Range 流并行下载（IDM 风格），写 .idmpart 临时文件完成后原子改名；
- 多文件（批量）：threads = 并行文件数，N 个文件同时下载（每个单流）。
"""

import os
import random
import re
import threading
import time
from urllib.parse import urlparse

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "download")

# 平台英文标识 -> 中文文件夹名（公共映射见 toolbox.utils.PLATFORM_CN）
from toolbox.utils import PLATFORM_CN as _PLATFORM_CN, MEDIA_DIR_SUFFIX


def _sanitize_filename(name: str, max_len: int = 60) -> str:
    """安全文件名（统一实现见 toolbox.utils.sanitize_filename）"""
    from toolbox.utils import sanitize_filename
    return sanitize_filename(name, max_len, "untitled")


def _ext_guess(content_type: str, url: str) -> str:
    """根据 Content-Type 或 URL 推断文件扩展名"""
    ct_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
    }
    for ct, ext in ct_map.items():
        if ct in content_type:
            return ext
    # 从 URL 推断
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".webm", ".heic"):
        if path.endswith(ext):
            return ext
    return ".jpg"  # 默认


def _is_direct_media_url(url: str) -> bool:
    """判断是否为真实媒体直链（视频/图片 CDN 直链），排除网页跳转链接。

    multi_downloader 返回的 video_url 是各平台 CDN 直链（douyinvod/xhscdn 等，
    含 .mp4 等扩展名）；而网页链接（如 douyin.com/video/xxx）不是直链。
    """
    if not url:
        return False
    low = url.lower()
    if any(k in low for k in (".mp4", ".mov", ".flv", ".m3u8", ".ts", ".webm")):
        return True
    if any(k in low for k in ("douyinvod", "xhscdn", "weibocdn", "livephoto")):
        return True
    return False


def _resolve_livephoto(url: str, headers: dict) -> str:
    """live 图视频（livephoto.us.sinaimg.cn）需先访问播放器页，拿 302 跳转后的带签名直链。

    无签名的 .mov 直接下载会 403（新浪 CDN 防盗链），播放器页会返回
    ?Expires=...&ssig=...&KID=unistore,video 的实时有效地址。
    """
    if "livephoto.us.sinaimg.cn" not in url or "ssig=" in url:
        return url
    from urllib.parse import quote
    player = f"https://video.weibo.com/media/play?livephoto={quote(url, safe='')}"
    try:
        r = httpx.get(player, headers=headers, timeout=20, follow_redirects=False)
        loc = r.headers.get("location", "")
        if loc:
            return loc
    except Exception:
        pass
    return url


_SINAIMG_TIER_RE = re.compile(r"(https?://w[a-z]\d*\.sinaimg\.cn/)([a-z0-9]+)/", re.IGNORECASE)


def _url_path(url: str) -> str:
    """取 URL 的路径部分（去 query/fragment，供扩展名判断）。"""
    from urllib.parse import urlparse
    try:
        return urlparse(url).path
    except Exception:
        return ""


def _upgrade_sinaimg(url: str) -> str:
    """sinaimg 图片 URL 升级为原图（large 档）。

    m.weibo.cn 接口给的常是 mw2000/orj1080 等压缩档（1.2MB vs 原图 12MB+），
    large 档才是原始上传文件。livephoto.us.sinaimg.cn（视频）不匹配此正则，不受影响。
    """
    if "sinaimg.cn" not in url:
        return url
    return _SINAIMG_TIER_RE.sub(r"\1large/", url, count=1)


_MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
               ".mp4", ".mov", ".webm")


def _find_existing_media(filepath: str) -> str | None:
    """目标路径或同 stem 其他媒体扩展名的已有文件。

    _download_one 会按 Content-Type 改扩展名（.jpg 存成 .png 等），
    判重需覆盖同 stem 任意媒体扩展，否则改名过的文件每次都被重下。
    """
    if os.path.exists(filepath):
        return filepath
    d, base = os.path.split(filepath)
    if "." not in base:
        return None
    stem = base.rsplit(".", 1)[0]
    try:
        for fn in os.listdir(d):
            if fn.startswith(stem + ".") and \
                    os.path.splitext(fn)[1].lower() in _MEDIA_EXTS:
                return os.path.join(d, fn)
    except OSError:
        pass
    return None


def _remote_size(url: str, headers: dict) -> int | None:
    """HEAD 探测远端文件大小（新浪图床已验证支持），失败返回 None。"""
    try:
        r = httpx.head(url, headers=headers, timeout=20, follow_redirects=True)
        if r.status_code == 200:
            cl = r.headers.get("content-length", "")
            if cl.isdigit():
                return int(cl)
    except Exception:
        pass
    return None


def _should_skip_download(url: str, filepath: str, headers: dict) -> bool:
    """已下载自动跳过（仅原图场景）。

    微博图片采集存的是 mw2000/orj1080 压缩档，_download_one 每次都升级成
    large 原图并把旧文件当压缩版删除重下——重复下载同一批图时全部重来。
    本地已有文件（含同 stem 其他扩展名）大小与原图一致，说明上次已经
    下过原图，直接跳过，不再发完整下载请求。
    """
    up = _upgrade_sinaimg(url)
    if up == url:
        return False  # 无原图升级语义（抖音/B站等），维持原有 Range/416 快速跳过
    local = _find_existing_media(filepath)
    if not local:
        return False
    try:
        size = os.path.getsize(local)
    except OSError:
        return False
    remote = _remote_size(up, headers)
    return remote is not None and remote == size


# ==================== 多线程分段下载（IDM 风格） ====================

# 小于 2MB 的文件分段收益低于握手开销，直接单流
SEG_MIN_FILE_SIZE = 2 * 1024 * 1024
# 每段至少 256KB（避免线程数 > 文件大小能切出的合理段数）
SEG_MIN_CHUNK = 256 * 1024


def _probe_range(url: str, headers: dict):
    """GET bytes=0-0 试探远端是否支持 Range 分段与真实总大小。

    返回 (total, supports_range, content_type)；探测失败返回 (None, False, "")。
    HEAD 在部分 CDN（新浪图床）上行为不一致，Range 试探最可靠。
    """
    h = dict(headers)
    h["Range"] = "bytes=0-0"
    try:
        r = httpx.get(url, headers=h, timeout=20, follow_redirects=True)
        ct = r.headers.get("content-type", "")
        if r.status_code == 206:
            cr = r.headers.get("content-range", "")  # "bytes 0-0/12345"
            total = int(cr.rsplit("/", 1)[-1]) if "/" in cr else None
            if total and total > 0:
                return total, True, ct
        if r.status_code == 200:
            cl = r.headers.get("content-length", "")
            return (int(cl) if cl.isdigit() else None), False, ct
    except Exception:
        pass
    return None, False, ""


def _download_ranged(url: str, filepath: str, headers: dict, threads: int,
                     total: int, progress_cb=None, seg_progress_cb=None,
                     cancel_check=None) -> tuple[str, bool]:
    """分段并行下载单个文件（IDM 风格核心）。

    - 文件均分为 threads 段，每段独立 Range 流写同一 .idmpart 临时文件
      的各自偏移（预分配 truncate，各段独立文件句柄 seek 写，无锁竞争）；
    - 全部段完成后原子改名（os.replace）为正式文件；中途取消/失败删除
      .part —— 分段半截文件中间有洞，不能走单流 Range 续传，完整重下最稳；
    - 段级重试 3 次（网络抖动只重下该段，从段内断点续）；
    - progress_cb(done, total)：全文件聚合字节进度（约 5 次/秒）；
    - seg_progress_cb(seg_idx, seg_done, seg_total)：单段进度（GUI 分段条）。
    返回 (filepath, success)。
    """
    part_path = filepath + ".idmpart"
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        # 预分配占位（各段按偏移覆写）
        with open(part_path, "wb") as f:
            f.truncate(total)
        base, rem = divmod(total, threads)
        segs = []
        start = 0
        for i in range(threads):
            size = base + (1 if i < rem else 0)
            segs.append((i, start, start + size - 1))
            start += size
        seg_done = [0] * threads
        lock = threading.Lock()
        stop = [False]
        last_t = [0.0]

        def _agg():
            done = sum(seg_done)
            now_t = time.monotonic()
            if progress_cb is not None and now_t - last_t[0] >= 0.2:
                progress_cb(done, total)
                last_t[0] = now_t

        def _seg_worker(i: int, s: int, e: int) -> bool:
            seg_total = e - s + 1
            for _attempt in range(3):  # 段级重试：从段内断点续下
                if stop[0] or (cancel_check is not None and cancel_check()):
                    return False
                pos = s + seg_done[i]
                h = dict(headers)
                h["Range"] = f"bytes={pos}-{e}"
                try:
                    with httpx.Client(timeout=60, follow_redirects=True,
                                      headers=h) as client:
                        with client.stream("GET", url) as resp:
                            resp.raise_for_status()
                            with open(part_path, "r+b") as f:
                                f.seek(pos)
                                for chunk in resp.iter_bytes(chunk_size=262144):
                                    if stop[0] or (cancel_check is not None
                                                   and cancel_check()):
                                        return False
                                    f.write(chunk)
                                    with lock:
                                        seg_done[i] += len(chunk)
                                        if seg_progress_cb is not None:
                                            seg_progress_cb(
                                                i, seg_done[i], seg_total)
                                    _agg()
                    if seg_done[i] >= seg_total:
                        return True
                except Exception:
                    continue  # 网络抖动 → 从段内断点重试
            return False

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futs = [pool.submit(_seg_worker, i, s, e) for i, s, e in segs]
            ok = all(f.result() for f in futs)
        if progress_cb is not None:
            progress_cb(sum(seg_done), total)
        if ok:
            os.replace(part_path, filepath)
            return filepath, True
        stop[0] = True
        try:
            os.remove(part_path)
        except OSError:
            pass
        return filepath, False
    except Exception:
        try:
            os.remove(part_path)
        except OSError:
            pass
        return filepath, False


def _download_one(url: str, filepath: str, headers: dict,
                  progress_cb=None, cancel_check=None, threads: int = 1,
                  seg_progress_cb=None) -> tuple[str, bool]:
    """下载单个文件，返回 (filepath, success)。

    流式写盘（边下边写，不整包进内存——大视频整包缓冲会造成内存暴涨/卡顿），
    progress_cb(done_bytes, total_bytes) 约 5 次/秒回报字节进度（无 content-length 时 total=0），
    cancel_check() 返回 True 时中止下载（半截文件保留，下次 Range 续传）。

    threads > 1 且为全新下载且远端支持 Range 且文件足够大时，走 IDM 风格
    分段并行下载（_download_ranged），seg_progress_cb 回报每段进度；
    其余情况（threads=1 / 半截续传 / 不支持 Range / 文件太小）回退单流。
    """
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        # live 图：先解析出带签名的实时直链
        url = _resolve_livephoto(url, headers)
        # 微博图片：压缩档升级为原图
        url2 = _upgrade_sinaimg(url)
        upgraded = url2 != url
        url = url2
        # 断点续传：文件已存在则用 Range 请求续传
        h = dict(headers)
        existing = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        # 多线程分段模式：仅在全新下载时启用（半截文件必须走单流续传）
        if threads > 1 and existing == 0:
            total, rng_ok, ct = _probe_range(url, headers)
            if (rng_ok and total and total >= SEG_MIN_FILE_SIZE
                    and total // threads >= SEG_MIN_CHUNK):
                # 扩展名按探测到的 Content-Type 先定好（分段完成后不再改名）
                ext = _ext_guess(ct, url)
                if not filepath.endswith(ext):
                    filepath = filepath.rsplit(".", 1)[0] + ext
                return _download_ranged(
                    url, filepath, headers, threads, total,
                    progress_cb=progress_cb, seg_progress_cb=seg_progress_cb,
                    cancel_check=cancel_check)
        if existing > 0 and upgraded:
            # URL 已升级为原图：旧文件是压缩版，续传会拼出损坏文件，删掉重下
            # （已下载的原图由 download_media 的 _should_skip_download 提前跳过）
            os.remove(filepath)
            existing = 0
        if existing > 0:
            h["Range"] = f"bytes={existing}-"
        with httpx.Client(timeout=60, follow_redirects=True, headers=h) as client:
            with client.stream("GET", url) as resp:
                # 416 Range Not Satisfiable = 文件已完整（Range 起点超过文件末尾）
                if resp.status_code == 416:
                    return filepath, True
                resp.raise_for_status()
                is_partial = resp.status_code == 206  # 206 = 服务端支持续传
                # 根据 Content-Type 调整扩展名
                ext = _ext_guess(resp.headers.get("content-type", ""), url)
                if not filepath.endswith(ext):
                    filepath = filepath.rsplit(".", 1)[0] + ext
                # 206 追加写（续传），200/2xx 覆盖写（重新下载）
                mode = "ab" if is_partial else "wb"
                # 进度基数：续传时 content-length 只是剩余长度，加上已存在部分
                total = int(resp.headers.get("content-length", 0) or 0)
                if is_partial and total:
                    total += existing
                done = existing if is_partial else 0
                last_t = 0.0
                with open(filepath, mode) as f:
                    for chunk in resp.iter_bytes(chunk_size=262144):
                        if cancel_check is not None and cancel_check():
                            return filepath, False  # 半截文件保留供续传
                        f.write(chunk)
                        done += len(chunk)
                        now_t = time.monotonic()
                        if progress_cb is not None and now_t - last_t >= 0.2:
                            progress_cb(done, total)
                            last_t = now_t
                if progress_cb is not None:
                    progress_cb(done, total)
                return filepath, True
    except Exception:
        return filepath, False


class _NullProgress:
    """quiet 模式占位进度条：GUI 经 progress_callback 展示进度，终端不再渲染。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_task(self, *args, **kwargs):
        return 0

    def update(self, *args, **kwargs):
        pass

    def advance(self, *args, **kwargs):
        pass


def download_media(
    info_list: list,
    base_dir: str | None = None,
    download_images: bool = True,
    download_live: bool = True,
    download_videos: bool = True,
    progress_callback=None,
    wait_range: tuple | None = None,
    cancel_event=None,
    skip_existing: bool = True,
    quiet: bool = False,
    byte_progress_callback=None,
    threads: int = 1,
    on_task_event=None,
) -> dict:
    """
    批量下载媒体文件。返回统计信息。

    Args:
        info_list: VideoInfo 列表
        base_dir: 基础输出目录（默认 download/）
        download_images: 是否下载图片
        download_live: 是否下载 live 图
        download_videos: 是否下载视频
        progress_callback: 进度回调 (done, total, name)，在工作线程调用
        wait_range: 文件间随机等待区间 (lo, hi) 秒；None 用默认 3~60（防反爬）
        cancel_event: threading.Event，置位后跳过剩余文件（stats["cancelled"]=True）；
            下载大文件中途置位也会立即中止当前文件（半截文件保留供 Range 续传）
        skip_existing: 已下载的原图自动跳过（本地文件与原图大小一致即视为已下载，
            stats["skipped"] 计数）；强制重下时传 False
        quiet: 静默模式（GUI 调用）：不向终端打印开始/完成/进度条等任何输出
        byte_progress_callback: 大文件（视频/Live图）字节级进度回调
            (done_bytes, total_bytes)，约 5 次/秒；None 则不回报（GUI 才需要）。
            注意：并行模式下多文件同时回报，仍需区分文件的调用方请改用 on_task_event
        threads: 线程数（2/4/8/16/32/64）。单任务大文件 = 分段数（IDM 风格）；
            多任务 = 并行下载的文件数。1 = 原串行行为（默认，命令行/旧调用方兼容）
        on_task_event: 下载事件回调（GUI 进度面板用，线程安全要求同 progress_callback）：
            ("plan",  mode, file_count, threads)  一次，mode ∈ "segments"/"parallel"/"serial"
            ("start", filename)                   文件开始下载（含单流模式）
            ("seg",   seg_idx, seg_done, seg_total) 分段进度（仅 segments 模式）
            ("bytes", filename, done, total)      文件字节进度（serial/parallel 模式大文件）
            ("done",  filename, ok)               文件结束（ok=True 成功 / False 失败）
            ("skip",  filename)                   已下载跳过
    """
    def _say(msg: str):
        if not quiet:
            console.print(msg)

    if base_dir is None:
        base_dir = DOWNLOAD_DIR

    # 收集下载任务
    tasks: list[tuple[str, str, str]] = []  # (url, filepath, media_type)
    for info in info_list:
        author = _sanitize_filename(info.author or "未知")
        date_str = ""
        if info.publish_time:
            date_str = re.sub(r'[^0-9]', '', info.publish_time[:10])
        if not date_str:
            date_str = time.strftime("%Y%m%d")
        title = _sanitize_filename(info.title or "", 50)
        plat_cn = _PLATFORM_CN.get(info.platform, info.platform or "未知")
        # 目录：[平台]_媒体/[用户]；文件名 {datetime}_{发布者}_{标题}.{格式}
        user_dir = os.path.join(base_dir, plat_cn + MEDIA_DIR_SUFFIX, author)
        stem = f"{date_str}_{author}_{title}" if title else f"{date_str}_{author}_{info.video_id[:8]}"

        # 封面（视频帖独有：抖音/B站等封面不在 images 里；图文帖封面=首图自动去重）
        if download_images and info.cover_url and info.cover_url not in info.images:
            cov_ext = os.path.splitext(_url_path(info.cover_url))[1].lower()
            if cov_ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                cov_ext = ".jpg"
            tasks.append((info.cover_url,
                          os.path.join(user_dir, f"{stem}_封面{cov_ext}"), "封面"))

        # 图片
        if download_images and info.images:
            for i, img_url in enumerate(info.images, 1):
                fname = f"{stem}.jpg" if len(info.images) == 1 else f"{stem}_{i}.jpg"
                tasks.append((img_url, os.path.join(user_dir, fname), "图片"))

        # live 图（扩展名跟随 URL：B站实况图 .mp4 / 微博 .mov）
        if download_live and info.live_photos:
            for i, lv_url in enumerate(info.live_photos, 1):
                ext = os.path.splitext(_url_path(lv_url))[1].lower()
                if ext not in (".mov", ".mp4"):
                    ext = ".mov"
                fname = f"{stem}_live{ext}" if len(info.live_photos) == 1 else f"{stem}_live{i}{ext}"
                tasks.append((lv_url, os.path.join(user_dir, fname), "Live图"))

        # 视频
        if download_videos and info.video_url:
            vurl = ""
            plat = info.platform or ""
            if plat == "weibo":
                # 微博: 从 page_info.media_info 提取真实流
                pi = info.raw_data.get("page_info", {}) or {}
                mi = pi.get("media_info", {}) or {}
                if isinstance(mi, dict):
                    vurl = (mi.get("stream_url_hd", "")
                            or mi.get("stream_url", "")
                            or mi.get("mp4_720p_mp4", "")
                            or mi.get("mp4_hd_mp4", "")
                            or mi.get("mp4_sd_mp4", "")
                            or mi.get("mp4_url", "")
                            or mi.get("h5_url", ""))
                # 兜底：page_info.urls（dict: 清晰度 -> mp4 直链）
                if not vurl:
                    urls = pi.get("urls", {})
                    if isinstance(urls, dict) and urls:
                        vurl = (urls.get("mp4_720p_mp4", "")
                                or urls.get("mp4_hd_mp4", "")
                                or urls.get("mp4_sd_mp4", "")
                                or next(iter(urls.values()), ""))
                # 视频分享帖（超话/星球视频）：page_info 为空，视频在
                # url_objects[0].object.object（与 m.weibo.cn 详情页同源）
                if not vurl:
                    for uo in info.raw_data.get("url_objects") or []:
                        inner = ((uo.get("object") or {}).get("object") or {})
                        if not isinstance(inner, dict):
                            continue
                        ou = inner.get("urls") or {}
                        if isinstance(ou, dict) and ou:
                            vurl = (ou.get("mp4_1080p_mp4") or ou.get("mp4_720p_mp4")
                                    or ou.get("mp4_hd_mp4") or ou.get("mp4_sd_mp4")
                                    or next(iter(ou.values()), ""))
                        if not vurl:
                            st = inner.get("stream") or {}
                            if isinstance(st, dict):
                                vurl = st.get("hd_url") or st.get("url") or ""
                        if vurl:
                            break
                # video.weibo.com 播放器页解析：raw_data 为 h5 playinfo
                # （urls=清晰度标签→直链 / stream_url 均在顶层，且为 // 协议相对格式）
                if not vurl:
                    pu = info.raw_data.get("urls") or {}
                    if isinstance(pu, dict) and pu:
                        vurl = (pu.get("高清 1080P") or pu.get("高清 720P")
                                or pu.get("标清 480P") or next(iter(pu.values()), ""))
                    if not vurl:
                        vurl = info.raw_data.get("stream_url") or ""
                    if vurl.startswith("//"):
                        vurl = "https:" + vurl
                # 最后一层兜底：调用方显式传入的 video_url 本身就是直链
                # （资产页/批量页构造的 VideoInfo 常无 raw_data，直链存于
                # video_url；网页链接 m.weibo.cn/detail/xxx 不会命中直链判断）
                if not vurl and _is_direct_media_url(info.video_url):
                    vurl = info.video_url
                if not vurl:
                    vurl = pi.get("url", "") if "weibo.com" not in (pi.get("url", "") or "") else ""
            elif plat not in ("bilibili", "B站"):
                # 抖音 + multi 平台（小红书/快手/好看/微视/梨视频/皮皮搞笑）：
                # video_url 已是真实直链（非网页链接）
                if _is_direct_media_url(info.video_url):
                    vurl = info.video_url
            if vurl:
                fname = f"{stem}.mp4"
                tasks.append((vurl, os.path.join(user_dir, fname), "视频"))

    if not tasks:
        _say("[yellow]没有可下载的媒体文件[/yellow]")
        return {"images": 0, "live": 0, "videos": 0, "total": 0, "failed": 0,
                "cancelled": False}

    _say(f"[bold]开始下载 {len(tasks)} 个文件到 [cyan]{os.path.abspath(base_dir)}[/cyan]...[/bold]")

    from auth import get_cookie as _gc
    # 惰性获取 cookie（避免下载抖音时也触发 weibo cookie 过期警告/无关请求）；
    # 并行下载时多线程共享缓存，加锁防并发重复取 cookie
    _ck_lock = threading.Lock()
    _dy_ck_cache = [None]
    _wb_ck_cache = [None]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://weibo.com/",
    }

    def _dy_headers():
        with _ck_lock:
            if _dy_ck_cache[0] is None:
                _dy_ck_cache[0] = _gc("douyin") or ""
        h = dict(headers)
        h["Referer"] = "https://www.douyin.com/"
        if _dy_ck_cache[0]:
            h["Cookie"] = _dy_ck_cache[0]
        return h

    def _wb_headers():
        with _ck_lock:
            if _wb_ck_cache[0] is None:
                _wb_ck_cache[0] = _gc("weibo") or ""
        h = dict(headers)
        h["Referer"] = "https://weibo.com/"
        if _wb_ck_cache[0]:
            h["Cookie"] = _wb_ck_cache[0]
        return h

    stats = {"images": 0, "covers": 0, "live": 0, "videos": 0, "total": len(tasks),
             "failed": 0, "skipped": 0, "cancelled": False,
             # 成功/跳过任务的 (最终路径, 类型)：调用方直接把文件挂到 UI 行，
             # 免去下载完成后全量重扫磁盘（曾致 GUI 未响应）
             "paths": []}
    if progress_callback:
        progress_callback(0, len(tasks), "")

    # 线程模式决策：
    #   segments — 单任务大文件，threads = 分段数（IDM 风格，一个文件拆 N 段）
    #   parallel — 多任务，threads = 同时在下的文件数（每个文件单流）
    #   serial   — threads=1 / 单任务小文件，维持原串行行为（兼容命令行与旧调用方）
    if len(tasks) == 1 and threads > 1 and tasks[0][2] in ("视频", "Live图"):
        mode = "segments"
    elif threads > 1 and len(tasks) > 1:
        mode = "parallel"
    else:
        mode = "serial"
    if on_task_event is not None:
        try:
            on_task_event("plan", mode, len(tasks), threads)
        except Exception:
            pass

    _lock = threading.Lock()

    def _bump(key: str, n: int = 1):
        with _lock:
            stats[key] += n

    def _report_progress(name: str):
        if progress_callback:
            with _lock:
                done = (stats["images"] + stats["covers"] + stats["live"]
                        + stats["videos"] + stats["failed"] + stats["skipped"])
            progress_callback(done, len(tasks), name)

    def _evt(*event):
        if on_task_event is not None:
            try:
                on_task_event(*event)
            except Exception:
                pass

    def _pick_headers(url: str) -> dict:
        """根据 URL 域名选择请求头（抖音/微博带 Cookie，其余按平台 Referer）。"""
        if "douyin.com" in url or "douyinvod" in url:
            return _dy_headers()
        if "weibo" in url or "sinaimg" in url:
            return _wb_headers()
        if "hdslb.com" in url or "bilibili.com" in url or "bilivideo" in url:
            h = dict(headers)
            h["Referer"] = "https://www.bilibili.com/"
            return h
        if "xiaohongshu" in url or "xhscdn" in url or "sns-img" in url:
            h = dict(headers)
            h["Referer"] = "https://www.xiaohongshu.com/"
            return h
        if "kuaishou" in url or "yximgs" in url or "gifshow" in url:
            h = dict(headers)
            h["Referer"] = "https://www.kuaishou.com/"
            return h
        return dict(headers)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]{task.completed}/{task.total}[/dim]"),
        console=console,
    ) if not quiet else _NullProgress()

    def _process_task(idx: int):
        """处理第 idx 个任务（串行循环体 / 并行 worker 共用）。"""
        url, fpath, mtype = tasks[idx]
        name = os.path.basename(fpath)
        if cancel_event is not None and cancel_event.is_set():
            stats["cancelled"] = True
            return
        hdrs = _pick_headers(url)
        # 已下载自动跳过（原图）：本地文件与原图大小一致 → 不发下载请求
        if skip_existing and _should_skip_download(url, fpath, hdrs):
            _bump("skipped")
            with _lock:
                stats["paths"].append((_find_existing_media(fpath) or fpath, mtype))
            progress.update(pbar, description=f"[dim]已下载，跳过 {name}[/dim]")
            progress.advance(pbar)
            _evt("skip", name)
            _report_progress(name)
            return
        # 防反爬等待：串行模式沿用「首个不等待」；并行模式每任务前随机等待
        # （错峰起跑，避免 N 个 worker 同一瞬间全打到 CDN）
        lo, hi = wait_range if wait_range else (3, 60)
        if mode == "parallel":
            wait = random.uniform(lo, hi) if hi > lo else lo
            if wait > 0:
                time.sleep(wait)
        elif idx > 0:
            wait = random.uniform(lo, hi) if hi > lo else lo
            if wait > 0:
                progress.update(pbar,
                                description=f"[yellow]随机等待 {wait:.1f}s（防反爬）...[/yellow]")
                time.sleep(wait)
        progress.update(pbar, description=f"[cyan]{mtype}[/cyan] {name}")
        # 大文件（视频/Live图）才报字节进度：图片秒下，逐张报进度是噪音；
        # 取消检查也只在此时传入——文件间本来就有检查，大文件中途能立即停
        big = mtype in ("视频", "Live图")
        cancel_chk = (cancel_event.is_set
                      if big and cancel_event is not None else None)
        ft = threads if (mode == "segments" and big) else 1
        seg_cb = ((lambda i, d, t: _evt("seg", i, d, t))
                  if mode == "segments" and big else None)

        def _byte_cb(done, total, _name=name):
            # 旧回调（asset 页）与新的 on_task_event 各自独立触发
            if byte_progress_callback is not None:
                byte_progress_callback(done, total)
            _evt("bytes", _name, done, total)

        _evt("start", name)
        filepath, ok = _download_one(
            url, fpath, hdrs,
            progress_cb=_byte_cb if big else None,
            cancel_check=cancel_chk,
            threads=ft,
            seg_progress_cb=seg_cb)
        if ok:
            with _lock:
                if mtype == "图片":
                    stats["images"] += 1
                elif mtype == "Live图":
                    stats["live"] += 1
                elif mtype == "封面":
                    stats["covers"] += 1
                elif mtype == "视频":
                    stats["videos"] += 1
                # 416 快速跳过（Range 起点超文件尾=已完整）时路径未按
                # Content-Type 调整过扩展名，用 _find_existing_media 兜底
                stats["paths"].append(
                    (_find_existing_media(filepath) or filepath, mtype))
            _evt("done", name, True)
        else:
            # 大文件中途取消：半截文件保留供续传，计失败但标记取消
            if cancel_event is not None and cancel_event.is_set():
                stats["cancelled"] = True
                _bump("failed")
                _say("[yellow]已取消（半截文件保留，下次续传）[/yellow]")
                _evt("done", name, False)
                return
            _bump("failed")
            _say(f"[red]X 失败: {os.path.basename(filepath)}[/red]")
            _evt("done", name, False)
        progress.advance(pbar)
        # 完成回调里不含 skipped（与原串行循环的口径一致）
        if progress_callback:
            with _lock:
                done = (stats["images"] + stats["live"] + stats["covers"]
                        + stats["videos"] + stats["failed"])
            progress_callback(done, len(tasks), os.path.basename(filepath))

    with progress:
        pbar = progress.add_task("[cyan]下载中...", total=len(tasks))
        if mode == "parallel":
            # 多文件并行：N 个文件同时在下载（批量下载线程语义）
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(threads, len(tasks))) as pool:
                futs = [pool.submit(_process_task, i) for i in range(len(tasks))]
                for f in futs:
                    f.result()
        else:
            # 串行 / 单文件分段：顺序处理（segments 模式的分段并行在
            # _download_ranged 内部的线程池完成，外层仍是单任务循环）
            for i in range(len(tasks)):
                _process_task(i)
                if stats["cancelled"]:
                    break

    _say("\n[bold green]V 下载完成:[/bold green]")
    _say(f"  图片: {stats['images']} | 封面: {stats['covers']} | "
         f"Live图: {stats['live']} | 视频: {stats['videos']}")
    if stats["skipped"]:
        _say(f"  [dim]跳过已下载: {stats['skipped']}[/dim]")
    if stats["failed"]:
        _say(f"  [red]失败: {stats['failed']}[/red]")
    _say(f"  [dim]保存到: {os.path.abspath(base_dir)}[/dim]")
    return stats
