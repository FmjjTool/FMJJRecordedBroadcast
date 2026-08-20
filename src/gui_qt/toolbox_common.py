"""录播工具箱页面共享常量与工具函数（对应原版 gui/toolbox_common.py）。"""

import os
import re

# 日期文件夹正则（YYYYMMDD）
DATE_FOLDER_RE = re.compile(r"^(20\d{6})")


def has_date_subfolders(directory: str) -> bool:
    """检查目录下是否存在日期格式的二级文件夹"""
    if not os.path.isdir(directory):
        return False
    for name in os.listdir(directory):
        full = os.path.join(directory, name)
        if os.path.isdir(full) and DATE_FOLDER_RE.match(name):
            return True
    return False


def count_date_subfolders(directory: str) -> int:
    """统计目录下日期格式的二级文件夹数量"""
    if not os.path.isdir(directory):
        return 0
    count = 0
    for name in os.listdir(directory):
        full = os.path.join(directory, name)
        if os.path.isdir(full) and DATE_FOLDER_RE.match(name):
            count += 1
    return count
