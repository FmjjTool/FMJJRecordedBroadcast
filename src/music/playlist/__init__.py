"""歌曲合集写入适配（L4）。

| 平台 | 创建歌单 | 添加歌曲 | 实测状态（2026-11-24） |
| --- | --- | --- | --- |
| 网易云音乐 | 是 | 是 | 创建/加歌/排序已实测通过 |
| QQ音乐 | 是 | 是 | 创建/加歌已实测通过（musicu 签名，逐首反向添加保序） |
| 酷狗音乐 | 需 App token | 需 App token | 网页 token 返回 20006/20017；App 扫码 token 已实测加歌 16/16、顺序正确 |
| 酷我音乐 | 否 | 否 | 写入接口 HTTP 504，当前不可用 |
| 5sing | 是 | 是 | songlist JSONP 接口：创建/加歌已实测通过（封面+标签必填，加歌按「类型$ID」） |

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
