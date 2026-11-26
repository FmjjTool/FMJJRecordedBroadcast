"""命名投稿 · B站投稿 WebUI 适配（L2）：概况、预检与确认上传任务。"""

from __future__ import annotations

from toolbox import webui_bili_publish
from toolbox.webui.jobs import JobCancelled


class PublishService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_bili_publish.overview()

    def precheck(self, payload: dict) -> dict:
        return webui_bili_publish.precheck(dict(payload))

    def categories(self) -> dict:
        return webui_bili_publish.categories()

    def templates(self) -> dict:
        return webui_bili_publish.templates()

    def save_template(self, payload: dict) -> dict:
        return webui_bili_publish.save_template(dict(payload))

    def history(self) -> dict:
        return webui_bili_publish.history()

    def allow_retry(self, payload: dict) -> dict:
        return webui_bili_publish.allow_retry(dict(payload))

    def start_upload(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not payload.get("confirmed"):
            raise ValueError("缺少投稿确认")
        if not (payload.get("paths") or []):
            raise ValueError("请先勾选要投稿的视频")
        result = webui_bili_publish.precheck(dict(payload))
        if not result["ok"]:
            raise ValueError(result["blocking"] or "预检未通过，请先处理提示")
        job = self.jobs.submit(
            "bili_publish",
            lambda task: self._upload_job(task, dict(payload)))
        return {"job": job.id}

    def _upload_job(self, job, payload: dict) -> dict:
        def progress(fraction, text) -> None:
            job.update(str(text), fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_bili_publish.upload(
                payload, on_progress=progress, on_log=job.log,
                is_cancelled=cancelled)
        except webui_bili_publish.PublishCancelled as exc:
            raise JobCancelled(str(exc)) from exc
