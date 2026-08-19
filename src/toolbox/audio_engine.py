"""音频提取引擎"""
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from .utils import is_video_file


AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".m4b", ".opus", ".amr"}
DATE_FOLDER_RE = re.compile(r"^20\d{6}$")


def extract_audio_one(src: Path, dst: Path) -> Tuple[bool, str]:
    """提取单个视频的音频"""
    cmd = [
        "ffmpeg", "-i", str(src),
        "-vn",
        "-acodec", "copy",
        "-y",
        "-hide_banner",
        str(dst),
    ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate(timeout=300)
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return True, ""
        stderr_str = stderr.decode("utf-8", errors="ignore")
        return False, stderr_str[-300:] if len(stderr_str) > 300 else stderr_str
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return False, "提取超时"
    except FileNotFoundError:
        return False, "ffmpeg 未找到"
    except Exception as e:
        return False, str(e)


def find_audio_videos(root: Path) -> List[Path]:
    """查找所有视频文件用于音频提取"""
    videos = []
    for path in root.rglob("*"):
        if path.is_file() and is_video_file(str(path)):
            videos.append(path)
    return sorted(videos)






def scan_audio_by_date(root: Path) -> Dict[str, List[Path]]:
    """按日期文件夹分组扫描音频文件。

    返回 {日期(YYYYMMDD) 或 "根目录": [Path, ...]}，按日期升序。
    """
    root = Path(root)
    groups: Dict[str, List[Path]] = {}
    if not root.is_dir():
        return groups
    # 工作目录根下直接摆放的音频
    root_audios = sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    if root_audios:
        groups["根目录"] = root_audios
    # 各日期文件夹（YYYYMMDD）内的音频
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not DATE_FOLDER_RE.match(d.name):
            continue
        audios = sorted(
            p for p in d.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        )
        if audios:
            groups[d.name] = audios
    return groups


def delete_files_safe(paths: List[Path]) -> Tuple[int, List[str]]:
    """安全删除文件：优先移入系统回收站（send2trash），失败则记录错误。

    返回 (成功删除数, 失败描述列表)。若 send2trash 不可用则退化为永久删除。

    注意：send2trash 在 Windows 某些路径（如系统盘 Temp、含 8.3 短路径名）
    下会误报"系统找不到指定的文件"，但文件实际已移入回收站——因此以
    原路径是否还存在作为最终判定标准。
    """
    try:
        import send2trash
        has_trash = True
    except Exception:
        has_trash = False
    ok = 0
    failed: List[str] = []
    for p in paths:
        try:
            if has_trash:
                send2trash.send2trash(str(p))
            else:
                os.remove(p)
            ok += 1
        except Exception as e:
            if not os.path.exists(p):
                # 误报：文件实际已被删除
                ok += 1
            else:
                failed.append(f"{p.name}: {e}")
    return ok, failed
