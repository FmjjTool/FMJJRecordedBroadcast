"""WebUI 媒体筛选（只读）：扫描源文件夹并列出图片 / 视频，支持图片预览。

只读：不移动、不复制、不删除、不改名、不写缓存；这些操作仍在桌面端。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from image_sorter import settings as is_settings
from image_sorter.constants import (SUPPORTED_IMAGE_FORMATS,
                                    SUPPORTED_VIDEO_FORMATS)

_MAX_SCAN_FILES = 20000
_MAX_PREVIEW = 20 * 1024 * 1024


def _folders() -> list[Path]:
    try:
        raw = is_settings.get_list("source_folders")
    except Exception:
        raw = []
    folders = []
    for item in raw:
        path = Path(str(item))
        if path.is_dir():
            folders.append(path)
    return folders


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def overview() -> dict:
    folders = _folders()
    return {
        "folders": [{"index": index, "path": str(path)}
                    for index, path in enumerate(folders)],
        "note": "只读扫描与预览；移动 / 复制 / 删除 / 改名与去重仍在桌面端。",
    }


def scan(kind: str = "all", keyword: str = "", limit: int = 500) -> dict:
    kind = str(kind or "all")
    if kind not in ("all", "image", "video"):
        raise ValueError("未知类型")
    keyword = str(keyword or "").strip().lower()
    limit = max(1, min(2000, int(limit)))
    folders = _folders()
    items = []
    total_files = 0
    total_images = 0
    total_videos = 0
    total_bytes = 0
    truncated = False
    scanned = 0
    for index, folder in enumerate(folders):
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith("."))
            for name in sorted(filenames):
                suffix = Path(name).suffix.lower()
                if suffix in SUPPORTED_IMAGE_FORMATS:
                    file_kind = "image"
                elif suffix in SUPPORTED_VIDEO_FORMATS:
                    file_kind = "video"
                else:
                    continue
                scanned += 1
                if scanned > _MAX_SCAN_FILES:
                    truncated = True
                    break
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total_files += 1
                total_bytes += int(stat.st_size)
                if file_kind == "image":
                    total_images += 1
                else:
                    total_videos += 1
                if kind != "all" and file_kind != kind:
                    continue
                if keyword and keyword not in name.lower():
                    continue
                if len(items) >= limit:
                    truncated = True
                    continue
                try:
                    rel = path.relative_to(folder).as_posix()
                except ValueError:
                    rel = path.name
                items.append({
                    "ref": f"{index}:{rel}",
                    "name": name,
                    "folder": folder.name,
                    "kind": file_kind,
                    "is_image": file_kind == "image",
                    "size": int(stat.st_size),
                    "mtime": _ts(stat.st_mtime),
                })
            if truncated and scanned > _MAX_SCAN_FILES:
                break
        if truncated and scanned > _MAX_SCAN_FILES:
            break
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "kind": kind,
        "keyword": keyword,
        "items": items,
        "shown": len(items),
        "total_files": total_files,
        "images": total_images,
        "videos": total_videos,
        "bytes": total_bytes,
        "truncated": truncated,
        "limit": limit,
        "note": "只读扫描；移动 / 复制 / 删除 / 改名与去重仍在桌面端。",
    }


def resolve(ref: str, *, preview: bool = False) -> Path:
    ref = str(ref or "").strip()
    if ":" not in ref:
        raise ValueError("文件标识不正确")
    index_text, rel = ref.split(":", 1)
    try:
        index = int(index_text)
    except ValueError as exc:
        raise ValueError("文件标识不正确") from exc
    folders = _folders()
    if index < 0 or index >= len(folders):
        raise ValueError("源文件夹不存在")
    folder = folders[index].resolve()
    target = (folder / rel).resolve()
    try:
        target.relative_to(folder)
    except ValueError as exc:
        raise ValueError("文件不在源文件夹内") from exc
    if not target.is_file():
        raise ValueError("文件不存在")
    if target.suffix.lower() not in SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS:
        raise ValueError("不支持的文件类型")
    if preview:
        if target.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError("只支持预览图片")
        try:
            if target.stat().st_size > _MAX_PREVIEW:
                raise ValueError("图片超过 20MB，请到桌面端查看")
        except OSError as exc:
            raise ValueError("文件读取失败") from exc
    return target
