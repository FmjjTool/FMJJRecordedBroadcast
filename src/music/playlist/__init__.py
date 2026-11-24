"""歌曲合集写入适配（L4）。

| 平台 | 创建歌单 | 添加歌曲 | 实测状态（2026-11-24） |
| --- | --- | --- | --- |
| 网易云音乐 | 是 | 是 | 创建/加歌已实测通过 |
| QQ音乐 | 是 | 待签名 | 创建已通过；加歌需网页 ag-1 签名，未通 |
| 酷狗音乐 | 待客户端 token | 待客户端 token | 网页登录态调用云歌单返回 20017 |
| 酷我音乐 | 否 | 否 | 写入接口返回 504，当前不可用 |
| 5sing | 否 | 否 | 无公开歌单创建接口 |

所有适配器只读取 auth 提供的当前登录态，不写回凭据；是否执行写入由 L3 决定。
"""

from __future__ import annotations

from .base import (
    AddSongsResult,
    AuthRequiredError,
    BasePlaylistWriter,
    ParseChangedError,
    PlatformRejectedError,
    PlaylistWriteError,
    RateLimitedError,
    UnsupportedPlatformError,
)
from .fivesing import FiveSingPlaylistWriter
from .kugou import KugouPlaylistWriter
from .kuwo import KuwoPlaylistWriter
from .netease import NeteasePlaylistWriter
from .qq import QQPlaylistWriter

WRITERS: dict[str, BasePlaylistWriter] = {
    "netease": NeteasePlaylistWriter(),
    "qq": QQPlaylistWriter(),
    "kugou": KugouPlaylistWriter(),
    "kuwo": KuwoPlaylistWriter(),
    "5sing": FiveSingPlaylistWriter(),
}

__all__ = [
    "WRITERS",
    "AddSongsResult",
    "AuthRequiredError",
    "BasePlaylistWriter",
    "ParseChangedError",
    "PlatformRejectedError",
    "PlaylistWriteError",
    "RateLimitedError",
    "UnsupportedPlatformError",
    "FiveSingPlaylistWriter",
    "KugouPlaylistWriter",
    "KuwoPlaylistWriter",
    "NeteasePlaylistWriter",
    "QQPlaylistWriter",
]
