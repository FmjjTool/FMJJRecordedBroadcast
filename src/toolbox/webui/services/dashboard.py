"""工作台只读数据服务。"""

from __future__ import annotations

from toolbox import webui_dashboard


class DashboardService:
    def overview(self) -> dict:
        return webui_dashboard.overview()
