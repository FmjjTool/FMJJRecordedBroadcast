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
    """通过统一管理接口只读获取当前有效登录态。

    网易云分权：建歌单优先用「网易云（歌单）」账号，未单独登录时回落主账号；
    会员下载始终用主账号（`auth.get_netease_cookie()`），两者互不影响。
    """
    from auth import get_cookie

    if (platform or "").strip().lower() == "netease":
        from auth import get_netease_playlist_cookie

        raw = get_netease_playlist_cookie()
    else:
        raw = get_cookie(platform)
    jar = parse_cookie_string(raw)
    if not jar:
        raise AuthRequiredError("需要登录后才能创建合集", platform=platform, phase="auth")
    return jar


def _body_snippet(text: str, limit: int = 120) -> str:
    """响应正文摘要：只用于错误提示，便于判断是登录页、风控页还是结构变化。"""
    value = " ".join((text or "").split())
    if not value:
        return "空响应"
    return value[:limit] + ("…" if len(value) > limit else "")


def response_json(resp: httpx.Response, *, platform: str, phase: str) -> dict:
    """解析 JSON，失败归为 ParseChanged，并把状态码与正文摘要带进提示。"""
    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
    detail = f"HTTP {resp.status_code}" + (f" {content_type}" if content_type else "")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - 统一转类型化错误
        raise ParseChangedError(
            f"平台返回内容无法解析（{detail}）：{_body_snippet(resp.text)}",
            platform=platform, phase=phase,
        ) from exc
    if not isinstance(data, dict):
        raise ParseChangedError(
            f"平台返回结构异常（{detail}）：{_body_snippet(resp.text)}",
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
    available = True          # False = 接口已确认不可用，界面不提供选择
    experimental = False
    verified = False
    prepend = False
    supports_sort = False
    note = ""
    batch_size = 50

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        raise NotImplementedError

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress: Optional[Callable[[int, int], None]] = None) -> AddSongsResult:
        raise NotImplementedError

    def sort_songs(self, playlist_id: str, ordered_song_ids: list[str],
                   cookie: dict[str, str], *,
                   transport: Optional[httpx.BaseTransport] = None) -> None:
        """按给定顺序重排歌单；平台不支持时由 supports_sort=False 跳过。"""
        return None

    def playlist_url(self, playlist_id: str, cookie: dict[str, str], *,
                     name: str = "",
                     transport: Optional[httpx.BaseTransport] = None) -> str:
        """歌单公开网页链接；拿不到（或平台没有网页歌单页）返回空串。"""
        return ""

    def playlist_song_ids(self, playlist_id: str, cookie: dict[str, str], *,
                          transport: Optional[httpx.BaseTransport] = None) -> set[str]:
        """歌单里已有的歌曲 ID，用于重跑 / 复用同名歌单时去重。

        平台不支持查询（或查询失败）时返回空集合，调用方按「没有重复」处理。
        """
        return set()

    def playlist_exists(self, playlist_id: str, cookie: dict[str, str], *,
                        name: str = "",
                        transport: Optional[httpx.BaseTransport] = None) -> bool:
        """复用历史歌单前核对它是否还在；平台不支持核对时按「存在」处理。"""
        return True

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
