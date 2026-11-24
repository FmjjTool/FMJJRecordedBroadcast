"""QQ音乐歌单写入（网页 API）。

创建：POST /splcloud/fcgi-bin/create_playlist.fcg
加歌：POST https://u.y.qq.com/cgi-bin/musicu.fcg
      module=music.musicasset.PlaylistDetailWrite, method=AddSonglist
登录态：uin + qm_keyst/qqmusic_key；接口使用固定 g_tk=5381。
"""

from __future__ import annotations

import json
import re
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

BASE = "https://c.y.qq.com"
CREATE_URL = f"{BASE}/splcloud/fcgi-bin/create_playlist.fcg?g_tk=5381"
MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
SONG_DETAIL_URL = f"{BASE}/v8/fcg-bin/fcg_play_single_song.fcg"
REFERER = "https://y.qq.com/n/yqq/playlist"


def _qq_uin(cookie: dict[str, str]) -> str:
    for key in ("uin", "p_uin", "wxuin", "luin"):
        raw = str(cookie.get(key) or "").strip().lstrip("oO")
        digits = re.sub(r"\D", "", raw)
        if digits:
            return digits
    return ""


class QQPlaylistWriter(BasePlaylistWriter):
    platform = "qq"
    platform_name = "QQ音乐"
    verified = False
    note = "创建已实测通过；加歌需网页 ag-1 签名，当前未通"
    # 单首失败会让整批返回 invalid request，逐首加便于隔离无效 mid
    batch_size = 1

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        uin = _qq_uin(cookie)
        if not uin:
            raise AuthRequiredError(
                "登录状态缺少账号信息，请先在账号管理中重新登录",
                platform=self.platform, phase="create",
            )
        data = {
            "loginUin": uin,
            "hostUin": 0,
            "format": "json",
            "inCharset": "utf8",
            "outCharset": "utf8",
            "notice": 0,
            "platform": "yqq",
            "needNewCode": 0,
            "g_tk": 5381,
            "uin": uin,
            "name": name,
            "show": 1,
            "formsender": 1,
            "utf8": 1,
            "qzreferrer": "https://y.qq.com/portal/profile.html#sub=other&tab=create&",
        }
        headers = {
            "Referer": REFERER,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with self._client(cookie, transport) as client:
            resp = client.post(CREATE_URL, data=data, headers=headers)
        body = response_json(resp, platform=self.platform, phase="create")
        code = _code(body)
        if code == 0:
            dirid = body.get("dirid") or body.get("dissid") or (body.get("data") or {}).get("dirid")
            if not dirid:
                raise ParseChangedError(
                    "创建成功但未返回歌单 ID，接口可能已变化",
                    platform=self.platform, phase="create",
                )
            return str(dirid)
        if code in (1, 1000):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="create",
            )
        if code == 21:
            raise PlatformRejectedError(
                "已存在同名歌单，请换一个合集名称",
                platform=self.platform, phase="create",
            )
        if code == 429:
            raise RateLimitedError(
                "QQ音乐请求过于频繁，请稍后再试",
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
        uin = _qq_uin(cookie)
        if not uin:
            raise AuthRequiredError(
                "登录状态缺少账号信息，请先在账号管理中重新登录",
                platform=self.platform, phase="add",
            )
        v_song_info = []
        resolved: list[SongInfo] = []
        with self._client(cookie, transport) as client:
            for song in songs:
                try:
                    numeric_id, song_type = self._resolve_song_ref(client, song)
                except Exception:  # noqa: BLE001 - 单首解析失败不影响其余歌曲
                    numeric_id, song_type = 0, 0
                if not numeric_id:
                    result.failed.append(
                        (song.song_name or song.song_id, "缺少QQ音乐数字歌曲 ID"))
                    continue
                v_song_info.append({"songType": song_type, "songId": numeric_id})
                resolved.append(song)
            if not v_song_info:
                return result

            payload = {
                "comm": {
                    "cv": 4747474,
                    "ct": 24,
                    "format": "json",
                    "inCharset": "utf-8",
                    "outCharset": "utf-8",
                    "notice": 0,
                    "platform": "yqq.json",
                    "needNewCode": 1,
                    "uin": uin,
                    "g_tk": 5381,
                },
                "req_0": {
                    "module": "music.musicasset.PlaylistDetailWrite",
                    "method": "AddSonglist",
                    "param": {
                        "dirId": int(playlist_id) if str(playlist_id).isdigit() else playlist_id,
                        "v_songInfo": v_song_info,
                    },
                },
            }
            resp = client.post(
                MUSICU_URL,
                data={
                    "data": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    "format": "json",
                    "g_tk": 5381,
                },
                headers={"Referer": REFERER, "Origin": "https://y.qq.com",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        body = response_json(resp, platform=self.platform, phase="add")
        req = _musicu_req(body)
        code = _code(req)
        if code == 0:
            result.added.extend(song.song_id for song in resolved)
            if progress:
                progress(len(resolved), len(songs))
            return result
        if code in (1000, 1001):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="add",
            )
        if code == 429:
            raise RateLimitedError(
                "QQ音乐请求过于频繁，请稍后再试",
                platform=self.platform, phase="add", retryable=True,
            )
        message = _error_message(req, "添加歌曲失败")
        for song in resolved:
            result.failed.append((song.song_name or song.song_id, message))
        return result

    def _resolve_song_ref(self, client: httpx.Client, song: SongInfo) -> tuple[int, int]:
        numeric_id = self._extra_int(song, "id", "songid", "songId")
        song_type = 0
        try:
            song_type = int((song.extra or {}).get("type") or 0)
        except (TypeError, ValueError):
            song_type = 0
        if numeric_id:
            return numeric_id, song_type
        mid = str(song.song_id or "")
        if not mid:
            return 0, song_type
        if mid.isdigit():
            return int(mid), song_type
        resp = client.get(
            SONG_DETAIL_URL,
            params={"songmid": mid, "format": "json"},
            headers={"Referer": "https://y.qq.com/"},
        )
        data = response_json(resp, platform=self.platform, phase="resolve")
        info = data.get("data") or []
        if isinstance(info, list):
            info = info[0] if info else {}
        if not isinstance(info, dict):
            return 0, song_type
        numeric_id = info.get("id") or info.get("songid") or 0
        song_type = int(info.get("type") or 0)
        return int(numeric_id) if numeric_id else 0, song_type


def _musicu_req(body: dict) -> dict:
    if isinstance(body, list):
        body = body[0] if body else {}
    if not isinstance(body, dict):
        return {}
    req = body.get("req_0") or body.get("req_1")
    return req if isinstance(req, dict) else body


def _code(body: dict) -> int:
    try:
        return int(body.get("code", -1))
    except (TypeError, ValueError):
        return -1


def _error_message(body: dict, fallback: str) -> str:
    message = body.get("msg") or body.get("message") or body.get("subcode")
    if message:
        return f"{fallback}：{message}"
    return f"{fallback}（错误码 {body.get('code', '未知')}）"
