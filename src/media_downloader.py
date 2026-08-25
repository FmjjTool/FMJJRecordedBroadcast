"""
媒体下载模块 - 下载图片 / live图 / 视频到本地
"""

import os
import random
import re
import time
from urllib.parse import urlparse

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "download")

# 平台英文标识 -> 中文文件夹名（multi_downloader 返回的已是中文，原样保留）
_PLATFORM_CN = {
    "bilibili": "B站",
    "douyin": "抖音",
    "weibo": "微博",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "haokan": "好看视频",
    "weishi": "微视",
    "pear": "梨视频",
    "pipigx": "皮皮搞笑",
}


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


def _download_one(url: str, filepath: str, headers: dict) -> tuple[str, bool]:
    """下载单个文件，返回 (filepath, success)"""
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
        if existing > 0 and upgraded:
            # URL 已升级为原图：旧文件是压缩版，续传会拼出损坏文件，删掉重下
            # （已下载的原图由 download_media 的 _should_skip_download 提前跳过）
            os.remove(filepath)
            existing = 0
        if existing > 0:
            h["Range"] = f"bytes={existing}-"
        with httpx.Client(timeout=60, follow_redirects=True, headers=h) as client:
            resp = client.get(url)
            # 416 Range Not Satisfiable = 文件已完整（Range 起点超过文件末尾）
            if resp.status_code == 416:
                return filepath, True
            resp.raise_for_status()
            is_partial = resp.status_code == 206  # 206 Partial Content = 服务端支持续传
            # 根据 Content-Type 调整扩展名
            ext = _ext_guess(resp.headers.get("content-type", ""), url)
            if not filepath.endswith(ext):
                filepath = filepath.rsplit(".", 1)[0] + ext
            # 206 追加写（续传），200/2xx 覆盖写（重新下载）
            mode = "ab" if is_partial else "wb"
            with open(filepath, mode) as f:
                f.write(resp.content)
            return filepath, True
    except Exception:
        return filepath, False


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
        cancel_event: threading.Event，置位后跳过剩余文件（stats["cancelled"]=True）
        skip_existing: 已下载的原图自动跳过（本地文件与原图大小一致即视为已下载，
            stats["skipped"] 计数）；强制重下时传 False
    """
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
        # 目录：[平台][用户]；文件名 {datetime}_{发布者}_{标题}.{格式}
        user_dir = os.path.join(base_dir, plat_cn, author)
        stem = f"{date_str}_{author}_{title}" if title else f"{date_str}_{author}_{info.video_id[:8]}"

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
        console.print("[yellow]没有可下载的媒体文件[/yellow]")
        return {"images": 0, "live": 0, "videos": 0, "total": 0, "failed": 0,
                "cancelled": False}

    console.print(f"[bold]开始下载 {len(tasks)} 个文件到 [cyan]{os.path.abspath(base_dir)}[/cyan]...[/bold]")

    from auth import get_cookie as _gc
    # 惰性获取 cookie（避免下载抖音时也触发 weibo cookie 过期警告/无关请求）
    _dy_ck_cache = [None]
    _wb_ck_cache = [None]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://weibo.com/",
    }

    def _dy_headers():
        if _dy_ck_cache[0] is None:
            _dy_ck_cache[0] = _gc("douyin") or ""
        h = dict(headers)
        h["Referer"] = "https://www.douyin.com/"
        if _dy_ck_cache[0]:
            h["Cookie"] = _dy_ck_cache[0]
        return h

    def _wb_headers():
        if _wb_ck_cache[0] is None:
            _wb_ck_cache[0] = _gc("weibo") or ""
        h = dict(headers)
        h["Referer"] = "https://weibo.com/"
        if _wb_ck_cache[0]:
            h["Cookie"] = _wb_ck_cache[0]
        return h

    stats = {"images": 0, "live": 0, "videos": 0, "total": len(tasks), "failed": 0,
             "skipped": 0, "cancelled": False}
    if progress_callback:
        progress_callback(0, len(tasks), "")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]{task.completed}/{task.total}[/dim]"),
        console=console,
    ) as progress:
        pbar = progress.add_task("[cyan]下载中...", total=len(tasks))

        # 串行下载：每个文件前随机等待（默认 3~60 秒防反爬；GUI 手动下载传小区间提速）
        for idx, (url, fpath, mtype) in enumerate(tasks):
            if cancel_event is not None and cancel_event.is_set():
                stats["cancelled"] = True
                console.print("[yellow]已取消，跳过剩余文件[/yellow]")
                break
            # 根据 URL 域名选择请求头（提前到跳过检查之前）
            if "douyin.com" in url or "douyinvod" in url:
                hdrs = _dy_headers()
            elif "weibo" in url or "sinaimg" in url:
                hdrs = _wb_headers()
            elif "hdslb.com" in url or "bilibili.com" in url or "bilivideo" in url:
                h = dict(headers)
                h["Referer"] = "https://www.bilibili.com/"
                hdrs = h
            else:
                hdrs = dict(headers)
            # 已下载自动跳过（原图）：本地文件与原图大小一致 → 不发下载请求
            if skip_existing and _should_skip_download(url, fpath, hdrs):
                stats["skipped"] += 1
                progress.update(pbar, description=f"[dim]已下载，跳过 {os.path.basename(fpath)}[/dim]")
                progress.advance(pbar)
                if progress_callback:
                    done = (stats["images"] + stats["live"] + stats["videos"]
                            + stats["failed"] + stats["skipped"])
                    progress_callback(done, len(tasks), os.path.basename(fpath))
                continue
            if idx > 0:
                lo, hi = wait_range if wait_range else (3, 60)
                wait = random.uniform(lo, hi) if hi > lo else lo
                if wait > 0:
                    progress.update(pbar, description=f"[yellow]随机等待 {wait:.1f}s（防反爬）...[/yellow]")
                    time.sleep(wait)
            progress.update(pbar, description=f"[cyan]{mtype}[/cyan] {os.path.basename(fpath)}")
            filepath, ok = _download_one(url, fpath, hdrs)
            if ok:
                if mtype == "图片":
                    stats["images"] += 1
                elif mtype == "Live图":
                    stats["live"] += 1
                elif mtype == "视频":
                    stats["videos"] += 1
            else:
                stats["failed"] += 1
                console.print(f"[red]X 失败: {os.path.basename(filepath)}[/red]")
            progress.advance(pbar)
            if progress_callback:
                done = stats["images"] + stats["live"] + stats["videos"] + stats["failed"]
                progress_callback(done, len(tasks), os.path.basename(filepath))

    console.print("\n[bold green]V 下载完成:[/bold green]")
    console.print(f"  图片: {stats['images']} | Live图: {stats['live']} | 视频: {stats['videos']}")
    if stats["skipped"]:
        console.print(f"  [dim]跳过已下载: {stats['skipped']}[/dim]")
    if stats["failed"]:
        console.print(f"  [red]失败: {stats['failed']}[/red]")
    console.print(f"  [dim]保存到: {os.path.abspath(base_dir)}[/dim]")
    return stats
