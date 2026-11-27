"""Qt-free, read-only single-song resolution for the WebUI.

Only a validated public identity reaches the existing platform adapter. User
links are never fetched. Cover previews use a separate credential-free client;
all state and returned images are process-local, with no persistent writer.
"""

from __future__ import annotations

import base64
import io
import re
import time
from urllib.parse import unquote, urlsplit, urlunsplit

from music.models import SongInfo
from toolbox import webui_music

MAX_REFERENCE = 2048
MAX_LYRIC_LINES = 100
MAX_LYRIC_CHARS = 12_000
MAX_COVER_BYTES = 4 * 1024 * 1024
MAX_COVER_SECONDS = 10.0
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_URL_TEXT = re.compile(r"(?:https?:)?//[^\s<>\"']+", re.IGNORECASE)
_SECRET_TEXT = re.compile(
    r"\b(?:cookie|authorization|access[_ -]?token|refresh[_ -]?token|password|passwd|"
    r"client[_ -]?secret|token|session(?:id|key)?|api[_ -]?key)[\"']?\s*[:=]", re.IGNORECASE)
_HASH = re.compile(r"[a-fA-F0-9]{32}")
_NUMBER = re.compile(r"[0-9]{1,20}")
_HOSTS = {
    "qq": {"y.qq.com", "i.y.qq.com", "c.y.qq.com"},
    "netease": {"music.163.com", "y.music.163.com", "m.music.163.com"},
    "kugou": {"kugou.com", "www.kugou.com", "m.kugou.com"},
    "kugou_lite": {"kugou.com", "www.kugou.com", "m.kugou.com"},
    "kuwo": {"kuwo.cn", "www.kuwo.cn", "m.kuwo.cn"},
    "5sing": {"5sing.kugou.com"},
}
_KUGOU_COVERS = {"imge.kugou.com", "imgessl.kugou.com", "singerimg.kugou.com"}
_COVER_HOSTS = {
    "qq": {"y.gtimg.cn"},
    "netease": {f"p{number}.music.126.net" for number in range(1, 7)},
    "kugou": _KUGOU_COVERS,
    "kugou_lite": _KUGOU_COVERS,
    "kuwo": {f"img{number}.kuwo.cn" for number in range(1, 5)}
            | {f"img{number}.kwcdn.kuwo.cn" for number in range(1, 5)}
            | {"star.kuwo.cn"},
    "5sing": _KUGOU_COVERS | {"wsingbssdl.kugou.com"},
}
_INVALID = "请输入所选平台的正确歌曲链接或 ID"


def _cancel(callback):
    if callback and callback():
        raise InterruptedError("已取消单曲解析")


def _checked_url(value, hosts):
    """Validate an exact host before extracting an ID or fetching an image."""
    try:
        if (len(value) > MAX_REFERENCE or _CONTROL.search(value)
                or any(char.isspace() for char in value) or "\\" in value
                or _CONTROL.search(unquote(value)) or "\\" in unquote(value)):
            raise ValueError
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ("http", "https")
                or parts.hostname not in hosts or parts.username is not None
                or parts.password is not None
                or parts.port not in (None, 443 if parts.scheme.lower() == "https" else 80)):
            raise ValueError
        return parts
    except (ValueError, UnicodeError):
        raise ValueError(_INVALID) from None


def _valid_id(platform, value):
    if type(value) is not str:
        return False
    if platform in ("netease", "kuwo"):
        return bool(_NUMBER.fullmatch(value)) and int(value) > 0
    if platform == "qq":
        return bool(re.fullmatch(r"[A-Za-z0-9]{8,64}", value)) and (
            not value.isdigit() or int(value) > 0)
    if platform == "5sing" and "/" in value:
        return bool(re.fullmatch(r"(?:yc|fc|bz)/[0-9]{1,20}", value)) and int(value.split("/")[1]) > 0
    return bool(_HASH.fullmatch(value))


def _song_id(platform, reference, scraper_cls):
    is_url = bool(re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", reference)) or reference.startswith("//")
    if is_url:
        _checked_url(reference, _HOSTS[platform])
    elif not _valid_id(platform, reference):
        raise ValueError(_INVALID)
    try:
        song_id = scraper_cls.extract_song_id(reference)
    except InterruptedError:
        raise
    except Exception:
        raise ValueError(_INVALID) from None
    if not _valid_id(platform, song_id) or (not is_url and song_id != reference):
        raise ValueError(_INVALID)
    if is_url:
        # Existing extractors intentionally accept loose desktop input. Require
        # a complete URL token here, so a valid prefix cannot hide a bad ID.
        token = re.escape(song_id)
        boundary = r"(?=[/?&#]|$)"
        patterns = {
            "qq": [rf"(?:songDetail/|/song/){token}(?:\.html)?{boundary}",
                   rf"[?&#]songmid={token}(?=[&#]|$)"],
            "netease": [rf"/song/{token}{boundary}", rf"[?&#]id={token}(?=[&#]|$)"],
            "kugou": [rf"[?&#]hash={token}(?=[&#]|$)", rf"mixsong/{token}(?:\.html)?{boundary}"],
            "kuwo": [rf"(?:play_detail/|yinyue/){token}(?:\.html)?{boundary}",
                     rf"[?&]rid={token}(?=[&#]|$)"],
            "5sing": [rf"/{token}\.html{boundary}", rf"/m/{token}{boundary}"],
        }
        if not any(re.search(pattern, reference) for pattern in patterns["kugou" if platform == "kugou_lite" else platform]):
            raise ValueError(_INVALID)
    return song_id


def validate_payload(payload):
    """Return an independent, validated request without contacting a platform."""
    if (type(payload) is not dict or set(payload) != {"platform", "reference"}
            or type(payload.get("platform")) is not str
            or type(payload.get("reference")) is not str):
        raise ValueError(_INVALID)
    platform, reference = payload["platform"], payload["reference"]
    if platform not in _HOSTS:
        raise ValueError("请选择单个平台解析歌曲")
    if not 1 <= len(reference) <= MAX_REFERENCE or _CONTROL.search(reference):
        raise ValueError(_INVALID)
    reference = reference.strip()
    entry = next((item for item in webui_music._platforms() if item[0] == platform), None)
    if entry is None:
        raise ValueError("音乐平台暂不可用，请稍后重试")
    _song_id(platform, reference, entry[2])
    return {"platform": platform, "reference": reference}


def _text(value, limit=300, *, multiline=False):
    value = str(value or "")[:max(limit * 2, 1000)]
    if _SECRET_TEXT.search(value):
        return "[已移除敏感信息]"
    value = _URL_TEXT.sub("[链接已省略]", value)
    value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127
                    or multiline and char in "\n\r\t")
    return value[:limit].strip()


def _cover_url(platform, value):
    if type(value) is not str or not value:
        return ""
    try:
        parts = _checked_url(value, _COVER_HOSTS[platform])
        # Public image identity needs no signed query, fragment or account data.
        return urlunsplit((parts.scheme.lower(), parts.hostname, parts.path, "", ""))
    except ValueError:
        return ""


def _cover_preview(platform, value, is_cancelled):
    _cancel(is_cancelled)
    url = _cover_url(platform, value)
    if not url:
        return ""
    try:
        import httpx
        from PIL import Image
        from toolbox.webui_music_pictures import _checked_image

        started = time.monotonic()
        data = bytearray()
        with httpx.Client(timeout=httpx.Timeout(4.0, connect=3.0),
                          follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                if response.status_code != 200 or not response.headers.get("content-type", "").lower().startswith("image/"):
                    return ""
                length = response.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) > MAX_COVER_BYTES):
                    return ""
                _cancel(is_cancelled)
                for chunk in response.iter_bytes(64 * 1024):
                    _cancel(is_cancelled)
                    if len(data) + len(chunk) > MAX_COVER_BYTES or time.monotonic() - started > MAX_COVER_SECONDS:
                        return ""
                    data.extend(chunk)
        _cancel(is_cancelled)
        checked, _mime = _checked_image(bytes(data), thumbnail=True)
        _cancel(is_cancelled)
        with Image.open(io.BytesIO(checked)) as source:
            source.thumbnail((160, 160), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            source.save(output, format="PNG")
        _cancel(is_cancelled)
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except InterruptedError:
        raise
    except Exception:
        _cancel(is_cancelled)
        return ""


def resolve(payload, on_log=None, is_cancelled=None, on_songs=None):
    """Resolve one official song identity and expose bounded display details."""
    _cancel(is_cancelled)
    body = validate_payload(payload)
    platform, reference = body["platform"], body["reference"]
    _pid, name, scraper_cls = next(item for item in webui_music._platforms() if item[0] == platform)
    song_id = _song_id(platform, reference, scraper_cls)
    if on_log:
        on_log(f"正在解析{name}单曲")
    _cancel(is_cancelled)
    try:
        song = scraper_cls().get_song_detail(song_id)
    except InterruptedError:
        raise
    except Exception:
        _cancel(is_cancelled)
        raise ValueError("单曲解析失败，请确认歌曲可访问后稍后重试") from None
    _cancel(is_cancelled)
    if not isinstance(song, SongInfo) or not _text(song.song_name or song.title):
        raise ValueError("未找到该歌曲（可能已下架或链接失效）")
    song = song.model_copy(update={"song_id": song_id, "platform": platform, "platform_name": name})
    row = webui_music._row(song)
    for key in ("name", "singer", "album", "album_id"):
        row[key] = _text(row[key])
    row["cover"] = _cover_url(platform, song.cover_url)
    row["download_meta"] = {key: _text(value, 80) for key, value in row["download_meta"].items()}
    lyrics = "\n".join(song.lyrics[:MAX_LYRIC_CHARS * 2].splitlines()[:MAX_LYRIC_LINES])
    detail = {key: _text(getattr(song, key))
              for key in ("language", "genre", "release_date", "mode")}
    detail.update(lyrics=_text(lyrics, MAX_LYRIC_CHARS, multiline=True),
                  vip=bool(song.pay.fee or song.pay.pay_play or song.pay.pay_down),
                  cover_preview=_cover_preview(platform, song.cover_url, is_cancelled))
    _cancel(is_cancelled)
    if on_songs is not None:
        on_songs([song])
    _cancel(is_cancelled)
    return {"keyword": row["name"] or song_id, "items": [row], "total": 1,
            "failures": [], "summary": f"{name} · {row['name']} · 单曲已就绪", "detail": detail}
