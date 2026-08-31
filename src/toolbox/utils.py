"""工具函数 - 公共工具函数"""
import os
import re
import sys
import json
import subprocess
import ctypes
from pathlib import Path
from typing import Tuple, Optional
from datetime import date

if sys.platform == "win32":
    from ctypes import wintypes


# ==================== 平台目录命名 ====================

# 平台英文标识 -> 中文目录名（导出/媒体目录统一用它命名）
PLATFORM_CN = {
    "bilibili": "B站",
    "douyin": "抖音",
    "weibo": "微博",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "haokan": "好看视频",
    "weishi": "微视",
    "pear": "梨视频",
    "pipigx": "皮皮搞笑",
}

# 已获取数据目录后缀：{平台}_文件 存数据导出（json/md/xlsx），{平台}_媒体 存媒体（视频/图片/音频）
DATA_DIR_SUFFIX = "_文件"
MEDIA_DIR_SUFFIX = "_媒体"


def platform_cn(platform: str) -> str:
    """平台英文标识 -> 中文目录名（未收录的返回原名）。"""
    return PLATFORM_CN.get((platform or "").lower(), platform or "unknown")


# 平台目录名别名 -> 英文标识。由 PLATFORM_CN 全量派生（中/英目录名都收录），
# 新增平台只需登记 PLATFORM_CN 一处；另含 B站 的俗称写法。
_PLATFORM_ALIASES = {"b站": "bilibili", "哔哩哔哩": "bilibili"}
_PLATFORM_ALIASES.update({en: en for en in PLATFORM_CN})
_PLATFORM_ALIASES.update({cn: en for en, cn in PLATFORM_CN.items()})


def norm_platform_dir(name: str) -> str:
    """平台目录名（新旧结构均可）-> 规范英文 platform 标识，未识别返回 ""。

    兼容：`抖音`/`douyin`（旧结构）与 `抖音_文件`/`抖音_媒体`/`douyin_文件`（新结构）。
    别名表覆盖 PLATFORM_CN 全部 9 个平台（含好看视频/微视/梨视频/皮皮搞笑）。
    """
    if not name:
        return ""
    s = name.lower()
    for suffix in (MEDIA_DIR_SUFFIX, DATA_DIR_SUFFIX):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    s = s.strip("_")
    return _PLATFORM_ALIASES.get(s, "")


# ==================== 文件大小格式化 ====================

def sanitize_filename(name: str, max_len: int = 60, fallback: str = "untitled") -> str:
    """清理文件名为安全文件名（防路径遍历 + 替换非法字符 + 截断）。

    统一 media_downloader / exporters / bilibili_download 里重复的实现。
    """
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name or "")
    name = re.sub(r'\.\.', '_', name)  # 防路径遍历
    name = re.sub(r'[_\s]+', '_', name).strip('_ ')
    if len(name) > max_len:
        name = name[:max_len].rstrip('_')
    return name or fallback


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# ==================== 时间戳保留 ====================

def preserve_timestamps(src: Path, dst: Path) -> bool:
    """保留文件时间戳"""
    try:
        src_stat = src.stat()
        os.utime(str(dst), (src_stat.st_atime, src_stat.st_mtime))
        if sys.platform == "win32":
            _set_creation_time_windows(str(dst), src_stat.st_ctime)
        return True
    except Exception:
        return False


def _set_creation_time_windows(path: str, creation_time: float):
    """Windows 下设置文件创建时间"""
    try:
        EPOCH_DIFFERENCE = 11644473600
        NSEC_PER_SEC = 10_000_000
        ft = int((creation_time + EPOCH_DIFFERENCE) * NSEC_PER_SEC)
        kernel32 = ctypes.windll.kernel32
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        hfile = kernel32.CreateFileW(
            path, GENERIC_WRITE, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
        )
        if hfile == -1:
            return
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                ]
            ct = FILETIME(ft & 0xFFFFFFFF, (ft >> 32) & 0xFFFFFFFF)
            kernel32.SetFileTime(hfile, ctypes.byref(ct), None, None)
        finally:
            kernel32.CloseHandle(hfile)
    except Exception:
        pass


# ==================== ffmpeg 错误分类 ====================

def classify_ffmpeg_error(stderr: str) -> Tuple[str, str]:
    """分类 ffmpeg 错误信息"""
    err = stderr.lower()
    if not stderr.strip():
        return "未知错误", "ffmpeg 没有输出错误信息，可能是被强制终止"
    if "invalid argument" in err and "muxer" in err:
        return "输出格式错误", "ffmpeg 无法识别输出格式"
    if "no such file or directory" in err:
        return "文件不存在", "输入或输出路径找不到文件"
    if "permission denied" in err:
        return "权限不足", "无法写入输出文件，可能被占用"
    if "invalid data found when processing input" in err:
        return "输入文件损坏", "视频文件数据损坏，可能录制不完整"
    if "could not find codec parameters" in err:
        return "无法识别编码", "ffmpeg 无法识别视频编码格式"
    if "stream map" in err and "matches no streams" in err:
        return "缺少音视频流", "文件中没有找到视频或音频流"
    if "aac_adtstoasc" in err or "bitstream filter" in err:
        return "音频格式不兼容", "音频比特流过滤器失败"
    if "non-monotonous dts" in err or "invalid dts" in err:
        return "时间戳异常", "视频时间戳错乱，常见于直播录屏"
    lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    last_lines = lines[-3:] if len(lines) >= 3 else lines
    return "转换失败", "\n".join("      " + l for l in last_lines)


# ==================== 字符串清理 ====================

FULL2HALF = {ord(c): ord(t) for c, t in zip(
    '，。！？：；""''（）～＿', ',!?:;""''()~_'
)}
ILLEGAL_CHARS_PATTERN = re.compile(r"[\/:*?<>|()\[\]{}【】（）\s]+")
UNDERSCORE_PATTERN = re.compile(r"_+")


def clean_special_chars(s: str) -> str:
    """清理特殊字符"""
    if not s:
        return ""
    s = str(s).translate(FULL2HALF)
    s = ILLEGAL_CHARS_PATTERN.sub("", s)
    s = UNDERSCORE_PATTERN.sub("_", s)
    return s.strip("_-. ")


# ==================== 日期提取 ====================

ORGANIZE_DATE_PATTERN = re.compile(r"(\d{4})(\d{2})(\d{2})")
# 支持带分隔符的日期：2026-06-08 / 2026_06_08 / 2026.06.08
ORGANIZE_DATE_SEP_PATTERN = re.compile(r"(\d{4})[-_./](\d{1,2})[-_./](\d{1,2})")


def _validate_date(y: int, mo: int, d: int) -> Optional[str]:
    """校验日期并返回 YYYYMMDD 字符串"""
    if not (2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    try:
        date(y, mo, d)
        return f"{y}{mo:02d}{d:02d}"
    except ValueError:
        # 处理月末边界（如2月30日 → 2月最后一天）
        if mo == 12:
            next_month = date(y + 1, 1, 1)
        else:
            next_month = date(y, mo + 1, 1)
        last_day = (next_month - date(y, mo, 1)).days
        if d <= last_day:
            return f"{y}{mo:02d}{d:02d}"
        return None


def extract_date_from_filename(filename: str) -> Optional[str]:
    """从文件名中提取日期，支持 YYYYMMDD / YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD"""
    name = os.path.splitext(filename)[0]

    # 优先匹配带分隔符的（更精确）
    for m in ORGANIZE_DATE_SEP_PATTERN.finditer(name):
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            # 冗余过滤：年必须在2000-2099，月1-12，日1-31，排除时间戳误匹配
            if not (2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                continue
            result = _validate_date(y, mo, d)
            if result:
                return result
        except ValueError:
            continue

    # 再匹配连续8位数字
    m = ORGANIZE_DATE_PATTERN.search(name)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return _validate_date(y, mo, d)
        except ValueError:
            pass

    return None


# ==================== 视频信息 ====================

VIDEO_EXTS = {".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".rmvb", ".webm", ".ogg"}


def is_video_file(filepath: str) -> bool:
    """判断是否为视频文件"""
    return os.path.splitext(filepath)[1].lower() in VIDEO_EXTS




# ==================== 视频信息 (ffprobe) ====================

def get_video_info(file_path: str) -> dict:
    """获取视频详细信息（分辨率、时长、编码等）"""
    import subprocess
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            file_path
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate(timeout=30)
        if p.returncode != 0:
            return {"error": "ffprobe 执行失败"}

        data = json.loads(stdout.decode("utf-8", errors="ignore"))

        video_stream = None
        audio_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video" and video_stream is None:
                video_stream = stream
            elif stream.get("codec_type") == "audio" and audio_stream is None:
                audio_stream = stream

        if not video_stream:
            return {"error": "未找到视频流"}

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        duration = float(data.get("format", {}).get("duration", 0))
        bit_rate = int(data.get("format", {}).get("bit_rate", 0))
        codec = video_stream.get("codec_name", "unknown")
        fps_str = video_stream.get("r_frame_rate", "0/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = round(float(num) / float(den), 2) if float(den) != 0 else 0
        else:
            fps = float(fps_str)

        file_size = int(data.get("format", {}).get("size", 0))
        orientation = "竖屏" if height > width else "横屏"

        info = {
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}",
            "duration": duration,
            "duration_str": f"{int(duration // 3600):02d}:{int((duration % 3600) // 60):02d}:{int(duration % 60):02d}",
            "bit_rate": bit_rate,
            "bit_rate_str": f"{bit_rate / 1000:.0f} kbps" if bit_rate else "未知",
            "codec": codec,
            "fps": fps,
            "file_size": file_size,
            "file_size_str": format_size(file_size) if file_size else "未知",
            "orientation": orientation,
            "has_audio": audio_stream is not None,
            "audio_codec": audio_stream.get("codec_name", "") if audio_stream else "",
        }
        return info
    except Exception as e:
        return {"error": str(e)}


# ==================== 视频截图 ====================

def capture_video_frame(file_path: str, output_path: str = None, time_seconds: float = None) -> str:
    """截取视频的一帧画面，返回截图路径"""
    import tempfile
    try:
        if output_path is None:
            output_path = os.path.join(tempfile.gettempdir(), f"thumb_{os.path.basename(file_path)}.jpg")

        # 先获取时长
        info = get_video_info(file_path)
        duration = info.get("duration", 0)

        # 默认截取 10% 位置或 30 秒
        if time_seconds is None:
            time_seconds = min(duration * 0.1, 30) if duration > 0 else 5

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(time_seconds),
            "-i", file_path,
            "-vframes", "1",
            "-q:v", "2",
            "-hide_banner",
            "-loglevel", "error",
            output_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        return ""
    except Exception:
        return ""
