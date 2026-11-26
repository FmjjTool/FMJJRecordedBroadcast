"""腾讯文档 WebUI 编辑用例（L3）：白名单两表的行级修改与删除。

范围硬编码为《封茗囧菌数据汇总库》的「官方动态」（ss_h2uh2s）与
「直播记录」（ss_1xgstm），按 recordID 逐条操作，不碰表结构、不批量覆盖。
每次写入前后都由 `LocalImportClient` 的 `validate_target` 校验白名单，
并与桌面上传共用 `import_pending` 的写入锁。

关于「同步前刷新」门禁：该门禁约束的是把**本地采集数据**写进表格时的
新鲜度；本模块只改云端已有行、预览时实时读取云端记录，不存在陈旧本地
数据，因此不套用刷新门禁，改为「执行前重新读取云端记录、执行后读回核对」。
"""

from __future__ import annotations

from toolbox import sync_refresh
from toolbox.tcdoc import import_pending as pending
from toolbox.tcdoc.import_local import (ImportErrorDetail, LocalImportClient,
                                        ManagedAuth, UnknownWriteResult)
from toolbox.tcdoc.openapi import display_value, encode_value, normalize_fields

DOC_NAME = "封茗囧菌数据汇总库"
TARGETS = {
    "official": {
        "name": "官方动态",
        "doc_key": "DRnd3bkhkQUR1aEJr",
        "tab_id": "ss_h2uh2s",
        "keys": ("作品标题", "发布时间", "发布账号", "发布平台"),
    },
    "live": {
        "name": "直播记录",
        "doc_key": "DRnd3bkhkQUR1aEJr",
        "tab_id": "ss_1xgstm",
        "keys": ("直播标题", "开始时间", "直播账户", "直播平台"),
    },
}
EDITABLE_TYPES = (1, 7, 16)   # 文本类字段
MAX_ROWS = 300
MAX_DELETE = 20


def status() -> dict:
    allowed, message = sync_refresh.upload_allowed()
    return {
        "doc_name": DOC_NAME,
        "targets": [{"key": key, "name": item["name"],
                     "tab_id": item["tab_id"]}
                    for key, item in TARGETS.items()],
        "max_delete": MAX_DELETE,
        "refresh": {"allowed": bool(allowed), "message": str(message)},
        "bypass": bool(sync_refresh.bypass_enabled()),
        "note": "只允许「官方动态」「直播记录」的行级修改与删除，按 recordID "
                "逐条操作；不碰表结构、不改下拉选项。修改仅限文本列，"
                "删除与修改都需要预览 + 二次确认，写后读回核对；"
                "与上传同样需要「同步前刷新」门禁（或桌面端免校对模式）。",
    }


def preview(tab: str, query: str = "", limit: int = MAX_ROWS) -> dict:
    """读取云端当前记录（实时），返回可编辑 / 可删除的行。"""
    target = _target(tab)
    query = str(query or "").strip().lower()
    limit = max(1, min(int(limit or MAX_ROWS), MAX_ROWS))
    client = _client(target)
    fields, records = _read_all(client)
    text_fields = [field for field in fields
                   if int(field.get("fieldType") or 0) in EDITABLE_TYPES]
    by_id = {str(field.get("fieldID")): field for field in fields}
    rows = []
    for record in records:
        values = record.get("values") or {}
        summary = {}
        for title in target["keys"]:
            field = _by_title(fields, title)
            summary[title] = display_value(field, values.get(by_id_of(field))) \
                if field else ""
        texts = {}
        for field in text_fields:
            title = str(field.get("fieldTitle") or "")
            if not title:
                continue
            texts[title] = display_value(field, values.get(
                str(field.get("fieldID"))))
        haystack = " ".join([str(record.get("recordID") or "")]
                            + list(summary.values()) + list(texts.values()))
        if query and query not in haystack.lower():
            continue
        rows.append({
            "record_id": str(record.get("recordID") or ""),
            "summary": summary,
            "values": texts,
        })
    total = len(rows)
    return {
        "tab": tab,
        "name": target["name"],
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "fields": [{"title": str(field.get("fieldTitle") or ""),
                    "type": int(field.get("fieldType") or 0)}
                   for field in text_fields
                   if str(field.get("fieldTitle") or "")],
        "rows": rows[:limit],
        "total": total,
        "shown": min(total, limit),
        "query": query,
        "refresh": _refresh_state(),
        "note": "只读预览：读取的是云端当前记录；修改 / 删除都要再确认一次。",
    }


def update(tab: str, record_id: str, values: dict,
           confirmed: bool = False) -> dict:
    """按 recordID 修改指定文本列：编码 → 写入 → 读回核对。"""
    if not confirmed:
        raise ValueError("缺少修改确认")
    rid = str(record_id or "").strip()
    if not rid:
        raise ValueError("请选择要修改的记录")
    if not isinstance(values, dict) or not values:
        raise ValueError("请填写要修改的字段")
    _ensure_gate()
    target = _target(tab)
    client = _client(target)
    fields, records = _read_all(client)
    payload: dict = {}
    planned = []
    for title, raw in values.items():
        title = str(title or "").strip()
        field = _by_title(fields, title)
        if field is None:
            raise ValueError(f"云端没有「{title}」这一列")
        if int(field.get("fieldType") or 0) not in EDITABLE_TYPES:
            raise ValueError(f"「{title}」不是文本列，本页不提供修改")
        ok, encoded = encode_value(field, raw)
        if not ok:
            raise ValueError(str(encoded))
        fid = str(field.get("fieldID"))
        payload[fid] = encoded
        planned.append({"title": title, "field_id": fid,
                        "value": str(raw or "").strip()})
    records = _read_records(client)
    current = _find(records, rid)
    if current is None:
        raise ValueError("该记录已不存在（可能已被删除），请重新读取后再试")
    try:
        with pending._write_lock():
            client.update_record(rid, payload)
    except UnknownWriteResult as exc:
        raise ValueError(f"修改结果不明（{exc}），请到表格核对后再操作") from exc
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc
    after = _find(_read_records(client), rid)
    if after is None:
        raise ValueError("修改后读回不到该记录，请到表格核对")
    values_after = after.get("values") or {}
    changed = []
    verified = True
    for item in planned:
        field = _by_title(fields, item["title"])
        got = display_value(field, values_after.get(item["field_id"]))
        same = got.strip() == item["value"]
        item["after"] = got
        item["ok"] = same
        verified = verified and same
        changed.append(item)
    return {
        "tab": tab,
        "tab_name": target["name"],
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "record_id": rid,
        "updated": len(changed),
        "fields": changed,
        "verified": verified,
        "summary": ("已修改 %d 个字段并读回一致" % len(changed) if verified
                    else "已提交修改，但读回内容与预期不一致，请到表格核对"),
    }


def delete(tab: str, record_ids: list, confirmed: bool = False) -> dict:
    """按 recordID 删除白名单表记录：先校验仍存在，再逐条删除并读回。"""
    if not confirmed:
        raise ValueError("缺少删除确认")
    ids = []
    for item in record_ids or []:
        rid = str(item or "").strip()
        if rid and rid not in ids:
            ids.append(rid)
    if not ids:
        raise ValueError("请选择要删除的记录")
    if len(ids) > MAX_DELETE:
        raise ValueError(f"单次最多删除 {MAX_DELETE} 条")
    _ensure_gate()
    target = _target(tab)
    client = _client(target)
    fields, records = _read_all(client)
    existing = {str(rec.get("recordID") or "") for rec in records}
    missing = [rid for rid in ids if rid not in existing]
    if missing:
        raise ValueError("有记录已不存在（可能已被他人删除），请重新读取后再试")
    titles = {str(rec.get("recordID") or ""): _title_of(rec, fields, target)
              for rec in records
              if str(rec.get("recordID") or "") in ids}
    deleted = []
    try:
        with pending._write_lock():
            for rid in ids:
                client.delete_record(rid)
                deleted.append(rid)
    except UnknownWriteResult as exc:
        raise ValueError(
            f"删除结果不明（{exc}），已删除 {len(deleted)} 条，请到表格核对"
        ) from exc
    except ImportErrorDetail as exc:
        raise ValueError(
            f"删除中断：已删除 {len(deleted)} 条，原因：{exc}") from exc
    remain = {str(rec.get("recordID") or "")
              for rec in _read_records(client)}
    leftover = [rid for rid in ids if rid in remain]
    return {
        "tab": tab,
        "tab_name": target["name"],
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "record_ids": ids,
        "titles": titles,
        "deleted": len(deleted),
        "leftover": leftover,
        "verified": not leftover,
        "summary": (f"已删除 {len(deleted)} 条并读回确认"
                    if not leftover else
                    f"已删除 {len(deleted) - len(leftover)} 条，"
                    f"仍有 {len(leftover)} 条未消失，请到表格核对"),
    }


def _refresh_state() -> dict:
    allowed, message = sync_refresh.upload_allowed()
    return {"allowed": bool(allowed), "message": str(message),
            "bypass": bool(sync_refresh.bypass_enabled())}


def _ensure_gate() -> None:
    """与上传同一条「同步前刷新」门禁；免校对模式下自动放行。"""
    allowed, message = sync_refresh.upload_allowed()
    if not allowed:
        raise ValueError(
            f"当前数据未刷新，暂不允许修改 / 删除：{message}。"
            "请先在桌面端完成「同步前刷新」，或在桌面端开启免校对模式。")


def _read_all(client):
    try:
        return normalize_fields(client.get_fields()), client.get_records()
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc


def _read_records(client):
    try:
        return client.get_records()
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc


def _target(tab: str) -> dict:
    target = TARGETS.get(str(tab or ""))
    if target is None:
        raise ValueError("只支持「官方动态」或「直播记录」")
    return target


def _client(target: dict) -> LocalImportClient:
    cookie = _read_cookie()
    if not cookie:
        raise ValueError("尚未登录腾讯文档，请先在桌面端完成登录后重试")
    try:
        client = LocalImportClient(ManagedAuth(cookie), target["doc_key"],
                                   target["tab_id"])
        client.connect()
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc
    return client


def _read_cookie() -> str:
    import auth as auth_module
    return auth_module.get_cookie("tcdoc_openapi") or ""


def _by_title(fields: list, title: str):
    return next((field for field in fields
                 if str(field.get("fieldTitle") or "") == title), None)


def by_id_of(field) -> str:
    return str(field.get("fieldID") or "") if field else ""


def _find(records: list, record_id: str):
    return next((rec for rec in records
                 if str(rec.get("recordID") or "") == record_id), None)


def _title_of(record: dict, fields: list, target: dict) -> str:
    values = record.get("values") or {}
    for name in target["keys"]:
        field = _by_title(fields, name)
        if field is None:
            continue
        text = display_value(field, values.get(by_id_of(field))).strip()
        if text:
            return text
    return str(record.get("recordID") or "")
