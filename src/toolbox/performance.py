"""按需性能记录；不读取业务内容、请求参数或凭证。"""

from __future__ import annotations

import os
import sys
import threading
import time
import tracemalloc
from collections import deque
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path


def auto_start_enabled() -> bool:
    """配置主数据由 config_manager 独占写入；旧配置缺省关闭，无需迁移。"""
    from toolbox.config_manager import load_convert_config

    return load_convert_config().get("performance_auto_start") is True


def save_auto_start(enabled: bool) -> bool:
    """桌面与 Web 共用同一开关，只通过配置 owner 保存并回读确认。"""
    from toolbox.config_manager import load_convert_config, save_convert_config

    if type(enabled) is not bool:
        raise ValueError("自动监控选项必须为开启或关闭")
    config = load_convert_config()
    config["performance_auto_start"] = enabled
    save_convert_config(config)
    if auto_start_enabled() != enabled:
        raise OSError("保存自动监控设置失败，请重试")
    return enabled


def saved_reports(limit=30):
    from toolbox.performance_reports import list_reports
    return list_reports(limit)


def saved_report(report_id):
    from toolbox.performance_reports import load_report
    return load_report(report_id)


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes as c
    from ctypes import wintypes as w

    class Memory(c.Structure):
        _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [
            (name, c.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    class Entry(c.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("pid", w.DWORD),
                    ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                    ("exe", w.WCHAR * 260)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    psapi = c.WinDLL("psapi", use_last_error=True)
    for name, arguments, result in (
        ("GetCurrentProcess", [], w.HANDLE),
        ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ("CloseHandle", [w.HANDLE], w.BOOL),
        ("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
        ("Process32FirstW", [w.HANDLE, c.POINTER(Entry)], w.BOOL),
        ("Process32NextW", [w.HANDLE, c.POINTER(Entry)], w.BOOL),
    ):
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = arguments, result
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Memory), w.DWORD]
    psapi.GetProcessMemoryInfo.restype = w.BOOL
    return c, kernel, psapi, Memory, Entry


def process_memory(pid=None):
    """Windows 工作集/私有提交分别测量；不可互换，也不包含子进程。"""
    if sys.platform == "win32":
        c, kernel, psapi, Memory, _ = _windows_api()
        handle = kernel.GetCurrentProcess() if pid is None else kernel.OpenProcess(0x1010, False, pid)
        if not handle:
            return {"rss": None, "private": None}
        try:
            data = Memory()
            data.cb = c.sizeof(data)
            if not psapi.GetProcessMemoryInfo(handle, c.byref(data), data.cb):
                return {"rss": None, "private": None}
            return {"rss": data.WorkingSetSize, "private": data.PrivateUsage}
        finally:
            if pid is not None:
                kernel.CloseHandle(handle)
    try:
        fields = Path(f"/proc/{pid or os.getpid()}/statm").read_text().split()
        return {"rss": int(fields[1]) * os.sysconf("SC_PAGE_SIZE"), "private": None}
    except (OSError, ValueError, IndexError, AttributeError):
        return {"rss": None, "private": None}


def child_process_memory():
    """递归统计当前仍存在的子进程；只保留程序名，不记录命令行。"""
    if sys.platform != "win32":
        return []
    c, kernel, _, _, Entry = _windows_api()
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, c.c_void_p(-1).value):
        return []
    entries = []
    try:
        entry = Entry()
        entry.dwSize = c.sizeof(entry)
        more = kernel.Process32FirstW(handle, c.byref(entry))
        while more:
            entries.append((entry.pid, entry.parent, entry.exe))
            more = kernel.Process32NextW(handle, c.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    parents, found = {os.getpid()}, []
    while True:
        next_entries = [(pid, name) for pid, parent, name in entries
                        if parent in parents and pid not in parents]
        if not next_entries:
            break
        for pid, name in next_entries:
            parents.add(pid)
            found.append({"pid": pid, "name": name, **process_memory(pid)})
    return found


def source_location(filename, line):
    """报告保留项目相对位置；外部文件只保留文件名，避免持久化用户目录。"""
    parts = str(filename).replace("\\", "/").split("/")
    if "src" in parts:
        name = "/".join(parts[parts.index("src"):])
    else:
        name = parts[-1]
    return f"{name}:{line}"


class PerformanceRecorder:
    MAX_SAMPLES = 3600
    MAX_EVENTS = 2000

    def __init__(self):
        self._lock = threading.RLock()
        self.active = False
        self.session = 0
        self._trace_owned = False
        self._snapshot = None
        self.samples = deque(maxlen=self.MAX_SAMPLES)
        self.events = deque(maxlen=self.MAX_EVENTS)
        self.snapshots = deque(maxlen=20)
        self.running = {}
        self._task_id = 0
        self.dropped_samples = self.dropped_events = 0
        self._startup = False
        self._startup_complete = False
        self._startup_phases = {}
        self._phase_pending = {}
        self._phase_id = 0
        self._startup_completed_seconds = None

    def start(self, trace=False, *, started=None, started_at=None, cpu_started=None, startup=False):
        with self._lock:
            if self.active:
                return
            if trace and tracemalloc.is_tracing():
                raise RuntimeError("Python 内存追踪已被其他工具开启，请先停止那个工具")
            if trace:
                tracemalloc.start(5)
            self._trace_owned = bool(trace)
            self.trace_enabled = bool(trace)
            self._snapshot = None
            self.session += 1
            self.samples.clear()
            self.events.clear()
            self.snapshots.clear()
            self.running.clear()
            self.dropped_samples = self.dropped_events = 0
            self.started = time.perf_counter() if started is None else started
            self.started_at = started_at or datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="milliseconds")
            self.stopped_at = None
            self._last_wall = self.started
            self._last_cpu = time.process_time() if cpu_started is None else cpu_started
            self._startup = bool(startup)
            self._startup_complete = False
            self._startup_phases.clear()
            self._phase_pending.clear()
            self._startup_completed_seconds = None
            self.active = True

    def stop(self):
        with self._lock:
            if not self.active:
                return
            self.active = False
            self.stopped_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
            self._snapshot = None
            if self._trace_owned:
                tracemalloc.stop()
            self._trace_owned = False

    def event(self, kind, label, **metrics):
        with self._lock:
            if not self.active:
                return
            self.dropped_events += len(self.events) == self.MAX_EVENTS
            self.events.append({"seconds": round(time.perf_counter() - self.started, 3),
                                "kind": kind, "label": str(label)[:180], **metrics})

    def sample(self, page, lag_ms, inventory, children):
        with self._lock:
            if not self.active:
                return
            now, cpu = time.perf_counter(), time.process_time()
            percent = 100 * (cpu - self._last_cpu) / max(now - self._last_wall, .001)
            self._last_wall, self._last_cpu = now, cpu
            traced, peak = tracemalloc.get_traced_memory() if self._trace_owned else (None, None)
            sample = {"seconds": round(now - self.started, 3), "page": page,
                      **process_memory(), "cpu_one_core_percent": round(percent, 1),
                      "ui_lag_ms": None if lag_ms is None else round(lag_ms, 1), "python_bytes": traced,
                      "python_peak_bytes": peak,
                      "trace_overhead_bytes": tracemalloc.get_tracemalloc_memory() if self._trace_owned else None,
                      "threads": threading.active_count(), "tasks": len(self.running),
                      "pages": inventory, "children": children}
            self.dropped_samples += len(self.samples) == self.MAX_SAMPLES
            self.samples.append(sample)
            return sample

    def phase_started(self, name):
        """只记录固定启动阶段与进程边界读数，不记录参数、业务内容或异常消息。"""
        with self._lock:
            if not self.active or not self._startup:
                return None
            self._phase_id += 1
            token = {"id": self._phase_id, "session": self.session, "name": str(name)[:80],
                     "time": time.perf_counter(), "cpu": time.process_time(), "memory": process_memory()}
            self._phase_pending[token["id"]] = token
            return token

    def phase_finished(self, token, failed=False, error_type=None):
        with self._lock:
            if token is None or token["session"] != self.session:
                return
            self._phase_pending.pop(token["id"], None)
            after = process_memory()
            deltas = {f"{key}_delta": after[key] - token["memory"][key]
                      if after[key] is not None and token["memory"][key] is not None else None
                      for key in ("rss", "private")}
            self.record_startup_phase(token["name"], (time.perf_counter() - token["time"]) * 1000,
                                      failed=failed, error_type=error_type,
                                      started_seconds=token["time"] - self.started,
                                      cpu_ms=(time.process_time() - token["cpu"]) * 1000, **deltas)

    def record_startup_phase(self, name, duration_ms, *, failed=False, error_type=None,
                             started_seconds=None, cpu_ms=None, rss_delta=None, private_delta=None):
        with self._lock:
            if not self.active or not self._startup:
                return
            name = str(name)[:80]
            if name not in self._startup_phases and len(self._startup_phases) >= 128:
                name = "其他启动阶段"
            row = self._startup_phases.setdefault(name, {
                "name": name, "count": 0, "total_ms": 0.0, "max_ms": 0.0, "failures": 0,
                "rss_delta": None, "private_delta": None, "cpu_ms": None,
                "first_seconds": round(started_seconds or 0, 3), "last_seconds": 0.0})
            row["count"] += 1
            row["total_ms"] = round(row["total_ms"] + duration_ms, 2)
            row["max_ms"] = max(row["max_ms"], round(duration_ms, 2))
            row["failures"] += bool(failed)
            row["last_seconds"] = round(time.perf_counter() - self.started, 3)
            for key, value in (("rss_delta", rss_delta), ("private_delta", private_delta), ("cpu_ms", cpu_ms)):
                if value is not None:
                    row[key] = (row[key] or 0) + value
            if error_type:
                row["error_type"] = str(error_type)[:80]
            self.event("startup_phase", name, duration_ms=round(duration_ms, 2), failed=bool(failed),
                       rss_delta=rss_delta, private_delta=private_delta)

    def complete_startup(self):
        with self._lock:
            if self.active and self._startup and not self._startup_complete:
                self._startup_complete = True
                self._startup_completed_seconds = round(time.perf_counter() - self.started, 3)
                return True
            return False

    def task_started(self, fn):
        with self._lock:
            if not self.active:
                return None
            self._task_id += 1
            token = (self.session, self._task_id)
            label = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', type(fn).__name__)}"
            self.running[token[1]] = {"label": label[:180], "started": time.perf_counter()}
            return token

    def task_finished(self, token, phase, duration_ms, failed=False):
        with self._lock:
            if token is None or not self.active or token[0] != self.session:
                return
            task = self.running.get(token[1])
            if task is None:
                return
            self.event(phase, task["label"], task_id=token[1], duration_ms=round(duration_ms, 2), failed=failed)
            if phase == "callback":
                self.running.pop(token[1], None)

    def memory_snapshot(self, session):
        with self._lock:
            if not self.active or session != self.session or not self._trace_owned:
                raise RuntimeError("请先开启 Python 内存追踪")
            previous = self._snapshot
        snapshot = tracemalloc.take_snapshot()
        current = snapshot.statistics("lineno")
        growth = snapshot.compare_to(previous, "lineno") if previous is not None else []

        def row(stat, difference=False):
            frame = stat.traceback[0]
            return {"location": source_location(frame.filename, frame.lineno),
                    "bytes": stat.size_diff if difference else stat.size,
                    "blocks": stat.count_diff if difference else stat.count}

        result = {"top": [row(stat) for stat in current[:30]],
                  "growth": [row(stat, True) for stat in growth if stat.size_diff > 0][:30],
                  "baseline": previous is None}
        with self._lock:
            if not self.active or session != self.session:
                return None
            self._snapshot = snapshot
            result["seconds"] = round(time.perf_counter() - self.started, 3)
            self.snapshots.append(result)
        return result

    def report(self):
        with self._lock:
            return {"schema_version": 1, "started_at": getattr(self, "started_at", None),
                    "stopped_at": getattr(self, "stopped_at", None), "active": self.active,
                    "platform": sys.platform, "python": sys.version.split()[0],
                    "python_tracing": getattr(self, "trace_enabled", False),
                    "startup": {"origin": "python_entry" if self._startup else None,
                                "complete": self._startup_complete,
                                "completed_seconds": self._startup_completed_seconds,
                                "phases": [dict(row) for row in self._startup_phases.values()],
                                "pending": [{"name": token["name"],
                                             "elapsed_ms": round((time.perf_counter() - token["time"]) * 1000, 2)}
                                            for token in self._phase_pending.values()],
                                "limitations": ["起点为 Python 入口，不能覆盖解释器与打包引导加载。",
                                                "内存从诊断初始化后开始采样，阶段增量不代表独占内存。",
                                                "阶段可能嵌套或并发，耗时与内存增量不可直接相加。"]},
                    "samples": list(self.samples), "events": list(self.events),
                    "memory_snapshots": list(self.snapshots),
                    "running_tasks": [{"label": task["label"], "started_seconds": round(task["started"] - self.started, 3)}
                                      for task in self.running.values()],
                    "dropped_samples": self.dropped_samples, "dropped_events": self.dropped_events,
                    "notes": ["CPU 100% 表示用满一个逻辑核，可超过 100%。",
                              "工作集与私有提交不同，不能相加；Python 追踪不涵盖 Qt/图片/全部原生分配。",
                              "页面增量是同一时段整个进程的变化，不能归为页面独占内存。",
                              "追踪只覆盖开启后的 Python 分配，并会增加 CPU/内存开销。",
                              "子进程为最近一次采样的当前后代，受进程退出与访问权限影响。"]}
