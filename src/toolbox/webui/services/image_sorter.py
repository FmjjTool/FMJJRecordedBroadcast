"""媒体筛选 WebUI 适配（L2）：源文件夹、扫描、预览与分类写操作。"""

from __future__ import annotations

from toolbox import webui_image_sorter


class ImageSorterService:
    def overview(self) -> dict:
        return webui_image_sorter.overview()

    def scan(self, kind: str, keyword: str, limit: int) -> dict:
        return webui_image_sorter.scan(kind, keyword, limit)

    def file(self, ref: str, preview: bool = False):
        return webui_image_sorter.resolve(ref, preview=preview)

    def transfer(self, payload: dict) -> dict:
        return webui_image_sorter.transfer(
            payload.get("refs") or [], str(payload.get("mode") or ""),
            str(payload.get("target") or ""), payload.get("confirmed") is True)

    def delete(self, payload: dict) -> dict:
        return webui_image_sorter.delete_to_trash(
            payload.get("refs") or [], payload.get("confirmed") is True)

    def rename(self, payload: dict) -> dict:
        return webui_image_sorter.rename(
            str(payload.get("ref") or ""), str(payload.get("name") or ""),
            payload.get("confirmed") is True)
