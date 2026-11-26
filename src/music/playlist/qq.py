"""QQ音乐歌单写入（网页 API）。

创建：POST /splcloud/fcgi-bin/create_playlist.fcg
加歌：POST https://u.y.qq.com/cgi-bin/musicu.fcg
      module=music.musicasset.PlaylistDetailWrite, method=AddSonglist
登录态：uin + qm_keyst/qqmusic_key；接口使用固定 g_tk=5381。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
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
SIGN_SALT = "CJBPACrRuNy7"
QQ_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36 "
    "QQJSSDK/1.3 /ANDROIDQQMUSIC/11050108 QQMusic/11.5.1.8 H5/1"
)


def _g_tk(cookie: dict[str, str]) -> int:
    key = (cookie.get("qqmusic_key") or cookie.get("qm_keyst")
           or cookie.get("p_skey") or cookie.get("skey") or "")
    h = 5381
    for ch in key:
        h = ((h << 5) + h + ord(ch)) & 0x7FFFFFFF
    return h


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
    prepend = True
    note = "创建/加歌已实测通过（musicu 签名）"
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
            # 同名歌单已存在：直接复用它继续加歌，避免中断后重跑必须先删歌单
            existing = body.get("dirid") or (body.get("data") or {}).get("dirid")
            if existing:
                return str(existing)
            raise PlatformRejectedError(
                "已存在同名歌单，请在 QQ音乐里删除或换一个名称",
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
                    "g_tk": _g_tk(cookie),
                    "uin": uin,
                    "format": "json",
                    "inCharset": "utf-8",
                    "outCharset": "utf-8",
                    "notice": 0,
                    "platform": "h5",
                    "needNewCode": 1,
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
            payload_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            content = payload_text.encode("utf-8")
            headers = {
                "User-Agent": QQ_UA,
                "Content-Type": "application/json",
                "Origin": "https://i.y.qq.com",
                "Referer": "https://y.qq.com/",
            }

            def _post() -> httpx.Response:
                sign = ("zza" + "".join(random.choice("0123456789abcdef") for _ in range(10))
                        + hashlib.md5((SIGN_SALT + payload_text).encode()).hexdigest())
                return client.post(f"{MUSICU_URL}?sign={sign}",
                                   content=content, headers=headers)

            try:
                body = response_json(_post(), platform=self.platform, phase="add")
            except ParseChangedError:
                # 偶发风控页 / 空响应：换新签名重试一次，仍失败才上报
                time.sleep(1.0)
                body = response_json(_post(), platform=self.platform, phase="add")
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

    def playlist_url(self, playlist_id: str, cookie: dict[str, str], *,
                     transport: Optional[httpx.BaseTransport] = None) -> str:
        """dirId → 歌单公开页（真正链接用的是 tid，不是 dirId）。"""
        uin = _qq_uin(cookie)
        ident = str(playlist_id or "").strip()
        if not uin or not ident:
            return ""
        payload = {
            "comm": {
                "g_tk": _g_tk(cookie),
                "uin": uin,
                "format": "json",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "notice": 0,
                "platform": "h5",
                "needNewCode": 1,
            },
            "req_0": {
                "module": "music.musicasset.PlaylistBaseRead",
                "method": "GetPlaylistByUin",
                "param": {"uin": uin, "sin": 0, "size": 200, "songNumTag": 0},
            },
        }
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        sign = ("zza" + "".join(random.choice("0123456789abcdef") for _ in range(10))
                + hashlib.md5((SIGN_SALT + text).encode()).hexdigest())
        headers = {
            "User-Agent": QQ_UA,
            "Content-Type": "application/json",
            "Origin": "https://i.y.qq.com",
            "Referer": "https://y.qq.com/",
        }
        try:
            with self._client(cookie, transport) as client:
                resp = client.post(f"{MUSICU_URL}?sign={sign}",
                                   content=text.encode("utf-8"), headers=headers)
            body = response_json(resp, platform=self.platform, phase="url")
        except (ParseChangedError, RateLimitedError):
            return ""
        req = _musicu_req(body)
        return _playlist_url_from_account(req, ident)
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


def _playlist_url_from_account(req: dict, dirid: str) -> str:
    """GetPlaylistByUin 回执（已拆到 req_0）→ 歌单页链接（tid 才是网页用的 id）。"""
    try:
        code = int((req or {}).get("code", 0))
    except (TypeError, ValueError):
        code = 0
    if code != 0:
        return ""
    for item in (req.get("data") or {}).get("v_playlist") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("dirId") or item.get("dirid") or "") == str(dirid):
            tid = str(item.get("tid") or item.get("dissid") or "").strip()
            if tid:
                return f"https://y.qq.com/n/ryqq/playlist/{tid}"
    return ""


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
