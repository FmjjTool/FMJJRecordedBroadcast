"""音乐下载 WebUI 适配（L2）：下载目录文件只读列表。"""

from __future__ import annotations

from toolbox import webui_music_download


class MusicDownloadService:
    def overview(self) -> dict:
        return webui_music_download.overview()
