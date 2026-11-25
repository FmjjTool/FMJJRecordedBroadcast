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

    def subtitle_overview(self) -> dict:
        return webui_tools.subtitle_overview()

    def subtitle_sources(self, limit: int = 200) -> dict:
        return webui_tools.list_subtitle_sources(limit)

    def start_subtitle_convert(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        paths = payload.get("paths") or []
        formats = payload.get("formats") or []
        if not isinstance(paths, list) or not paths:
            raise ValueError("请选择要转换的字幕文件")
        if not isinstance(formats, list) or not formats:
            raise ValueError("请至少选择一种输出格式")
        job = self._jobs.submit(
            "subtitle_convert",
            lambda task: self._subtitle_job(task, paths, formats))
        return {"job": job.id}

    def _subtitle_job(self, job, paths, formats) -> dict:
        def progress(done: int, total: int, name: str) -> None:
            job.update(f"正在转换 {done}/{total}: {name}",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.convert_subtitles(
                paths, formats, on_progress=progress,
                is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def organize_overview(self) -> dict:
        return webui_tools.organize_overview()

    def organize_preview(self, limit: int = 100) -> dict:
        return webui_tools.list_organize_preview(limit)

    def start_organize(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        dates = payload.get("dates") or []
        if not isinstance(dates, list) or not dates:
            raise ValueError("请选择要整理的日期分组")
        if not payload.get("confirmed"):
            raise ValueError("缺少整理确认")
        job = self._jobs.submit(
            "organize_run",
            lambda task: self._organize_job(task, dates))
        return {"job": job.id}

    def _organize_job(self, job, dates) -> dict:
        def progress(done: int, total: int, name: str) -> None:
            job.update(f"{name}（{done}/{total}）",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.organize_run(
                dates, on_progress=progress, is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def rename_overview(self) -> dict:
        return webui_tools.rename_overview()

    def rename_preview(self, limit: int = 200) -> dict:
        return webui_tools.list_rename_preview(limit)

    def video_overview(self) -> dict:
        return webui_tools.video_overview()

    def video_sources(self, limit: int = 200) -> dict:
        return webui_tools.list_video_sources(limit)

    def start_video_convert(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        paths = payload.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ValueError("请选择要转换的视频")
        job = self._jobs.submit(
            "video_convert",
            lambda task: self._video_job(task, paths))
        return {"job": job.id}

    def _video_job(self, job, paths) -> dict:
        def progress(done: int, total: int, name: str) -> None:
            job.update(f"正在转换 {done}/{total}: {name}",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.convert_videos(
                paths, on_progress=progress, is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def calendar_days(self) -> dict:
        return webui_tools.calendar_days()

    def calendar_day_files(self, folder: str, limit: int = 500) -> dict:
        return webui_tools.calendar_day_files(folder, limit)

    def launcher_overview(self) -> dict:
        return webui_tools.launcher_overview()

    def migrate_overview(self) -> dict:
        return webui_tools.migrate_overview()

    def migrate_scan(self) -> dict:
        return webui_tools.migrate_scan()

    def start_migrate(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        mode = str(payload.get("mode") or "")
        if mode not in ("copy", "move"):
            raise ValueError("迁移模式只支持复制或剪切")
        folders = payload.get("folders") or []
        if not isinstance(folders, list) or not folders:
            raise ValueError("请选择要迁移的日期文件夹")
        confirmed = bool(payload.get("confirmed"))
        if not confirmed:
            raise ValueError("缺少迁移确认")
        job = self._jobs.submit(
            "file_migrate",
            lambda task: self._migrate_job(task, mode, folders))
        return {"job": job.id}

    def _migrate_job(self, job, mode, folders) -> dict:
        def progress(done: int, total: int, name: str) -> None:
            job.update(f"正在迁移 {done}/{total}: {name}",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.migrate_run(
                mode, folders, on_progress=progress, is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc