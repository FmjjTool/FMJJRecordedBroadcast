"""本地 WebUI 服务：静态页 + 快照（SSE）+ 动作提交。

只绑定 127.0.0.1；API 与事件流要求启动时生成的访问令牌。服务线程不访问
Qt 控件，全部快照与动作经 WebUiBridge 排队到 GUI 主线程。
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from gui_qt.page_index import nav_groups

_log = logging.getLogger(__name__)

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

_MAX_BODY = 256 * 1024
_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")


class WebUiServer:
    def __init__(self, bridge, port: int = 0, token: str = ""):
        self._bridge = bridge
        self._port = int(port or 0)
        self._token = token or secrets.token_urlsafe(18)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._client_dir = Path(__file__).resolve().parent / "client"

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/?token={self._token}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler = _make_handler(self)
        httpd = _ThreadingServer(("127.0.0.1", self._port), handler)
        self._httpd = httpd
        self._port = int(httpd.server_address[1])
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="webui-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # ---------- 供 handler 使用 ----------

    def _page_groups(self) -> list[dict]:
        groups = [{"g": "总览", "items": [{"k": "home", "t": "工作台"}]}]
        for group, items in nav_groups():
            if not items:
                continue
            groups.append({"g": group, "items": [
                {"k": key, "t": title} for key, title in items]})
        return groups

    def _read_static(self, name: str) -> bytes | None:
        target = (self._client_dir / name).resolve()
        try:
            target.relative_to(self._client_dir.resolve())
        except ValueError:
            return None
        try:
            return target.read_bytes()
        except OSError:
            return None


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _make_handler(app: WebUiServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WebUIMirror/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _auth(self, query: dict[str, list[str]]) -> bool:
            supplied = self.headers.get("X-WebUI-Token", "")
            if not supplied:
                supplied = (query.get("token") or [""])[0]
            return bool(supplied) and hmac.compare_digest(supplied, app.token)

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return False
            host = (parsed.hostname or "").lower()
            return host in ("127.0.0.1", "localhost") and parsed.port == app.port

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path: str) -> None:
            name, mime = _STATIC[path]
            data = app._read_static(name)
            if data is None:
                self._json({"error": "not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", _CSP)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path in _STATIC:
                self._static(path)
                return
            if path == "/api/pages":
                if not self._auth(query):
                    self._json({"error": "forbidden"}, 403)
                    return
                self._json({"groups": app._page_groups()})
                return
            if path == "/api/state":
                if not self._auth(query):
                    self._json({"error": "forbidden"}, 403)
                    return
                app._bridge.request_refresh()
                result = app._bridge.wait_snapshot(0, 3.0)
                if result is None:
                    self._json({"error": "页面尚未就绪"}, 503)
                    return
                _version, text = result
                self._send_json_text(text)
                return
            if path == "/api/events":
                if not self._auth(query):
                    self._json({"error": "forbidden"}, 403)
                    return
                self._events(query)
                return
            self._json({"error": "not found"}, 404)

        def _send_json_text(self, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _events(self, query: dict[str, list[str]]) -> None:
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            app._bridge.subscribe()
            try:
                while True:
                    result = app._bridge.wait_snapshot(since, 20.0)
                    if result is None:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    since, text = result
                    self.wfile.write(
                        b"event: state\ndata: " + text.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError,
                    ConnectionResetError, OSError):
                pass
            finally:
                app._bridge.unsubscribe()
                self.close_connection = True

        def do_POST(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path != "/api/action":
                self._json({"error": "not found"}, 404)
                return
            if not self._auth(query) or not self._origin_ok():
                self._json({"error": "forbidden"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_BODY:
                self._json({"error": "bad request"}, 400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._json({"error": "bad request"}, 400)
                return
            if not isinstance(payload, dict) or not str(payload.get("a") or ""):
                self._json({"error": "bad request"}, 400)
                return
            app._bridge.post_action(payload)
            self._json({"ok": True})

    return Handler
