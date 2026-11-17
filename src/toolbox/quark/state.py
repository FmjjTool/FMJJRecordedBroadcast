# -*- coding: utf-8 -*-
"""夸克网盘备份 · 本地状态库（增量判定的唯一依据）。

库文件：`config/quark_backup.db`
- `uploaded`：每条本地文件一行，记录 size / mtime / md5 / 云端 fid，用来判断「要不要传」
- `runs`    ：每次备份一行，供界面展示最近记录

判定口径（增量追加）：
- 没有记录 → 传
- size 或 mtime 变了 → 传
- 其余 → 跳过（云端多余文件一律不删）
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploaded (
    local_path  TEXT PRIMARY KEY,
    mapping_id  TEXT NOT NULL DEFAULT '',
    remote_dir  TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL NOT NULL DEFAULT 0,
    md5         TEXT NOT NULL DEFAULT '',
    fid         TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_uploaded_mapping ON uploaded(mapping_id);

CREATE TABLE IF NOT EXISTS resume (
    token       TEXT PRIMARY KEY,
    provider    TEXT NOT NULL DEFAULT '',
    local_path  TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id  TEXT NOT NULL DEFAULT '',
    local_dir   TEXT NOT NULL DEFAULT '',
    remote_dir  TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    scanned     INTEGER NOT NULL DEFAULT 0,
    uploaded    INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    bytes       INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 1,
    message     TEXT NOT NULL DEFAULT ''
);
"""


def db_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return Path(get_config_dir()) / "quark_backup.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """打开状态库（首次自动建表）。"""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 已同步文件 ====================

def get_record(mapping_id: str, local_path: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT local_path, size, mtime, md5, fid FROM uploaded"
            " WHERE mapping_id=? AND local_path=?",
            (mapping_id, local_path),
        ).fetchone()
    if not row:
        return None
    return {"local_path": row[0], "size": row[1], "mtime": row[2],
            "md5": row[3], "fid": row[4]}


def put_record(mapping_id: str, local_path: str, remote_dir: str, size: int,
               mtime: float, md5: str = "", fid: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO uploaded (local_path, mapping_id, remote_dir, size, mtime,"
            " md5, fid, uploaded_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(local_path) DO UPDATE SET mapping_id=excluded.mapping_id,"
            " remote_dir=excluded.remote_dir, size=excluded.size, mtime=excluded.mtime,"
            " md5=excluded.md5, fid=excluded.fid, uploaded_at=excluded.uploaded_at",
            (local_path, mapping_id, remote_dir, int(size), float(mtime),
             md5, fid, _now()),
        )


def drop_record(local_path: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM uploaded WHERE local_path=?", (local_path,))


def forget_mapping(mapping_id: str) -> int:
    """清掉某条映射的同步记录（下次备份会重新传一遍）。"""
    with connect() as conn:
        cur = conn.execute("DELETE FROM uploaded WHERE mapping_id=?", (mapping_id,))
        return cur.rowcount or 0


def mapping_stats(mapping_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM uploaded WHERE mapping_id=?",
            (mapping_id,),
        ).fetchone()
    return {"files": row[0] or 0, "bytes": row[1] or 0}


# ==================== 运行记录 ====================

def add_run(result) -> None:
    """写入一条运行记录（传入 backup.BackupResult）。"""
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (mapping_id, local_dir, remote_dir, started_at, finished_at,"
            " scanned, uploaded, skipped, failed, bytes, ok, message)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (result.mapping_id, result.local_dir, result.remote_dir,
             result.started_at, result.finished_at, result.scanned,
             result.uploaded, result.skipped, result.failed, result.bytes,
             1 if result.ok else 0, result.message or "; ".join(result.errors[:3])),
        )


def recent_runs(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT mapping_id, local_dir, remote_dir, started_at, finished_at,"
            " scanned, uploaded, skipped, failed, bytes, ok, message"
            " FROM runs ORDER BY id DESC LIMIT ?", (int(limit),),
        ).fetchall()
    keys = ("mapping_id", "local_dir", "remote_dir", "started_at", "finished_at",
            "scanned", "uploaded", "skipped", "failed", "bytes", "ok", "message")
    return [dict(zip(keys, row)) for row in rows]


# ==================== 断点续传 ====================

class ResumeHandle:
    """一条文件的续传句柄：按 (后端, 路径, 大小, mtime, 远端目录) 定位。

    各后端自己决定 payload 结构（任务 id、分片进度、已完成的 etag/序号），
    引擎只负责创建句柄、成功后 clear()。
    """

    def __init__(self, provider: str, local_path: str, size: int, mtime: float,
                 remote_dir: str):
        raw = f"{provider}|{local_path}|{int(size)}|{float(mtime):.6f}|{remote_dir}"
        self.token = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        self.provider = provider
        self.local_path = str(local_path)
        self.size = int(size)
        self.mtime = float(mtime)

    def load(self) -> dict:
        with connect() as conn:
            row = conn.execute(
                "SELECT payload FROM resume WHERE token=?", (self.token,)).fetchone()
        if not row:
            return {}
        try:
            data = json.loads(row[0] or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, payload: dict) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO resume (token, provider, local_path, size, mtime, payload,"
                " updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(token) DO UPDATE SET payload=excluded.payload,"
                " updated_at=excluded.updated_at",
                (self.token, self.provider, self.local_path, self.size, self.mtime,
                 json.dumps(payload, ensure_ascii=False), _now()),
            )

    def clear(self) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM resume WHERE token=?", (self.token,))


def purge_resume(days: int = 7) -> int:
    """清掉过期的续传记录（默认 7 天），返回删除条数。"""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM resume WHERE updated_at < datetime('now', 'localtime', ?)",
            (f"-{int(days)} days",))
        return cur.rowcount or 0
