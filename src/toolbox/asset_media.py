"""资产媒体辅助（L3）：视频/图文下载编排与封面命名、复用、下载。"""

from __future__ import annotations

import os
import re
import time


def download_item_media(item: dict, resolved: dict, *, base_dir: str,
                        threads: int = 4, progress_callback=None,
                        byte_progress_callback=None, cancel_event=None,
                        on_task_event=None) -> dict:
    """构建 VideoInfo 并调用 download_media；视频/图文 note 共用入口。"""
    from platforms.base import VideoInfo, now_str
    from media_downloader import download_media, _is_direct_media_url

    video_url = resolved.get("video_url")
    raw_data = resolved.get("raw_data")
    note_images = resolved.get("note_images")
    platform = item.get("platform", "")
    info = VideoInfo(
        platform=platform, video_id=item.get("video_id", ""),
        title=item.get("title") or item.get("date_part") or "未知",
        author=item.get("author", ""),
        video_url=video_url, cover_url=item.get("cover_url", ""),
        publish_time=item.get("publish_time", ""),
        raw_data=raw_data, raw_url="", fetched_at=now_str(),
    )
    if note_images:
        info.images = note_images
        return download_media(
            [info], base_dir=base_dir,
            download_images=True, download_live=False, download_videos=False,
            threads=threads, progress_callback=progress_callback,
            wait_range=(0.5, 1.5), cancel_event=cancel_event,
            on_task_event=on_task_event, quiet=True)

    if not video_url:
        raise ValueError("无可用视频链接")
    if platform == "xiaohongshu" and not _is_direct_media_url(video_url):
        raise ValueError("小红书视频流获取失败：笔记链接可能已失效或被风控，请重新获取该条目")
    if platform == "douyin" and "douyin.com" in video_url \
            and not _is_direct_media_url(video_url):
        raise ValueError("抖音直链获取失败（接口风控 + 浏览器兜底均未拿到直链），稍后重试")

    result = download_media(
        [info], base_dir=base_dir,
        download_images=False, download_live=False, download_videos=True,
        threads=threads, progress_callback=progress_callback,
        byte_progress_callback=byte_progress_callback,
        wait_range=(0.5, 1.5), cancel_event=cancel_event,
        on_task_event=on_task_event, quiet=True)
    cover = (info.cover_url or "").strip()
    if (cover and (result.get("videos") or result.get("skipped"))
            and not result.get("cancelled")):
        cpath = save_video_cover(info, base_dir)
        if cpath:
            result["covers"] = result.get("covers", 0) + 1
            result.setdefault("paths", []).append((cpath, "封面"))
    return result


def save_video_cover(info, base_dir: str):
    """按 download_media 同名规则保存封面，返回封面路径；失败/无封面返回 None。"""
    cover = (getattr(info, "cover_url", "") or "").strip()
    if not cover:
        return None
    try:
        from media_downloader import (
            _sanitize_filename, _PLATFORM_CN, MEDIA_DIR_SUFFIX, _url_path,
            _download_one,
        )
        from toolbox.utils import claim_output_stem

        author = _sanitize_filename(getattr(info, "author", "") or "未知")
        date_str = re.sub(r"[^0-9]", "", (getattr(info, "publish_time", "") or "")[:10]) \
            or time.strftime("%Y%m%d")
        title = _sanitize_filename(getattr(info, "title", "") or "", 50)
        plat_cn = _PLATFORM_CN.get(info.platform, info.platform)
        user_dir = os.path.join(base_dir, plat_cn + MEDIA_DIR_SUFFIX, author)
        stem = (f"{date_str}_{author}_{title}" if title
                else f"{date_str}_{author}_{(info.video_id or '')[:8]}")
        stem = claim_output_stem(user_dir, stem, str(info.video_id or ""))

        if cover.startswith("//"):
            cover = "https:" + cover
        ext = os.path.splitext(_url_path(cover))[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        cpath = os.path.join(user_dir, f"{stem}_封面{ext}")
        for existing_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            existing = os.path.join(user_dir, f"{stem}_封面{existing_ext}")
            if os.path.exists(existing):
                return existing
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36"}
        for domain, referer in (("douyin", "https://www.douyin.com/"),
                                 ("sinaimg", "https://weibo.com/"),
                                 ("weibo", "https://weibo.com/"),
                                 ("xhscdn", "https://www.xiaohongshu.com/"),
                                 ("xiaohongshu", "https://www.xiaohongshu.com/")):
            if domain in cover:
                headers["Referer"] = referer
                break
        cpath, ok = _download_one(cover, cpath, headers)
        return cpath if ok and os.path.exists(cpath) else None
    except Exception:
        return None
