"""视频元数据写入（版权）：ffmpeg 重封装写标准标签 + 读回校验。

为什么单独一层：
- 交互重命名页要「写入版权」，但页面不该自己拼 ffmpeg 命令（分层约束 L2 不承载处理逻辑）。
- Windows 资源管理器**不显示 MP4 的「版权」属性**（实测：`copyright` 写成 MP4 的
  `cprt` 标签后，资源管理器 详细信息 → 版权 仍为空；标题/备注/作者/专辑都能显示）。
  因此除标准 `copyright` 外，mp4/mov/m4v 同时写入「备注」(`comment`)，
  资源管理器可以在「备注」里看到；标准版权仍保留给 MediaInfo / 剪辑软件读取。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

_MP4_SUFFIXES = {".mp4", ".mov", ".m4v"}
COMMENT_PREFIX = "版权："


def _load_ffmpeg() -> str:
    from toolbox.utils import find_ffmpeg_exe
    return find_ffmpeg_exe()


def read_tags(path) -> dict:
    """读 MP4 家族标签（mutagen）；读不到返回空 dict。"""
    try:
        from mutagen.mp4 import MP4
        return dict(MP4(str(path)).tags or {})
    except Exception:  # noqa: BLE001 - 不是 mp4 家族或没有 mutagen
        return {}


def read_copyright(path) -> str:
    tags = read_tags(path)
    for key in ("cprt", "\xa9cpy"):
        value = tags.get(key)
        if value:
            return str(value[0])
    return ""


def build_command(ffmpeg: str, source: Path, target: Path, text: str) -> list[str]:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(source), "-map", "0", "-c", "copy",
           "-metadata", f"copyright={text}"]
    if source.suffix.lower() in _MP4_SUFFIXES:
        # Windows 属性不显示 MP4 版权，额外写一份到「备注」
        cmd += ["-metadata", f"comment={COMMENT_PREFIX}{text}",
                "-movflags", "+faststart"]
    cmd.append(str(target))
    return cmd


def write_copyright(filepath, text: str) -> tuple[bool, str]:
    """给视频写入版权；返回 (是否成功, 给用户看的说明)。

    原视频只在全部校验通过后才被替换；任何失败都保留原文件。
    """
    text = str(text or "").strip()
    if not text:
        return True, ""
    path = Path(filepath)
    if not path.is_file():
        return False, "视频文件不存在"
    try:
        ffmpeg = _load_ffmpeg()
    except Exception:  # noqa: BLE001
        ffmpeg = ""
    if not ffmpeg:
        return False, "未找到 ffmpeg，无法写入版权"

    from toolbox.organize_engine import _locked_reader

    try:
        with tempfile.TemporaryDirectory(prefix=".fmjj-meta-", dir=path.parent) as directory:
            temporary = Path(directory) / ("video" + path.suffix)
            with _locked_reader(path):
                before = path.stat()
                result = subprocess.run(
                    build_command(ffmpeg, path, temporary, text),
                    capture_output=True, timeout=180)
                if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
                    return False, "FFmpeg 元数据写入失败，原视频已保留"
                if path.suffix.lower() in _MP4_SUFFIXES:
                    written = read_copyright(temporary)
                    if written != text:
                        return False, "版权读回校验不一致，原视频已保留"
                with open(temporary, "r+b") as stream:
                    os.fsync(stream.fileno())
                os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
            current = path.stat()
            if (current.st_size, current.st_mtime_ns, current.st_ino) != (
                    before.st_size, before.st_mtime_ns, before.st_ino):
                return False, "元数据处理期间原视频变化，原视频已保留"
            os.replace(temporary, path)
    except Exception as error:  # noqa: BLE001
        return False, f"元数据处理失败（{type(error).__name__}: {error}），原视频已保留"

    if path.suffix.lower() in _MP4_SUFFIXES:
        return True, ("版权已写入；资源管理器不显示 MP4 版权，已同时写入「备注」，"
                      "标准版权可用 MediaInfo / 剪辑软件查看")
    return True, "版权信息已写入"