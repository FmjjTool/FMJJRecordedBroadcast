"""WebUI 标签（只读）：音乐目录文件与标签快照、标签历史。

读取标签不写文件；修改 / 批量写入 / 历史恢复仍在桌面端。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import get_config_dir, get_music_download_dir

_AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".ape", ".wv",
               ".aac", ".opus", ".wma"}
_MAX_FILES = 300
_MAX_HISTORY = 60


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _history() -> list[dict]:
    path = Path(get_config_dir()) / "music_tag_history.db"
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                               timeout=3)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT createtime, filepath, title, artist, album FROM tag_history"
            " ORDER BY serial DESC LIMIT ?", (_MAX_HISTORY,)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    result = []
    for row in rows:
        result.append({
            "time": _ts(row["createtime"]),
            "file": os.path.basename(str(row["filepath"] or "")),
            "title": row["title"] or "",
            "artist": row["artist"] or "",
            "album": row["album"] or "",
        })
    return result


def overview(limit: int = 120) -> dict:
    limit = max(1, min(_MAX_FILES, int(limit)))
    root = Path(get_music_download_dir())
    from music.tags_io import load_tags

    files = []
    truncated = False
    if root.is_dir():
        scanned = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in _AUDIO_EXTS:
                    continue
                scanned.append(Path(dirpath) / name)
        for path in scanned[:limit]:
            try:
                stat = path.stat()
                rel = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            try:
                tags = load_tags(str(path)) or {}
            except Exception:
                tags = {}
            files.append({
                "name": path.name,
                "rel": rel,
                "size": int(stat.st_size),
                "mtime": _ts(stat.st_mtime),
                "title": str(tags.get("title") or ""),
                "artist": str(tags.get("artist") or ""),
                "album": str(tags.get("album") or ""),
                "year": str(tags.get("year") or ""),
                "genre": str(tags.get("genre") or ""),
            })
        truncated = len(scanned) > limit
    files.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "root": str(root),
        "files": files,
        "total": len(files),
        "truncated": truncated,
        "history": _history(),
        "note": "只读：修改 / 批量写入标签、歌词抓取与历史恢复仍在桌面端。",
    }
