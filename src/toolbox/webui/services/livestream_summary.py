"""直播总结 WebUI 适配（L2）：转录清单、设置快照与已保存总结只读。"""

from __future__ import annotations

from toolbox import webui_livestream_summary


class LivestreamSummaryService:
    def overview(self) -> dict:
        return webui_livestream_summary.overview()

    def saved_text(self, name: str) -> dict:
        return webui_livestream_summary.saved_text(name)
