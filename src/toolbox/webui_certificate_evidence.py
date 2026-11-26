"""证书记录的 Web 用例：仅选中记录导出，不接受客户端文件路径。"""

from __future__ import annotations

import ntpath

from toolbox import certificate_evidence


def overview() -> dict:
    return certificate_evidence.list_exports(
        output_root=certificate_evidence.configured_output_root())


def export(payload: dict, *, is_cancelled=lambda: False,
           on_progress=lambda done, total: None) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"id", "ids", "password", "confirmed"}:
        raise ValueError("导出参数无效，请重新选择证书记录")
    if payload.get("confirmed") is not True:
        raise ValueError("请先确认导出所选证书记录")
    if ("id" in payload) == ("ids" in payload):
        raise ValueError("请选择需要导出的证书记录")
    record_id = payload.get("ids") if "ids" in payload else payload.get("id")
    ids = record_id if "ids" in payload else [record_id]
    if (not isinstance(ids, list) or not ids
            or len(ids) > certificate_evidence.MAX_BATCH_RECORDS):
        raise ValueError(f"每次请选择 1–{certificate_evidence.MAX_BATCH_RECORDS} 条证书记录")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 256
           or ntpath.isabs(value) or any(character in value for character in "/\\:\0")
           for value in ids):
        raise ValueError("所选证书记录标识无效，请刷新后重试")
    if len(set(ids)) != len(ids):
        raise ValueError("所选证书记录重复，请重新选择")
    password = payload.get("password")
    if not isinstance(password, str):
        raise ValueError("请输入导出密码")
    return certificate_evidence.bundle_bytes(
        record_id, password, output_root=certificate_evidence.configured_output_root(),
        is_cancelled=is_cancelled, on_progress=on_progress)
