"""闲鱼只读适配器，协议依据 goofish.com 官方 PC 搜索与商品页面。

能力矩阵：关键词搜索首批最多 30 件 / 商品详情，匿名公开页 HTTP →
匿名临时浏览器 → 已启用登录态临时浏览器；不翻页、不登录、不保存会话。
平台要求登录或人工验证时明确停止，不绕过验证。无时间线/追踪/交易能力。
响应字段参考官方 xy-site/0.0.188 的 p_search-index.js、p_item-index.js。
适配器不写资料库、不依赖 Qt，凭据由调用方的统一管理层快照注入。
"""
from __future__ import annotations

import json
import hashlib
import re
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlsplit

import httpx

MAX_ITEMS = 30
MAX_BODY = 2 * 1024 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"


class XianyuError(ValueError):
    code = "xianyu_error"


class AuthRequired(XianyuError):
    code = "auth_required"


class TicketRequired(AuthRequired):
    code = "temporary_ticket_required"


class Blocked(XianyuError):
    code = "blocked"


class RateLimited(XianyuError):
    code = "rate_limited"


class ParseChanged(XianyuError):
    code = "parse_changed"


class NetworkError(XianyuError):
    code = "network_error"


class NotFound(XianyuError):
    code = "not_found"


class Cancelled(XianyuError):
    code = "cancelled"


def check_cancel(cancel=None):
    if cancel and cancel():
        raise Cancelled("已取消闲鱼任务，已保存的商品资料予以保留")


def item_id(text: str) -> str:
    """仅接受商品 ID 或官方完整商品链接；不展开任意短链。"""
    text = str(text or "").strip()
    if re.fullmatch(r"\d{5,24}", text):
        return text
    for candidate in re.findall(r"https?://[^\s<>\"'，。】]+", text):
        parsed = urlsplit(candidate)
        if (parsed.scheme in {"https", "http"} and not parsed.username
                and parsed.port in {None, 80, 443}
                and (parsed.hostname or "").lower() in {
                    "www.goofish.com", "goofish.com", "h5.m.goofish.com",
                    "2.taobao.com", "h5.m.taobao.com"}
                and (parsed.path.rstrip("/") in {"/item", "/item.htm"}
                     or parsed.path.startswith("/app/idleFish-F2e/"))):
            value = parse_qs(parsed.query).get("id", [""])[0]
            if re.fullmatch(r"\d{5,24}", value):
                return value
    raise ValueError("请粘贴闲鱼完整商品链接（goofish.com/item?id=…）")


def item_url(value: str) -> str:
    return "https://www.goofish.com/item?id=" + item_id(value)


def image_url(value) -> str:
    value = str(value or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme in {"https", "http"} and not parsed.username
                and parsed.port in {None, 80, 443}
                and (host == "alicdn.com" or host.endswith(".alicdn.com"))):
            return parsed._replace(scheme="https", fragment="").geturl()
    except ValueError:
        pass
    return ""


def _text(value, limit=1000):
    return str(value or "").strip()[:limit] if not isinstance(value, (dict, list)) else ""


def _price(value):
    if isinstance(value, list):
        return "".join(_text(part.get("text"), 40) for part in value
                       if isinstance(part, dict))[:100]
    return _text(value, 100)


def _data(payload):
    if not isinstance(payload, dict):
        raise ParseChanged("闲鱼返回格式已变化，请稍后重试")
    ret = payload.get("ret") or []
    ret = [ret] if isinstance(ret, str) else ret
    codes = " ".join(str(code).split("::", 1)[0] for code in ret).upper()
    if any(word in codes for word in ("USER_VALIDATE", "RGV", "CAPTCHA", "ACCESS_DENIED")):
        raise Blocked("闲鱼暂时拒绝访问或需要人工验证，请稍后重试")
    if any(word in codes for word in ("LIMIT", "FREQUENT", "TRAFFIC")):
        raise RateLimited("闲鱼请求过于频繁，请稍后再试")
    if any(word in codes for word in ("SESSION", "LOGIN", "AUTH")):
        raise AuthRequired("闲鱼需要登录或登录已失效，请登录后重试")
    if "TOKEN" in codes:
        raise TicketRequired("闲鱼临时验证尚未完成，请稍后重试")
    if any(word in codes for word in ("NOT_EXIST", "NOT_FOUND", "ITEM_DELETED")):
        raise NotFound("闲鱼商品不存在或已下架")
    if ret and not any(str(code).upper().startswith("SUCCESS") for code in ret):
        raise ParseChanged("闲鱼未能返回商品资料，请稍后重试")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ParseChanged("闲鱼返回格式已变化，请稍后重试")
    return data


def parse_search(payload, limit=MAX_ITEMS) -> list[dict]:
    data = _data(payload)
    source = data.get("resultList")
    if not isinstance(source, list):
        raise ParseChanged("闲鱼搜索结构已变化，请稍后重试")
    rows, seen = [], set()
    for entry in source:
        if not isinstance(entry, dict):
            continue
        obj = entry.get("data", entry)
        if not isinstance(obj, dict):
            continue
        nested = obj.get("item", {}).get("main", {}) if isinstance(obj.get("item"), dict) else {}
        obj = nested.get("exContent", obj) if isinstance(nested, dict) else obj
        if not isinstance(obj, dict):
            continue
        value = str(obj.get("itemId") or obj.get("id") or "")
        if not re.fullmatch(r"\d{5,24}", value) or value in seen:
            continue
        title = _text(obj.get("title"))
        if not title:
            continue
        user = obj.get("userInfo") or {}
        rows.append({"item_id": value, "title": title, "price": _price(obj.get("price")),
                     "sales": "", "seller": _text(user.get("nick") if isinstance(user, dict) else "", 100),
                     "location": _text(obj.get("area") or obj.get("city"), 100),
                     "url": item_url(value), "images": [url] if (url := image_url(obj.get("picUrl"))) else []})
        seen.add(value)
        if len(rows) >= max(1, min(MAX_ITEMS, int(limit))):
            break
    if source and not rows:
        raise ParseChanged("闲鱼搜索结果缺少商品字段，请稍后重试")
    return rows


def parse_detail(payload, expected_id: str) -> dict:
    data = _data(payload)
    data = data.get("itemDetailDO", data)
    item = data.get("itemDO") if isinstance(data, dict) else None
    seller = data.get("sellerDO") or {} if isinstance(data, dict) else {}
    if not isinstance(item, dict):
        raise ParseChanged("闲鱼商品结构已变化，请稍后重试")
    value = item_id(str(item.get("itemId") or item.get("id") or expected_id))
    if value != item_id(expected_id):
        raise ParseChanged("闲鱼返回了其他商品，已停止保存")
    description = _text(item.get("desc"), 30000)
    title = _text(item.get("title") or description.split("\n")[0])
    if not title:
        raise ParseChanged("闲鱼商品缺少标题，请稍后重试")
    images = []
    for entry in (item.get("imageInfos") or [])[:30]:
        url = image_url(entry.get("url") if isinstance(entry, dict) else entry)
        if url and url not in images:
            images.append(url)
    return {"item_id": value, "title": title, "description": description,
            "price": _price(item.get("soldPrice") or item.get("price")), "sales": "",
            "seller": _text(seller.get("nick") or seller.get("nickname"), 100),
            "location": _text(item.get("location") or item.get("city"), 100),
            "status": _text(item.get("itemStatusStr"), 100),
            "url": item_url(value), "images": images}


def _public_payload(url, parser, cancel):
    """匿名页中有 SSR JSON 时直接解析；壳页再走临时浏览器。"""
    check_cancel(cancel)
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=8), follow_redirects=False) as client:
            with client.stream("GET", url, headers={"User-Agent": UA}) as response:
                if response.status_code == 429:
                    raise RateLimited("闲鱼请求过于频繁，请稍后再试")
                if response.status_code == 403:
                    raise Blocked("闲鱼暂时拒绝访问，请稍后重试")
                if response.status_code == 404:
                    raise NotFound("闲鱼商品不存在或已下架")
                if response.status_code != 200:
                    return None
                body = bytearray()
                for chunk in response.iter_bytes():
                    check_cancel(cancel)
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise ParseChanged("闲鱼页面过大，已停止读取")
                html = bytes(body).decode("utf-8", "replace")
        for match in re.finditer(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                return parser(json.loads(match.group(1)))
            except (json.JSONDecodeError, ParseChanged):
                continue
    except httpx.HTTPError:
        return None
    return None


def _browser_payload(url, api, parser, cookie, cancel):
    check_cancel(cancel)
    result, errors = [], []
    try:
        from playwright.sync_api import sync_playwright
        from toolbox.browser import launch
        with sync_playwright() as playwright:
            browser = launch(playwright, headless=True)
            try:
                context = browser.new_context(user_agent=UA, locale="zh-CN",
                                              viewport={"width": 1280, "height": 900})
                if cookie:
                    jar = SimpleCookie()
                    jar.load(cookie)
                    context.add_cookies([{"name": key, "value": morsel.value,
                                          "domain": ".goofish.com", "path": "/", "secure": True}
                                         for key, morsel in jar.items()])
                page = context.new_page()

                def capture(response):
                    host = (urlsplit(response.url).hostname or "").lower()
                    if not (host == "goofish.com" or host.endswith(".goofish.com")) or api not in response.url:
                        return
                    try:
                        if response.status == 429:
                            raise RateLimited("闲鱼请求过于频繁，请稍后再试")
                        if response.status == 403:
                            raise Blocked("闲鱼暂时拒绝访问，请稍后重试")
                        size = response.headers.get("content-length", "")
                        if size.isdigit() and int(size) > MAX_BODY:
                            raise ParseChanged("闲鱼返回内容过大，已停止读取")
                        if "json" not in response.headers.get("content-type", "").lower():
                            raise ParseChanged("闲鱼未返回商品数据，请稍后重试")
                        raw = response.body()
                        if len(raw) > MAX_BODY:
                            raise ParseChanged("闲鱼返回内容过大，已停止读取")
                        result.append(parser(json.loads(raw)))
                    except XianyuError as exc:
                        errors.append(exc)
                    except (ValueError, TypeError):
                        errors.append(ParseChanged("闲鱼返回格式已变化，请稍后重试"))

                page.on("response", capture)
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                deadline = time.monotonic() + 18
                # 首个请求可能只建立内存游客票据，官网随后自动重试；不自行刷新登录态。
                while not result and time.monotonic() < deadline:
                    check_cancel(cancel)
                    if errors and not isinstance(errors[-1], AuthRequired):
                        raise errors[-1]
                    page.wait_for_timeout(200)
                if result:
                    return result[0]
                if errors:
                    raise errors[-1]
                visible = page.locator("body").inner_text(timeout=2000)[:5000]
                if any(word in visible for word in ("验证码", "滑动验证", "安全验证", "访问受限")):
                    raise Blocked("闲鱼需要人工验证，请稍后重试")
                if "登录" in visible:
                    raise AuthRequired("闲鱼需要登录或登录已失效，请登录后重试")
                raise ParseChanged("闲鱼未返回可读取的商品资料，请稍后重试")
            finally:
                browser.close()
    except XianyuError:
        raise
    except ImportError as exc:
        raise NetworkError("闲鱼搜索需要采集引擎，请在全局设置检查依赖") from exc
    except Exception as exc:
        raise NetworkError("闲鱼连接失败，请检查网络与全局设置中的采集引擎后重试") from exc


def _fetch(url, api, parser, credential, on_log, is_cancelled):
    if on_log:
        on_log("读取闲鱼公开页面")
    try:
        result = _public_payload(url, parser, is_cancelled)
    except AuthRequired:
        result = None
    if result is not None:
        return result
    try:
        if on_log:
            on_log("读取闲鱼临时页面")
        return _browser_payload(url, api, parser, "", is_cancelled)
    except AuthRequired:
        cookie = credential() if credential else ""
        if not cookie:
            raise
        check_cancel(is_cancelled)
        if on_log:
            on_log("使用当前闲鱼登录态只读请求")
        return _browser_payload(url, api, parser, cookie, is_cancelled)


def search(keyword, limit=MAX_ITEMS, *, credential=None, on_log=None, is_cancelled=None):
    keyword = str(keyword or "").strip()
    if not keyword or len(keyword) > 100 or "http://" in keyword or "https://" in keyword:
        raise ValueError("请输入 1–100 字的闲鱼搜索关键词，商品链接请使用解析")
    return _fetch("https://www.goofish.com/search?q=" + quote(keyword),
                  "mtop.taobao.idlemtopsearch.pc.search", lambda data: parse_search(data, limit),
                  credential, on_log, is_cancelled)


def fetch_item(text, *, credential=None, on_log=None, is_cancelled=None):
    value = item_id(text)
    return _fetch(item_url(value), "mtop.taobao.idle.pc.detail",
                  lambda data: parse_detail(data, value), credential, on_log, is_cancelled)


def parse_login(payload):
    """官网导航使用 data.module.base.displayName 判定登录，不把本地ID当成功。"""
    try:
        data = _data(payload)
    except AuthRequired:
        ret = payload.get("ret") or []
        codes = " ".join(str(value).split("::", 1)[0] for value in
                         ([ret] if isinstance(ret, str) else ret)).upper()
        if any(code in codes for code in ("SESSION_EXPIRED", "NEED_LOGIN", "NOT_LOGIN")):
            return {"ok": False, "username": "", "detail": "闲鱼登录已失效"}
        raise
    module = data.get("module")
    base = module.get("base") if isinstance(module, dict) else None
    if not isinstance(base, dict):
        raise ParseChanged("闲鱼登录验证结构已变化，请稍后重试")
    raw_name = base.get("displayName", "")
    if not isinstance(raw_name, str):
        raise ParseChanged("闲鱼登录验证字段已变化，请稍后重试")
    name = raw_name.strip()[:100]
    return {"ok": bool(name), "username": name,
            "detail": "" if name else "闲鱼尚未返回已登录账号"}


def _verify_http(cookie):
    """只读官方导航接口；响应票据仅在请求内存中，绝不写管理层。"""
    jar = SimpleCookie()
    jar.load(cookie)
    morsel = jar.get("_m_h5_tk")
    token = morsel.value.split("_", 1)[0] if morsel else ""
    if not token:
        raise TicketRequired("闲鱼需要临时页面验证")
    timestamp = str(int(time.time() * 1000))
    data = "{}"
    app_key = "34839810"
    sign = hashlib.md5(f"{token}&{timestamp}&{app_key}&{data}".encode()).hexdigest()
    api = "mtop.idle.web.user.page.nav"
    with httpx.Client(timeout=httpx.Timeout(15, connect=8), follow_redirects=False) as client:
        with client.stream("GET", f"https://h5api.m.goofish.com/h5/{api}/1.0/",
                           params={"jsv": "2.7.3", "appKey": app_key, "t": timestamp,
                                   "sign": sign, "api": api, "v": "1.0", "type": "originaljson",
                                   "dataType": "json", "accountSite": "xianyu", "data": data},
                           headers={"User-Agent": UA, "Cookie": cookie,
                                    "Referer": "https://www.goofish.com/", "Accept": "application/json"}) as response:
            if response.status_code == 429:
                raise RateLimited("闲鱼请求过于频繁，请稍后验证")
            if response.status_code == 403:
                raise Blocked("闲鱼暂时拒绝验证，请稍后重试")
            if response.status_code != 200:
                raise NetworkError("闲鱼验证连接失败，请稍后重试")
            if "json" not in response.headers.get("content-type", "").lower():
                raise ParseChanged("闲鱼未返回登录验证数据，请稍后重试")
            raw = bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > MAX_BODY:
                    raise ParseChanged("闲鱼验证返回内容过大，已停止读取")
            try:
                return parse_login(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise ParseChanged("闲鱼验证返回格式已变化，请稍后重试") from exc


def verify_login(cookie):
    """统一账户管理验证契约；明确失效=False，网络/风控/结构变化=None。"""
    if not cookie:
        return {"ok": False, "username": "", "detail": "尚未登录闲鱼"}
    try:
        try:
            return _verify_http(cookie)
        except AuthRequired:
            # H5临时票据不等于账号失效，由官网在独立内存会话完成一次只读验证。
            return _browser_payload("https://www.goofish.com/", "mtop.idle.web.user.page.nav",
                                    parse_login, cookie, None)
    except TicketRequired:
        return {"ok": None, "username": "", "detail": "闲鱼临时验证尚未完成，请稍后重试"}
    except AuthRequired:
        return {"ok": False, "username": "", "detail": "闲鱼登录已失效"}
    except XianyuError as exc:
        return {"ok": None, "username": "", "detail": str(exc)}
    except (httpx.HTTPError, ValueError):
        return {"ok": None, "username": "", "detail": "闲鱼验证网络不可用，请稍后重试"}
