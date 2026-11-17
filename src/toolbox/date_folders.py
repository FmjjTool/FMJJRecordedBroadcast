"""日期文件夹（工作目录/YYYYMMDD）的统一识别与扫描。

正则、日期取值、当天字符串、排序扫描都从这里出，页面与引擎不再各写一份。
"""

import os
import re
from datetime import datetime
from typing import Iterable, List, Tuple

# 日期文件夹以 8 位日期开头；`20260923_备份` 这类带后缀的仍归入同一天。
DATE_FOLDER_RE = re.compile(r"^(20\d{6})")


def match_date_folder(name: str):
    """返回日期文件夹的 re.Match（前缀 20XXXXXX）；不是日期文件夹返回 None。"""
    return DATE_FOLDER_RE.match(name or "")


def date_folder_value(name: str) -> str:
    """取文件夹名里的 YYYYMMDD；不是日期文件夹返回空串。"""
    m = match_date_folder(name)
    return m.group(1) if m else ""


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
