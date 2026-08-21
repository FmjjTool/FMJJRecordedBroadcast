import asyncio
import re

from ..config import WEIBO_UID, MOBILE_USER_AGENT


def _parse_num(s: object) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip()
    if not s:
        return None
    s = s.replace(",", "").replace(" ", "")
    m = re.match(r"([\d.]+)\u4e07", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"([\d.]+)\u4ebf", s)
    if m:
        return int(float(m.group(1)) * 100000000)
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _sync_collect(uid=None) -> dict:
    from auth import get_cookie

    if get_cookie("weibo"):
        return _http_collect(uid)
    return _playwright_collect(uid)


def _http_collect(uid=None) -> dict:
    import requests

    from auth import get_cookie

    uid = uid or WEIBO_UID
    headers = {
        "User-Agent": MOBILE_USER_AGENT,
        "Referer": f"https://m.weibo.cn/u/{uid}/",
        "X-Requested-With": "XMLHttpRequest",
    }
    s = requests.Session()
    s.headers.update(headers)
    cookie = get_cookie("weibo")
    if cookie:
        s.headers["Cookie"] = cookie
    else:
        # 游客态：先访问首页拿游客 cookie（mweibo_capid 等），否则接口返回 HTML
        try:
            s.get("https://m.weibo.cn/", timeout=15)
        except Exception:
            pass

    url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=100505{uid}"
    r = s.get(url, timeout=15)
    try:
        data = r.json()
    except ValueError:
        return {"platform": "weibo", "error": "接口返回异常（非 JSON），请在 Cookie 管理中登录微博后重试"}
    if data.get("ok") != 1:
        return {"platform": "weibo", "error": "接口拒绝访问，请在 Cookie 管理中登录微博后重试"}
    ui = (data.get("data") or {}).get("userInfo") or {}
    if not ui:
        return {"platform": "weibo", "error": "no user data from API"}

    followers = _parse_num(ui.get("followers_count"))
    following = _parse_num(ui.get("follow_count"))
    posts = _parse_num(ui.get("statuses_count"))

    return {
        "platform": "weibo",
        "name": ui.get("screen_name"),
        "fans": followers,
        "following": following,
        "weibo_count": posts,
        "sign": ui.get("description"),
        "avatar": ui.get("avatar_hd") or ui.get("profile_image_url"),
        "verified": ui.get("verified"),
        "verified_reason": ui.get("verified_reason"),
        "gender": ui.get("gender"),
        "cover": ui.get("cover_image_phone"),
        "level": ui.get("mbrank"),
        "uid": uid,
    }


def _playwright_collect(uid=None) -> dict:
    """游客态兜底：本机有 Playwright 时用无头浏览器；没有则提示登录。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"platform": "weibo", "error": "未登录微博：请在「Cookie 管理」中登录后再采集"}

    from ..config import PLAYWRIGHT_TIMEOUT

    uid = uid or WEIBO_UID
    user_url = f"https://m.weibo.cn/u/{uid}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=MOBILE_USER_AGENT,
            viewport={"width": 414, "height": 896},
            locale="zh-CN",
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        user_info = {}

        def on_response(response):
            if (
                "container/getIndex" in response.url
                and f"containerid=100505{uid}" in response.url
            ):
                try:
                    data = response.json()
                    if data.get("ok") == 1:
                        ui = data["data"].get("userInfo", {})
                        user_info.update(ui)
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
                return {"platform": "weibo", "error": "page navigation failed"}

        for _ in range(20):
            if user_info:
                break
            page.wait_for_timeout(500)
        context.close()
        browser.close()

        if not user_info:
            return {"platform": "weibo", "error": "未登录微博：请在「Cookie 管理」中登录后再采集"}
        followers = _parse_num(user_info.get("followers_count"))
        following = _parse_num(user_info.get("follow_count"))
        posts = _parse_num(user_info.get("statuses_count"))
        return {
            "platform": "weibo",
            "name": user_info.get("screen_name"),
            "fans": followers,
            "following": following,
            "weibo_count": posts,
            "sign": user_info.get("description"),
            "avatar": user_info.get("avatar_hd") or user_info.get("profile_image_url"),
            "verified": user_info.get("verified"),
            "verified_reason": user_info.get("verified_reason"),
            "gender": user_info.get("gender"),
            "cover": user_info.get("cover_image_phone"),
            "level": user_info.get("mbrank"),
            "uid": uid,
        }


async def collect(uid=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, uid)
    except Exception as e:
        return {"platform": "weibo", "error": str(e)}
