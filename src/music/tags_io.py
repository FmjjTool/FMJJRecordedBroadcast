"""音频标签读写核心：mutagen 多格式统一封装。

内部统一字段键（供 UI / 网络匹配使用）：
title, artist, album, albumartist, year, track, disc, genre,
composer, lyricist, comment, lyrics

支持：FLAC/OGG/OPUS/MP3(ID3)/M4A/MP4/WMA(ASF)/WAV/APE/WV/TTA/DSF/AIFF/MPC。
"""

from __future__ import annotations

import os

import mutagen
import mutagen.asf
from mutagen.apev2 import APEv2
from mutagen.flac import Picture
from mutagen.id3 import (
    APIC, COMM, ID3, TALB, TCOM, TDRC, TEXT, TIT2, TPE1, TPE2, TPOS, TCON,
    TRCK, USLT, SYLT,
)
from mutagen.mp4 import MP4, MP4Cover

SUPPORTED_EXT = {
    ".flac", ".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4", ".wma",
    ".wav", ".ape", ".wv", ".tta", ".dsf", ".aiff", ".aif", ".mpc",
}

FIELDS = ("title", "artist", "album", "albumartist", "year", "track", "disc",
          "genre", "composer", "lyricist", "comment", "lyrics")


# ==================== Vorbis 系（FLAC/OGG/OPUS） ====================

def _vorbis_read(audio) -> dict:
    t = audio.tags or {}

    def one(*keys):
        for k in keys:
            v = t.get(k)
            if v:
                return str(v[0]).strip()
        return ""
    return {
        "title": one("title"), "artist": one("artist"), "album": one("album"),
        "albumartist": one("albumartist", "album_artist"), "year": one("date", "year"),
        "track": one("tracknumber"), "disc": one("discnumber"),
        "genre": one("genre"), "composer": one("composer"),
        "lyricist": one("lyricist", "author"), "comment": one("comment"),
        "lyrics": one("lyrics", "unsyncedlyrics"),
    }


def _vorbis_write(path: str, tags: dict):
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("无法解析的音频文件")
    audio.delete()  # 清空后重写（保证移除的字段真正删除）
    t = audio.tags if audio.tags is not None else audio
    m = {"title": "title", "artist": "artist", "album": "album",
         "albumartist": "albumartist", "year": "date", "track": "tracknumber",
         "disc": "discnumber", "genre": "genre", "composer": "composer",
         "lyricist": "lyricist", "comment": "comment", "lyrics": "lyrics"}
    cover_backup = None
    if path.lower().endswith(".flac"):
        cover_backup = load_cover(path)  # FLAC delete() 会连封面一起删，先备份
    for k, vk in m.items():
        v = (tags.get(k) or "").strip()
        if v:
            t[vk] = [v]
    audio.tags = t
    audio.save()
    if cover_backup:
        data, mime = cover_backup
        save_cover(path, data, mime)


# ==================== ID3 系（MP3/WAV/DSF/AIFF） ====================

def _id3_read(tags: ID3) -> dict:
    def one(f):
        v = tags.getall(f)
        return str(v[0]).strip() if v else ""
    return {
        "title": one("TIT2"), "artist": one("TPE1"), "album": one("TALB"),
        "albumartist": one("TPE2"), "year": one("TDRC"),
        "track": one("TRCK").split("/")[0], "disc": one("TPOS").split("/")[0],
        "genre": one("TCON"), "composer": one("TCOM"),
        "lyricist": one("TEXT"), "comment": one("COMM::'chi'") or one("COMM::XXX") or one("COMM"),
        "lyrics": one("USLT::'chi'") or one("USLT::XXX") or one("USLT"),
    }


def _id3_write(path: str, tags: dict):
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("无法解析的音频文件")
    if audio.tags is None:
        audio.add_tags()
    t = audio.tags
    for f in ("TIT2", "TPE1", "TALB", "TPE2", "TDRC", "TRCK", "TPOS", "TCON", "TCOM", "TEXT"):
        t.delall(f)
    for comm in ("COMM", "USLT"):
        t.delall(comm)
    m = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TPE2": "albumartist",
         "TDRC": "year", "TCON": "genre", "TCOM": "composer", "TEXT": "lyricist"}
    for f, k in m.items():
        v = (tags.get(k) or "").strip()
        if v:
            t.setall(f, [globals()[f](encoding=3, text=v)])
    v = (tags.get("track") or "").strip()
    if v:
        t.setall("TRCK", [TRCK(encoding=3, text=v)])
    v = (tags.get("disc") or "").strip()
    if v:
        t.setall("TPOS", [TPOS(encoding=3, text=v)])
    v = (tags.get("comment") or "").strip()
    if v:
        t.setall("COMM", [COMM(encoding=3, lang="chi", desc="", text=v)])
    v = (tags.get("lyrics") or "").strip()
    if v:
        t.setall("USLT", [USLT(encoding=3, lang="chi", desc="", text=v)])
    audio.save()


# ==================== MP4/M4A ====================

_MP4_KEYS = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
             "albumartist": "aART", "year": "\xa9day", "genre": "\xa9gen",
             "composer": "\xa9wrt", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}


def _mp4_read(audio: MP4) -> dict:
    t = audio.tags or {}

    def one(k):
        v = t.get(k)
        return str(v[0]).strip() if v else ""
    trkn = t.get("trkn")
    disk = t.get("disk")
    return {
        **{k: one(vk) for k, vk in _MP4_KEYS.items()},
        "track": str(trkn[0][0]) if trkn and trkn[0][0] else "",
        "disc": str(disk[0][0]) if disk and disk[0][0] else "",
        "lyricist": "",
    }


def _mp4_write(path: str, tags: dict):
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    t = audio.tags
    for vk in list(_MP4_KEYS.values()) + ["trkn", "disk"]:
        if vk in t:
            del t[vk]
    for k, vk in _MP4_KEYS.items():
        v = (tags.get(k) or "").strip()
        if v:
            t[vk] = [v]
    v = (tags.get("track") or "").strip()
    if v.isdigit():
        t["trkn"] = [(int(v), 0)]
    v = (tags.get("disc") or "").strip()
    if v.isdigit():
        t["disk"] = [(int(v), 0)]
    audio.save()


# ==================== ASF (WMA) ====================

def _asf_read(audio) -> dict:
    def one(*keys):
        for k in keys:
            v = audio.get(k)
            if v and str(v[0]).strip():
                return str(v[0]).strip()
        return ""
    return {"title": one("Title"), "artist": one("Author"), "album": one("WM/AlbumTitle"),
            "albumartist": one("WM/AlbumArtist"), "year": one("WM/Year"),
            "track": one("WM/TrackNumber"), "genre": one("WM/Genre"),
            "composer": one("WM/Composer"), "lyricist": one("WM/Writer"),
            "comment": one("Description"), "lyrics": one("WM/Lyrics"),
            "disc": one("WM/PartOfSet")}


def _asf_write(path: str, tags: dict):
    audio = mutagen.asf.ASF(path)
    m = {"title": "Title", "artist": "Author", "album": "WM/AlbumTitle",
         "albumartist": "WM/AlbumArtist", "year": "WM/Year", "track": "WM/TrackNumber",
         "disc": "WM/PartOfSet", "genre": "WM/Genre", "composer": "WM/Composer",
         "lyricist": "WM/Writer", "comment": "Description", "lyrics": "WM/Lyrics"}
    for k, ak in m.items():
        v = (tags.get(k) or "").strip()
        if v:
            audio[ak] = [v]
        elif ak in audio:
            del audio[ak]
    audio.save()


# ==================== APEv2（APE/WV/MPC） ====================

_APE_KEYS = {"title": "Title", "artist": "Artist", "album": "Album", "year": "Year",
             "track": "Track", "genre": "Genre", "composer": "Composer",
             "lyricist": "Lyricist", "comment": "Comment", "lyrics": "Lyrics"}


def _ape_read(path: str) -> dict:
    try:
        t = APEv2(path)
    except Exception:
        return {k: "" for k in FIELDS}

    def one(k):
        try:
            return str(t[k]).strip()
        except KeyError:
            return ""
    return {k: one(vk) for k, vk in _APE_KEYS.items()} | {"albumartist": "", "disc": ""}


def _ape_write(path: str, tags: dict):
    try:
        t = APEv2(path)
    except Exception:
        t = APEv2()
    for k, ak in _APE_KEYS.items():
        v = (tags.get(k) or "").strip()
        if v:
            t[ak] = v
        elif ak in t:
            del t[ak]
    t.save(path)


# ==================== 统一入口 ====================

def load_tags(path: str) -> dict:
    """读标签 → 统一字段 dict（缺失字段为空串）。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".ape", ".wv", ".mpc"):
            return _ape_read(path)
        audio = mutagen.File(path, easy=False)
        if audio is None:
            return {k: "" for k in FIELDS}
        if isinstance(audio.tags, ID3):
            return _id3_read(audio.tags)
        if isinstance(audio, MP4):
            return _mp4_read(audio)
        if audio.__class__.__module__.startswith("mutagen.asf"):
            return _asf_read(audio)
        return _vorbis_read(audio)   # FLAC/OGG/OPUS
    except Exception:
        return {k: "" for k in FIELDS}


def save_tags(path: str, tags: dict) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ape", ".wv", ".mpc"):
        _ape_write(path, tags)
    elif ext in (".mp3", ".wav", ".dsf", ".aiff", ".aif"):
        _id3_write(path, tags)
    elif ext in (".m4a", ".mp4"):
        _mp4_write(path, tags)
    elif ext == ".wma":
        _asf_write(path, tags)
    else:
        _vorbis_write(path, tags)


def load_cover(path: str) -> tuple[bytes, str] | None:
    """读内嵌封面 → (bytes, mime)；无封面返回 None。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".flac":
            audio = mutagen.File(path)
            pics = audio.pictures if audio else []
            if pics:
                return pics[0].data, pics[0].mime or "image/jpeg"
        elif ext in (".mp3", ".wav", ".dsf", ".aiff", ".aif"):
            audio = mutagen.File(path)
            if audio and isinstance(audio.tags, ID3):
                for apic in audio.tags.getall("APIC"):
                    return apic.data, apic.mime or "image/jpeg"
        elif ext in (".m4a", ".mp4"):
            audio = MP4(path)
            covr = (audio.tags or {}).get("covr")
            if covr:
                c = covr[0]
                mime = "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(c), mime
        elif ext == ".wma":
            audio = mutagen.asf.ASF(path)
            for k, v in audio.items():
                if "Picture" in k and v and hasattr(v[0], "value"):
                    p = v[0]
                    return p.value, getattr(p, "mime", "image/jpeg")
    except Exception:
        pass
    return None


def save_cover(path: str, data: bytes, mime: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".flac":
        audio = mutagen.File(path)
        audio.clear_pictures()
        pic = Picture()
        pic.data, pic.mime = data, mime
        pic.type = 3   # Front Cover
        audio.add_picture(pic)
        audio.save()
    elif ext in (".mp3", ".wav", ".dsf", ".aiff", ".aif"):
        audio = mutagen.File(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.setall("APIC", [APIC(
            encoding=3, mime=mime, type=3, desc="Front Cover", data=data)])
        audio.save()
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        audio.save()
    else:
        raise ValueError(f"{ext} 格式暂不支持内嵌封面")


def clear_cover(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".flac":
            audio = mutagen.File(path)
            audio.clear_pictures()
            audio.save()
        elif ext in (".mp3", ".wav", ".dsf", ".aiff", ".aif"):
            audio = mutagen.File(path)
            if audio and audio.tags is not None:
                audio.tags.delall("APIC")
                audio.save()
        elif ext in (".m4a", ".mp4"):
            audio = MP4(path)
            if audio.tags and "covr" in audio.tags:
                del audio.tags["covr"]
                audio.save()
        else:
            return False
        return True
    except Exception:
        return False


def probe(path: str) -> dict:
    """音频技术信息：格式标签/时长/比特率/采样率。"""
    ext = os.path.splitext(path)[1].lstrip(".").upper()
    try:
        audio = mutagen.File(path)
        if audio is None:
            return {"fmt": ext, "duration": 0, "bitrate": 0, "sr": 0}
        return {"fmt": ext,
                "duration": int(audio.info.length or 0),
                "bitrate": int(getattr(audio.info, "bitrate", 0) or 0),
                "sr": int(getattr(audio.info, "sample_rate", 0) or 0)}
    except Exception:
        return {"fmt": ext, "duration": 0, "bitrate": 0, "sr": 0}


def fmt_duration(sec: int) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
