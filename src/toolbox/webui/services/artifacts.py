"""采集工具 WebUI 适配（L2）：本地产物列表、图片预览与文件下载。"""

from __future__ import annotations

from toolbox import webui_artifacts


class ArtifactService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def kinds(self) -> list[dict]:
        return webui_artifacts.kind_names()

    def start_comments(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        from toolbox import webui_collect_tools
        webui_collect_tools.ensure_comments(dict(payload))
        job = self.jobs.submit(
            "comments_fetch",
            lambda task: webui_collect_tools.comments_run(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_wechat(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        from toolbox import webui_collect_tools
        webui_collect_tools.ensure_wechat(dict(payload))
        job = self.jobs.submit(
            "wechat_parse",
            lambda task: webui_collect_tools.wechat_run(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    @staticmethod
    def _progress(task):
        def progress(done, total, text) -> None:
            fraction = (float(done) / total) if total else None
            task.update(str(text), fraction)
        return progress

    def list(self, kind: str, group_limit: int = 200,
             file_limit: int = 200) -> dict:
        return webui_artifacts.list_artifacts(kind, group_limit, file_limit)

    def file(self, kind: str, rel: str, preview: bool = False):
        return webui_artifacts.resolve_file(kind, rel, preview=preview)
