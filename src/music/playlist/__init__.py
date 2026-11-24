"""歌曲合集写入适配（L4）。

| 平台 | 创建歌单 | 添加歌曲 | 验证状态 |
| --- | --- | --- | --- |
| 网易云音乐 | 是 | 是 | 接口已核对，待真实账号实测 |
| QQ音乐 | 是 | 是 | 接口已核对，待真实账号实测 |
| 酷狗音乐 | 是 | 是 | 协议来自开源实现，待真实账号实测 |
| 酷我音乐 | 实验 | 实验 | 公开网页未暴露创建入口，待实测 |
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
