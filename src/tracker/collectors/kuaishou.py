"""快手账号采集：GraphQL visionProfile（游客票据常被登录墙拦截，失败时回退 Cookie）。"""

import asyncio

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

_GUEST_COOKIE = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"

_QUERY = """query visionProfile($pcursor: String, $principalId: String) {
  visionProfile(pcursor: $pcursor, principalId: $principalId) {
    result
    userProfile {
      ownerCount { fan photo follow photo_public __typename }
      profile { gender user_name user_id headurl user_text __typename }
      __typename
    }
    __typename
  }
}"""


def _sync_collect(principal_id=None) -> dict:
    import httpx

    def _call(cookie: str):
        headers = {"User-Agent": UA,
                   "Referer": f"https://www.kuaishou.com/profile/{principal_id}",
                   "Origin": "https://www.kuaishou.com",
                   "Content-Type": "application/json",
                   "Cookie": cookie}
        r = httpx.post("https://www.kuaishou.com/graphql",
                       json={"operationName": "visionProfile",
                             "query": _QUERY,
                             "variables": {"pcursor": "", "principalId": principal_id}},
                       headers=headers, timeout=20)
        return r.json()

    d = _call(_GUEST_COOKIE)
    prof = ((d.get("data") or {}).get("visionProfile") or {})
    up = prof.get("userProfile") or {}
    if not up:
        # 登录墙 → 接入 Cookie 重试
        try:
            from auth import get_cookie
            ck = get_cookie("kuaishou") or ""
        except Exception:
            ck = ""
        if ck:
            d = _call(ck)
            prof = ((d.get("data") or {}).get("visionProfile") or {})
            up = prof.get("userProfile") or {}
    if not up:
        return {"platform": "kuaishou", "error": "需要登录快手（Cookie 管理 → 快手）"}

    counts = up.get("ownerCount") or {}
    info = up.get("profile") or {}
    return {
        "platform": "kuaishou",
        "name": info.get("user_name") or "",
        "fans": int(counts.get("fan") or 0),
        "following": int(counts.get("follow") or 0),
        "video_count": int(counts.get("photo_public") or counts.get("photo") or 0),
        "sign": info.get("user_text") or "",
        "uid": info.get("user_id") or principal_id,
    }


async def collect(principal_id=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, principal_id)
    except Exception as e:
        return {"platform": "kuaishou", "error": str(e)}
