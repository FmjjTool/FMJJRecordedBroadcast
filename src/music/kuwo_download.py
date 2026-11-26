"""酷我下载地址适配：只选择平台已提供、可用的非加密音频直链。

音质元数据不等于下载授权；没有直链的 FLAC/APE 条目不生成地址，
也不通过改写试听接口参数尝试取得受限文件。账号仅由统一管理层只读提供。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import FileInfo, SongInfo
from .scrapers.kuwo import _parse_formats
from .utils import get_client, make_headers, safe_json


@dataclass
class UrlResult:
    url: str = ""
    ext: str = ""
    quality: str = ""
    error: str = ""
    is_preview: bool = False


_DETAIL_URL = "https://m.kuwo.cn/newh5/singles/songinfoandlrc"
_QUALITIES = {"highest", "flac", "320k", "128k"}
_AUDIO_FORMATS = {"flac", "ape", "wav", "mp3", "ogg", "aac", "m4a", "wma"}
_LOSSLESS = {"flac", "ape", "wav"}
_MAX_DETAIL_CHARS = 2_000_000


def _safe_audio_url(value: str) -> str:
    """元数据中的地址仅接受酷我平台/CDN 域名，拒绝本机和任意跳转目标。"""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    if not any(host == suffix or host.endswith("." + suffix)
               for suffix in ("kuwo.cn", "kuwo.com")):
        return ""
    return value


def _format(file: FileInfo) -> str:
    ext = (file.extension or "").lower().lstrip(".")
    if not ext:
        try:
            ext = urlsplit(file.url or "").path.rsplit(".", 1)[-1].lower()
        except ValueError:
            return ""
    return ext if ext in _AUDIO_FORMATS else ""


def _bitrate(file: FileInfo) -> int:
    rate = file.bitrate or 0
    if not rate:
        match = re.search(r"(\d+)\s*(k(?:bps)?|bps)", file.quality or "", re.I)
        if match:
            rate = int(match.group(1))
            if match.group(2).lower().startswith("k"):
                rate *= 1000
    if 0 < rate < 10_000:
        rate *= 1000
    return rate


def _rank(file: FileInfo) -> tuple[int, int]:
    ext = _format(file)
    if ext in _LOSSLESS:
        return 1, {"flac": 3, "ape": 2, "wav": 1}[ext]
    return 0, _bitrate(file)


def _pick(files: list[FileInfo], quality: str) -> UrlResult | None:
    candidates = []
    for file in files:
        ext = _format(file)
        url = _safe_audio_url(file.url)
        rate = _bitrate(file)
        if not ext or not url:
            continue
        if quality == "flac" and ext != "flac":
            continue
        if quality in {"320k", "128k"}:
            if ext != "mp3" or rate != int(quality[:-1]) * 1000:
                continue
        candidates.append((file, url, ext, rate))
    if not candidates:
        return None
    file, url, ext, rate = max(candidates, key=lambda row: _rank(row[0]))
    tag = ext.upper() if ext in _LOSSLESS else (f"{rate // 1000}kbps" if rate else ext.upper())
    return UrlResult(url=url, ext="." + ext, quality=tag)


def _detail_files(song_id: str) -> tuple[list[FileInfo], str]:
    try:
        from auth import get_cookie
        account = get_cookie("kuwo")
    except Exception:
        return [], "酷我无法读取当前登录状态"
    headers = make_headers(referer="https://m.kuwo.cn/")
    if account:
        headers["Cookie"] = account
    try:
        with get_client(timeout=15) as client:
            # 禁止跟随重定向，确保登录态只发送到上面的固定平台主机。
            response = client.get(_DETAIL_URL, params={"musicId": song_id, "httpsStatus": 1},
                                  headers=headers, follow_redirects=False)
    except Exception:
        return [], "酷我歌曲详情请求异常，请稍后重试"
    if response.status_code in {401, 403}:
        return [], "酷我需要登录或当前账号没有该音质权限"
    if response.status_code == 429:
        return [], "酷我请求过于频繁，请稍后再试"
    if response.status_code != 200:
        return [], f"酷我歌曲详情请求失败（HTTP {response.status_code}）"
    if len(response.text) > _MAX_DETAIL_CHARS:
        return [], "酷我歌曲详情响应超出大小限制"
    payload = safe_json(response.text)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return [], "酷我歌曲详情接口可能已变化"
    wrapper = payload["data"]
    info = wrapper.get("songinfo", wrapper)
    if not isinstance(info, dict):
        return [], "酷我歌曲详情接口可能已变化"
    return _parse_formats(info.get("formats")), ""


def resolve_url(song: SongInfo, quality: str = "highest") -> UrlResult:
    """解析指定音质；显式档缺少授权直链时返回错误，不默降。"""
    quality = quality or "highest"
    if quality not in _QUALITIES:
        return UrlResult(error="酷我不支持所选音质")
    song_id = str(song.song_id or "").removeprefix("MUSIC_")
    if not song_id.isdigit():
        return UrlResult(error="酷我歌曲 ID 无效")
    files = list(song.files or [])
    selected = _pick(files, quality)
    if selected:
        # 最高档仍有仅声明但无直链的更高规格时，先查询一次详情。
        selected_rank = max((_rank(f) for f in files if _safe_audio_url(f.url)
                             and _format(f)), default=(-1, 0))
        if quality != "highest" or not any(
                _format(f) and _rank(f) > selected_rank for f in files):
            return selected
    detailed, error = _detail_files(song_id)
    selected = _pick(files + detailed, quality)
    if selected:
        return selected
    if error:
        return UrlResult(error=error)
    label = {"flac": "FLAC 无损", "320k": "320kbps MP3", "128k": "128kbps MP3"}.get(quality, "可用")
    return UrlResult(error=f"酷我未提供{label}下载地址（可能受歌曲权限或接口限制）")
