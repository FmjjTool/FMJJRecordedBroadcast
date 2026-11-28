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

from music.models import AlbumInfo, PlaylistInfo, SongInfo
from toolbox import webui_music

MAX_REFERENCE = 2048
MAX_LYRIC_LINES = 100
MAX_LYRIC_CHARS = 12_000
MAX_COVER_BYTES = 4 * 1024 * 1024
MAX_COVER_SECONDS = 10.0
MAX_ENTITY_SONGS = 100
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
_ENTITY_LABELS = {"playlist": "歌单", "album": "专辑", "artist": "歌手"}
_ENTITY_PATHS = {
    "qq": {
        "playlist": (r"/n/ryqq/playlist/\d+/?", r"/playlist/\d+/?"),
        "album": (r"/n/ryqq/albumDetail/[A-Za-z0-9]+/?", r"/album/[A-Za-z0-9]+(?:\.html)?/?"),
        "artist": (r"/n/ryqq/singer/[A-Za-z0-9]+/?", r"/singer/[A-Za-z0-9]+/?"),
    },
    "netease": {
        "playlist": (r"/(?:#\/)?playlist/?",),
        "album": (r"/(?:#\/)?album/?",),
        "artist": (r"/(?:#\/)?artist/?",),
    },
    "kugou": {
        "playlist": (r"/yy/special/single/\d+\.html/?", r"/special/single/\d+\.html/?", r"/plist/\d+/?"),
        "album": (r"/album/\d+\.html/?",),
        "artist": (r"/singer/(?:info/[A-Za-z0-9]+|home/\d+|\d+)\.html?/?", r"/singer/info/[A-Za-z0-9]+/?"),
    },
    "kugou_lite": {
        "playlist": (r"/yy/special/single/\d+\.html/?", r"/special/single/\d+\.html/?", r"/plist/\d+/?"),
        "album": (r"/album/\d+\.html/?",),
        "artist": (r"/singer/(?:info/[A-Za-z0-9]+|home/\d+|\d+)\.html?/?", r"/singer/info/[A-Za-z0-9]+/?"),
    },
    "kuwo": {
        "playlist": (r"/playlist_detail/\d+/?", r"/playlists/\d+/?"),
        "album": (r"/album_detail/\d+/?",),
        "artist": (r"/(?:singer|artist)_detail/\d+/?",),
    },
    "5sing": {
        "playlist": (r"/[A-Za-z0-9_\-]+/dj/[0-9A-Fa-f]+\.html/?",),
        "album": (r"/album/\d+\.html/?",),
        "artist": (r"/[A-Za-z0-9_\-]+/default\.html/?",),
    },
}


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
    if (type(payload) is not dict or set(payload) not in ({"platform", "reference"},
                                                           {"platform", "reference", "entity"})
            or type(payload.get("platform")) is not str
            or type(payload.get("reference")) is not str):
        raise ValueError(_INVALID)
    platform, reference = payload["platform"], payload["reference"]
    entity = payload.get("entity", "song")
    if type(entity) is not str or entity not in ("song", "playlist", "album", "artist"):
        raise ValueError("请选择有效的音乐解析类型")
    if platform not in _HOSTS:
        raise ValueError("请选择单个平台解析歌曲")
    if not 1 <= len(reference) <= MAX_REFERENCE or _CONTROL.search(reference):
        raise ValueError(_INVALID)
    reference = reference.strip()
    entry = next((item for item in webui_music._platforms() if item[0] == platform), None)
    if entry is None:
        raise ValueError("音乐平台暂不可用，请稍后重试")
    if entity == "song":
        _song_id(platform, reference, entry[2])
    else:
        _entity_reference(platform, entity, reference, entry[2], allow_keyword=entity in ("album", "artist"))
    return {"platform": platform, "reference": reference, "entity": entity}


def _entity_path_allowed(platform, entity, parts) -> bool:
    fragment_path = (parts.fragment.split("?", 1)[0] if parts.fragment else "")
    candidate_paths = [parts.path or "/"] + (["/" + fragment_path.lstrip("#/")] if fragment_path else [])
    if parts.query and entity in ("playlist", "album", "artist"):
        # Query-style official URLs are accepted only for the platform's own host.
        return any(any(re.search(pattern, path, re.IGNORECASE) for path in candidate_paths)
                   for pattern in _ENTITY_PATHS.get(platform, {}).get(entity, ())) or (
                       platform == "netease" and any(re.search(r"/(?:playlist|album|artist)$", path, re.I) for path in candidate_paths))
    return any(any(re.fullmatch(pattern, path, re.IGNORECASE) for path in candidate_paths)
               for pattern in _ENTITY_PATHS.get(platform, {}).get(entity, ()))


def _valid_entity_id(platform, entity, value):
    if platform == "5sing" and entity == "playlist":
        return bool(re.fullmatch(r"https://5sing\.kugou\.com/[A-Za-z0-9_\-]+/dj/[0-9A-Fa-f]+\.html", value))
    if platform == "5sing" and entity == "artist":
        return bool(re.fullmatch(r"[\w\-\u0080-\uffff]{1,80}", value, re.UNICODE))
    if entity == "artist" and platform in ("kugou", "kugou_lite"):
        return bool(re.fullmatch(r"[A-Za-z0-9]{1,64}", value))
    if entity == "playlist" or platform in ("netease", "kugou", "kugou_lite", "kuwo", "5sing"):
        return bool(re.fullmatch(r"\d{1,20}", value)) and int(value) > 0
    if platform == "qq":
        return bool(re.fullmatch(r"[A-Za-z0-9]{10,64}", value)) and any(ch.isdigit() for ch in value)
    return False


def _entity_reference(platform, entity, reference, scraper_cls, *, allow_keyword=False):
    is_url = bool(re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", reference)) or reference.startswith("//")
    if is_url:
        parts = _checked_url(reference, _HOSTS[platform])
        if not _entity_path_allowed(platform, entity, parts):
            raise ValueError(f"请输入所选平台的正确{_ENTITY_LABELS[entity]}链接或 ID")
    extractor = getattr(scraper_cls, f"extract_{entity}_id", None)
    value = ""
    if extractor:
        try:
            value = str(extractor(reference) or "")
        except Exception:
            value = ""
    if not value and platform == "netease":
        if not is_url and re.fullmatch(r"\d{1,20}", reference):
            value = reference
        patterns = {
            "playlist": (r"(?:[?#&]|/)(?:id=)?(\d+)",),
            "album": (r"(?:[?#&]|/)(?:id=)?(\d+)",),
            "artist": (r"(?:[?#&]|/)(?:id=)?(\d+)",),
        }
        for pattern in patterns[entity]:
            match = re.search(pattern, reference)
            if match:
                value = match.group(1)
                break
    if value and _valid_entity_id(platform, entity, value):
        return {"id": value, "keyword": "", "is_url": is_url}
    if not is_url and allow_keyword and 1 <= len(reference) <= 120:
        return {"id": "", "keyword": reference, "is_url": False}
    raise ValueError(f"请输入所选平台的正确{_ENTITY_LABELS[entity]}链接或 ID")


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


def _entity_song_rows(platform, platform_name, songs, on_songs=None):
    bounded = [song for song in (songs or []) if isinstance(song, SongInfo)][:MAX_ENTITY_SONGS]
    captured = []
    rows = []
    for song in bounded:
        canonical = song.model_copy(update={"platform": platform,
                                            "platform_name": platform_name})
        captured.append(canonical)
        row = webui_music._row(canonical)
        for key in ("name", "singer", "album", "album_id"):
            row[key] = _text(row[key])
        row["cover"] = _cover_url(platform, canonical.cover_url)
        row["download_meta"] = {key: _text(value, 80)
                                 for key, value in row["download_meta"].items()}
        rows.append(row)
    if on_songs is not None:
        on_songs(captured)
    return rows, captured


def _netease_cookie_dict():
    from auth import get_netease_playlist_cookie
    from netease import parse_cookie_string
    raw = get_netease_playlist_cookie()
    cookies = parse_cookie_string(raw) if raw else {}
    if not cookies:
        raise ValueError("网易云实体解析需要有效登录状态，请先完成登录后重试")
    return cookies


def _netease_song(item, *, album_name=""):
    artists = item.get("artists") or item.get("ar") or []
    album = item.get("album") or item.get("al") or {}
    return SongInfo(song_id=str(item.get("id") or ""), song_name=item.get("name") or "",
                    title=item.get("name") or "", singer=" / ".join(str(a.get("name") or "") for a in artists if isinstance(a, dict)),
                    album_name=album_name or album.get("name") or "", album_id=str(album.get("id") or ""),
                    duration=int(item.get("duration") or item.get("dt") or 0) // 1000,
                    cover_url=album.get("picUrl") or "", platform="netease",
                    platform_name="网易云音乐", url=f"https://music.163.com/song?id={item.get('id') or ''}")


def _resolve_netease(entity, value, keyword, on_log, is_cancelled, on_songs):
    from netease.music_api import NeteaseAPI
    api = NeteaseAPI()
    cookies = _netease_cookie_dict()
    if keyword:
        if entity == "album":
            candidates = api.search_album(keyword, cookies, limit=10)
            return {"kind": "albums", "entities": [{"id": str(item.get("id") or ""), "name": item.get("name") or "", "artist": item.get("artist") or "", "count": 0} for item in candidates if item.get("id") and item.get("name")], "detail": {"name": keyword}}
        candidates = api.search_artist(keyword, cookies, limit=10)
        return {"kind": "artists", "entities": [{"id": str(item.get("id") or ""), "name": item.get("name") or "", "artist": "", "count": item.get("musicSize") or 0} for item in candidates if item.get("id") and item.get("name")], "detail": {"name": keyword}}
    if entity == "playlist":
        raw = api.get_playlist_detail(int(value), cookies, max_tracks=MAX_ENTITY_SONGS)
        songs = [_netease_song(item) for item in (raw.get("tracks") or [])[:MAX_ENTITY_SONGS]]
        rows, _ = _entity_song_rows("netease", "网易云音乐", songs, on_songs)
        detail = {"name": raw.get("name") or value, "creator": raw.get("creator") or "",
                  "song_count": raw.get("trackCount") or len(raw.get("tracks") or []),
                  "returned_count": len(rows), "description": raw.get("description") or ""}
    elif entity == "album":
        raw = api.get_album_detail(int(value), cookies, max_songs=MAX_ENTITY_SONGS)
        songs = [_netease_song(item, album_name=raw.get("name") or "") for item in (raw.get("songs") or [])[:MAX_ENTITY_SONGS]]
        rows, _ = _entity_song_rows("netease", "网易云音乐", songs, on_songs)
        detail = {"name": raw.get("name") or value, "singer": raw.get("artist") or "",
                  "release_date": str(raw.get("publishTime") or "")[:10],
                  "song_count": raw.get("size") or len(raw.get("songs") or []),
                  "returned_count": len(rows), "description": raw.get("description") or ""}
    else:
        raw = api.get_artist_detail(int(value), cookies)
        song_raw = api.get_artist_songs(int(value), limit=MAX_ENTITY_SONGS)
        songs = [_netease_song(item) for item in song_raw]
        rows, _ = _entity_song_rows("netease", "网易云音乐", songs, on_songs)
        albums = [{"id": str(item.get("id") or ""), "name": item.get("name") or "", "artist": item.get("artist") or "", "count": item.get("size") or 0}
                  for item in api.get_artist_albums(int(value), limit=30) if item.get("id") and item.get("name")]
        detail = {"name": raw.get("name") or value, "song_count": raw.get("musicSize") or len(rows),
                  "album_count": raw.get("albumSize") or len(albums), "description": raw.get("briefDesc") or "", "albums": albums}
    return {"kind": entity, "items": rows, "total": len(rows), "failures": [],
            "summary": f"网易云音乐 · {detail.get('name') or value} · 已读取 {len(rows)} 首",
            "detail": detail, "keyword": detail.get("name") or str(value)}


def resolve_entity(payload, on_log=None, is_cancelled=None, on_songs=None):
    body = validate_payload(payload)
    entity = body["entity"]
    if entity == "song":
        return resolve(body, on_log=on_log, is_cancelled=is_cancelled, on_songs=on_songs)
    _cancel(is_cancelled)
    platform, reference = body["platform"], body["reference"]
    if platform == "5sing" and entity == "album":
        raise ValueError("5sing暂不支持专辑解析，请选择其他平台")
    entry = next(item for item in webui_music._platforms() if item[0] == platform)
    _pid, name, scraper_cls = entry
    target = _entity_reference(platform, entity, reference, scraper_cls,
                               allow_keyword=entity in ("album", "artist"))
    if on_log:
        on_log(f"正在解析{name}{_ENTITY_LABELS[entity]}")
    if platform == "netease":
        try:
            outcome = _resolve_netease(entity, target["id"], target["keyword"], on_log, is_cancelled, on_songs)
        except ValueError:
            raise
        except Exception:
            raise ValueError(f"{name}{_ENTITY_LABELS[entity]}解析失败，请确认内容可访问后重试") from None
        outcome.setdefault("platform", platform)
        return outcome
    scraper = scraper_cls()
    if target["keyword"]:
        try:
            candidates = getattr(scraper, f"search_{entity}")(target["keyword"], limit=10)
        except Exception:
            raise ValueError(f"{name}{_ENTITY_LABELS[entity]}搜索失败，请稍后重试") from None
        entities = []
        for item in candidates or []:
            if isinstance(item, dict):
                ident = item.get("id") or item.get("album_id") or item.get("artist_id")
                title = item.get("name") or item.get("album_name") or ""
                artist = item.get("artist") or item.get("singer") or ""
                count = item.get("song_count") or item.get("size") or item.get("musicSize") or 0
            elif isinstance(item, AlbumInfo):
                ident, title = item.album_id, item.album_name or item.title
                artist, count = item.singer, item.song_count
            else:
                continue
            if ident and title:
                entities.append({"id": str(ident), "name": str(title),
                                 "artist": str(artist), "count": int(count or 0)})
        return {"kind": entity + "s", "entities": entities, "items": [], "total": 0,
                "failures": [], "summary": f"{name} · 找到 {len(entities)} 个{_ENTITY_LABELS[entity]}候选",
                "detail": {"name": target["keyword"]}, "keyword": target["keyword"]}
    getter = getattr(scraper, f"get_{entity}", None)
    if getter is None:
        raise ValueError(f"{name}暂不支持{_ENTITY_LABELS[entity]}解析")
    _cancel(is_cancelled)
    try:
        if entity == "artist" and platform == "5sing":
            detail_obj = getter(target["id"], max_songs=MAX_ENTITY_SONGS, fetch_durations=False)
        else:
            try:
                detail_obj = getter(target["id"], max_songs=MAX_ENTITY_SONGS)
            except TypeError:
                detail_obj = getter(target["id"])
    except InterruptedError:
        raise
    except Exception:
        raise ValueError(f"{name}{_ENTITY_LABELS[entity]}解析失败，请确认内容可访问后重试") from None
    _cancel(is_cancelled)
    if detail_obj is None:
        raise ValueError(f"未找到该{_ENTITY_LABELS[entity]}，请确认链接或 ID 后重试")
    if isinstance(detail_obj, dict):
        songs = list(detail_obj.get("songs") or [])
    else:
        songs = list(getattr(detail_obj, "songs", None) or [])
    rows, _ = _entity_song_rows(platform, name, songs, on_songs)
    if isinstance(detail_obj, PlaylistInfo):
        detail = {"name": detail_obj.name, "creator": detail_obj.creator, "song_count": detail_obj.song_count,
                  "returned_count": len(rows), "play_count": detail_obj.play_count, "tags": detail_obj.tags,
                  "description": detail_obj.description}
    elif isinstance(detail_obj, AlbumInfo):
        detail = {"name": detail_obj.album_name or detail_obj.title, "singer": detail_obj.singer,
                  "release_date": detail_obj.release_date, "album_type": detail_obj.album_type,
                  "language": detail_obj.language, "genre": detail_obj.genre, "company": detail_obj.company,
                  "song_count": detail_obj.song_count, "returned_count": len(rows), "description": detail_obj.description}
    else:
        raw = detail_obj if isinstance(detail_obj, dict) else {}
        albums = []
        for album in (raw.get("albums") or []):
            if isinstance(album, dict):
                ident = album.get("id") or album.get("album_id")
                title = album.get("name") or album.get("album_name")
                artist = album.get("artist") or album.get("singer") or ""
                count = album.get("count") or album.get("song_count") or 0
            else:
                ident, title = getattr(album, "album_id", ""), getattr(album, "album_name", "")
                artist, count = getattr(album, "singer", ""), getattr(album, "song_count", 0)
            if ident and title:
                albums.append({"id": str(ident), "name": str(title), "artist": str(artist), "count": int(count or 0)})
        detail = {"name": raw.get("name") or target["id"], "song_count": raw.get("song_count") or len(rows),
                  "album_count": raw.get("album_count") or len(albums), "fans": raw.get("fans") or 0,
                  "description": raw.get("desc") or "", "albums": albums}
    return {"kind": entity, "items": rows, "total": len(rows), "failures": [],
            "summary": f"{name} · {detail.get('name') or target['id']} · 已读取 {len(rows)} 首",
            "detail": detail, "keyword": detail.get("name") or target["id"]}
