"""腾讯文档监控 · SQLite 存档。

两张表，均按 doc_key 分区，同库可容纳任意数量文档：
- daily     : 每次采集追加一行（同一天多次采集保留多行），增量相对上一次采集
- snapshots : 每次采集的成功/失败明细（失败原因留档，便于回溯）

增量计算口径：与该文档「上一次成功采集」比较；首次采集或某一侧缺失时为 NULL。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DAILY_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_key        TEXT NOT NULL,
    ts             TEXT NOT NULL,
    date           TEXT NOT NULL,
    views          INTEGER,
    members        INTEGER,
    readers        INTEGER,
    views_delta    INTEGER,
    members_delta  INTEGER
)
"""

SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_key    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    views      INTEGER,
    members    INTEGER,
    readers    INTEGER,
    ok         INTEGER NOT NULL,
    message    TEXT
)
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _connect(db_path: Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db(db_path: Path) -> None:
    """建表；早期库缺列（readers）时自动补列，老库数据不丢。"""
    with _connect(db_path) as conn:
        conn.execute(DAILY_SCHEMA)
        conn.execute(SNAPSHOT_SCHEMA)
        if "readers" not in _columns(conn, "daily"):
            conn.execute("ALTER TABLE daily ADD COLUMN readers INTEGER")
        if "readers" not in _columns(conn, "snapshots"):
            conn.execute("ALTER TABLE snapshots ADD COLUMN readers INTEGER")


# ==================== 写入 ====================

def add_snapshot(db_path: Path, doc_key: str, ts: str, views, members,
                 readers, ok: bool, message: str = "") -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO snapshots (doc_key, ts, views, members, readers, ok, message)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_key, ts, views, members, readers, 1 if ok else 0, message),
        )


def previous_entry(db_path: Path, doc_key: str) -> sqlite3.Row | None:
    with _connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM daily WHERE doc_key = ? ORDER BY ts DESC, id DESC LIMIT 1", (doc_key,)
        ).fetchone()


def append_daily(db_path: Path, doc_key: str, ts: str, day: str,
                 views, members, readers) -> None:
    """每次采集追加一行，增量相对该文档上一次采集计算。"""
    prev = previous_entry(db_path, doc_key)
    views_delta = (views - prev["views"]
                   if (prev and views is not None and prev["views"] is not None) else None)
    members_delta = (members - prev["members"]
                     if (prev and members is not None and prev["members"] is not None) else None)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO daily (doc_key, ts, date, views, members, readers,"
            " views_delta, members_delta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_key, ts, day, views, members, readers, views_delta, members_delta),
        )


def refresh_deltas(db_path: Path, doc_key: str) -> None:
    """按采集顺序补算增量（历史数据被修正 / 导入后保持一致）。"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, views, members FROM daily WHERE doc_key = ?"
            " ORDER BY ts, id", (doc_key,)
        ).fetchall()
        prev_views = prev_members = None
        for row in rows:
            views, members = row["views"], row["members"]
            views_delta = (views - prev_views
                           if (views is not None and prev_views is not None) else None)
            members_delta = (members - prev_members
                             if (members is not None and prev_members is not None) else None)
            conn.execute(
                "UPDATE daily SET views_delta = ?, members_delta = ? WHERE id = ?",
                (views_delta, members_delta, row["id"]),
            )
            if views is not None:
                prev_views = views
            if members is not None:
                prev_members = members


# ==================== 读取 ====================

def all_daily(db_path: Path, doc_key: str) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM daily WHERE doc_key = ? ORDER BY ts, id", (doc_key,)
        ).fetchall()


def all_snapshots(db_path: Path, doc_key: str, limit: int = 5000) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE doc_key = ? ORDER BY id DESC LIMIT ?",
            (doc_key, limit),
        ).fetchall()
    return rows[::-1]


def latest(db_path: Path, doc_key: str) -> sqlite3.Row | None:
    with _connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM daily WHERE doc_key = ? ORDER BY ts DESC, id DESC LIMIT 1", (doc_key,)
        ).fetchone()


def doc_stats(db_path: Path, doc_key: str) -> tuple[int, str]:
    """(记录条数, 最近采集时间)；无记录返回 (0, "")。"""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(ts) AS last FROM daily WHERE doc_key = ?", (doc_key,)
        ).fetchone()
    return (int(row["n"] or 0), str(row["last"] or "")) if row else (0, "")


def clear_doc(db_path: Path, doc_key: str) -> int:
    """删除某文档的全部历史（文档清单里移除文档时可选）。返回删除行数。"""
    with _connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM daily WHERE doc_key = ?", (doc_key,)).fetchone()[0]
        conn.execute("DELETE FROM daily WHERE doc_key = ?", (doc_key,))
        conn.execute("DELETE FROM snapshots WHERE doc_key = ?", (doc_key,))
    return int(n or 0)


# ==================== 旧版数据导入 ====================

def import_legacy(legacy_db_path: Path, target_db: Path) -> tuple[int, int]:
    """把旧版独立工具（腾讯文档数据保存）的历史记录并入本库。

    按 (doc_key, ts) 幂等合并：目标库已有的同一文档同一采集时间不再插入，
    所以重复调用安全，也能与「已经采集过几次」的新库并存。
    返回 (新插入的每日记录数, 新插入的采集明细数)。
    """
    legacy = Path(legacy_db_path)
    if not legacy.is_file():
        return (0, 0)
    daily_n = snap_n = 0
    try:
        with sqlite3.connect(f"file:{legacy}?mode=ro", uri=True) as src:
            src.row_factory = sqlite3.Row
            tables = {row["name"] for row in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            daily_rows = src.execute(
                "SELECT * FROM daily ORDER BY ts, id").fetchall() if "daily" in tables else []
            snap_rows = src.execute(
                "SELECT * FROM snapshots ORDER BY ts, id").fetchall() if "snapshots" in tables else []
    except sqlite3.Error:
        return (0, 0)

    init_db(target_db)
    with _connect(target_db) as conn:
        exist_daily = {(row["doc_key"], row["ts"]) for row in conn.execute(
            "SELECT doc_key, ts FROM daily")}
        exist_snap = {(row["doc_key"], row["ts"]) for row in conn.execute(
            "SELECT doc_key, ts FROM snapshots")}
        for row in daily_rows:
            keys = row.keys()
            mark = (row["doc_key"], row["ts"])
            if mark in exist_daily:
                continue
            conn.execute(
                "INSERT INTO daily (doc_key, ts, date, views, members, readers,"
                " views_delta, members_delta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["doc_key"], row["ts"], row["date"],
                 row["views"], row["members"],
                 row["readers"] if "readers" in keys else None,
                 row["views_delta"] if "views_delta" in keys else None,
                 row["members_delta"] if "members_delta" in keys else None),
            )
            exist_daily.add(mark)
            daily_n += 1
        for row in snap_rows:
            keys = row.keys()
            if "doc_key" not in keys:
                continue
            mark = (row["doc_key"], row["ts"])
            if mark in exist_snap:
                continue
            conn.execute(
                "INSERT INTO snapshots (doc_key, ts, views, members, readers, ok, message)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["doc_key"], row["ts"], row["views"], row["members"],
                 row["readers"] if "readers" in keys else None,
                 row["ok"], row["message"] if "message" in keys else ""),
            )
            exist_snap.add(mark)
            snap_n += 1
    return (daily_n, snap_n)
