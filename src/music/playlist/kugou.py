"""酷狗音乐歌单写入（cloudlist 服务）。

创建：POST https://gateway.kugou.com/cloudlist.service/v5/add_list
加歌：POST https://gateway.kugou.com/cloudlist.service/v6/add_song
登录态：userid + token；请求参数使用 Android 版 MD5 签名。

协议参数与签名算法参考开源项目 KuGouMusicApi（MakcRe/KuGouMusicApi）的
playlist_add / playlist_tracks_add 模块。
"""

from __future__ import annotations

import hashlib
import json
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

BASE = "https://gateway.kugou.com"
CREATE_URL = f"{BASE}/cloudlist.service/v5/add_list"
ADD_URL = f"{BASE}/cloudlist.service/v6/add_song"
SIGN_SALT = "OIlwieks28dk2k092lksi2UIkp"
APPID = "1005"
CLIENTVER = "20489"
ANDROID_UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"


def _stringify(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def sign_params(params: dict, body: str) -> str:
    """Android 版签名：MD5(盐 + 按 key 排序的 k=v + 请求体 + 盐)。"""
    param_string = "".join(
        f"{key}={_stringify(value)}" for key, value in sorted(params.items())
    )
    return hashlib.md5(
        f"{SIGN_SALT}{param_string}{body}{SIGN_SALT}".encode("utf-8")
    ).hexdigest()


def _cookie_value(cookie: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(cookie.get(key) or "").strip()
        if value:
            return value
    return ""


def _kugoo_userid(cookie: dict[str, str]) -> str:
    userid = _cookie_value(cookie, "userid", "user_id", "kugouid")
    if userid:
        return userid
    raw = cookie.get("KuGoo") or ""
    for part in raw.split("&"):
        if part.lower().startswith("kugooid="):
            return part.split("=", 1)[1].strip()
    return ""


class KugouPlaylistWriter(BasePlaylistWriter):
    platform = "kugou"
    platform_name = "酷狗音乐"
    verified = False
    note = "云歌单需 App 扫码 token；网页登录态返回 20017，App token 已实测 16/16 加歌"
    batch_size = 10

    def _identity(self, cookie: dict[str, str], *, refresh: bool = False) -> tuple[str, str, str, str]:
        userid = _kugoo_userid(cookie)
        mid = _cookie_value(cookie, "kg_mid", "mid", "KUGOU_API_MID")
        dfid = _cookie_value(cookie, "kg_dfid", "dfid")
        token = ""
        auth_error = None
        if userid:
            # 云歌单只认 App token：用网页登录票据换取并缓存
            try:
                from ..kugou_app_auth import get_app_token
                token = get_app_token({**cookie, "userid": userid}, force=refresh)
            except AuthRequiredError as exc:
                auth_error = exc
        if not token:
            token = _cookie_value(cookie, "token", "t")
        if not userid or not token:
            raise auth_error or AuthRequiredError(
                "登录状态不完整，请先在账号管理中重新登录",
                platform=self.platform, phase="auth",
            )
        if auth_error is not None:
            # 网页 token 无法写云歌单，直接给出换取失败的真实原因
            raise auth_error
        return userid, token, mid, dfid

    def _signed_request(self, url: str, data: dict, extra_params: dict,
                        cookie: dict[str, str], userid: str, token: str,
                        mid: str, dfid: str, transport=None) -> dict:
        clienttime = int(time.time())
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        params = {
            "dfid": dfid or "-",
            "mid": mid or "-",
            "uuid": "-",
            "appid": APPID,
            "clientver": CLIENTVER,
            "clienttime": clienttime,
            "token": token,
            "userid": userid,
        }
        params.update(extra_params)
        params["signature"] = sign_params(params, body)
        headers = {
            "User-Agent": ANDROID_UA,
            "dfid": dfid or "-",
            "mid": mid or "-",
            "clienttime": str(clienttime),
            "kg-rc": "1",
            "kg-thash": "5d816a0",
            "kg-rec": "1",
            "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
            "Content-Type": "application/json",
        }
        with self._client(cookie, transport) as client:
            resp = client.post(url, params=params, content=body, headers=headers)
        return response_json(resp, platform=self.platform, phase="request")

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        return self._create(name, cookie, transport, refresh=False)

    def _create(self, name: str, cookie: dict[str, str], transport, *,
                refresh: bool) -> str:
        userid, token, mid, dfid = self._identity(cookie, refresh=refresh)
        clienttime = int(time.time())
        data = {
            "userid": userid,
            "token": token,
            "total_ver": 0,
            "name": name,
            "type": 0,
            "source": 1,
            "is_pri": 0,
            "list_create_gid": "",
            "from_shupinmv": 0,
        }
        params = {
            "last_time": clienttime,
            "last_area": "gztx",
            "userid": userid,
            "token": token,
        }
        body = self._signed_request(
            CREATE_URL, data, params, cookie, userid, token, mid, dfid, transport)
        if _needs_app_token(body) and not refresh:
            return self._create(name, cookie, transport, refresh=True)
        if _needs_app_token(body):
            raise _app_login_error(self.platform, "create")
        if _is_auth_error(body):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="create",
            )
        if not _is_ok(body):
            raise PlatformRejectedError(
                _error_message(body, "创建歌单失败"),
                platform=self.platform, phase="create",
            )
        playlist_id = _find_listid(body)
        if not playlist_id:
            raise ParseChangedError(
                "创建成功但未返回歌单 ID，接口可能已变化",
                platform=self.platform, phase="create",
            )
        return str(playlist_id)

    def _global_id(self, playlist_id: str, cookie: dict[str, str]) -> tuple[str, tuple]:
        """歌单 ID → (global_collection_id, 登录身份)；拿不到身份时返回 ("", ())。"""
        ident = str(playlist_id or "").strip()
        if not ident:
            return "", ()
        try:
            identity = self._identity(cookie)
        except Exception:  # noqa: BLE001 - 拿不到身份时不硬拼
            return "", ()
        if ident.startswith("collection_"):
            return ident, identity
        userid = identity[0]
        return f"collection_3_{userid}_{ident}_0", identity

    def _list_info(self, playlist_id: str, cookie: dict[str, str],
                   transport=None) -> dict:
        """云歌单详情（名字、是否下架）；查不到返回空字典。"""
        global_id, identity = self._global_id(playlist_id, cookie)
        if not global_id:
            return {}
        userid, token, mid, dfid = identity
        try:
            body = self._signed_request(
                "https://gateway.kugou.com/pubsongs/v3/get_list_info",
                {"data": [{"global_collection_id": global_id, "userid": 0,
                           "specialid": 0}], "userid": 0, "token": ""},
                {}, cookie, userid, token, mid, dfid, transport)
        except Exception:  # noqa: BLE001 - 查不到详情就当未知
            return {}
        items = body.get("data") or []
        return items[0] if items and isinstance(items[0], dict) else {}

    def playlist_song_ids(self, playlist_id: str, cookie: dict[str, str], *,
                          transport: Optional[httpx.BaseTransport] = None) -> set[str]:
        """云歌单里已有的歌曲 hash（重跑/复用时去重用）。"""
        global_id, identity = self._global_id(playlist_id, cookie)
        if not global_id:
            return set()
        userid, token, mid, dfid = identity
        page_size = 300
        found: set[str] = set()
        for page in range(1, 21):
            try:
                body = self._signed_request(
                    "https://gateway.kugou.com/pubsongs/v2/get_other_list_file_nofilt",
                    {"global_collection_id": global_id, "page": page,
                     "pagesize": page_size, "type": 0},
                    {"global_collection_id": global_id, "page": page,
                     "pagesize": page_size},
                    cookie, userid, token, mid, dfid, transport)
            except Exception:  # noqa: BLE001 - 读不到就当没有重复
                break
            data = body.get("data") or {}
            songs = data.get("songs") or []
            for song in songs:
                value = str(song.get("hash") or "").strip().upper()
                if value:
                    found.add(value)
            total = int(data.get("count") or 0)
            if len(songs) < page_size or page * page_size >= max(total, len(found)):
                break
        return found

    def playlist_exists(self, playlist_id: str, cookie: dict[str, str], *,
                        name: str = "",
                        transport: Optional[httpx.BaseTransport] = None) -> bool:
        info = self._list_info(playlist_id, cookie, transport)
        if not info:
            return False
        if info.get("is_drop"):
            return False
        got_name = str(info.get("name") or "").strip()
        return not (name and got_name and got_name != str(name).strip())

    def playlist_url(self, playlist_id: str, cookie: dict[str, str], *,
                     name: str = "",
                     transport: Optional[httpx.BaseTransport] = None) -> str:
        """酷狗云歌单的公开页。

        2026-11-26 实测：`/yy/special/single/` 除了数字 specialid，也接受
        `collection_3_<userid>_<listid>_0`（global_collection_id）。自建云歌单
        即使没发布到歌单广场（specialid 为空），用这个地址也能直接打开，
        页面标题就是歌单名，因此不再依赖 specialid。

        歌单不存在 / 已下架 / 名字对不上时返回空串，由调用方提示手工登记。
        """
        global_id, identity = self._global_id(playlist_id, cookie)
        if not global_id:
            return ""
        info = self._list_info(playlist_id, cookie, transport)
        if info:
            if info.get("is_drop"):
                return ""
            got_name = str(info.get("name") or "").strip()
            if name and got_name and got_name != str(name).strip():
                # 拼出来的歌单不是这一场，宁可不写
                return ""
            global_id = str(info.get("global_collection_id") or global_id)
        return f"https://www.kugou.com/yy/special/single/{global_id}.html"

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress=None) -> AddSongsResult:
        return self._add(playlist_id, songs, cookie, transport, progress, refresh=False)

    def _add(self, playlist_id: str, songs: list[SongInfo],
             cookie: dict[str, str], transport, progress, *, refresh: bool) -> AddSongsResult:
        result = AddSongsResult()
        userid, token, mid, dfid = self._identity(cookie, refresh=refresh)
        resources = []
        for song in songs:
            if not song.song_id:
                result.failed.append((song.song_name or "", "缺少酷狗歌曲 hash"))
                continue
            resources.append({
                "number": 1,
                "name": song.song_name or "",
                "hash": song.song_id,
                "size": 0,
                "sort": 0,
                "timelen": 0,
                "bitrate": 0,
                    "album_id": self._extra_int(song, "album_id", "albumid"),
                    # 云歌单按 mixsongid 定位音频：必须用 album_audio_id，
                    # 用单曲 audio_id 会命中别的资源（实测声声慢被换成有声书）。
                    "mixsongid": self._extra_int(song, "mixsongid", "mix_songid",
                                                 "album_audio_id", "audio_id"),
            })
        if not resources:
            return result

        clienttime = int(time.time())
        data = {
            "userid": userid,
            "token": token,
            "listid": str(playlist_id),
            "list_ver": 0,
            "type": 0,
            "slow_upload": 1,
            "scene": "false;null",
            "data": resources,
        }
        params = {
            "last_time": clienttime,
            "last_area": "gztx",
            "userid": userid,
            "token": token,
        }
        body = self._signed_request(
            ADD_URL, data, params, cookie, userid, token, mid, dfid, transport)
        if _needs_app_token(body) and not refresh:
            return self._add(playlist_id, songs, cookie, transport, progress, refresh=True)
        if _needs_app_token(body):
            raise _app_login_error(self.platform, "add")
        if _is_auth_error(body):
            raise AuthRequiredError(
                "登录状态不可用，请先在账号管理中重新登录",
                platform=self.platform, phase="add",
            )
        if not _is_ok(body):
            raise PlatformRejectedError(
                _error_message(body, "添加歌曲失败"),
                platform=self.platform, phase="add",
            )
        hashes = [item["hash"] for item in resources]
        result.added.extend(hashes)
        if progress:
            progress(len(hashes), len(hashes))
        return result


def _is_ok(body: dict) -> bool:
    if body.get("status") == 1:
        return True
    for key in ("error_code", "errcode", "code"):
        if key in body:
            try:
                return int(body[key]) == 0
            except (TypeError, ValueError):
                return False
    return False


def _app_login_error(platform: str, phase: str) -> AuthRequiredError:
    from ..kugou_app_auth import APP_LOGIN_HINT
    return AuthRequiredError(
        f"酷狗云歌单需要登录酷狗 App：{APP_LOGIN_HINT}",
        platform=platform, phase=phase,
    )


def _needs_app_token(body: dict) -> bool:
    """20017/20006 等说明网页 token 不能写云歌单，需要换 App token。"""
    for key in ("error_code", "errcode", "code"):
        if str(body.get(key)) in ("20017", "20006", "20001"):
            return True
    return _is_auth_error(body)


def _is_auth_error(body: dict) -> bool:
    for key in ("error_code", "errcode", "code"):
        try:
            if int(body.get(key, -1)) in (20010, 20011, 20012, 30001, 40001):
                return True
        except (TypeError, ValueError):
            continue
    message = str(body.get("error") or body.get("message") or body.get("msg") or "")
    return "token" in message.lower() or "login" in message.lower()


def _find_listid(body: dict) -> str:
    candidates = [body]
    data = body.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        info = data.get("info")
        if isinstance(info, list) and info:
            candidates.append(info[0])
        elif isinstance(info, dict):
            candidates.append(info)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in ("listid", "list_id", "listId", "specialid"):
            value = item.get(key)
            if value not in (None, "", 0, "0"):
                return str(value)
    return ""


def _error_message(body: dict, fallback: str) -> str:
    code = body.get("error_code") or body.get("errcode") or body.get("code")
    if str(code) in ("20017", "20006", "20001"):
        return (f"{fallback}：酷狗云歌单只认 App 登录令牌（错误码 {code}）；"
                "网页登录态无法写入，需在酷狗 App 扫码换取令牌")
    message = body.get("error") or body.get("message") or body.get("msg")
    if message:
        return f"{fallback}：{message}"
    return f"{fallback}（错误码 {code or '未知'}）"
