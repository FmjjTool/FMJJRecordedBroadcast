"""音频标签读写核心：对齐 musictag（TagLibSharp）行为。

统一字段键：
    title, artist, album, albumartist, year, track, track_count,
    disc, disc_count, genre, composer, lyricist, comment, lyrics

补充能力：
    · 全格式：FLAC / OGG / OPUS / MP3 / M4A / MP4 / WMA / WAV / APE / WV /
      TTA / DSF / AIFF / MPC
    · 多值字段按连接符读取（默认 "/"）、写入时按 TagLib 语义写成单值
    · track/disc 支持 number + total（"3/12"）
    · 封面/多图片：ID3 APIC、FLAC Picture、OGG/OPUS METADATA_BLOCK_PICTURE、
      MP4 covr、ASF WM/Picture、APEv2 Cover Art (Front/Back)
    · ID3v2.3 / v2.4 版本可控（默认 2.3，与 musictag 一致）
    · 标签类型摘要、音频属性（位深/声道/采样率/比特率/时长）
    · 乱码修复：按原始字节用指定编码重解码 / 伪编码档位转码
    · 只改指定字段，不删除无关标签（不再整标签清空重写）
"""

from __future__ import annotations

import base64
import os
import re
import struct

import mutagen
import mutagen.asf
from mutagen.apev2 import APEBinaryValue, APEv2
from mutagen.flac import Picture
from mutagen.id3 import (
    APIC, COMM, ID3, TALB, TCOM, TCON, TDRC, TEXT, TIT2, TPE1, TPE2, TPOS,
    TRCK, USLT,
)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

SUPPORTED_EXT = {
    ".flac", ".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4", ".m4b", ".wma",
    ".wav", ".ape", ".wv", ".tta", ".dsf", ".aiff", ".aif", ".mpc",
}

# 字段顺序与 musictag 的表单/列一致
FIELDS = (
    "title", "artist", "album", "albumartist", "year", "track", "track_count",
    "disc", "disc_count", "genre", "composer", "lyricist", "comment", "lyrics",
)

TEXT_FIELDS = FIELDS

DEFAULT_CONNECTOR = "/"
DEFAULT_ID3_VERSION = 3

# TagLib.PictureType 名称（索引 = ID3 APIC type code；0 视为非图片附件）
PICTURE_TYPE_NAMES = [
    "Other", "File Icon", "Other File Icon", "Front Cover", "Back Cover",
    "Leaflet Page", "Media", "Lead Artist", "Artist", "Conductor",
    "Band", "Composer", "Lyricist", "Recording Location", "During Recording",
    "During Performance", "Movie Screen Capture", "Coloured Fish",
    "Illustration", "Band Logo",
]

# 标准 ID3 流派表（ID3v1 + Winamp 扩展，索引即 TCON 数值）
GENRES = [
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal",
    "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
    "Hard Rock", "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion",
    "Bebop", "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde",
    "Gothic Rock", "Progressive Rock", "Psychedelic Rock", "Symphonic Rock",
    "Slow Rock", "Big Band", "Chorus", "Easy Listening", "Acoustic",
    "Humour", "Speech", "Chanson", "Opera", "Chamber Music", "Sonata",
    "Symphony", "Booty Bass", "Primus", "Porn Groove", "Satire", "Slow Jam",
    "Club", "Tango", "Samba", "Folklore", "Ballad", "Power Ballad",
    "Rhythmic Soul", "Freestyle", "Duet", "Punk Rock", "Drum Solo",
    "A Cappella", "Euro-House", "Dance Hall", "Goa", "Drum & Bass",
    "Club-House", "Hardcore", "Terror", "Indie", "BritPop", "Negerpunk",
    "Polsk Punk", "Beat", "Christian Gangsta Rap", "Heavy Metal",
    "Black Metal", "Crossover", "Contemporary Christian", "Christian Rock",
    "Merengue", "Salsa", "Thrash Metal", "Anime", "JPop", "Synthpop",
    "Abstract", "Art Rock", "Baroque", "Bhangra", "Big Beat", "Breakbeat",
    "Chillout", "Downtempo", "Dub", "EBM", "Eclectic", "Electro",
    "Electroclash", "Emo", "Experimental", "Garage", "Global", "IDM",
    "Illbient", "Industro-Goth", "Jam Band", "Krautrock", "Leftfield",
    "Lounge", "Math Rock", "New Romantic", "Nu-Breakz", "Post-Punk",
    "Post-Rock", "Psytrance", "Shoegaze", "Space Rock", "Trop Rock",
    "World Music", "Neoclassical", "Audiobook", "Audio Theatre",
    "Neue Deutsche Welle", "Podcast", "Indie Rock", "G-Funk", "Dubstep",
    "Garage Rock", "Psybient",
]

_ID3_FRAME = {
    "title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
    "year": "TDRC", "genre": "TCON", "composer": "TCOM", "lyricist": "TEXT",
}

_VORBIS_KEY = {
    "title": ("TITLE",), "artist": ("ARTIST",), "album": ("ALBUM",),
    "albumartist": ("ALBUMARTIST", "ALBUM ARTIST", "ALBUM_ARTIST"),
    "year": ("DATE", "YEAR"), "genre": ("GENRE",), "composer": ("COMPOSER",),
    "lyricist": ("LYRICIST", "AUTHOR"), "comment": ("COMMENT",),
    "lyrics": ("LYRICS", "UNSYNCEDLYRICS"),
}

_APE_KEY = {
    "title": "Title", "artist": "Artist", "album": "Album",
    "albumartist": "Album Artist", "year": "Year", "genre": "Genre",
    "composer": "Composer", "lyricist": "Lyricist", "comment": "Comment",
    "lyrics": "Lyrics",
}

_MP4_KEY = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
    "albumartist": "aART", "year": "\xa9day", "genre": "\xa9gen",
    "composer": "\xa9wrt", "comment": "\xa9cmt", "lyrics": "\xa9lyr",
}

_ASF_KEY = {
    "title": "Title", "artist": "Author", "album": "WM/AlbumTitle",
    "albumartist": "WM/AlbumArtist", "year": "WM/Year",
    "genre": "WM/Genre", "composer": "WM/Composer", "lyricist": "WM/Writer",
    "comment": "Description", "lyrics": "WM/Lyrics",
}


# ==================== 通用工具 ====================

def _empty() -> dict:
    return {k: "" for k in FIELDS}


def _join(values, connector: str) -> str:
    out = [str(v).strip() for v in (values or []) if str(v).strip()]
    return (connector or DEFAULT_CONNECTOR).join(out)


def parse_number_count(value) -> tuple[int, int]:
    """'3/12' → (3, 12)；'3' → (3, 0)；非法 → (0, 0)。"""
    if value is None:
        return 0, 0
    s = str(value).strip()
    m = re.match(r"^\s*(\d+)\s*(?:/\s*(\d+))?", s)
    if not m:
        return 0, 0
    number = int(m.group(1))
    count = int(m.group(2)) if m.group(2) else 0
    return number, count


def _value_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return _join(v, DEFAULT_CONNECTOR)
    return str(v).strip()


def _year_of(text: str) -> str:
    m = re.search(r"(\d{4})", str(text or ""))
    return m.group(1) if m else ""


def genre_name(index: int) -> str:
    if 0 <= index < len(GENRES):
        return GENRES[index]
    return ""


def decode_genre_text(value: str) -> str:
    """'13' / '(13)' / '(13)Pop' → 'Pop'；非数字原样返回。"""
    s = str(value or "").strip()
    if not s:
        return ""
    m = re.match(r"^\(?\s*(\d+)\s*\)?\s*(.*)$", s)
    if not m:
        return s
    name = genre_name(int(m.group(1)))
    rest = (m.group(2) or "").strip()
    if name and rest:
        return name
    return name or s


def _decode_genre_values(values) -> str:
    """只对纯数字（可带括号、可多段斜杠）的流派做索引解码。

    "Pop/Funk" 这类真实文本流派必须原样保留，不能被斜杠拆开。
    """
    out = []
    for v in values or []:
        s = str(v).strip()
        if not s:
            continue
        if re.fullmatch(r"\(?\s*\d+\s*\)?", s):
            out.append(decode_genre_text(s))
        elif re.fullmatch(r"\(?\s*\d+\s*\)?(/(\(?\s*\d+\s*\)?))+", s):
            out.extend(decode_genre_text(part) for part in s.split("/"))
        else:
            out.append(s)
    return "/".join(out)


def _tagtypes_summary(path: str, audio) -> str:
    """仿 musictag 的 f(tagtypes)：'ID3v2.3,ID3v1' / 'FLAC' / 'Vorbis Comment'。"""
    parts = []
    tags = getattr(audio, "tags", None)
    try:
        if isinstance(tags, ID3):
            ver = getattr(tags, "version", None)
            if isinstance(ver, tuple) and len(ver) >= 2:
                parts.append(f"ID3v2.{ver[1]}")
            else:
                parts.append("ID3v2")
            if os.path.splitext(path)[1].lower() == ".mp3":
                try:
                    with open(path, "rb") as f:
                        f.seek(-128, os.SEEK_END)
                        if f.read(3) == b"TAG":
                            parts.append("ID3v1")
                except OSError:
                    pass
            return ",".join(parts)
        cls_name = audio.__class__.__name__
        if cls_name == "FLAC":
            return "FLAC"
        if cls_name in ("OggVorbis", "OggOpus", "Opus", "OggFLAC"):
            return "Vorbis Comment"
        if isinstance(audio, MP4):
            return "MPEG-4"
        if audio.__class__.__module__.startswith("mutagen.asf"):
            return "ASF"
        if cls_name in ("WAVE",):
            return "RIFF"
        if cls_name in ("AIFF", "DSF", "TrueAudio"):
            return "ID3v2"
        if cls_name in ("WavPack", "MonkeysAudio", "Musepack"):
            return "APE"
    except Exception:
        pass
    return ""


# ==================== ID3 系（MP3/WAV/DSF/AIFF/TTA） ====================

def _id3_text(tags: ID3, *frame_ids: str) -> str:
    for fid in frame_ids:
        frames = tags.getall(fid)
        if frames:
            values = []
            for fr in frames:
                values.extend(getattr(fr, "text", []) or [])
            joined = _join(values, DEFAULT_CONNECTOR)
            if joined:
                return joined
    return ""


def _id3_pick_comment(tags: ID3) -> str:
    frames = tags.getall("COMM")
    if not frames:
        return ""
    # 优先空描述（TagLib 的 Tag.Comment 语义）
    for fr in frames:
        if not (getattr(fr, "desc", "") or "").strip():
            return _text_value(fr.text)
    return _text_value(frames[0].text)


def _id3_pick_lyrics(tags: ID3) -> str:
    frames = tags.getall("USLT")
    if not frames:
        return ""
    for fr in frames:
        if not (getattr(fr, "desc", "") or "").strip():
            return _text_value(fr.text)
    return _text_value(frames[0].text)


def _text_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return "".join(str(v) for v in value)
    return str(value or "")


def _read_id3(tags: ID3) -> dict:
    out = _empty()
    out["title"] = _id3_text(tags, "TIT2")
    out["artist"] = _id3_text(tags, "TPE1")
    out["album"] = _id3_text(tags, "TALB")
    out["albumartist"] = _id3_text(tags, "TPE2")
    out["composer"] = _id3_text(tags, "TCOM")
    out["lyricist"] = _id3_text(tags, "TEXT")

    year = _id3_text(tags, "TDRC")
    if not year:
        year = _id3_text(tags, "TYER")
    out["year"] = _year_of(year)

    track, track_count = parse_number_count(_id3_text(tags, "TRCK"))
    disc, disc_count = parse_number_count(_id3_text(tags, "TPOS"))
    out["track"] = str(track) if track else ""
    out["track_count"] = str(track_count) if track_count else ""
    out["disc"] = str(disc) if disc else ""
    out["disc_count"] = str(disc_count) if disc_count else ""

    genre_values = []
    for fr in tags.getall("TCON"):
        genre_values.extend(getattr(fr, "text", []) or [])
    out["genre"] = _decode_genre_values(genre_values)
    out["comment"] = _id3_pick_comment(tags)
    out["lyrics"] = _id3_pick_lyrics(tags)
    return out


def _id3_encoding(id3_version: int) -> int:
    # ID3v2.3 只允许 Latin1/UTF-16；v2.4 允许 UTF-8
    return 1 if int(id3_version) == 3 else 3


def _id3_set_text(tags: ID3, frame_id: str, value: str, enc: int):
    tags.delall(frame_id)
    if value:
        cls = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TPE2": TPE2,
               "TDRC": TDRC, "TCON": TCON, "TCOM": TCOM, "TEXT": TEXT}[frame_id]
        if frame_id == "TDRC":
            tags.add(TDRC(encoding=enc, text=value))
        else:
            tags.add(cls(encoding=enc, text=[value]))


def _save_id3(audio, tags: dict, pictures, id3_version: int):
    if audio.tags is None:
        audio.add_tags()
    tag = audio.tags
    enc = _id3_encoding(id3_version)

    for field in ("title", "artist", "album", "albumartist", "composer",
                  "lyricist", "genre"):
        if field in tags:
            val = str(tags.get(field) or "").strip()
            _id3_set_text(tag, _ID3_FRAME[field], val, enc)

    if "year" in tags:
        year = _year_of(tags.get("year") or "")
        _id3_set_text(tag, "TDRC", year, enc)

    if "track" in tags or "track_count" in tags:
        number, _old = parse_number_count(tags.get("track"))
        count, _old2 = parse_number_count(tags.get("track_count"))
        tag.delall("TRCK")
        if number:
            tag.add(TRCK(encoding=enc, text=[f"{number}/{count}" if count else str(number)]))

    if "disc" in tags or "disc_count" in tags:
        number, _old = parse_number_count(tags.get("disc"))
        count, _old2 = parse_number_count(tags.get("disc_count"))
        tag.delall("TPOS")
        if number:
            tag.add(TPOS(encoding=enc, text=[f"{number}/{count}" if count else str(number)]))

    if "comment" in tags:
        tag.delall("COMM")
        val = str(tags.get("comment") or "").strip()
        if val:
            tag.add(COMM(encoding=enc, lang="chi", desc="", text=val))

    if "lyrics" in tags:
        tag.delall("USLT")
        val = str(tags.get("lyrics") or "")
        if val.strip():
            tag.add(USLT(encoding=enc, lang="chi", desc="", text=val))

    if pictures is not None:
        tag.delall("APIC")
        used_descs: set[str] = set()
        for index, pic in enumerate(pictures):
            desc = pic.desc or ""
            # mutagen 的 APIC HashKey 只含描述：空描述的多张图会互相覆盖
            if desc in used_descs:
                desc = f"Picture {index + 1}"
            used_descs.add(desc)
            tag.add(APIC(encoding=3, mime=pic.mime or "image/jpeg",
                         type=int(pic.type or 3), desc=desc,
                         data=pic.data))

    try:
        if int(id3_version) == 3:
            tag.update_to_v23()
            audio.save(v2_version=3)
        else:
            tag.update_to_v24()
            audio.save(v2_version=4)
    except TypeError:
        audio.save()


# ==================== Vorbis 系（FLAC/OGG/OPUS） ====================

def _vorbis_get(audio, keys) -> list:
    out = []
    for key in keys:
        try:
            vals = audio.get(key)
        except Exception:
            vals = None
        if vals:
            out.extend([str(v) for v in vals])
    return out


def _read_vorbis(audio) -> dict:
    out = _empty()
    for field, keys in _VORBIS_KEY.items():
        out[field] = _join(_vorbis_get(audio, keys), DEFAULT_CONNECTOR)
    out["year"] = _year_of(out["year"])

    track, track_count = parse_number_count(_join(_vorbis_get(audio, ("TRACKNUMBER",)), "/"))
    if not track_count:
        track_count = parse_number_count(_join(_vorbis_get(audio, ("TRACKTOTAL", "TOTALTRACKS")), "/"))[0]
    disc, disc_count = parse_number_count(_join(_vorbis_get(audio, ("DISCNUMBER",)), "/"))
    if not disc_count:
        disc_count = parse_number_count(_join(_vorbis_get(audio, ("DISCTOTAL", "TOTALDISCS")), "/"))[0]
    out["track"] = str(track) if track else ""
    out["track_count"] = str(track_count) if track_count else ""
    out["disc"] = str(disc) if disc else ""
    out["disc_count"] = str(disc_count) if disc_count else ""
    out["genre"] = _decode_genre_values([out["genre"]]) if out["genre"] else ""
    return out


def _vorbis_del(audio, *keys):
    for key in keys:
        try:
            if key in audio:
                del audio[key]
        except Exception:
            pass


def _vorbis_set(audio, key: str, value: str):
    _vorbis_del(audio, key)
    if value:
        audio[key] = [value]


def _save_vorbis(audio, tags: dict, pictures, is_flac: bool):
    for field, keys in _VORBIS_KEY.items():
        if field in tags:
            val = str(tags.get(field) or "").strip()
            if field == "lyrics":
                val = str(tags.get(field) or "")
            _vorbis_set(audio, keys[0], val)
            for alt in keys[1:]:
                _vorbis_del(audio, alt)

    if "year" in tags:
        _vorbis_set(audio, "DATE", _year_of(tags.get("year") or ""))

    if "track" in tags or "track_count" in tags:
        num = parse_number_count(tags.get("track"))[0]
        total = parse_number_count(tags.get("track_count"))[0]
        _vorbis_set(audio, "TRACKNUMBER", str(num) if num else "")
        _vorbis_set(audio, "TRACKTOTAL", str(total) if total else "")

    if "disc" in tags or "disc_count" in tags:
        num = parse_number_count(tags.get("disc"))[0]
        total = parse_number_count(tags.get("disc_count"))[0]
        _vorbis_set(audio, "DISCNUMBER", str(num) if num else "")
        _vorbis_set(audio, "DISCTOTAL", str(total) if total else "")

    if pictures is not None:
        if is_flac:
            audio.clear_pictures()
            for pic in pictures:
                p = Picture()
                p.data = pic.data
                p.mime = pic.mime or "image/jpeg"
                p.type = int(pic.type or 3)
                p.desc = pic.desc or ""
                audio.add_picture(p)
        else:
            _vorbis_del(audio, "metadata_block_picture", "METADATA_BLOCK_PICTURE")
            blocks = []
            for pic in pictures:
                p = Picture()
                p.data = pic.data
                p.mime = pic.mime or "image/jpeg"
                p.type = int(pic.type or 3)
                p.desc = pic.desc or ""
                blocks.append(base64.b64encode(p.write()).decode("ascii"))
            if blocks:
                audio["metadata_block_picture"] = blocks
    audio.save()


# ==================== MP4/M4A ====================

def _read_mp4(audio: MP4) -> dict:
    out = _empty()
    t = audio.tags or {}
    for field, key in _MP4_KEY.items():
        vals = t.get(key)
        out[field] = _join(vals, DEFAULT_CONNECTOR) if vals else ""
    # MP4 无标准作词家原子，用 iTunes freeform 保存（TagLib 同样会丢，这里补齐）
    freeform = t.get("----:com.apple.iTunes:LYRICIST")
    if freeform:
        out["lyricist"] = _join(
            [bytes(x).decode("utf-8", "replace") for x in freeform], DEFAULT_CONNECTOR)
    out["year"] = _year_of(out["year"])
    trkn = t.get("trkn")
    disk = t.get("disk")
    if trkn:
        entry = trkn[0]
        if isinstance(entry, (tuple, list)) and entry:
            number = entry[0]
            count = entry[1] if len(entry) > 1 else 0
        else:
            number, count = entry, 0
        out["track"] = str(number) if number else ""
        out["track_count"] = str(count) if count else ""
    if disk:
        entry = disk[0]
        if isinstance(entry, (tuple, list)) and entry:
            number = entry[0]
            count = entry[1] if len(entry) > 1 else 0
        else:
            number, count = entry, 0
        out["disc"] = str(number) if number else ""
        out["disc_count"] = str(count) if count else ""
    return out


def _save_mp4(audio: MP4, tags: dict, pictures, _id3_version: int):
    if audio.tags is None:
        audio.add_tags()
    t = audio.tags
    for field, key in _MP4_KEY.items():
        if field in tags:
            val = str(tags.get(field) or "")
            if field != "lyrics":
                val = val.strip()
            if key in t:
                del t[key]
            if val:
                t[key] = [val]
    if "lyricist" in tags:
        key = "----:com.apple.iTunes:LYRICIST"
        if key in t:
            del t[key]
        val = str(tags.get("lyricist") or "").strip()
        if val:
            t[key] = [MP4FreeForm(val.encode("utf-8"))]
    if "year" in tags:
        y = _year_of(tags.get("year") or "")
        if "\xa9day" in t:
            del t["\xa9day"]
        if y:
            t["\xa9day"] = [y]
    if "track" in tags or "track_count" in tags:
        num = parse_number_count(tags.get("track"))[0]
        total = parse_number_count(tags.get("track_count"))[0]
        if "trkn" in t:
            del t["trkn"]
        if num:
            t["trkn"] = [(num, total)]
    if "disc" in tags or "disc_count" in tags:
        num = parse_number_count(tags.get("disc"))[0]
        total = parse_number_count(tags.get("disc_count"))[0]
        if "disk" in t:
            del t["disk"]
        if num:
            t["disk"] = [(num, total)]
    if pictures is not None:
        if "covr" in t:
            del t["covr"]
        covers = []
        for pic in pictures:
            fmt = MP4Cover.FORMAT_PNG if "png" in (pic.mime or "").lower() else MP4Cover.FORMAT_JPEG
            covers.append(MP4Cover(pic.data, imageformat=fmt))
        if covers:
            t["covr"] = covers
    audio.save()


# ==================== ASF (WMA) ====================

def _asf_text(audio, key: str) -> str:
    try:
        v = audio.get(key)
    except Exception:
        return ""
    if v:
        try:
            return str(v[0]).strip()
        except Exception:
            return ""
    return ""


def _read_asf(audio) -> dict:
    out = _empty()
    for field, key in _ASF_KEY.items():
        out[field] = _asf_text(audio, key)
    out["year"] = _year_of(out["year"])
    track, track_count = parse_number_count(_asf_text(audio, "WM/TrackNumber"))
    disc, disc_count = parse_number_count(_asf_text(audio, "WM/PartOfSet"))
    out["track"] = str(track) if track else ""
    out["track_count"] = str(track_count) if track_count else ""
    out["disc"] = str(disc) if disc else ""
    out["disc_count"] = str(disc_count) if disc_count else ""
    return out


def _save_asf(audio, tags: dict, pictures, _id3_version: int):
    for field, key in _ASF_KEY.items():
        if field in tags:
            val = str(tags.get(field) or "")
            if field != "lyrics":
                val = val.strip()
            elif val and not val.strip():
                val = ""
            if key in audio:
                del audio[key]
            if val:
                audio[key] = val
    if "year" in tags:
        val = _year_of(tags.get("year") or "")
        if "WM/Year" in audio:
            del audio["WM/Year"]
        if val:
            audio["WM/Year"] = val
    if "track" in tags or "track_count" in tags:
        num = parse_number_count(tags.get("track"))[0]
        total = parse_number_count(tags.get("track_count"))[0]
        if "WM/TrackNumber" in audio:
            del audio["WM/TrackNumber"]
        if num:
            audio["WM/TrackNumber"] = f"{num}/{total}" if total else str(num)
    if "disc" in tags or "disc_count" in tags:
        num = parse_number_count(tags.get("disc"))[0]
        total = parse_number_count(tags.get("disc_count"))[0]
        if "WM/PartOfSet" in audio:
            del audio["WM/PartOfSet"]
        if num:
            audio["WM/PartOfSet"] = f"{num}/{total}" if total else str(num)
    if pictures is not None:
        for key in list(audio.keys()):
            if key.startswith("WM/Picture"):
                try:
                    del audio[key]
                except Exception:
                    pass
        if pictures:
            audio["WM/Picture"] = _build_asf_picture(pictures[0])
    audio.save()


def _parse_asf_picture(data: bytes):
    """WM/Picture: BYTE type + DWORD size + UTF-16LE mime\\0 + UTF-16LE desc\\0 + data."""
    try:
        pic_type = data[0]
        size = struct.unpack_from("<I", data, 1)[0]
        pos = 5
        mime, pos = _read_utf16z(data, pos)
        desc, pos = _read_utf16z(data, pos)
        img = data[pos:pos + size] if size else data[pos:]
        return Pic(data=img, mime=mime, type=pic_type, desc=desc)
    except Exception:
        return None


def _read_utf16z(data: bytes, pos: int) -> tuple[str, int]:
    end = pos
    while end + 1 < len(data):
        if data[end] == 0 and data[end + 1] == 0:
            break
        end += 2
    text = data[pos:end].decode("utf-16-le", "replace")
    return text, min(end + 2, len(data))


def _build_asf_picture(pic: Pic) -> bytes:
    mime = (pic.mime or "image/jpeg").encode("utf-16-le") + b"\x00\x00"
    desc = (pic.desc or "").encode("utf-16-le") + b"\x00\x00"
    return (bytes([int(pic.type or 3)]) + struct.pack("<I", len(pic.data))
            + mime + desc + pic.data)


# ==================== APEv2（APE/WV/MPC） ====================

def _ape_read_tag(path: str) -> APEv2 | None:
    try:
        return APEv2(path)
    except Exception:
        return None


def _read_ape(path: str) -> dict:
    t = _ape_read_tag(path)
    out = _empty()
    if t is None:
        return out
    for field, key in _APE_KEY.items():
        try:
            out[field] = str(t[key]).strip()
        except (KeyError, Exception):
            out[field] = ""
    out["year"] = _year_of(out["year"])
    track, track_count = parse_number_count(_ape_get(t, "Track", "TrackNumber"))
    disc, disc_count = parse_number_count(_ape_get(t, "Disc", "DiscNumber"))
    out["track"] = str(track) if track else ""
    out["track_count"] = str(track_count) if track_count else ""
    out["disc"] = str(disc) if disc else ""
    out["disc_count"] = str(disc_count) if disc_count else ""
    return out


def _ape_get(t: APEv2, *keys) -> str:
    for key in keys:
        try:
            return str(t[key]).strip()
        except Exception:
            continue
    return ""


def _save_ape(path: str, tags: dict, pictures, _id3_version: int):
    t = _ape_read_tag(path)
    if t is None:
        t = APEv2()
    for field, key in _APE_KEY.items():
        if field in tags:
            val = str(tags.get(field) or "")
            if field != "lyrics":
                val = val.strip()
            if key in t:
                del t[key]
            if val:
                t[key] = val
    if "year" in tags:
        val = _year_of(tags.get("year") or "")
        if "Year" in t:
            del t["Year"]
        if val:
            t["Year"] = val
    if "track" in tags or "track_count" in tags:
        num = parse_number_count(tags.get("track"))[0]
        total = parse_number_count(tags.get("track_count"))[0]
        for key in ("Track", "TrackNumber"):
            if key in t:
                del t[key]
        if num:
            t["Track"] = f"{num}/{total}" if total else str(num)
    if "disc" in tags or "disc_count" in tags:
        num = parse_number_count(tags.get("disc"))[0]
        total = parse_number_count(tags.get("disc_count"))[0]
        for key in ("Disc", "DiscNumber"):
            if key in t:
                del t[key]
        if num:
            t["Disc"] = f"{num}/{total}" if total else str(num)
    if pictures is not None:
        for key in list(t.keys()):
            if key.lower().startswith("cover art"):
                del t[key]
        for pic in pictures:
            key = "Cover Art (Back)" if int(pic.type or 3) == 4 else "Cover Art (Front)"
            name = (pic.desc or "cover").encode("utf-8", "replace")
            if not re.search(r"\.(jpe?g|png|gif|webp)$", name.decode("utf-8", "ignore"), re.I):
                ext = ".png" if "png" in (pic.mime or "").lower() else ".jpg"
                name += ext.encode()
            t[key] = APEBinaryValue(name + b"\x00" + pic.data)
    t.save(path)


# ==================== 图片 ====================

class Pic:
    """内嵌图片（data/mime/type/desc）。"""
    __slots__ = ("data", "mime", "type", "desc")

    def __init__(self, data: bytes = b"", mime: str = "", type: int = 3, desc: str = ""):
        self.data = bytes(data or b"")
        self.mime = mime or _sniff_mime(self.data)
        self.type = int(type or 3)
        self.desc = desc or ""

    def __repr__(self):
        return f"Pic({len(self.data)}B, {self.mime}, type={self.type})"


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


def _read_pictures_id3(tags: ID3) -> list[Pic]:
    out = []
    for apic in tags.getall("APIC"):
        if int(getattr(apic, "type", 0) or 0) == 0:
            continue
        if not apic.data:
            continue
        out.append(Pic(apic.data, apic.mime or "", int(apic.type or 0), apic.desc or ""))
    return out


def _read_pictures_flac(audio) -> list[Pic]:
    out = []
    for p in getattr(audio, "pictures", []) or []:
        if p.data:
            out.append(Pic(p.data, p.mime or "", int(p.type or 0), p.desc or ""))
    return out


def _read_pictures_ogg(audio) -> list[Pic]:
    out = []
    try:
        blocks = audio.get("metadata_block_picture") or []
    except Exception:
        blocks = []
    for block in blocks:
        try:
            out.append(_pic_from_flac_block(base64.b64decode(block)))
        except Exception:
            continue
    return out


def _pic_from_flac_block(data: bytes) -> Pic:
    p = Picture(data)
    return Pic(p.data, p.mime or "", int(p.type or 0), p.desc or "")


def _read_pictures_mp4(audio: MP4) -> list[Pic]:
    out = []
    covr = (audio.tags or {}).get("covr") or []
    for c in covr:
        data = bytes(c)
        mime = "image/png" if getattr(c, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
        out.append(Pic(data, mime, 3, ""))
    return out


def _read_pictures_asf(audio) -> list[Pic]:
    out = []
    try:
        attr = audio.get("WM/Picture")
    except Exception:
        attr = None
    if attr:
        for item in attr:
            raw = getattr(item, "value", None)
            if isinstance(raw, bytes):
                pic = _parse_asf_picture(raw)
                if pic and pic.data:
                    out.append(pic)
    return out


def _read_pictures_ape(path: str) -> list[Pic]:
    out = []
    t = _ape_read_tag(path)
    if t is None:
        return out
    for key, ptype in (("Cover Art (Front)", 3), ("Cover Art (Back)", 4)):
        try:
            raw = t[key].value
        except Exception:
            continue
        if isinstance(raw, bytes) and raw:
            if b"\x00" in raw:
                name, data = raw.split(b"\x00", 1)
            else:
                name, data = b"", raw
            out.append(Pic(data, _sniff_mime(data), ptype, name.decode("utf-8", "replace")))
    return out


def load_pictures(path: str) -> list[Pic]:
    """读全部内嵌图片（过滤 type=0 的非图片附件）。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".ape", ".mpc"):
            return _read_pictures_ape(path)
        if ext == ".wv":
            pics = _read_pictures_ape(path)
            if pics:
                return pics
            audio = mutagen.File(path)
            if audio is not None and isinstance(getattr(audio, "tags", None), ID3):
                return _read_pictures_id3(audio.tags)
            return []
        audio = mutagen.File(path)
        if audio is None:
            return []
        tags = getattr(audio, "tags", None)
        if isinstance(tags, ID3):
            return _read_pictures_id3(tags)
        if isinstance(audio, MP4):
            return _read_pictures_mp4(audio)
        if audio.__class__.__module__.startswith("mutagen.asf"):
            return _read_pictures_asf(audio)
        if audio.__class__.__name__ == "FLAC":
            return _read_pictures_flac(audio)
        if audio.__class__.__name__ in ("OggVorbis", "OggOpus", "Opus", "OggFLAC"):
            return _read_pictures_ogg(audio)
    except Exception:
        pass
    return []


def load_cover(path: str) -> tuple[bytes, str] | None:
    """读内嵌封面 → (bytes, mime)；无封面返回 None（兼容旧接口）。"""
    pics = load_pictures(path)
    if not pics:
        return None
    front = next((p for p in pics if int(p.type) == 3), pics[0])
    return front.data, front.mime or "image/jpeg"


def save_pictures(path: str, pictures: list[Pic]):
    """整体替换内嵌图片（空列表 = 清除）。"""
    ext = os.path.splitext(path)[1].lower()
    pictures = [p if isinstance(p, Pic) else Pic(**p) for p in (pictures or [])]
    if ext in (".ape", ".mpc"):
        _save_ape(path, {}, pictures, DEFAULT_ID3_VERSION)
        return
    if ext == ".wv":
        t = _ape_read_tag(path)
        if t is not None:
            _save_ape(path, {}, pictures, DEFAULT_ID3_VERSION)
            return
        audio = mutagen.File(path)
        if audio is not None and isinstance(getattr(audio, "tags", None), ID3):
            _save_id3(audio, {}, pictures, _detect_id3_version(path))
            return
        _save_ape(path, {}, pictures, DEFAULT_ID3_VERSION)
        return
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("无法解析的音频文件")
    tags = getattr(audio, "tags", None)
    cls_name = audio.__class__.__name__
    if isinstance(tags, ID3) or cls_name in ("WAVE", "AIFF", "DSF", "TrueAudio"):
        _save_id3(audio, {}, pictures, _detect_id3_version(path))
    elif isinstance(audio, MP4):
        _save_mp4(audio, {}, pictures, DEFAULT_ID3_VERSION)
    elif audio.__class__.__module__.startswith("mutagen.asf"):
        _save_asf(audio, {}, pictures, DEFAULT_ID3_VERSION)
    elif cls_name == "FLAC":
        _save_vorbis(audio, {}, pictures, True)
    elif cls_name in ("OggVorbis", "OggOpus", "Opus", "OggFLAC"):
        _save_vorbis(audio, {}, pictures, False)
    else:
        raise ValueError(f"{ext} 格式暂不支持内嵌封面")


def save_cover(path: str, data: bytes, mime: str):
    """写单张前置封面（保留其它图片）。"""
    pics = [p for p in load_pictures(path) if int(p.type) != 3]
    pics.insert(0, Pic(data, mime, 3, ""))
    save_pictures(path, pics)


def clear_cover(path: str) -> bool:
    """清除全部内嵌图片。"""
    try:
        save_pictures(path, [])
        return True
    except Exception:
        return False


# ==================== 统一读写入口 ====================

def _detect_id3_version(path: str) -> int:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        if head[:3] == b"ID3" and head[3] in (3, 4):
            return head[3]
    except OSError:
        pass
    # DSF：ID3 头在文件头指针位置
    try:
        with open(path, "rb") as f:
            if f.read(4) == b"DSD ":
                f.seek(20)
                ptr = struct.unpack("<Q", f.read(8))[0]
                f.seek(ptr)
                head = f.read(4)
                if head[:3] == b"ID3" and head[3] in (3, 4):
                    return head[3]
    except OSError:
        pass
    return DEFAULT_ID3_VERSION


def load_tags(path: str, connector: str = DEFAULT_CONNECTOR) -> dict:
    """读标签 → 统一字段 dict（缺失字段为空串）。"""
    ext = os.path.splitext(path)[1].lower()
    out = _empty()
    try:
        if ext in (".ape", ".mpc"):
            out = _read_ape(path)
        elif ext == ".wv":
            out = _read_ape(path)
            if not any(out.values()):
                audio = mutagen.File(path)
                if audio is not None and isinstance(getattr(audio, "tags", None), ID3):
                    out = _read_id3(audio.tags)
        else:
            audio = mutagen.File(path)
            if audio is None:
                return out
            tags = getattr(audio, "tags", None)
            if isinstance(tags, ID3):
                out = _read_id3(tags)
            elif isinstance(audio, MP4):
                out = _read_mp4(audio)
            elif audio.__class__.__module__.startswith("mutagen.asf"):
                out = _read_asf(audio)
            elif audio.__class__.__name__ == "TrueAudio":
                # TrueAudio 可能同时带 ID3v2 与 APEv2；ID3 优先
                if isinstance(tags, ID3):
                    out = _read_id3(tags)
                else:
                    out = _read_ape(path)
            else:
                out = _read_vorbis(audio)
    except Exception:
        return _empty()
    if connector and connector != DEFAULT_CONNECTOR:
        for field in ("artist", "genre", "composer", "albumartist"):
            if out.get(field):
                parts = [p for p in str(out[field]).split(DEFAULT_CONNECTOR) if p]
                out[field] = connector.join(parts)
    return out


def save_tags(path: str, tags: dict, *, id3_version: int = DEFAULT_ID3_VERSION,
              pictures: list[Pic] | None = None):
    """写入标签（只处理 tags 中出现的字段；空串 = 删除该字段）。"""
    if not tags and pictures is None:
        return
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ape", ".mpc"):
        _save_ape(path, tags, pictures, id3_version)
        return
    if ext == ".wv":
        t = _ape_read_tag(path)
        if t is not None:
            _save_ape(path, tags, pictures, id3_version)
            return
        audio = mutagen.File(path)
        if audio is None:
            raise ValueError("无法解析的音频文件")
        if isinstance(getattr(audio, "tags", None), ID3):
            _save_id3(audio, tags, pictures, id3_version)
        else:
            _save_ape(path, tags, pictures, id3_version)
        return
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("无法解析的音频文件")
    tags_obj = getattr(audio, "tags", None)
    cls_name = audio.__class__.__name__
    if isinstance(tags_obj, ID3) or cls_name in ("WAVE", "AIFF", "DSF", "TrueAudio"):
        _save_id3(audio, tags, pictures, _detect_id3_version(path) if tags_obj is None else id3_version)
    elif isinstance(audio, MP4):
        _save_mp4(audio, tags, pictures, id3_version)
    elif audio.__class__.__module__.startswith("mutagen.asf"):
        _save_asf(audio, tags, pictures, id3_version)
    elif cls_name == "FLAC":
        _save_vorbis(audio, tags, pictures, True)
    elif cls_name in ("OggVorbis", "OggOpus", "Opus", "OggFLAC"):
        _save_vorbis(audio, tags, pictures, False)
    else:
        _save_vorbis(audio, tags, pictures, False)


def _file_exists(path: str) -> bool:
    try:
        return os.path.isfile(path)
    except OSError:
        return False


def probe(path: str) -> dict:
    """音频技术信息 + 标签类型。"""
    ext = os.path.splitext(path)[1].lstrip(".").upper()
    empty = {"fmt": ext, "duration": 0, "bitrate": 0, "sr": 0, "bits": 0,
             "channels": 0, "tagtypes": ""}
    try:
        audio = mutagen.File(path)
        if audio is None:
            return empty
        info = getattr(audio, "info", None)
        duration = int(getattr(info, "length", 0) or 0)
        bitrate = int(getattr(info, "bitrate", 0) or 0)
        sr = int(getattr(info, "sample_rate", 0) or 0)
        if not sr and ext == "OPUS":
            sr = 48000  # Opus 固定 48kHz（mutagen 的 sample_rate 为 0）
        bits = int(getattr(info, "bits_per_sample", 0) or 0)
        if not bits and ext in ("MP3", "OGG", "OPUS", "WMA", "MPC"):
            bits = 16  # musictag 对有损格式统一显示 16bit（位深无实际意义）
        channels = int(getattr(info, "channels", 0) or 0)
        return {"fmt": ext, "duration": duration, "bitrate": bitrate, "sr": sr,
                "bits": bits, "channels": channels,
                "tagtypes": _tagtypes_summary(path, audio)}
    except Exception:
        return empty


def fmt_duration(sec: int) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_duration_ms(ms: int) -> str:
    ms = max(0, int(ms))
    total, millis = divmod(ms, 1000)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}.{millis:03d}"


# ==================== 编码修复 ====================

ENCODING_PROFILES = [
    "UTF-8", "UTF-16", "UTF-16LE", "UTF-16BE", "ISO-8859-1", "GB18030", "BIG5",
    "Shift_JIS", "EUC-JP", "EUC-KR",
    "GB=>BIG5", "GB=>Shift_JIS", "GB=>EUC-KR",
    "BIG5=>GB", "BIG5=>Shift_JIS", "BIG5=>EUC-KR",
    "ISO-8859-1=>GB", "ISO-8859-1=>BIG5", "ISO-8859-1=>Shift_JIS",
    "ISO-8859-1=>EUC-KR",
    "ASMO-708", "ISO-8859-6", "WINDOWS-1256", "ISO-8859-4", "WINDOWS-1257",
    "IBM852", "ISO-8859-2", "WINDOWS-1250", "CP866", "ISO-8859-5", "KOI8-R",
    "KOI8-U", "WINDOWS-1251", "ISO-8859-7", "WINDOWS-1253", "ISO-8859-8",
    "WINDOWS-1255", "WINDOWS-874", "ISO-8859-9", "WINDOWS-1254",
    "WINDOWS-1258", "WINDOWS-1252",
]

_CODEC = {
    "UTF-8": "utf-8", "UTF-16": "utf-16", "UTF-16LE": "utf-16-le",
    "UTF-16BE": "utf-16-be", "ISO-8859-1": "latin-1", "GB18030": "gb18030",
    "BIG5": "big5", "Shift_JIS": "shift_jis", "EUC-JP": "euc_jp",
    "EUC-KR": "euc_kr", "ASMO-708": "iso8859-6", "ISO-8859-6": "iso8859-6",
    "WINDOWS-1256": "cp1256", "ISO-8859-4": "iso8859-4",
    "WINDOWS-1257": "cp1257", "IBM852": "cp852", "ISO-8859-2": "iso8859-2",
    "WINDOWS-1250": "cp1250", "CP866": "cp866", "ISO-8859-5": "iso8859-5",
    "KOI8-R": "koi8_r", "KOI8-U": "koi8_u", "WINDOWS-1251": "cp1251",
    "ISO-8859-7": "iso8859-7", "WINDOWS-1253": "cp1253",
    "ISO-8859-8": "iso8859-8", "WINDOWS-1255": "cp1255", "WINDOWS-874": "cp874",
    "ISO-8859-9": "iso8859-9", "WINDOWS-1254": "cp1254",
    "WINDOWS-1258": "cp1258", "WINDOWS-1252": "cp1252",
}

_RAW_VOCAB = {
    "title": ("TIT2", "TITLE", "Title"),
    "artist": ("TPE1", "ARTIST", "Artist"),
    "album": ("TALB", "ALBUM", "Album"),
    "year": ("TDRC", "DATE", "Year"),
    "genre": ("TCON", "GENRE", "Genre"),
    "albumartist": ("TPE2", "ALBUMARTIST", "Album Artist"),
    "composer": ("TCOM", "COMPOSER", "Composer"),
    "lyricist": ("TEXT", "LYRICIST", "Lyricist"),
    "comment": (None, "COMMENT", "Comment"),
    "lyrics": (None, "LYRICS", "Lyrics"),
}


def _string_type_name(encoding_id: int) -> str:
    return {0: "Latin1", 1: "UTF16", 2: "UTF16BE", 3: "UTF8"}.get(int(encoding_id), "UTF8")


def _encode_by_string_type(text: str, encoding_id: int) -> bytes:
    enc = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(int(encoding_id), "utf-8")
    return str(text).encode(enc, "replace")


def _raw_field_data(audio, field: str) -> tuple[list[bytes], str, str]:
    """→ (原始字节块, 标签类型, 字符串编码类型)。"""
    tags = getattr(audio, "tags", None)
    if isinstance(tags, ID3):
        if field in ("comment", "lyrics"):
            frame_id = "COMM" if field == "comment" else "USLT"
            frames = tags.getall(frame_id)
            if not frames:
                return [], "", ""
            fr = next((f for f in frames if not (getattr(f, "desc", "") or "").strip()), frames[0])
            text = _text_value(getattr(fr, "text", ""))
            enc = int(getattr(fr, "encoding", 1) or 1)
            return ([_encode_by_string_type(text, enc)] if text else []), "ID3v2", _string_type_name(enc)
        frame_id = _RAW_VOCAB.get(field, (None,))[0]
        if not frame_id:
            return [], "", ""
        blocks = []
        enc = 1
        for fr in tags.getall(frame_id):
            enc = int(getattr(fr, "encoding", 1) or 1)
            for value in getattr(fr, "text", []) or []:
                if str(value):
                    blocks.append(_encode_by_string_type(str(value), enc))
        return blocks, ("ID3v2" if blocks else ""), (_string_type_name(enc) if blocks else "")
    try:
        if audio.__class__.__name__ == "FLAC" or audio.__class__.__name__ in ("OggVorbis", "OggOpus", "Opus", "OggFLAC"):
            key = _RAW_VOCAB.get(field, (None, None))[1]
            if not key:
                return [], "", ""
            vals = _vorbis_get(audio, (key,))
            return [str(v).encode("utf-8") for v in vals if str(v)], ("Vorbis Comment" if vals else ""), ("UTF8" if vals else "")
        if audio.__class__.__module__.startswith("mutagen.asf"):
            return [], "", ""
    except Exception:
        pass
    return [], "", ""


def _raw_field_data_ape(path: str, field: str) -> tuple[list[bytes], str, str]:
    t = _ape_read_tag(path)
    if t is None:
        return [], "", ""
    key = _RAW_VOCAB.get(field, (None, None, None))[2]
    if not key:
        return [], "", ""
    try:
        item = t[key]
    except Exception:
        return [], "", ""
    try:
        values = item.to_string_array() if hasattr(item, "to_string_array") else [str(item)]
    except Exception:
        values = [str(item)]
    blocks = [str(v).encode("utf-8") for v in values if str(v)]
    return blocks, ("APE" if blocks else ""), ("UTF8" if blocks else "")


def _normalize_encode_name(name: str) -> str:
    return {
        "UTF8": "UTF-8", "UTF16": "UTF-16", "UTF16BE": "UTF-16BE",
        "UTF16LE": "UTF-16LE", "Latin1": "ISO-8859-1",
    }.get(name, name or "GB18030")


def _decode_with_bom(data: bytes, codec: str) -> str:
    if codec == "utf-16":
        if data[:2] == b"\xff\xfe":
            return data[2:].decode("utf-16-le", "replace")
        if data[:2] == b"\xfe\xff":
            return data[2:].decode("utf-16-be", "replace")
        return data.decode("utf-16-le", "replace")
    return data.decode(codec, "replace")


def _trim_null(text: str) -> str:
    idx = text.find("\x00")
    return text if idx < 0 else text[:idx]


def decode_field_value(path: str, field: str, encoding: str,
                       current_text: str) -> str:
    """按 musictag 的编码修复逻辑重解字段（返回新文本）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ape", ".mpc", ".wv"):
        blocks, _tag_type, string_type = _raw_field_data_ape(path, field)
        if not blocks:
            audio = mutagen.File(path)
            if audio is not None:
                blocks, _tag_type, string_type = _raw_field_data(audio, field)
    else:
        audio = mutagen.File(path)
        if audio is None:
            return current_text
        blocks, _tag_type, string_type = _raw_field_data(audio, field)

    current_tag = load_tags(path).get(field, "")
    if blocks and current_tag == current_text:
        if "=>" in encoding:
            source = _pseudo_source(encoding, current_text, string_type)
            target = _pseudo_target(encoding)
            return transcode_text(current_text, source, target)
        codec = _CODEC.get(encoding)
        if not codec:
            return current_text
        parts = []
        for block in blocks:
            text = _trim_null(_decode_with_bom(block, codec))
            parts.append(decode_genre_text(text) if field == "genre" else text)
        return "; ".join(parts)

    if "=>" in encoding:
        return transcode_text(current_text, _pseudo_source(encoding, current_text, string_type),
                              _pseudo_target(encoding))
    codec = _CODEC.get(encoding)
    if not codec:
        return current_text
    source = _CODEC.get(_normalize_encode_name(string_type), "utf-8")
    try:
        raw = current_text.encode(source, "replace")
        return _decode_with_bom(raw, codec)
    except Exception:
        return current_text


def _pseudo_source(profile: str, current_text: str, string_type: str) -> str:
    if profile.startswith("GB=>"):
        return "gb18030"
    if profile.startswith("BIG5=>"):
        return "big5"
    if profile.startswith("ISO-8859-1=>"):
        return "latin-1"
    return _CODEC.get(_normalize_encode_name(string_type), "utf-8")


def _pseudo_target(profile: str) -> str:
    target = profile.split("=>", 1)[1] if "=>" in profile else profile
    return {"GB": "gb18030", "BIG5": "big5", "Shift_JIS": "shift_jis",
            "EUC-KR": "euc_kr"}.get(target, _CODEC.get(target, "utf-8"))


def transcode_text(value: str, source: str, target: str) -> str:
    try:
        raw = str(value).encode(source, "replace")
        return raw.decode(target, "replace")
    except Exception:
        return value
