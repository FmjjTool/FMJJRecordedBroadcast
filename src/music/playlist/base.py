"""歌单写入适配器的公共契约与错误分类。

适配器只负责平台协议；是否创建、用哪个账号、失败后是否继续由 L3 决定。
凭据通过 auth.get_cookie() 只读获取，不落盘、不输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from ..models import SongInfo

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class PlaylistWriteError(RuntimeError):
    """歌单写入失败。message 可直接展示给用户，不包含凭据内容。"""

    def __init__(self, message: str, *, platform: str = "", phase: str = "",
                 retryable: bool = False):
        super().__init__(message)
        self.platform = platform
        self.phase = phase
        self.retryable = retryable


class AuthRequiredError(PlaylistWriteError):
    """未登录或登录态失效。"""


class RateLimitedError(PlaylistWriteError):
    """平台限流，需要降低频率或稍后再试。"""


class UnsupportedPlatformError(PlaylistWriteError):
    """平台没有公开的写歌单能力。"""


class PlatformRejectedError(PlaylistWriteError):
    """平台明确拒绝本次写入（参数、权限、风控等）。"""


class ParseChangedError(PlaylistWriteError):
    """接口结构变化，无法安全判断结果。"""


def parse_cookie_string(cookie: str) -> dict[str, str]:
    """把 Cookie 请求头解析为字典；空值/无等号片段忽略。"""
    jar: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            jar[key] = value.strip()
    return jar


def cookie_from_auth(platform: str) -> dict[str, str]:
    """通过统一管理接口只读获取当前有效登录态。"""
    from auth import get_cookie

    jar = parse_cookie_string(get_cookie(platform))
    if not jar:
        raise AuthRequiredError("需要登录后才能创建合集", platform=platform, phase="auth")
    return jar


def response_json(resp: httpx.Response, *, platform: str, phase: str) -> dict:
    """解析 JSON，失败归为 ParseChanged，避免把结构变化当空结果。"""
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - 统一转类型化错误
        raise ParseChangedError(
            "平台返回内容无法解析，接口可能已变化",
            platform=platform, phase=phase,
        ) from exc
    if not isinstance(data, dict):
        raise ParseChangedError(
            "平台返回结构异常，接口可能已变化",
            platform=platform, phase=phase,
        )
    return data


@dataclass
class AddSongsResult:
    added: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


class BasePlaylistWriter:
    platform = ""
    platform_name = ""
    supports_create = True
    experimental = False
    verified = False
    batch_size = 50

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        raise NotImplementedError

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress: Optional[Callable[[int, int], None]] = None) -> AddSongsResult:
        raise NotImplementedError

    def _client(self, cookie: dict[str, str], transport=None) -> httpx.Client:
        return httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            cookies=cookie,
            transport=transport,
            headers={"User-Agent": DEFAULT_UA},
        )

    @staticmethod
    def _extra_int(song: SongInfo, *keys: str) -> int:
        for key in keys:
            value = (song.extra or {}).get(key)
            try:
                if value not in (None, ""):
                    return int(value)
            except (TypeError, ValueError):
                continue
        return 0
