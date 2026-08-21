import asyncio

from ..config import DOUYIN_SEC_UID


def _sync_collect(sec_uid=None) -> dict:
    from auth import get_cookie

    if get_cookie("douyin"):
        return _http_collect(sec_uid)
    return _playwright_collect(sec_uid)


def _http_collect(sec_uid=None) -> dict:
    import requests
    from urllib.parse import urlencode

    from auth import get_cookie
    from multi_downloader import UA_PC, _douyin_abogus

    sec_uid = sec_uid or DOUYIN_SEC_UID
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "version_code": "170808",
        "sec_user_id": sec_uid,
        "personal_center_strategy": "1",
    }
    query = urlencode(params)
    a_bogus = _douyin_abogus(query)
    if not a_bogus:
        return {"platform": "douyin", "error": "a_bogus 签名失败（需要本机安装 node.js）"}

    url = f"https://www.douyin.com/aweme/v1/web/user/profile/other/?{query}&a_bogus={a_bogus}"
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA_PC,
        "Referer": "https://www.douyin.com/",
    })
    cookie = get_cookie("douyin")
    if cookie:
        s.headers["Cookie"] = cookie
    else:
        # 游客态：先访问首页拿 ttwid 等游客 cookie，否则接口直接拒绝
        try:
            s.get("https://www.douyin.com/", timeout=15)
        except Exception:
            pass

    r = s.get(url, timeout=15)
    try:
        data = r.json()
    except ValueError:
        return {"platform": "douyin", "error": "接口返回异常（非 JSON），请在 Cookie 管理中登录抖音后重试"}
    if data.get("status_code") != 0 or not data.get("user"):
        code = data.get("status_code")
        if code in (8, "8"):
            return {"platform": "douyin", "error": "需要登录：请在 Cookie 管理中登录抖音"}
        return {"platform": "douyin", "error": f"接口返回异常（status_code={code}），建议登录抖音后重试"}

    user_data = data["user"]
    avatar_url = None
    for key in ("avatar_larger", "avatar_medium", "avatar_168x168"):
        obj = user_data.get(key)
        if isinstance(obj, dict):
            urls = obj.get("url_list")
            if urls:
                avatar_url = urls[0]
                break

    return {
        "platform": "douyin",
        "name": user_data.get("nickname"),
        "fans": user_data.get("follower_count"),
        "following": user_data.get("following_count"),
        "likes": user_data.get("total_favorited"),
        "sign": user_data.get("signature"),
        "video_count": user_data.get("aweme_count"),
        "avatar": avatar_url,
        "short_id": user_data.get("short_id"),
        "uid": user_data.get("uid"),
        "gender": user_data.get("gender"),
        "verified": bool(user_data.get("custom_verify")),
    }


def _playwright_collect(sec_uid=None) -> dict:
    """游客态兜底：本机有 Playwright 时用无头浏览器过风控；没有则提示登录。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"platform": "douyin", "error": "未登录抖音：请在「Cookie 管理」中登录后再采集"}

    from ..config import BROWSER_USER_AGENT, PLAYWRIGHT_TIMEOUT

    sec_uid = sec_uid or DOUYIN_SEC_UID
    user_url = f"https://www.douyin.com/user/{sec_uid}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT * 2)

        user_data = {}

        def on_response(response):
            if "aweme/v1/web/user/profile/other/" in response.url:
                try:
                    data = response.json()
                    if data.get("status_code") == 0 and data.get("user"):
                        user_data.update(data["user"])
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            page.goto(user_url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT)
        except Exception:
            try:
                page.goto(user_url, wait_until="commit", timeout=15000)
            except Exception:
                context.close()
                browser.close()
                return {"platform": "douyin", "error": "page navigation failed"}

        for _ in range(30):
            if user_data:
                break
            page.wait_for_timeout(500)
        context.close()
        browser.close()

        if not user_data:
            return {"platform": "douyin", "error": "未登录抖音：请在「Cookie 管理」中登录后再采集"}
        return {
            "platform": "douyin",
            "name": user_data.get("nickname"),
            "fans": user_data.get("follower_count"),
            "following": user_data.get("following_count"),
            "likes": user_data.get("total_favorited"),
            "sign": user_data.get("signature"),
            "video_count": user_data.get("aweme_count"),
            "short_id": user_data.get("short_id"),
            "uid": user_data.get("uid"),
            "gender": user_data.get("gender"),
            "verified": bool(user_data.get("custom_verify")),
        }


async def collect(sec_uid=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, sec_uid)
    except Exception as e:
        return {"platform": "douyin", "error": str(e)}
