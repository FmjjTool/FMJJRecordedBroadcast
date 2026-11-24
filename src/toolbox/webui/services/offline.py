"""线下现场视频 WebUI 适配（L2）：清单读取与搜索任务提交。"""

from __future__ import annotations

from toolbox import webui_offline
from toolbox.webui.jobs import JobCancelled


class OfflineService:
    def __init__(self, jobs=None):
        self._jobs = jobs

    def overview(self) -> dict:
        return webui_offline.overview()

    def start_search(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        date = str(payload.get("date") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not date or not name:
            raise ValueError("请选择线下活动")
        platforms = payload.get("platforms") or list(webui_offline.PLATFORMS)
        if not isinstance(platforms, list) or not platforms:
            raise ValueError("请至少选择一个搜索来源")
        job = self._jobs.submit(
            "offline_video_search",
            lambda task: self._search_job(task, date, name, platforms))
        return {"job": job.id}

    def _search_job(self, job, date: str, name: str, platforms) -> dict:
        def progress(done: int, total: int, text: str) -> None:
            job.update(text or "搜索中…",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        result = webui_offline.search(
            date, name, platforms, on_progress=progress,
            is_cancelled=cancelled)
        if result.get("cancelled"):
            raise JobCancelled("搜索已取消")
        return result