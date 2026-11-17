"""音乐标签 XLSX 匹配规则（纯函数，不依赖 Qt）。"""

from __future__ import annotations

import os
import re

XLSX_FIELD_LABELS = [
    ("filename", "文件名"), ("title", "标题"), ("artist", "艺术家"),
    ("album", "专辑"), ("albumartist", "专辑艺术家"), ("year", "年份"),
    ("track", "音轨号"), ("track_count", "音轨总数"), ("disc", "碟号"),
    ("disc_count", "碟总数"), ("genre", "风格"), ("composer", "作曲家"),
    ("lyricist", "作词家"), ("comment", "注释"), ("lyrics", "歌词"),
]

_XLSX_ALIASES = {
    "filename": ("文件名", "文件", "filename", "file"),
    "title": ("标题", "歌名", "歌曲", "名称", "title", "song", "name"),
    "artist": ("艺术家", "歌手", "演唱者", "演唱", "artist", "singer"),
    "album": ("专辑", "album"),
    "albumartist": ("专辑艺术家", "专辑歌手", "albumartist", "album artist",
                    "album_artist"),
    "year": ("年份", "年代", "发行年份", "发行日期", "日期", "year", "date",
             "release"),
    "track": ("音轨", "音轨号", "曲目号", "轨道", "track", "trackno", "track_no"),
    "track_count": ("音轨总数", "曲目总数", "总曲目", "tracktotal", "track total"),
    "disc": ("碟号", "碟", "唱片", "disc", "discno"),
    "disc_count": ("碟总数", "总碟数", "disctotal", "disc total"),
    "genre": ("风格", "流派", "genre"),
    "composer": ("作曲", "作曲家", "composer"),
    "lyricist": ("作词", "作词家", "作词者", "lyricist", "writer"),
    "comment": ("注释", "备注", "说明", "comment", "note"),
    "lyrics": ("歌词", "lyric", "lyrics", "lrc"),
}

XLSX_MATCH_MIN = 45


def auto_map_field(header: str) -> str:
    h = str(header or "").strip().lower()
    if not h:
        return ""
    for field, aliases in _XLSX_ALIASES.items():
        if h in aliases:
            return field
    for field, aliases in _XLSX_ALIASES.items():
        if any(a in h or h in a for a in aliases if len(a) >= 2):
            return field
    return ""


def cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def norm_key(s: str) -> str:
    return re.sub(r"[\s\-_·・~～]+", "", str(s or "")).lower()


def pair_score(a: str, b: str) -> int:
    if not a or not b:
        return 0
    if a == b:
        return 50
    if a in b or b in a:
        return 35
    return 0


def xlsx_match_score(page_row: dict, vals: dict) -> float:
    path = page_row["path"]
    tags = page_row["tags"]
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]

    fx = norm_key(vals.get("filename", ""))
    if fx:
        base = norm_key(os.path.basename(path))
        st = norm_key(stem)
        if fx == base or fx == st:
            return 100
        if len(fx) >= 4 and (fx in st or st in fx):
            return 80
        return 0

    xt = norm_key(vals.get("title", ""))
    if not xt:
        return 0

    title_cands = []
    if tags.get("title"):
        title_cands.append(norm_key(tags["title"]))
    if len(parts) >= 2:
        title_cands.append(norm_key(parts[1]))
    elif stem:
        title_cands.append(norm_key(stem))
    title_score = max((pair_score(c, xt) for c in title_cands), default=0)

    artist_cands = []
    if tags.get("artist"):
        artist_cands.append(norm_key(tags["artist"]))
    if len(parts) >= 2:
        artist_cands.append(norm_key(parts[0]))

    xa = norm_key(vals.get("artist", ""))
    if not xa or not artist_cands:
        artist_score = 5
    else:
        best = max((pair_score(c, xa) for c in artist_cands), default=0)
        artist_score = 30 if best == 50 else (20 if best == 35 else -20)

    album_score = 0
    xal = norm_key(vals.get("album", ""))
    if xal and tags.get("album"):
        a = norm_key(tags["album"])
        album_score = 10 if a == xal else (5 if (a in xal or xal in a) else 0)

    return title_score + artist_score + album_score
