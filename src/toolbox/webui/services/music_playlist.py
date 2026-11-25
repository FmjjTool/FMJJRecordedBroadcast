"""歌单 WebUI 适配（L2）：本地报告列表与内容只读。"""

from __future__ import annotations

from toolbox import webui_music_playlist


class MusicPlaylistService:
    def overview(self) -> dict:
        return webui_music_playlist.overview()

    def report_text(self, name: str) -> dict:
        return webui_music_playlist.report_text(name)
