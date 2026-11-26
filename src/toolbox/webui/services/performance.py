"""性能诊断 WebUI 适配（L2）：本机读数、桌面设置与历史报告。"""

from __future__ import annotations

from toolbox import webui_performance
from toolbox.webui.errors import NotFound


class PerformanceService:
    def overview(self) -> dict:
        return webui_performance.overview()

    def save_settings(self, payload: dict) -> dict:
        return webui_performance.save_settings(payload)

    def reports(self, limit: int = 30) -> dict:
        return webui_performance.reports(limit)

    def report_detail(self, report_id: str) -> dict:
        try:
            return webui_performance.report_detail(report_id)
        except FileNotFoundError:
            raise NotFound("报告不存在或已轮转，请刷新列表后重试") from None

    def report_download(self, report_id: str) -> dict:
        try:
            return webui_performance.report_download(report_id)
        except FileNotFoundError:
            raise NotFound("报告不存在或已轮转，请刷新列表后重试") from None
