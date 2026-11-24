"""酷我音乐歌单写入（实验）。

公开网页没有暴露创建歌单入口，以下接口来自酷我 Web 旧版调用约定：
创建：POST /api/www/playlist/addPlaylist
加歌：POST /api/www/playlist/addSongToPlayList
登录态：kw_token；写请求带 csrf 头，另按 Hm_Iuvt Cookie 生成 Secret 头。

该适配器标记为实验能力：接口参数尚未用真实账号验证，失败时会明确提示。
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

import httpx

from ..models import SongInfo
from ..utils import kuwo_secret
from .base import (
    AddSongsResult,
    AuthRequiredError,
    BasePlaylistWriter,
    ParseChangedError,
    PlatformRejectedError,
    response_json,
)

BASE = "https://www.kuwo.cn"
CREATE_URL = f"{BASE}/api/www/playlist/addPlaylist"
ADD_URL = f"{BASE}/api/www/playlist/addSongToPlayList"


def _req_id() -> str:
    stamp = format(int(time.time() * 1000), "x").rjust(12, "0")
    return f"{stamp}-{uuid.uuid4().hex}"


def _hm_token(cookie: dict[str, str]) -> str:
    for key, value in cookie.items():
        if key.startswith("Hm_Iuvt") and value:
            return value
    return ""


class KuwoPlaylistWriter(BasePlaylistWriter):
    platform = "kuwo"
    platform_name = "酷我音乐"
    verified = False
    available = True          # 可勾选；写入会明确失败并说明原因
    experimental = True
    note = ("网页建单接口已下线（addPlaylist/createPlaylist 返回 504、"
            "用户歌单列表 404），勾选后写入会失败；需酷我 App 协议才能真正建单")
    batch_size = 50

    def _headers(self, cookie: dict[str, str]) -> dict[str, str]:
        headers = {
            "Referer": f"{BASE}/",
            "Content-Type": "application/json",
        }
        csrf = cookie.get("kw_token") or cookie.get("csrf") or ""
        hm_token = _hm_token(cookie)
        if hm_token:
            headers["Secret"] = kuwo_secret(hm_token)
            if not csrf:
                csrf = headers["Secret"]
        if csrf:
            headers["csrf"] = csrf
        return headers

    def _params(self, extra: dict) -> dict:
        return {
            **extra,
            "reqId": _req_id(),
            "httpsStatus": 1,
            "plat": "web_www",
        }

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        if not (cookie.get("kw_token") or cookie.get("csrf")
                or _hm_token(cookie) or cookie.get("userid")):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="create",
            )
        params = self._params({"name": name, "isOpen": 0})
        with self._client(cookie, transport) as client:
            resp = client.post(
                CREATE_URL, params=params,
                json={"name": name, "isOpen": 0},
                headers=self._headers(cookie),
            )
        _check_status(resp, self.platform, "create")
        body = response_json(resp, platform=self.platform, phase="create")
        if _is_ok(body):
            playlist_id = _find_playlist_id(body)
            if not playlist_id:
                raise ParseChangedError(
                    "创建成功但未返回歌单 ID，接口可能已变化",
                    platform=self.platform, phase="create",
                )
            return str(playlist_id)
        raise _map_error(body, self.platform, "create", "创建歌单失败")

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress=None) -> AddSongsResult:
        result = AddSongsResult()
        music_ids = [song.song_id for song in songs if song.song_id]
        if len(music_ids) != len(songs):
            for song in songs:
                if not song.song_id:
                    result.failed.append((song.song_name or "", "缺少酷我歌曲 ID"))
        if not music_ids:
            return result
        params = self._params({
            "playlistId": playlist_id,
            "musicIds": ",".join(music_ids),
        })
        with self._client(cookie, transport) as client:
            resp = client.post(
                ADD_URL, params=params,
                json={"playlistId": playlist_id, "musicIds": ",".join(music_ids)},
                headers=self._headers(cookie),
            )
        _check_status(resp, self.platform, "add")
        body = response_json(resp, platform=self.platform, phase="add")
        if _is_ok(body):
            result.added.extend(music_ids)
            if progress:
                progress(len(music_ids), len(music_ids))
            return result
        raise _map_error(body, self.platform, "add", "添加歌曲失败")


def _check_status(resp, platform: str, phase: str) -> None:
    """把网页端的 5xx/鉴权状态翻成能看懂的提示，避免暴露代理原文。"""
    if resp.status_code >= 500:
        raise PlatformRejectedError(
            f"酷我网页建单接口已下线（HTTP {resp.status_code}），只能在酷我 App 内建单",
            platform=platform, phase=phase,
        )
    if resp.status_code in (401, 403):
        raise AuthRequiredError(
            "登录状态不可用，请先在账号管理中重新登录",
            platform=platform, phase=phase,
        )


def _is_ok(body: dict) -> bool:
    if body.get("success") is True:
        return True
    try:
        return int(body.get("code", -1)) == 200
    except (TypeError, ValueError):
        return False


def _find_playlist_id(body: dict) -> str:
    data = body.get("data")
    candidates = [body, data]
    if isinstance(data, dict):
        for key in ("playlist", "info"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in ("id", "pid", "playlistId", "playlist_id"):
            value = item.get(key)
            if value not in (None, "", 0, "0"):
                return str(value)
    return ""


def _map_error(body: dict, platform: str, phase: str, fallback: str):
    message = str(body.get("message") or body.get("msg") or "")
    lowered = message.lower()
    if "login" in lowered or "未登录" in message or "登陆" in message:
        return AuthRequiredError(
            "登录状态不可用，请先在账号管理中重新登录",
            platform=platform, phase=phase,
        )
    if "频繁" in message or "rate" in lowered:
        return PlatformRejectedError(
            "酷我请求过于频繁，请稍后再试",
            platform=platform, phase=phase, retryable=True,
        )
    if "illegal" in lowered:
        return PlatformRejectedError(
            "酷我网页建单接口已下线（返回 illegal / 504），只能在酷我 App 内建单",
            platform=platform, phase=phase,
        )
    detail = f"{fallback}：{message}" if message else fallback
    return PlatformRejectedError(detail, platform=platform, phase=phase)
