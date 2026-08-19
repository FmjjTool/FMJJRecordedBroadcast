import asyncio
import requests

from ..config import BILIBILI_MID, BROWSER_USER_AGENT, REQUEST_TIMEOUT

HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Referer": "https://space.bilibili.com/",
}


def _sync_collect(mid=None) -> dict:
    mid = mid or BILIBILI_MID
    api_url = f"https://api.bilibili.com/x/web-interface/card?mid={mid}"
    resp = requests.get(api_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    d = resp.json()
    if d.get("code") != 0:
        return {"platform": "bilibili", "error": d.get("message", "unknown error")}
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


async def collect(mid=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, mid)
    except Exception as e:
        return {"platform": "bilibili", "error": str(e)}
