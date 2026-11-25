"""WebUI 音乐下载（只读）：下载目录里的本地音频文件。

搜索、下载与解密仍在桌面端。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import get_music_download_dir

_AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".ape", ".wv",
               ".aac", ".opus", ".wma"}
_MAX_FILES = 3000


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def overview() -> dict:
    root = Path(get_music_download_dir())
    files = []
    total_bytes = 0
    by_ext: dict[str, int] = {}
    truncated = False
    scanned = 0
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                suffix = Path(name).suffix.lower()
                if suffix not in _AUDIO_EXTS:
                    continue
                scanned += 1
                if scanned > _MAX_FILES:
                    truncated = True
                    break
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                    rel = path.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                total_bytes += int(stat.st_size)
                by_ext[suffix] = by_ext.get(suffix, 0) + 1
                files.append({
                    "name": name,
                    "rel": rel,
                    "ext": suffix.lstrip("."),
                    "size": int(stat.st_size),
                    "mtime": _ts(stat.st_mtime),
                })
            if truncated:
                break
    files.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "root": str(root),
        "files": files,
        "total": len(files),
        "bytes": total_bytes,
        "by_ext": [{"ext": key.lstrip("."), "count": count}
                   for key, count in sorted(by_ext.items())],
        "truncated": truncated,
        "note": "只读：搜索、下载、解密与导出仍在桌面端。",
    }
