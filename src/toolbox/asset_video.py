"""资产通用视频源解析（L3）：抖音/微博/小红书下载前刷新直链。

不依赖 Qt；解析失败时保留调用方传入的旧直链/原始数据。
"""

from __future__ import annotations


def resolve_video_source(item: dict, *, video_url: str, video_id: str,
                         raw_data=None, quality: str = "") -> dict:
    """返回 {video_url, raw_data, note_images}。

    - 抖音：重新解析作品页拿新鲜直链；图文 note 返回 images。
    - 微博：依次尝试 raw_url 与 detail/{mid}，拿新直链与 raw_data。
    - 小红书：依次尝试 raw_url / 原链接 / 裸笔记页，拿直链或全部图片。
    """
    vurl2 = video_url
    note_images = None
    raw = raw_data
    platform = (item or {}).get("platform", "")

    if platform == "douyin" and video_id:
        try:
            from multi_downloader import parse_video
            data = parse_video(f"https://www.douyin.com/video/{video_id}", quality=quality)
            if data.get("is_note") or (data.get("images") and not data.get("video_url")):
                note_images = data.get("images") or []
            elif data.get("video_url"):
                vurl2 = data["video_url"]
        except Exception:
            pass
    elif platform == "weibo":
        from platforms import get_parser
        parser = get_parser("weibo")
        raw_url = (item or {}).get("raw_url") or ""
        candidates = []
        if raw_url and any(k in raw_url for k in
                           ("fid=", "/detail/", "/status/", "/tv/show/",
                            "h5.video.weibo.com")):
            candidates.append(raw_url)
        if video_id and str(video_id).isdigit():
            candidates.append(f"https://m.weibo.cn/detail/{video_id}")
        for url in candidates:
            try:
                info = parser.parse(url)
                if info and info.video_url:
                    vurl2 = info.video_url
                    if info.raw_data:
                        raw = info.raw_data
                    break
            except Exception:
                continue
    elif platform == "xiaohongshu":
        from platforms import get_parser
        parser = get_parser("xiaohongshu")
        candidates = []
        for url in ((item or {}).get("raw_url") or "", video_url):
            if url and url not in candidates:
                candidates.append(url)
        if video_id:
            bare = f"https://www.xiaohongshu.com/discovery/item/{video_id}"
            if bare not in candidates:
                candidates.append(bare)
        for url in candidates:
            try:
                info = parser.parse(url)
            except Exception:
                continue
            if not info:
                continue
            if info.video_url:
                vurl2 = info.video_url
                break
            if info.images:
                note_images = info.images
                break

    return {"video_url": vurl2, "raw_data": raw, "note_images": note_images}
