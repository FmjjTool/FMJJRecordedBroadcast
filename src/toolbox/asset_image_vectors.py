# -*- coding: utf-8 -*-
"""资产图片特征缓存（config/asset_image_vectors.db）。

路径：config/asset_image_vectors.db（已 gitignore，不随安装包分发）。
owner：仅本模块写入；readers 为 L3 asset_image_search。
primary/derived：派生缓存，可由本地图片 + 图识别模型随时重建，无需备份。
去重键：规范化绝对路径（os.path.normcase + abspath）。
保留/清理：不自动删除；文件或模型变化按文件戳/模型标识失效，可手动 clear()。
schema 版本：1；版本不符时丢弃派生缓存并重建。
会被后台线程调用，每次短连接（WAL + busy timeout），禁止共享连接。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1

_lock = threading.Lock()
_db_path: Path | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS vectors (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    model TEXT,
    dim INTEGER,
    vector BLOB,
    updated REAL);
"""


def db_path() -> Path:
    global _db_path
    if _db_path is not None:
        return _db_path
    from toolbox.config_manager import get_config_dir
    _db_path = get_config_dir() / "asset_image_vectors.db"
    return _db_path


def set_db_path(path) -> None:
    """测试或迁移时指定缓存库位置；None 恢复默认。"""
    global _db_path
    _db_path = Path(path) if path else None


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
    elif str(row[0]) != str(SCHEMA_VERSION):
        # 派生缓存可安全丢弃重建，不迁移旧特征
        conn.execute("DELETE FROM vectors")
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                     (str(SCHEMA_VERSION),))
        conn.commit()
    return conn


def fetch_many(items, model: str) -> dict[str, np.ndarray]:
    """按 [(路径, mtime, size)] 取有效特征，返回 {规范化路径: 向量}。"""
    rows = [(_key(path), float(mtime), int(size)) for path, mtime, size in items]
    out: dict[str, np.ndarray] = {}
    if not rows:
        return out
    try:
        with _lock:
            conn = _connect()
            try:
                for start in range(0, len(rows), 400):
                    chunk = rows[start:start + 400]
                    stored_map = {row[0]: row for row in chunk}
                    marks = ",".join("?" * len(chunk))
                    sql = (f"SELECT path, mtime, size, model, dim, vector FROM vectors "
                           f"WHERE path IN ({marks})")
                    for key, mtime, size, saved_model, dim, blob in conn.execute(
                            sql, [row[0] for row in chunk]):
                        if saved_model != model or not blob:
                            continue
                        stored = stored_map.get(key)
                        if stored is None:
                            continue
                        if abs(float(mtime) - stored[1]) > 1e-6 or int(size) != stored[2]:
                            continue
                        array = np.frombuffer(blob, dtype=np.float32)
                        if dim and array.size == int(dim):
                            out[key] = array
                return out
            finally:
                conn.close()
    except Exception:  # noqa: BLE001 - 缓存不可用按未命中处理
        return {}


def upsert_many(rows) -> None:
    """写入特征：[(路径, mtime, size, model, dim, float32 向量)]。"""
    if not rows:
        return
    now = time.time()
    data = []
    for path, mtime, size, model, dim, vector in rows:
        array = np.asarray(vector, dtype=np.float32)
        data.append((_key(path), float(mtime), int(size), str(model),
                     int(dim), array.tobytes(), now))
    try:
        with _lock:
            conn = _connect()
            try:
                conn.executemany(
                    "INSERT INTO vectors(path, mtime, size, model, dim, vector, updated) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                    "mtime=excluded.mtime, size=excluded.size, model=excluded.model, "
                    "dim=excluded.dim, vector=excluded.vector, updated=excluded.updated",
                    [(row[0], row[1], row[2], row[3], row[4], row[5], row[6])
                     for row in data])
                conn.commit()
            finally:
                conn.close()
    except Exception:  # noqa: BLE001 - 缓存写入失败不影响本次识别
        return


def count(model: str = "") -> int:
    """当前缓存行数（可选按模型过滤）。"""
    try:
        with _lock:
            conn = _connect()
            try:
                if model:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM vectors WHERE model=?", (model,)).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
    except Exception:  # noqa: BLE001
        return 0


def clear() -> bool:
    """清空派生缓存（模型更换或排查问题时使用）。"""
    try:
        with _lock:
            conn = _connect()
            try:
                conn.execute("DELETE FROM vectors")
                conn.commit()
            finally:
                conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False