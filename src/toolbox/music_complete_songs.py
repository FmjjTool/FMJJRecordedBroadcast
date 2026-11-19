"""完整歌曲只读索引与平台详情匹配。

数据来自腾讯文档本地快照 tables.db 的「完整歌曲」表（ss_ktwk0t），
只读不写回。音乐信息页选中歌曲后，优先用表内平台链接取详情，
链接缺失或无法解析时回退到歌名搜索。
"""

from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from music import (
    FiveSingScraper, KugouScraper, KuwoScraper, NeteaseScraper, QQMusicScraper,
)
from music.models import SongInfo

DOC_KEY = "DRnd3bkhkQUR1aEJr"
TAB_ID = "ss_ktwk0t"

LINK_FIELDS = {
    "kugou": "酷狗音乐",
    "qq": "QQ音乐",
    "netease": "网易云音乐",
    "kuwo": "酷我音乐",
    "5sing": "5sing",
}

# 与音乐信息页五列顺序一致：key, 显示名, 采集器, 完整歌曲链接列
PLATFORM_SPECS = (
    ("kugou", "酷狗音乐", KugouScraper, "酷狗音乐"),
    ("qq", "QQ音乐", QQMusicScraper, "QQ音乐"),
    ("netease", "网易云音乐", NeteaseScraper, "网易云音乐"),
    ("kuwo", "酷我音乐", KuwoScraper, "酷我音乐"),
    ("5sing", "5sing音乐", FiveSingScraper, "5sing"),
)


def _tables_db_path() -> Path:
    from toolbox.tcdoc import config as tcdoc_config
    return tcdoc_config.tables_db_path()


def _text(value) -> str:
    if isinstance(value, list):
        return "、".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    return str(value or "").strip()


def load_complete_songs(path=None) -> list[dict]:
    """读取本地完整歌曲快照，返回按表格顺序的只读条目。"""
    db = Path(path) if path else _tables_db_path()
    if not db.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT row_no, data_json FROM rows WHERE doc_key=? AND tab_id=?"
                " ORDER BY row_no", (DOC_KEY, TAB_ID)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []

    songs = []
    for row_no, payload in rows:
        try:
            values = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        title = _text(values.get("歌曲名称"))
        if not title:
            continue
        songs.append({
            "row_no": row_no,
            "title": title,
            "singer": _text(values.get("演唱者")),
            "duration": _text(values.get("歌曲时长")),
            "date": _text(values.get("首发日期")),
            "album": _text(values.get("所属专辑")),
            "intro": _text(values.get("歌曲简介")),
            "language": _text(values.get("语言")),
            "lyricist": _text(values.get("作词人")),
            "composer": _text(values.get("作曲人")),
            "company": _text(values.get("出品方")),
            "original_artist": _text(values.get("原唱艺人")),
            "accompaniment": _text(values.get("可用伴奏形式")),
            "track_no": _text(values.get("音轨号")),
            "links": {key: _text(values.get(field)) for key, field in LINK_FIELDS.items()},
        })
    return songs


def _search_keyword(song: dict) -> str:
    return " ".join(part for part in (song.get("singer"), song.get("title")) if part).strip()


_TITLE_CLEAN_RE = re.compile(r"[\s（）()【】\[\]《》〈〉·•\-_/&、,，.。!！?？:：'\"“”‘’]+")
_LANGUAGE_ORDER = ("汉语（CN）", "日语（JP）", "韩语（KP）", "英语（EN）")


def _norm_title(text) -> str:
    return _TITLE_CLEAN_RE.sub("", str(text or "")).lower()


def _title_matches(left, right) -> bool:
    a, b = _norm_title(left), _norm_title(right)
    return bool(a and b and (a in b or b in a))


def _attach_album(scraper, item) -> None:
    album_id = str(getattr(item, "album_id", "") or "")
    if not album_id or album_id == "0" or (item.extra or {}).get("_album"):
        return
    try:
        album = scraper.get_album(album_id)
    except Exception:
        return
    if album:
        item.extra["_album"] = album.model_dump()


def _add_accompaniment_probe(key: str, scraper, songs: list, title: str, limit: int) -> list:
    """酷我链接只返回原曲时，补搜一次伴奏，避免「可用伴奏形式」永远为空。"""
    if key != "kuwo" or not title:
        return songs
    if any("伴奏" in f"{item.song_name or ''} {item.album_name or ''}" for item in songs):
        return songs
    try:
        extras = scraper.search(f"{title} 伴奏", limit=limit)
    except Exception:
        return songs
    seen = {item.song_id for item in songs if item.song_id}
    for item in extras:
        text = f"{item.song_name or ''} {item.album_name or ''}"
        if item.song_id in seen or not _title_matches(text, title):
            continue
        songs.append(item)
        seen.add(item.song_id)
    return songs


def fetch_platform_songs(song: dict, platform_specs: Iterable = PLATFORM_SPECS,
                         limit: int = 8) -> dict[str, list[SongInfo]]:
    """按完整歌曲条目取五平台结果：链接优先，失败回退歌名搜索。"""
    links = song.get("links") or {}
    specs = tuple(platform_specs)

    def one(spec):
        key, _label, scraper_cls, link_field = spec
        scraper = scraper_cls()
        url = str(links.get(key) or links.get(link_field) or "").strip()
        song_id = ""
        if url:
            try:
                resolver = getattr(scraper, "resolve_song_id", None)
                song_id = resolver(url) if resolver else scraper.extract_song_id(url)
            except (NotImplementedError, ValueError, TypeError):
                song_id = ""
        if song_id:
            try:
                detail = scraper.get_song_detail(song_id)
                if detail:
                    detail.song_name = detail.song_name or song.get("title", "")
                    detail.singer = detail.singer or song.get("singer", "")
                    detail.platform = getattr(scraper, "platform", "")
                    detail.platform_name = getattr(scraper, "platform_name", "")
                    _attach_album(scraper, detail)
                    return key, _add_accompaniment_probe(
                        key, scraper, [detail], song.get("title", ""), limit)
            except Exception:
                pass
        try:
            songs = scraper.search(_search_keyword(song), limit=limit)
        except Exception:
            return key, []
        target = next((item for item in songs
                       if _title_matches(item.song_name, song.get("title"))), None)
        if target is not None:
            try:
                scraper.fetch_full_detail(target)
            except Exception:
                pass
        songs = _add_accompaniment_probe(key, scraper, songs, song.get("title", ""), limit)
        return key, songs

    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        return dict(pool.map(one, specs))


def _date_text(value) -> str:
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", str(value or ""))
    if not match:
        return ""
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _album_value(results: dict, field: str) -> str:
    for key in ("qq", "netease", "kugou", "kuwo", "5sing"):
        for item in (results.get(key) or [])[:1]:
            album = (item.extra or {}).get("_album")
            if isinstance(album, dict):
                value = str(album.get(field) or "").strip()
                if value:
                    return value
    return ""


def _first_song_value(results: dict, field: str) -> str:
    for key in ("qq", "netease", "kugou", "kuwo", "5sing"):
        for item in (results.get(key) or [])[:1]:
            value = getattr(item, field, "")
            if value:
                return str(value)
    return ""


def _release_dates(results: dict) -> list[str]:
    dates = []
    for key in ("qq", "netease", "kugou", "kuwo", "5sing"):
        for item in (results.get(key) or [])[:1]:
            album = (item.extra or {}).get("_album")
            values = []
            if isinstance(album, dict):
                values.append(album.get("release_date"))
            values.append(getattr(item, "release_date", ""))
            for value in values:
                text = _date_text(value)
                if text and text not in dates:
                    dates.append(text)
    return dates


def _normalize_language(*values) -> str:
    found = []
    for value in values:
        for part in re.split(r"[、,，/;；&|]+", str(value or "")):
            text = part.strip()
            if not text:
                continue
            if any(key in text for key in ("国语", "华语", "汉语", "中文", "粤语")):
                mapped = "汉语（CN）"
            elif any(key in text for key in ("日语", "日文")):
                mapped = "日语（JP）"
            elif any(key in text for key in ("韩语", "韩文")):
                mapped = "韩语（KP）"
            elif any(key in text for key in ("英语", "英文")):
                mapped = "英语（EN）"
            else:
                continue
            if mapped not in found:
                found.append(mapped)
    found.sort(key=lambda item: _LANGUAGE_ORDER.index(item)
               if item in _LANGUAGE_ORDER else len(_LANGUAGE_ORDER))
    return "、".join(found)


_CREDIT_RE = re.compile(r"^(作词|作曲|出品|原唱)[^：:]{0,24}[：:]\s*(.+)$")


def _credit_from_lyrics(lyrics: str, key: str) -> str:
    for line in (lyrics or "").splitlines()[:40]:
        clean = re.sub(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d+)?\]\s*", "", line).strip()
        match = _CREDIT_RE.match(clean)
        if match and match.group(1) == key:
            return match.group(2).strip()
    return ""


def _singer_names(singer: str) -> list[str]:
    return [part.strip() for part in re.split(r"[、,/，&;；]+|\s+feat\.?\s+",
                                              str(singer or ""), flags=re.I)
            if part.strip()]


def _chorus_form(singer: str) -> str:
    count = len(_singer_names(singer))
    if count <= 1:
        return "单人演唱"
    return "双人合唱" if count == 2 else "多人合唱"


def _accompaniment_form(results: dict, title: str) -> str:
    """伴奏形式判断（口径与腾讯表格「可用伴奏形式」一致）。

    - 歌名 / 专辑名带「和声」字样 → 和声伴奏
    - 带「伴奏」/ instrumental / off vocal，或 5sing 的伴奏模式 → 纯伴奏（即普通伴奏）
    - 都没有 → 无伴奏
    """
    has_plain = False
    for items in results.values():
        for item in items:
            text = f"{item.song_name or ''} {item.album_name or ''}"
            if not _title_matches(text, title):
                continue
            lowered = text.lower()
            if "和声" in text:
                return "和声伴奏"
            mode = str(getattr(item, "mode", "") or "")
            if (mode == "伴奏"
                    or "伴奏" in text
                    or "instrumental" in lowered
                    or "off vocal" in lowered):
                has_plain = True
    return "纯伴奏" if has_plain else "无伴奏"


def _original_artist(results: dict, fallback_singer: str) -> str:
    for key in ("netease", "qq", "kugou", "kuwo", "5sing"):
        for item in (results.get(key) or [])[:1]:
            extra = item.extra or {}
            origin = extra.get("originSongSimpleData")
            if isinstance(origin, dict):
                names = [str(artist.get("name") or "").strip()
                         for artist in origin.get("artists") or []
                         if isinstance(artist, dict)]
                names = [name for name in names if name]
                if names:
                    return "、".join(names)
            cover_type = str(extra.get("originCoverType", ""))
            if cover_type == "0":
                return item.singer or fallback_singer
            credited = _credit_from_lyrics(item.lyrics, "原唱")
            if credited:
                return credited
            if getattr(item, "mode", "") == "原唱":
                return item.singer or fallback_singer
    return ""


def build_song_profile(song: dict, platform_results: dict) -> dict:
    """汇总平台详情、专辑详情与歌词头，生成完整歌曲填充预览。"""
    all_items = [item for items in platform_results.values() for item in items]
    lyrics = max((item.lyrics or "" for item in all_items), key=len, default="")
    singer = song.get("singer") or _first_song_value(platform_results, "singer")
    dates = _release_dates(platform_results)
    chosen_date = _date_text(song.get("date")) or (dates[0] if dates else "")
    language = _normalize_language(
        _album_value(platform_results, "language"),
        _first_song_value(platform_results, "language"),
        song.get("language", ""))
    intro = song.get("intro") or _album_value(platform_results, "description")
    company = song.get("company") or _album_value(platform_results, "company")
    album = song.get("album") or _album_value(platform_results, "album_name") \
        or _first_song_value(platform_results, "album_name")
    original = song.get("original_artist") or _original_artist(platform_results, singer)
    accompaniment = _accompaniment_form(platform_results, song.get("title", ""))
    conflicts = {}
    if len(dates) > 1:
        conflicts["首发日期"] = dates
    if song.get("accompaniment") and song["accompaniment"] != accompaniment:
        conflicts["可用伴奏形式"] = [song["accompaniment"], accompaniment]
    return {
        "歌曲名称": song.get("title") or _first_song_value(platform_results, "song_name"),
        "演唱者": singer,
        "歌曲简介": intro,
        "完整歌词": lyrics,
        "歌曲时长": song.get("duration") or _format_seconds(
            all_items[0].duration if all_items else 0),
        "作词人": song.get("lyricist") or _credit_from_lyrics(lyrics, "作词"),
        "作曲人": song.get("composer") or _credit_from_lyrics(lyrics, "作曲"),
        "出品方": company,
        "首发日期": chosen_date,
        "语言": language,
        "原唱艺人": original,
        "合唱形式": _chorus_form(singer),
        "可用伴奏形式": accompaniment,
        "所属专辑": album,
        "音轨号": str(song.get("track_no")
                       or _first_song_value(platform_results, "track_no") or ""),
        "封面图": _first_song_value(platform_results, "cover_url"),
        "_首发日期候选": dates,
        "_conflicts": conflicts,
    }


def _format_seconds(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    return f"{total // 60}:{total % 60:02d}"
