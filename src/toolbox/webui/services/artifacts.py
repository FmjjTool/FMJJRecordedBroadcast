"""采集工具 WebUI 适配（L2）：本地产物列表、图片预览与文件下载。"""

from __future__ import annotations

from toolbox import webui_artifacts


class ArtifactService:
    def kinds(self) -> list[dict]:
        return webui_artifacts.kind_names()

    def list(self, kind: str, group_limit: int = 200,
             file_limit: int = 200) -> dict:
        return webui_artifacts.list_artifacts(kind, group_limit, file_limit)

    def file(self, kind: str, rel: str, preview: bool = False):
        return webui_artifacts.resolve_file(kind, rel, preview=preview)
