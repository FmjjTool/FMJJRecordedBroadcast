"""WebUI 性能诊断：本机只读读数、桌面启动选项与已保存报告。

不依赖桌面进程或 Qt；历史报告只通过 performance_reports owner 读取。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
from pathlib import Path


FIELDS = {
    "auto_start": {"type": "bool", "label": "启动桌面软件后自动监控", "default": False},
}


def save_settings(payload: dict) -> dict:
    from toolbox.performance import save_auto_start

    if set(payload) != set(FIELDS):
        raise ValueError("请提供自动监控选项")
    return {"auto_start": save_auto_start(payload["auto_start"])}


def reports(limit: int = 30) -> dict:
    """读取已落盘的性能会话，不启动或控制桌面监控。"""
    from toolbox import performance_reports

    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("报告条数需在 1–30 之间")
    return performance_reports.list_reports(limit=limit)


def report_detail(report_id: str) -> dict:
    """报告标识和读取边界由唯一存储 owner 校验。"""
    from toolbox import performance_reports

    return performance_reports.load_report(report_id)


def report_download(report_id: str) -> dict:
    """返回同一份已保存报告的 JSON，不重新采样，也不写导出目录。"""
    from toolbox.performance_reports import read_report_bytes
    return {
        "data": read_report_bytes(report_id),
        "filename": report_id + ".json",
    }


def _cpu_percent() -> float | None:
    try:
        import psutil
        return float(psutil.cpu_percent(interval=0.2))
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                            ("dwHighDateTime", ctypes.c_uint32)]

            kernel32 = ctypes.windll.kernel32

            def _sample():
                idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
                kernel32.GetSystemTimes(ctypes.byref(idle),
                                        ctypes.byref(kernel),
                                        ctypes.byref(user))
                return (idle.dwLowDateTime + (idle.dwHighDateTime << 32),
                        kernel.dwLowDateTime + (kernel.dwHighDateTime << 32),
                        user.dwLowDateTime + (user.dwHighDateTime << 32))

            first = _sample()
            import time
            time.sleep(0.2)
            second = _sample()
            idle_delta = second[0] - first[0]
            total_delta = ((second[1] - first[1]) + (second[2] - first[2]))
            if total_delta <= 0:
                return None
            return round(max(0.0, 100.0 * (1 - idle_delta / total_delta)), 1)
        except Exception:
            return None
    try:
        load = os.getloadavg()[0]
        return round(min(100.0, load / max(1, os.cpu_count() or 1) * 100), 1)
    except (OSError, AttributeError):
        return None


def _memory() -> dict:
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {"total": int(mem.total), "available": int(mem.available),
                "used": int(mem.total - mem.available),
                "percent": float(mem.percent)}
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32),
                            ("dwMemoryLoad", ctypes.c_uint32),
                            ("ullTotalPhys", ctypes.c_uint64),
                            ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64),
                            ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64),
                            ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(
                    ctypes.byref(status)):
                total = int(status.ullTotalPhys)
                available = int(status.ullAvailPhys)
                return {"total": total, "available": available,
                        "used": total - available,
                        "percent": float(status.dwMemoryLoad)}
        except Exception:
            pass
    return {}


def _disks() -> list[dict]:
    rows = []
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            rows.append({"mount": part.mountpoint,
                         "total": int(usage.total), "used": int(usage.used),
                         "free": int(usage.free),
                         "percent": float(usage.percent)})
    except Exception:
        rows = []
    if not rows and sys.platform == "win32":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            mount = f"{letter}:\\"
            if not Path(mount).exists():
                continue
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            rows.append({
                "mount": mount, "total": int(usage.total),
                "used": int(usage.total - usage.free), "free": int(usage.free),
                "percent": round(100.0 * (usage.total - usage.free)
                                 / max(1, usage.total), 1),
            })
    return rows


def overview() -> dict:
    from toolbox.performance import auto_start_enabled, process_memory

    memory = _memory()
    percent = None
    if memory:
        percent = memory.get("percent")
    process = process_memory()
    note = ("本机读数只读；自动监控选项将在下次启动桌面软件时生效，不控制当前桌面进程。"
            "已自动保存的报告可在下方查看和下载；实时采样、标记与 Python 快照仍在桌面端。")
    return {
        "fields": FIELDS,
        "settings": {"auto_start": auto_start_enabled()},
        "cpu": {"percent": _cpu_percent(), "cores": os.cpu_count()},
        "memory": memory,
        "disks": _disks(),
        "process": {
            "pid": os.getpid(),
            "rss": process.get("rss"),
            "private": process.get("private"),
            "threads": threading.active_count(),
            "python": sys.version.split()[0],
        },
        "platform": sys.platform,
        "note": note,
    }
