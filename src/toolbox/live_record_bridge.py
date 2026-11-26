"""桌面录制控制桥（L3）：本机 HTTP 通道，供 WebUI 子进程远控桌面录制。

只监听 127.0.0.1，随机端口 + 随机令牌；桥信息写入
`config/webui_desktop_bridge.json`（pid / port / token），WebUI 读取后调用。
控制器由桌面侧注入（Qt 侧负责切主线程），本模块不 import PySide6。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from toolbox.config_manager import get_app_root

_log = logging.getLogger(__name__)

BRIDGE_FILE = "webui_desktop_bridge.json"
_ACTIONS = {"start_record", "stop_record", "start_monitor", "stop_monitor",
            "add_anchor", "import_anchors"}


class DesktopBridge:
    def __init__(self, controller):
        self._controller = controller
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._token = ""

    @property
    def file_path(self) -> Path:
        return get_app_root() / "logs" / BRIDGE_FILE

    def start(self) -> dict:
        if self._httpd is not None:
            return self._info()
        self._token = secrets.token_urlsafe(24)
        handler = _make_handler(self)
        httpd = _ThreadingServer(("127.0.0.1", 0), handler)
        self._httpd = httpd
        port = int(httpd.server_address[1])
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="desktop-record-bridge",
            daemon=True)
        self._thread.start()
        self._write_file(port)
        _log.info("录制控制桥已启动：127.0.0.1:%s", port)
        return self._info()

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
        try:
            self.file_path.unlink()
        except OSError:
            pass
        self._token = ""

    def status(self) -> dict:
        return self._controller.status()

    def action(self, platform: str, room: str, action: str,
               extra: dict | None = None) -> dict:
        if action not in _ACTIONS:
            return {"ok": False, "message": "不支持的操作"}
        return self._controller.action(platform, room, action, extra or {})

    def _info(self) -> dict:
        httpd = self._httpd
        port = int(httpd.server_address[1]) if httpd is not None else 0
        return {"running": bool(httpd), "port": port, "pid": os.getpid()}

    def _write_file(self, port: int) -> None:
        payload = {"port": int(port), "token": self._token,
                   "pid": os.getpid()}
        path = self.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            _log.warning("写入录制控制桥信息失败：%s", exc)


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_handler(bridge: DesktopBridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FMJJRecordBridge/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> bool:
            supplied = self.headers.get("x-bridge-token", "")
            return bool(supplied) and bool(bridge._token) and \
                secrets.compare_digest(supplied, bridge._token)

        def do_GET(self):
            if self.path != "/status":
                self._send(404, {"error": "not found"})
                return
            if not self._auth():
                self._send(403, {"error": "forbidden"})
                return
            try:
                self._send(200, bridge.status())
            except Exception as exc:
                self._send(500, {"error": str(exc)[:300]})

        def do_POST(self):
            if self.path != "/action":
                self._send(404, {"error": "not found"})
                return
            if not self._auth():
                self._send(403, {"error": "forbidden"})
                return
            try:
                length = int(self.headers.get("content-length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"error": "invalid json"})
                return
            platform = str(payload.get("platform") or "")
            room = str(payload.get("room") or "")
            action = str(payload.get("action") or "")
            extra = payload.get("extra")
            if not isinstance(extra, dict):
                extra = {}
            if not platform or (not room and action != "import_anchors"):
                self._send(400, {"error": "缺少平台或房间号"})
                return
            try:
                self._send(200, bridge.action(platform, room, action, extra))
            except Exception as exc:
                self._send(500, {"error": str(exc)[:300]})

    return Handler