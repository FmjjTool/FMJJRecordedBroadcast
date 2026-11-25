"""抖音指数 WebUI 适配（L2）：本地快照只读列表与报表。"""

from __future__ import annotations

from toolbox import webui_douyin_index


class DouyinIndexService:
    def overview(self) -> dict:
        return webui_douyin_index.overview()

    def report(self, name: str) -> dict:
        return webui_douyin_index.load_report(name)
