"""音乐下载 WebUI 适配（L2）：下载目录列表 + 搜索 / 下载任务。"""

from __future__ import annotations

from toolbox import webui_music, webui_music_download


class MusicDownloadService:
    def __init__(self, jobs=None):
        self.jobs = jobs
        from toolbox.webui_music_export import MusicSearchExports
        self.search_exports = MusicSearchExports()

    def overview(self) -> dict:
        return webui_music_download.overview()

    def qualities(self) -> dict:
        return webui_music.download_quality_options()

    def _submit(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        job = self.jobs.submit(
            kind,
            lambda task: handler(payload, on_log=task.log,
                                 is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_search(self, payload: dict) -> dict:
        payload = dict(payload)
        if not str(payload.get("keyword") or "").strip():
            raise ValueError("请输入歌曲关键词")
        if str(payload.get("platform") or "") not in (
                "all", "qq", "netease", "kugou", "kuwo", "5sing", "kugou_lite"):
            raise ValueError("未知音乐平台")
        return self._submit("music_search", payload, self.search_exports.search)

    def start_resolve(self, payload: dict) -> dict:
        from toolbox import webui_music_resolve
        from toolbox.webui.jobs import JobCancelled

        if self.jobs is None:
            raise ValueError("任务服务不可用")
        body = webui_music_resolve.validate_payload(payload)

        def run(task):
            try:
                outcome = self.search_exports.resolve(
                    body, on_log=task.log,
                    is_cancelled=lambda: bool(task.cancel_requested))
                if task.cancel_requested:
                    raise JobCancelled()
                return outcome
            except InterruptedError:
                raise JobCancelled() from None

        job = self.jobs.submit("music_resolve", run)
        return {"job": job.id}

    def start_export(self, payload: dict) -> dict:
        from toolbox.webui.jobs import JobCancelled

        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not isinstance(payload, dict):
            raise ValueError("导出参数无效，请重新勾选歌曲")
        body = dict(payload)

        def run(task):
            try:
                public, archive = self.search_exports.export_selected(
                    body, task.workspace,
                    is_cancelled=lambda: bool(task.cancel_requested),
                    on_progress=lambda message, value: task.update(message, value))
                if task.cancel_requested:
                    raise JobCancelled()
                task.add_file(archive)
                return public
            except InterruptedError:
                raise JobCancelled() from None

        job = self.jobs.submit("music_export", run)
        return {"job": job.id}

    def start_download(self, payload: dict) -> dict:
        payload = dict(payload)
        if type(payload.get("include_cover", True)) is not bool:
            raise ValueError("封面选项无效，请重新选择")
        payload.setdefault("include_cover", True)
        rows = payload.get("items") or []
        if not isinstance(rows, list) or not rows:
            raise ValueError("请先勾选要下载的歌曲")
        if len(rows) > 100:
            raise ValueError("一次最多下载 100 首")
        return self._submit("music_download", payload, webui_music.download)
