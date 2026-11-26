"""证书证据导出的 Web 表现适配，不保存密码或任务结果。"""

from __future__ import annotations

import threading

from toolbox import webui_certificate_evidence


class CertificateService:
    def __init__(self):
        self._export_lock = threading.Lock()

    def overview(self) -> dict:
        return webui_certificate_evidence.overview()

    def export(self, payload: dict) -> dict:
        if not self._export_lock.acquire(blocking=False):
            raise ValueError("已有证据包正在导出，请稍后重试")
        try:
            return webui_certificate_evidence.export(payload)
        except OSError:
            raise ValueError("读取证据文件失败，请确认文件可访问后重试") from None
        finally:
            self._export_lock.release()
