"""WebUI 账户追踪（音乐 / 网媒）：清单与历史缓存、批量采集、导出变化对比图片。

清单与历史只读本地（xlsx 缓存）；批量采集与导出复用桌面端 L4
（`music_account.accounts` / `tracker.accounts` 与各自 `export_png`）。
抖音账号采集顺带抓直播历史，失败不影响指标结果。本模块 Qt-free。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from toolbox.platform_names import (MEDIA_PLATFORM_NAMES,
                                    MUSIC_PLATFORM_NAMES)

_KINDS = {"music": "音乐账号", "media": "网媒账号"}

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


def _modules(kind: str):
    kind = str(kind or "").strip()
    if kind == "music":
        from music_account import accounts as acc_mod
        from music_account import export_png as export_mod
    elif kind == "media":
        from tracker import accounts as acc_mod
        from tracker import export_png as export_mod
    else:
        raise ValueError("未知账号类型")
    return kind, acc_mod, export_mod


def collect_all(payload: dict, on_progress=None, on_log=None,
                is_cancelled=None) -> dict:
    """按平台筛选批量采集（4 并发），逐账号写历史缓存。"""
    kind, acc_mod, _export_mod = _modules(payload.get("kind"))
    platform = str(payload.get("platform") or "").strip()
    try:
        accounts = list(acc_mod.load_accounts())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"读取账号清单失败：{exc}") from exc
    targets = [account for account in accounts
               if not platform
               or (str(account.get("platform") or "")).strip() == platform]
    if not targets:
        raise ValueError("没有符合条件的账号")
    total = len(targets)
    logs = []
    counts = {"ok": 0, "failed": 0, "live": 0}
    failures = []
    done = 0

    def work(account):
        record = acc_mod.collect_account(account)
        acc_mod.save_history(account.get("name", "未命名"), record)
        live = None
        live_error = ""
        if kind == "media" and str(account.get("platform") or "").strip() == "douyin":
            try:
                from tracker.douyin_live import collect_and_save
                live = collect_and_save(account, record.get("uid"))
                live_error = str((live or {}).get("error") or "")[:120]
            except Exception as exc:  # noqa: BLE001 - 直播历史是补充项
                live_error = str(exc)[:120]
        return record, live, live_error

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for account in targets:
            if is_cancelled and is_cancelled():
                break
            futures[pool.submit(work, account)] = account
        for future in as_completed(futures):
            account = futures[future]
            done += 1
            name = str(account.get("name") or account.get("url") or "-")
            try:
                record, live, live_error = future.result()
                counts["ok"] += 1
                if live and not live_error:
                    counts["live"] += 1
                error = str((record or {}).get("error") or "")
                logs.append(f"{name}：{error or '采集完成'}"
                            + ("（含直播历史）" if live and not live_error else ""))
                if live_error:
                    logs.append(f"{name}：直播历史失败（{live_error}）")
            except Exception as exc:  # noqa: BLE001 - 单账号失败不影响其它
                counts["failed"] += 1
                failures.append({"name": name, "error": str(exc)[:200]})
                logs.append(f"{name}：失败（{exc}）")
            _progress(on_progress, done, total, f"已处理 {done}/{total}：{name}")
    cancelled = bool(is_cancelled and is_cancelled())
    return {
        "ok": counts["ok"],
        "failed": counts["failed"],
        "live": counts["live"],
        "cancelled": cancelled,
        "logs": logs[-200:],
        "failures": failures[:50],
        "summary": (f"{_KINDS[kind]}批量采集完成：成功 {counts['ok']}"
                    + (f"，失败 {counts['failed']}" if counts["failed"] else "")
                    + (f"，含直播历史 {counts['live']}" if counts["live"] else "")
                    + ("（已取消，剩余账号未处理）" if cancelled else "")),
        "note": "每个账号采完即写入历史缓存；结果会刷新到账号卡片。",
    }


def export_png(payload: dict) -> dict:
    """导出该账号最近两次采集的变化对比 PNG（返回图片字节）。"""
    kind, acc_mod, export_mod = _modules(payload.get("kind"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("缺少账号名称")
    platform = str(payload.get("platform") or "").strip()
    try:
        accounts = list(acc_mod.load_accounts())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"读取账号清单失败：{exc}") from exc
    account = next((item for item in accounts
                    if str(item.get("name") or "") == name
                    and (not platform
                         or str(item.get("platform") or "") == platform)), None)
    if account is None:
        raise ValueError("账号不存在或已改名，请刷新后重试")
    try:
        history = list(acc_mod.load_history(name))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"读取历史失败：{exc}") from exc
    if len(history) < 2:
        raise ValueError("该账号至少需要两次采集数据才能导出变化对比")
    try:
        path = export_mod.export_diff_png(account, history, theme="dark")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"导出图片失败：{exc}") from exc
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取导出图片失败：{exc}") from exc
    return {"data": data, "filename": target.name, "size": len(data),
            "summary": f"已导出 {target.name}（{_KINDS[kind]}变化对比）"}


def _progress(on_progress, done, total, text) -> None:
    if on_progress:
        on_progress(done, total, text)


def groups(kind: str) -> dict:
    """账号分组列表（音乐 / 网媒各自的 groups 配置）。"""
    _kind, acc_mod, _export = _modules(kind)
    try:
        mapping = acc_mod.get_groups() or {}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"读取分组失败：{exc}") from exc
    rows = [{"id": str(gid), "name": str(name)}
            for gid, name in sorted(mapping.items())]
    return {"groups": rows, "total": len(rows)}


def group_png(payload: dict) -> dict:
    """导出某分组全部账号的变化对比合成 PNG（返回字节）。"""
    _kind, acc_mod, export_mod = _modules(payload.get("kind"))
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("请选择分组")
    mapping = acc_mod.get_groups() or {}
    group_name = str(mapping.get(group_id) or "").strip()
    if not group_name:
        raise ValueError("分组不存在或已删除，请刷新后重试")
    try:
        path = export_mod.export_group_png(group_id, group_name)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"导出分组图片失败：{exc}") from exc
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取导出图片失败：{exc}") from exc
    return {"data": data, "filename": target.name, "size": len(data),
            "summary": f"已导出分组「{group_name}」变化对比（{target.name}）"}
