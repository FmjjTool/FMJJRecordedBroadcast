"""批量改名 WebUI 适配（L2）：预览与执行改名。"""

from __future__ import annotations

from toolbox import webui_rename_batch


def _options(payload: dict) -> dict:
    payload = payload or {}
    return {
        "recursive": bool(payload.get("recursive")),
        "filter": payload.get("filter") or "all",
        "template": payload.get("template") or "",
        "prefix": payload.get("prefix") or "",
        "suffix": payload.get("suffix") or "",
        "find": payload.get("find") or "",
        "to": payload.get("to") or "",
        "case": payload.get("case") or "",
        "convert": payload.get("convert") or "",
        "pos_idx": payload.get("pos_idx"),
        "pos_text": payload.get("pos_text") or "",
        "from_right": bool(payload.get("from_right")),
        "textmode": payload.get("textmode") or "",
        "numbering": bool(payload.get("numbering")),
        "start": payload.get("start"),
        "width": payload.get("width"),
        "ignore_ext": payload.get("ignore_ext", True),
        "conflict": payload.get("conflict", True),
    }


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
        return webui_rename_batch.execute(path, _options(payload))
