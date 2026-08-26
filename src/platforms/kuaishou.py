"""快手 (Kuaishou) 解析器：视频页 + 主页作品列表。

数据源：页面内嵌 window.__APOLLO_STATE__（defaultClient 内 VisionVideoDetail* 键）。
- 视频页：游客 HTTP 可拿全量（caption/播放/点赞/作者/直链）
- 主页：游客被登录墙拦截，需 Cookie（Cookie 管理 → 快手）
"""

import json
import re
from datetime import datetime
from typing import Optional

import httpx

from .base import PlatformBase, VideoInfo, now_str

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# 游客固定 Cookie（PC_WEB 票据，视频页解析用）
_GUEST_COOKIE = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"

_VIDEO_URL_RE = re.compile(r"kuaishou\.com/f/([a-zA-Z0-9_-]+)", re.I)
_VIDEO_ID_RE = re.compile(r"kuaishou\.com/short-video/([a-zA-Z0-9]+)", re.I)
_USER_URL_RE = re.compile(r"kuaishou\.com/profile/([a-zA-Z0-9]+)", re.I)


def _balanced_json(html: str, marker: str):
    """提取 marker = {...}; 的完整 JSON（花括号配平，兼容字符串内花括号）。"""
    m = re.search(marker + r"\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    depth = 0
    j = start
    instr = False
    esc = False
    q = ""
    while j < len(html):
        c = html[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif instr:
            if c == q:
                instr = False
        elif c in ('"', "'"):
            instr = True
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    raw = html[start:j + 1]
    if not raw:
        return None
    raw = raw.rstrip().rstrip(";")
    try:
        return json.loads(raw)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", raw).replace("undefined", "null"))
        except Exception:
            return None


def _get_cookie() -> str:
    try:
        from auth import get_cookie
        return get_cookie("kuaishou") or ""
    except Exception:
        return ""


def _fetch(url: str, cookie: str = "") -> str:
    headers = {"User-Agent": UA, "Referer": "https://www.kuaishou.com/",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    headers["Cookie"] = cookie if cookie else _GUEST_COOKIE
    resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"快手页面请求失败（HTTP {resp.status_code}）")
    return resp.text


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return 0


def _ms_to_str(ms) -> str:
    try:
        ts = int(ms)
        if ts > 10 ** 12:
            ts //= 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return ""


def _best_video_url(photo: dict) -> tuple:
    """从 videoResource 选最高清直链，返回 (url, duration秒, 宽, 高)。"""
    vr = ((photo.get("videoResource") or {}).get("json") or {})
    reps = []
    for codec in ("h264", "h265"):
        adapt = ((vr.get(codec) or {}).get("adaptationSet")) or []
        for a in adapt:
            for r in (a.get("representation") or []):
                if r.get("url"):
                    reps.append((int(r.get("width") or 0), int(r.get("height") or 0), r["url"]))
    duration = int(photo.get("duration") or 0) // 1000
    if not reps:
        url = photo.get("photoUrl") or ""
        if not url:
            return "", duration, 0, 0
        return url, duration, 0, 0
    best = max(reps, key=lambda x: x[0] * x[1])
    return best[2], duration, best[0], best[1]


def _photo_to_info(photo: dict, author: dict, raw_url: str = "") -> VideoInfo:
    video_url, duration, w, h = _best_video_url(photo)
    caption = (photo.get("caption") or "").strip()
    title = caption.splitlines()[0][:60] if caption else ""
    tags = re.findall(r"#([^#\s]+)", caption)
    return VideoInfo(
        platform="kuaishou",
        kind="video",
        video_id=str(photo.get("id") or ""),
        title=title,
        description=caption,
        author=(author or {}).get("name") or "",
        author_id=str((author or {}).get("id") or ""),
        author_face=(author or {}).get("headerUrl") or "",
        play_count=_to_int(photo.get("viewCount")),
        like_count=_to_int(photo.get("realLikeCount") or photo.get("likeCount")),
        duration=duration,
        dimension=f"{w}x{h}" if w and h else "",
        cover_url=photo.get("coverUrl") or "",
        video_url=video_url,
        publish_time=_ms_to_str(photo.get("timestamp")),
        tags=[t for t in tags if t][:10],
        raw_url=raw_url,
        fetched_at=now_str(),
    )


class KuaishouParser(PlatformBase):
    name = "kuaishou"

    def extract_id(self, url: str) -> str:
        m = _VIDEO_ID_RE.search(url) or _VIDEO_URL_RE.search(url)
        return m.group(1) if m else ""

    def parse(self, url: str) -> Optional[VideoInfo]:
        m = _VIDEO_URL_RE.search(url) or _VIDEO_ID_RE.search(url)
        if not m:
            raise ValueError("不是快手视频链接")
        html = _fetch(url)
        data = _balanced_json(html, r"window\.__APOLLO_STATE__")
        if not data:
            raise RuntimeError("快手页面解析失败（游客票据可能过期，稍后重试或登录快手）")
        dc = data.get("defaultClient") or {}
        photo = author = None
        for k, v in dc.items():
            if k.startswith("VisionVideoDetailPhoto:"):
                photo = v
            elif k.startswith("VisionVideoDetailAuthor:"):
                author = v
        if not photo:
            raise RuntimeError("快手未找到视频数据")
        info = _photo_to_info(photo, author or {}, raw_url=url)
        return info

    # ==================== 主页作品列表（需 Cookie） ====================

    def fetch_user_posts(self, url: str, count: int = 20, **kwargs) -> list:
        m = _USER_URL_RE.search(url)
        if not m:
            raise ValueError("不是快手主页链接")
        user_id = m.group(1)
        # 一级：游客 HTTP（大概率登录墙）
        try:
            html = _fetch(url)
            posts = self._posts_from_apollo(html, count)
            if posts:
                return posts
        except Exception:
            pass
        # 二级：接入 Cookie 重试
        ck = _get_cookie()
        if ck:
            html = _fetch(url, cookie=ck)
            posts = self._posts_from_apollo(html, count)
            if posts:
                return posts
            raise RuntimeError("快手 Cookie 已接入但主页无作品数据（Cookie 可能失效）")
        raise RuntimeError("快手主页需要登录：请在「Cookie 管理」中登录快手后重试")

    def _posts_from_apollo(self, html: str, count: int) -> list:
        data = _balanced_json(html, r"window\.__APOLLO_STATE__")
        if not data:
            return []
        dc = data.get("defaultClient") or {}
        author = {}
        feeds = []
        for k, v in dc.items():
            if k.startswith("VisionProfileAuthor:") or k.startswith("VisionVideoDetailAuthor:"):
                author = v
            elif k.startswith("VisionProfilePhoto:") or k.startswith("VisionVideoDetailPhoto:"):
                feeds.append(v)
            elif k.startswith("VisionProfile"):
                # ROOT_QUERY.visionProfile(...).feeds 为 id 引用列表，跳过
                pass
        # ROOT_QUERY 引用顺序优先（feeds 顺序）
        ordered = []
        for k, v in dc.items():
            m = re.match(r"\$ROOT_QUERY\.visionProfile\(.*?\)\.feeds\.(\d+)", k)
            if m and isinstance(v, dict):
                ordered.append((int(m.group(1)), v))
        if ordered:
            ordered.sort()
            photos = []
            for _, ref in ordered:
                pid = (ref.get("photo") or {}).get("id") or ""
                if pid.startswith("VisionProfilePhoto:") or pid.startswith("VisionVideoDetailPhoto:"):
                    photos.append(dc.get(pid) or {})
            feeds = [p for p in photos if p]
        result = [_photo_to_info(p, author) for p in feeds[:max(1, count)] if p.get("id")]
        return result
