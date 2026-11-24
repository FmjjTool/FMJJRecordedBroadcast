"""音视频工具 WebUI 适配（L2）：参数校验、任务提交与 L3 调用。"""

from __future__ import annotations

from toolbox import webui_tools
from toolbox.webui.jobs import JobCancelled


class ToolService:
    def __init__(self, jobs=None):
        self._jobs = jobs

    def audio_overview(self) -> dict:
        return webui_tools.audio_overview()

    def audio_sources(self, limit: int = 200) -> dict:
        return webui_tools.list_audio_sources(limit)

    def start_audio_extract(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        paths = payload.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ValueError("请选择要提取的视频")
        all_tracks = bool(payload.get("all_tracks"))
        job = self._jobs.submit(
            "audio_extract",
            lambda task: self._audio_job(task, paths, all_tracks))
        return {"job": job.id}

    def _audio_job(self, job, paths, all_tracks: bool) -> dict:
        def progress(done: int, total: int, name: str) -> None:
            job.update(f"正在处理 {done}/{total}: {name}",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.extract_audio(
                paths, all_tracks=all_tracks, on_progress=progress,
                is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc