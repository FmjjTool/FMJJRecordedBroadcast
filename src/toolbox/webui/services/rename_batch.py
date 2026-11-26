"""批量改名 WebUI 适配（L2）：预览与执行改名。"""

from __future__ import annotations

from toolbox import webui_rename_batch
from toolbox.webui_rename_batch import FIELDS


def _options(payload: dict) -> dict:
    """按中间层的字段表整表透传，不逐个点名，避免漏字段。"""
    payload = payload or {}
    return {name: payload.get(name) for name in FIELDS}


class RenameBatchService:
    def overview(self) -> dict:
        return webui_rename_batch.overview()

    def preview(self, payload: dict) -> dict:
        payload = payload or {}
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("请填写要扫描的文件夹")
        return webui_rename_batch.preview(path, _options(payload))

    def execute(self, payload: dict) -> dict:
        payload = payload or {}
        if not payload.get("confirmed"):
            raise ValueError("缺少执行确认")
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("请填写要扫描的文件夹")
        plan = payload.get("plan")
        if plan is not None and not isinstance(plan, list):
            raise ValueError("执行计划格式错误")
        if isinstance(plan, list) and len(plan) > 2000:
            raise ValueError("执行计划超过 2000 条")
        return webui_rename_batch.execute(path, _options(payload), plan)
