"""腾讯文档「中间层同步」WebUI 用例（L3）：本地比对预览、TSV 与白名单批量新增。

只读比对：`bridge.build_pending` / `build_live_pending` / `build_song_pending` /
`build_event_pending` / `build_merch_pending` 全部读本地快照与本地采集数据，不联网。

写回：仅允许《封茗囧菌数据汇总库》的「官方动态」（ss_h2uh2s）与「直播记录」
（ss_1xgstm）批量新增，且必须满足刷新门禁（或桌面端免校对模式）、带二次确认；
写入走与桌面端相同的 `import_pending.run` / `write_lives`，读回校验并记录推送历史。
完整歌曲 / 线下活动 / 周边图鉴在网页端只读预览（仅新增授权未覆盖到 Web）。

「同步前刷新」直接复用 `sync_refresh.run`（采集平台资料 + 拉取腾讯文档快照）。
本模块 Qt-free，只依赖 L3/L4 用例。
"""

from __future__ import annotations

from toolbox import sync_refresh
from toolbox.tcdoc import bridge, tables
from toolbox.tcdoc import import_pending as pending
from toolbox.tcdoc.import_local import (ImportErrorDetail, LocalImage,
                                        LocalImportClient, ManagedAuth,
                                        validate_target)

DOC_NAME = "封茗囧菌数据汇总库"
MAX_UPLOAD = 50
DAYS_OPTIONS = (("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90),
                ("近 1 年", 365))
LIVE_DUP_MODES = (("重复：跳过", "skip"), ("重复：只填空", "fill"),
                  ("重复：覆盖自动字段", "overwrite"))
# 网页端允许批量新增的两张表（与 webui_tcdoc_write 的单条写回白名单一致）
APPEND_TARGETS = {
    ("DRnd3bkhkQUR1aEJr", "ss_h2uh2s"): "official",
    ("DRnd3bkhkQUR1aEJr", "ss_1xgstm"): "live",
}


class BridgeCancelled(Exception):
    """上传任务在开始前被取消。"""


def _write_mode(target: dict) -> str:
    if (target["doc_key"], target["tab_id"]) in APPEND_TARGETS:
        return "append"
    return "read_only"


def overview() -> dict:
    """中间层概览：可同步的表、刷新门禁与默认范围。"""
    targets = []
    for item in bridge.list_targets():
        targets.append({
            "doc_key": item["doc_key"],
            "doc_name": item.get("doc_name") or "",
            "doc_url": item.get("doc_url") or "",
            "tab_id": item["tab_id"],
            "tab_name": item.get("tab_name") or "",
            "profile": item.get("profile") or "posts",
            "n_rows": int(item.get("n_rows") or 0),
            "columns": len(item.get("columns") or []),
            "write_mode": _write_mode(item),
        })
    allowed, message = sync_refresh.upload_allowed()
    return {
        "doc_name": DOC_NAME,
        "targets": targets,
        "days": [{"label": label, "value": value} for label, value in DAYS_OPTIONS],
        "dup_modes": [{"label": label, "value": value}
                      for label, value in LIVE_DUP_MODES],
        "refresh": {"allowed": bool(allowed), "message": str(message)},
        "bypass": bool(sync_refresh.bypass_enabled()),
        "max_upload": MAX_UPLOAD,
        "note": ("比对只读本地快照与本地采集数据；批量上传只限「官方动态」「直播记录」"
                 "两表且仅新增，需要先「同步前刷新」（或桌面端开启免校对模式）。"),
    }


def preview(payload: dict, on_progress=None, is_cancelled=None) -> dict:
    """按表 / 时间范围 / 账号比对，返回待同步行与 TSV（不写入任何数据）。"""
    target = _find_target(payload.get("doc_key"), payload.get("tab_id"))
    days = _days(payload.get("days"))
    profile = target["profile"]
    accounts = _accounts(payload.get("accounts"))
    dup_mode = str(payload.get("dup_mode") or "skip")
    if dup_mode not in {value for _label, value in LIVE_DUP_MODES}:
        dup_mode = "skip"
    _progress(on_progress, 0.2, 1, "正在比对本地数据与表格快照")
    _check_cancelled(is_cancelled)
    if profile == "lives":
        result = bridge.build_live_pending(days=days, duplicate_mode=dup_mode)
    elif profile == "songs":
        result = bridge.build_song_pending()
    elif profile == "events":
        result = bridge.build_event_pending(days=days)
    elif profile == "merch":
        result = bridge.build_merch_pending(days=days)
    else:
        result = bridge.build_pending(target["doc_key"], target["tab_id"],
                                      days=days, accounts=accounts)
    _check_cancelled(is_cancelled)
    rows = list(result.get("rows") or [])
    existing = list(result.get("existing") or [])
    columns = list(result.get("columns") or [])
    items = [_item(profile, row, index) for index, row in enumerate(rows)]
    tsv = ""
    try:
        tsv = bridge.to_tsv(rows, columns, target["doc_key"], target["tab_id"])
    except Exception:  # noqa: BLE001 - TSV 只是辅助，失败不影响预览
        tsv = ""
    allowed, message = sync_refresh.upload_allowed()
    write_mode = _write_mode(target)
    _progress(on_progress, 1, 1, "比对完成")
    return {
        "doc_key": target["doc_key"],
        "doc_name": target.get("doc_name") or DOC_NAME,
        "tab_id": target["tab_id"],
        "tab_name": target.get("tab_name") or "",
        "profile": profile,
        "days": days,
        "dup_mode": dup_mode,
        "columns": columns,
        "items": items,
        "total": len(rows),
        "existing": len(existing),
        "tsv": tsv,
        "stats": _stats(result.get("stats") or {}),
        "can_upload": write_mode == "append",
        "write_mode": write_mode,
        "refresh": {"allowed": bool(allowed), "message": str(message)},
        "note": ("只读比对：不写入腾讯文档。" if write_mode != "append"
                 else "只读比对：勾选后可在页面确认上传，仅新增、不修改已有行。"),
    }


def refresh(on_status=None) -> dict:
    """同步前刷新：采集平台资料 + 拉取腾讯文档快照（与桌面端同一套流程）。"""
    return sync_refresh.run(on_status=on_status or (lambda _text: None))


def ensure_uploadable(payload: dict) -> dict:
    """提交上传任务前的同步校验：二次确认、白名单、刷新门禁与勾选数量。

    在请求线程执行，门禁不通过直接返回 400，不会先建任务再失败。
    """
    if payload.get("confirmed") is not True:
        raise ValueError("请在页面上确认后再上传")
    target = _find_target(payload.get("doc_key"), payload.get("tab_id"))
    if _write_mode(target) != "append":
        raise ValueError("该表在网页端只读；如需写入请在桌面端操作")
    try:
        validate_target(target["doc_key"], target["tab_id"])
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc
    allowed, message = sync_refresh.upload_allowed()
    if not allowed:
        raise ValueError(f"当前数据未刷新，暂不允许写入：{message}。"
                         "请先「同步前刷新」，或在桌面端开启免校对模式。")
    keys = [str(item) for item in (payload.get("keys") or []) if str(item).strip()]
    if not keys:
        raise ValueError("请先勾选要上传的记录")
    if len(keys) > MAX_UPLOAD:
        raise ValueError(f"单次最多上传 {MAX_UPLOAD} 条，请缩小范围")
    return target


def upload(payload: dict, on_progress=None, is_cancelled=None) -> dict:
    """批量新增：重建待同步清单 → 按勾选键过滤 → 写入 → 读回校验。"""
    target = ensure_uploadable(payload)
    keys = [str(item) for item in (payload.get("keys") or []) if str(item).strip()]
    days = _days(payload.get("days"))
    profile = target["profile"]
    _progress(on_progress, 0.15, 1, "正在重建待同步清单")
    if profile == "lives":
        result = bridge.build_live_pending(
            days=days, duplicate_mode=str(payload.get("dup_mode") or "skip"))
    else:
        result = bridge.build_pending(target["doc_key"], target["tab_id"],
                                      days=days,
                                      accounts=_accounts(payload.get("accounts")))
    rows = list(result.get("rows") or [])
    picked = []
    seen = set()
    for index, row in enumerate(rows):
        key = _row_key(profile, row, index)
        if key in keys and key not in seen:
            seen.add(key)
            picked.append(row)
    if not picked:
        raise ValueError("待上传记录已变化或已同步，请重新比对后再试")
    _check_cancelled(is_cancelled)
    if profile == "lives":
        return _upload_lives(target, picked, on_progress, is_cancelled)
    return _upload_posts(target, picked, days, on_progress, is_cancelled)


def _upload_posts(target, rows, days, on_progress, is_cancelled) -> dict:
    _progress(on_progress, 0.3, 1, "正在准备记录与本地图片")
    sources = bridge.collect_local_items(days)
    catalogue = pending.build_catalogue(pending.ROOT, sources)
    try:
        records, missing = pending.records_from_rows(pending.ROOT, rows, catalogue)
    except ImportErrorDetail as exc:
        raise ValueError(f"待上传记录无法写入：{exc}") from exc
    media = pending.ROOT / "download/已获取数据"
    for record in records:
        record["_images"] = [LocalImage.inspect(pending.ROOT / path, media)
                             for path in record.get("images") or []]
    _check_cancelled(is_cancelled)
    _progress(on_progress, 0.45, 1, "正在校验收件字段与下拉选项")
    client = _client(target)
    client.connect()
    fields = client.get_fields()
    blocked = []
    for record in records:
        try:
            pending.make_plan(record, fields, available_only=False,
                              allow_new_options=False,
                              doc_key=target["doc_key"], tab_id=target["tab_id"])
        except ImportErrorDetail as exc:
            blocked.append({"title": str(record.get("作品标题") or ""),
                            "reason": str(exc)})
    if blocked:
        raise ValueError(
            f"{len(blocked)} 条记录字段不满足写入条件（如新账号 / 新平台需要新建下拉选项）："
            f"{blocked[0]['title']}（{blocked[0]['reason']}）；请在桌面端确认后上传")
    for record in records:
        if is_cancelled and is_cancelled():
            raise BridgeCancelled("任务已取消")
        pending.prepare_comment_images(record)
    progress = {"done": 0}

    def emit(event) -> None:
        if event.get("phase") != "record_created":
            return
        progress["done"] += 1
        _progress(on_progress, 0.5 + 0.4 * progress["done"] / max(1, len(records)),
                  1, f"正在上传 {progress['done']}/{len(records)}")

    _progress(on_progress, 0.5, 1, "正在连接腾讯文档并新增记录")
    receipt = pending.run(client, records, apply=True, verify_each=False,
                          emit=emit, profile="posts",
                          allow_new_options=False, source="WebUI 中间层上传")
    added = list(receipt.get("added") or [])
    skipped = list(receipt.get("skipped") or [])
    updated = int(receipt.get("updated") or 0)
    duplicates = list(receipt.get("duplicates") or [])
    failed = [item for item in added if not item.get("verified")]
    if added:
        sync_refresh.invalidate("WebUI 中间层上传完成，需重新刷新后再写")
    _progress(on_progress, 1, 1, "上传完成")
    return {
        "tab_name": target.get("tab_name") or "官方动态",
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "profile": "posts",
        "added": len(added),
        "updated": updated,
        "skipped": len(skipped),
        "duplicates": len(duplicates),
        "failed_verify": len(failed),
        "missing_images": len(missing),
        "record_ids": [str(item.get("record_id") or "") for item in added][:20],
        "summary": _summary(len(added), updated, len(skipped), len(failed),
                            len(missing)),
        "note": "仅新增 / 追加，不修改或删除已有行。",
    }


def _upload_lives(target, rows, on_progress, is_cancelled) -> dict:
    _progress(on_progress, 0.4, 1, "正在连接腾讯文档")
    client = _client(target)
    client.connect()
    _check_cancelled(is_cancelled)
    _progress(on_progress, 0.55, 1, f"正在新增 {len(rows)} 条直播记录")
    receipt = pending.write_lives(client, rows, limit=len(rows), apply=True,
                                  verify_each=False)
    added = list(receipt.get("added") or [])
    skipped = list(receipt.get("skipped") or [])
    errors = list(receipt.get("errors") or [])
    failed = [item for item in added if not item.get("verified")]
    if added:
        sync_refresh.invalidate("WebUI 中间层上传完成，需重新刷新后再写")
    _progress(on_progress, 1, 1, "上传完成")
    return {
        "tab_name": target.get("tab_name") or "直播记录",
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "profile": "lives",
        "added": len(added),
        "updated": 0,
        "skipped": len(skipped),
        "duplicates": len(skipped),
        "failed_verify": len(failed),
        "error": (errors[0].get("error") if errors else ""),
        "record_ids": [str(item.get("record_id") or "") for item in added][:20],
        "summary": _live_summary(len(added), len(skipped), len(failed), errors),
        "note": "仅新增直播记录；人工列（AI 摘要 / 截图 / 标签 / 备注）不会被覆盖。",
    }


def _summary(added, updated, skipped, failed, missing) -> str:
    if not added and not updated:
        return ("未写入：云端已存在相同记录" if skipped
                else "未写入任何记录")
    parts = [f"新增 {added} 条"]
    if updated:
        parts.append(f"追加 {updated} 条")
    if skipped:
        parts.append(f"跳过 {skipped} 条")
    if failed:
        parts.append(f"{failed} 条读回校验未通过")
    if missing:
        parts.append(f"{missing} 张本地图片缺失")
    return "，".join(parts)


def _live_summary(added, skipped, failed, errors) -> str:
    if not added:
        if skipped:
            return "未写入：云端已存在相同直播记录"
        if errors:
            return f"未写入：{errors[0].get('error') or '记录无法写入'}"
        return "未写入任何记录"
    text = f"新增 {added} 条直播记录"
    if skipped:
        text += f"，跳过 {skipped} 条重复"
    if failed:
        text += f"，{failed} 条读回校验未通过"
    return text


def _item(profile: str, row: dict, index: int) -> dict:
    if profile == "lives":
        _label, url = tables.split_link(row.get("直播链接1"))
        flag = str(row.get("_dup") or "")
        if not flag and row.get("_needs_review"):
            flag = "需人工确认"
        return {"key": _row_key(profile, row, index),
                "title": str(row.get("直播标题") or ""),
                "time": str(row.get("开始时间") or ""),
                "account": str(row.get("直播账户") or ""),
                "platform": str(row.get("直播平台") or ""),
                "link": str(url or ""), "images": 0, "flag": flag}
    if profile == "songs":
        return {"key": _row_key(profile, row, index),
                "title": str(row.get("歌曲名称") or ""),
                "time": str(row.get("首发日期") or ""),
                "account": str(row.get("演唱者") or ""),
                "platform": "音乐", "link": "",
                "images": len(row.get("_images") or []), "flag": ""}
    if profile == "events":
        return {"key": _row_key(profile, row, index),
                "title": str(row.get("活动名称") or ""),
                "time": str(row.get("活动日期") or ""),
                "account": str(row.get("城市") or ""),
                "platform": "活动",
                "link": _link_text(row.get("购票链接 1") or row.get("购票链接1")),
                "images": len(row.get("_images") or []),
                "flag": str(row.get("_source") or "")}
    if profile == "merch":
        return {"key": _row_key(profile, row, index),
                "title": str(row.get("周边名称") or ""),
                "time": str(row.get("首发日期") or ""),
                "account": str(row.get("获取方式") or ""),
                "platform": "周边", "link": "",
                "images": len(row.get("_images") or []),
                "flag": str(row.get("_source") or "")}
    link = _link_text(row.get("作品链接") or row.get("动态链接(b站)"))
    return {"key": _row_key(profile, row, index),
            "title": str(row.get("作品标题") or ""),
            "time": str(row.get("发布时间") or ""),
            "account": str(row.get("发布账号") or ""),
            "platform": str(row.get("发布平台") or ""),
            "link": link,
            "images": len(row.get("_images") or []),
            "flag": ""}


def _row_key(profile: str, row: dict, index: int) -> str:
    if profile == "lives":
        return pending.live_key(row)
    if profile == "songs":
        title = str(row.get("歌曲名称") or "").strip()
        return f"song:{title}" if title else f"row:{index}"
    if profile == "events":
        return "event:{}|{}|{}".format(row.get("活动名称") or "",
                                       row.get("活动日期") or "",
                                       row.get("城市") or "")
    if profile == "merch":
        return "merch:" + str(row.get("周边名称") or index)
    link = _link_text(row.get("作品链接") or row.get("动态链接(b站)"))
    return pending.link_key(link) or f"row:{index}"


def _link_text(value) -> str:
    _label, url = tables.split_link(value)
    return str(url or "")


def _stats(raw: dict) -> dict:
    return {
        "local_items": int(raw.get("local_items") or 0),
        "local_groups": int(raw.get("local_groups") or 0),
        "synced_groups": int(raw.get("synced_groups") or 0),
        "filtered_partner": int(raw.get("filtered_partner") or 0),
        "pending": int(raw.get("pending") or 0),
        "with_images": int(raw.get("with_images") or 0),
        "image_count": int(raw.get("image_count") or 0),
        "remote_rows": int(raw.get("remote_rows") or 0),
        "since": str(raw.get("since") or ""),
    }


def _find_target(doc_key, tab_id) -> dict:
    doc_key = str(doc_key or "").strip()
    tab_id = str(tab_id or "").strip()
    if not doc_key or not tab_id:
        raise ValueError("请先选择要同步的表格")
    for item in bridge.list_targets():
        if item["doc_key"] == doc_key and item["tab_id"] == tab_id:
            return item
    raise ValueError("该表不在本地已同步列表中，请先在桌面端「表格同步」拉取")


def _accounts(value):
    """账号过滤：all → 不过滤；default → 只比对腾讯表出现过的账号。"""
    return None if str(value or "default") == "all" else "default"


def _days(value) -> int:
    try:
        days = int(value or 30)
    except (TypeError, ValueError):
        days = 30
    return max(1, min(days, 3650))


def _client(target: dict) -> LocalImportClient:
    cookie = _read_cookie()
    if not cookie:
        raise ValueError("尚未登录，无法写入；请先在桌面端完成登录后重试")
    return LocalImportClient(ManagedAuth(lambda: cookie), target["doc_key"],
                             target["tab_id"])


def _read_cookie() -> str:
    import auth as auth_module
    return auth_module.get_cookie("tcdoc_openapi") or ""


def _check_cancelled(is_cancelled) -> None:
    if is_cancelled and is_cancelled():
        raise BridgeCancelled("任务已取消")


def _progress(on_progress, done, total, text) -> None:
    if on_progress:
        on_progress(done, total, text)
