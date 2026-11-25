"""WebUI 采集任务（只读）：账号清单与采集设置快照。

不触发批量解析 / 采集；这些动作仍在桌面端。
"""

from __future__ import annotations

from toolbox.config_manager import load_convert_config
from toolbox.platform_names import MEDIA_PLATFORM_NAMES


def _manager():
    import accounts_manager
    return accounts_manager


def overview() -> dict:
    manager = _manager()
    try:
        accounts = manager.load_accounts()
    except Exception:
        accounts = []
    try:
        groups = manager.get_groups() or {}
    except Exception:
        groups = {}
    from platforms.base import URLRecognizer

    rows = []
    by_platform: dict[str, int] = {}
    for account in accounts:
        platform = ""
        try:
            platform = URLRecognizer.recognize(account.url) or ""
        except Exception:
            platform = ""
        group_text = "、".join(
            (str(gid) + " " + str(groups.get(str(gid)) or "")).strip()
            for gid in account.group_ids if str(gid))
        rows.append({
            "name": account.name or account.url,
            "url": account.url,
            "platform": platform,
            "platform_name": MEDIA_PLATFORM_NAMES.get(platform,
                                                     platform or "未知"),
            "groups": group_text,
            "note": account.note or "",
            "kind": account.kind or "auto",
        })
        by_platform[platform or "unknown"] = by_platform.get(
            platform or "unknown", 0) + 1
    cfg = load_convert_config()
    settings = {
        "count": 10,
        "rate_count": 10,
        "rate_interval": 10,
        "incremental": bool(cfg.get("inc_db_checked", True)),
        "auto_download": bool(cfg.get("auto_dl_checked", False)),
        "date_start": str(cfg.get("fetch_date_start") or ""),
        "date_end": str(cfg.get("fetch_date_end") or ""),
    }
    return {
        "accounts": rows,
        "total": len(rows),
        "by_platform": [
            {"key": key, "name": MEDIA_PLATFORM_NAMES.get(key, key),
             "count": count}
            for key, count in sorted(by_platform.items())
        ],
        "settings": settings,
        "note": "只读：账号清单与采集设置快照；批量解析、获取最新与会话定时仍在桌面端。",
    }
