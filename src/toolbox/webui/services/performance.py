"""性能诊断 WebUI 适配（L2）：本机读数只读。"""

from __future__ import annotations

from toolbox import webui_performance


class PerformanceService:
    def overview(self) -> dict:
        return webui_performance.overview()
