"""账户追踪（音乐 / 网媒）WebUI 适配（L2）：概览、历史、批量采集与导出图片。"""

from __future__ import annotations

from toolbox import webui_trackers


class TrackerService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def music(self) -> dict:
        return webui_trackers.music_overview()

    def media(self) -> dict:
        return webui_trackers.media_overview()

    def music_history(self, name: str, limit: int = 50) -> dict:
        return webui_trackers.music_history(name, limit)

    def media_history(self, name: str, limit: int = 50) -> dict:
        return webui_trackers.media_history(name, limit)

    def start_collect(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        kind = str(payload.get("kind") or "")
        if kind not in ("music", "media"):
            raise ValueError("未知账号类型")
        job = self.jobs.submit(
            "tracker_collect",
            lambda task: webui_trackers.collect_all(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def export_png(self, payload: dict) -> dict:
        return webui_trackers.export_png(dict(payload))

    @staticmethod
    def _progress(task):
        def progress(done, total, text) -> None:
            fraction = (float(done) / total) if total else None
            task.update(str(text), fraction)
        return progress
