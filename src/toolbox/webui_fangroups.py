"""WebUI 粉丝群（QQ 群订阅列表）只读数据（L3）。

读取 QQ 群订阅目录与人数历史（本地 SQLite），不修改订阅、不触发刷新 / 下载。
"""

from __future__ import annotations

from pathlib import Path


def _service():
    from toolbox import qq_groups_service
    return qq_groups_service


def overview() -> dict:
    service = _service()
    groups = []
    for row in service.catalog():
        source = {"share": "分享链接", "login": "群号"}.get(row.source, "-")
        groups.append({
            "no": row.no,
            "name": row.name,
            "members": row.member_count,
            "change": row.delta_text,
            "status": row.status,
            "source": source,
            "updated": row.fetched_at,
            "has_avatar": bool(row.avatar_path),
        })
    return {"groups": groups, "total": len(groups),
            "note": "只读：QQ 群订阅列表与人数历史；订阅、刷新、导出与头像下载仍在桌面端。"}


def history(group_no: str, limit: int = 200) -> dict:
    group_no = str(group_no or "").strip()
    if not group_no:
        raise ValueError("缺少群号")
    rows = []
    for row in _service().history(group_no, limit=limit):
        rows.append({
            "time": row.get("recorded_ts") or row.get("fetched_at") or "",
            "members": row.get("member_count"),
            "name": row.get("name") or "",
        })
    return {"group_no": group_no, "rows": rows, "total": len(rows)}


def avatar_file(group_no: str) -> Path | None:
    group_no = str(group_no or "").strip()
    if not group_no:
        return None
    for row in _service().catalog():
        if row.no != group_no:
            continue
        path = Path(row.avatar_path) if row.avatar_path else None
        if path is None or not path.is_file():
            return None
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
        except OSError:
            return None
        return path
    return None
