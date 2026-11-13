import asyncio
import time

import requests

from ..config import BILIBILI_MID, BROWSER_USER_AGENT, REQUEST_TIMEOUT

HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Referer": "https://space.bilibili.com/",
}

# B站风控/限流码：-352 风控校验失败、-412 请求被拦截、-509 请求过于频繁
_RETRY_CODES = {-352, -412, -509}
_RETRY_DELAYS = (2, 5)


def _card(mid: str, cookie: str = "") -> dict:
    """请求一次 card 接口，返回原始 JSON（不抛业务错误）。"""
    headers = dict(HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    resp = requests.get(
        f"https://api.bilibili.com/x/web-interface/card?mid={mid}",
        headers=headers, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _to_record(d: dict) -> dict:
    card = d["data"]["card"]
    return {
        "platform": "bilibili",
        "name": card.get("name"),
        "fans": d["data"].get("follower"),
        "following": card.get("attention"),
        "likes": d["data"].get("like_num"),
        "sign": card.get("sign"),
        "video_count": d["data"].get("archive_count"),
        "face": card.get("face"),
        "official_title": card.get("Official", {}).get("title"),
        "level": card.get("level_info", {}).get("current_level"),
        "vip_type": card.get("vip", {}).get("type"),
        "sex": card.get("sex"),
        "birthday": card.get("birthday"),
    }


def _sync_collect(mid=None) -> dict:
    mid = mid or BILIBILI_MID
    try:
        from auth import get_cookie
        cookie = (get_cookie("bilibili") or "").strip()
    except Exception:  # noqa: BLE001 - Cookie 读取失败按游客处理
        cookie = ""

    # 一级：游客请求，遇风控码按退避重试
    last_err = ""
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            d = _card(mid)
        except Exception as e:  # noqa: BLE001 - 网络异常同样走重试/兜底
            last_err = str(e)
            continue
        code = d.get("code")
        if code == 0:
            return _to_record(d)
        last_err = d.get("message") or str(code)
        if code not in _RETRY_CODES:
            return {"platform": "bilibili", "error": last_err}

    # 二级：游客被风控挡住，接入已登录 Cookie 重试
    if cookie:
        print("[B站采集] 游客请求被风控，接入 Cookie 重试")
        try:
            d = _card(mid, cookie)
            if d.get("code") == 0:
                return _to_record(d)
            last_err = d.get("message") or str(d.get("code"))
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    return {"platform": "bilibili", "error": f"B站风控拦截（{last_err}），可稍后重试或检查 Cookie 管理中的 B站 登录"}


async def collect(mid=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, mid)
    except Exception as e:
        return {"platform": "bilibili", "error": str(e)}
