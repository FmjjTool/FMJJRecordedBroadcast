"""文件时间规则（Qt-free）：解析时间文本、按文件名提取时间、写入文件时间。

桌面「批量改名 → 时间修改」页与 WebUI 共用这一份实现：只改文件的修改 /
创建 / 访问时间，不改文件名与内容。Windows 下创建时间走 win32file，
缺 pywin32 时回落 ctypes。
"""

from __future__ import annotations

import os
import re
from datetime import datetime

_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y%m%d%H%M%S", "%Y%m%d",
)
_OFFSET_RE = re.compile(r"([+-])(\d+(?:\.\d+)?)([smhd])")
_OFFSET_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_NAME_RE = re.compile(
    r"(20\d{2})[-_.年/](\d{1,2})[-_.月/](\d{1,2})日?"
    r"(?:[-_ T](\d{1,2})[-_:T时](\d{1,2})(?:[-_:分](\d{1,2}))?)?")
_NAME_COMPACT_RE = re.compile(
    r"(20\d{2})(\d{2})(\d{2})[-_ ]?(\d{2})?(\d{2})?(\d{2})?")


def parse_time_text(text: str) -> float | None:
    """解析常见时间文本，返回时间戳；无法解析返回 None。"""
    text = str(text or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def parse_offset(text: str) -> float | str:
    """解析偏移量（+2h / -210m / -30s / +1d），返回秒数或错误文本。"""
    match = _OFFSET_RE.fullmatch(str(text or "").strip().lower())
    if not match:
        return "偏移格式错误（示例：-210m、+2h、-30s、+1d）"
    sign = 1 if match.group(1) == "+" else -1
    return sign * float(match.group(2)) * _OFFSET_UNITS[match.group(3)]


def parse_time_from_name(name: str) -> tuple[float, float, float] | None:
    """从文件名提取时间（2024-01-02、20240102、2024-01-02 15-04-05 等）。"""
    match = _NAME_RE.search(name)
    if match:
        year, month, day = (int(match.group(1)), int(match.group(2)),
                            int(match.group(3)))
        hour = int(match.group(4) or 12)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
    else:
        match = _NAME_COMPACT_RE.search(name)
        if not match:
            return None
        year, month, day = (int(match.group(1)), int(match.group(2)),
                            int(match.group(3)))
        hour = int(match.group(4) or 12)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
    try:
        ts = datetime(year, month, day, hour, minute, second).timestamp()
    except ValueError:
        return None
    return (ts, ts, ts)


def compute_times(path: str, mode: str, value: str = "",
                  offset: str = "") -> tuple[float, float, float | None] | str:
    """按方式计算目标 (atime, mtime, ctime)；无法计算时返回错误文本。"""
    try:
        stat = os.stat(path)
    except OSError:
        return "文件不可访问"
    if mode == "set":
        ts = parse_time_text(value)
        if ts is None:
            return "时间格式错误（示例：2026-08-21 20:30:00）"
        return (ts, ts, ts)
    if mode == "offset":
        delta = parse_offset(offset)
        if isinstance(delta, str):
            return delta
        return (stat.st_atime + delta, stat.st_mtime + delta,
                (stat.st_ctime + delta) if os.name == "nt" else None)
    if mode == "name":
        got = parse_time_from_name(os.path.basename(path))
        if not got:
            return "文件名无时间"
        return got
    return "未知的时间修改方式"


def set_file_times(path: str, atime: float, mtime: float,
                   ctime: float | None) -> None:
    """写入文件时间。ctime 仅 Windows 生效（pywin32 优先，无则 ctypes）。"""
    os.utime(path, (atime, mtime))
    if ctime is None or os.name != "nt":
        return
    try:
        import pywintypes
        import win32file
        handle = win32file.CreateFileW(path, win32file.GENERIC_WRITE, 0, None,
                                       win32file.OPEN_EXISTING, 0, None)
        try:
            win32file.SetFileTime(handle, pywintypes.Time(ctime),
                                  pywintypes.Time(atime),
                                  pywintypes.Time(mtime))
        finally:
            win32file.CloseHandle(handle)
    except ImportError:
        import ctypes
        from ctypes import wintypes

        def _ft(ts: float) -> wintypes.FILETIME:
            value = int(ts * 10_000_000) + 116444736000000000
            ft = wintypes.FILETIME()
            ft.dwLowDateTime = value & 0xFFFFFFFF
            ft.dwHighDateTime = value >> 32
            return ft

        kernel32 = ctypes.windll.kernel32
        create_time = _ft(ctime)
        access_time = _ft(atime)
        write_time = _ft(mtime)
        handle = kernel32.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x80,
                                      None)
        if handle != -1:
            try:
                kernel32.SetFileTime(handle, ctypes.byref(create_time),
                                     ctypes.byref(access_time),
                                     ctypes.byref(write_time))
            finally:
                kernel32.CloseHandle(handle)
