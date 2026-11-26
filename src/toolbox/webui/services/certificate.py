"""证书证据导出的 Web 表现适配，不保存密码或任务结果。"""

from __future__ import annotations

from toolbox import webui_certificate_evidence


class CertificateService:
    def overview(self) -> dict:
        return webui_certificate_evidence.overview()

    def export(self, payload: dict) -> dict:
        return webui_certificate_evidence.export(payload)
