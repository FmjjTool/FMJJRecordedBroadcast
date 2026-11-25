"""WebUI 网络库（只读）：备份映射、统计、最近运行与任务日志。

不触发备份 / 下载，也不浏览云端目录；这些仍在桌面端。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _modules():
    from toolbox.quark import config as qconfig
    from toolbox.quark import job as qjob
    from toolbox.quark import state as qstate
    return qconfig, qstate, qjob


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _tail(path: Path, max_lines: int = 200, max_bytes: int = 65536) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[-max_lines:]


def overview() -> dict:
    qconfig, qstate, qjob = _modules()
    try:
        mappings = qconfig.load_mappings()
    except Exception:
        mappings = []
    rows = []
    for mapping in mappings:
        try:
            stats = qstate.mapping_stats(mapping.id)
        except Exception:
            stats = {"files": 0, "bytes": 0}
        rows.append({
            "id": mapping.id,
            "provider": mapping.provider,
            "provider_name": mapping.provider_label(),
            "local_dir": mapping.local_dir,
            "remote_dir": mapping.remote_dir,
            "enabled": bool(mapping.enabled),
            "exclude": mapping.exclude,
            "max_file_mb": mapping.max_file_mb,
            "share": bool(mapping.share),
            "last_run": mapping.last_run or "",
            "last_result": mapping.last_result or "",
            "last_share_url": mapping.last_share_url or "",
            "files": int(stats.get("files") or 0),
            "bytes": int(stats.get("bytes") or 0),
        })
    try:
        runs = qstate.recent_runs(10)
    except Exception:
        runs = []
    run_rows = [{
        "finished_at": _ts(row.get("finished_at")),
        "local_dir": row.get("local_dir") or "",
        "remote_dir": row.get("remote_dir") or "",
        "scanned": row.get("scanned") or 0,
        "uploaded": row.get("uploaded") or 0,
        "skipped": row.get("skipped") or 0,
        "failed": row.get("failed") or 0,
        "bytes": row.get("bytes") or 0,
        "ok": bool(row.get("ok")),
        "message": str(row.get("message") or "")[:200],
    } for row in runs]
    try:
        schedule = qconfig.load_schedule()
    except Exception:
        schedule = {"mode": "daily", "time": "02:30", "interval_minutes": 120}
    try:
        log_lines = _tail(qjob._log_path())
    except Exception:
        log_lines = []
    return {
        "mappings": rows,
        "total": len(rows),
        "runs": run_rows,
        "schedule": schedule,
        "log": log_lines,
        "note": "只读：新增 / 修改映射、立即备份与文件下载仍在桌面端。",
    }
