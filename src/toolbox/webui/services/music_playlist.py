"""歌单 WebUI 适配（L2）：本地报告列表 + 预览 / 写入平台任务。"""

from __future__ import annotations

from toolbox import webui_music_playlist


class MusicPlaylistService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_music_playlist.overview()

    def report_text(self, name: str) -> dict:
        return webui_music_playlist.report_text(name)

    def capabilities(self) -> dict:
        return {"platforms": webui_music_playlist.capabilities(),
                "events": webui_music_playlist.events()}

    def _submit(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        job = self.jobs.submit(
            kind,
            lambda task: handler(payload, on_log=task.log,
                                 is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_preview(self, payload: dict) -> dict:
        payload = dict(payload)
        if not str(payload.get("name") or "").strip():
            raise ValueError("请填写歌单名称")
        if not payload.get("platforms"):
            raise ValueError("请至少选择一个目标平台")
        return self._submit("playlist_preview", payload,
                            webui_music_playlist.preview)

    def start_apply(self, payload: dict) -> dict:
        payload = dict(payload)
        if not str(payload.get("preview_id") or ""):
            raise ValueError("请先预览匹配")
        if not webui_music_playlist.has_preview(str(payload["preview_id"])):
            raise ValueError("预览结果已过期，请重新预览")
        return self._submit("playlist_apply", payload,
                            webui_music_playlist.apply)