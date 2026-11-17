"""资产条目关联文件枚举（L3，只读，不依赖 Qt）。"""

from __future__ import annotations

import os


def collect_asset_files(item: dict) -> dict:
    """返回条目关联的 docs / images / videos（仅保留实际存在的文件）。"""
    item = item or {}
    file_path = item.get("file_path", "")
    stem = os.path.splitext(file_path)[0]

    docs = [file_path] if file_path.lower().endswith((".json", ".md", ".xlsx")) else []
    for ext in (".md", ".xlsx", "_raw.json", "_comments.json", "_comments.txt"):
        candidate = stem + ext
        if os.path.exists(candidate):
            docs.append(candidate)

    media = item.get("media_files") or []
    images = [m.get("path") for m in media if m.get("type") == "image"]
    videos = [m.get("path") for m in media if m.get("type") == "video"]

    def existing(paths):
        return [p for p in paths if p and os.path.exists(p)]

    return {"docs": existing(docs), "images": existing(images),
            "videos": existing(videos)}
