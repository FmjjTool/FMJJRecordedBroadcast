"""快手 (Kuaishou) 解析器：视频页 + 主页作品列表。

- 视频页：内嵌 window.__APOLLO_STATE__（defaultClient 内 VisionVideoDetail* 键），
  游客 HTTP 可拿全量（caption/播放/点赞/作者/直链）
- 主页：2026-09 改版后纯客户端渲染（__APOLLO_STATE__ 消失），作品列表由浏览器
  调 /rest/v/profile/feed（需指纹+签名，HTTP 直连必被拦截）→ Playwright 渲染
  + 拦截 feed 响应；登录墙(result=109)时需在「Cookie 管理」登录快手
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


def _best_feed_video_url(photo: dict) -> tuple:
    """新版 feed 的 photo（manifest/photoUrls 结构）选最高清直链，返回 (url, 秒, 宽, 高)。"""
    reps = []
    for key in ("manifest", "manifestH265"):
        mf = photo.get(key)
        if not isinstance(mf, dict):
            continue
        for a in (mf.get("adaptationSet") or []):
            for r in (a.get("representation") or []):
                if r.get("url"):
                    reps.append((int(r.get("width") or 0), int(r.get("height") or 0), r["url"]))
    duration = int(photo.get("duration") or 0) // 1000
    if reps:
        best = max(reps, key=lambda x: x[0] * x[1])
        return best[2], duration, best[0], best[1]
    for pu in (photo.get("photoUrls") or []):
        u = pu.get("url") if isinstance(pu, dict) else pu
        if u:
            return u, duration, 0, 0
    return "", duration, 0, 0


def _feed_item_to_info(item: dict) -> Optional[VideoInfo]:
    """feed 响应条目 {photo, author} → VideoInfo。"""
    photo = item.get("photo") or {}
    author = item.get("author") or {}
    pid = str(photo.get("id") or "")
    if not pid:
        return None
    video_url, duration, w, h = _best_feed_video_url(photo)
    if not w and photo.get("width") and photo.get("height"):
        w, h = int(photo.get("width") or 0), int(photo.get("height") or 0)
    caption = (photo.get("caption") or "").strip()
    title = caption.splitlines()[0][:60] if caption else ""
    tags = re.findall(r"#([^#\s]+)", caption)
    return VideoInfo(
        platform="kuaishou",
        kind="video",
        video_id=pid,
        title=title,
        description=caption,
        author=author.get("name") or "",
        author_id=str(author.get("id") or ""),
        author_face=author.get("headerUrl") or "",
        play_count=_to_int(photo.get("viewCount")),
        like_count=_to_int(photo.get("realLikeCount") or photo.get("likeCount")),
        duration=duration,
        dimension=f"{w}x{h}" if w and h else "",
        cover_url=photo.get("coverUrl") or photo.get("animatedCoverUrl") or "",
        video_url=video_url,
        publish_time=_ms_to_str(photo.get("timestamp")),
        tags=[t for t in tags if t][:10],
        raw_url=f"https://www.kuaishou.com/short-video/{pid}",
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

    # ==================== 主页作品列表（Playwright 拦截） ====================

    def fetch_user_posts(self, url: str, count: int = 20, **kwargs) -> list:
        m = _USER_URL_RE.search(url)
        if not m:
            raise ValueError("不是快手主页链接")
        # 2026-09 改版：主页纯 CSR，作品 API 需浏览器指纹+签名，HTTP 直连无数据
        return self._posts_via_browser(url, count)

    def _posts_via_browser(self, url: str, count: int) -> list:
        """无头浏览器打开主页，拦截 /rest/v/profile/feed 响应（签名由浏览器生成）。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("快手主页获取需要 Playwright，请重装应用修复运行环境")
        from toolbox.browser import launch as pw_launch

        need = max(1, count)
        feeds: dict = {}
        state = {"login": False, "no_more": False}

        def on_response(resp):
            try:
                if "rest/v/profile/feed" not in resp.url:
                    return
                j = resp.json()
                result = j.get("result")
                if result == 109:
                    state["login"] = True
                    return
                if result != 1:
                    return
                for item in (j.get("feeds") or []):
                    pid = str((item.get("photo") or {}).get("id") or "")
                    if pid and pid not in feeds:
                        feeds[pid] = item
                if str(j.get("pcursor") or "") == "no_more":
                    state["no_more"] = True
            except Exception:
                pass

        with sync_playwright() as p:
            browser = pw_launch(p, headless=True,
                                args=["--disable-blink-features=AutomationControlled",
                                      "--no-sandbox", "--disable-dev-shm-usage"])
            try:
                ctx = browser.new_context(user_agent=UA,
                                          viewport={"width": 1280, "height": 900},
                                          locale="zh-CN")
                ck = _get_cookie()
                for pair in ck.split(";"):
                    n, eq, v = pair.strip().partition("=")
                    if n and eq:
                        ctx.add_cookies([{"name": n, "value": v,
                                          "domain": ".kuaishou.com", "path": "/"}])
                page = ctx.new_page()
                page.on("response", on_response)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass  # domcontentloaded 超时也继续等 feed 响应
                rounds = 14 if need <= 60 else 40
                prev, stalls = -1, 0
                for _ in range(rounds):
                    page.wait_for_timeout(1500)
                    if state["login"]:
                        raise RuntimeError(
                            "快手主页需要登录：请在「Cookie 管理」中登录快手后重试")
                    if len(feeds) >= need or state["no_more"]:
                        break
                    if len(feeds) == prev:
                        stalls += 1
                        if stalls >= 3:  # 连续滚动无新数据 → 已到底
                            break
                    else:
                        stalls = 0
                    prev = len(feeds)
                    page.evaluate("window.scrollBy(0, 2500)")
                if state["login"]:
                    raise RuntimeError(
                        "快手主页需要登录：请在「Cookie 管理」中登录快手后重试")
            finally:
                browser.close()

        if not feeds:
            raise RuntimeError(
                "快手主页未捕获到作品数据（Cookie 可能失效，请在「Cookie 管理」中重新登录快手）")
        posts = []
        for item in list(feeds.values())[:need]:
            info = _feed_item_to_info(item)
            if info:
                posts.append(info)
        return posts
