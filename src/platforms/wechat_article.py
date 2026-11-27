"""微信公开图文与历史列表适配；不获取、刷新或保存任何登录信息。

历史列表使用未公开的 profile_ext 接口，只消费统一管理层短期阅读会话。
访问控制、限流、失效和响应变化均明确停止，不自动切换账号或绕过验证。
"""
from __future__ import annotations

import html as html_lib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Comment

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
WX_URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/[^\s<>\"']+", re.I)
_IMAGE_HOSTS = {"mmbiz.qpic.cn", "mmbiz.qlogo.cn", "wx.qlogo.cn"}
_SECRET_NAMES = {"key", "uin", "pass_ticket", "appmsg_token", "wxtoken", "token"}
_MAX_HTML, _MAX_IMAGE, _MAX_PIXELS = 8 * 1024 * 1024, 20 * 1024 * 1024, 20_000_000


class WechatAdapterError(ValueError):
    code = "WechatAdapterError"
    platform = "wechat"

    def __init__(self, message="微信公众号访问失败", *, phase="", http_status=None):
        super().__init__(message)
        self.phase, self.http_status = phase, http_status


class AuthRequired(WechatAdapterError):
    code = "AuthRequired"


class RateLimited(WechatAdapterError):
    code = "RateLimited"


class NotFound(WechatAdapterError):
    code = "NotFound"


class ParseChanged(WechatAdapterError):
    code = "ParseChanged"


class NetworkError(WechatAdapterError):
    code = "NetworkError"


class Blocked(WechatAdapterError):
    code = "Blocked"


class Unsupported(WechatAdapterError):
    code = "Unsupported"


def _parts(url):
    try:
        value = html_lib.unescape(str(url or "").strip())
        if len(value) > 32768 or any(ord(c) < 32 for c in value):
            raise ValueError
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.username or parts.password
                or parts.port not in (None, 443) or not parts.hostname):
            raise ValueError
        return parts
    except (ValueError, TypeError):
        raise Unsupported("链接格式不受支持", phase="url") from None


def canonical_article_url(url: str) -> str:
    """公开文章主键：只保留严格白名单的公开参数。"""
    parts = _parts(url)
    if parts.hostname != "mp.weixin.qq.com":
        raise Unsupported("只支持微信公众平台文章链接", phase="url")
    if re.fullmatch(r"/s/[A-Za-z0-9_-]{1,128}", parts.path):
        return "https://mp.weixin.qq.com" + parts.path
    if parts.path != "/s":
        raise Unsupported("只支持微信公众平台图文链接", phase="url")
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=80)
    except ValueError:
        raise Unsupported("文章链接参数不正确", phase="url") from None
    values = {}
    for key, value in pairs:
        if key in ("__biz", "mid", "idx", "sn"):
            if key in values:
                raise Unsupported("文章链接参数重复", phase="url")
            values[key] = value
    if (not re.fullmatch(r"[A-Za-z0-9_+/=-]{4,256}", values.get("__biz", ""))
            or not re.fullmatch(r"[0-9]{1,24}", values.get("mid", ""))
            or not re.fullmatch(r"[0-9]{1,3}", values.get("idx", ""))
            or not re.fullmatch(r"[A-Fa-f0-9]{16,64}", values.get("sn", ""))):
        raise Unsupported("文章链接缺少有效公开参数", phase="url")
    return "https://mp.weixin.qq.com/s?" + urlencode(
        {key: values[key] for key in ("__biz", "mid", "idx", "sn")})


def extract_urls(text: str) -> List[str]:
    seen, result = set(), []
    for match in WX_URL_RE.finditer(html_lib.unescape(text or "")):
        try:
            value = canonical_article_url(match.group(0).rstrip("），。；;])！!、"))
        except WechatAdapterError:
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _redact_text(value):
    names = r"(?:key|uin|pass_ticket|appmsg_token|wxtoken|token)"
    return re.sub(r"(?i)(?:\b|%26|%3f)" + names + r"(?:=|%3d|%253d)[^\s&<>\"']*",
                  "[已移除阅读信息]", str(value or ""))


def _clean_url(value, *, image=False):
    try:
        value = html_lib.unescape(str(value or ""))
        if value.startswith("//"):
            value = "https:" + value
        if image and value.startswith("http://"):
            value = "https://" + value[7:]
        parts = _parts(value)
        if image and parts.hostname not in _IMAGE_HOSTS:
            return ""
        if parts.hostname == "mp.weixin.qq.com" and parts.path.startswith("/s"):
            return canonical_article_url(value)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True, max_num_fields=80)
                 if k.lower() not in _SECRET_NAMES]
        return urlunsplit(("https", parts.hostname, parts.path, urlencode(query), ""))
    except (WechatAdapterError, ValueError):
        return ""


@dataclass
class WechatImage:
    url: str
    fmt: str = "jpeg"
    data: bytes = b""
    width: int = 0
    height: int = 0


@dataclass
class WechatArticle:
    url: str
    title: str = ""
    account: str = ""
    author: str = ""
    publish_time: str = ""
    content_html: str = ""
    images: List[WechatImage] = field(default_factory=list)
    biz: str = ""
    avatar: str = ""

    def image_by_url(self, url: str) -> Optional[WechatImage]:
        return next((image for image in self.images if image.url == url), None)


def _make_session():
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return session


class _PublicOnlyAuth(requests.auth.AuthBase):
    def __call__(self, request):
        # 覆盖调用方 session.auth / netrc；图片与公开请求不夹带长期登录信息。
        request.headers.pop("Authorization", None)
        request.headers.pop("Cookie", None)
        return request


def _upstream_article_url(value):
    value = html_lib.unescape(str(value or ""))
    # 平台历史响应仍可能使用明文协议地址；仅将精确微信域升级为 TLS。
    if value.startswith("http://mp.weixin.qq.com/"):
        value = "https://" + value[7:]
    return canonical_article_url(value)


def _read_url(session, url, *, kind="html", params=None, authenticated=False):
    """逐跳验证目标、限制响应；异常不含请求地址或服务端原文。"""
    limit = _MAX_IMAGE if kind == "image" else _MAX_HTML
    for hop in range(4):
        allowed = _IMAGE_HOSTS if kind == "image" else {"mp.weixin.qq.com"}
        if _parts(url).hostname not in allowed:
            raise Blocked("平台返回了不受支持的目标", phase=kind)
        response = None
        try:
            response = session.get(
                url, params=params, timeout=(5, 20), allow_redirects=False, stream=True,
                headers={"User-Agent": UA, "Cookie": None, "Authorization": None, "Referer": None},
                auth=_PublicOnlyAuth())
            status = int(response.status_code)
            if status in (301, 302, 303, 307, 308):
                if authenticated or hop == 3:
                    raise Blocked("平台要求重新验证，已停止访问", phase=kind, http_status=status)
                target = urljoin(url, response.headers.get("Location", ""))
                target = _clean_url(target, image=True) if kind == "image" else canonical_article_url(target)
                if not target:
                    raise Blocked("平台返回了不受支持的目标", phase=kind)
                url, params = target, None
                continue
            if status == 429:
                raise RateLimited("请求过于频繁，请稍后再试", phase=kind, http_status=status)
            if status == 401:
                raise AuthRequired("需要有效阅读会话", phase=kind, http_status=status)
            if status in (403, 451):
                raise Blocked("平台暂时拒绝访问", phase=kind, http_status=status)
            if status in (404, 410):
                raise NotFound("内容不存在或已删除", phase=kind, http_status=status)
            if not 200 <= status < 300:
                raise NetworkError("平台请求失败，请稍后再试", phase=kind, http_status=status)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            expected = {"html": ("text/html", "application/xhtml+xml"),
                        "json": ("application/json", "text/javascript", "text/plain"),
                        "image": ("image/",)}[kind]
            if kind == "json" and content_type in ("text/html", "application/xhtml+xml"):
                raise Blocked("平台要求验证，已停止访问", phase=kind)
            if not any(content_type.startswith(item) for item in expected):
                raise ParseChanged("平台响应格式发生变化", phase=kind)
            try:
                content_length = int(response.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                raise ParseChanged("平台响应长度不正确", phase=kind) from None
            if content_length > limit:
                raise ParseChanged("平台响应超过安全大小限制", phase=kind)
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise ParseChanged("平台响应超过安全大小限制", phase=kind)
                chunks.append(chunk)
            return b"".join(chunks)
        except requests.RequestException:
            raise NetworkError("网络异常，请稍后再试", phase=kind) from None
        finally:
            if response is not None:
                response.close()
    raise Blocked("平台跳转次数过多", phase=kind)


def _js_string(page, name):
    match = re.search(r"\b" + re.escape(name) + r"\s*=\s*[\"']([^\"'\r\n]*)[\"']", page)
    return html_lib.unescape(match.group(1)) if match else ""


def fetch_article(url: str, session: Optional[requests.Session] = None) -> WechatArticle:
    public_url = canonical_article_url(url)
    owned, session = session is None, session or _make_session()
    try:
        page = _read_url(session, public_url).decode("utf-8", errors="replace")
    finally:
        if owned:
            session.close()
    soup = BeautifulSoup(page, "html.parser")
    content = soup.find(id="js_content")
    if content is None:
        if "操作频繁" in page:
            raise RateLimited("请求过于频繁，请稍后再试", phase="article")
        if any(item in page for item in ("环境异常", "访问过于频繁", "验证后继续")):
            raise Blocked("平台要求验证，已停止访问", phase="article")
        if any(item in page for item in ("内容已被发布者删除", "该内容已被删除")):
            raise NotFound("内容不存在或已删除", phase="article")
        raise ParseChanged("未找到图文正文，页面可能已变化", phase="article")
    art = WechatArticle(url=public_url)
    og_url = soup.find("meta", property="og:url")
    candidates = [og_url.get("content", "") if og_url else ""]
    biz = _js_string(page, "biz")
    mid = _js_string(page, "mid") or _js_string(page, "appmsgid")
    idx, sn = _js_string(page, "idx"), _js_string(page, "sn")
    if biz and mid and idx and sn:
        candidates.insert(0, "https://mp.weixin.qq.com/s?" + urlencode(
            {"__biz": biz, "mid": mid, "idx": idx, "sn": sn}))
    for candidate in candidates:
        try:
            canonical = _upstream_article_url(candidate)
            if "?" in canonical:
                art.url = canonical
                break
        except WechatAdapterError:
            continue
    art.biz = dict(parse_qsl(urlsplit(art.url).query)).get("__biz", "")
    if not art.biz and re.fullmatch(r"[A-Za-z0-9_+/=-]{4,256}", biz):
        art.biz = biz
    title, og_title = soup.find(id="activity-name"), soup.find("meta", property="og:title")
    art.title = _redact_text(title.get_text(strip=True) if title else
                             (og_title.get("content", "") if og_title else "未命名文章"))
    account, author = soup.find(id="js_name"), soup.find("meta", attrs={"name": "author"})
    art.account = _redact_text(account.get_text(strip=True) if account else _js_string(page, "nickname"))
    art.author = _redact_text(author.get("content", "") if author else "")
    published = soup.find(id="publish_time")
    art.publish_time = _redact_text(published.get_text(strip=True) if published else "")
    if not art.publish_time:
        stamp = re.search(r"\b(?:var\s+)?ct\s*=\s*[\"']?(\d{10})", page)
        if stamp:
            art.publish_time = datetime.fromtimestamp(int(stamp.group(1))).strftime("%Y-%m-%d %H:%M")
    for name in ("hd_head_img", "head_img", "ori_head_img", "round_head_img"):
        art.avatar = _clean_url(_js_string(page, name), image=True)
        if art.avatar:
            break
    _clean_content(soup, content, art)
    art.content_html = content.decode_contents()
    return art


def _clean_content(soup, content, art):
    for node in content.find_all(string=lambda value: isinstance(value, Comment)):
        node.extract()
    for tag in content.find_all(["script", "style", "object", "embed", "form", "input", "svg", "link", "meta"]):
        tag.decompose()
    for selector in (".qr_code_pc", ".qr_code_pc_outer", "mp-common-profile",
                     "mp-common-product", ".reward_area", "#js_sponsor_ad_area"):
        for tag in content.select(selector):
            tag.decompose()
    for tag_name, replacement in (("iframe", "视频内容"), ("mpvoice", "语音消息")):
        for tag in content.find_all(tag_name):
            paragraph = soup.new_tag("p")
            paragraph.string = replacement
            tag.replace_with(paragraph)
    seen = set()
    for image in content.find_all("img"):
        source = _clean_url(image.get("data-src") or image.get("src") or "", image=True)
        if not source:
            image.decompose()
            continue
        image["src"] = source
        if source not in seen:
            seen.add(source)
            art.images.append(WechatImage(url=source))
    allowed = {"href", "src", "alt", "title", "style", "class", "id", "width", "height", "colspan", "rowspan", "align"}
    for tag in content.find_all(True):
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag[attr]
                continue
            value = tag[attr]
            if isinstance(value, list):
                value = " ".join(value)
            if attr == "href":
                value = _clean_url(urljoin(art.url, value))
                if not value:
                    del tag[attr]
                    continue
            if attr == "style":
                value = ";".join(item for item in value.split(";") if not re.search(
                    r"(?i)(url\s*\(|expression|@import|visibility\s*:|position\s*:)", item))
            tag[attr] = _redact_text(value)
    for node in list(content.find_all(string=True)):
        cleaned = _redact_text(str(node))
        if cleaned != str(node):
            node.replace_with(cleaned)


def fetch_history_page(biz, offset, session_snapshot) -> dict:
    """只读单页（最多10组图文），不写业务库、不重试、不刷新会话。"""
    from wechat_session import is_current
    if not session_snapshot or not is_current(session_snapshot):
        raise AuthRequired("阅读会话已失效，需要重新提供", phase="history")
    if not isinstance(biz, str) or not re.fullmatch(r"[A-Za-z0-9_+/=-]{4,256}", biz):
        raise Unsupported("公众号标识不正确", phase="history")
    if biz != session_snapshot.biz:
        raise AuthRequired("当前阅读会话与目标公众号不一致", phase="history")
    try:
        offset = int(offset)
        if not 0 <= offset <= 10_000_000:
            raise ValueError
    except (TypeError, ValueError):
        raise Unsupported("历史列表位置不正确", phase="history") from None
    params = {"action": "getmsg", "__biz": biz, "f": "json", "offset": offset,
              "count": 10, "is_ok": 1, "scene": 124, **session_snapshot._request_parameters()}
    session = _make_session()
    try:
        raw = _read_url(session, "https://mp.weixin.qq.com/mp/profile_ext",
                        kind="json", params=params, authenticated=True)
    finally:
        session.close()
    if not is_current(session_snapshot):
        raise AuthRequired("阅读会话已变化，任务已停止", phase="history")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        ret = int(payload.get("ret", -99999))
    except (TypeError, ValueError):
        raise ParseChanged("历史列表响应格式发生变化", phase="history") from None
    if ret in (-3, -6, -8, -13, 200003):
        raise AuthRequired("阅读会话已失效，需要重新提供", phase="history")
    if ret in (-2, -4, 200013):
        raise RateLimited("请求过于频繁，请稍后再试", phase="history")
    if ret != 0:
        raise Blocked("平台暂时拒绝读取历史列表", phase="history")
    try:
        general = payload["general_msg_list"]
        general = json.loads(general) if isinstance(general, str) else general
        messages = general["list"]
        if not isinstance(messages, list):
            raise ValueError
        continuation, next_offset = int(payload["can_msg_continue"]), int(payload["next_offset"])
        if continuation not in (0, 1) or not 0 <= next_offset <= 10_000_000:
            raise ValueError
        if continuation and next_offset <= offset:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ParseChanged("历史列表结构或分页位置发生变化", phase="history") from None
    articles, seen = [], set()
    for message in messages:
        if not isinstance(message, dict):
            raise ParseChanged("历史消息结构发生变化", phase="history")
        common, item = message.get("comm_msg_info") or {}, message.get("app_msg_ext_info")
        if item is None:
            continue  # 历史也可能包含纯文字/图片等非图文消息。
        if not isinstance(common, dict) or not isinstance(item, dict):
            raise ParseChanged("历史图文结构发生变化", phase="history")
        children = item.get("multi_app_msg_item_list") or []
        if not isinstance(children, list):
            raise ParseChanged("历史多图文结构发生变化", phase="history")
        for record in [item, *children]:
            if not isinstance(record, dict):
                raise ParseChanged("历史图文条目发生变化", phase="history")
            link = record.get("content_url")
            if not link:
                continue  # 删除、视频等记录没有公开图文地址。
            try:
                link = _upstream_article_url(urljoin("https://mp.weixin.qq.com", link))
            except WechatAdapterError:
                raise ParseChanged("历史图文地址格式发生变化", phase="history") from None
            if link in seen:
                continue
            seen.add(link)
            try:
                stamp = int(common.get("datetime", 0))
                published = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else ""
            except (TypeError, ValueError, OverflowError, OSError):
                raise ParseChanged("历史图文时间格式发生变化", phase="history") from None
            articles.append({"url": link, "title": _redact_text(record.get("title", "")),
                             "account": _redact_text(record.get("nickname") or item.get("nickname") or ""),
                             "biz": biz, "publish_time": published, "author": _redact_text(record.get("author", "")),
                             "avatar": _clean_url(item.get("head_img", ""), image=True)})
    return {"articles": articles, "next_offset": next_offset, "has_more": bool(continuation)}


def _image_data(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
            raise ValueError("图片尺寸超过安全限制")
        fmt = str(image.format or "").lower()
        if fmt not in ("jpeg", "png", "gif", "webp"):
            raise ValueError("图片格式不受支持")
        if fmt == "webp":
            image.load()
            if image.mode in ("RGBA", "P", "LA"):
                foreground = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(foreground, mask=foreground.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, "JPEG", quality=92)
            return output.getvalue(), "jpeg", width, height
        image.verify()
        return data, fmt, width, height


def download_images(art: WechatArticle, session: Optional[requests.Session] = None,
                    progress: Optional[Callable[[int, int, str], None]] = None,
                    should_cancel: Optional[Callable[[], bool]] = None,
                    threads: int = 1) -> Tuple[int, int]:
    total, cancel = len(art.images), should_cancel or (lambda: False)

    def fetch(image, supplied=None):
        if cancel():
            return False
        url = _clean_url(image.url, image=True)
        if not url:
            return False
        own, use_session = supplied is None, supplied or _make_session()
        try:
            data = _read_url(use_session, url, kind="image")
            if cancel():
                return False
            data, fmt, width, height = _image_data(data)
            image.url, image.data, image.fmt = url, data, fmt
            image.width, image.height = width, height
            return True
        except (WechatAdapterError, ValueError, OSError):
            return False
        finally:
            if own:
                use_session.close()

    ok = fail = 0
    if threads > 1 and total > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(8, max(1, int(threads)))) as executor:
            futures = []
            for image in art.images:
                if cancel():
                    break
                futures.append(executor.submit(fetch, image))
            for index, future in enumerate(as_completed(futures), 1):
                if future.result():
                    ok += 1
                else:
                    fail += 1
                if progress:
                    progress(index, total, art.title)
        return ok, fail
    for index, image in enumerate(art.images, 1):
        if cancel():
            break
        if fetch(image, session):
            ok += 1
        else:
            fail += 1
        if progress:
            progress(index, total, art.title)
    return ok, fail
