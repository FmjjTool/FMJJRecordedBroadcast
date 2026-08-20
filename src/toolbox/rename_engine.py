"""重命名引擎 - 文件批量重命名核心逻辑"""
import os
import re
from typing import List, Tuple

from .config_manager import get_rename_config
from .utils import clean_special_chars


# ==================== 正则模式 ====================

TIME_HHMMSS_PATTERN = re.compile(r"(?<![\d-])(\d{2}-\d{2}-\d{2})(?!\d)", re.I)
STANDARD_DT_PATTERN = re.compile(r"20\d{2}-\d{2}-\d{2}[_\-](\d{2})-(\d{2})-(\d{2})")
TIMESTAMP_14_PATTERN = re.compile(r"20\d{2}(0[1-9]|1[0-2])([0-2]\d|3[01])(\d{2})(\d{2})(\d{2})")
REC_TIME_PATTERN = re.compile(r"20\d{6}-(\d{6})")
BV_PATTERN = re.compile(r"BV[a-zA-Z0-9]{8,12}", re.I)
REC_PREFIX_PATTERN = re.compile(r"录制-\d+", re.I)
DATE_STAMP_PATTERN = re.compile(r"20\d{6}-\d{6}|20\d{12}")
DATE_DASH_PATTERN = re.compile(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}")
TIME_CLEAN_PATTERN = re.compile(r"(?<!20)\b\d{2}-\d{2}-\d{2}\b")
PURE_DIGIT6_PATTERN = re.compile(r"\d{6}")
DATE_FOLDER_PATTERN = re.compile(r"^(20\d{6})")


# ==================== 数据类 ====================






# ==================== 时间提取 ====================

def extract_time_str(file_name: str) -> str:
    """从文件名中提取时间字符串"""
    fname = str(file_name).lower()
    # 标准命名 2026-08-19_20-15-30_标题（_ 或 - 分隔日期与时间）
    m = STANDARD_DT_PATTERN.search(fname)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = TIME_HHMMSS_PATTERN.search(fname)
    if m:
        return m.group(1)
    m = TIMESTAMP_14_PATTERN.search(fname)
    if m:
        return m.group(3) + "-" + m.group(4) + "-" + m.group(5)
    m = REC_TIME_PATTERN.search(fname)
    if m:
        hhmmss = m.group(1)
        return hhmmss[0:2] + "-" + hhmmss[2:4] + "-" + hhmmss[4:6]
    return "00-00-00"








def extract_file_title(file_name: str) -> str:
    """提取文件标题"""
    if not file_name:
        return "无标题"
    t = os.path.splitext(str(file_name))[0]
    t = REC_PREFIX_PATTERN.sub("", t)
    t = DATE_STAMP_PATTERN.sub("", t)
    t = re.sub(r"^\d+|-(\d+)-", "", t)
    t = re.sub(r"[-._]", "", t)
    t = DATE_DASH_PATTERN.sub("", t)
    t = TIME_CLEAN_PATTERN.sub("", t)
    t = PURE_DIGIT6_PATTERN.sub("", t)
    t = clean_special_chars(t)
    return t if t else "无标题"








# ==================== 扫描文件夹 ====================

def scan_date_folders(root: str) -> List[Tuple[str, str]]:
    """扫描日期文件夹"""
    cfg = get_rename_config()
    result = []
    if not os.path.isdir(root):
        return result
    try:
        for name in os.listdir(root):
            full_path = os.path.join(root, name)
            if not os.path.isdir(full_path):
                continue
            if name in cfg.skip_subfolders:
                continue
            m = DATE_FOLDER_PATTERN.match(name)
            if m:
                dt_str = m.group(1)
                dt = dt_str[:4] + "-" + dt_str[4:6] + "-" + dt_str[6:8]
                result.append((name, dt))
    except Exception:
        pass
    result.sort(key=lambda x: x[0])
    return result


# ==================== 缓存操作 ====================









