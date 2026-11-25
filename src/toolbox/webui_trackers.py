"""WebUI 账户追踪（音乐 / 网媒）只读数据（L3）。

只读本地账号清单与历史缓存（xlsx），不写账号文件、不触发采集。
采集 / 导出仍在桌面端。
"""

from __future__ import annotations

from toolbox.platform_names import (MEDIA_PLATFORM_NAMES,
                                    MUSIC_PLATFORM_NAMES)

MUSIC_METRICS = [
    ("粉丝数", ("fans",)),
    ("关注数", ("following",)),
    ("播放/听众", ("listens",)),
    ("歌曲数", ("songs",)),
    ("专辑数", ("albums",)),
    ("MV数", ("mvs",)),
    ("作品数", ("works",)),
    ("简介", ("sign",)),
]
MEDIA_METRICS = [
    ("粉丝数", ("fans",)),
    ("关注数", ("following",)),
    ("获赞数", ("likes",)),
    ("内容数", ("video_count", "weibo_count")),
    ("等级", ("level",)),
    ("认证", ("official_title", "verified_reason", "verified")),
    ("简介", ("sign",)),
    ("昵称", ("name",)),
]
_NUM_KEYS = {"fans", "following", "listens", "songs", "albums", "mvs",
             "works", "likes", "video_count", "weibo_count", "level"}


def _music_module():
    from music_account import accounts
    return accounts


def _media_module():
    from tracker import accounts
    return accounts


def _fmt_value(key: str, value) -> str:
    if value is None or value == "":
        return "-"
    if key == "verified":
        return "已认证" if value else "-"
    if key in _NUM_KEYS:
        try:
            return f"{int(float(value)):,}"
        except (TypeError, ValueError):
            return str(value)
    text = str(value)
    return text if len(text) <= 32 else text[:32] + "…"


def _latest_metrics(record: dict, metrics) -> list[dict]:
    cells = []
    for label, keys in metrics:
        value = None
        for key in keys:
            if record.get(key) not in (None, ""):
                value = record.get(key)
                break
        cells.append({"label": label,
                      "value": _fmt_value(keys[0], value)})
    return cells


def _overview(kind: str) -> dict:
    if kind == "music":
        module = _music_module()
        metrics = MUSIC_METRICS
        names = MUSIC_PLATFORM_NAMES
    else:
        module = _media_module()
        metrics = MEDIA_METRICS
        names = MEDIA_PLATFORM_NAMES
    accounts = module.load_accounts()
    try:
        groups = module.get_groups() or {}
    except Exception:
        groups = {}
    rows = []
    for acc in accounts:
        platform = str(acc.get("platform") or "")
        if not platform and kind == "music":
            try:
                platform = (module.parse_url(acc.get("url", "")) or [""])[0] or ""
            except Exception:
                platform = ""
        name = str(acc.get("name") or "未命名")
        try:
            history = module.load_history(name) or []
        except Exception:
            history = []
        latest = history[0] if history else {}
        group_ids = acc.get("group_ids")
        if group_ids is None:
            group_id = str(acc.get("group_id") or "")
            group_ids = [group_id] if group_id else []
        group_text = "、".join(
            (str(gid) + " " + str(groups.get(str(gid)) or "")).strip()
            for gid in group_ids if str(gid))
        rows.append({
            "name": name,
            "platform": platform,
            "platform_name": names.get(platform, platform or "未知"),
            "group": group_text,
            "records": len(history),
            "last_time": str(latest.get("_time") or ""),
            "metrics": _latest_metrics(latest, metrics),
        })
    platforms = []
    for row in rows:
        item = {"key": row["platform"], "name": row["platform_name"]}
        if item not in platforms:
            platforms.append(item)
    return {"kind": kind, "accounts": rows, "total": len(rows),
            "platforms": platforms, "groups": groups,
            "note": "只读：采集与导出仍在桌面端。"}


def music_overview() -> dict:
    return _overview("music")


def media_overview() -> dict:
    return _overview("media")


def _history(kind: str, name: str, limit: int = 50) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("缺少账号名称")
    module = _music_module() if kind == "music" else _media_module()
    metrics = MUSIC_METRICS if kind == "music" else MEDIA_METRICS
    try:
        rows = module.load_history(name) or []
    except Exception as exc:
        raise ValueError(f"读取历史失败：{str(exc)[:60]}") from exc
    limit = max(1, min(200, int(limit)))
    rows = rows[:limit]
    columns = [{"key": "time", "label": "采集时间"}]
    for label, keys in metrics:
        columns.append({"key": keys[0], "label": label})
    table = []
    for row in rows:
        cells = {"time": row.get("_time") or "-"}
        for label, keys in metrics:
            value = None
            for key in keys:
                if row.get(key) not in (None, ""):
                    value = row.get(key)
                    break
            cells[keys[0]] = _fmt_value(keys[0], value)
        table.append(cells)
    return {"kind": kind, "name": name, "columns": columns,
            "rows": table, "total": len(rows)}


def music_history(name: str, limit: int = 50) -> dict:
    return _history("music", name, limit)


def media_history(name: str, limit: int = 50) -> dict:
    return _history("media", name, limit)
