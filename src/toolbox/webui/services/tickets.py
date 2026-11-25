"""票务解析 WebUI 适配（L2）：最近缓存与导出目录只读。"""

from __future__ import annotations

from toolbox import webui_tickets


class TicketsService:
    def overview(self) -> dict:
        return webui_tickets.overview()
