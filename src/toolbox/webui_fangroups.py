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


def query(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """按平台查询粉丝群（抖音 / 微博 / B站），保存快照并加入定时快照名单。"""
    from platforms import fan_groups as fg

    platform = str(payload.get("platform") or "").strip()
    if platform not in ("douyin", "weibo", "bilibili"):
        raise ValueError("平台只支持抖音 / 微博 / B站")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("请粘贴主页链接或输入 UID")
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")

    def say(message) -> None:
        if on_log:
            on_log(str(message))

    try:
        owner_id, groups = fg.fetch_groups(platform, text, progress=say)
    except Exception as exc:  # noqa: BLE001 - 平台异常类型较多
        raise ValueError(f"查询失败：{exc}") from exc
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消（未保存快照）")
    try:
        avatar_ok, avatar_total = fg.download_avatars(
            platform, owner_id, groups, progress=say)
    except Exception:  # noqa: BLE001 - 头像下载失败不影响结果
        avatar_ok, avatar_total = 0, 0
    try:
        previous = fg.load_latest(platform, owner_id)
        changes = fg.diff_members(groups, previous)
        snapshot = fg.save_snapshot(platform, owner_id, groups)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"保存快照失败：{exc}") from exc
    subscribed, sub_message = False, ""
    try:
        from toolbox import account_registry as registry
        subscribed, sub_message = registry.subscribe_fan_groups(platform, owner_id)
    except Exception:  # noqa: BLE001 - 订阅失败不阻断查询
        pass
    rows = []
    for group in groups:
        rows.append({
            "group_id": group.group_id,
            "name": group.name,
            "group_no": group.group_no,
            "members": group.member_count,
            "member_text": group.member_text,
            "change": changes.get(group.group_id),
            "tags": group.tags or "",
            "notice": group.notice or "",
            "url": group.url or "",
            "avatar": bool(group.avatar_path),
        })
    label = fg.PLATFORM_LABELS.get(platform, platform)
    previous_at = str((previous or {}).get("fetched_at") or "")
    return {
        "platform": platform,
        "platform_label": label,
        "owner_id": owner_id,
        "groups": rows,
        "total": len(rows),
        "changes": len(changes),
        "avatars": {"ok": int(avatar_ok), "total": int(avatar_total)},
        "snapshot": str(snapshot),
        "previous_at": previous_at,
        "subscribed": bool(subscribed),
        "subscribe_note": str(sub_message or "")[:200],
        "summary": (f"{label} {owner_id}：查到 {len(rows)} 个群"
                    + (f"，头像 {avatar_ok}/{avatar_total}" if avatar_total else "")
                    + (f"，人数变化 {len(changes)} 个" if previous_at else "")
                    + ("，已加入定时快照" if subscribed else "")),
        "note": "快照已保存到粉丝群导出目录；查询不会修改群资料。",
    }
