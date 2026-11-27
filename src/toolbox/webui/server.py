"""Qt-free WebUI 服务端：路由、令牌与短期会话、静态页与任务下载。

只绑定指定地址（默认 127.0.0.1）；/api/* 需要 X-WebUI-Token（长期令牌或
短期会话票据）；POST 额外校验 Origin 与 Host 一致。`/api/auth/login` 用
长期令牌换取会话票据（默认 12 小时，内存存储，重启失效），避免长期令牌
长期留在浏览器与链接中。业务接口全部调用 L3 服务，不接触 Qt。
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import logging
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from toolbox.webui.errors import ApiError

_log = logging.getLogger(__name__)

SESSION_TTL = 12 * 3600
_MAX_SESSIONS = 64

_MAX_BODY = 2 * 1024 * 1024
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/tcdoc_versions.js": ("tcdoc_versions.js", "text/javascript; charset=utf-8"),
    "/performance_reports.js": ("performance_reports.js", "text/javascript; charset=utf-8"),
    "/cover_automation.js": ("cover_automation.js", "text/javascript; charset=utf-8"),
    "/local_llm.js": ("local_llm.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    json: dict = field(default_factory=dict)

    def q(self, name: str, default: str = "", *, strip: bool = True) -> str:
        value = (self.query.get(name) or [default])[0]
        text = str(value)
        return text.strip() if strip else text

    def q_int(self, name: str, default: int, low: int | None = None,
              high: int | None = None) -> int:
        try:
            value = int(self.q(name, str(default)) or default)
        except (TypeError, ValueError):
            value = default
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return value

    def body(self, name: str, default=None):
        return (self.json or {}).get(name, default)


@dataclass
class FileResponse:
    path: Path
    filename: str
    content_type: str = "application/octet-stream"


@dataclass
class BytesResponse:
    """内联二进制响应（预览图 / 视频首帧等），不作为附件下载。"""
    data: bytes
    content_type: str = "image/jpeg"


@dataclass
class AttachmentResponse:
    """内存中生成的小文件，按附件下载（片段 CSV / EDL / 章节等）。"""
    data: bytes
    filename: str
    content_type: str = "application/octet-stream"


@dataclass
class RangeResponse:
    """本地媒体文件流，支持 HTTP Range（视频预览拖动进度）。"""
    path: Path
    content_type: str = "video/mp4"


class WebUIServer:
    def __init__(self, token: str, host: str = "127.0.0.1", port: int = 0,
                 on_stop=None):
        self._token = str(token or "")
        self._host = host or "127.0.0.1"
        self._port = int(port or 0)
        self._on_stop = on_stop
        self._static_dir = Path(__file__).resolve().parent / "static"
        self._routes: dict[tuple[str, str], object] = {}
        self._dynamic: list[tuple[str, re.Pattern, object]] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, float] = {}
        self._sessions_lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._port

    @property
    def host(self) -> str:
        return self._host

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self._host in ("0.0.0.0", "::") else self._host
        return f"http://{host}:{self._port}/?token={quote(self._token)}"

    def get(self, path: str, fn) -> None:
        self._routes[("GET", path)] = fn

    def post(self, path: str, fn) -> None:
        self._routes[("POST", path)] = fn

    def get_re(self, pattern: str, fn) -> None:
        self._dynamic.append(("GET", re.compile(pattern), fn))

    def post_re(self, pattern: str, fn) -> None:
        self._dynamic.append(("POST", re.compile(pattern), fn))

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler = _make_handler(self)
        httpd = _ThreadingServer((self._host, self._port), handler)
        self._httpd = httpd
        self._port = int(httpd.server_address[1])
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="webui-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if self._on_stop is not None:
            try:
                self._on_stop()
            except Exception:
                pass
            self._on_stop = None

    def _auth(self, req: Request) -> bool:
        supplied = req.headers.get("x-webui-token", "")
        if not supplied:
            supplied = req.q("token")
        if not supplied or not self._token:
            return False
        if hmac.compare_digest(supplied, self._token):
            return True
        return self._session_valid(supplied)

    def _master_token_ok(self, supplied: str) -> bool:
        return bool(supplied) and bool(self._token) and hmac.compare_digest(
            supplied, self._token)

    def _session_valid(self, ticket: str) -> bool:
        now = time.time()
        with self._sessions_lock:
            expiry = self._sessions.get(ticket)
            if expiry is None:
                return False
            if expiry <= now:
                self._sessions.pop(ticket, None)
                return False
            return True

    def issue_session(self, ttl: int = SESSION_TTL) -> dict:
        """签发短期会话票据（内存存储，服务重启即失效）。"""
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        with self._sessions_lock:
            for key, expiry in list(self._sessions.items()):
                if expiry <= now:
                    self._sessions.pop(key, None)
            while len(self._sessions) >= _MAX_SESSIONS:
                oldest = min(self._sessions, key=self._sessions.get)
                self._sessions.pop(oldest, None)
            self._sessions[ticket] = now + ttl
        return {"ticket": ticket, "expires_in": int(ttl)}

    def revoke_session(self, ticket: str) -> bool:
        with self._sessions_lock:
            return self._sessions.pop(ticket, None) is not None

    def _origin_ok(self, req: Request) -> bool:
        origin = req.headers.get("origin", "")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return bool(parsed.netloc) and parsed.netloc == req.headers.get("host", "")

    def _read_static(self, name: str) -> bytes | None:
        base = self._static_dir.resolve()
        target = (base / name).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            return None
        try:
            return target.read_bytes()
        except OSError:
            return None

    def _dispatch(self, req: Request) -> object:
        if req.path == "/api/health":
            return {"ok": True}
        if req.path == "/api/auth/login" and req.method == "POST":
            if not self._origin_ok(req):
                return _error(403, "origin rejected")
            supplied = req.headers.get("x-webui-token", "") or \
                str((req.json or {}).get("token") or "")
            if not self._master_token_ok(supplied):
                return _error(403, "forbidden")
            return self.issue_session()
        if req.path == "/api/auth/logout" and req.method == "POST":
            if not self._origin_ok(req):
                return _error(403, "origin rejected")
            supplied = req.headers.get("x-webui-token", "") or \
                str((req.json or {}).get("ticket") or "")
            if not self._auth(req):
                return _error(403, "forbidden")
            self.revoke_session(supplied)
            return {"ok": True}
        if not self._auth(req):
            return _error(403, "forbidden")
        if req.method == "POST" and not self._origin_ok(req):
            return _error(403, "origin rejected")
        handler = self._routes.get((req.method, req.path))
        if handler is not None:
            return handler(req)
        for method, pattern, fn in self._dynamic:
            if method != req.method:
                continue
            match = pattern.fullmatch(req.path)
            if match:
                return fn(req, **match.groupdict())
        return _error(404, "not found")


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _error(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": message}


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _make_handler(app: WebUIServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FMJJWebUI/2.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _headers(self, status: int, content_type: str, length: int,
                     extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _send(self, result) -> None:
            if isinstance(result, FileResponse):
                self._send_file(result)
                return
            if isinstance(result, AttachmentResponse):
                self._send_attachment(result)
                return
            if isinstance(result, RangeResponse):
                self._send_range(result)
                return
            if isinstance(result, BytesResponse):
                self._headers(200, result.content_type, len(result.data))
                self.wfile.write(result.data)
                return
            if isinstance(result, tuple) and len(result) == 2:
                status, payload = result
            else:
                status, payload = 200, result
            body = _json_bytes(payload)
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _send_range(self, response: RangeResponse) -> None:
            try:
                size = response.path.stat().st_size
                handle = response.path.open("rb")
            except OSError:
                self._send(_error(404, "file not found"))
                return
            start, end = 0, max(0, size - 1)
            ranged = False
            match = _RANGE_RE.fullmatch((self.headers.get("Range") or "").strip())
            if match and size > 0:
                first, last = match.group(1), match.group(2)
                if first:
                    start = int(first)
                    if last:
                        end = min(int(last), size - 1)
                elif last:
                    length = min(int(last), size)
                    start, end = size - length, size - 1
                if start >= size or start > end:
                    handle.close()
                    self._headers(416, "text/plain; charset=utf-8", 0,
                                  {"Content-Range": f"bytes */{size}"})
                    return
                ranged = True
            length = end - start + 1 if size else 0
            extra = {"Accept-Ranges": "bytes"}
            if ranged:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._headers(206 if ranged else 200, response.content_type,
                          length, extra)
            try:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(1024 * 128, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            finally:
                handle.close()

        def _send_attachment(self, response: AttachmentResponse) -> None:
            data = response.data or b""
            ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_",
                                response.filename) or "download"
            utf8_name = quote(response.filename, safe="")
            self._headers(
                200, response.content_type, len(data),
                {"Content-Disposition":
                 f'attachment; filename="{ascii_name}"; '
                 f"filename*=UTF-8''{utf8_name}"})
            self.wfile.write(data)

        def _send_file(self, response: FileResponse) -> None:
            try:
                size = response.path.stat().st_size
                handle = response.path.open("rb")
            except OSError:
                self._send(_error(404, "file not found"))
                return
            with handle:
                ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_",
                                    response.filename) or "download"
                utf8_name = quote(response.filename, safe="")
                self._headers(
                    200, response.content_type, size,
                    {"Content-Disposition":
                     f'attachment; filename="{ascii_name}"; '
                     f"filename*=UTF-8''{utf8_name}"})
                while True:
                    chunk = handle.read(1024 * 64)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _serve_static(self, path: str) -> None:
            name, mime = _STATIC[path]
            data = app._read_static(name)
            if data is None:
                self._send(_error(404, "not found"))
                return
            etag = '"' + hashlib.sha1(data).hexdigest()[:20] + '"'
            if (self.headers.get("If-None-Match") or "").strip() == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            body = data
            headers = {
                "Content-Security-Policy": _CSP,
                "ETag": etag,
                "Cache-Control": "no-cache",
            }
            accept = (self.headers.get("Accept-Encoding") or "").lower()
            if len(data) > 1024 and "gzip" in accept:
                body = gzip.compress(data, 6)
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
            self._headers(200, mime, len(body), headers)
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path in _STATIC:
                self._serve_static(parsed.path)
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self._handle("GET", parsed)

        def do_POST(self):
            self._handle("POST", urlsplit(self.path))

        def _handle(self, method: str, parsed) -> None:
            query = parse_qs(parsed.query)
            headers = {key.lower(): value for key, value in self.headers.items()}
            body = {}
            if method == "POST":
                try:
                    length = int(headers.get("content-length", "0") or 0)
                except ValueError:
                    length = 0
                if length > _MAX_BODY:
                    self._send(_error(413, "body too large"))
                    return
                raw = self.rfile.read(length) if length else b""
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        self._send(_error(400, "invalid json"))
                        return
                    if not isinstance(body, dict):
                        self._send(_error(400, "invalid body"))
                        return
            req = Request(method=method, path=parsed.path, query=query,
                          headers=headers, json=body)
            try:
                result = app._dispatch(req)
            except ApiError as exc:
                result = _error(exc.status, str(exc))
            except ValueError as exc:
                result = _error(400, str(exc))
            except Exception:
                _log.exception("WebUI 接口失败：%s %s", method, parsed.path)
                result = _error(500, "服务内部错误，请查看服务端日志")
            try:
                self._send(result)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self.close_connection = True

    return Handler
