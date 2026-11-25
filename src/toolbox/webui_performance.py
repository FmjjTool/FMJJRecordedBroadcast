"""WebUI 性能诊断（只读）：本机 CPU / 内存 / 磁盘 / 当前服务进程读数。

桌面端「性能诊断」的采样、快照、导出与子进程明细仍只在桌面端。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
from pathlib import Path


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
    from toolbox.performance import process_memory

    memory = _memory()
    percent = None
    if memory:
        percent = memory.get("percent")
    process = process_memory()
    note = ("只读本机读数：桌面端「性能诊断」的采样曲线、Python 分配快照、"
            "页面耗时与导出报告仍在桌面端。")
    return {
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
