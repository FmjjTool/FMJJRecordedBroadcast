# -*- coding: utf-8 -*-
"""collected.db 主数据存储与派生投影入口。

collected.db 是「已获取」页面展示/导出快照，唯一写入方本模块；
increment.db 是它的派生分平台归档，由本模块的 `ingest_items(..., incremental=True)`
调用 `toolbox.collected_projection` 写入。页面不再直接写 SQLite。

旧 collected.json 首次启动自动迁移入库，原文件改名 .bak 保留备份。
schema：videos 既有结构保持不变；主键由 toolbox.dedup.collect_key 提供。
readers：collected_library 用例提供轻量目录/分页；显式导出与同步读取完整字段。
保留策略：不自动清理主数据，仅在用户明确清空/删除时由本模块执行事务。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, is_dataclass
from pathlib import Path

from toolbox.dedup import collect_key

_lock = threading.Lock()

_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS videos ("
    " dedup_key TEXT PRIMARY KEY,"
    " platform  TEXT,"
    " created_at REAL,"
    " data      TEXT NOT NULL)")


def db_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "collected.db"


def _resolve_db_path(path=None) -> Path:
    return Path(path) if path is not None else db_path()


def _connect(path=None):
    p = _resolve_db_path(path)
    os.makedirs(p.parent, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_CREATE_TABLE)
    except BaseException:
        conn.close()
        raise
    return conn


def _row_of(info, now):
    return (collect_key(info), (info.platform or ""), now,
            json.dumps(asdict(info), ensure_ascii=False))


def insert_items(items, db_path=None) -> int:
    """增量写入 collected.db，只插新行（主键冲突忽略），返回新增行数。"""
    rows = []
    now = time.time()
    for item in items or []:
        if item is None:
            continue
        try:
            rows.append(_row_of(item, now))
        except Exception:
            continue
    if not rows:
        return 0
    try:
        with _lock:
            conn = _connect(db_path)
            try:
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO videos"
                    " (dedup_key, platform, created_at, data) VALUES (?, ?, ?, ?)",
                    rows)
                conn.commit()
                return conn.total_changes - before
            finally:
                conn.close()
    except Exception:
        return 0


def save_all(items, db_path=None) -> bool:
    """全量同步列表到 collected.db（清空/重建场景用），保留原有 created_at。"""
    try:
        rows_items = [i for i in items or [] if is_dataclass(i)]
        with _lock:
            conn = _connect(db_path)
            try:
                old = dict(conn.execute(
                    "SELECT dedup_key, created_at FROM videos").fetchall())
                now = time.time()
                rows = []
                for item in rows_items:
                    key = collect_key(item)
                    rows.append((key, (item.platform or ""), old.get(key) or now,
                                 json.dumps(asdict(item), ensure_ascii=False)))
                conn.execute("DELETE FROM videos")
                conn.executemany(
                    "INSERT OR REPLACE INTO videos"
                    " (dedup_key, platform, created_at, data) VALUES (?, ?, ?, ?)",
                    rows)
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


def _migrate_from_json(conn, path: Path):
    """旧 collected.json → db（仅在 db 为空时执行一次），成功后 json 改名 .bak。"""
    from platforms.base import VideoInfo
    jp = path.parent / "collected.json"
    if not jp.exists():
        return
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    fields = set(VideoInfo.__dataclass_fields__)
    for d in data:
        if not isinstance(d, dict):
            continue
        info = VideoInfo(**{k: v for k, v in d.items() if k in fields})
        if not collect_key(info):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO videos"
            " (dedup_key, platform, created_at, data) VALUES (?, ?, ?, ?)",
            _row_of(info, now))
    conn.commit()
    try:
        jp.rename(jp.with_suffix(".json.bak"))
    except OSError:
        pass


def load_items(db_path=None) -> list:
    """读取全部已获取条目（按入库顺序），首次运行自动迁移旧 collected.json。"""
    from platforms.base import VideoInfo
    fields = set(VideoInfo.__dataclass_fields__)
    try:
        p = _resolve_db_path(db_path)
        with ExitStack() as resources:
            with _lock:
                conn = _connect(p)
                resources.callback(conn.close)
                if conn.execute("SELECT 1 FROM videos LIMIT 1").fetchone() is None:
                    _migrate_from_json(conn, p)
                rows = conn.execute("SELECT data FROM videos ORDER BY rowid")
                resources.callback(rows.close)
            # WAL 读快照允许采集继续写入。逐行解码避免同时保留全库 JSON 与对象列表。
            items = []
            for (data,) in rows:
                try:
                    d = json.loads(data)
                    if isinstance(d, dict):
                        items.append(VideoInfo(**{k: v for k, v in d.items() if k in fields}))
                except Exception:
                    continue
            return items
    except Exception:
        return []


def iter_metadata(db_path=None):
    """只读取目录字段，避免启动时解码/保留所有正文与 raw_data；不重写展示去重。"""
    p = _resolve_db_path(db_path)
    with ExitStack() as resources:
        with _lock:
            conn = _connect(p)
            resources.callback(conn.close)
            if conn.execute("SELECT 1 FROM videos LIMIT 1").fetchone() is None:
                _migrate_from_json(conn, p)
            cursor = conn.execute(
                "SELECT dedup_key, platform, json_extract(data, '$.publish_time'), "
                "json_extract(data, '$.video_id'), json_extract(data, '$.video_url'), "
                "json_extract(data, '$.raw_url') FROM videos "
                "WHERE CASE WHEN json_valid(data) THEN json_type(data)='object' ELSE 0 END "
                "ORDER BY rowid")
            resources.callback(cursor.close)
        for storage_key, platform, published, video_id, video_url, raw_url in cursor:
            key = collect_key({"video_id": video_id, "video_url": video_url, "raw_url": raw_url})
            yield storage_key, key, platform or "", published or ""


def read_by_keys(keys, *, include_raw=True, db_path=None):
    """按一页稳定存储键读取；展示页不解码原始响应，显式导出仍返回完整数据。"""
    from platforms.base import VideoInfo
    keys = list(keys)
    if not keys:
        return {}
    fields = set(VideoInfo.__dataclass_fields__)
    out = {}
    with ExitStack() as resources:
        conn = _connect(db_path)
        resources.callback(conn.close)
        payload = "data" if include_raw else "json_remove(data, '$.raw_data')"
        for start in range(0, len(keys), 400):
            chunk = keys[start:start + 400]
            cursor = conn.execute(
                f"SELECT dedup_key, {payload} FROM videos WHERE dedup_key IN "
                f"({','.join('?' for _ in chunk)}) "
                "AND CASE WHEN json_valid(data) THEN json_type(data)='object' ELSE 0 END", chunk)
            try:
                for key, text in cursor:
                    try:
                        data = json.loads(text)
                        out[key] = VideoInfo(**{k: v for k, v in data.items() if k in fields})
                    except (ValueError, TypeError):
                        continue
            finally:
                cursor.close()
    return out


def existing_keys(keys, db_path=None):
    keys = list(keys)
    found = set()
    with ExitStack() as resources:
        conn = _connect(db_path)
        resources.callback(conn.close)
        for start in range(0, len(keys), 400):
            chunk = keys[start:start + 400]
            rows = conn.execute(f"SELECT dedup_key FROM videos WHERE dedup_key IN "
                                f"({','.join('?' for _ in chunk)})", chunk)
            try:
                found.update(row[0] for row in rows)
            finally:
                rows.close()
    return found


def delete_keys(keys, db_path=None):
    """只删除明确选中的主数据；不以旧内存快照覆盖并发入库的新记录。"""
    with _lock:
        conn = _connect(db_path)
        try:
            conn.executemany("DELETE FROM videos WHERE dedup_key=?", ((key,) for key in keys))
            conn.commit()
        finally:
            conn.close()


def clear_items(db_path=None):
    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute("DELETE FROM videos")
            conn.commit()
        finally:
            conn.close()


def ingest_items(items, *, incremental: bool = False, db_path=None, projector=None) -> dict:
    """运行期唯一入库入口：先写 collected 主数据，增量模式再投影 increment。

    返回 `{平台: 新增条数}`；非增量模式返回空字典。projector 仅供隔离测试注入。
    """
    items = list(items or [])
    if not items:
        return {}
    insert_items(items, db_path=db_path)
    if not incremental:
        return {}
    if projector is None:
        from toolbox.collected_projection import project_items as projector
    return projector(items)
