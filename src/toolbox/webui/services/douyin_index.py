"""抖音指数 WebUI 适配（L2）：本地快照报表、达人搜索与抓取任务。"""

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