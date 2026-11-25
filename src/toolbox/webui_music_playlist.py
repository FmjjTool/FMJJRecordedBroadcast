"""WebUI 歌单（只读）：本地预览 / 执行报告。

创建与写入平台歌单需要账号授权，仍在桌面端。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_MAX_TEXT = 512 * 1024


def _service():
    from music import playlist_service
    return playlist_service


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _report_dir() -> Path:
    return Path(_service().report_dir())


def overview() -> dict:
    root = _report_dir()
    reports = []
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            name = path.name
            if not (name.startswith("preview_")
                    or name.startswith("journal_")
                    or name.startswith("result_")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if name.startswith("preview_"):
                kind = "预览"
            elif name.startswith("journal_"):
                kind = "执行日志"
            else:
                kind = "执行结果"
            reports.append({
                "name": name,
                "kind": kind,
                "size": int(stat.st_size),
                "mtime": _ts(stat.st_mtime),
            })
    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "report_dir": str(root),
        "reports": reports,
        "total": len(reports),
        "note": "只读：生成预览、创建歌单与写入平台仍在桌面端（需要账号授权）。",
    }


def report_text(name: str) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("缺少报告文件名")
    root = _report_dir().resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("文件不在报告目录内") from exc
    if not target.is_file() or target.suffix.lower() != ".json":
        raise ValueError("报告文件不存在")
    try:
        if target.stat().st_size > _MAX_TEXT:
            raise ValueError("报告超过 512KB，请到桌面端查看")
        data = json.loads(target.read_text(encoding="utf-8"))
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except (OSError, ValueError) as exc:
        raise ValueError("报告文件读取失败") from exc
    return {"name": target.name, "text": text}
