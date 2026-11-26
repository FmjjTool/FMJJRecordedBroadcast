"""网易云音乐歌单写入（网页 API）。

创建：POST /api/playlist/create
加歌：POST /api/playlist/manipulate/tracks
登录态：Cookie 中的 MUSIC_U；POST 需带 csrf_token（取自 __csrf Cookie）。
"""

from __future__ import annotations

import json
from typing import Optional

import httpx

from ..models import SongInfo
from .base import (
    AddSongsResult,
    AuthRequiredError,
    BasePlaylistWriter,
    ParseChangedError,
    PlatformRejectedError,
    RateLimitedError,
    response_json,
)

BASE = "https://music.163.com"


class NeteasePlaylistWriter(BasePlaylistWriter):
    platform = "netease"
    platform_name = "网易云音乐"
    verified = True
    supports_sort = True
    note = "创建/加歌/排序已实测通过"
    batch_size = 100

    def _csrf(self, cookie: dict[str, str]) -> str:
        return cookie.get("__csrf") or cookie.get("csrf") or ""

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        data = {"name": name}
        csrf = self._csrf(cookie)
        if csrf:
            data["csrf_token"] = csrf
        headers = {
            "Referer": f"{BASE}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with self._client(cookie, transport) as client:
            resp = client.post(f"{BASE}/api/playlist/create", data=data, headers=headers)
        body = response_json(resp, platform=self.platform, phase="create")
        code = _code(body)
        if code == 200:
            playlist = body.get("playlist") or {}
            playlist_id = playlist.get("id") or body.get("id")
            if not playlist_id:
                raise ParseChangedError(
                    "创建成功但未返回歌单 ID，接口可能已变化",
                    platform=self.platform, phase="create",
                )
            return str(playlist_id)
        if code in (301, 302, 401):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="create",
            )
        if code == 429:
            raise RateLimitedError(
                "网易云请求过于频繁，请稍后再试",
                platform=self.platform, phase="create", retryable=True,
            )
        raise PlatformRejectedError(
            _error_message(body, "创建歌单失败"),
            platform=self.platform, phase="create",
        )

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress=None) -> AddSongsResult:
        result = AddSongsResult()
        if not songs:
            return result
        track_ids = [song.song_id for song in songs if song.song_id]
        if len(track_ids) != len(songs):
            missing = [song.song_name or song.song_id for song in songs if not song.song_id]
            for name in missing:
                result.failed.append((name, "缺少网易云歌曲 ID"))
        if not track_ids:
            return result

        payload_ids = [int(track_id) if str(track_id).isdigit() else track_id
                       for track_id in track_ids]
        data = {
            "op": "add",
            "pid": playlist_id,
            "trackIds": json.dumps(payload_ids, separators=(",", ":")),
            "imme": "true",
        }
        csrf = self._csrf(cookie)
        if csrf:
            data["csrf_token"] = csrf
        headers = {
            "Referer": f"{BASE}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with self._client(cookie, transport) as client:
            resp = client.post(f"{BASE}/api/playlist/manipulate/tracks", data=data, headers=headers)
        body = response_json(resp, platform=self.platform, phase="add")
        code = _code(body)
        if code == 200:
            result.added.extend(track_ids)
            if progress:
                progress(len(track_ids), len(track_ids))
            return result
        if code in (301, 302, 401):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="add",
            )
        if code == 429:
            raise RateLimitedError(
                "网易云请求过于频繁，请稍后再试",
                platform=self.platform, phase="add", retryable=True,
            )
        raise PlatformRejectedError(
            _error_message(body, "添加歌曲失败"),
            platform=self.platform, phase="add",
        )

    def playlist_url(self, playlist_id: str, cookie: dict[str, str], *,
                     name: str = "",
                     transport: Optional[httpx.BaseTransport] = None) -> str:
        ident = str(playlist_id or "").strip()
        if not ident:
            return ""
        return f"https://music.163.com/playlist?id={ident}"

    def sort_songs(self, playlist_id: str, ordered_song_ids: list[str],
                   cookie: dict[str, str], *,
                   transport: Optional[httpx.BaseTransport] = None) -> None:
        if not ordered_song_ids:
            return
        data = {
            "op": "update",
            "pid": playlist_id,
            "trackIds": json.dumps([str(x) for x in ordered_song_ids],
                                   separators=(",", ":")),
        }
        csrf = self._csrf(cookie)
        if csrf:
            data["csrf_token"] = csrf
        headers = {
            "Referer": f"{BASE}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with self._client(cookie, transport) as client:
            resp = client.post(f"{BASE}/api/playlist/manipulate/tracks",
                               data=data, headers=headers)
        body = response_json(resp, platform=self.platform, phase="sort")
        code = _code(body)
        if code == 200:
            return
        if code in (301, 302, 401):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="sort",
            )
        if code == 429:
            raise RateLimitedError(
                "网易云请求过于频繁，请稍后再试",
                platform=self.platform, phase="sort", retryable=True,
            )
        raise PlatformRejectedError(
            _error_message(body, "排序失败"),
            platform=self.platform, phase="sort",
        )


def _code(body: dict) -> int:
    try:
        return int(body.get("code", -1))
    except (TypeError, ValueError):
        return -1


def _error_message(body: dict, fallback: str) -> str:
    message = body.get("message") or body.get("msg") or body.get("error")
    if message:
        return f"{fallback}：{message}"
    return f"{fallback}（错误码 {body.get('code', '未知')}）"
