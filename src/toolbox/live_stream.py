# -*- coding: utf-8 -*-
"""直播流解析：快手 / 微博 / 小红书 / 网易云（LOOK 直播）。

与 toolbox/live_record.py 的 fetch_douyin / fetch_bili 返回同一结构，
由 live_record.fetch_info / fetch_status 统一分发。

解析策略（有界回退，不无限重试）：
- 快手：游客 HTTP livedetail（自动生成 did）优先，拿不到再转浏览器嗅探。
- 微博：带已保存登录态的直播页 HTTP 优先，拿不到再转浏览器嗅探。
- 小红书 / 网易云：网页接口依赖动态签名或登录，直接浏览器嗅探页面自身请求。
浏览器嗅探只捕获页面已经发出的 m3u8/flv 请求与 JSON 响应，不伪造登录态；
登录态只来自统一管理接口 auth.get_cookie(platform)，读取后即用即弃。

能力矩阵：
| 平台 | 开播状态 | 取流 | 登录方式 | 已知限制 |
| 快手 | 支持 | HTTP + 浏览器 | 游客 / 已存登录态 | 风控严格时需登录态 |
| 微博 | 支持 | 页面 + 浏览器 | 已存登录态更稳 | 需能打开直播页 |
| 小红书 | 支持 | 浏览器 | 已存登录态 | 签名动态生成，游客可能被拦 |
| 网易云 | 支持 | 浏览器 | 游客 / 已存登录态 | 以 LOOK 直播页为准 |

自动化测试只覆盖纯解析函数；真实取流属于平台联调，需用户明确要求后手工验证。
"""

from __future__ import annotations

import html
import json
import re
import secrets
import threading
import time
from urllib.parse import quote

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

KUAISHOU_API = "https://live.kuaishou.com/live_api/liveroom/livedetail"

# 缓存：直播中 25 秒、未开播 75 秒、失败 10 秒，避免监控循环反复开浏览器
CACHE_TTL_LIVE = 25.0
CACHE_TTL_OFFLINE = 75.0
CACHE_TTL_ERROR = 10.0

BROWSER_TIMEOUT_MS = 20_000
BROWSER_WAIT_S = 15.0
MAX_JSON_BODIES = 30

_ENDED_MARKERS = ("直播已结束", "直播结束", "已下播", "暂未开播", "未开播", "主播暂不在")
_LIVE_MARKERS = ("正在直播", "直播中", "LIVE")
_LOGIN_MARKERS = ("请先登录", "扫码登录", "登录后查看", "登录查看")

SPECS = {
    "kuaishou": {
        "name": "快手直播",
        "referer": "https://live.kuaishou.com/",
        "url": "https://live.kuaishou.com/u/{room}",
        "cookie_domains": (".kuaishou.com",),
        "hint": "如 3xabcdef / https://live.kuaishou.com/u/3xabcdef",
    },
    "weibo": {
        "name": "微博直播",
        "referer": "https://weibo.com/",
        "url": "https://weibo.com/l/wblive/p/show/{room}",
        "cookie_domains": (".weibo.com", ".weibo.cn"),
        "hint": "如 1022:2321325xxxx / https://weibo.com/l/wblive/p/show/1022:2321325xxxx",
    },
    "xiaohongshu": {
        "name": "小红书直播",
        "referer": "https://www.xiaohongshu.com/",
        "url": "https://www.xiaohongshu.com/livestream/{room}",
        "cookie_domains": (".xiaohongshu.com",),
        "hint": "如 5f8a1b2c / https://www.xiaohongshu.com/livestream/5f8a1b2c",
    },
    "netease": {
        "name": "网易云直播",
        "referer": "https://look.163.com/",
        "url": "https://look.163.com/live?id={room}",
        "cookie_domains": (".163.com", ".music.163.com"),
        "hint": "如 123456789 / https://look.163.com/live?id=123456789",
    },
}

NEW_PLATFORMS = tuple(SPECS)

_MEDIA_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?\.(?:m3u8|flv)(?:\?[^\s\"'<>\\]*)?", re.I)
_API_HINTS = ("/api/", "livedetail", "live_info", "room", "detail", "stream", "play")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_ONLINE_RES = (
    re.compile(r"([\d,，.]+(?:\.\d+)?万?)\s*(?:人)?\s*(?:在线|观看|人气)"),
    re.compile(r"(?:在线|观看|人气)[:：\s]*([\d,，.]+(?:\.\d+)?万?)"),
)
_ROOM_NAME_RE = re.compile(r"([^\s|【】\[\]，,]{1,24})的直播间")

_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_ROOM_LOCKS: dict[tuple, threading.Lock] = {}
_ROOM_LOCKS_GUARD = threading.Lock()


# ==================== 平台 / 房间 ====================

def display_name(platform: str) -> str:
    return (SPECS.get(platform) or {}).get("name") or platform


def room_hint(platform: str) -> str:
    return (SPECS.get(platform) or {}).get("hint") or "房间号 / 直播间链接"


def live_url(platform: str, room: str) -> str:
    spec = SPECS.get(platform)
    if not spec:
        return ""
    return spec["url"].format(room=quote(str(room), safe=":"))


def parse_room(platform: str, text) -> str:
    """从房间号或直播间链接提取房间 ID；识别不了返回空串。"""
    s = str(text or "").strip()
    if not s:
        return ""
    if platform == "kuaishou":
        m = (re.search(r"live\.kuaishou\.com/u/([0-9A-Za-z_\-]+)", s)
             or re.search(r"kuaishou\.com/(?:profile|u)/([0-9A-Za-z_\-]+)", s))
        if m:
            return m.group(1)
        return _raw_id(s, r"[0-9A-Za-z_\-]{3,64}")
    if platform == "weibo":
        m = (re.search(r"live\.weibo\.com/(?:show|room)\?(?:[^#]*&)?id=([0-9A-Za-z:_\-]+)", s)
             or re.search(r"weibo\.com/l/wblive/p/show/([0-9A-Za-z:_\-]+)", s)
             or re.search(r"(?:live_id|room_id)=([0-9A-Za-z:_\-]+)", s))
        if m:
            return m.group(1)
        return _raw_id(s, r"\d{3,}(?::[0-9A-Za-z]+)?")
    if platform == "xiaohongshu":
        m = (re.search(r"xiaohongshu\.com/livestream/([0-9A-Za-z_\-]+)", s)
             or re.search(r"/livestream/([0-9A-Za-z_\-]+)", s))
        if m:
            return m.group(1)
        return _raw_id(s, r"[0-9A-Za-z_\-]{4,64}")
    if platform == "netease":
        m = (re.search(r"(?:look|music)\.163\.com/live\?(?:[^#]*&)?id=([0-9A-Za-z_\-]+)", s)
             or re.search(r"(?:liveId|roomId)=([0-9A-Za-z_\-]+)", s))
        if m:
            return m.group(1)
        return _raw_id(s, r"\d{3,20}")
    return ""


def _raw_id(text: str, pattern: str) -> str:
    """纯数字/字符房间号；带 / ? # 的链接不当作房间号。"""
    if any(ch in text for ch in "/?#"):
        return ""
    return text if re.fullmatch(pattern, text) else ""


# ==================== 纯解析工具 ====================

def cookie_pairs(raw: str) -> list[tuple[str, str]]:
    pairs = []
    for part in str(raw or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip() and value.strip():
            pairs.append((name.strip(), value.strip()))
    return pairs


def extract_media_urls(text) -> list[str]:
    """从 HTML / JSON 文本提取 m3u8、flv 直链（兼容 JSON 转义斜杠）。"""
    body = html.unescape(str(text or "")).replace("\\/", "/")
    out, seen = [], set()
    for url in _MEDIA_RE.findall(body):
        url = url.replace("&amp;", "&").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def pick_stream_url(urls, quality: str = "") -> tuple[str, list[str]]:
    """按 m3u8/FLV、清晰度与主播放列表优先挑一条流地址。"""
    clean = []
    seen = set()
    for url in urls or []:
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        clean.append(url)

    def score(url: str) -> int:
        low = url.lower()
        value = 0
        if ".m3u8" in low:
            value += 100
        elif ".flv" in low:
            value += 50
        if "master" in low or "playlist" in low:
            value += 12
        if any(bad in low for bad in ("/ad/", "ads", "preview", "cover")):
            value -= 60
        q = str(quality or "")
        for token in ("1080", "720", "480"):
            if token in low:
                value += {"1080": 6, "720": 4, "480": 2}[token]
                if token == q:
                    value += 20
        if not q:
            for token, bonus in (("uhd", 5), ("bd", 3), ("hd", 2), ("sd", 1)):
                if re.search(r"(?<![a-z])%s(?![a-z])" % token, low):
                    value += bonus
        return -value

    ranked = sorted(clean, key=score)
    return (ranked[0] if ranked else ""), ranked


def parse_page_text(title, body) -> dict:
    """页面标题/正文 → 主播名、标题、在线人数、是否明确已结束。"""
    title = str(title or "").strip()
    body = str(body or "")
    text = title + "\n" + body[:4000]
    ended = any(mark in text for mark in _ENDED_MARKERS)
    live_hint = any(mark in text for mark in _LIVE_MARKERS) and not ended
    name = ""
    match = _ROOM_NAME_RE.search(title) or _ROOM_NAME_RE.search(body[:800])
    if match:
        name = match.group(1).strip()
    online = 0
    for pattern in _ONLINE_RES:
        match = pattern.search(text)
        if match:
            online = _parse_count(match.group(1))
            if online:
                break
    return {"name": name, "online": online, "ended": ended, "live_hint": live_hint,
            "title": _clean_title(title)}


def _clean_title(title: str) -> str:
    text = re.sub(r"\s*[-_|—]\s*(小红书|微博|快手|网易云音乐|LOOK直播|哔哩哔哩).*$", "",
                  str(title or ""))
    return text.strip()[:120]


def _parse_count(text) -> int:
    raw = str(text or "").replace(",", "").replace("，", "").strip()
    try:
        if raw.endswith("万"):
            return int(float(raw[:-1]) * 10000)
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _json_or_none(text):
    raw = str(text or "").strip()
    if not raw:
        return None
    if not raw.startswith(("{", "[")):
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _html_text(text: str) -> str:
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", str(text or ""))
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html.unescape(body))[:4000]


def _html_title(text: str) -> str:
    match = _TITLE_RE.search(str(text or ""))
    return html.unescape(match.group(1)).strip() if match else ""


def _first_text(node, keys) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        value = node.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _info(**kwargs) -> dict:
    info = {"live": False, "status": 0, "title": "", "name": "", "online": 0,
            "cover": "", "stream": "", "quality": "", "quality_key": "",
            "qualities": {}, "is_hls": False, "error": ""}
    stream = str(kwargs.get("stream") or "")
    info.update(kwargs)
    info["is_hls"] = bool(info.get("is_hls") or ".m3u8" in stream.lower())
    return info


def _brief(error) -> str:
    return re.sub(r"\s+", " ", str(error or "")).strip()[:200]


# ==================== 统一入口 ====================

def fetch(platform: str, room, quality: str = "") -> dict:
    """读取直播间信息与直播流；结构同 live_record.fetch_douyin/fetch_bili。"""
    if platform not in SPECS:
        return _info(status=None, error="不支持的平台")
    parsed = parse_room(platform, room)
    if not parsed:
        return _info(status=None, error="无法识别房间号，请填写直播间链接或房间号")
    room = parsed

    key = (platform, room)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return dict(hit[1])

    lock = _lock_for(key)
    if not lock.acquire(timeout=40):
        return _info(status=None, error="正在解析该直播间，请稍后重试")
    try:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and hit[0] > time.monotonic():
                return dict(hit[1])
        info = None
        if platform == "kuaishou":
            info = _fetch_kuaishou_http(room, quality)
        elif platform == "weibo":
            info = _fetch_weibo_http(room, quality)
        if info is None:
            info = _fetch_with_browser(platform, room, quality)
        _remember(key, info)
        return dict(info)
    finally:
        lock.release()


def fetch_status(platform: str, room) -> dict:
    info = fetch(platform, room)
    if info.get("status") is None:
        return {"error": info.get("error") or "房间状态读取失败"}
    return {"live": bool(info.get("live")), "status": info.get("status"),
            "title": info.get("title") or "", "online": info.get("online") or 0}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _lock_for(key) -> threading.Lock:
    with _ROOM_LOCKS_GUARD:
        lock = _ROOM_LOCKS.get(key)
        if lock is None:
            lock = _ROOM_LOCKS[key] = threading.Lock()
        return lock


def _remember(key, info) -> None:
    if info.get("stream"):
        ttl = CACHE_TTL_LIVE
    elif info.get("status") is None:
        ttl = CACHE_TTL_ERROR
    else:
        ttl = CACHE_TTL_OFFLINE
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, dict(info))
        if len(_CACHE) > 200:
            expired = [k for k, v in _CACHE.items() if v[0] <= time.monotonic()]
            for k in expired:
                _CACHE.pop(k, None)


def _managed_cookie(platform: str) -> str:
    try:
        import auth
        return auth.get_cookie(platform) or ""
    except Exception:  # noqa: BLE001 - 管理器不可用时按游客流程
        return ""


# ==================== 快手 / 微博 HTTP ====================

def _fetch_kuaishou_http(room: str, quality: str):
    cookie = _managed_cookie("kuaishou") or ("did=web_" + secrets.token_hex(16))
    headers = {"User-Agent": UA, "Referer": live_url("kuaishou", room),
               "Accept": "application/json, text/plain, */*", "Cookie": cookie}
    try:
        resp = httpx_get(KUAISHOU_API, params={"principalId": room},
                         headers=headers, timeout=15)
        text = resp.text
    except Exception:  # noqa: BLE001 - 交回浏览器嗅探
        return None
    data = _json_or_none(text)
    root = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        return None
    stream = root.get("liveStream") if isinstance(root.get("liveStream"), dict) else {}
    urls = extract_media_urls(text)
    for item in stream.get("playUrls") or []:
        if isinstance(item, dict):
            urls.extend(extract_media_urls(item.get("url") or ""))
            reps = ((item.get("adaptationSet") or {}).get("representation") or [])
            for rep in reps:
                if isinstance(rep, dict):
                    urls.extend(extract_media_urls(rep.get("url") or ""))
    best, ranked = pick_stream_url(urls, quality)
    confident = "liveStream" in root or "author" in root
    if not best and (not confident or stream.get("playUrls")):
        return None
    author = root.get("author") if isinstance(root.get("author"), dict) else {}
    return _info(
        live=bool(best), status=1 if best else 0,
        title=_first_text(stream, ("caption", "title")) or _first_text(root, ("caption", "title")),
        name=_first_text(author, ("name", "principalName")) or _first_text(root, ("name",)),
        online=_parse_count(stream.get("userCount") or root.get("userCount")),
        cover=_first_text(stream, ("coverUrl", "cover")) or _first_text(author, ("avatar",)),
        stream=best, quality="", qualities={}, is_hls=".m3u8" in best,
        error="" if best or confident else "未读取到快手直播状态",
    )


def _fetch_weibo_http(room: str, quality: str):
    cookie = _managed_cookie("weibo")
    headers = {"User-Agent": UA, "Referer": "https://weibo.com/"}
    if cookie:
        headers["Cookie"] = cookie
    pages = (f"https://weibo.com/l/wblive/p/show/{quote(room, safe=':')}",
             f"https://live.weibo.com/show?id={quote(room, safe=':')}")
    for page_url in pages:
        try:
            resp = httpx_get(page_url, headers=headers, timeout=15, follow_redirects=True)
            text = resp.text
        except Exception:  # noqa: BLE001 - 换下一个地址/浏览器
            continue
        best, ranked = pick_stream_url(extract_media_urls(text), quality)
        meta = parse_page_text(_html_title(text), _html_text(text))
        if best:
            return _info(live=True, status=1, title=meta["title"], name=meta["name"],
                         online=meta["online"], stream=best, is_hls=".m3u8" in best)
        if meta["ended"]:
            return _info(live=False, status=0, title=meta["title"], name=meta["name"])
    return None


# ==================== 浏览器嗅探 ====================

def _fetch_with_browser(platform: str, room: str, quality: str) -> dict:
    url = live_url(platform, room)
    try:
        from playwright.sync_api import sync_playwright
        from toolbox.browser import launch as pw_launch
    except Exception as exc:  # noqa: BLE001
        return _info(status=None, error="浏览器组件不可用：%s" % _brief(exc))

    media: list[str] = []
    responses: list = []
    page_error = ""
    title = ""
    body = ""
    cover = ""

    def on_response(resp):
        try:
            resp_url = resp.url or ""
            low = resp_url.lower()
            if ".m3u8" in low or ".flv" in low:
                media.append(resp_url)
            elif any(hint in low for hint in _API_HINTS) and len(responses) < MAX_JSON_BODIES:
                responses.append(resp)
        except Exception:  # noqa: BLE001 - 单个响应异常不影响整体
            pass

    try:
        with sync_playwright() as pw:
            browser = pw_launch(
                pw, headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"])
            try:
                context = browser.new_context(
                    user_agent=UA, locale="zh-CN",
                    viewport={"width": 1280, "height": 800})
                cookies = context_cookies(platform)
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception:  # noqa: BLE001 - 无效单项不阻断游客访问
                        pass
                page = context.new_page()
                page.on("response", on_response)
                try:
                    page.goto(url, wait_until="domcontentloaded",
                              timeout=BROWSER_TIMEOUT_MS)
                except Exception as exc:  # noqa: BLE001 - 超时也继续读已加载内容
                    page_error = _brief(exc)
                started = time.monotonic()
                while time.monotonic() - started < BROWSER_WAIT_S:
                    if media:
                        break
                    elapsed = time.monotonic() - started
                    if elapsed > 2.0:
                        _poke_playback(page, elapsed)
                    try:
                        page.wait_for_timeout(400)
                    except Exception:
                        break
                for resp in list(responses):
                    try:
                        media.extend(extract_media_urls(resp.text()))
                    except Exception:
                        continue
                try:
                    title = page.title() or ""
                except Exception:
                    pass
                try:
                    body = page.evaluate(
                        "() => document.body ? document.body.innerText.slice(0, 4000) : ''") or ""
                except Exception:
                    pass
                try:
                    cover = page.eval_on_selector(
                        "meta[property='og:image']", "el => el.content") or ""
                except Exception:
                    cover = ""
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - 浏览器启动/导航失败
        return _info(status=None, error="浏览器取流失败：%s" % _brief(exc))

    best, ranked = pick_stream_url(media, quality)
    meta = parse_page_text(title, body)
    if best:
        return _info(live=True, status=1, title=meta["title"], name=meta["name"],
                     online=meta["online"], cover=cover, stream=best,
                     is_hls=".m3u8" in best.lower())
    if not meta["ended"] and not body and page_error:
        return _info(status=None, error="打开直播间失败：%s" % page_error)
    error = ""
    if any(mark in body for mark in _LOGIN_MARKERS):
        error = "需要登录后查看该直播间"
    elif meta["live_hint"]:
        error = "页面显示直播中，但未捕获到直播流（可能需要登录或平台改版）"
    return _info(live=False, status=0, title=meta["title"], name=meta["name"],
                 online=meta["online"], cover=cover, error=error)


def _poke_playback(page, elapsed: float) -> None:
    """页面可能停在“进入直播间”；轻点一次并让媒体元素自动播放。"""
    if elapsed < 6.0:
        for label in ("进入直播间", "继续观看", "点击观看"):
            try:
                page.get_by_text(label, exact=False).first.click(timeout=600)
                break
            except Exception:
                continue
    try:
        page.evaluate(
            "() => document.querySelectorAll('video,audio').forEach(e => {"
            "e.muted = true; e.play && e.play().catch(() => {}); })")
    except Exception:
        pass


def context_cookies(platform: str) -> list[dict]:
    """已保存登录态 → Playwright 上下文 Cookie；总开关关闭或未登录时返回空。"""
    pairs = cookie_pairs(_managed_cookie(platform))
    if not pairs:
        return []
    cookies = []
    for domain in SPECS[platform]["cookie_domains"]:
        for name, value in pairs:
            cookies.append({"name": name, "value": value,
                            "domain": domain, "path": "/"})
    return cookies


def httpx_get(url, **kwargs):
    """延迟导入 httpx，便于在无网络/无依赖环境下只使用纯解析函数。"""
    import httpx
    return httpx.get(url, **kwargs)