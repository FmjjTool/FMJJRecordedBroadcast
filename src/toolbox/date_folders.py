"""日期文件夹（工作目录/YYYYMMDD）的统一识别与扫描。

正则、日期取值、当天字符串、排序扫描都从这里出，页面与引擎不再各写一份。
"""

import os
import re
from datetime import datetime
from typing import Iterable, List, Tuple

# 日期文件夹以 8 位日期开头；`20260923_备份` 这类带后缀的仍归入同一天，
# 跨天录制的文件夹为 `20261124~20261126`。
DATE_FOLDER_RE = re.compile(r"^(20\d{6})")
DATE_RANGE_SEP = "~"
_COMPACT_DATE_RE = re.compile(r"^(20\d{2})(\d{2})(\d{2})$")
_SEP_DATE_RE = re.compile(r"^(20\d{2})[-./](\d{1,2})[-./](\d{1,2})$")


def match_date_folder(name: str):
    """返回日期文件夹的 re.Match（前缀 20XXXXXX）；不是日期文件夹返回 None。"""
    return DATE_FOLDER_RE.match(name or "")


def _normalize_date(value: str) -> str:
    """YYYYMMDD / YYYY-MM-DD / YYYY.MM.DD / YYYY_MM_DD → YYYYMMDD；非法返回空串。"""
    text = str(value or "").strip()
    for pattern in (_COMPACT_DATE_RE, _SEP_DATE_RE):
        match = pattern.fullmatch(text)
        if not match:
            continue
        try:
            return datetime(int(match.group(1)), int(match.group(2)),
                            int(match.group(3))).strftime("%Y%m%d")
        except ValueError:
            return ""
    return ""


def split_date_range(value: str):
    """解析单日或 `开始~结束`（紧凑/带分隔符均可）→ (YYYYMMDD, YYYYMMDD)。

    空值或非法值返回 ("", "")；结束早于开始时原样返回两端，由调用方决定是否报错。
    """
    text = str(value or "").replace("～", DATE_RANGE_SEP).strip()
    if not text:
        return "", ""
    parts = [part.strip() for part in text.split(DATE_RANGE_SEP)]
    if len(parts) == 1:
        start = _normalize_date(parts[0])
        return (start, start) if start else ("", "")
    if len(parts) != 2:
        return "", ""
    start, end = _normalize_date(parts[0]), _normalize_date(parts[1])
    if not start or not end:
        return "", ""
    return start, end


def format_date_range(start: str, end: str = "", dashed: bool = False) -> str:
    """开始/结束日期 → `YYYYMMDD` 或 `YYYYMMDD~YYYYMMDD`；同一天只保留一个日期。"""
    first = _normalize_date(start)
    last = _normalize_date(end) if end else first
    if not first or not last or last <= first:
        return _dash_date(first) if dashed and first else first
    if dashed:
        return f"{_dash_date(first)}~{_dash_date(last)}"
    return f"{first}~{last}"


def _dash_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else value


def date_folder_value(name: str) -> str:
    """取文件夹名里的起始 YYYYMMDD；不是日期文件夹返回空串。"""
    m = match_date_folder(name)
    return m.group(1) if m else ""


def date_folder_range(name: str):
    """日期文件夹 → (开始, 结束)；跨天文件夹识别 `~YYYYMMDD`，单日两端相同。"""
    start = date_folder_value(name)
    if not start:
        return "", ""
    match = re.match(r"~(20\d{6})", str(name or "")[8:])
    return start, match.group(1) if match else start


def date_folder_end(name: str) -> str:
    """日期文件夹的结束 YYYYMMDD；不是日期文件夹返回空串。"""
    return date_folder_range(name)[1]


def folder_date_label(value: str) -> str:
    """YYYYMMDD → YYYY-MM-DD；长度不符原样返回。"""
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def is_date_folder(name: str, exact: bool = False) -> bool:
    """是否为日期文件夹；exact=True 时只认纯 YYYYMMDD（不允许后缀）。"""
    value = name or ""
    if exact:
        return len(value) == 8 and value.isdigit() and match_date_folder(value) is not None
    return match_date_folder(value) is not None


def scan_date_folders(root: str, skip_subfolders: Iterable[str] | None = None
                      ) -> List[Tuple[str, str]]:
    """扫描 root 下的日期文件夹，返回 [(文件夹名, YYYY-MM-DD)]，按名称升序。

    skip_subfolders 为 None 时读取重命名配置的跳过目录；显式传集合则只按它过滤。
    """
    if not os.path.isdir(root):
        return []
    if skip_subfolders is None:
        try:
            from .config_manager import get_rename_config
            skip_subfolders = get_rename_config().skip_subfolders
        except Exception:
            skip_subfolders = ()
    skip = set(skip_subfolders or ())
    result: List[Tuple[str, str]] = []
    try:
        names = os.listdir(root)
    except OSError:
        return result
    for name in names:
        if name in skip:
            continue
        full_path = os.path.join(root, name)
        if not os.path.isdir(full_path):
            continue
        value = date_folder_value(name)
        if value:
            result.append((name, folder_date_label(value)))
    result.sort(key=lambda item: item[0])
    return result


def has_date_subfolders(directory: str) -> bool:
    """目录下是否存在日期格式的子文件夹。"""
    if not os.path.isdir(directory):
        return False
    try:
        names = os.listdir(directory)
    except OSError:
        return False
    return any(is_date_folder(name)
               and os.path.isdir(os.path.join(directory, name))
               for name in names)


def count_date_subfolders(directory: str) -> int:
    """统计目录下日期格式的子文件夹数量。"""
    if not os.path.isdir(directory):
        return 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    return sum(1 for name in names
               if is_date_folder(name)
               and os.path.isdir(os.path.join(directory, name)))


def today_str() -> str:
    """当天日期（本地时区）YYYYMMDD。"""
    return datetime.now().strftime("%Y%m%d")
