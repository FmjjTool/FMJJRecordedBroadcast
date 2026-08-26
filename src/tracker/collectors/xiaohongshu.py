"""小红书账号采集：游客 HTTP 拉主页 __INITIAL_STATE__（粉丝/关注/获赞），
游客被频控时回退 Cookie。"""

import asyncio

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _sync_collect(user_id=None) -> dict:
    import httpx
    from platforms.xiaohongshu import _balanced_json, _parse_cn_count

    url = f"https://www.xiaohongshu.com/user/profile/{user_id}"

    def _pull(cookie: str = ""):
        headers = {"User-Agent": UA,
                   "Referer": "https://www.xiaohongshu.com/",
                   "Accept-Language": "zh-CN,zh;q=0.9"}
        if cookie:
            headers["Cookie"] = cookie
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        data = _balanced_json(resp.text, r"window\.__INITIAL_STATE__")
        if not data:
            return None, "页面解析失败（可能需要 Cookie）"
        upd = (data.get("user") or {}).get("userPageData") or {}
        return (upd.get("basicInfo") or {}), upd, None

    basic, upd, err = _pull()
    if not basic:
        # 游客被频控（页面无用户数据）→ Cookie 重试
        try:
            from auth import get_cookie
            ck = get_cookie("xiaohongshu") or ""
        except Exception:
            ck = ""
        if ck:
            basic, upd, err = _pull(ck)
    if not basic:
        return {"platform": "xiaohongshu",
                "error": err or "无用户数据（游客被限流，可在 Cookie 管理登录小红书后重试）"}
    fans = following = likes = 0
    for it in (upd.get("interactions") or []):
        t = it.get("type")
        if t == "fans":
            fans = _parse_cn_count(it.get("count"))
        elif t == "follows":
            following = _parse_cn_count(it.get("count"))
        elif t == "interaction":
            likes = _parse_cn_count(it.get("count"))
    return {
        "platform": "xiaohongshu",
        "name": basic.get("nickname") or "",
        "fans": fans,
        "following": following,
        "likes": likes,
        "sign": basic.get("desc") or "",
        "uid": basic.get("redId") or "",
    }


async def collect(user_id=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, user_id)
    except Exception as e:
        return {"platform": "xiaohongshu", "error": str(e)}
