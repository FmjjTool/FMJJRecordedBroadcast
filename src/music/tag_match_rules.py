"""Shared, Qt-free rules for automatic music tag matching.

These functions only build queries and update drafts. Sidecar entries describe
potential writes; each caller owns path checks, overwrite decisions and IO.
"""

from __future__ import annotations

import os

from music import tags_io

TEXT_MATCH_FIELDS = [
    ("title", "标题"), ("artist", "艺术家"), ("album", "专辑"), ("year", "年份"),
    ("track", "音轨"), ("disc", "碟号"), ("genre", "风格"), ("comment", "注释"),
]
NUMBER_FIELDS = {"track", "disc"}


def build_query(row: dict, settings: dict):
    """Keep the desktop title fallback and optional artist/album conditions."""
    tags = row.get("tags") or {}
    stem = os.path.splitext(os.path.basename(row["path"]))[0]
    if settings.get("use_filename_only") or not (tags.get("title") or "").strip():
        title = stem
        used_filename = True
    else:
        title = (tags.get("title") or "").strip()
        used_filename = False
    artist = (tags.get("artist") or "").strip() if settings.get("use_artist") else ""
    album = (tags.get("album") or "").strip() if settings.get("use_album") else ""
    return title, artist, album, used_filename


def build_update_plan(row: dict, match, settings: dict) -> dict:
    """Merge one match without changing the input or reading/writing files."""
    cand = match.candidate
    tags = dict(row.get("tags") or {})
    overwrite = settings.get("overwrite") or {}
    modes = settings.get("modes") or {}
    for key, _label in TEXT_MATCH_FIELDS:
        if modes.get(key, 1) != 0:
            continue
        value = getattr(cand, key, "")
        if key in NUMBER_FIELDS:
            value = str(value) if value else ""
        value = str(value or "").strip()
        if not value:
            continue
        if overwrite.get(key) or not str(tags.get(key) or "").strip():
            tags[key] = value
            if key == "track" and cand.track:
                tags["track"] = str(cand.track)
            if key == "disc" and cand.disc:
                tags["disc"] = str(cand.disc)

    pictures = list(row.get("pictures") or [])
    pictures_dirty = False
    cover_mode = modes.get("cover", 3)
    if cover_mode in (0, 2) and match.cover:
        if overwrite.get("cover") or not any(p.type == 3 for p in pictures):
            pictures = [p for p in pictures if p.type != 3]
            pictures.insert(0, tags_io.Pic(match.cover, match.cover_mime or "image/jpeg", 3, ""))
            pictures_dirty = True

    lyrics_mode = modes.get("lyrics", 3)
    if lyrics_mode in (0, 2) and cand.lyric:
        if overwrite.get("lyrics") or not str(tags.get("lyrics") or "").strip():
            tags["lyrics"] = cand.lyric

    sidecars = []
    if cover_mode in (1, 2) and match.cover:
        sidecars.append({
            "suffix": ".png" if "png" in (match.cover_mime or "") else ".jpg",
            "data": match.cover, "overwrite": bool(overwrite.get("cover")),
        })
    if lyrics_mode in (1, 2) and cand.lyric:
        sidecars.append({
            "suffix": ".lrc", "data": cand.lyric.encode("utf-8"),
            "overwrite": bool(overwrite.get("lyrics")),
        })

    return {
        "tags": tags,
        "pictures": pictures,
        "pictures_dirty": pictures_dirty,
        "_matched": f"{cand.source_name} · {cand.title} · {cand.artist}",
        "sidecars": sidecars,
    }
