"""抖音指数 WebUI 适配（L2）：快照报表、搜索与确认后的资产回写任务。"""

from __future__ import annotations

from toolbox import webui_douyin_index


class DouyinIndexService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_douyin_index.overview()

    def report(self, name: str) -> dict:
        return webui_douyin_index.load_report(name)

    def search(self, payload: dict) -> dict:
        return webui_douyin_index.search(dict(payload))

    def start_fetch(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not str(payload.get("uid") or "").strip():
            raise ValueError("请选择要抓取的达人")
        if payload.get("confirmed") is not True:
            raise ValueError("请先确认抓取并回写已有资产")
        job = self.jobs.submit(
            "douyin_index_fetch",
            lambda task: webui_douyin_index.fetch(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    @staticmethod
    def _progress(task):
        def progress(fraction, text) -> None:
            task.update(str(text), fraction)
        return progress
