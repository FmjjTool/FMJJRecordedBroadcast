"""性能诊断 WebUI 适配（L2）：本机只读读数与桌面自动监控设置。"""

from __future__ import annotations

from toolbox import webui_performance


class PerformanceService:
    def overview(self) -> dict:
        return webui_performance.overview()

    def save_settings(self, payload: dict) -> dict:
        return webui_performance.save_settings(payload)
