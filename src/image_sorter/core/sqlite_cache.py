"""
SQLite cache store for metadata and hash acceleration.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Iterable, Optional, Set

from image_sorter._log import logger


class SQLiteCacheStore:
    """Lightweight sqlite-backed cache."""

    def __init__(self, db_file: Path):
        self.db_file = Path(db_file)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None（autocommit）：本类所有写都是单语句，closing() 关闭
        # 连接不会提交事务——源项目因此导致 MD5 缓存/已分类标记从未真正落盘
        conn = sqlite3.connect(str(self.db_file), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _ensure_schema(self):
        try:
            with closing(self._connect()) as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS media_group_time_cache (
                        path TEXT PRIMARY KEY,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        ctime_ns INTEGER NOT NULL,
                        group_ts REAL,
                        updated_at REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_media_group_time_cache_group_ts
                    ON media_group_time_cache(group_ts);

                    CREATE TABLE IF NOT EXISTS media_md5_cache (
                        path TEXT PRIMARY KEY,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        md5 TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_media_md5_cache_md5
                    ON media_md5_cache(md5);

                    CREATE TABLE IF NOT EXISTS classified_md5 (
                        md5 TEXT PRIMARY KEY,
                        added_at REAL NOT NULL
                    );
                    """
                )
        except Exception as e:
            logger.log_exception(e, f"初始化SQLite缓存失败: {self.db_file}")

    def get_grouping_timestamp(
        self,
        file_path: Path,
        size: int,
        mtime_ns: int,
        ctime_ns: int,
    ) -> Optional[float]:
        normalized = self._normalize_path_str(file_path)
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    """
                    SELECT group_ts
                    FROM media_group_time_cache
                    WHERE path = ? AND size = ? AND mtime_ns = ? AND ctime_ns = ?
                    """,
                    (normalized, int(size), int(mtime_ns), int(ctime_ns)),
                ).fetchone()
            if row is None:
                return None
            value = row[0]
            if value is None:
                return None
            return float(value)
        except Exception as e:
            logger.log_exception(e, f"读取SQLite分组时间失败: {file_path}")
            return None

    def upsert_grouping_timestamp(
        self,
        file_path: Path,
        size: int,
        mtime_ns: int,
        ctime_ns: int,
        group_ts: Optional[float],
    ):
        normalized = self._normalize_path_str(file_path)
        now = time.time()
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO media_group_time_cache(path, size, mtime_ns, ctime_ns, group_ts, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size = excluded.size,
                        mtime_ns = excluded.mtime_ns,
                        ctime_ns = excluded.ctime_ns,
                        group_ts = excluded.group_ts,
                        updated_at = excluded.updated_at
                    """,
                    (
                        normalized,
                        int(size),
                        int(mtime_ns),
                        int(ctime_ns),
                        float(group_ts) if group_ts is not None else None,
                        now,
                    ),
                )
        except Exception as e:
            logger.log_exception(e, f"写入SQLite分组时间失败: {file_path}")

    def get_cached_md5(self, file_path: Path, size: int, mtime_ns: int) -> str:
        normalized = self._normalize_path_str(file_path)
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    """
                    SELECT md5
                    FROM media_md5_cache
                    WHERE path = ? AND size = ? AND mtime_ns = ?
                    """,
                    (normalized, int(size), int(mtime_ns)),
                ).fetchone()
            if row is None:
                return ""
            value = row[0]
            return str(value) if isinstance(value, str) else ""
        except Exception as e:
            logger.log_exception(e, f"读取SQLite MD5缓存失败: {file_path}")
            return ""

    def upsert_md5(self, file_path: Path, size: int, mtime_ns: int, md5_value: str):
        if not md5_value:
            return
        normalized = self._normalize_path_str(file_path)
        now = time.time()
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO media_md5_cache(path, size, mtime_ns, md5, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size = excluded.size,
                        mtime_ns = excluded.mtime_ns,
                        md5 = excluded.md5,
                        updated_at = excluded.updated_at
                    """,
                    (normalized, int(size), int(mtime_ns), str(md5_value), now),
                )
        except Exception as e:
            logger.log_exception(e, f"写入SQLite MD5缓存失败: {file_path}")

    def mark_classified_md5(self, md5_hash: str):
        if not md5_hash:
            return
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO classified_md5(md5, added_at)
                    VALUES(?, ?)
                    ON CONFLICT(md5) DO NOTHING
                    """,
                    (str(md5_hash), time.time()),
                )
        except Exception as e:
            logger.log_exception(e, "写入SQLite已分类MD5失败")

    def mark_classified_md5_batch(self, md5_hashes: Iterable[str]):
        rows = [(str(h), time.time()) for h in md5_hashes if isinstance(h, str) and h]
        if not rows:
            return
        try:
            with closing(self._connect()) as conn:
                conn.executemany(
                    """
                    INSERT INTO classified_md5(md5, added_at)
                    VALUES(?, ?)
                    ON CONFLICT(md5) DO NOTHING
                    """,
                    rows,
                )
        except Exception as e:
            logger.log_exception(e, "批量写入SQLite已分类MD5失败")

    def load_classified_md5_set(self) -> Set[str]:
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute("SELECT md5 FROM classified_md5").fetchall()
            return {str(r[0]) for r in rows if r and isinstance(r[0], str) and r[0]}
        except Exception as e:
            logger.log_exception(e, "读取SQLite已分类MD5失败")
            return set()

    def clear_md5_cache(self):
        try:
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM media_md5_cache")
        except Exception as e:
            logger.log_exception(e, "清理SQLite MD5缓存失败")

    def clear_classified_md5(self):
        try:
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM classified_md5")
        except Exception as e:
            logger.log_exception(e, "清理SQLite已分类MD5失败")

    @staticmethod
    def _normalize_path_str(path: Path) -> str:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path.absolute()
        return os.path.normcase(os.path.normpath(str(resolved)))
