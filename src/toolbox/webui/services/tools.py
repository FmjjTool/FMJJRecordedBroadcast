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

    def start_rename_execute(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        rel = str(payload.get("path") or "").strip()
        if not rel:
            raise ValueError("缺少视频文件")
        account = str(payload.get("account") or "").strip()
        types = payload.get("types") or []
        if not account:
            raise ValueError("请选择直播账户")
        if not isinstance(types, list) or not types:
            raise ValueError("请至少选择一种直播类型")
        if not payload.get("confirmed"):
            raise ValueError("缺少改名确认")
        danmu = bool(payload.get("danmu"))
        platform = str(payload.get("platform") or "").strip()
        title = str(payload.get("title") or "").strip()
        job = self._jobs.submit(
            "rename_execute",
            lambda task: webui_tools.rename_execute(
                rel, account, types, danmu, platform, title))
        return {"job": job.id}

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

    def start_video_compress(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        paths = payload.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ValueError("请选择要压缩的视频")
        preset = str(payload.get("preset") or "normal")
        resolution = str(payload.get("resolution") or "keep")
        preset, resolution = webui_tools.validate_compress_options(
            preset, resolution)
        clear_metadata = bool(payload.get("clear_metadata", True))
        job = self._jobs.submit(
            "video_compress",
            lambda task: self._video_compress_job(
                task, paths, preset, resolution, clear_metadata))
        return {"job": job.id}

    def _video_compress_job(self, job, paths, preset: str, resolution: str,
                            clear_metadata: bool) -> dict:
        def progress(done, total, name) -> None:
            fraction = (float(done) / total) if total else None
            job.update(f"正在压缩: {name}", fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.compress_videos(
                paths, preset=preset, resolution=resolution,
                clear_metadata=clear_metadata, on_progress=progress,
                is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def start_video_merge(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        paths = payload.get("paths") or []
        if not isinstance(paths, list) or len(paths) < 2:
            raise ValueError("至少选择 2 个视频才能合并")
        mode = str(payload.get("mode") or "copy")
        if mode not in ("copy", "encode"):
            raise ValueError("未知合并方式")
        output_name = str(payload.get("output_name") or "").strip()
        output_ext = str(payload.get("output_ext") or ".mp4").lower()
        if output_ext not in (".mp4", ".mkv"):
            raise ValueError("合并输出只支持 mp4 / mkv")
        job = self._jobs.submit(
            "video_merge",
            lambda task: self._video_merge_job(
                task, paths, mode, output_name, output_ext))
        return {"job": job.id}

    def _video_merge_job(self, job, paths, mode: str, output_name: str,
                         output_ext: str) -> dict:
        def progress(done, total, name) -> None:
            fraction = (float(done) / total) if total else None
            job.update(f"正在合并: {name}", fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.merge_videos(
                paths, mode=mode, output_name=output_name,
                output_ext=output_ext, on_progress=progress,
                is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def start_video_cut(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        rel = str(payload.get("path") or "").strip()
        if not rel:
            raise ValueError("请选择要切割的视频")
        segments = payload.get("segments") or []
        if not isinstance(segments, list) or not segments:
            raise ValueError("请至少填写一个切割片段")
        mode = str(payload.get("mode") or "copy")
        if mode not in ("copy", "precise"):
            raise ValueError("未知切割方式")
        export = str(payload.get("export") or "each")
        if export not in ("each", "merge", "complement"):
            raise ValueError("未知导出方式")
        merged_name = str(payload.get("merged_name") or "").strip()
        job = self._jobs.submit(
            "video_cut",
            lambda task: self._video_cut_job(
                task, rel, segments, mode, export, merged_name))
        return {"job": job.id}

    def _video_cut_job(self, job, rel: str, segments, mode: str, export: str,
                       merged_name: str) -> dict:
        def progress(done, total, name) -> None:
            fraction = (float(done) / total) if total else None
            job.update(f"正在切割: {name}", fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.cut_video(
                rel, segments, mode=mode, export=export,
                merged_name=merged_name, on_progress=progress,
                is_cancelled=cancelled)
        except webui_tools.ToolCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def start_video_snapshot(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        rel = str(payload.get("path") or "").strip()
        if not rel:
            raise ValueError("请选择要截图的视频")
        mode = str(payload.get("mode") or "single")
        if mode not in ("single", "interval"):
            raise ValueError("未知截图方式")
        position = str(payload.get("position") or "").strip()
        interval_sec = payload.get("interval_sec") or 0
        image_format = str(payload.get("format") or "jpg").lower()
        if mode == "single" and not position:
            raise ValueError("请填写截图时间点")
        job = self._jobs.submit(
            "video_snapshot",
            lambda task: self._video_snapshot_job(
                task, rel, mode, position, interval_sec, image_format))
        return {"job": job.id}

    def _video_snapshot_job(self, job, rel: str, mode: str, position: str,
                            interval_sec, image_format: str) -> dict:
        def progress(done, total, name) -> None:
            fraction = (float(done) / total) if total else None
            job.update(f"正在截图: {name}", fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_tools.snapshot_video(
                rel, mode=mode, position=position, interval_sec=interval_sec,
                image_format=image_format, on_progress=progress,
                is_cancelled=cancelled)
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