"""WebUI 粉丝群用例（L3，Qt-free）。

复用 QQ 群服务读取目录 / 历史、刷新与取消订阅；订阅与历史仍由既有 owner 写入。
"""

from __future__ import annotations

from pathlib import Path
import re


def _service():
    from toolbox import qq_groups_service
    return qq_groups_service


def _qq_public_row(row) -> dict:
    """只输出展示字段，分享输入与本地头像路径不离开服务端。"""
    return {
        "no": row.no,
        "name": row.name,
        "members": row.member_count,
        "change": row.delta_text,
        "delta": getattr(row, "change", None),
        "status": row.status,
        "source": {"share": "分享链接", "login": "群号"}.get(row.source, "-"),
        "updated": row.fetched_at,
        "has_avatar": bool(row.avatar_path),
        "subscribed": bool(row.share_url),
    }


def overview() -> dict:
    groups = [_qq_public_row(row) for row in _service().catalog()]
    return {"groups": groups, "total": len(groups),
            "subscribed_count": sum(row["subscribed"] for row in groups),
            "note": "QQ群支持查询订阅、刷新、取消订阅、人数历史、导出与头像下载；取消订阅保留历史和头像。"}


def _qq_refresh_error(message: str) -> str:
    """平台异常可能含链接、路径或登录信息，只返回固定类别提示。"""
    text = str(message or "")
    if re.search(r"登录|认证|票据|cookie|token|credential|authorization", text, re.I):
        return "需要登录或登录状态已失效"
    if re.search(r"超时|timeout|timed out", text, re.I):
        return "请求超时，请稍后重试"
    status = re.search(r"\bHTTP\s*(\d{3})\b", text, re.I)
    if status:
        return f"读取群资料失败（HTTP {status.group(1)}）"
    return "刷新失败，请稍后重试"


def qq_refresh(payload: dict | None = None, on_log=None, is_cancelled=None,
               on_progress=None) -> dict:
    """刷新全部 QQ 群订阅；取消时保留已完成的历史记录和部分结果。"""
    service = _service()
    total = sum(bool(row.share_url) for row in service.catalog())
    if not total:
        raise ValueError("还没有已订阅的QQ群，请先查询并订阅")

    class CancelProbe:
        def is_set(self):
            return bool(is_cancelled and is_cancelled())

    cancel = CancelProbe()

    def progress(index, count, label):
        message = f"刷新QQ群 {index}/{count}：{label}"
        if on_log:
            on_log(message)
        if on_progress:
            on_progress((index - 1) / max(count, 1), message)

    if cancel.is_set():
        result = {"total": total, "ok": 0, "errors": [], "results": [], "cancelled": True}
    else:
        try:
            result = service.refresh_all(progress=progress, cancel=cancel)
        except Exception as exc:  # noqa: BLE001 - 不向网页透传存储 / 平台内部信息
            raise ValueError(_qq_refresh_error(str(exc))) from exc
    rows = [_qq_public_row(row) for row in result["results"]]
    errors = [{"name": str(name), "error": _qq_refresh_error(message)}
              for name, message in result["errors"]]
    total, ok = int(result["total"]), len(rows)
    cancelled = bool(result["cancelled"] or cancel.is_set())
    if cancelled:
        summary = f"刷新已取消，已完成 {ok}/{total} 个群，已完成记录已保留"
    else:
        summary = f"已刷新 {ok}/{total} 个群"
    if errors:
        summary += f"，失败 {len(errors)} 个"
    if on_progress:
        on_progress((ok + len(errors)) / max(total, 1), summary)
    return {"total": total, "ok": ok, "errors": errors, "results": rows,
            "cancelled": cancelled, "summary": summary}


def qq_remove(payload: dict) -> dict:
    """仅取消订阅，不删除人数历史、头像或任何平台资料。"""
    if payload.get("confirmed") is not True:
        raise ValueError("取消订阅需要确认")
    group_no = str(payload.get("no") or "").strip()
    if not re.fullmatch(r"[0-9]{5,12}", group_no):
        raise ValueError("请输入有效的QQ群号")
    service = _service()
    row = next((item for item in service.catalog() if item.no == group_no), None)
    if row is None:
        raise ValueError("QQ群不存在，请刷新列表后重试")
    try:
        removed = bool(service.remove(group_no)) if row.share_url else False
    except Exception as exc:  # noqa: BLE001 - 不透传存储路径等内部信息
        raise ValueError("取消订阅失败，请稍后重试") from exc
    summary = "已取消订阅，人数历史与头像已保留" if removed else "该群未订阅，人数历史与头像已保留"
    return {"removed": removed, "group_no": group_no, "summary": summary}


def history(group_no: str, limit: int = 200) -> dict:
    group_no = str(group_no or "").strip()
    if not group_no:
        raise ValueError("缺少群号")
    rows = []
    for row in _service().history(group_no, limit=limit):
        rows.append({
            "time": row.get("fetched_at") or row.get("recorded_ts") or "",
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


def qq_export() -> dict:
    """把当前 QQ 群列表导出为 Excel（与桌面同一份格式），供网页直接下载。"""
    service = _service()
    rows = service.catalog()
    if not rows:
        raise ValueError("还没有群可导出")
    path = service.export_rows(rows)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"导出文件读取失败：{exc}") from exc
    return {"data": data, "filename": path.name}


def qq_avatars(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """下载 / 刷新已订阅群头像；已有文件默认复用。"""
    service = _service()
    log = on_log or (lambda _msg: None)
    force = bool(payload.get("force"))
    log("重新下载全部群头像" if force else "补齐缺失的群头像")

    def progress(index, total, label):
        if is_cancelled and is_cancelled():
            return
        log(f"下载群头像 {index}/{total}：{label}")

    result = service.refresh_avatars(force=force, progress=progress)
    ok, total, errors = result["ok"], result["total"], result["errors"]
    summary = f"已下载 {ok}/{total} 个群头像"
    if errors:
        summary += f"，失败 {len(errors)} 个"
    return {"ok": ok, "total": total,
            "errors": [{"name": name, "error": message}
                       for name, message in errors][:20],
            "summary": summary}


def qq_query(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """粘贴群号或分享链接：读取群资料、保存快照并订阅（分享链接免登录）。"""
    service = _service()
    log = on_log or (lambda _msg: None)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("请粘贴群号或群资料分享链接")
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")
    log(f"读取群资料：{text[:60]}")
    try:
        row = service.save_link(text, str(payload.get("name") or ""))
    except Exception as exc:  # noqa: BLE001 - 平台异常类型较多
        raise ValueError(f"查询失败：{exc}") from exc
    return {
        "group_no": row.no,
        "name": row.name,
        "members": row.member_count,
        "change": row.delta_text,
        "status": row.status,
        "summary": f"{row.name or row.no}：{row.member_count} 人",
    }
