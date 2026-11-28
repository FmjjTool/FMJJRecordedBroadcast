"""Private-pipe worker for the explicit, bounded reading-detection session.

L1 lifecycle only: no application configuration, account storage or logging.
Detection is bounded to 120 seconds; cleanup retries are intentionally unbounded.
A killed worker or power loss cannot be repaired by an in-memory lease.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import sys
import threading
import time
from urllib.parse import urlencode

from toolbox.wechat_capture_windows import CaptureNativeError


_MAX_COMMAND = 1024
_MAX_TIMEOUT = 120
_SAFE_CODES = frozenset({
    "unsupported", "invalid_port", "invalid_certificate", "busy", "existing_proxy",
    "proxy_read_failed", "proxy_write_failed", "proxy_restore_failed",
    "certificate_prepare_failed", "certificate_install_failed",
    "certificate_remove_failed", "certificate_cleanup_failed", "cleanup_pending", "native_failed",
})


class _ProtocolError(RuntimeError):
    pass


class _PollingPipeReader:
    """Read only available pipe bytes so closing an idle input cannot hang."""

    def __init__(self, stream, kernel):
        import msvcrt

        self.stream = stream
        self.kernel = kernel
        self.handle = msvcrt.get_osfhandle(stream.fileno())
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.pending = bytearray()

    def readline(self, limit):
        while not self.closed.is_set():
            ending = self.pending.find(b"\n", 0, limit)
            count = ending + 1 if ending >= 0 else min(len(self.pending), limit)
            if ending >= 0 or count == limit:
                result = bytes(self.pending[:count])
                del self.pending[:count]
                return result
            with self.lock:
                if self.closed.is_set():
                    return b""
                available = wintypes.DWORD()
                if not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                    result = bytes(self.pending)
                    self.pending.clear()
                    return result
                if available.value:
                    # There is one reader; reading no more than the available
                    # count cannot wait for a future parent command.
                    data = self.stream.read(min(available.value, limit - len(self.pending)))
                    if not data:
                        return b""
                    self.pending.extend(data)
                    continue
            self.closed.wait(0.05)
        return b""

    def close(self):
        self.closed.set()
        with self.lock:
            self.stream.close()


class _WindowsPipes:
    """Rebuild inherited PIPE handles even for PyInstaller --windowed."""

    def __init__(self):
        if sys.platform != "win32":
            raise _ProtocolError()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        self.kernel.GetStdHandle.restype = ctypes.c_void_p
        self.kernel.GetFileType.argtypes = [ctypes.c_void_p]
        self.kernel.GetFileType.restype = wintypes.DWORD
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = ctypes.c_void_p
        self.kernel.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
                                              wintypes.BOOL, wintypes.DWORD]
        self.kernel.DuplicateHandle.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.PeekNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                             ctypes.POINTER(wintypes.DWORD)]
        self.kernel.PeekNamedPipe.restype = wintypes.BOOL

    def open_pipe(self, number: int, mode: str):
        import msvcrt

        handle = self.kernel.GetStdHandle(number & 0xffffffff)
        if handle in (None, 0, ctypes.c_void_p(-1).value) or self.kernel.GetFileType(handle) != 3:
            raise _ProtocolError()
        process = self.kernel.GetCurrentProcess()
        duplicate = ctypes.c_void_p()
        if not self.kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
            raise _ProtocolError()
        flags = (os.O_RDONLY if mode == "rb" else os.O_WRONLY) | os.O_BINARY
        flags |= getattr(os, "O_NOINHERIT", 0)
        try:
            descriptor = msvcrt.open_osfhandle(duplicate.value, flags)
        except BaseException:
            self.kernel.CloseHandle(duplicate)
            raise _ProtocolError() from None
        try:
            stream = os.fdopen(descriptor, mode, buffering=0)
        except BaseException:
            os.close(descriptor)
            raise _ProtocolError() from None
        try:
            # A blocking CRT read prevents closing the handle when successful
            # detection finishes before the parent closes its write end.
            return _PollingPipeReader(stream, self.kernel) if mode == "rb" else stream
        except BaseException:
            stream.close()
            raise _ProtocolError() from None


def _private_stdio(api=None):
    api = api or _WindowsPipes()
    reader = api.open_pipe(-10, "rb")
    try:
        return reader, api.open_pipe(-11, "wb")
    except BaseException:
        reader.close()
        raise


class _ParentWatch:
    def __init__(self):
        self.handle = None
        self.kernel = None
        if sys.platform == "win32":
            try:
                self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                self.kernel.OpenProcess.restype = ctypes.c_void_p
                self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
                self.kernel.WaitForSingleObject.restype = wintypes.DWORD
                self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                self.kernel.CloseHandle.restype = wintypes.BOOL
                self.handle = self.kernel.OpenProcess(0x100000, False, os.getppid())
            except Exception:
                self.handle = None

    def alive(self) -> bool:
        # If Windows denies the process handle, private stdin EOF still watches
        # the only parent's write end; this does not create a public endpoint.
        return not self.handle or self.kernel.WaitForSingleObject(self.handle, 0) == 258

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class _Commands:
    def __init__(self, stream, wake):
        self.stream = stream
        self.wake = wake
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.bad = threading.Event()
        self.timeout = _MAX_TIMEOUT
        self.thread = threading.Thread(target=self._read, daemon=True, name="wechat-detection-input")

    def start(self):
        self.thread.start()

    def _read(self):
        first = True
        try:
            while True:
                line = self.stream.readline(_MAX_COMMAND + 1)
                if not line:
                    self.stop.set()
                    return
                if len(line) > _MAX_COMMAND or not line.endswith(b"\n"):
                    raise _ProtocolError()
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise _ProtocolError()
                if first:
                    timeout = value.get("timeout", _MAX_TIMEOUT)
                    if (value.get("command") != "start" or set(value) - {"command", "timeout"}
                            or isinstance(timeout, bool) or not isinstance(timeout, int)
                            or not 1 <= timeout <= _MAX_TIMEOUT):
                        raise _ProtocolError()
                    self.timeout = timeout
                    first = False
                    self.ready.set()
                    self.wake.set()
                elif value == {"command": "stop"}:
                    self.stop.set()
                    return
                else:
                    raise _ProtocolError()
        except BaseException:
            self.bad.set()
            self.stop.set()
        finally:
            self.ready.set()
            self.wake.set()


class _Output:
    def __init__(self, stream):
        self.stream = stream
        self.failed = False

    def emit(self, state, *, cleanup_ok=False, cleanup_failed=False, url=None, code=None):
        if self.failed:
            return False
        data = {"state": state, "busy": state in ("starting", "detecting", "restoring"),
                "cleanup_ok": cleanup_ok, "cleanup_failed": cleanup_failed}
        if state == "succeeded" and cleanup_ok and url:
            data["url"] = url
        if state == "error" and isinstance(code, str) and code in _SAFE_CODES:
            data["code"] = code
        line = (json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        try:
            pending = memoryview(line)
            while pending:
                written = self.stream.write(pending)
                if not isinstance(written, int) or written <= 0:
                    raise _ProtocolError()
                pending = pending[written:]
            self.stream.flush()
            return True
        except BaseException:
            self.failed = True
            return False


class _Candidate:
    def __init__(self, wake):
        self.lock = threading.Lock()
        self.wake = wake
        self.armed = False
        self.value = ""

    def capture(self, value):
        with self.lock:
            if not self.armed or self.value:
                return
        try:
            # This existing validator is pure: it does not import auth, read
            # configuration, contact the platform, or install a session.
            from wechat_session import validate_session_url
            if not isinstance(value, str) or len(value) > 32768:
                return
            params = validate_session_url(value)
            minimal = {key: params[key] for key in ("__biz", "key", "uin", "pass_ticket", "appmsg_token") if key in params}
            candidate = "https://mp.weixin.qq.com/s?" + urlencode(minimal)
            validate_session_url(candidate)
        except Exception:
            return
        with self.lock:
            if self.armed and not self.value:
                self.value = candidate
                self.wake.set()

    def read(self):
        with self.lock:
            return self.value

    def disarm(self):
        with self.lock:
            self.armed = False

    def clear(self):
        with self.lock:
            self.value = ""
            self.armed = False


def run_worker(reader, writer, *, certificate_factory=None, lease_factory=None,
               proxy_factory=None, clock=None, retry_wait=None, parent_alive=None,
               poll_wait=None) -> int:
    """Inject only fake facilities/streams for isolated lifecycle verification."""
    if certificate_factory is None or lease_factory is None:
        from toolbox.wechat_capture_windows import CaptureCertificate, WindowsCaptureLease
        certificate_factory = certificate_factory or CaptureCertificate
        lease_factory = lease_factory or WindowsCaptureLease
    if proxy_factory is None:
        from toolbox.wechat_capture_proxy import WechatCaptureProxy
        proxy_factory = WechatCaptureProxy
    clock = clock or time.monotonic
    parent_alive = parent_alive or (lambda: True)
    retry_wait = retry_wait or threading.Event().wait
    wake = threading.Event()
    poll_wait = poll_wait or wake.wait
    output = _Output(writer)
    commands = _Commands(reader, wake)
    candidate = _Candidate(wake)
    certificate = lease = proxy = None
    state = "error"
    error_code = None
    deadline = None
    restore_reported = False

    def interrupted(*, include_deadline=True):
        if commands.bad.is_set() or output.failed:
            return "error"
        if commands.stop.is_set() or not parent_alive():
            return "cancelled"
        if include_deadline and deadline is not None and clock() >= deadline:
            return "expired"
        return None

    def retry():
        # A cancellation event must not turn cleanup into a busy loop.
        try:
            retry_wait(1.0)
        except BaseException:
            pass

    def report_failed_cleanup():
        nonlocal restore_reported
        if not restore_reported:
            output.emit("restoring", cleanup_failed=True)
            restore_reported = True

    try:
        commands.start()
        handshake_deadline = clock() + 5
        while not commands.ready.is_set() and clock() < handshake_deadline and parent_alive():
            poll_wait(0.05)
            wake.clear()
        if not commands.ready.is_set():
            raise _ProtocolError()
        state = interrupted() or "starting"
        if state == "starting":
            deadline = clock() + commands.timeout
            output.emit("starting")
            if interrupted() is None:
                certificate = certificate_factory()
                certificate.prepare()
            if interrupted() is None:
                proxy = proxy_factory(server_context=certificate.context, on_capture=candidate.capture)
                proxy.start()
            if interrupted() is None:
                # Retain the object even if acquisition partly fails.
                lease = lease_factory()
                lease.acquire(proxy.port, certificate.root_der)
            state = interrupted() or "detecting"
            if state == "detecting":
                with candidate.lock:
                    candidate.armed = True
                output.emit("detecting")
                while True:
                    state = interrupted() or "detecting"
                    if state != "detecting":
                        break
                    if candidate.read():
                        state = "succeeded"
                        break
                    if not proxy.running:
                        state = "error"
                        break
                    poll_wait(min(0.1, max(0.0, deadline - clock())))
                    wake.clear()
    except BaseException as error:
        state = "error"
        if isinstance(error, CaptureNativeError) and error.code in _SAFE_CODES:
            error_code = error.code
    finally:
        candidate.disarm()
        if certificate is not None or proxy is not None or lease is not None:
            output.emit("restoring")
        if lease is not None:
            while True:
                try:
                    if lease.release().get("ok") is True:
                        break
                except BaseException:
                    pass
                report_failed_cleanup()
                retry()
        if proxy is not None:
            while True:
                try:
                    if proxy.stop() is True:
                        break
                except BaseException:
                    pass
                report_failed_cleanup()
                retry()
        if certificate is not None:
            while True:
                try:
                    certificate.close()
                    break
                except BaseException:
                    report_failed_cleanup()
                    retry()
        late_reason = interrupted(include_deadline=False)
        if late_reason:
            state = late_reason
        value = candidate.read() if state == "succeeded" else None
        output.emit(state, cleanup_ok=True, url=value, code=error_code)
        value = None
        candidate.clear()
    return 1 if state == "error" else 0


def main() -> int:
    reader = writer = parent = None
    try:
        reader, writer = _private_stdio()
        parent = _ParentWatch()
        return run_worker(reader, writer, parent_alive=parent.alive)
    except BaseException:
        # A stream/bootstrap failure occurs before a lease exists.  Do not
        # replace a missing private pipe with console, files or /dev/null.
        return 2
    finally:
        if parent is not None:
            parent.close()
        for stream in (reader, writer):
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
