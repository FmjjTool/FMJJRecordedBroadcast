"""资产目录扫描与文件类型分类（L3，只读，不依赖 Qt）。"""

from __future__ import annotations

import json
import os
from datetime import datetime

from toolbox.config_manager import get_export_subdir

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


def scan_cache_path():
    """本地扫描增量缓存路径（非「已获取资源」索引）。"""
    return get_export_subdir("video_data") / "_assets_index.json"


def append_scan_cache(files: list) -> int:
    """追加本地扫描结果到 JSON 缓存，按 (path, name) 去重。"""
    path = scan_cache_path()
    existing = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = data if isinstance(data, list) else []
        except Exception:
            existing = []
    seen = {(e.get("path"), e.get("name")) for e in existing if isinstance(e, dict)}
    for item in files:
        if not isinstance(item, dict):
            continue
        key = (item.get("path"), item.get("name"))
        if key not in seen:
            seen.add(key)
            existing.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(existing)


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
