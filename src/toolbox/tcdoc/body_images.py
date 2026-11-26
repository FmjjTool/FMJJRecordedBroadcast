"""腾讯文档正文本地图像：只读 DOM → 无凭据下载 → 内容寻址缓存。

Owner: 本模块是 ``正文图片/`` 的唯一写入方。schema 2 的每页 .index.json
仅保存来源身份哈希及相对路径；不保存 URL、签名或登录态。图片与索引均为派生缓存，
不修改云端，旧文件不清理。archive 只引用结果，桌面与 Web 通过 safe_image_path 读取。

能力：AI 文档/普通文档的正文图像块与正文 img；有界滚动加载；复用当前页面
已成功加载的可信图床响应，缺失时匿名 HTTPS 下载，或读取页面内 blob/data。
不采附件/文件卡片图标、工具区、占位图，不支持 SVG、任意外链、
需附带账户凭据的图床、画布截图或绕过平台访问控制；单张失败保留明确状态。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import time
import warnings
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import httpx
from PIL import Image

from . import config as cfg_mod

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGE_BYTES = 128 * 1024 * 1024
MAX_PIXELS = 50_000_000
MAX_SIDE = 32768
MAX_IMAGES = 200
MAX_SCROLLS = 40
MAX_REDIRECTS = 3
MAX_CAPTURE_IMAGES = 200
MAX_CAPTURE_BYTES = 128 * 1024 * 1024
_SELECTION_VERSION = 2
_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp",
               "BMP": ".bmp", "AVIF": ".avif"}
_HOSTS = ("docs.qq.com", "qpic.cn", "gtimg.com")
_IDENTITY_PARAMS = {"id", "fileid", "file_id", "imageid", "image_id", "md5", "sha256"}
_SELECTORS = {
    "aio": ("#sc-page-content", "#sc-page-edit-content", "#grid-root"),
    "doc": ("#workbench-content-container", "#grid-root"),
    "slide": ("#workbench-content-container",),
    "form": ("#workbench-content-container",),
    "pdf": ("#workbench-content-container",),
}
_SAFE_FILE = re.compile(r"^[0-9a-f]{64}\.(?:jpg|png|gif|webp|bmp|avif)$")


class BodyImagesCancelled(RuntimeError):
    """正文采集已取消；调用方不得把它降级成单页失败。"""


def _check_cancelled(is_cancelled=None) -> None:
    if is_cancelled and is_cancelled():
        raise BodyImagesCancelled("已取消正文采集")


def _emit(on_status, message: str) -> None:
    if on_status:
        try:
            on_status(message)
        except Exception:  # noqa: BLE001 - 状态回调不影响采集
            pass


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))


def _scoped_path(relative_path: str, root: Path, *, must_exist: bool) -> Path:
    relative = str(relative_path or "")
    parts = PurePosixPath(relative).parts
    if ("\\" in relative or ":" in relative or len(parts) != 4
            or parts[0] != "正文图片"
            or any(not re.fullmatch(r"[0-9a-f]{64}", part) for part in parts[1:3])
            or not _SAFE_FILE.fullmatch(parts[3])):
        raise ValueError("正文图片路径无效")
    root = Path(root).absolute()
    if _is_link(root):
        raise ValueError("正文图片目录不允许链接")
    target = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if _is_link(cursor):
            raise ValueError("正文图片目录不允许链接")
    try:
        target.resolve().relative_to(root.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("正文图片路径越界") from exc
    if must_exist:
        if not target.is_file():
            raise ValueError("正文图片文件不存在")
        size = target.stat().st_size
        if size <= 0 or size > MAX_BYTES:
            raise ValueError("正文图片文件大小无效")
    return target


def safe_image_path(relative_path, root=None) -> Path:
    """只允许受控目录内、无链接、有效后缀及大小的已存在图片。"""
    return _scoped_path(str(relative_path or ""), Path(root) if root is not None
                        else cfg_mod.data_dir(), must_exist=True)


def _inspect_image(content: bytes) -> tuple[str, int, int]:
    if not content or len(content) > MAX_BYTES:
        raise ValueError("图片为空或超过 20MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                ext = _EXTENSIONS.get(image.format or "")
                if not ext:
                    raise ValueError("不支持的图片格式")
                if (width <= 0 or height <= 0 or max(width, height) > MAX_SIDE
                        or width * height > MAX_PIXELS):
                    raise ValueError("图片尺寸过大")
                image.verify()
            # verify() 对 JPEG 等格式只核对结构，实际解码再排除截断图片。
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return ext, width, height
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片尺寸过大") from exc
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - 只返回不含来源地址的短错误
        raise ValueError("图片损坏或格式不支持") from exc


def _allowed_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (parsed.scheme == "https" and parsed.username is None
                and parsed.password is None and parsed.port in (None, 443)
                and any(host == base or host.endswith("." + base) for base in _HOSTS))
    except ValueError:
        return False


class ImageCapture:
    """复用此页面已经收到的图像响应，绝不重发浏览器请求或读取账户请求头。

    response 事件只登记受限的响应引用；DOM 明确需要该图时才读取响应体。
    缓存只在内存中按完整 URL 的哈希关联，且必须通过内容类型/实际解码检查。
    """

    def __init__(self, page):
        self._page = page
        self._pending = OrderedDict()
        self._bodies = OrderedDict()
        self._ready = set()
        self._bytes = 0
        self._closed = False
        self._handler = self._on_response
        self._finished_handler = self._on_finished
        page.on("response", self._handler)
        page.on("requestfinished", self._finished_handler)

    def _on_response(self, response) -> None:
        if self._closed:
            return
        try:
            if (response.status != 200 or response.request.resource_type != "image"
                    or not _allowed_url(response.url)):
                return
            headers = response.headers
            content_type = str(headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/") or content_type == "image/svg+xml":
                return
            length = int(headers.get("content-length") or 0)
            if length < 0 or length > MAX_BYTES:
                return
            key = _sha(str(response.url))
            self._pending[key] = response
            self._pending.move_to_end(key)
            while len(self._pending) > MAX_CAPTURE_IMAGES:
                old_key, _old_response = self._pending.popitem(last=False)
                self._ready.discard(old_key)
        except Exception:  # noqa: BLE001 - 不打印响应/地址/请求头
            return

    def _on_finished(self, request) -> None:
        if self._closed:
            return
        try:
            key = _sha(str(request.url))
            if key in self._pending:
                self._ready.add(key)
        except Exception:  # noqa: BLE001 - 请求已销毁时不影响采集
            return

    def get(self, url: str, is_cancelled=None) -> bytes | None:
        """仅返回当前 DOM 使用的同一来源的已加载原始图像字节。"""
        _check_cancelled(is_cancelled)
        if self._closed or not _allowed_url(url):
            return None
        key = _sha(url)
        cached = self._bodies.get(key)
        if cached is not None:
            self._bodies.move_to_end(key)
            return cached
        if key in self._pending and key not in self._ready:
            # response 事件发生在响应头到达时。仅在请求确已结束后读 body，
            # 避免未完成的长响应阻塞取消；短暂等待让浏览器处理在途的原图。
            deadline = time.monotonic() + 1.5
            while key not in self._ready and time.monotonic() < deadline:
                _check_cancelled(is_cancelled)
                try:
                    self._page.wait_for_timeout(100)
                except Exception:
                    return None
            if key not in self._ready:
                return None
        response = self._pending.pop(key, None)
        self._ready.discard(key)
        if response is None:
            return None
        try:
            content = response.body()
            _check_cancelled(is_cancelled)
            if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
                return None
            _inspect_image(content)
            if len(content) > MAX_CAPTURE_BYTES:
                return None
            while self._bodies and (self._bytes + len(content) > MAX_CAPTURE_BYTES
                                    or len(self._bodies) >= MAX_CAPTURE_IMAGES):
                _old_key, old_content = self._bodies.popitem(last=False)
                self._bytes -= len(old_content)
            self._bodies[key] = content
            self._bytes += len(content)
            return content
        except BodyImagesCancelled:
            raise
        except Exception:  # noqa: BLE001 - 拒绝坏图，不把 URL 或平台错误写进结果
            return None

    def close(self) -> None:
        """解除此实例安装的监听并释放字节，不关闭页面或浏览器。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._page.remove_listener("response", self._handler)
            self._page.remove_listener("requestfinished", self._finished_handler)
        except Exception:  # noqa: BLE001 - 页面已关闭时仍清空内存
            pass
        self._pending.clear()
        self._bodies.clear()
        self._ready.clear()
        self._bytes = 0
        self._page = None


def capture_page_responses(page) -> ImageCapture:
    """必须在 page.goto 前安装；同一页面跨子页复用，最终调用 close。"""
    return ImageCapture(page)


def _identity(source: dict) -> str:
    url = str(source.get("src") or "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return _sha("invalid:" + url)
    if parsed.scheme in ("http", "https"):
        query = sorted((key.lower(), value) for key, value in parse_qsl(parsed.query)
                       if key.lower() in _IDENTITY_PARAMS)
        return _sha((parsed.hostname or "").lower() + parsed.path + "?" + urlencode(query))
    if parsed.scheme == "data":
        return _sha(url)
    # blob URL 随浏览器会话改变，以正文块身份/DOM位置稳定关联旧的成功结果。
    return _sha("embedded:" + str(source.get("node") or ""))


def _http_image(url: str, is_cancelled=None) -> bytes:
    deadline = time.monotonic() + 30
    headers = {"Accept": "image/*", "User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    for redirect in range(MAX_REDIRECTS + 1):
        _check_cancelled(is_cancelled)
        if not _allowed_url(url):
            raise ValueError("非受支持的腾讯图床地址")
        try:
            # 每跳独立匿名请求：不复用浏览器 context，也不接受上一跳响应的票据。
            with httpx.stream("GET", url, headers=headers, timeout=8,
                              follow_redirects=False, trust_env=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirect == MAX_REDIRECTS:
                        raise ValueError("图片重定向次数过多")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("图片重定向地址缺失")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise ValueError(f"图片请求未成功（{response.status_code}）")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type and not (content_type.startswith("image/")
                                         or content_type == "application/octet-stream"):
                    raise ValueError("返回内容不是图片")
                try:
                    declared = int(response.headers.get("content-length", "0"))
                except ValueError:
                    declared = 0
                if declared > MAX_BYTES:
                    raise ValueError("图片超过 20MB")
                chunks, size = [], 0
                for chunk in response.iter_bytes(65536):
                    _check_cancelled(is_cancelled)
                    if time.monotonic() > deadline:
                        raise ValueError("图片下载超时")
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("图片超过 20MB")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise ValueError("图片下载超时") from exc
        except httpx.HTTPError as exc:
            raise ValueError("图片网络请求失败") from exc
    raise ValueError("图片重定向次数过多")


_EMBEDDED_JS = """async ({src, limit}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
        if (!(src.startsWith('blob:') || src.startsWith('data:image/')))
            return {error: 'source'};
        const response = await fetch(src, {credentials: 'omit', redirect: 'error', signal: controller.signal});
        if (!response.ok) return {error: 'response'};
        const reader = response.body.getReader();
        const chunks = []; let size = 0;
        while (true) {
            const next = await reader.read();
            if (next.done) break;
            size += next.value.length;
            if (size > limit) { await reader.cancel(); return {error: 'large'}; }
            chunks.push(next.value);
        }
        const blob = new Blob(chunks);
        return await new Promise(resolve => {
            const file = new FileReader();
            const timeout = setTimeout(() => { file.abort(); resolve({error: 'timeout'}); }, 5000);
            file.onload = () => { clearTimeout(timeout); resolve({data: String(file.result).split(',')[1] || ''}); };
            file.onerror = () => { clearTimeout(timeout); resolve({error: 'read'}); };
            file.readAsDataURL(blob);
        });
    } catch (_) { return {error: 'read'}; }
    finally { clearTimeout(timer); }
}"""


def _embedded_image(page, url: str, is_cancelled=None) -> bytes:
    _check_cancelled(is_cancelled)
    if len(url) > MAX_BYTES * 4 // 3 + 1024:
        raise ValueError("图片超过 20MB")
    result = page.evaluate(_EMBEDDED_JS, {"src": url, "limit": MAX_BYTES})
    _check_cancelled(is_cancelled)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("页面内图片读取失败")
    encoded = str(result.get("data") or "")
    if len(encoded) > MAX_BYTES * 4 // 3 + 8:
        raise ValueError("图片超过 20MB")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("页面内图片数据无效") from exc


_SOURCES_JS = r"""({selectors, max, position}) => {
    const root = selectors.map(s => document.querySelector(s)).find(Boolean);
    if (!root) return {items: [], missing: true};
    let scroller = root;
    while (scroller && scroller !== document.documentElement) {
        const style = getComputedStyle(scroller);
        if (scroller.scrollHeight > scroller.clientHeight + 8 && /(auto|scroll)/.test(style.overflowY)) break;
        scroller = scroller.parentElement;
    }
    scroller = scroller || document.scrollingElement || document.documentElement;
    if (position !== null) scroller.scrollTop = position;
    const items = [];
    const metadata = element => ['class', 'data-type', 'data-block-type', 'data-node-type',
        'data-tid', 'data-testid', 'role'].map(key => element.getAttribute(key) || '').join(' ');
    const imageBlock = element => /(?:^|[\s_-])(?:image|img|picture|photo)(?:[\s_-]|$)|image(?:block|container|wrapper|content)/i.test(metadata(element));
    const excluded = element => {
        for (let parent = element, depth = 0; parent && depth < 14; parent = parent.parentElement, depth++) {
            if (parent !== root && /^(BUTTON|NAV|ASIDE)$/.test(parent.tagName || '')) return true;
            const value = metadata(parent);
            if (/(?:^|[\s_-])(?:attachment|attach|file|icon|avatar|toolbar|sidebar|menu|tooltip|popover|placeholder|loading|spinner|skeleton|emoji|resize|handle|comment)(?:[\s_-]|$)|(?:file|attachment)(?:card|preview|icon|wrapper|block)|(?:toolbar|avatar|placeholder|skeleton)/i.test(value)) return true;
            if (parent.matches('a[download],[role="toolbar"],[role="menu"],[role="tooltip"]')) return true;
            if (parent === root) break;
        }
        return false;
    };
    const decorativeUrl = src => {
        if (/^data:image\/(?:svg|x-icon)/i.test(src)) return true;
        try {
            const path = new URL(src, location.href).pathname;
            return /(?:^|\/)(?:icons?|file[-_]?icons?|filetypes?|attachment[-_]?icons?|placeholders?|loading|spinner|emoji)(?:\/|[_.-])|\.(?:svg|ico)$/i.test(path)
                || /(?:^|[/_.-])(?:file[-_]?type|attach[-_]?file|default[-_]?file)(?:[/_.-]|$)/i.test(path);
        } catch (_) { return true; }
    };
    // 背景图只检查显式图像节点，不再扫描正文内所有元素的背景样式。
    const candidates = 'img,[data-block-type],[data-node-type],[data-type],[data-tid],[class*="image"],[class*="Image"],[class*="picture"],[class*="photo"]';
    const elements = Array.from(root.querySelectorAll(candidates)).slice(0, 12000);
    for (let index = 0; index < elements.length && items.length < max; index++) {
        const element = elements[index];
        if (excluded(element)) continue;
        const style = getComputedStyle(element);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const block = element.closest('[data-block-id]');
        const node = (block ? block.getAttribute('data-block-id') : '') + ':' + index;
        let explicit = imageBlock(element);
        for (let parent = element.parentElement, depth = 0; !explicit && parent && parent !== root && depth < 6;
             parent = parent.parentElement, depth++) explicit = imageBlock(parent);
        if (element.tagName === 'IMG') {
            const lazy = element.getAttribute('data-src') || element.getAttribute('data-original') || element.getAttribute('data-lazy-src') || '';
            let src = element.currentSrc || element.src || lazy;
            if (lazy && (!src || decorativeUrl(src) || (element.naturalWidth <= 2 && element.naturalHeight <= 2))) src = lazy;
            if (!src || decorativeUrl(src)) continue;
            const width = element.naturalWidth || rect.width, height = element.naturalHeight || rect.height;
            if (!lazy && width <= 2 && height <= 2) continue;
            if (!explicit && ((width <= 64 && height <= 64) || (rect.width <= 64 && rect.height <= 64))) continue;
            if (/\.(?:docx?|xlsx?|pptx?|pdf|zip|rar|7z)(?:\s|$)/i.test(element.alt || '')) continue;
            items.push({src: src.length <= 28000000 ? src : 'unsupported:large', node, classification: 'body-image-v2'});
            continue;
        }
        if (!imageBlock(element)) continue;
        const background = style.backgroundImage || '';
        const matcher = /url\(["']?([^"')]+)["']?\)/g;
        let match;
        while ((match = matcher.exec(background)) && items.length < max) {
            try {
                const src = new URL(match[1], location.href).href;
                if (!decorativeUrl(src)) items.push({src, node: node + ':bg', classification: 'body-image-v2'});
            } catch (_) {}
        }
    }
    return {items, top: scroller.scrollTop, height: scroller.clientHeight,
            end: scroller.scrollHeight, atEnd: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4};
}"""


def _page_sources(page, doc: dict, is_cancelled=None) -> list[dict]:
    selectors = list(_SELECTORS.get(str(doc.get("kind") or "").lower(), ()))
    if "#workbench-content-container" not in selectors:
        selectors.append("#workbench-content-container")
    sources, seen, original = [], set(), None
    complete, last_end = False, None
    deadline = time.monotonic() + 12
    try:
        position = None
        for step in range(MAX_SCROLLS):
            _check_cancelled(is_cancelled)
            if position is not None:
                page.evaluate(_SOURCES_JS, {"selectors": selectors, "max": 0, "position": position})
                page.wait_for_timeout(180)
                _check_cancelled(is_cancelled)
            result = page.evaluate(_SOURCES_JS, {"selectors": selectors,
                                               "max": MAX_IMAGES + 1, "position": None})
            if not isinstance(result, dict) or result.get("missing"):
                raise RuntimeError("正文图片容器未能读取")
            if original is None:
                original = result.get("top", 0)
                if original:
                    position = 0
                    continue
            for source in result.get("items") or []:
                if not isinstance(source, dict) or not source.get("src"):
                    continue
                identity = _identity(source)
                if identity not in seen:
                    sources.append(source)
                    seen.add(identity)
            if len(sources) > MAX_IMAGES:
                raise RuntimeError("正文图片未完整读取（超过单页图片上限）")
            current_end = (result.get("end"), len(sources))
            if result.get("atEnd") and current_end == last_end:
                complete = True
                break
            if time.monotonic() >= deadline:
                break
            last_end = current_end if result.get("atEnd") else None
            position = float(result.get("top") or 0) + max(200, float(result.get("height") or 600) * .8)
    finally:
        if original is not None:
            try:
                page.evaluate(_SOURCES_JS, {"selectors": selectors, "max": 0, "position": original})
            except Exception:  # noqa: BLE001 - 页面消失时不遮盖取消/原错误
                pass
    if not complete:
        raise RuntimeError("正文图片未完整读取（达到滚动限制）")
    return sources


def _index_path(folder: Path, root: Path) -> Path:
    # 检查目录时借用合法图片名，索引不向读取端暴露。
    relative = folder.relative_to(root).as_posix() + "/" + "0" * 64 + ".png"
    _scoped_path(relative, root, must_exist=False)
    target = folder / ".index.json"
    if _is_link(target):
        raise ValueError("正文图片索引不允许链接")
    return target


def _atomic_write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".body-", suffix=".part", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def collect_page_images(page, doc: dict, page_id, on_status=None, is_cancelled=None,
                        capture=None) -> list[dict]:
    """抓取一页正文图片；单图失败返回占位，取消直接传播，文字不被替换。"""
    _check_cancelled(is_cancelled)
    sources = _page_sources(page, doc, is_cancelled)
    root = Path(cfg_mod.data_dir()).absolute()
    folder = root / "正文图片" / _sha(str(doc.get("key") or doc.get("url") or "document")) / _sha(str(page_id))
    index_path = _index_path(folder, root)
    cache = {}
    if index_path.is_file() and index_path.stat().st_size <= 512 * 1024:
        try:
            saved = json.loads(index_path.read_text(encoding="utf-8"))
            cache = saved.get("images", {}) if saved.get("schema") == _SELECTION_VERSION else {}
            if not isinstance(cache, dict):
                cache = {}
        except (OSError, ValueError, AttributeError):
            cache = {}
    results, total_bytes = [], 0
    deadline = time.monotonic() + 120
    for number, source in enumerate(sources, 1):
        _check_cancelled(is_cancelled)
        key = _identity(source)
        item = {"key": key, "name": f"图片_{number:03d}", "path": "", "status": "failed",
                "error": "", "width": 0, "height": 0, "classification": "body-image-v2"}
        _emit(on_status, f"正在保存正文图片 {number}/{len(sources)}")
        try:
            previous = cache.get(key)
            if (isinstance(previous, dict) and previous.get("classification") == "body-image-v2"
                    and str(previous.get("path") or "").startswith(
                    folder.relative_to(root).as_posix() + "/")):
                try:
                    cached = safe_image_path(previous.get("path"), root)
                    content = cached.read_bytes()
                    ext, width, height = _inspect_image(content)
                    if cached.stem != _sha(content) or cached.suffix != ext:
                        raise ValueError("图片缓存校验失败")
                    item.update(path=cached.relative_to(root).as_posix(), status="downloaded",
                                width=width, height=height, name=item["name"] + ext)
                    results.append(item)
                    continue
                except (ValueError, OSError):
                    pass
            if time.monotonic() > deadline or total_bytes >= MAX_PAGE_BYTES:
                raise ValueError("已达到单页图片采集限额")
            url = str(source.get("src") or "")
            content = capture.get(url, is_cancelled) if capture is not None else None
            if content is None:
                content = (_embedded_image(page, url, is_cancelled) if url.startswith(("blob:", "data:"))
                           else _http_image(url, is_cancelled))
            _check_cancelled(is_cancelled)
            total_bytes += len(content)
            if total_bytes > MAX_PAGE_BYTES:
                raise ValueError("已达到单页图片采集限额")
            ext, width, height = _inspect_image(content)
            relative = (folder.relative_to(root) / (_sha(content) + ext)).as_posix()
            target = _scoped_path(relative, root, must_exist=False)
            if not target.exists():
                _atomic_write(target, content)
            else:
                # 内容寻址文件不可被静默覆盖；已存在时必须验证一致。
                if not target.is_file() or target.read_bytes() != content:
                    raise ValueError("图片缓存校验失败")
            item.update(path=relative, status="downloaded", width=width, height=height,
                        name=item["name"] + ext)
            cache[key] = {key: value for key, value in item.items() if key != "error"}
        except BodyImagesCancelled:
            raise
        except ValueError as exc:
            item["error"] = str(exc) if str(exc) in {
                "图片为空或超过 20MB", "不支持的图片格式", "图片尺寸过大", "图片损坏或格式不支持",
                "非受支持的腾讯图床地址", "图片重定向次数过多", "图片重定向地址缺失", "返回内容不是图片",
                "图片超过 20MB", "图片下载超时", "图片网络请求失败", "页面内图片读取失败",
                "页面内图片数据无效", "已达到单页图片采集限额", "图片缓存校验失败"} else "图片保存失败"
            if re.fullmatch(r"图片请求未成功（\d{3}）", str(exc)):
                item["error"] = str(exc)
        except Exception:  # noqa: BLE001 - 不转储 URL/浏览器响应或平台凭据
            item["error"] = "图片保存失败"
        results.append(item)
    _check_cancelled(is_cancelled)
    if sources:
        try:
            _atomic_write(index_path, json.dumps({"schema": _SELECTION_VERSION, "images": cache}, ensure_ascii=False).encode("utf-8"))
        except OSError:
            _emit(on_status, "正文图片已保存，缓存索引未能更新")
    return results
