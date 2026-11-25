"""账户追踪（音乐 / 网媒）WebUI 适配（L2）：只读概览与历史。"""

from __future__ import annotations

from toolbox import webui_trackers


class TrackerService:
    def music(self) -> dict:
        return webui_trackers.music_overview()

    def media(self) -> dict:
        return webui_trackers.media_overview()

    def music_history(self, name: str, limit: int = 50) -> dict:
        return webui_trackers.music_history(name, limit)

    def media_history(self, name: str, limit: int = 50) -> dict:
        return webui_trackers.media_history(name, limit)
