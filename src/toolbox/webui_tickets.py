"""WebUI 票务解析（只读）：最近一次解析缓存与导出目录。

解析、拉详情、导出与图片下载仍在桌面端。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import get_export_dir


def _base() -> Path:
    return Path(get_export_dir()) / "票务演出"


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _count_images(folder: Path) -> int:
    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    count = 0
    try:
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                count += 1
    except OSError:
        pass
    return count


def overview() -> dict:
    base = _base()
    payload = {}
    if (base / "_latest.json").is_file():
        try:
            payload = json.loads(
                (base / "_latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
    events = []
    for item in payload.get("events") or []:
        if not isinstance(item, dict):
            continue
        events.append({
            "title": item.get("title") or "-",
            "platform": item.get("platform_cn") or item.get("platform") or "-",
            "city": item.get("city") or "-",
            "venue": item.get("venue") or "-",
            "show_time": item.get("show_time") or "-",
            "price": item.get("price_range") or "-",
            "status": item.get("status") or "-",
            "url": item.get("url") or "",
        })
    folders = []
    if base.is_dir():
        for folder in sorted(base.iterdir(), key=lambda item: item.name):
            if not folder.is_dir():
                continue
            try:
                stat = folder.stat()
            except OSError:
                continue
            folders.append({
                "name": folder.name,
                "images": _count_images(folder),
                "mtime": _ts(stat.st_mtime),
            })
    folders.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "saved_at": str(payload.get("saved_at") or ""),
        "events": events,
        "total": len(events),
        "folders": folders[:200],
        "folder_total": len(folders),
        "base": str(base),
        "note": "只读：解析、拉详情、导出 JSON 与图片下载仍在桌面端。",
    }
