"""QQ 音乐原生 vkey 下载适配；由服务端决定当前账号可用的音质。

使用 file.media_mid 定位媒体文件，按 filename 匹配授权结果；
不使用第三方解析、不修改账号状态、不请求加密媒体格式。
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlsplit

from .models import SongInfo
from .utils import get_client, make_headers, safe_json


@dataclass
class QQDownloadUrl:
    url: str = ""
    ext: str = ""
    quality: str = ""
    error: str = ""
    is_preview: bool = False


_LEVELS = {"flac": ("F000", ".flac", "FLAC"),
           "320k": ("M800", ".mp3", "320kbps"),
           "128k": ("M500", ".mp3", "128kbps")}


def _audio_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443}):
            return ""
        if any(host == suffix or host.endswith("." + suffix)
               for suffix in ("qq.com", "qqmusic.com")):
            return value
    except (TypeError, ValueError):
        pass
    return ""


def _json(response) -> dict:
    if response.status_code != 200 or len(response.text) > 2_000_000:
        return {}
    data = safe_json(response.text)
    return data if isinstance(data, dict) else {}


def resolve_url(song: SongInfo, quality: str = "highest") -> QQDownloadUrl:
    """最高可用按 FLAC→320k→128k；显式档只请求对应文件。"""
    quality = quality or "highest"
    levels = list(_LEVELS) if quality == "highest" else [quality]
    if any(level not in _LEVELS for level in levels):
        return QQDownloadUrl(error="QQ音乐不支持所选音质")
    songmid = str(song.song_id or "")
    if not re.fullmatch(r"[A-Za-z0-9]+", songmid):
        return QQDownloadUrl(error="QQ音乐歌曲标识无效，请重新解析歌曲")
    try:
        from auth import get_cookie
        account = get_cookie("qq")
    except Exception:
        return QQDownloadUrl(error="QQ音乐无法读取当前登录状态")
    headers = make_headers(referer="https://y.qq.com/")
    if account:
        headers["Cookie"] = account
    jar = SimpleCookie()
    try:
        jar.load(account or "")
    except Exception:
        return QQDownloadUrl(error="QQ音乐登录状态不可用，请稍后重试")
    uin = next((jar[key].value.lstrip("o") for key in ("uin", "wxuin", "qqmusic_uin")
                if key in jar), "0")
    if not uin.isdigit():
        uin = "0"
    extra = song.extra or {}
    file_info = extra.get("file") if isinstance(extra.get("file"), dict) else {}
    media_mid = extra.get("media_mid") or extra.get("strMediaMid") or file_info.get("media_mid")
    try:
        with get_client(timeout=15) as client:
            if not media_mid:
                detail = _json(client.get(
                    "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg",
                    params={"songmid": songmid, "format": "json"},
                    headers=headers, follow_redirects=False))
                info = detail.get("data")
                if isinstance(info, list):
                    info = info[0] if info else {}
                if isinstance(info, dict):
                    media = info.get("file") or {}
                    media_mid = media.get("media_mid") or info.get("strMediaMid")
            if not media_mid or not re.fullmatch(r"[A-Za-z0-9]+", str(media_mid)):
                return QQDownloadUrl(error="QQ音乐未返回媒体文件标识，请重新解析歌曲")
            filenames = [f"{_LEVELS[level][0]}{media_mid}{_LEVELS[level][1]}" for level in levels]
            body = {
                "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0},
                "req_0": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey",
                          "param": {"filename": filenames,
                                    "guid": str(secrets.randbelow(9_000_000_000) + 1_000_000_000),
                                    "songmid": [songmid] * len(levels), "songtype": [0] * len(levels),
                                    "uin": uin, "loginflag": 1, "platform": "20"}},
            }
            response = client.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=body,
                                   headers=headers, follow_redirects=False)
            payload = _json(response)
    except Exception:
        return QQDownloadUrl(error="QQ音乐下载地址请求失败，请稍后重试")
    if response.status_code == 429:
        return QQDownloadUrl(error="QQ音乐请求过于频繁，请稍后再试")
    reply = payload.get("req_0") or {}
    data = reply.get("data") or {}
    if not isinstance(reply, dict) or not isinstance(data, dict):
        return QQDownloadUrl(error="QQ音乐下载接口可能已变化，请稍后重试")
    entries = data.get("midurlinfo") or []
    entries = entries if isinstance(entries, list) else []
    by_name = {entry.get("filename"): entry for entry in entries if isinstance(entry, dict)}
    servers = data.get("sip") or ["https://dl.stream.qqmusic.qq.com/"]
    servers = servers if isinstance(servers, list) else []
    for level, filename in zip(levels, filenames):
        entry = by_name.get(filename) or {}
        if entry.get("result", 0) not in (0, "0") or entry.get("subcode", 0) not in (0, "0"):
            continue
        path = entry.get("purl")
        if not isinstance(path, str) or not path:
            continue
        for server in servers:
            if not isinstance(server, str) or not _audio_url(server):
                continue
            url = _audio_url(urljoin(server, path))
            ext = _LEVELS[level][1]
            if url and urlsplit(url).path.lower().endswith(ext):
                return QQDownloadUrl(url=url, ext=ext, quality=_LEVELS[level][2])
    return QQDownloadUrl(error="QQ音乐未授权所选音质（需要登录、会员权限或歌曲暂无版权）")
