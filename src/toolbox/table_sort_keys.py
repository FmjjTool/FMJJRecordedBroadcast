"""表格显示排序键（Qt-free）；桌面与 Web 共用，空值恒在末尾。"""

from __future__ import annotations

import re
from datetime import datetime

_EMPTY = (9, 0)
_NUM_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_UNIT = (("亿", 1e8), ("万", 1e4), ("w", 1e4), ("W", 1e4),
         ("千", 1e3), ("k", 1e3), ("K", 1e3))
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d", "%Y年%m月%d日", "%m-%d %H:%M", "%H:%M:%S", "%H:%M",
)
_STRIP_SUFFIX = ("次播放", "分钟/段", "MB/段", "GB/段", "分钟", "小时",
                 "播放", "次", "人", "条", "个", "秒", "分", "%",
                 "kbps", "kHz", "MB", "GB", "KB", "元", "张", "字", "首")


def _as_number(text: str):
    t = text.replace(",", "").replace("，", "").replace(" ", "").strip()
    for suffix in _STRIP_SUFFIX:
        if t.endswith(suffix) and len(t) > len(suffix):
            t = t[: -len(suffix)]
            break
    mult = 1.0
    for unit, factor in _UNIT:
        if t.endswith(unit) and len(t) > len(unit):
            t, mult = t[: -len(unit)], factor
            break
    if not _NUM_RE.match(t):
        return None
    try:
        return float(t) * mult
    except ValueError:
        return None


def _as_datetime(text: str):
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def sort_key(text) -> tuple:
    """显示文本 → 可比较排序键。空值返回 ``_EMPTY``。"""
    if text is None:
        return _EMPTY
    s = str(text).strip()
    if not s or s in {"-", "—", "--", "N/A", "n/a", "无", "未知"}:
        return _EMPTY
    num = _as_number(s)
    if num is not None:
        return (0, num)
    dt = _as_datetime(s)
    if dt is not None:
        return (1, dt.timestamp())
    return (2, s.casefold())


def sort_records(records, key_func, ascending: bool = True) -> list:
    """按 ``key_func(record)`` 排序记录列表；空值恒排最后。返回新列表。"""
    filled, empty = [], []
    for rec in records:
        k = key_func(rec)
        (empty if k == _EMPTY else filled).append(rec)
    filled.sort(key=key_func, reverse=not ascending)
    return filled + empty
