"""B站 Web 鉴权层：WBI 签名 / 设备指纹(buvid) / bili_ticket / App 签名。

复刻自 BiliResourceDownloader (Tauri+Vue) 的 src/utils/wbi.ts 与 loginManager.ts。
"""

import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0")

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

APP_KEY = "1d8b6e7d45233436"
APP_SEC = "560c52ccd288fed045859ed18bffd973"

_lock = threading.Lock()
_wbi_cache: dict | None = None          # {img_key, sub_key, w_webid, ts}
_buvid_cache: dict | None = None        # {buvid3, buvid4, b_nut, ts}
_ticket_cache: dict | None = None       # {ticket, expires_at}


def _now() -> int:
    return int(time.time())


# ==================== WBI ====================

def _get_wbi_keys(client: httpx.Client) -> dict:
    """获取 WBI 签名密钥（60 分钟缓存）。"""
    global _wbi_cache
    with _lock:
        if _wbi_cache and _now() - _wbi_cache["ts"] < 3600:
            return _wbi_cache
    r = client.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
    )
    data = r.json()["data"]["wbi_img"]
    img_key = data["img_url"].rsplit("/", 1)[1].rsplit(".", 1)[0]
    sub_key = data["sub_url"].rsplit("/", 1)[1].rsplit(".", 1)[0]
    w_webid = ""
    try:
        r2 = client.get("https://space.bilibili.com/1", headers={"User-Agent": UA})
        m = re.search(r'<script id="__RENDER_DATA__" type="application/json">([^<]+)</script>', r2.text)
        if m:
            render = json.loads(urllib.parse.unquote(m.group(1)))
            w_webid = str(render.get("access_id", ""))
    except Exception:
        pass
    with _lock:
        _wbi_cache = {"img_key": img_key, "sub_key": sub_key, "w_webid": w_webid, "ts": _now()}
    return _wbi_cache


def refresh_wbi():
    """强制清除 WBI 缓存（-352/-403 重试前调用）。"""
    global _wbi_cache
    with _lock:
        _wbi_cache = None


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _wbi_query(params: dict, keys: dict) -> str:
    mixin = _mixin_key(keys["img_key"], keys["sub_key"])
    merged = {**params, "wts": _now(), "w_webid": keys.get("w_webid", "")}
    parts = []
    for k in sorted(merged):
        v = str(merged[k]).replace("!", "").replace("'", "").replace("(", "").replace(")", "").replace("*", "")
        parts.append(f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(v, safe='')}")
    query = "&".join(parts)
    w_rid = hashlib.md5((query + mixin).encode()).hexdigest()
    return query + "&w_rid=" + w_rid


def sign_wbi(url: str, params: dict, client: httpx.Client) -> str:
    """对 url + params 做 WBI 签名，返回完整 URL。"""
    keys = _get_wbi_keys(client)
    sep = "&" if "?" in url else "?"
    return url + sep + _wbi_query(params, keys)


# ==================== App 签名 ====================

def sign_app(params: dict) -> dict:
    """App 端签名（build=8160400 固定 ts）。"""
    merged = {**params, "build": 8160400, "ts": 1700000000, "appkey": APP_KEY}
    qs = "&".join(f"{k}={merged[k]}" for k in sorted(merged))
    merged["sign"] = hashlib.md5((qs + APP_SEC).encode()).hexdigest()
    return merged


# ==================== 设备指纹 / 票据 ====================

def get_buvid(client: httpx.Client) -> dict:
    """未登录请求所需的 buvid3/buvid4/b_nut（进程内缓存）。"""
    global _buvid_cache
    with _lock:
        if _buvid_cache:
            return _buvid_cache
    try:
        r = client.get(
            "https://api.bilibili.com/x/frontend/finger/spi",
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
        )
        d = r.json()["data"]
        buvid3, buvid4 = d.get("b_3", "1"), d.get("b_4", "1")
    except Exception:
        buvid3, buvid4 = "1", "1"
    with _lock:
        _buvid_cache = {"buvid3": buvid3, "buvid4": buvid4, "b_nut": str(_now())}
    return _buvid_cache


def get_bili_ticket(client: httpx.Client) -> str:
    """GenWebTicket 获取 bili_ticket（HMAC-SHA256 签名，约3天有效）。"""
    global _ticket_cache
    with _lock:
        if _ticket_cache and _ticket_cache["expires_at"] > _now() + 60:
            return _ticket_cache["ticket"]
    buvid = get_buvid(client)
    ts = _now()
    hexsign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
    url = (
        "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
        f"?key_id=ec02&hexsign={hexsign}&context%5Bts%5D={ts}&csrf="
    )
    try:
        r = client.post(
            url,
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            cookies={"buvid3": buvid["buvid3"], "buvid4": buvid["buvid4"]},
        )
        d = r.json()["data"]
        with _lock:
            _ticket_cache = {"ticket": d["ticket"], "expires_at": d["created_at"] + d["ttl"]}
        return d["ticket"]
    except Exception:
        return ""


def build_cookie_header(client: httpx.Client, login_cookie: str) -> str:
    """拼完整 Cookie：登录态用登录 cookie；未登录用设备指纹 + ticket。"""
    if login_cookie:
        ticket = get_bili_ticket(client)
        return f"{login_cookie}; bili_ticket={ticket}" if ticket else login_cookie
    buvid = get_buvid(client)
    ticket = get_bili_ticket(client)
    parts = [f"buvid3={buvid['buvid3']}", f"buvid4={buvid['buvid4']}", f"b_nut={buvid['b_nut']}"]
    if ticket:
        parts.append(f"bili_ticket={ticket}")
    return "; ".join(parts)
