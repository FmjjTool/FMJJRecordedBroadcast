"""票务解析 WebUI 适配（L2）：缓存概览、解析任务与导出任务。"""

from __future__ import annotations

from toolbox import webui_tickets
from toolbox.webui.jobs import JobCancelled


class TicketsService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_tickets.overview()

    def start_parse(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_tickets.prepare_payload(payload)
        job = self.jobs.submit(
            "tickets_parse",
            lambda task: webui_tickets.parse(
                dict(payload), on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_export(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_tickets.prepare_payload(payload)
        job = self.jobs.submit(
            "tickets_export",
            lambda task: self._export_job(task, dict(payload)))
        return {"job": job.id}

    def _export_job(self, job, payload: dict) -> dict:
        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tickets.export(
                payload, on_log=job.log, is_cancelled=cancelled)
        except ValueError as exc:
            if "取消" in str(exc):
                raise JobCancelled(str(exc)) from exc
            raise
