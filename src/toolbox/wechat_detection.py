"""微信阅读自动检测用例。仅统一账号入口调用；不拥有第二套会话存储。

工作进程管理临时网络设置及还原，主进程只在确认还原成功后调用既有
wechat_session 内存接口。状态 DTO 永远不含请求地址、异常或阅读参数。
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

_WORKER_FLAG = "--wechat-reading-detect-worker"
_MAX_LINE = 49152
_DIAGNOSTIC_FIELDS = (
    "proxy_connections", "connect_requests", "wechat_connects",
    "upstream_tls", "client_tls", "http_requests",
    "valid_candidates",
)
_MAX_DIAGNOSTIC_COUNT = 1_000_000
_MESSAGES = {
    "idle": "尚未开始检测",
    "starting": "正在准备检测，请稍候",
    "detecting": "请在电脑微信中新打开一篇目标公众号文章",
    "restoring": "正在还原临时网络设置，请勿强制退出",
    "succeeded": "已自动识别，可返回公众号页面继续操作",
    "cancelled": "已停止检测并还原临时网络设置",
    "expired": "检测已超时；请重新开始后在电脑微信打开文章",
    "error": "检测未完成，请稍后重试",
}
_ERROR_MESSAGES = {
    "busy": "已有自动检测正在运行，请先结束该检测",
    "existing_proxy": "当前已启用系统代理或自动代理，请暂时关闭后重试",
    "certificate_install_failed": "未能信任临时检测证书，检测未开始",
    "certificate_prepare_failed": "无法准备临时检测证书，请稍后重试",
    "proxy_read_failed": "无法读取系统网络设置，检测未开始",
    "proxy_write_failed": "无法应用临时网络设置，检测未完成",
    "unsupported": "自动检测目前仅支持 Windows 桌面端",
}


def _enabled() -> bool:
    # is_cookie_enabled 仅只读查询；不调用保存、刷新、迁移或登录流程。
    try:
        import auth
        return bool(auth.is_cookie_enabled())
    except Exception:
        return False


def _install(url: str):
    from wechat_session import set_session_from_url
    return set_session_from_url(url)


def _spawn_worker():
    root = Path(__file__).resolve().parents[2]
    argv = ([sys.executable, _WORKER_FLAG] if getattr(sys, "frozen", False)
            else [sys.executable, "-B", str(root / "main.py"), _WORKER_FLAG])
    environment = os.environ.copy()
    for key in ("FMJJ_WECHAT_SESSION_ENDPOINT", "FMJJ_WECHAT_SESSION_CAPABILITY"):
        environment.pop(key, None)
    return subprocess.Popen(
        argv, cwd=str(root), env=environment, shell=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        bufsize=0,
    )


class WechatDetectionController:
    """单次检测状态机；依赖注入仅用于隔离的虚构系统/进程验证。"""

    def __init__(self, *, process_factory=None, enabled=None, installer=None,
                 clock=None, platform=None, timeout=120):
        self._factory = process_factory or _spawn_worker
        self._enabled = enabled or _enabled
        self._installer = installer or _install
        self._clock = clock or time.monotonic
        self._platform = os.name if platform is None else platform
        self._timeout = max(5, min(120, int(timeout)))
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stop_request = threading.Event()
        self._state = "idle"
        self._message = _MESSAGES["idle"]
        self._busy = False
        self._cleanup_ok = True
        self._cleanup_failed = False
        self._recovery_required = False
        self._deadline = 0.0
        self._cancelled = False
        self._expired = False
        self._process = None
        self._diagnostics = dict.fromkeys(_DIAGNOSTIC_FIELDS, 0)

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state, "message": self._message,
                "busy": self._busy, "cleanup_ok": self._cleanup_ok,
                "cleanup_failed": self._cleanup_failed,
                "recovery_required": self._recovery_required,
                "diagnostics": dict(self._diagnostics),
                "remaining_seconds": (max(0, int(self._deadline - self._clock()))
                                      if self._busy and self._state in
                                      ("starting", "detecting") else 0),
            }

    def _set(self, state, *, cleanup_ok=None, cleanup_failed=False, busy=None,
             recovery_required=False, message=None):
        # Caller holds _lock. No input supplied by a child becomes visible text.
        self._state = state
        self._message = message or _MESSAGES[state]
        if cleanup_ok is not None:
            self._cleanup_ok = cleanup_ok
        self._cleanup_failed = cleanup_failed
        self._recovery_required = recovery_required
        if busy is not None:
            self._busy = busy

    def start(self, *, consent=False) -> dict:
        with self._lock:
            if self._busy or not self._cleanup_ok:
                return self.status()
            if consent is not True:
                self._set("error", message="请先确认临时网络设置说明")
                return self.status()
            if self._platform != "nt":
                self._set("error", message="自动检测目前仅支持 Windows 桌面端")
                return self.status()
            if os.environ.get("FMJJ_WECHAT_SESSION_ENDPOINT"):
                self._set("error", message="请在桌面端统一账号入口开始检测")
                return self.status()
            if not self._enabled():
                self._set("error", message="账号使用已关闭，暂不能开始检测")
                return self.status()
            self._cancelled = False
            self._expired = False
            self._stop_request.clear()
            self._diagnostics = dict.fromkeys(_DIAGNOSTIC_FIELDS, 0)
            self._deadline = self._clock() + self._timeout
            self._set("starting", busy=True, cleanup_ok=True)
            try:
                threading.Thread(target=self._run, daemon=True,
                                 name="wechat-reading-detection").start()
            except Exception:
                self._set("error", busy=False, cleanup_ok=True)
            return self.status()

    def _send(self, process, payload) -> bool:
        try:
            line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("ascii")
            with self._write_lock:
                process.stdin.write(line)
                process.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def stop(self) -> dict:
        with self._lock:
            if not self._busy:
                return self.status()
            self._cancelled = True
            self._stop_request.set()
            self._set("restoring", cleanup_failed=self._cleanup_failed)
        return self.status()

    @staticmethod
    def _read(process, events):
        try:
            while True:
                line = process.stdout.readline(_MAX_LINE + 1)
                if not line:
                    break
                if len(line) > _MAX_LINE or not line.endswith(b"\n"):
                    events.put(("invalid", None))
                    # Keep draining until restoration is acknowledged, without retaining
                    # an oversized record or treating an unread child as safely stopped.
                    while line and not line.endswith(b"\n"):
                        line = process.stdout.readline(_MAX_LINE + 1)
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError
                except (ValueError, UnicodeError):
                    events.put(("invalid", None))
                    continue
                events.put(("record", value))
        except (OSError, ValueError):
            pass
        finally:
            events.put(("eof", None))

    def _run(self):
        process = None
        terminal = None
        malformed = False
        sent_stop = False
        try:
            process = self._factory()
            events = queue.Queue(maxsize=16)
            reader = threading.Thread(target=self._read, args=(process, events),
                                      daemon=True, name="wechat-detection-reader")
            with self._lock:
                self._process = process
                self._cleanup_ok = False  # Launch occurred; only child can attest restoration.
            reader.start()
            if not self._send(process, {"command": "start", "timeout": self._timeout}):
                malformed = True
            while True:
                with self._lock:
                    if (self._clock() >= self._deadline and terminal is None
                            and self._state in ("starting", "detecting")):
                        self._expired = True
                    if not self._enabled():
                        self._cancelled = True
                    must_stop = self._cancelled or self._expired or malformed
                    if must_stop:
                        self._set("restoring", cleanup_failed=self._cleanup_failed)
                if self._stop_request.is_set() or (must_stop and not sent_stop):
                    self._stop_request.clear()
                    self._send(process, {"command": "stop"})
                    sent_stop = True
                try:
                    kind, data = events.get(timeout=0.15)
                except queue.Empty:
                    continue
                if kind == "eof":
                    break
                if kind == "invalid":
                    malformed = True
                    continue
                state = data.get("state")
                if terminal is not None:
                    malformed = True
                    data.clear()
                    continue
                if "diagnostics" in data:
                    raw_diagnostics = data.get("diagnostics")
                    if (not isinstance(raw_diagnostics, dict)
                            or set(raw_diagnostics) != set(_DIAGNOSTIC_FIELDS)
                            or any(type(value) is not int
                                   or not 0 <= value <= _MAX_DIAGNOSTIC_COUNT
                                   for value in raw_diagnostics.values())):
                        malformed = True
                        continue
                    with self._lock:
                        if any(raw_diagnostics[name] < self._diagnostics[name]
                               for name in _DIAGNOSTIC_FIELDS):
                            malformed = True
                            continue
                        self._diagnostics = dict(raw_diagnostics)
                if not isinstance(state, str) or state not in _MESSAGES or state == "idle":
                    malformed = True
                    continue
                if state in ("succeeded", "cancelled", "expired", "error"):
                    if data.get("cleanup_ok") is not True:
                        malformed = True
                        continue
                    terminal = data
                    # After a terminal event only EOF is valid; don't consume a later candidate.
                    continue
                with self._lock:
                    if state == "restoring":
                        self._set("restoring", cleanup_ok=False,
                                  cleanup_failed=data.get("cleanup_failed") is True)
                    elif not must_stop:
                        self._set(state, cleanup_ok=False)
            with self._lock:
                cleaned = bool(terminal and terminal.get("cleanup_ok") is True)
                if not cleaned:
                    self._set("error", busy=False, cleanup_ok=False, cleanup_failed=True,
                              recovery_required=True,
                              message="检测进程意外结束，无法确认还原；请检查系统网络设置")
                elif malformed:
                    self._set("error", busy=False, cleanup_ok=True)
                elif self._cancelled or not self._enabled():
                    self._set("cancelled", busy=False, cleanup_ok=True)
                elif self._expired:
                    self._set("expired", busy=False, cleanup_ok=True)
                elif terminal["state"] == "succeeded":
                    candidate = terminal.pop("url", "")
                    try:
                        # No UI receives candidate; the one existing owner validates it again.
                        self._installer(candidate)
                    except Exception:
                        self._set("error", busy=False, cleanup_ok=True)
                    else:
                        self._set("succeeded", busy=False, cleanup_ok=True)
                    finally:
                        candidate = ""
                else:
                    code = terminal.get("code")
                    message = (_ERROR_MESSAGES.get(code) if isinstance(code, str)
                               and terminal["state"] == "error" else None)
                    self._set(terminal["state"], busy=False, cleanup_ok=True, message=message)
        except Exception:
            with self._lock:
                if process is None:
                    self._set("error", busy=False, cleanup_ok=True)
                else:
                    self._set("error", busy=False, cleanup_ok=False, cleanup_failed=True,
                              recovery_required=True,
                              message="检测中断，无法确认还原；请检查系统网络设置")
        finally:
            if terminal is not None:
                terminal.clear()
            if process is not None:
                # Closing our write end lets the independent child restore on parent shutdown.
                for stream in (process.stdin, process.stdout):
                    try:
                        stream.close()
                    except (OSError, ValueError, AttributeError):
                        pass
                try:
                    process.wait(timeout=3)
                except (subprocess.TimeoutExpired, OSError):
                    pass  # Never kill a child which might still own temporary network state.
            with self._lock:
                if self._process is process:
                    self._process = None


_detector = None
_detector_lock = threading.Lock()


def get_detector() -> WechatDetectionController:
    global _detector
    with _detector_lock:
        if _detector is None:
            _detector = WechatDetectionController()
            atexit.register(_detector.stop)
        return _detector
