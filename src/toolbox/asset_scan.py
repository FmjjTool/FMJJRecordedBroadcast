"""资产目录扫描与文件类型分类（L3，只读，不依赖 Qt）。"""

from __future__ import annotations

import os
from datetime import datetime

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".flv", ".avi", ".ts", ".m4v", ".wmv"}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".opus"}

SCAN_SKIP_DIRS = {"__pycache__", ".git", ".svn", ".idea", ".vscode",
                  "node_modules", "$recycle.bin", "system volume information"}


def classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


def scan_assets(folder: str, limit: int = 0, depth: int = 0) -> list:
    """扫描文件夹里的图片/视频/音频，返回文件信息列表。limit=0 表示无上限。

    depth: 0=只扫当前层；N>0=向下递归 N 层；负数=不限深度。
    输出顺序：先本层文件，再逐个进入子目录递归。
    """
    results = []

    def walk(path, level, rel):
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            return False
        subdirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in SCAN_SKIP_DIRS and (depth < 0 or level < depth):
                        subdirs.append(e)
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                kind = classify(e.name)
                if not kind:
                    continue
                st = e.stat()
            except OSError:
                continue
            results.append({
                "path": e.path, "name": e.name, "size": st.st_size,
                "type": kind, "rel_dir": rel,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
            if limit and len(results) >= limit:
                return True
        for e in subdirs:
            if walk(e.path, level + 1, f"{rel}/{e.name}" if rel else e.name):
                return True
        return False

    try:
        walk(folder, 0, "")
    except Exception:
        pass
    return results
