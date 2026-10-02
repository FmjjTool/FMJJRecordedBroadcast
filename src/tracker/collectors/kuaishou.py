"""快手账号采集：GraphQL visionProfile 新版 schema（2026-09-27 快手 web 改版后
`visionProfile(userId: String)`；游客/随机 did 返回 result=2 无 userProfile，
需要「网页访问过快手」的 did Cookie；粉丝数是 "102.8万" 字符串需转数值）。"""

import asyncio

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

_GUEST_COOKIE = "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"

# 2026-09-27 改版后 schema：visionProfile 参数从 (pcursor, principalId) 变为 (userId)
_QUERY = """query visionProfile($userId: String) {
  visionProfile(userId: $userId) {
    result
    userProfile {
      ownerCount { fan photo follow photo_public __typename }
      profile { gender user_name user_id headurl user_text __typename }
      __typename
    }
    __typename
  }
}"""


def _to_int(v) -> int:
    """快手新版 ownerCount 数值是字符串："102.8万" → 1028000，"3" → 3。"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return 0
    try:
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100_000_000)
        if s.endswith("万"):
            return int(float(s[:-1]) * 10_000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


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
                             "variables": {"userId": principal_id}},
                       headers=headers, timeout=20)
        return r.json()

    def _profile(d):
        prof = ((d.get("data") or {}).get("visionProfile") or {})
        return prof.get("userProfile") or {}

    # 存档 Cookie 优先（真实浏览器访问过的 did 可用，即使无 web_st 登录态）
    up = {}
    try:
        from auth import get_cookie
        ck = get_cookie("kuaishou") or ""
    except Exception:
        ck = ""
    if ck:
        try:
            up = _profile(_call(ck))
        except Exception:
            up = {}
    if not up:
        # 回退硬编码游客 did（多数时候 result=2，但保留兜底）
        try:
            up = _profile(_call(_GUEST_COOKIE))
        except Exception:
            up = {}
    if not up:
        return {"platform": "kuaishou",
                "error": "快手已改版，粉丝数据需要带网页 Cookie：请在「Cookie 管理 → 快手」"
                         "登录（或用浏览器访问过快手后保存 Cookie）再试"}

    counts = up.get("ownerCount") or {}
    info = up.get("profile") or {}
    return {
        "platform": "kuaishou",
        "name": info.get("user_name") or "",
        "fans": _to_int(counts.get("fan")),
        "following": _to_int(counts.get("follow")),
        "video_count": _to_int(counts.get("photo_public") or counts.get("photo")),
        "sign": info.get("user_text") or "",
        "uid": info.get("user_id") or principal_id,
    }


async def collect(principal_id=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, principal_id)
    except Exception as e:
        return {"platform": "kuaishou", "error": str(e)}
