"""本地表格逐行比对与回写用例，供桌面及 WebUI 共用（Qt-free）。

只操作已核实文档中的官方动态 / 直播记录，不删除记录、不改表结构。
比对及选中状态只在内存中保存；远端数据由 openapi.TdClient 唯一写入，
刷新状态仍归 sync_refresh、互斥仍归 import_pending，不新增数据存储。
空白本地单元格不清空云端；更新仅包含存在差异的已映射字段。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
import re
from typing import Callable

from toolbox.tcdoc import openapi

WRITABLE_OPS = frozenset({"新增", "更新"})
CLOUD_EDITABLE_TYPES = openapi.SUPPORTED_TYPES | {7, 16}
MAX_CLOUD_EDIT_RECORDS = 300


class MappingError(ValueError):
    """需要用户修正数据或重新比对；可能附带已完成行的 receipt。"""


class MappingWriteUnknown(MappingError):
    """写请求或读回结果无法确认；禁止自动重试。"""


def _name(value) -> str:
    return re.sub(r"[\s_\-（）()]+", "", str(value or "")).casefold()


def suggest_mapping(headers: list[str], fields: list[dict]) -> dict[str, str]:
    """只自动匹配唯一同名列，不猜测相似字段、不重复使用目标字段。"""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for field in fields:
        if (field.get("fieldID") and field.get("fieldTitle")
                and int(field.get("fieldType") or 0) in openapi.SUPPORTED_TYPES | {7, 16}):
            by_name[_name(field["fieldTitle"])].append(field)
    result: dict[str, str] = {}
    used: set[str] = set()
    for header in headers:
        matches = by_name.get(_name(header), [])
        if len(matches) == 1:
            fid = str(matches[0]["fieldID"])
            if fid not in used:
                result[header] = fid
                used.add(fid)
    return result


def suggest_key(headers: list[str], mapping: dict[str, str],
                field_by_id: dict[str, dict]) -> str:
    """优先稳定链接 / ID，其次标题；不以标题默默覆盖已有同名行。"""
    candidates = [header for header in headers
                  if mapping.get(header) in field_by_id]

    def rank(header):
        field = field_by_id[mapping[header]]
        name = _name(header)
        title = _name(field.get("fieldTitle"))
        if name in {"id", "recordid", "记录id", "作品id", "动态id", "直播id",
                    "唯一id", "唯一标识", "唯一编号"}:
            return 0
        if int(field.get("fieldType") or 0) == 8:
            return 1 if any(word in title for word in ("作品链接", "直播链接", "动态链接")) else 2
        if any(word in name for word in ("bvid", "statusid", "videoid")):
            return 2
        if any(word in name for word in ("链接", "url", "link")):
            return 3
        if any(word in name for word in ("标题", "名称", "name", "title")):
            return 5
        return 6

    return min(candidates, key=rank) if candidates else ""


def _value_key(field: dict, value) -> str:
    # 链接的可见标题可以不同，记录身份必须按实际地址比较。
    if int(field.get("fieldType") or 0) == 8:
        if isinstance(value, list):
            links = [str(item.get("link") or "").strip()
                     for item in value if isinstance(item, dict)]
            if any(links):
                return "|".join(link for link in links if link)
        if isinstance(value, dict) and value.get("link"):
            return str(value["link"]).strip()
    return openapi.value_key(field, value)


def _encode(field: dict, raw):
    try:
        return openapi.encode_value(field, raw)
    except (TypeError, ValueError, OverflowError):
        return False, f"「{field.get('fieldTitle') or '该字段'}」的值无法识别"


def _mapping_error(headers, mapping, field_by_id, key_col) -> str:
    if key_col not in headers or not mapping.get(key_col):
        return "请选择已匹配的唯一标识列后重新比对"
    if not mapping:
        return "请先匹配本地列与云端字段"
    targets: set[str] = set()
    titles: set[str] = set()
    for col, fid in mapping.items():
        if not fid:
            continue
        if col not in headers or fid not in field_by_id:
            return "字段匹配已失效，请刷新字段后重新匹配"
        field = field_by_id[fid]
        title = str(field.get("fieldTitle") or "")
        if not title:
            return "云端字段缺少名称，请刷新字段后重试"
        if fid in targets or title in titles:
            return f"多列匹配到同一个云端字段「{title}」，请保留一列后重新比对"
        if sum(str(item.get("fieldTitle") or "") == title
               for item in field_by_id.values()) > 1:
            return f"云端存在同名字段「{title}」，无法安全写入"
        targets.add(fid)
        titles.add(title)
    if mapping.get(key_col) not in field_by_id:
        return "唯一标识列的字段匹配已失效，请重新匹配"
    return ""


def _field_signature(mapping, field_by_id):
    return {fid: deepcopy({key: field_by_id[fid].get(key) for key in
                           ("fieldID", "fieldTitle", "fieldType", "_options")})
            for fid in mapping.values() if fid in field_by_id}


def _record_field_value(record, field):
    values = record.get("values") or {}
    title = str(field.get("fieldTitle") or "")
    fid = str(field.get("fieldID") or "")
    return values.get(title, values.get(fid))


def prepare_cloud_updates(field_by_id: dict[str, dict], records: list[dict],
                          updates: list[dict]) -> list[dict]:
    """把用户暂存的云端单元格编辑变成按 recordID 锁定的预览计划。"""
    if not isinstance(updates, list) or not updates or len(updates) > MAX_CLOUD_EDIT_RECORDS:
        raise MappingError(f"请选择 1–{MAX_CLOUD_EDIT_RECORDS} 条记录并编辑后再预览")
    fields = {str(fid): field for fid, field in field_by_id.items()}
    title_counts = Counter(str(field.get("fieldTitle") or "") for field in fields.values())
    by_record = defaultdict(list)
    for record in records:
        rid = str(record.get("recordID") or "").strip()
        if rid:
            by_record[rid].append(record)
    seen_records: set[str] = set()
    plans = []
    for update in updates:
        if not isinstance(update, dict):
            raise MappingError("修改预览格式无效，请重新选择记录")
        rid = str(update.get("record_id") or "").strip()
        patch = update.get("values")
        if not rid or rid in seen_records or not isinstance(patch, dict) or not patch:
            raise MappingError("修改预览包含空记录、重复记录或空字段，请重新选择")
        seen_records.add(rid)
        matches = by_record.get(rid, [])
        if len(matches) != 1:
            raise MappingError("所选记录已不存在或记录 ID 不唯一，请刷新云端数据")
        record = matches[0]
        values = {}
        snapshot = {}
        fields_used = {}
        changes = []
        for raw_fid, raw in patch.items():
            fid = str(raw_fid or "").strip()
            field = fields.get(fid)
            title = str((field or {}).get("fieldTitle") or "").strip()
            if (field is None or not title
                    or int(field.get("fieldType") or 0) not in CLOUD_EDITABLE_TYPES):
                raise MappingError("所选字段不支持回写，请刷新字段并重新编辑")
            if title_counts[title] != 1:
                raise MappingError(f"云端存在同名字段「{title}」，无法安全回写")
            ok, encoded = _encode(field, raw)
            if not ok:
                raise MappingError(str(encoded))
            old = _record_field_value(record, field)
            if encoded is None:
                if openapi.display_value(field, old).strip():
                    raise MappingError(f"「{title}」不能用空值清除，请保留原内容后再预览")
                continue
            before_key = _value_key(field, old)
            if before_key == _value_key(field, encoded):
                continue
            values[title] = encoded
            snapshot[fid] = before_key
            fields_used[fid] = field
            changes.append({
                "field_id": fid, "field": title,
                "remote": openapi.display_value(field, old),
                "local": openapi.display_value(field, encoded),
            })
        if values:
            plans.append({
                "record_id": rid, "values": values, "changes": changes,
                "_snapshot": snapshot,
                "_fields": _field_signature(
                    {fid: fid for fid in fields_used}, fields),
            })
    if not plans:
        raise MappingError("没有检测到可回写的字段变化")
    return plans


def _cloud_edit_fingerprint(action):
    return {key: action.get(key) for key in
            ("record_id", "values", "changes", "_snapshot", "_fields")}


def compare(headers: list[str], rows: list[dict], mapping: dict[str, str],
            field_by_id: dict[str, dict], key_col: str,
            records: list[dict]) -> list[dict]:
    """逐本地行给出新增 / 更新 / 不变 / 错误，不产生任何写入。"""
    error = _mapping_error(headers, mapping, field_by_id, key_col)
    if error:
        return [{"row_no": pos, "op": "错误", "key": "",
                 "record_id": "", "values": {}, "changes": [],
                 "message": error} for pos, _ in enumerate(rows, 1)]
    key_field = field_by_id[mapping[key_col]]
    key_title = key_field["fieldTitle"]
    remote_index: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        key = _value_key(key_field, (record.get("values") or {}).get(key_title))
        if key:
            remote_index[key].append(record)
    normalized_keys = []
    for row in rows:
        ok, value = _encode(key_field, row.get(key_col))
        normalized_keys.append(_value_key(key_field, value) if ok and value is not None else "")
    counts = Counter(key for key in normalized_keys if key)
    signature = _field_signature(mapping, field_by_id)
    actions = []
    for pos, (row, key) in enumerate(zip(rows, normalized_keys), 1):
        base = {"row_no": pos, "key": key or str(row.get(key_col) or "").strip(),
                "record_id": "", "values": {}, "changes": [],
                "_fields": signature}
        errors = []
        if not key:
            errors.append("唯一标识为空或格式无法识别")
        elif counts[key] > 1:
            errors.append(f"本地存在 {counts[key]} 行相同标识，请先核对重复行")
        elif len(remote_index.get(key, [])) > 1:
            errors.append("云端存在多行相同标识，无法确定要匹配的记录")
        values = {}
        by_title = {}
        for col, fid in mapping.items():
            if not fid:
                continue
            field = field_by_id[fid]
            ok, encoded = _encode(field, row.get(col))
            if not ok:
                errors.append(str(encoded))
            elif encoded is not None:
                values[field["fieldTitle"]] = encoded
                by_title[field["fieldTitle"]] = field
        if errors:
            actions.append({**base, "op": "错误", "message": "；".join(errors)})
            continue
        record = (remote_index.get(key) or [None])[0]
        existing = (record.get("values") or {}) if record else {}
        changes = []
        changed = {}
        snapshot = {}
        for title, encoded in values.items():
            field = by_title[title]
            current = existing.get(title)
            snapshot[title] = _value_key(field, current)
            if record is None or snapshot[title] != _value_key(field, encoded):
                changed[title] = encoded
                changes.append({"field": title,
                                "local": openapi.display_value(field, encoded),
                                "remote": openapi.display_value(field, current)})
        if record is None:
            actions.append({**base, "op": "新增", "values": values,
                            "changes": changes, "_snapshot": snapshot,
                            "message": "云端没有相同标识，可新增这一行"})
        elif not record.get("recordID"):
            actions.append({**base, "op": "错误",
                            "message": "匹配记录缺少记录 ID，无法安全写入"})
        elif changed:
            actions.append({**base, "op": "更新", "record_id": str(record["recordID"]),
                            "values": changed, "changes": changes, "_snapshot": snapshot,
                            "message": "仅更新：" + "、".join(changed)})
        else:
            actions.append({**base, "op": "不变", "record_id": str(record["recordID"]),
                            "_snapshot": snapshot, "message": "已存在且内容一致，自动跳过"})
    return actions


def _fingerprint(action):
    return {key: action.get(key) for key in
            ("row_no", "op", "key", "record_id", "values", "changes", "_snapshot", "_fields")}


def _check_cancelled(is_cancelled, receipt):
    if is_cancelled and is_cancelled():
        exc = MappingError(f"已取消（已写入 {receipt['added'] + receipt['updated']} 行）")
        receipt["cancelled"] = True
        exc.receipt = _public_receipt(receipt)
        raise exc


def _public_receipt(receipt):
    return deepcopy({key: value for key, value in receipt.items() if not key.startswith("_")})


def _ensure_gate():
    from toolbox import sync_refresh
    allowed, reason = sync_refresh.upload_allowed()
    if not allowed:
        raise MappingError(f"暂不允许写入：{reason}。请先在中间层完成「同步前刷新」。")


def _read_cloud_state(client, file_id, sheet_id, log):
    try:
        fields = client.get_fields(file_id, sheet_id)
        records = client.get_records(file_id, sheet_id, log)
    except Exception:
        # 读请求失败没有使前面已验证的写入变成未知；外层统一附上已完成回执。
        # 原始响应可能含平台数据，不传给界面、日志或异常链。
        raise MappingError("读取云端字段或记录失败，已停止；请检查网络后重新比对") from None
    return fields, records


@contextmanager
def _selected_write_lock(receipt):
    from toolbox import sync_refresh
    from toolbox.tcdoc import import_pending
    with import_pending._write_lock():
        try:
            yield
        finally:
            if receipt.pop("_attempted", False):
                # 必须在释放跨进程锁之前作废，不能让下一写入方抢到旧刷新记录。
                sync_refresh.invalidate("映射回写已提交，需重新刷新后再写")


def execute_selected(client, file_id: str, sheet_id: str, headers: list[str],
                     rows: list[dict], mapping: dict[str, str],
                     field_by_id: dict[str, dict], key_col: str,
                     preview_actions: list[dict], selected_row_nos: list[int],
                     log: Callable[[str], None], is_cancelled=None) -> dict:
    """显式选行 → 锁内实时比对 → 精确计划核对 → 逐行写入与读回。"""
    client.validate_write_target(file_id, sheet_id)
    if sheet_id not in openapi.WRITABLE_TAB_IDS:
        raise MappingError("该子表不允许映射回写")
    if not isinstance(selected_row_nos, (list, tuple, set)) or not selected_row_nos:
        raise MappingError("请先勾选要写入的差异行")
    if any(isinstance(number, bool) or not isinstance(number, int)
           or number < 1 or number > len(rows) for number in selected_row_nos):
        raise MappingError("选择的本地行无效，请重新比对后再选")
    selected = sorted(set(selected_row_nos))
    previews = {item.get("row_no"): item for item in preview_actions}
    if len(previews) != len(preview_actions):
        raise MappingError("预览包含重复行号，请重新比对")
    for number in selected:
        if previews.get(number, {}).get("op") not in WRITABLE_OPS:
            raise MappingError(f"本地第 {number} 行不可写入，请重新比对并选择差异行")
    receipt = {"added": 0, "updated": 0, "verified": True,
               "record_ids": [], "row_nos": [], "unknown_record_outcome": False,
               "cancelled": False}
    _check_cancelled(is_cancelled, receipt)
    _ensure_gate()
    try:
        with _selected_write_lock(receipt):
            _ensure_gate()
            _check_cancelled(is_cancelled, receipt)
            fields, records = _read_cloud_state(client, file_id, sheet_id, log)
            current_fields = {str(item["fieldID"]): item for item in fields}
            fresh = {item["row_no"]: item for item in compare(
                headers, rows, mapping, current_fields, key_col, records)}
            for number in selected:
                if _fingerprint(previews[number]) != _fingerprint(fresh.get(number, {})):
                    raise MappingError(f"本地第 {number} 行的匹配或云端内容已变化，未写入；请重新比对后确认")
            key_field = current_fields[mapping[key_col]]
            by_title = {item["fieldTitle"]: item for item in current_fields.values()}
            for number in selected:
                _check_cancelled(is_cancelled, receipt)
                if receipt["row_nos"]:
                    # 跨进程锁只约束本软件，云端其它编辑者仍可在两行之间改数据。
                    # 后续行写前再次读取，不能覆盖用户确认后才发生的变动。
                    latest_fields, latest_records = _read_cloud_state(
                        client, file_id, sheet_id, log)
                    latest_by_id = {str(item["fieldID"]): item for item in latest_fields}
                    latest = compare(headers, rows, mapping, latest_by_id, key_col, latest_records)
                    current = next((item for item in latest if item["row_no"] == number), {})
                    if _fingerprint(previews[number]) != _fingerprint(current):
                        raise MappingError(
                            f"本地第 {number} 行的匹配或云端内容已变化，已停止后续写入；请重新比对后确认")
                    fresh[number] = current
                action = fresh[number]
                values = deepcopy(action["values"])
                if not values:
                    raise MappingError(f"本地第 {number} 行没有可写字段，请重新比对")
                # 云端读取可能阻塞；读取期间收到取消时不能继续提交下一行。
                _check_cancelled(is_cancelled, receipt)
                receipt["_attempted"] = True
                try:
                    if action["op"] == "新增":
                        client.add_records(file_id, sheet_id, [{"values": values}])
                        receipt["added"] += 1
                    else:
                        client.update_records(file_id, sheet_id, [{
                            "recordID": action["record_id"], "values": values}])
                        receipt["updated"] += 1
                    saved_rows = client.get_records(file_id, sheet_id, log)
                    if action["op"] == "更新":
                        matches = [row for row in saved_rows
                                   if str(row.get("recordID") or "") == action["record_id"]]
                    else:
                        matches = [row for row in saved_rows if _value_key(
                            key_field, (row.get("values") or {}).get(key_field["fieldTitle"])) == action["key"]]
                    if len(matches) != 1 or not matches[0].get("recordID"):
                        raise MappingWriteUnknown("写后无法唯一定位记录")
                    saved = matches[0]
                    rid = str(saved["recordID"])
                    receipt["record_ids"].append(rid)
                    for title, expected in values.items():
                        if _value_key(by_title[title], (saved.get("values") or {}).get(title)) != _value_key(by_title[title], expected):
                            raise MappingWriteUnknown(f"「{title}」读回内容与预期不一致")
                except Exception as exc:
                    receipt["verified"] = False
                    receipt["unknown_record_outcome"] = True
                    unknown = MappingWriteUnknown(
                        f"本地第 {number} 行写入结果未能确认，已停止且不会自动重试；请到云端核对后重新比对")
                    unknown.receipt = _public_receipt(receipt)
                    raise unknown from exc
                receipt["row_nos"].append(number)
                log(f"本地第 {number} 行已{action['op']}并读回一致（{len(receipt['row_nos'])}/{len(selected)}）")
    except MappingError as exc:
        if not hasattr(exc, "receipt"):
            exc.receipt = _public_receipt(receipt)
        raise
    return receipt
