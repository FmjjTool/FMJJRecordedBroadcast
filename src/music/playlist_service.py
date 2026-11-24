"""歌曲合集编排（L3）：来源读取、跨平台匹配、预览计划与写入执行。

写入默认只生成计划，不触碰平台；调用方确认后才创建歌单并分批加歌。
真实凭据只通过统一管理接口只读获取，不写回、不记录。
"""

from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from .models import SongInfo
from .playlist import (
    WRITERS,
    AuthRequiredError,
    BasePlaylistWriter,
    ParseChangedError,
    PlatformRejectedError,
    PlaylistWriteError,
    RateLimitedError,
    UnsupportedPlatformError,
)
from .scrapers import (
    FiveSingScraper,
    KugouScraper,
    KuwoScraper,
    NeteaseScraper,
    QQMusicScraper,
)

SOURCE_SCRAPERS = {
    "qq": QQMusicScraper,
    "netease": NeteaseScraper,
    "kugou": KugouScraper,
    "kuwo": KuwoScraper,
    "5sing": FiveSingScraper,
}

PLATFORM_NAMES = {
    "qq": "QQ音乐",
    "netease": "网易云音乐",
    "kugou": "酷狗音乐",
    "kuwo": "酷我音乐",
    "5sing": "5sing",
}

_LINK_KEYS = {
    "qq": ("qq", "QQ音乐", "QQ音乐链接"),
    "netease": ("netease", "网易云音乐", "网易云音乐链接"),
    "kugou": ("kugou", "酷狗音乐", "酷狗音乐链接"),
    "kuwo": ("kuwo", "酷我音乐", "酷我音乐链接"),
    "5sing": ("5sing", "5sing链接"),
}

_VERSION_RULES = (
    ("和声伴奏", ("和声伴奏",)),
    ("伴奏", ("伴奏", "instrumental", "off vocal", "offvocal")),
    ("live", ("live", "现场")),
    ("remix", ("remix", "混音")),
    ("dj", ("dj",)),
    ("cover", ("cover", "翻唱")),
    ("片段", ("片段",)),
    ("demo", ("demo",)),
)


@dataclass
class SourceSong:
    title: str
    artist: str = ""
    album: str = ""
    song_id: str = ""
    platform: str = ""
    raw: Optional[SongInfo] = None
    links: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "song_id": self.song_id,
            "platform": self.platform,
        }
        links = {key: value for key, value in self.links.items() if value}
        if links:
            data["links"] = links
        return data


@dataclass
class MatchItem:
    source: SourceSong
    status: str = ""  # matched / uncertain / missing / duplicate
    target: Optional[SongInfo] = None
    score: float = 0.0
    candidates: list[tuple[SongInfo, float]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "status": self.status,
            "score": round(self.score, 4),
            "target": _song_brief(self.target),
            "candidates": [
                {"song": _song_brief(song), "score": round(score, 4)}
                for song, score in self.candidates[:3]
            ],
            "note": self.note,
        }


@dataclass
class PlatformPlan:
    platform: str
    playlist_name: str
    items: list[MatchItem]
    writer: BasePlaylistWriter
    source_label: str = ""

    @property
    def platform_name(self) -> str:
        return self.writer.platform_name or PLATFORM_NAMES.get(self.platform, self.platform)

    def counts(self) -> dict[str, int]:
        counts = {"matched": 0, "uncertain": 0, "missing": 0, "duplicate": 0}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def selected(self, include_uncertain: bool = False) -> list[MatchItem]:
        allowed = {"matched"}
        if include_uncertain:
            allowed.add("uncertain")
        return [item for item in self.items if item.status in allowed and item.target]

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "platform_name": self.platform_name,
            "playlist_name": self.playlist_name,
            "source": self.source_label,
            "experimental": self.writer.experimental,
            "counts": self.counts(),
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class ApplyResult:
    platform: str
    playlist_name: str
    playlist_id: str = ""
    added: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    stopped: str = ""

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "playlist_name": self.playlist_name,
            "playlist_id": self.playlist_id,
            "added": self.added,
            "failed": self.failed,
            "skipped": self.skipped,
            "stopped": self.stopped,
        }


def get_scraper(platform: str):
    platform = (platform or "").strip().lower()
    cls = SOURCE_SCRAPERS.get(platform)
    if not cls:
        raise ValueError(f"不支持的来源平台：{platform}")
    return cls()


def get_writer(platform: str) -> BasePlaylistWriter:
    platform = (platform or "").strip().lower()
    writer = WRITERS.get(platform)
    if not writer:
        raise ValueError(f"不支持的目标平台：{platform}")
    return writer


def get_login_jar(platform: str) -> dict[str, str]:
    """只读获取目标平台当前登录态；未登录时抛 AuthRequiredError。"""
    from .playlist.base import cookie_from_auth

    return cookie_from_auth(platform)


def capabilities() -> list[dict]:
    rows = []
    for platform, writer in WRITERS.items():
        rows.append({
            "platform": platform,
            "name": writer.platform_name,
            "supports_create": writer.supports_create,
            "experimental": writer.experimental,
            "verified": writer.verified,
            "batch_size": writer.batch_size,
        })
    return rows


def supported_targets() -> list[str]:
    return [platform for platform, writer in WRITERS.items() if writer.supports_create]


def detect_platform(text: str) -> str:
    value = (text or "").strip().lower()
    if "5sing.kugou.com" in value or "5sing.com" in value:
        return "5sing"
    if "y.qq.com" in value or "qq.com" in value:
        return "qq"
    if "music.163.com" in value:
        return "netease"
    if "kugou.com" in value:
        return "kugou"
    if "kuwo.cn" in value:
        return "kuwo"
    return ""


def read_source_playlist(platform: str, url_or_id: str) -> tuple[list[SourceSong], str]:
    scraper = get_scraper(platform)
    value = (url_or_id or "").strip()
    playlist_id = ""
    try:
        playlist_id = scraper.extract_playlist_id(value)
    except NotImplementedError:
        playlist_id = ""
    if not playlist_id:
        playlist_id = value
    playlist = scraper.get_playlist(playlist_id)
    if not playlist:
        raise ValueError(f"未能读取{PLATFORM_NAMES.get(platform, platform)}歌单：{url_or_id}")
    songs = [
        SourceSong(
            title=song.song_name or song.title,
            artist=song.singer,
            album=song.album_name,
            song_id=song.song_id,
            platform=platform,
            raw=song,
        )
        for song in playlist.songs
        if (song.song_name or song.title)
    ]
    return songs, playlist.name or playlist_id


def read_source_file(path: str | Path) -> list[SourceSong]:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"歌曲清单不存在：{file_path}")
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        return _sources_from_json(file_path)
    if suffix in (".xlsx", ".xlsm"):
        rows = _rows_from_xlsx(file_path)
    elif suffix == ".csv":
        with file_path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = [row for row in csv.reader(stream)]
    else:
        rows = _rows_from_text(file_path)
    return _rows_to_sources(rows)


def build_plan(platform: str, songs: list[SourceSong], playlist_name: str, *,
               scraper=None, min_score: float = 0.72, uncertain_score: float = 0.55,
               search_limit: int = 5, search_interval: float = 0.35,
               progress: Optional[Callable[[int, int, SourceSong, MatchItem], None]] = None,
               cancel=None) -> PlatformPlan:
    writer = get_writer(platform)
    if not writer.supports_create:
        raise UnsupportedPlatformError(
            f"{writer.platform_name}没有公开的歌单创建接口，暂不支持",
            platform=platform, phase="plan",
        )
    scraper = scraper or get_scraper(platform)
    items: list[MatchItem] = []
    used_ids: dict[str, int] = {}
    total = len(songs)
    for index, source in enumerate(songs, 1):
        if cancel is not None and cancel.is_set():
            break
        item = _match_one(
            platform, source, scraper,
            min_score=min_score, uncertain_score=uncertain_score,
            search_limit=search_limit,
        )
        if item.target and item.status in ("matched", "uncertain"):
            previous = used_ids.get(item.target.song_id)
            if previous:
                item.status = "duplicate"
                item.note = f"与第 {previous} 首匹配到同一首歌，已跳过"
            else:
                used_ids[item.target.song_id] = index
        items.append(item)
        if progress:
            progress(index, total, source, item)
        if search_interval and index < total:
            time.sleep(search_interval)
    return PlatformPlan(
        platform=platform,
        playlist_name=playlist_name,
        items=items,
        writer=writer,
        source_label="",
    )


def apply_plan(plan: PlatformPlan, cookie: dict[str, str], *,
               transport=None, include_uncertain: bool = False,
               interval: float = 0.8,
               on_progress: Optional[Callable[[int, int, str], None]] = None,
               journal_path: str | Path | None = None,
               resume: dict | None = None) -> ApplyResult:
    selected = plan.selected(include_uncertain)
    result = ApplyResult(platform=plan.platform, playlist_name=plan.playlist_name)
    if not selected:
        return result

    already_added = set((resume or {}).get("added") or [])
    playlist_id = str((resume or {}).get("playlist_id") or "")
    remaining = [
        item for item in selected
        if not (item.target and item.target.song_id in already_added)
    ]
    skipped = [
        {"song": item.target.song_name if item.target else item.source.title,
         "reason": "断点记录中已添加"}
        for item in selected
        if item.target and item.target.song_id in already_added
    ]
    result.skipped.extend(skipped)
    result.added.extend(item.target.song_id for item in selected
                        if item.target and item.target.song_id in already_added)

    if not playlist_id:
        playlist_id = plan.writer.create_playlist(plan.playlist_name, cookie, transport=transport)
    result.playlist_id = playlist_id

    total = len(remaining)
    done = 0
    batch_size = max(1, int(plan.writer.batch_size or 50))
    for start in range(0, total, batch_size):
        batch = remaining[start:start + batch_size]
        batch_songs = [item.target for item in batch if item.target]
        try:
            added = plan.writer.add_songs(
                playlist_id, batch_songs, cookie, transport=transport,
            )
            result.added.extend(added.added)
            for name, reason in added.failed:
                result.failed.append({"song": name, "reason": reason})
        except (AuthRequiredError, RateLimitedError, UnsupportedPlatformError,
                ParseChangedError, PlatformRejectedError) as exc:
            result.stopped = str(exc)
            for item in remaining[start:]:
                result.skipped.append({
                    "song": item.target.song_name if item.target else item.source.title,
                    "reason": result.stopped,
                })
            _write_journal(journal_path, result)
            break
        except PlaylistWriteError as exc:
            result.failed.append({"song": f"第 {start + 1}-{start + len(batch)} 首", "reason": str(exc)})
        done += len(batch)
        if on_progress:
            on_progress(done, total, f"已添加 {len(result.added)} 首")
        _write_journal(journal_path, result)
        if interval and done < total:
            time.sleep(interval)
    return result


def load_journal(path: str | Path) -> dict:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def match_score(source: SourceSong, candidate: SongInfo) -> float:
    norm_source = _normalize_title(source.title)
    norm_candidate = _normalize_title(candidate.song_name or candidate.title)
    if not norm_source or not norm_candidate:
        return 0.0
    if norm_source == norm_candidate:
        title_score = 1.0
    elif norm_source in norm_candidate or norm_candidate in norm_source:
        title_score = 0.9
    else:
        title_score = SequenceMatcher(None, norm_source, norm_candidate).ratio()

    if source.artist:
        norm_artist = _normalize_artist(source.artist)
        cand_artist = _normalize_artist(candidate.singer)
        if not norm_artist or not cand_artist:
            artist_score = 0.5
        elif norm_artist == cand_artist:
            artist_score = 1.0
        elif norm_artist in cand_artist or cand_artist in norm_artist:
            artist_score = 0.85
        else:
            artist_score = SequenceMatcher(None, norm_artist, cand_artist).ratio() * 0.7
        # 歌手权重高于歌名：避免同名翻唱被当成原曲加进合集
        score = title_score * 0.65 + artist_score * 0.35
    else:
        score = title_score

    if _version_tokens(source.title) != _version_tokens(candidate.song_name or candidate.title):
        # 原曲与伴奏/DJ/Live 等版本不能互相替代，压到默认阈值以下
        score = min(score - 0.2, 0.68)
    return max(0.0, min(1.0, score))


def _match_one(platform: str, source: SourceSong, scraper, *,
               min_score: float, uncertain_score: float, search_limit: int) -> MatchItem:
    if source.platform == platform and source.song_id and source.raw:
        return MatchItem(source=source, status="matched", target=source.raw, score=1.0,
                         note="同平台直接使用原歌曲 ID")
    link = (source.links or {}).get(platform, "")
    if link:
        extract = getattr(scraper, "extract_song_id", None)
        try:
            linked_id = extract(link) if extract else ""
        except NotImplementedError:
            linked_id = ""
        if linked_id:
            return MatchItem(
                source=source,
                status="matched",
                target=SongInfo(
                    song_id=linked_id,
                    song_name=source.title,
                    title=source.title,
                    singer=source.artist,
                    album_name=source.album,
                    platform=platform,
                    platform_name=getattr(scraper, "platform_name", "") or PLATFORM_NAMES.get(platform, platform),
                    url=link,
                ),
                score=1.0,
                note="使用完整歌曲表中的平台链接",
            )
    query = f"{source.title} {source.artist}".strip()
    candidates = scraper.search_song(query, limit=search_limit) or []
    # 歌手名可能让搜索漏掉原曲（平台只收录翻唱/别名），高分不足时再用纯歌名补搜
    if source.artist:
        best_so_far = max((match_score(source, song) for song in candidates), default=0.0)
        if best_so_far < min_score:
            seen_ids = {song.song_id for song in candidates}
            for song in scraper.search_song(source.title, limit=search_limit) or []:
                if song.song_id not in seen_ids:
                    seen_ids.add(song.song_id)
                    candidates.append(song)
    scored = sorted(
        ((song, match_score(source, song)) for song in candidates),
        key=lambda pair: pair[1],
        reverse=True,
    )
    best_song, best_score = scored[0] if scored else (None, 0.0)
    item = MatchItem(source=source, target=best_song, score=best_score,
                     candidates=scored[:3])
    if best_song and best_score >= min_score:
        item.status = "matched"
    elif best_song and best_score >= uncertain_score:
        item.status = "uncertain"
        item.note = "相似度不足，需人工确认"
    else:
        item.status = "missing"
        item.target = None
        item.note = "未找到足够相似的歌曲"
    return item


def _write_journal(path, result: ApplyResult) -> None:
    if not path:
        return
    try:
        from toolbox.durable_io import atomic_write_json
        atomic_write_json(path, {
            **result.to_dict(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception:
        pass


def _song_brief(song: Optional[SongInfo]) -> dict:
    if not song:
        return {}
    return {
        "song_id": song.song_id,
        "song_name": song.song_name or song.title,
        "singer": song.singer,
        "album_name": song.album_name,
        "duration": song.duration,
        "url": song.url,
    }


def _normalize_title(text: str) -> str:
    value = (text or "").lower().strip()
    value = re.sub(r"[\(\)\[\]【】（）]", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value, flags=re.UNICODE)
    return value


def _normalize_artist(text: str) -> str:
    value = (text or "").lower()
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value, flags=re.UNICODE)
    return value


def _version_tokens(text: str) -> set[str]:
    lowered = (text or "").lower()
    tokens: set[str] = set()
    for label, needles in _VERSION_RULES:
        if any(needle in lowered for needle in needles):
            tokens.add(label)
    return tokens


def _sources_from_json(path: Path) -> list[SourceSong]:
    with path.open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if isinstance(data, dict):
        data = data.get("songs") or data.get("items") or data.get("list") or []
    if not isinstance(data, list):
        raise ValueError("JSON 歌曲清单应为数组，或包含 songs/items 数组")
    sources: list[SourceSong] = []
    seen: dict[tuple[str, str], SourceSong] = {}
    for item in data:
        links: dict[str, str] = {}
        if isinstance(item, str):
            title = item.strip()
            artist = album = ""
        elif isinstance(item, dict):
            title = _first_value(item, "title", "song_name", "name", "歌名", "歌曲", "歌曲名称")
            artist = _first_value(item, "artist", "singer", "歌手", "演唱者")
            album = _first_value(item, "album", "album_name", "专辑", "所属专辑")
            raw_links = item.get("links")
            if isinstance(raw_links, dict):
                links.update({str(key).lower(): str(value).strip()
                              for key, value in raw_links.items() if value})
            for platform, keys in _LINK_KEYS.items():
                for key in keys:
                    value = item.get(key)
                    if value not in (None, ""):
                        links[platform] = str(value).strip()
                        break
        else:
            continue
        if not title:
            continue
        links = {key: value for key, value in links.items() if value}
        dedup_key = (title.lower(), artist.lower())
        if dedup_key in seen:
            seen[dedup_key].links.update(links)
            continue
        source = SourceSong(title=title, artist=artist, album=album, links=links)
        seen[dedup_key] = source
        sources.append(source)
    return sources


def _rows_from_xlsx(path: Path) -> list[list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("读取 Excel 需要 openpyxl") from exc
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        return [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]
    finally:
        workbook.close()


def _rows_from_text(path: Path) -> list[list[str]]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line in stream:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            parts = re.split(r"\t|\s+\|\s+|\s+-\s+|\s+—\s+", value, maxsplit=2)
            rows.append([part.strip() for part in parts])
    return rows


def _rows_to_sources(rows: list[list[str]]) -> list[SourceSong]:
    if not rows:
        return []
    title_index, artist_index, album_index = 0, 1, 2
    first = [str(cell or "").strip().lower() for cell in rows[0]]
    header_hit = any(cell in ("歌名", "歌曲", "标题", "title", "song", "song_name")
                     for cell in first)
    if header_hit:
        title_index = _column_index(first, ("歌名", "歌曲", "标题", "title", "song", "song_name"))
        artist_index = _column_index(first, ("歌手", "艺人", "artist", "singer"))
        album_index = _column_index(first, ("专辑", "album", "album_name"))
        rows = rows[1:]
    sources: list[SourceSong] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        title = _cell(row, title_index)
        if not title:
            continue
        artist = _cell(row, artist_index) if artist_index >= 0 else ""
        album = _cell(row, album_index) if album_index >= 0 else ""
        key = (title.strip().lower(), artist.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        sources.append(SourceSong(title=title, artist=artist, album=album))
    return sources


def _column_index(row: list[str], names: tuple[str, ...]) -> int:
    for index, cell in enumerate(row):
        if cell in names:
            return index
    return -1


def _cell(row: list[str], index: int) -> str:
    if 0 <= index < len(row):
        return str(row[index] or "").strip()
    return ""


def _first_value(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""
