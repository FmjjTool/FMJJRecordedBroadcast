"""采集条目可打开链接（桌面页与 WebUI 共用，L3）。"""

from __future__ import annotations

import re


def openable_url(platform: str, video_id: str, video_url: str,
                 raw_url: str, kind: str = "", has_images: bool = False) -> str:
    ru = (raw_url or "").strip()
    plat = (platform or "").strip()
    vid = str(video_id or "").strip()
    if plat == "douyin" and "/user/" in ru and vid:
        is_note = kind == "image" or (has_images and not video_url)
        return f"https://www.douyin.com/note/{vid}" if is_note \
            else f"https://www.douyin.com/video/{vid}"
    if plat == "weibo" and vid and re.fullmatch(r"https?://weibo\.com/u?\d+/?", ru):
        match = re.search(r"weibo\.com/u?(\d+)", ru)
        if match:
            return f"https://weibo.com/{match.group(1)}/{vid}"
    if plat == "bilibili" and vid and "space.bilibili.com" in ru:
        return f"https://www.bilibili.com/video/{vid}"
    return ru or (video_url or "")
