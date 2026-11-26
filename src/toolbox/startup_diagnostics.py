"""Qt-free 诊断会话：入口采样交接、启动阶段、周期/最终自动保存。

记录器是内存主数据；performance_reports 是自动 JSON 报告的唯一写入方。
开关关闭时不创建线程、目录、文件或异常钩子。这里只记录固定阶段与性能指标。
"""

from __future__ import annotations

import sys
import threading
import time

_current = None
_hook = None


class DiagnosticSession:
    SAVE_INTERVAL = 15.0
    SAMPLE_INTERVAL = 1.0

    def __init__(self, recorder, *, early=False):
        from toolbox.performance_reports import new_report_id
        self.recorder = recorder
        self.report_id = new_report_id()
        self.early = early
        self.gui_attached = False
        self.status = "running"
        self.error_type = None
        self._save_lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = False
        self._requested = None
        self._last_saved = None
        self._save_error = None
        self._worker = None

    def start(self):
        self.save("startup_started" if self.early else "monitor_started")
        self._worker = threading.Thread(target=self._run, name="performance-autosave", daemon=True)
        self._worker.start()
        return self

    def _run(self):
        next_sample = time.monotonic() + self.SAMPLE_INTERVAL
        next_save = time.monotonic() + self.SAVE_INTERVAL
        while not self._closed:
            self._wake.wait(max(0.01, min(next_sample, next_save) - time.monotonic()))
            self._wake.clear()
            if self._closed:
                break
            now = time.monotonic()
            if now >= next_sample:
                if self.early and not self.gui_attached and self.recorder.active:
                    try:
                        self.recorder.sample("启动阶段", None, None, [])
                    except Exception:
                        pass
                next_sample = now + self.SAMPLE_INTERVAL
            reason, self._requested = self._requested, None
            if reason or now >= next_save:
                self.save(reason or "periodic")
                next_save = time.monotonic() + self.SAVE_INTERVAL

    def request_save(self, reason="startup_phase"):
        if not self._closed:
            self._requested = reason
            self._wake.set()

    def attach_gui(self):
        self.gui_attached = True

    def info(self):
        # 保存完成后整份替换元数据；UI 读最后成功快照，不等待磁盘写入锁。
        saved = self._last_saved or {}
        return {"id": self.report_id, "enabled": True,
                "filename": saved.get("filename"), "saved_at": saved.get("saved_at"),
                "error": self._save_error, "status": self.status,
                "interval_seconds": self.SAVE_INTERVAL}

    def _save_locked(self, reason):
        from toolbox.performance_reports import save_report
        try:
            snapshot = self.recorder.report()
            snapshot["status"] = self.status
            if self.error_type:
                snapshot["error_type"] = self.error_type
            saved = save_report(self.report_id, snapshot, reason)
            self._last_saved = saved
            self._save_error = None
            return True
        except Exception as exc:
            self._save_error = type(exc).__name__
            return False

    def save(self, reason="periodic"):
        # 快照在同一锁内生成；最终保存之后不允许旧周期快照回写。
        with self._save_lock:
            if self._closed:
                return False
            return self._save_locked(reason)

    def save_now(self):
        with self._save_lock:
            return self._save_locked(self.status if self._closed else "manual_save")

    def finish(self, reason="stopped", error_type=None):
        with self._save_lock:
            if self._closed:
                return
            self._closed = True
            self.status = reason
            self.error_type = str(error_type)[:80] if error_type else self.error_type
            self.recorder.stop()
            self._save_locked(reason)
        self._wake.set()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=1.0)


def current_session():
    return _current


def current_recorder():
    return _current.recorder if _current is not None and _current.recorder.active else None


def start_runtime_session(recorder, trace=False):
    global _current
    if _current is not None and _current.recorder is recorder:
        _current.finish("stopped")
    recorder.start(trace)
    _current = DiagnosticSession(recorder).start()
    _current.attach_gui()
    return _current


def bootstrap(entry_started, entry_wall, entry_cpu):
    """桌面入口调用一次；调用前必须完成全部无窗口参数分流。"""
    global _current
    from toolbox.config_manager import performance_auto_start_readonly
    try:
        enabled = performance_auto_start_readonly()
    except Exception:
        return None
    if not enabled:
        return None
    from datetime import datetime, timezone, timedelta
    from toolbox.performance import PerformanceRecorder
    recorder = PerformanceRecorder()
    recorder.start(False, started=entry_started,
                   started_at=datetime.fromtimestamp(entry_wall, timezone(timedelta(hours=8))).isoformat(timespec="milliseconds"),
                   cpu_started=entry_cpu, startup=True)
    recorder.sample("启动阶段", None, None, [])
    recorder.record_startup_phase("入口与诊断准备", (time.perf_counter() - entry_started) * 1000,
                                  started_seconds=0, cpu_ms=(time.process_time() - entry_cpu) * 1000)
    _current = DiagnosticSession(recorder, early=True).start()
    import atexit
    atexit.register(finish_current, "process_exit")
    install_exception_hook()
    return _current


class _Phase:
    def __init__(self, name):
        self.name = name
        self.session = None
        self.token = None

    def __enter__(self):
        self.session = current_session()
        if self.session is not None:
            try:
                self.token = self.session.recorder.phase_started(self.name)
                self.session.request_save()
            except Exception:
                self.token = None
        return self

    def __exit__(self, kind, value, tb):
        if self.session is not None and self.token is not None:
            try:
                self.session.recorder.phase_finished(self.token, kind is not None, kind.__name__ if kind else None)
                self.session.request_save()
            except Exception:
                pass
        return False


def phase(name):
    return _Phase(name)


def complete_startup():
    session = current_session()
    if session is not None and session.recorder.complete_startup():
        session.request_save("startup_complete")


def finish_current(reason="normal_exit", error_type=None):
    session = current_session()
    if session is not None:
        session.finish(reason, error_type)


def finish_failure(error_type):
    session = current_session()
    if session is not None:
        reason = "startup_failure" if session.early and not session.recorder._startup_complete else "uncaught_exception"
        session.finish(reason, error_type)


def install_exception_hook():
    """日志初始化可能替换钩子；链式重装，只存异常类型、不存消息/参数。"""
    global _hook
    if current_recorder() is None or sys.excepthook is _hook:
        return
    previous = sys.excepthook

    def capture(kind, value, tb):
        session = current_session()
        if session is not None:
            if not session.gui_attached:
                session.finish("startup_failure", kind.__name__)
            else:
                session.recorder.event("uncaught_exception", kind.__name__)
                session.error_type = kind.__name__
                session.save("uncaught_exception")
        previous(kind, value, tb)

    _hook = capture
    sys.excepthook = capture
