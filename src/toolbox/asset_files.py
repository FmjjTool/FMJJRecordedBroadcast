"""资产条目关联文件枚举（L3，只读，不依赖 Qt）。"""

from __future__ import annotations

import os

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic"}


def merge_asset_media(item: dict, new_paths=None) -> list[dict]:
    """只核对条目已有关联和下载回执路径，不枚举资源目录。"""
    media = []
    known = set()
    for record in item.get("media_files") or []:
        path = record.get("path") or ""
        key = os.path.normcase(os.path.abspath(path)) if path else ""
        if key and key not in known and os.path.isfile(path):
            media.append(dict(record))
            known.add(key)
    for path, _kind in new_paths or []:
        key = os.path.normcase(os.path.abspath(path)) if path else ""
        if not key or key in known or not os.path.isfile(path):
            continue
        media.append({
            "path": path, "name": os.path.basename(path),
            "type": "image" if os.path.splitext(path)[1].lower() in _IMAGE_EXTS else "video",
            "date_part": item.get("date_part", ""),
            "author": item.get("author", ""), "subdir": item.get("platform", ""),
        })
        known.add(key)
    return media


def entry_after_file_deletion(item: dict, deleted_paths: set[str]) -> dict | None:
    """按实际删除回执更新一个条目；文本消失但媒体尚在时保留媒体条目。"""
    result = dict(item)
    result["media_files"] = merge_asset_media(item)
    source = str(item.get("file_path") or "")
    source_deleted = os.path.normcase(os.path.abspath(source)) in deleted_paths if source else False
    if not source_deleted:
        if source and not source.lower().endswith(".json"):
            result["expected_imgs"] = sum(m["type"] == "image" for m in result["media_files"])
        return result
    media = result["media_files"]
    if not media:
        return None
    result.update(file_path=media[0]["path"], raw_data={}, description="",
                  expected_imgs=sum(m["type"] == "image" for m in media),
                  cover_url=next((m["path"] for m in media if m["type"] == "image"), ""))
    for key in ("video_id", "is_dynamic", "_bili_bvid"):
        result.pop(key, None)
    return result


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
