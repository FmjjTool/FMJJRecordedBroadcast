"""标签 WebUI 适配（L2）：音乐文件标签与历史只读。"""

from __future__ import annotations

from toolbox import webui_music_tag


class MusicTagService:
    def overview(self, limit: int = 120) -> dict:
        return webui_music_tag.overview(limit)
