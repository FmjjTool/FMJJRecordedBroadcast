"""音乐标签历史：保存/恢复最近 5 次标签修改（对齐 musictag 的 TagHistory）。

只记录基础字段（title/artist/album/year/track/disc）发生变化且新值非空的快照，
每个文件最多保留 5 条；数据库放在工具箱 config 目录。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

MAX_RECORDS_PER_FILE = 5

_HISTORY_FIELDS = ("title", "artist", "album", "year", "track", "disc")
_SNAPSHOT_FIELDS = (
    "title", "artist", "album", "albumartist", "year", "track", "track_count",
    "disc", "disc_count", "genre", "composer", "lyricist", "comment", "lyrics",
)


def _db_path() -> str:
    try:
        from toolbox.config_manager import get_config_dir
        root = get_config_dir()
    except Exception:
        root = os.path.join(os.path.expanduser("~"), ".fengming_toolbox")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "music_tag_history.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tag_history ("
        " serial INTEGER PRIMARY KEY AUTOINCREMENT,"
        " filepath TEXT NOT NULL,"
        " createtime REAL NOT NULL,"
        " title TEXT, artist TEXT, album TEXT,"
        " albumartist TEXT, year TEXT,"
        " track TEXT, track_count TEXT,"
        " disc TEXT, disc_count TEXT,"
        " genre TEXT, composer TEXT, lyricist TEXT,"
        " comment TEXT, lyrics TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_history_path ON tag_history(filepath)")
    return conn


def snapshot(tags: dict) -> dict:
    return {k: str(tags.get(k, "") or "") for k in _SNAPSHOT_FIELDS}


def _changed(before: dict, after: dict) -> bool:
    for field in _HISTORY_FIELDS:
        if str(before.get(field, "") or "") != str(after.get(field, "") or ""):
            return True
    return any(str(after.get(f) or "").strip() for f in _HISTORY_FIELDS)


def record(filepath: str, before: dict, after: dict):
    """保存一条历史快照（无基础字段变化时跳过）。"""
    before = snapshot(before or {})
    after = snapshot(after or {})
    if not filepath or not _changed(before, after):
        return
    try:
        conn = _connect()
        try:
            cols = ("filepath", "createtime") + _SNAPSHOT_FIELDS
            values = (filepath, time.time()) + tuple(after[f] for f in _SNAPSHOT_FIELDS)
            conn.execute(
                "INSERT INTO tag_history (" + ",".join(cols) + ") VALUES ("
                + ",".join("?" * len(cols)) + ")", values)
            rows = conn.execute(
                "SELECT serial FROM tag_history WHERE filepath=? ORDER BY serial DESC",
                (filepath,)).fetchall()
            for (serial,) in rows[MAX_RECORDS_PER_FILE:]:
                conn.execute("DELETE FROM tag_history WHERE serial=?", (serial,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def history(filepath: str) -> list[dict]:
    """按时间倒序返回历史快照。"""
    out = []
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT createtime," + ",".join(_SNAPSHOT_FIELDS)
                + " FROM tag_history WHERE filepath=? ORDER BY serial DESC",
                (filepath,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return out
    for row in rows:
        item = {"createtime": row[0]}
        for i, field in enumerate(_SNAPSHOT_FIELDS, 1):
            item[field] = row[i] or ""
        out.append(item)
    return out


def clear(filepath: str = ""):
    try:
        conn = _connect()
        try:
            if filepath:
                conn.execute("DELETE FROM tag_history WHERE filepath=?", (filepath,))
            else:
                conn.execute("DELETE FROM tag_history")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
