"""Shared filename rendering for desktop and Web music-tag rename previews.

Pure rules only: no filesystem writes, configuration, or UI dependencies.
"""

from __future__ import annotations

import re

DEFAULT_PATTERN = "@2 - @1"
PRESETS = ("@2 - @1", "@1 - @2", "@5. @1", "@5. @2 - @1",
           "@4@5. @1", "@4@5. @2 - @1", "@1")
TOKENS = (("@1", "标题"), ("@2", "艺术家"), ("@3", "专辑"),
          ("@4", "碟号"), ("@5", "音轨"), ("@6", "年份"),
          ("@7", "注释"), ("@8", "专辑艺术家"))


def render_rename_pattern(pattern: str, tags: dict) -> str:
    """Render the existing desktop template without changing its behavior."""
    track = str(tags.get("track") or "")
    disc = str(tags.get("disc") or "")
    mapping = {
        "@1": str(tags.get("title") or ""),
        "@2": str(tags.get("artist") or ""),
        "@3": str(tags.get("album") or ""),
        "@4": disc,
        "@5": track,
        "@6": str(tags.get("year") or ""),
        "@7": str(tags.get("comment") or ""),
        "@8": str(tags.get("albumartist") or ""),
    }
    out = pattern
    for key in sorted(mapping, reverse=True):
        out = out.replace(key, mapping[key])
    out = re.sub(r"[\\/]", ";", out)
    out = re.sub(r'[\s":*?<>|]', " ", out).strip()
    return out
