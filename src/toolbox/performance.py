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
    """报告保留项目相对位置，第三方文件只保留末两级，不输出用户名。"""
    parts = str(filename).replace("\\", "/").split("/")
    if "src" in parts:
        name = "/".join(parts[parts.index("src"):])
    else:
        name = "/".join(parts[-2:])
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

    def start(self, trace=False):
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
            self.started = time.perf_counter()
            self.started_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
            self.stopped_at = None
            self._last_wall, self._last_cpu = self.started, time.process_time()
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
                      "ui_lag_ms": round(lag_ms, 1), "python_bytes": traced,
                      "python_peak_bytes": peak,
                      "trace_overhead_bytes": tracemalloc.get_tracemalloc_memory() if self._trace_owned else None,
                      "threads": threading.active_count(), "tasks": len(self.running),
                      "pages": inventory, "children": children}
            self.dropped_samples += len(self.samples) == self.MAX_SAMPLES
            self.samples.append(sample)
            return sample

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
