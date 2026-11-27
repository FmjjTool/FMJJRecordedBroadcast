"""统一账号管理中的微信阅读会话：短时内存、只读使用、不写任何凭据文件。

桌面主动启动的 WebUI 通过带随机能力的本机只读通道取同一会话；
浏览器与独立启动的进程没有该通道。退出程序或到期即失效。
"""
from __future__ import annotations

import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION_TTL_SECONDS = 10 * 60
_ENDPOINT_ENV = "FMJJ_WECHAT_SESSION_ENDPOINT"
_CAPABILITY_ENV = "FMJJ_WECHAT_SESSION_CAPABILITY"
_PATH = "/wechat-reading-session"
_lock = threading.RLock()
_session = None
_broker = None
_broker_capability = ""
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class SessionInputError(ValueError):
    """输入错误仅含静态提示，不回显用户粘贴的内容。"""


@dataclass(frozen=True)
class SessionSnapshot:
    identity: str
    expires_at: float
    biz: str
    _key: str = field(repr=False)
    _uin: str = field(repr=False)
    _pass_ticket: str = field(default="", repr=False)
    _appmsg_token: str = field(default="", repr=False)
    _deadline: float = field(default=0.0, repr=False)

    def _request_parameters(self) -> dict:
        return {k: v for k, v in {
            "key": self._key, "uin": self._uin,
            "pass_ticket": self._pass_ticket,
            "appmsg_token": self._appmsg_token,
        }.items() if v}


def _enabled() -> bool:
    try:
        # 延迟导入；模块本身没有文件路径、存储或迁移行为。
        import auth
        return bool(auth.is_cookie_enabled())
    except Exception:
        return False


def _local_session() -> SessionSnapshot | None:
    global _session
    with _lock:
        if not _enabled() or (_session and time.monotonic() >= _session._deadline):
            _session = None
        return _session


def set_session_from_url(url: str) -> SessionSnapshot:
    """仅接收用户主动粘贴的文章请求地址；不抓取、不落盘、不探测登录。"""
    global _session
    if os.environ.get(_ENDPOINT_ENV):
        raise SessionInputError("请在桌面端统一账号入口设置阅读会话")
    if not _enabled():
        raise SessionInputError("账号使用已关闭，暂不能设置阅读会话")
    value = html.unescape(str(url or "").strip())
    if not value or len(value) > 32768 or any(ord(c) < 32 for c in value):
        raise SessionInputError("请输入完整的微信文章请求地址")
    try:
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme != "https" or parts.hostname != "mp.weixin.qq.com"
                or parts.username or parts.password or parts.port not in (None, 443)
                or not re.fullmatch(r"/s(?:/[A-Za-z0-9_-]+)?", parts.path)):
            raise ValueError
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True, max_num_fields=80)
    except (ValueError, TypeError):
        raise SessionInputError("只接受微信文章的安全请求地址") from None
    params = {}
    for key, val in pairs:
        if key in ("__biz", "key", "uin", "pass_ticket", "appmsg_token"):
            if key in params or not val or len(val) > 8192 or any(ord(c) < 33 for c in val):
                raise SessionInputError("阅读信息不完整或格式不正确")
            params[key] = val
    biz = params.get("__biz", "")
    if (not re.fullmatch(r"[A-Za-z0-9_+/=-]{4,256}", biz)
            or not params.get("key") or not params.get("uin")):
        raise SessionInputError("地址中缺少有效阅读信息；普通分享链接不能用于历史列表")
    now = time.time()
    snapshot = SessionSnapshot(
        identity=secrets.token_hex(16), expires_at=now + SESSION_TTL_SECONDS, biz=biz,
        _key=params["key"], _uin=params["uin"],
        _pass_ticket=params.get("pass_ticket", ""),
        _appmsg_token=params.get("appmsg_token", ""),
        _deadline=time.monotonic() + SESSION_TTL_SECONDS,
    )
    with _lock:
        _session = snapshot
    return snapshot


def clear_session() -> None:
    """只丢弃本进程临时内存；没有长期凭据清除路径。"""
    global _session
    with _lock:
        _session = None


def _remote_session() -> SessionSnapshot | None:
    endpoint = os.environ.get(_ENDPOINT_ENV, "")
    capability = os.environ.get(_CAPABILITY_ENV, "")
    try:
        parts = urllib.parse.urlsplit(endpoint)
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1"
                or not parts.port or parts.username or parts.password
                or parts.path != _PATH or parts.query or parts.fragment
                or not re.fullmatch(r"[0-9a-f]{64}", capability)):
            return None
        request = urllib.request.Request(endpoint, headers={
            "Authorization": "Bearer " + capability, "Accept": "application/json",
        })
        with _opener.open(request, timeout=3) as response:
            # 本机 broker 也不允许重定向到其他目标。
            if response.geturl() != endpoint:
                return None
            raw = response.read(32769)
        if len(raw) > 32768:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("identity"):
            return None
        remaining = min(SESSION_TTL_SECONDS, float(data["expires_at"]) - time.time())
        if remaining <= 0 or not re.fullmatch(r"[0-9a-f]{32}", data["identity"]):
            return None
        return SessionSnapshot(
            identity=data["identity"], expires_at=float(data["expires_at"]),
            biz=str(data["biz"]), _key=str(data["key"]), _uin=str(data["uin"]),
            _pass_ticket=str(data.get("pass_ticket", "")),
            _appmsg_token=str(data.get("appmsg_token", "")),
            _deadline=time.monotonic() + remaining)
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        return None


def get_session() -> SessionSnapshot | None:
    if os.environ.get(_ENDPOINT_ENV):
        return _remote_session()
    return _local_session()


def is_current(snapshot: SessionSnapshot | None) -> bool:
    current = get_session()
    return bool(snapshot and current and current.identity == snapshot.identity)


def session_status() -> dict:
    snapshot = get_session()
    return {
        "ready": snapshot is not None,
        "expires_at": snapshot.expires_at if snapshot else None,
        "remaining_seconds": max(0, int(snapshot.expires_at - time.time())) if snapshot else 0,
    }


class _BrokerServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


class _BrokerHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        supplied = self.headers.get("Authorization", "")
        if (self.path != _PATH or self.client_address[0] != "127.0.0.1"
                or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin")
                or not hmac.compare_digest(supplied, "Bearer " + _broker_capability)):
            self.send_error(404)
            return
        snapshot = _local_session()
        payload = {}
        if snapshot:
            payload = {
                "identity": snapshot.identity, "expires_at": snapshot.expires_at,
                "biz": snapshot.biz, **snapshot._request_parameters(),
            }
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def webui_environment() -> dict[str, str]:
    """仅传给桌面主动创建的 WebUI 子进程，不传给浏览器或命令行。"""
    global _broker, _broker_capability
    if os.environ.get(_ENDPOINT_ENV):
        return {}
    with _lock:
        if _broker is None:
            _broker_capability = secrets.token_hex(32)
            _broker = _BrokerServer(("127.0.0.1", 0), _BrokerHandler)
            threading.Thread(target=_broker.serve_forever, daemon=True,
                             name="wechat-session-reader").start()
        return {
            _ENDPOINT_ENV: f"http://127.0.0.1:{_broker.server_port}{_PATH}",
            _CAPABILITY_ENV: _broker_capability,
        }
