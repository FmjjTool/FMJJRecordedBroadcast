"""QQ音乐歌单写入（网页 API）。

创建：POST /splcloud/fcgi-bin/create_playlist.fcg
加歌：GET  /splcloud/fcgi-bin/fcg_music_add2songdir.fcg
登录态：uin + qm_keyst/qqmusic_key；接口使用固定 g_tk=5381。
"""

from __future__ import annotations

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
ADD_URL = f"{BASE}/splcloud/fcgi-bin/fcg_music_add2songdir.fcg?g_tk=5381"
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
    verified = True
    batch_size = 50

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
        mids = [song.song_id for song in songs if song.song_id]
        if len(mids) != len(songs):
            for song in songs:
                if not song.song_id:
                    result.failed.append((song.song_name or song.song_id, "缺少QQ音乐歌曲 mid"))
        if not mids:
            return result

        params = {
            "midlist": ",".join(mids),
            "typelist": ",".join(["13"] * len(mids)),
            "dirid": playlist_id,
            "addtype": "",
            "formsender": 4,
            "r2": 0,
            "r3": 1,
            "utf8": 1,
        }
        headers = {"Referer": REFERER}
        with self._client(cookie, transport) as client:
            resp = client.get(ADD_URL, params=params, headers=headers)
        body = response_json(resp, platform=self.platform, phase="add")
        code = _code(body)
        if code == 0:
            result.added.extend(mids)
            if progress:
                progress(len(mids), len(mids))
            return result
        if code in (1, 1000):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="add",
            )
        if code == 429:
            raise RateLimitedError(
                "QQ音乐请求过于频繁，请稍后再试",
                platform=self.platform, phase="add", retryable=True,
            )
        raise PlatformRejectedError(
            _error_message(body, "添加歌曲失败"),
            platform=self.platform, phase="add",
        )


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
