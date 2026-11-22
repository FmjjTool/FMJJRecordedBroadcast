"""重命名引擎 - 文件批量重命名核心逻辑"""
import json
import os
import re
from pathlib import Path

from .date_folders import scan_date_folders  # noqa: F401  兼容旧导入路径
from .organize_engine import companion_candidates
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


# ==================== 数据类 ====================






# ==================== 时间提取 ====================

def is_standard_recording_name(filename: str) -> bool:
    """交互重命名生成的日期/时间前缀；投稿统一检查同一规则。"""
    from datetime import datetime
    match = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_.+", os.path.basename(filename))
    if not match:
        return False
    try:
        datetime.strptime(match.group(1) + " " + match.group(2), "%Y-%m-%d %H-%M-%S")
        return True
    except ValueError:
        return False

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

# scan_date_folders 由 toolbox.date_folders 提供（skip_subfolders 读重命名配置）。

# ==================== 附属文件同步改名 ====================

_TIMELINE_SUFFIX = "_时间轴.json"


def _unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = os.path.splitext(path.name)
    number = 1
    while True:
        candidate = path.with_name(f"{stem}_{number}{suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def _sidecar_tail(name: str, old_stem: str, old_ext: str, new_ext: str) -> str:
    """附属文件在新主干的尾部；`.ts.ffmpeg.log` 的扩展名跟随视频改成 `.mp4`。"""
    tail = name[len(old_stem):]
    if old_ext != new_ext and tail.lower().startswith(old_ext + ".ffmpeg.log"):
        tail = new_ext + tail[len(old_ext):]
    return tail


def _update_timeline(timeline_path, old_video, new_video) -> bool:
    """把时间轴里指向旧视频的路径改到新视频，避免重命名后弹幕对齐失效。"""
    path = Path(timeline_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    old_abs = os.path.normcase(os.path.abspath(str(old_video)))
    old_dir = os.path.normcase(os.path.dirname(os.path.abspath(str(old_video))))
    old_stem = Path(old_video).stem
    new_abs = os.path.abspath(str(new_video))
    changed = False
    for entry in data.get("files") or []:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("file") or "")
        if not raw:
            continue
        same = os.path.normcase(os.path.abspath(raw)) == old_abs
        if not same:
            raw_path = Path(raw)
            same = (os.path.normcase(os.path.abspath(raw_path.parent)) == old_dir
                    and raw_path.stem == old_stem)
        if same:
            entry["file"] = new_abs
            changed = True
    if data.get("base") == old_stem:
        data["base"] = Path(new_video).stem
        changed = True
    if data.get("merged") and os.path.normcase(
            os.path.abspath(str(data.get("target") or ""))) == old_abs:
        data["target"] = new_abs
        changed = True
    if changed:
        temporary = str(path) + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
        os.replace(temporary, path)
    return changed


def rebase_companions(src_video, dst_video):
    """视频改名后同步改名弹幕 / 封面 / 时间轴 / ffmpeg 日志，并修正时间轴路径。

    返回 (已改名列表, 跳过原因列表)；单个附属文件失败不影响视频改名结果。
    """
    src = Path(src_video)
    dst = Path(dst_video)
    old_stem, new_stem = src.stem, dst.stem
    old_ext, new_ext = src.suffix.lower(), dst.suffix.lower()
    if old_stem == new_stem and old_ext == new_ext:
        return [], []
    moved, skipped = [], []
    for item in companion_candidates(src):
        if not item.name.startswith(old_stem):
            continue
        tail = _sidecar_tail(item.name, old_stem, old_ext, new_ext)
        if re.match(r"_\d{3}(?:_|\.|$)", tail):
            continue  # 分段自己的附属文件，等分段改名时处理
        target = item.with_name(new_stem + tail)
        if os.path.normcase(str(target)) == os.path.normcase(str(item)):
            continue
        target = _unique_target(target)
        try:
            os.rename(item, target)
        except OSError as error:
            skipped.append(f"{item.name}：{error}")
            continue
        moved.append(str(target))
        if tail.lower().endswith(_TIMELINE_SUFFIX.lower()):
            try:
                _update_timeline(target, src, dst)
            except OSError as error:
                skipped.append(f"{target.name}（时间轴路径未更新）：{error}")
    return moved, skipped


# ==================== 缓存操作 ====================









