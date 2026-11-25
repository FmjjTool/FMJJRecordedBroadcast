"""WebUI 采集任务：账号清单、设置快照、批量解析与按勾选账号采集。

批量解析与采集复用 L4（`download_ops` / `accounts_manager` /
`account_collection.collect_accounts` / `collected_store`），与桌面端同一套
识别与落库规则；不 import Qt。
"""

from __future__ import annotations

import threading
from urllib.parse import urlparse

from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.platform_names import MEDIA_PLATFORM_NAMES

_MAX_BATCH_LINES = 200


def _manager():
    import accounts_manager
    return accounts_manager


def _is_account_url(url: str) -> bool:
    """账号主页链接判定（与桌面端 info_accounts 同一规则）。"""
    u = (url or "").strip().lower()
    if not u:
        return False
    if ("space.bilibili.com/" in u or "m.bilibili.com/space/" in u
            or "douyin.com/user/" in u or "iesdouyin.com/share/user/" in u
            or "xiaohongshu.com/user/profile/" in u
            or "kuaishou.com/profile/" in u):
        return True
    p = urlparse(u)
    host, path = p.netloc, p.path.strip("/")
    if "weibo" in host:
        if path.startswith("u/") or path.startswith("n/"):
            return True
        if path and "/" not in path and path.isdigit():
            return True
    return False


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
        "note": ("可勾选账号后「获取最新」，或在下方批量解析链接；"
                 "运行日志与结果只在本次任务内显示。"),
    }


# ==================== 批量解析 / 采集 ====================

def _lines(text) -> list[str]:
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line)
    if not rows:
        raise ValueError("请输入账号主页或作品链接（一行一个）")
    if len(rows) > _MAX_BATCH_LINES:
        raise ValueError(f"单次最多 {_MAX_BATCH_LINES} 行，请分批处理")
    return rows


def parse_batch(payload: dict, log=None, on_progress=None,
                is_cancelled=None) -> dict:
    """逐行处理：账号主页 → 添加账号；作品链接 / 分享文案 → 解析并入库。"""
    from toolbox import download_ops
    from toolbox.collected_store import insert_items

    lines = _lines(payload.get("text"))
    manager = _manager()
    existing = {account.url for account in manager.load_accounts()}
    logs = []
    counts = {"accounts": 0, "duplicate_accounts": 0, "posts": 0,
              "new_posts": 0, "failed": 0}
    cancelled = False
    for index, raw in enumerate(lines, 1):
        if is_cancelled and is_cancelled():
            cancelled = True
            break
        _progress(on_progress, index - 1, len(lines), f"处理第 {index} 行")
        line = f"[{index}/{len(lines)}]"
        url = download_ops.extract_share_url(raw)
        if _is_account_url(url):
            if url in existing:
                counts["duplicate_accounts"] += 1
                _log(log, logs, f"{line} 账号已存在，跳过：{url[:60]}")
                continue
            name = ""
            try:
                from platforms.account_names import fetch_display_name
                name, _err = fetch_display_name(url)
            except Exception:  # noqa: BLE001 - 取名失败不影响添加
                name = ""
            if manager.add_account(url, name=name):
                existing.add(url)
                counts["accounts"] += 1
                _log(log, logs, f"{line} 已添加账号：{name or url[:60]}")
            else:
                counts["duplicate_accounts"] += 1
                _log(log, logs, f"{line} 账号已存在，跳过：{url[:60]}")
            continue
        try:
            info = download_ops.parse_url_info(raw)
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            _log(log, logs, f"{line} {url[:70]}（{exc}）")
            continue
        added = int(insert_items([info]) or 0)
        counts["posts"] += 1
        counts["new_posts"] += added
        _log(log, logs, f"{line} "
                        + ("已存入「已获取」" if added else "已存在，未重复入库")
                        + f"：{info.summary()}")
    _progress(on_progress, len(lines), len(lines), "处理完成")
    return {
        "ok": not cancelled,
        "cancelled": cancelled,
        "total": len(lines),
        "counts": counts,
        "logs": logs[-200:],
        "summary": (f"共 {len(lines)} 行：新增账号 {counts['accounts']}，"
                    f"重复账号 {counts['duplicate_accounts']}，"
                    f"作品 {counts['posts']} 条（新入库 {counts['new_posts']}），"
                    f"失败 {counts['failed']}")
                  + ("（已取消）" if cancelled else ""),
    }


def ensure_collectable(payload: dict) -> list:
    """提交采集任务前校验账号清单，避免先建任务再失败。"""
    manager = _manager()
    all_accounts = manager.load_accounts()
    wanted = [str(url) for url in (payload.get("urls") or []) if str(url).strip()]
    if wanted:
        by_url = {account.url: account for account in all_accounts}
        missing = [url for url in wanted if url not in by_url]
        if missing:
            raise ValueError(f"{len(missing)} 个账号已不存在，请刷新后重选")
        accounts = [by_url[url] for url in wanted]
        if not accounts:
            raise ValueError("选中的账号已不在账号清单中，请刷新页面")
    else:
        accounts = list(all_accounts)
    if not accounts:
        raise ValueError("账号清单为空，请先添加账号")
    return accounts


def collect(payload: dict, log=None, on_progress=None,
            is_cancelled=None) -> dict:
    """按勾选账号（或全部）采集最新内容，落库规则与桌面端一致。"""
    from toolbox.account_collection import collect_accounts
    from toolbox.collected_store import ingest_items, insert_items

    accounts = ensure_collectable(payload)
    try:
        count = int(payload.get("count") or 10)
    except (TypeError, ValueError):
        count = 10
    count = max(1, min(count, 200))
    incremental = payload.get("incremental")
    if incremental is None:
        incremental = bool(load_convert_config().get("inc_db_checked", True))
    date_start = str(payload.get("date_start") or "").strip()
    date_end = str(payload.get("date_end") or "").strip()
    logs = []
    cancel_event = threading.Event()
    total_accounts = len(accounts)
    done = {"n": 0}

    def on_event(event: dict) -> None:
        if is_cancelled and is_cancelled():
            cancel_event.set()
        if event.get("type") == "log":
            _log(log, logs, str(event.get("text") or ""))
        elif event.get("type") == "progress":
            done["n"] = int(event.get("done") or 0)
            _progress(on_progress, done["n"], total_accounts,
                      str(event.get("label") or "采集进度"))

    def persist_batch(posts, inc) -> tuple:
        try:
            if inc:
                added = ingest_items(posts, incremental=True)
                parts = "、".join(f"{platform} +{number}"
                                  for platform, number in added.items() if number)
                return (f"已入库 {parts}" if parts else "无新增（已全部在库中）"), added
            insert_items(posts)
            return f"已入库 {len(posts)} 条", len(posts)
        except Exception as exc:  # noqa: BLE001
            return f"保存失败: {exc}", {}

    total, run_log, _posts = collect_accounts(
        accounts, count,
        rate_count=int(payload.get("rate_count") or 10),
        rate_interval=float(payload.get("rate_interval") or 10),
        incremental=bool(incremental),
        on_event=on_event, cancel_event=cancel_event,
        date_start=date_start, date_end=date_end,
        insert_items=insert_items, persist_batch=persist_batch)
    _progress(on_progress, total_accounts, total_accounts, "采集完成")
    return {
        "ok": not cancel_event.is_set(),
        "cancelled": cancel_event.is_set(),
        "accounts": total_accounts,
        "items": int(total or 0),
        "logs": (logs + list(run_log or []))[-300:],
        "summary": (f"{total_accounts} 个账号完成，共处理 {int(total or 0)} 条内容")
                   + ("（已取消，剩余账号跳过）" if cancel_event.is_set() else ""),
    }


def save_settings(payload: dict) -> dict:
    """保存会话级采集设置（增量模式、日期段）；账号与条数仍按次传入。"""
    cfg = load_convert_config()
    if "incremental" in payload:
        cfg["inc_db_checked"] = bool(payload.get("incremental"))
    if "date_start" in payload:
        cfg["fetch_date_start"] = str(payload.get("date_start") or "")
    if "date_end" in payload:
        cfg["fetch_date_end"] = str(payload.get("date_end") or "")
    save_convert_config(cfg)
    return {"ok": True, "incremental": bool(cfg.get("inc_db_checked", True)),
            "date_start": cfg.get("fetch_date_start") or "",
            "date_end": cfg.get("fetch_date_end") or ""}


def _log(log, logs, text: str) -> None:
    if not text:
        return
    logs.append(str(text))
    if log:
        log(str(text))


def _progress(on_progress, done, total, text) -> None:
    if on_progress:
        on_progress(done, total, text)
