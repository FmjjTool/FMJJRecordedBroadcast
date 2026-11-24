"""5sing 歌单写入占位：平台没有公开的歌单创建/加歌接口。"""

from __future__ import annotations

from typing import Optional

import httpx

from ..models import SongInfo
from .base import BasePlaylistWriter, UnsupportedPlatformError


class FiveSingPlaylistWriter(BasePlaylistWriter):
    platform = "5sing"
    platform_name = "5sing"
    supports_create = False
    verified = False
    note = "无公开歌单创建接口"

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        raise UnsupportedPlatformError(
            "5sing 没有公开的歌单创建接口，暂不支持创建合集",
            platform=self.platform, phase="create",
        )

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress=None):
        raise UnsupportedPlatformError(
            "5sing 没有公开的歌单加歌接口，暂不支持创建合集",
            platform=self.platform, phase="add",
        )
