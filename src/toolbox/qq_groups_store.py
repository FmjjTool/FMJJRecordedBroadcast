"""QQ群人数历史（SQLite）：取代 快照_*.json / 最新.json。

为什么不再用 JSON 文件：
- 每天（或按间隔）刷新一次就多一个 1 KB 文件，一个群一年几百个文件，翻起来很痛苦；
- 人数只是「群号 + 时间 + 人数」这种规整数据，本来就是表结构；
- 写库能一次查询出某个群的全部历史，也方便以后画曲线。

一个群一次刷新 = 一行；同一时刻多次刷新按时间排序，最新一行即「当前人数」，
前一行用来算变化。库文件固定一个：download/粉丝群/QQ群/qq_groups.db。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_NAME = "qq_groups.db"

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_no     TEXT    NOT NULL,
    name         TEXT    NOT NULL DEFAULT '',
    member_count INTEGER,
    member_limit INTEGER,
    avatar       TEXT    NOT NULL DEFAULT '',
    avatar_path  TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL DEFAULT '',
    fetched_at   TEXT    NOT NULL,
    recorded_ts  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_group_snapshot_no_ts
    ON group_snapshot (group_no, recorded_ts DESC);
"""


def db_path(create: bool = True) -> Path:
    """库文件固定落在 download/粉丝群/QQ群/qq_groups.db。"""
    from platforms import fan_groups as fg

    root = fg.group_dir("qqgroup", "all", create=create).parent
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root / DB_NAME


def _connect(create: bool = True) -> sqlite3.Connection | None:
    path = db_path(create=create)
    if not create and not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "group_id": str(row["group_no"]),
        "group_no": str(row["group_no"]),
        "name": str(row["name"] or ""),
        "member_count": row["member_count"],
        "member_limit": row["member_limit"],
        "avatar": str(row["avatar"] or ""),
        "avatar_path": str(row["avatar_path"] or ""),
        "source": str(row["source"] or ""),
        "fetched_at": str(row["fetched_at"] or ""),
        "recorded_ts": float(row["recorded_ts"] or 0),
    }


def record(group, source: str = "") -> None:
    """追加一条快照（同一个群同一秒重复写只保留一行）。"""
    now = datetime.now()
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    values = (
        str(getattr(group, "group_no", "") or getattr(group, "group_id", "")),
        str(getattr(group, "name", "") or ""),
        getattr(group, "member_count", None),
        getattr(group, "member_limit", None),
        str(getattr(group, "avatar", "") or ""),
        str(getattr(group, "avatar_path", "") or ""),
        str(source or ""),
        fetched_at,
        now.timestamp(),
    )
    if not values[0]:
        return
    with _LOCK:
        conn = _connect()
        if conn is None:
            return
        try:
            exists = conn.execute(
                "SELECT 1 FROM group_snapshot WHERE group_no = ? AND fetched_at = ?",
                (values[0], fetched_at)).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO group_snapshot (group_no, name, member_count, "
                    "member_limit, avatar, avatar_path, source, fetched_at, recorded_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
                conn.commit()
            else:
                conn.execute(
                    "UPDATE group_snapshot SET name = ?, member_count = ?, "
                    "member_limit = ?, avatar = ?, avatar_path = ?, source = ? "
                    "WHERE group_no = ? AND fetched_at = ?",
                    (values[1], values[2], values[3], values[4], values[5], values[6],
                     values[0], fetched_at))
                conn.commit()
        finally:
            conn.close()


def latest_group(group_no: str) -> dict | None:
    """该群最新一条记录（没有则 None）。"""
    group_no = str(group_no or "").strip()
    if not group_no:
        return None
    with _LOCK:
        conn = _connect(create=False)
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM group_snapshot WHERE group_no = ? "
                "ORDER BY recorded_ts DESC, id DESC LIMIT 1", (group_no,)).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
    return _row_to_dict(row) if row is not None else None


def latest_snapshot(group_no: str) -> dict | None:
    """与旧 最新.json 同结构，供 diff_members / 页面复用。"""
    item = latest_group(group_no)
    if item is None:
        return None
    return {"platform": "qqgroup", "platform_label": "QQ群",
            "owner_id": str(group_no), "fetched_at": item["fetched_at"],
            "note": "", "groups": [item]}


def history(group_no: str, limit: int = 500) -> list[dict]:
    """该群的历史记录，按时间倒序（最新在前）。"""
    group_no = str(group_no or "").strip()
    if not group_no:
        return []
    with _LOCK:
        conn = _connect(create=False)
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM group_snapshot WHERE group_no = ? "
                "ORDER BY recorded_ts DESC, id DESC LIMIT ?",
                (group_no, max(1, int(limit or 500)))).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    return [_row_to_dict(r) for r in rows]


def count(group_no: str = "") -> int:
    with _LOCK:
        conn = _connect(create=False)
        if conn is None:
            return 0
        try:
            if group_no:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM group_snapshot WHERE group_no = ?",
                    (str(group_no),)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM group_snapshot").fetchone()
        except sqlite3.Error:
            return 0
        finally:
            conn.close()
    return int(row["n"] if row is not None else 0)


def import_json_snapshots(root: Path | None = None) -> dict:
    """把老的 快照_*.json / 最新.json 导进库（幂等：按群号+采集时间去重）。

    返回 {"files": 读取的文件数, "rows": 新增行数, "skipped": 跳过的文件数}。
    """
    from platforms import fan_groups as fg

    base = Path(root) if root else fg.group_dir("qqgroup", "all", create=False).parent
    stats = {"files": 0, "rows": 0, "skipped": 0}
    if not base.exists():
        return stats
    for group_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        json_files = sorted(group_dir.glob("快照_*.json"))
        if not json_files:
            continue
        for path in json_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stats["skipped"] += 1
                continue
            groups = payload.get("groups") or []
            if not groups or not isinstance(groups[0], dict):
                stats["skipped"] += 1
                continue
            item = groups[0]
            fetched_at = str(payload.get("fetched_at") or "")
            group_no = str(item.get("group_no") or item.get("group_id") or group_dir.name)
            with _LOCK:
                conn = _connect()
                if conn is None:
                    stats["skipped"] += 1
                    continue
                try:
                    exists = conn.execute(
                        "SELECT 1 FROM group_snapshot WHERE group_no = ? AND fetched_at = ?",
                        (group_no, fetched_at)).fetchone()
                    if exists is None:
                        conn.execute(
                            "INSERT INTO group_snapshot (group_no, name, member_count, "
                            "member_limit, avatar, avatar_path, source, fetched_at, "
                            "recorded_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (group_no, str(item.get("name") or ""),
                             item.get("member_count"), item.get("member_limit"),
                             str(item.get("avatar") or ""),
                             str(item.get("avatar_path") or ""), "import",
                             fetched_at, _ts_of(fetched_at)))
                        conn.commit()
                        stats["rows"] += 1
                    else:
                        stats["skipped"] += 1
                finally:
                    conn.close()
            stats["files"] += 1
    return stats


def _ts_of(text: str) -> float:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return 0.0
