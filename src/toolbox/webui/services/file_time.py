"""文件时间修改 WebUI 适配（L2）：预览与应用。"""

from __future__ import annotations

from toolbox import webui_file_time


class FileTimeService:
    def overview(self) -> dict:
        return webui_file_time.overview()

    def preview(self, payload: dict) -> dict:
        payload = payload or {}
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("请填写要扫描的文件夹")
        return webui_file_time.preview(path, payload)

    def apply(self, payload: dict) -> dict:
        payload = payload or {}
        if not payload.get("confirmed"):
            raise ValueError("缺少执行确认")
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("请填写要扫描的文件夹")
        return webui_file_time.apply(path, payload)
