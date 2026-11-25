"""采集任务 WebUI 适配（L2）：账号清单、设置、批量解析与按勾选采集。"""

from __future__ import annotations

from toolbox import webui_accounts


class InfoAccountsService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_accounts.overview()

    def save_settings(self, payload: dict) -> dict:
        return webui_accounts.save_settings(payload)

    def start_parse(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not str(payload.get("text") or "").strip():
            raise ValueError("请输入账号主页或作品链接（一行一个）")
        job = self.jobs.submit(
            "info_parse",
            lambda task: webui_accounts.parse_batch(
                dict(payload), log=task.log,
                on_progress=self._progress(task),
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_collect(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        webui_accounts.ensure_collectable(dict(payload))
        job = self.jobs.submit(
            "info_collect",
            lambda task: webui_accounts.collect(
                dict(payload), log=task.log,
                on_progress=self._progress(task),
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    @staticmethod
    def _progress(task):
        def progress(done, total, text) -> None:
            fraction = (float(done) / total) if total else None
            task.update(str(text), fraction)
        return progress
