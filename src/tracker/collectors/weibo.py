import asyncio
import json
import re

from ..config import WEIBO_UID, MOBILE_USER_AGENT
from toolbox.browser import launch as pw_launch

PC_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")


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


def _is_vague(s: object) -> bool:
    """m端接口把粉丝数脱敏成「126.6万」这类万化文本。"""
    return isinstance(s, str) and ("万" in s or "亿" in s)


def _precise_counts(uid: str, cookie: str = "") -> dict:
    """PC端 weibo.com/ajax/profile/info 拿精确 粉丝/关注/微博数。

    游客走 passport visitor 系统生成 SUB/SUBP；有 Cookie 直接用。
    失败返回 {}（调用方保持 m 端万化值兜底）。
    """
    import requests

    with requests.Session() as s:
        s.headers.update({"User-Agent": PC_USER_AGENT})
        if cookie:
            s.headers["Cookie"] = cookie
        else:
            try:
                rv = s.post(
                    "https://passport.weibo.com/visitor/genvisitor",
                    data={"cb": "gen_callback",
                          "fp": json.dumps({"os": "1", "browser": "Chrome134",
                                            "fonts": "undefined",
                                            "screenInfo": "1920*1080*24",
                                            "plugins": ""})},
                    timeout=15)
                m = re.search(r"\((\{.*\})\)\s*;?\s*$", rv.text, re.S)
                tid = ((json.loads(m.group(1)).get("data") or {}).get("tid") or "") if m else ""
                if not tid:
                    return {}
                s.get(f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={tid}"
                      f"&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=0.123",
                      timeout=15)
            except Exception:
                return {}
        try:
            r = s.get(f"https://weibo.com/ajax/profile/info?uid={uid}",
                      headers={"Referer": f"https://weibo.com/u/{uid}/",
                               "X-Requested-With": "XMLHttpRequest"},
                      timeout=15)
            user = ((r.json().get("data") or {}).get("user")) or {}
            out = {}
            if isinstance(user.get("followers_count"), int):
                out["fans"] = user["followers_count"]
            if isinstance(user.get("friends_count"), int):
                out["following"] = user["friends_count"]
            if isinstance(user.get("statuses_count"), int):
                out["weibo_count"] = user["statuses_count"]
            # 获赞数（m端接口无此字段）：status_total_counter.like_cnt，
            # 格式化字符串如 "6,138,945"
            stc = user.get("status_total_counter") or {}
            likes = _parse_num(stc.get("like_cnt"))
            if likes is not None:
                out["likes"] = likes
            return out
        except Exception:
            return {}


def _apply_precise(result: dict, uid: str, cookie: str, raw_followers) -> dict:
    """PC端精确数覆盖：粉丝数万化值修正 + 获赞数（m端没有该字段）。"""
    if not _is_vague(raw_followers) and result.get("likes") is not None:
        return result
    precise = _precise_counts(uid, cookie)
    for k, v in precise.items():
        if v is not None:
            result[k] = v
    return result


def _sync_collect(uid=None) -> dict:
    """三级回退：① 游客 HTTP（含临时游客票重试）→ ② Cookie HTTP → ③ 游客 Playwright。

    游客成功时不读取已存 Cookie；HTTP 全部失败后才启动浏览器。
    """
    try:
        r = _http_collect(uid, cookie="")
        if not r.get("error"):
            return r
    except Exception:
        pass  # 单层请求/解析异常不能中断后续回退

    try:
        from auth import get_cookie
        cookie = get_cookie("weibo") or ""  # 仍遵守总开关与有效期
    except Exception:
        cookie = ""
    if cookie:
        print("[微博采集] 游客方式失败，接入 Cookie 重试")
        try:
            r = _http_collect(uid, cookie=cookie)
            if not r.get("error"):
                return r
        except Exception:
            pass

    try:
        r = _playwright_collect(uid)
        if not r.get("error"):
            return r
    except Exception:
        pass
    return {"platform": "weibo", "error": "游客与 Cookie 方式均失败：请在「Cookie 管理」中登录微博后重试"}


def _http_collect(uid=None, cookie: str = "") -> dict:
    import requests

    uid = uid or WEIBO_UID
    headers = {
        "User-Agent": MOBILE_USER_AGENT,
        "Referer": f"https://m.weibo.cn/u/{uid}/",
        "X-Requested-With": "XMLHttpRequest",
    }
    url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=100505{uid}"
    ui = {}
    error = "no user data from API"
    with requests.Session() as s:
        s.headers.update(headers)
        if cookie:
            s.headers["Cookie"] = cookie
        else:
            # 首页票据有时已足够；被拒时再补 SUB/SUBP，避免直接启动浏览器。
            try:
                s.get("https://m.weibo.cn/", timeout=15)
            except Exception:
                pass

        for attempt in range(1 if cookie else 2):
            if attempt:
                try:
                    from platforms.weibo import WeiboParser
                    visitor_cookie = WeiboParser()._get_visitor_cookie()
                except Exception:
                    visitor_cookie = ""
                if not visitor_cookie:
                    break
                s.headers["Cookie"] = visitor_cookie
            try:
                response = s.get(url, timeout=15)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("ok") != 1:
                    error = "接口拒绝访问，请在 Cookie 管理中登录微博后重试"
                    continue
                user = (data.get("data") or {}).get("userInfo")
                if isinstance(user, dict) and user:
                    ui = user
                    break
                error = "no user data from API"
            except Exception as exc:
                error = f"微博资料接口请求失败：{exc}"
    if not ui:
        return {"platform": "weibo", "error": error}

    followers = _parse_num(ui.get("followers_count"))
    following = _parse_num(ui.get("follow_count"))
    posts = _parse_num(ui.get("statuses_count"))

    result = {
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
    return _apply_precise(result, uid, cookie, ui.get("followers_count"))


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
        browser = pw_launch(
            pw, headless=True,
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
        result = {
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
        return _apply_precise(result, uid, "", user_info.get("followers_count"))


async def collect(uid=None) -> dict:
    try:
        return await asyncio.to_thread(_sync_collect, uid)
    except Exception as e:
        return {"platform": "weibo", "error": str(e)}
