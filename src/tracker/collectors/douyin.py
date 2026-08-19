import asyncio

from ..config import DOUYIN_SEC_UID, BROWSER_USER_AGENT, PLAYWRIGHT_TIMEOUT


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
window.chrome = { runtime: {} };
"""


def _sync_collect(sec_uid=None) -> dict:
    from playwright.sync_api import sync_playwright
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
        page.add_init_script(STEALTH_JS)
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
            return {"platform": "douyin", "error": "no user data from API"}

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


async def collect(sec_uid=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, sec_uid)
    except Exception as e:
        return {"platform": "douyin", "error": str(e)}
