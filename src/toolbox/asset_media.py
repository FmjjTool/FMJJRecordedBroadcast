"""资产媒体辅助（L3）：视频封面的命名、复用与下载。"""

from __future__ import annotations

import os
import re
import time


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
