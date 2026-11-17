"""资产图片 URL 提取（L3，只读）：从条目 raw.json 取图片与 Live 图视频。"""

from __future__ import annotations

import json
import os


def extract_pic_urls(item: dict) -> tuple:
    """返回 (pic_urls, live_urls)；兼容微博/B站/抖音/快手/小红书 raw 结构。"""
    urls, lives = [], []
    file_path = (item or {}).get("file_path", "")
    if not file_path:
        return urls, lives
    stem = os.path.splitext(file_path)[0]
    candidates = [stem + "_raw.json", stem.replace(".json", "_raw.json")]
    raw_path = next((path for path in candidates if os.path.exists(path)), "")
    if not raw_path:
        return urls, lives
    try:
        with open(raw_path, encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return urls, lives

        from platforms.weibo import WeiboParser
        # 微博 pics：original/largest/large/url；videoSrc 是播放器页
        for pic in data.get("pics", []) or []:
            if isinstance(pic, dict):
                url = (pic.get("original", {}).get("url")
                       or pic.get("largest", {}).get("url")
                       or pic.get("large", {}).get("url")
                       or pic.get("url") or "")
                live = pic.get("videoSrc") or ""
                if live:
                    mov = WeiboParser._extract_livephoto_url(live)
                    if mov and mov not in lives:
                        lives.append(mov)
            elif isinstance(pic, str):
                url = pic
            else:
                url = ""
            if url and url not in urls:
                urls.append(url)
        if not lives and data.get("live_photo"):
            for live in str(data["live_photo"]).split():
                if live:
                    mov = WeiboParser._extract_livephoto_url(live)
                    if mov and mov not in lives:
                        lives.append(mov)
        # 微博 pic_ids → sinaimg 大图
        if not urls:
            for pic_id in data.get("pic_ids", []) or []:
                if pic_id:
                    urls.append(f"https://wx1.sinaimg.cn/large/{pic_id}.jpg")
        # B站 opus.pics
        if not urls:
            try:
                major = (((data.get("modules", {}) or {}).get("module_dynamic", {}) or {})
                         .get("major", {}) or {})
                for pic in (major.get("opus", {}) or {}).get("pics", []) or []:
                    if not isinstance(pic, dict):
                        continue
                    url = pic.get("url", "") or pic.get("src", "")
                    if url and url not in urls:
                        urls.append(url)
                    live = pic.get("live_url") or ""
                    if live and live not in lives:
                        lives.append(live)
            except Exception:
                pass
        # B站新版 draw.items
        if not urls:
            try:
                major = (((data.get("modules", {}) or {}).get("module_dynamic", {}) or {})
                         .get("major", {}) or {})
                for pic in (major.get("draw", {}) or {}).get("items", []) or []:
                    if not isinstance(pic, dict):
                        continue
                    url = pic.get("src", "")
                    if url and url not in urls:
                        urls.append(url)
                    live = pic.get("live_url") or ""
                    if live and live not in lives:
                        lives.append(live)
            except Exception:
                pass
        # 抖音/快手/小红书 images：url_list[0] 或 url
        if not urls:
            for image in data.get("images", []) or []:
                if isinstance(image, dict):
                    url_list = image.get("url_list") or []
                    url = url_list[0] if url_list else (image.get("url") or "")
                elif isinstance(image, str):
                    url = image
                else:
                    url = ""
                if url and url not in urls:
                    urls.append(url)
    except Exception:
        pass
    return urls, lives
