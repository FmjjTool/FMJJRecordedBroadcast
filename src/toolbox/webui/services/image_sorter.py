"""媒体筛选 WebUI 适配（L2）：源文件夹、只读扫描与图片预览。"""

from __future__ import annotations

from toolbox import webui_image_sorter


class ImageSorterService:
    def overview(self) -> dict:
        return webui_image_sorter.overview()

    def scan(self, kind: str, keyword: str, limit: int) -> dict:
        return webui_image_sorter.scan(kind, keyword, limit)

    def file(self, ref: str, preview: bool = False):
        return webui_image_sorter.resolve(ref, preview=preview)
