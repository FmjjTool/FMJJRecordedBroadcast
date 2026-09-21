"""全局视频搜索：关键词 → 三大平台（B站/抖音/微博）视频搜索结果。

单独获取页「搜索模式」的数据源：
- search_bilibili：search/type 接口（WBI 签名，复用 bili.api 的游客→Cookie 自动回退）
- search_douyin：aweme/v1/web/search/item（a_bogus 签名，Cookie 优先游客兜底）
- search_weibo：m.weibo.cn 容器接口 type=60 视频搜索（访客通行证优先，复用 _build_info）

统一返回 list[VideoInfo]；video_id 即可反解标准链接
（bilibili→BV页 / douyin→video页 / weibo→m.weibo.cn/detail）供「解析选中」复用。
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx

from .base import VideoInfo, normalize_datetime, now_str

PLATFORM_LABELS = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博"}


def _cookie(platform: str) -> str:
    try:
        from auth import get_cookie
        return get_cookie(platform) or ""
    except Exception:
        return ""


def parse_url_of(info: VideoInfo) -> str:
    """搜索结果条目 → 标准可解析链接（单独获取页解析复用）。"""
    vid = (info.video_id or "").strip()
    if info.platform == "bilibili" and vid:
        return f"https://www.bilibili.com/video/{vid}"
    if info.platform == "douyin" and vid:
        return f"https://www.douyin.com/video/{vid}"
    if info.platform == "weibo" and vid:
        return f"https://m.weibo.cn/detail/{vid}"
    return info.video_url or info.raw_url


# ==================== B站 ====================

def _parse_bili_duration(text: str) -> int:
    m = re.match(r"^(\d+):(\d{1,2})(?::(\d{1,2}))?$", (text or "").strip())
    if not m:
        try:
            return int(float(text or 0))
        except (TypeError, ValueError):
            return 0
    parts = [int(g) for g in m.groups() if g is not None]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def _build_bili_info(v: dict, keyword: str) -> VideoInfo:
    bvid = v.get("bvid") or f"av{v.get('aid', '')}"
    pic = v.get("pic", "") or ""
    if pic.startswith("//"):
        pic = "https:" + pic
    return VideoInfo(
        platform="bilibili", video_id=bvid,
        title=re.sub(r"<[^>]+>", "", v.get("title", "")),
        description=v.get("description", ""),
        author=v.get("author", ""), author_id=str(v.get("mid", "")),
        play_count=int(v.get("play", 0) or 0),
        comment_count=int(v.get("review", 0) or 0),
        favorite_count=int(v.get("favorites", 0) or 0),
        duration=_parse_bili_duration(v.get("duration", "")),
        cover_url=pic,
        video_url=f"https://www.bilibili.com/video/{bvid}",
        publish_time=normalize_datetime(v.get("pubdate")),
        tags=[t.strip() for t in (v.get("tag") or "").split(",") if t.strip()],
        raw_url=f"https://search.bilibili.com/all?keyword={keyword}",
        raw_data=v, fetched_at=now_str(),
    )


def _bili_search_pages(keyword: str, count: int, cookie: str = "") -> list:
    """B站视频搜索翻页（WBI 签名）。cookie 空=游客态。"""
    from bili import wbi as bili_wbi

    results = []
    client = httpx.Client(timeout=15, follow_redirects=True)
    try:
        page = 1
        while len(results) < count and page <= 5:
            url = bili_wbi.sign_wbi(
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                {"search_type": "video", "keyword": keyword, "page": page}, client)
            headers = {
                "User-Agent": bili_wbi.UA,
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com/",
                "Cookie": bili_wbi.build_cookie_header(client, cookie),
            }
            r = client.get(url, headers=headers)
            js = r.json()
            if js.get("code") != 0:
                raise RuntimeError(f"code={js.get('code')} {js.get('message', '')}")
            items = [v for v in (js.get("data") or {}).get("result") or []
                     if v.get("type") == "video"]
            for v in items:
                results.append(_build_bili_info(v, keyword))
            if len(items) < 20:
                break
            page += 1
    finally:
        client.close()
    return results[:count]


def search_bilibili(keyword: str, count: int = 20) -> list:
    """B站视频搜索：游客态优先；游客被软限流（code=0 空 result，搜索接口
    特有，不走 api_get 的 -352 回退链）时接入登录 Cookie 重试。
    """
    ck = _cookie("bilibili")
    try:
        results = _bili_search_pages(keyword, count, cookie="")
    except RuntimeError as e:
        if not ck:
            raise
        print(f"[B站搜索] 游客请求失败（{e}），接入 Cookie 重试...")
        results = []
    if not results and ck:
        print("[B站搜索] 游客态未返回结果（可能被限流），接入 Cookie 重试...")
        results = _bili_search_pages(keyword, count, cookie=ck)
    if not results:
        raise RuntimeError("B站搜索未返回结果（游客被限流；可在「Cookie 管理」登录B站后重试）")
    print(f"[B站搜索] 获取到 {len(results)} 条")
    return results


# ==================== 抖音 ====================

def _douyin_search_http(keyword: str, count: int, cookie: str) -> tuple:
    """HTTP 直调 search/item。返回 (results, err)，err 非空=本层失败。"""
    import requests
    from multi_downloader import UA_PC, _douyin_abogus
    from .douyin import DouyinParser

    s = requests.Session()
    s.headers.update({"User-Agent": UA_PC, "Referer": "https://www.douyin.com/"})
    if cookie:
        s.headers["Cookie"] = cookie
    else:
        try:
            s.get("https://www.douyin.com/", timeout=15)
        except Exception:
            pass

    results = []
    offset = 0
    seen = set()
    for _page in range(5):
        params = {
            "device_platform": "webapp", "aid": "6383",
            "channel": "channel_pc_web", "search_channel": "aweme_video_web",
            "keyword": keyword, "search_source": "normal_search",
            "query_correct_type": "1", "is_filter_search": "0",
            "from_group_id": "", "offset": str(offset), "count": "15",
            "list_type": "multi", "update_version_code": "170400",
            "pc_client_type": "1", "support_qa_low_latency": "0",
            "sort_type": "0", "publish_time": "0", "filter_duration": "0",
            "source": "normal_search",
        }
        query = urlencode(params)
        a_bogus = _douyin_abogus(query, UA_PC)
        if not a_bogus:
            return [], "a_bogus 签名失败（node 异常）"
        try:
            r = s.get(
                f"https://www.douyin.com/aweme/v1/web/search/item/?{query}&a_bogus={a_bogus}",
                timeout=20)
            if not r.text.strip():
                return [], "被风控拦截（空响应）"
            d = r.json()
        except Exception as e:
            return [], str(e)
        if d.get("status_code") != 0:
            return [], f"status_code={d.get('status_code')}"
        items = d.get("data") or []
        taken = 0
        for item in items:
            aweme = item.get("aweme_info") or {}
            aid = str(aweme.get("aweme_id") or "")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            info = DouyinParser()._build_from_aweme(aweme, aid)
            info.raw_url = f"https://www.douyin.com/search/{keyword}"
            results.append(info)
            taken += 1
            if len(results) >= count:
                break
        if taken == 0 or len(results) >= count:
            break
        offset += 15
    return results, ""


def _douyin_search_pw(keyword: str, count: int, cookie: str) -> list:
    """Playwright 兜底：打开搜索页拦截 search/item 网络响应。

    2026-09 实测搜索接口风控等级高于用户时间线：无头/有头/游客均可能
    跳「验证码中间页」——那是环境级风控，只能等待解除或换网络。
    """
    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch as pw_launch
    from .douyin import DouyinParser

    class _Abort(Exception):
        pass

    results = []
    with sync_playwright() as p:
        browser = pw_launch(p, headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage",
        ])
        try:
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"),
                viewport={"width": 1920, "height": 1080}, locale="zh-CN",
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
                window.chrome = { runtime: {} };
            """)
            if cookie:
                for item in cookie.split("; "):
                    if "=" in item:
                        name, value = item.split("=", 1)
                        try:
                            context.add_cookies([{"name": name, "value": value,
                                                  "domain": ".douyin.com", "path": "/"}])
                        except Exception:
                            pass
            page = context.new_page()
            collected = []

            def on_response(response):
                if response.status != 200 or "search/item" not in response.url:
                    return
                try:
                    for it in response.json().get("data") or []:
                        a = it.get("aweme_info") or {}
                        if a.get("aweme_id"):
                            collected.append(a)
                except Exception:
                    pass

            page.on("response", on_response)
            from urllib.parse import quote
            try:
                page.goto(f"https://www.douyin.com/search/{quote(keyword)}?type=video",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                # 验证码中间页：环境级风控，等/滚都没用，快速放弃
                if "验证" in page.title():
                    print("[抖音搜索] Playwright 命中验证码中间页（环境级风控）")
                    raise _Abort()
                page.wait_for_timeout(2000)
                for _ in range(4):
                    if len(collected) >= count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2500)
            except _Abort:
                pass
            for a in collected[:count]:
                info = DouyinParser()._build_from_aweme(a, str(a.get("aweme_id", "")))
                info.raw_url = f"https://www.douyin.com/search/{keyword}"
                results.append(info)
        except Exception as e:
            print(f"[抖音搜索] Playwright 失败: {e}")
        finally:
            browser.close()
    return results


def _douyin_search_manual(keyword: str, count: int, cookie: str, notify) -> list:
    """有头浏览器手动过验证码：弹可见窗口给用户操作，过完后拦截搜索结果。

    验证通过后回存会话 Cookie（ttwid/msToken 等）到 Cookie 管理，
    后续 HTTP 直调即可带新鲜指纹，无需每次手动验证。
    用户中途关窗/超时则返回已收集的部分（可能为空）。
    """
    import time
    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch as pw_launch
    from .douyin import DouyinParser
    from urllib.parse import quote

    results = []
    collected = []
    with sync_playwright() as p:
        browser = pw_launch(p, headless=False, args=[  # 有头：用户要看得见才能操作
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage",
        ])
        try:
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 860}, locale="zh-CN",
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
                window.chrome = { runtime: {} };
            """)
            if cookie:
                for item in cookie.split("; "):
                    if "=" in item:
                        name, value = item.split("=", 1)
                        try:
                            context.add_cookies([{"name": name, "value": value,
                                                  "domain": ".douyin.com", "path": "/"}])
                        except Exception:
                            pass
            page = context.new_page()

            def on_response(response):
                if response.status != 200 or "search/item" not in response.url:
                    return
                try:
                    for it in response.json().get("data") or []:
                        a = it.get("aweme_info") or {}
                        if a.get("aweme_id"):
                            collected.append(a)
                except Exception:
                    pass

            page.on("response", on_response)
            search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                if "验证" in page.title():
                    if notify:
                        notify("🔍 抖音搜索触发验证码：请在弹出的浏览器窗口中手动完成验证"
                               "（3 分钟内有效，完成后自动继续搜索）", "warning")
                    print("[抖音搜索] 等待用户手动完成验证码...")
                    deadline = time.time() + 180
                    while time.time() < deadline:
                        page.wait_for_timeout(1000)
                        if "验证" not in page.title():
                            break
                    # 验证后一般自动跳回搜索页；多等一会让结果加载，没跳回就重新导航
                    page.wait_for_timeout(5000)
                    if "/search/" not in page.url:
                        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(4000)
                # 滚动加载收满 count 条
                for _ in range(6):
                    if len(collected) >= count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[抖音搜索] 手动验证模式中断（窗口被关闭或超时）: {e}")
            # 回存验证后的会话 Cookie：下次搜索直接走 HTTP 直调，不再弹验证
            try:
                cks = context.cookies("https://www.douyin.com")
                merged = "; ".join(f"{c['name']}={c['value']}" for c in cks)
                if merged:
                    from auth import set_cookie
                    set_cookie("douyin", merged)
                    print("[抖音搜索] 会话 Cookie 已回存（下次搜索更稳）")
                    if notify and collected:
                        notify("✅ 抖音验证通过，Cookie 已回存，下次搜索无需再验证", "success")
            except Exception:
                pass
            for a in collected[:count]:
                info = DouyinParser()._build_from_aweme(a, str(a.get("aweme_id", "")))
                info.raw_url = f"https://www.douyin.com/search/{keyword}"
                results.append(info)
        except Exception as e:
            print(f"[抖音搜索] 手动验证模式失败: {e}")
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return results


def search_douyin(keyword: str, count: int = 20, notify=None) -> list:
    """抖音视频搜索：① HTTP 直调（Cookie→游客）→ ② 无头 Playwright →
    ③ 有头浏览器手动过验证码（弹窗给用户，过完回存 Cookie）。

    notify(msg, level) 用于向 GUI 主线程发提示（验证码弹窗时告知用户）。
    """
    ck = _cookie("douyin")
    last_err = ""
    for cookie in filter(None, [ck, ""]):
        results, err = _douyin_search_http(keyword, count, cookie)
        if results:
            print(f"[抖音搜索] HTTP 直调获取到 {len(results)} 条")
            return results
        last_err = err
        if cookie:
            print(f"[抖音搜索] Cookie 态未获取到数据（{err}），回退游客态...")
    # 无头 Playwright 兜底
    results = _douyin_search_pw(keyword, count, ck)
    if results:
        print(f"[抖音搜索] Playwright 获取到 {len(results)} 条")
        return results
    # 有头手动验证兜底：弹浏览器让用户过验证码
    results = _douyin_search_manual(keyword, count, ck, notify)
    if results:
        print(f"[抖音搜索] 手动验证模式获取到 {len(results)} 条")
        return results
    raise RuntimeError(
        f"抖音搜索被风控拦截（{last_err or '验证码'}）：手动验证未完成或验证后仍无结果，"
        "可稍后重试，或在「Cookie 管理」更新抖音登录后重试")


# ==================== 微博 ====================

def search_weibo(keyword: str, count: int = 20) -> list:
    """微博视频搜索（m.weibo.cn 容器 type=60，每页 10 条，翻页到 count）。"""
    from .weibo import WeiboParser

    wp = WeiboParser()
    # Cookie 优先级：用户配置 > 访客通行证（搜索接口游客直连常被拦）
    cookie = _cookie("weibo") or wp._get_visitor_cookie() or ""
    headers = {
        "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                       "Mobile/15E148 Safari/604.1"),
        "Referer": "https://m.weibo.cn/",
    }
    if cookie:
        headers["Cookie"] = cookie

    client = httpx.Client(headers=headers, timeout=15, follow_redirects=True)
    results = []
    try:
        page = 1
        while len(results) < count and page <= 5:
            try:
                r = client.get("https://m.weibo.cn/api/container/getIndex", params={
                    "containerid": f"100103type=60&q={keyword}",
                    "page_type": "searchall", "page": page,
                })
                data = r.json()
            except Exception as e:
                if page == 1:
                    raise RuntimeError(f"微博搜索请求失败：{e}")
                break
            if data.get("ok") != 1:
                if page == 1:
                    raise RuntimeError("微博搜索接口被拦（未获取到数据）")
                break
            cards = (data.get("data") or {}).get("cards") or []
            taken = 0
            for card in cards:
                mblogs = []
                if card.get("mblog"):
                    mblogs.append(card["mblog"])
                for g in card.get("card_group") or []:
                    if g.get("mblog"):
                        mblogs.append(g["mblog"])
                for mb in mblogs:
                    page_info = mb.get("page_info") or {}
                    # type=60 是视频容器，但混入的广告/热词卡无 page_info.video 字段
                    if not (page_info.get("type") == "video" or page_info.get("media_info")):
                        continue
                    wid = mb.get("bid") or str(mb.get("id") or "")
                    if not wid:
                        continue
                    info = wp._build_info(
                        mb, wid, f"https://s.weibo.com/weibo?q={keyword}", client)
                    if info:
                        results.append(info)
                        taken += 1
                        if len(results) >= count:
                            break
                if len(results) >= count:
                    break
            if taken == 0:
                break
            page += 1
    finally:
        client.close()
    if not results:
        raise RuntimeError("微博搜索未获取到结果（关键词可能无视频内容）")
    print(f"[微博搜索] 获取到 {len(results)} 条")
    return results


# ==================== 统一入口 ====================

def search_videos(platform: str, keyword: str, count: int = 20, notify=None) -> list:
    """platform: bilibili / douyin / weibo / all（全部=三平台并发，失败平台降级跳过）。

    notify(msg, level) 转发给抖音搜索（验证码弹窗时提示用户手动操作）。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise RuntimeError("请输入搜索关键词")
    platform = (platform or "all").strip().lower()

    if platform == "all":
        from concurrent.futures import ThreadPoolExecutor
        results = []
        errors = []
        # 抖音可能弹有头浏览器等用户手动过验证码（最多 3 分钟），等待上限放宽
        timeouts = {"bilibili": 90, "weibo": 90, "douyin": 330}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(search_videos, p, keyword, count, notify): p
                       for p in ("bilibili", "douyin", "weibo")}
            for fut, p in futures.items():
                try:
                    results.extend(fut.result(timeout=timeouts[p]))
                except Exception as e:
                    errors.append(f"{PLATFORM_LABELS.get(p, p)}: {e}")
                    print(f"[搜索] {PLATFORM_LABELS.get(p, p)} 失败: {e}")
        if not results:
            raise RuntimeError("全部平台搜索失败：" + "；".join(errors))
        return results

    if platform == "bilibili":
        return search_bilibili(keyword, count)
    if platform == "douyin":
        return search_douyin(keyword, count, notify)
    if platform == "weibo":
        return search_weibo(keyword, count)
    raise RuntimeError(f"不支持的平台：{platform}")
