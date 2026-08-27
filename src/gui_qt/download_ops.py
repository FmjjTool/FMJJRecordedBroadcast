"""下载共享操作（对应原版 info_pages / download_page 中重复的下载逻辑）。

供「已获取」「单个解析」「批量导入」页共用：条目分组、B站分类、画质映射、
抖音直链刷新、多平台 URL 解析、B站下载执行、非 B站媒体下载。
"""

import os
import random
import time

from toolbox.config_manager import get_export_subdir

# multi_downloader 返回的中文平台名 → 英文标识（COLLECTED / 下载目录共用英文键）
MULTI_PLATFORM_EN = {
    "抖音": "douyin",
    "微博": "weibo",
    "小红书": "xiaohongshu",
    "快手": "kuaishou",
    "好看视频": "haokan",
    "微视": "weishi",
    "梨视频": "pear",
    "皮皮搞笑": "pipigx",
}


def parse_url_info(url: str):
    """解析单条 URL → VideoInfo（单个解析/批量导入共用）。

    B站/抖音/微博走完整解析器（信息全），其余 7 个视频平台（小红书/快手/
    好看/微视/梨视频/皮皮搞笑）走 multi_downloader 构造最小 VideoInfo。
    无法识别时抛 ValueError。
    """
    from platforms import URLRecognizer, get_parser
    from platforms.base import VideoInfo, now_str

    platform = URLRecognizer.recognize(url)
    parser = get_parser(platform) if platform else None
    if parser:
        info = parser.parse(url)
        if not info:
            raise ValueError("获取失败（可能反爬，需 Cookie）")
        return info

    from multi_downloader import parse_video
    d = parse_video(url)  # 无法识别的域名抛 ValueError
    info = VideoInfo(
        platform=MULTI_PLATFORM_EN.get(d.get("platform", ""), d.get("platform") or "unknown"),
        kind="video",
        video_id=str(d.get("video_id") or url[:64]),
        title=d.get("title", ""),
        author=d.get("author", ""),
        author_id=str(d.get("author_id", "") or ""),
        play_count=int(d.get("play_count", 0) or 0),
        like_count=int(d.get("like_count", 0) or 0),
        comment_count=int(d.get("comment_count", 0) or 0),
        cover_url=d.get("cover_url", ""),
        video_url=d.get("video_url", ""),
        publish_time=d.get("publish_time", ""),
        raw_url=url,
        fetched_at=now_str(),
    )
    if d.get("images"):
        info.images = d["images"]
    return info


def resolve_items(items):
    """按平台分组：返回 (bili_items, non_bili_items)。"""
    bili = [i for i in items if i.platform == "bilibili"]
    non_bili = [i for i in items if i.platform != "bilibili"]
    return bili, non_bili


def classify_bili(bili_items):
    """分类 B站条目：图文动态(draw)走图片，视频(bv/av/AV动态)走 playurl。"""
    bv_items, draw_items = [], []
    for info in bili_items:
        vid = info.video_id or ""
        rd = info.raw_data or {}
        rtype = (rd.get("type", "") or "").upper()
        if vid.startswith("dyn_") and "DRAW" in rtype:
            draw_items.append(info)
        elif vid.startswith("dyn_") and "AV" in rtype:
            major = ((rd.get("modules", {}) or {}).get("module_dynamic", {}) or {}).get("major", {}) or {}
            arch = major.get("archive", {}) or {}
            real_bvid = arch.get("bvid", "")
            if real_bvid:
                info.video_id = real_bvid
            bv_items.append(info)
        elif vid.startswith("BV") or vid.lower().startswith("av"):
            bv_items.append(info)
        else:
            draw_items.append(info)  # 兜底当动态
    return bv_items, draw_items


def quality_to_qn(label):
    """画质下拉选项 → B站 qn 编号。最高画质返回 None（B站自动选最高）。"""
    return {"8K": 127, "杜比视界": 126, "HDR": 125, "4K": 120,
            "1080P60": 116, "1080P": 80, "720P": 64, "480P": 32}.get(label)


def refresh_douyin_urls(items, quality):
    """抖音条目重新解析拿新直链（存量直链有时效性）+ 按画质选。"""
    for info in items:
        if info.platform != "douyin" or not getattr(info, "video_id", ""):
            continue
        try:
            from multi_downloader import parse_video
            d = parse_video(f"https://www.douyin.com/video/{info.video_id}", quality=quality)
            if d.get("video_url"):
                info.video_url = d["video_url"]
        except Exception:
            pass


def get_bilibili_cookie():
    try:
        from auth import get_cookie
        return get_cookie("bilibili") or ""
    except Exception:
        return ""


def do_bilibili_download(bv_items, draw_items, non_bili, quality, ck,
                         quality_label="最高画质", log=None):
    """实际 B站下载（后台线程内执行）。

    每个视频前随机等待 3~60 秒（防反爬硬性条件）；
    draw_items 下动态图片，non_bili 走 media_downloader（抖音重新解析）。
    log(msg) 可选：实时日志回调（需线程安全）。
    返回 (paths, draw_stats, other_stats, errors)。
    """
    from bilibili_download import download_bilibili_by_bvid
    from media_downloader import download_media
    out_dir = str(get_export_subdir("video_data"))
    paths, errors = [], []

    def progress_cb(done, total, name):
        if log:
            log(f"✅ ({done}/{total}) {name}")

    for idx, info in enumerate(bv_items):
        title = (info.title or info.video_id)[:40]
        if idx > 0:
            wait = random.uniform(3, 60)
            if log:
                log(f"⏳ 随机等待 {wait:.1f}s（防反爬）...")
            time.sleep(wait)
        if log:
            log(f"⬇ ({idx + 1}/{len(bv_items)}) 正在下载 {title}...")
        try:
            path = download_bilibili_by_bvid(
                info.video_id, quality=quality, cookie=ck,
                out_dir=out_dir, title=info.title,
                author=info.author, publish_time=info.publish_time,
            )
            paths.append(path)
            if log:
                log(f"✅ 完成 {title} → {os.path.basename(str(path))}")
        except Exception as e:
            errors.append(f"{title}: {str(e)[:40]}")
            if log:
                log(f"❌ 失败 {title} → {e}")

    draw_stats = None
    if draw_items:
        if log:
            log(f"⬇ 下载 {len(draw_items)} 个动态图片...")
        try:
            draw_stats = download_media(draw_items, base_dir=out_dir,
                                        download_images=True, download_live=False,
                                        download_videos=False,
                                        progress_callback=progress_cb if log else None,
                                        quiet=True)
        except Exception as e:
            errors.append(f"动态图片: {str(e)[:40]}")
            if log:
                log(f"❌ 动态图片失败: {e}")

    other_stats = None
    if non_bili:
        if log:
            log(f"⬇ 下载 {len(non_bili)} 个其他媒体...")
        try:
            refresh_douyin_urls(non_bili, quality_label)
            other_stats = download_media(non_bili, base_dir=out_dir,
                                         progress_callback=progress_cb if log else None,
                                         quiet=True)
        except Exception as e:
            errors.append(f"其他: {str(e)[:40]}")
            if log:
                log(f"❌ 其他失败: {e}")
    return paths, draw_stats, other_stats, errors


def download_media_items(items, log=None):
    """非 B站媒体下载（后台线程内执行）。返回 stats dict。"""
    from media_downloader import download_media
    base_dir = str(get_export_subdir("video_data"))

    def progress_cb(done, total, name):
        if log:
            log(f"✅ ({done}/{total}) {name}")

    return download_media(items, base_dir=base_dir,
                          progress_callback=progress_cb if log else None,
                          quiet=True)


def summarize_download(paths, draw_stats, other_stats, errors):
    """汇总下载结果为提示文本（对应原版 done 回调的拼接逻辑）。"""
    lines = []
    if paths:
        lines.append(f"B站视频: {len(paths)} 个")
    if draw_stats:
        lines.append(f"动态图片: {draw_stats.get('images', 0)} 张")
    if other_stats:
        line = (f"其他: 图 {other_stats.get('images', 0)}"
                f" / 封面 {other_stats.get('covers', 0)}"
                f" / 视频 {other_stats.get('videos', 0)}")
        if other_stats.get("skipped", 0):
            line += f"（跳过已下载 {other_stats['skipped']}）"
        lines.append(line)
    msg = " | ".join(lines) or "无内容下载"
    if errors:
        msg += f"（失败 {len(errors)} 条）"
    return msg
