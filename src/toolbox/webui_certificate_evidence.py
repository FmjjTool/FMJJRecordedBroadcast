"""证书记录的 Web 用例：仅选中记录导出，不接受客户端文件路径。"""

from __future__ import annotations

from toolbox import certificate_evidence


def overview() -> dict:
    return certificate_evidence.list_exports(
        output_root=certificate_evidence.configured_output_root())


def export(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"id", "password", "confirmed"}:
        raise ValueError("导出参数无效，请重新选择证书记录")
    if payload.get("confirmed") is not True:
        raise ValueError("请先确认导出所选证书记录")
    record_id = payload.get("id")
    password = payload.get("password")
    if not isinstance(record_id, str) or not record_id or len(record_id) > 128:
        raise ValueError("请选择一条证书记录")
    if not isinstance(password, str):
        raise ValueError("请输入导出密码")
    return certificate_evidence.bundle_bytes(
        record_id, password, output_root=certificate_evidence.configured_output_root())
