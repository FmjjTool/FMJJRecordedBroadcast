"""腾讯文档 WebUI 宣传图追加（L3）：仅限「线下活动」的「宣传图」列。

范围硬编码为《封茗囧菌数据汇总库》的「线下活动」（ss_e1kkm2）。只向**已存在**
的活动行追加本地 `cover/` 与 `images/` 里云端还没有的图片；不改其它字段、
不删已有图片、不改表结构。追加走 `import_pending.run(profile="events")`
同一套实现（上传图片 → update_image_field → 读回核对）。
"""

from __future__ import annotations

from toolbox import sync_refresh
from toolbox.tcdoc import bridge
from toolbox.tcdoc import import_pending as pending
from toolbox.tcdoc.import_local import (ImportErrorDetail, LocalImportClient,
                                        ManagedAuth)

DOC_NAME = "封茗囧菌数据汇总库"
DOC_KEY = "DRnd3bkhkQUR1aEJr"
TAB_ID = "ss_e1kkm2"
TAB_NAME = "线下活动"
MAX_ITEMS = 50


def status() -> dict:
    allowed, message = sync_refresh.upload_allowed()
    return {
        "doc_name": DOC_NAME,
        "tab_name": TAB_NAME,
        "tab_id": TAB_ID,
        "max_items": MAX_ITEMS,
        "refresh": {"allowed": bool(allowed), "message": str(message)},
        "bypass": bool(sync_refresh.bypass_enabled()),
        "note": "只向已存在的线下活动行追加「宣传图」里缺的本地图片；"
                "不改其它字段、不删或替换已有图片、不改表结构。"
                "需要先「同步前刷新」（或桌面端免校对模式）。",
    }


def preview(days: int = 365, query: str = "", limit: int = 200) -> dict:
    """列出现有活动里还能追加哪些本地宣传图（只读，读云端当前行）。"""
    days = _days(days)
    query = str(query or "").strip().lower()
    limit = max(1, min(int(limit or 200), 500))
    result = bridge.build_event_pending(days=days)
    groups = list(result.get("existing") or [])
    if not groups:
        return {"doc_name": DOC_NAME, "tab_name": TAB_NAME, "tab_id": TAB_ID,
                "items": [], "total": 0, "shown": 0, "images": 0,
                "synced": 0, "refresh": _refresh(),
                "note": "本地没有「已存在且有本地宣传图」的线下活动。"}
    records = pending.event_records_from_rows(
        [{**row, "_existing": True} for row in groups])
    client = _client()
    live = pending._event_live_index(client.get_records(), DOC_KEY, TAB_ID)
    kept = pending.append_candidates(records, live, DOC_KEY, TAB_ID)
    items = []
    for record in kept:
        columns = record.get("columns") or {}
        name = str(columns.get("活动名称") or "")
        date = str(columns.get("活动日期") or "")
        city = str(columns.get("城市") or "")
        haystack = (name + " " + date + " " + city).lower()
        if query and query not in haystack:
            continue
        images = record.get("_images") or []
        items.append({
            "key": record["key"],
            "name": name,
            "date": date,
            "city": city,
            "record_id": record.get("_record_id") or "",
            "existing_images": int(record.get("_existing_images") or 0),
            "add_count": len(images),
            "add_names": [image.upload_name for image in images],
            "blocked": str(record.get("blocked") or ""),
        })
    total = len(items)
    return {
        "doc_name": DOC_NAME,
        "tab_name": TAB_NAME,
        "tab_id": TAB_ID,
        "items": items[:limit],
        "total": total,
        "shown": min(total, limit),
        "images": sum(item["add_count"] for item in items),
        "synced": max(0, len(records) - len(kept)),
        "refresh": _refresh(),
        "note": "只读预览：对比云端当前行与本地 cover / images，"
                "已在表里的同名图片不会再传；补齐过的活动不会列出。",
    }


def apply(keys: list, days: int = 365, confirmed: bool = False) -> dict:
    """按活动键追加上面预览过的图片：上传 → 只更新该行「宣传图」→ 读回。"""
    if not confirmed:
        raise ValueError("缺少追加确认")
    wanted = []
    for item in keys or []:
        key = str(item or "").strip()
        if key and key not in wanted:
            wanted.append(key)
    if not wanted:
        raise ValueError("请选择要追加宣传图的活动")
    if len(wanted) > MAX_ITEMS:
        raise ValueError(f"单次最多追加 {MAX_ITEMS} 场")
    _ensure_gate()
    days = _days(days)
    result = bridge.build_event_pending(days=days)
    groups = list(result.get("existing") or [])
    if not groups:
        raise ValueError("本地没有可追加宣传图的线下活动，请重新预览")
    records = pending.event_records_from_rows(
        [{**row, "_existing": True} for row in groups])
    client = _client()
    live = pending._event_live_index(client.get_records(), DOC_KEY, TAB_ID)
    kept = pending.append_candidates(records, live, DOC_KEY, TAB_ID)
    by_key = {record["key"]: record for record in kept}
    selected = [by_key[key] for key in wanted if key in by_key]
    missing = [key for key in wanted if key not in by_key]
    if missing:
        raise ValueError(
            f"有 {len(missing)} 场活动已补齐或已变化（可能已被他人补图），"
            "请重新预览后再试")
    blocked = [record for record in selected if record.get("blocked")]
    if blocked:
        raise ValueError(str(blocked[0]["blocked"]))
    plan_images = sum(len(record.get("_images") or []) for record in selected)
    try:
        with pending._write_lock():
            receipt = pending.run(client, selected, apply=True,
                                  verify_each=False, profile="events",
                                  source="WebUI 追加宣传图")
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc
    appended = list(receipt.get("appended") or [])
    failed = [item for item in appended if not item.get("verified")]
    if appended:
        sync_refresh.invalidate("WebUI 追加宣传图完成，需重新刷新后再写")
    return {
        "doc_name": DOC_NAME,
        "tab_name": TAB_NAME,
        "tab_id": TAB_ID,
        "events": len(appended),
        "planned_images": plan_images,
        "uploaded_images": int(receipt.get("uploaded_images") or 0),
        "failed_verify": len(failed),
        "record_ids": [str(item.get("record_id") or "") for item in appended][:20],
        "titles": [str(item.get("title") or "") for item in appended][:20],
        "summary": ("已追加 %d 场 · 上传 %d 张宣传图并读回核对" % (
            len(appended), int(receipt.get("uploaded_images") or 0))
            if not failed else
            "已追加 %d 场，其中 %d 场读回不一致，请到表格核对" % (
                len(appended), len(failed))),
        "note": "只更新这些行的「宣传图」列；其它字段与已有图片未改动。",
    }


def _refresh() -> dict:
    allowed, message = sync_refresh.upload_allowed()
    return {"allowed": bool(allowed), "message": str(message),
            "bypass": bool(sync_refresh.bypass_enabled())}


def _ensure_gate() -> None:
    allowed, message = sync_refresh.upload_allowed()
    if not allowed:
        raise ValueError(
            f"当前数据未刷新，暂不允许追加宣传图：{message}。"
            "请先「同步前刷新」，或在桌面端开启免校对模式。")


def _days(days) -> int:
    try:
        value = int(days or 365)
    except (TypeError, ValueError):
        value = 365
    return max(1, min(value, 3650))


def _client() -> LocalImportClient:
    cookie = _read_cookie()
    if not cookie:
        raise ValueError("尚未登录腾讯文档，请先在桌面端完成登录后重试")
    try:
        client = LocalImportClient(ManagedAuth(cookie), DOC_KEY, TAB_ID)
        client.connect()
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc
    return client


def _read_cookie() -> str:
    import auth as auth_module
    return auth_module.get_cookie("tcdoc_openapi") or ""
