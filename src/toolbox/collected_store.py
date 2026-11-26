# -*- coding: utf-8 -*-
"""collected.db 主数据存储与派生投影入口。

collected.db 是「已获取」页面展示/导出快照，唯一写入方本模块；
increment.db 是它的派生分平台归档，由本模块的 `ingest_items(..., incremental=True)`
调用 `toolbox.collected_projection` 写入。页面不再直接写 SQLite。

旧 collected.json 首次启动自动迁移入库，原文件改名 .bak 保留备份。
schema：videos 既有结构保持不变；主键由 toolbox.dedup.collect_key 提供。
readers：collected_library 用例提供轻量目录/分页；显式导出与同步读取完整字段。
保留策略：不自动清理主数据，仅在用户明确清空/删除时由本模块执行事务。
指数补丁只更新已存在抖音行的 data，不改变既有 schema、主键与 created_at。
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
                "SELECT dedup_key, json_extract(data, '$.platform'), json_extract(data, '$.publish_time'), "
                "json_extract(data, '$.video_id'), json_extract(data, '$.video_url'), "
                "json_extract(data, '$.raw_url') FROM videos "
                "WHERE CASE WHEN json_valid(data) THEN json_type(data)='object' ELSE 0 END "
                "ORDER BY rowid")
            resources.callback(cursor.close)
        for storage_key, platform, published, video_id, video_url, raw_url in cursor:
            key = collect_key({"video_id": video_id, "video_url": video_url, "raw_url": raw_url})
            yield storage_key, key, platform or "", published or ""


def _card_filter(platform: str, keyword: str) -> tuple[str, list]:
    where = ["CASE WHEN json_valid(data) THEN json_type(data)='object' ELSE 0 END"]
    params: list = []
    plat = (platform or "").strip()
    if plat == "other":
        where.append("COALESCE(platform,'') NOT IN ('douyin','bilibili','weibo')")
    elif plat:
        where.append("platform = ?")
        params.append(plat)
    kw = (keyword or "").strip().lower()
    if kw:
        where.append("(LOWER(json_extract(data,'$.title')) LIKE ?"
                     " OR LOWER(json_extract(data,'$.author')) LIKE ?"
                     " OR LOWER(json_extract(data,'$.video_id')) LIKE ?)")
        params += [f"%{kw}%"] * 3
    return " AND ".join(where), params


def query_cards(platform: str = "", keyword: str = "",
                limit: int = 50, offset: int = 0, db_path=None) -> tuple[list, int]:
    """分平台分页读取展示卡片（标题/作者/计数/链接），不返回正文与原始数据。"""
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    where, params = _card_filter(platform, keyword)
    with ExitStack() as resources:
        conn = _connect(_resolve_db_path(db_path))
        resources.callback(conn.close)
        total = conn.execute(
            f"SELECT COUNT(*) FROM videos WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            "SELECT dedup_key, platform,"
            " json_extract(data,'$.title'), json_extract(data,'$.author'),"
            " json_extract(data,'$.publish_time'), json_extract(data,'$.kind'),"
            " json_extract(data,'$.play_count'), json_extract(data,'$.like_count'),"
            " json_extract(data,'$.video_id'), json_extract(data,'$.video_url'),"
            " json_extract(data,'$.raw_url')"
            f" FROM videos WHERE {where}"
            " ORDER BY (json_extract(data,'$.publish_time') IS NULL"
            " OR json_extract(data,'$.publish_time')='') ASC,"
            " json_extract(data,'$.publish_time') DESC, rowid DESC"
            " LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    items = [{
        "key": row[0], "platform": row[1] or "", "title": row[2] or "",
        "author": row[3] or "", "publish_time": row[4] or "",
        "kind": row[5] or "", "play_count": row[6], "like_count": row[7],
        "video_id": row[8] or "", "video_url": row[9] or "",
        "raw_url": row[10] or "",
    } for row in rows]
    return items, int(total)


def query_keys(platform: str = "", keyword: str = "", limit: int = 5000,
               db_path=None) -> list[str]:
    """按当前平台/关键词取稳定键，供 WebUI 批量导出。"""
    where, params = _card_filter(platform, keyword)
    with ExitStack() as resources:
        conn = _connect(_resolve_db_path(db_path))
        resources.callback(conn.close)
        rows = conn.execute(
            f"SELECT dedup_key FROM videos WHERE {where}"
            " ORDER BY (json_extract(data,'$.publish_time') IS NULL"
            " OR json_extract(data,'$.publish_time')='') ASC,"
            " json_extract(data,'$.publish_time') DESC, rowid DESC LIMIT ?",
            params + [max(1, min(20000, int(limit or 5000)))]).fetchall()
    return [row[0] for row in rows]


def newest_publish_times(platform: str, author_sec_uid: str,
                         limit: int = 100, db_path=None) -> list[str]:
    """读取指定平台/作者已入库的最近发布时间文本（只读，供新鲜度对比）。"""
    if not platform or not author_sec_uid:
        return []
    with ExitStack() as resources:
        conn = _connect(db_path)
        resources.callback(conn.close)
        rows = conn.execute(
            "SELECT json_extract(data, '$.publish_time') FROM videos "
            "WHERE platform = ? "
            "AND CASE WHEN json_valid(data) THEN json_type(data)='object' ELSE 0 END "
            "AND json_extract(data, '$.raw_data.author.sec_uid') = ? "
            "ORDER BY json_extract(data, '$.publish_time') DESC LIMIT ?",
            (platform, author_sec_uid, max(1, int(limit))))
        resources.callback(rows.close)
        return [r[0] for r in rows if r[0]]


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


def patch_existing_metrics(patches, *, db_path=None) -> dict:
    """在主数据中精确补充已有抖音作品；缺失库/表不创建，不运行迁移。"""
    from toolbox.douyin_index_metrics import (
        merge_existing_metrics, metric_patch_result, metric_patch_status)
    result = metric_patch_result()
    if not isinstance(patches, dict) or not patches:
        return result
    path = _resolve_db_path(db_path)
    if not path.is_file():
        return result
    try:
        with _lock:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
            try:
                conn.execute("PRAGMA busy_timeout=10000")
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'").fetchone():
                    return result
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute("SELECT dedup_key, data FROM videos WHERE platform='douyin'").fetchall()
                for key, payload in rows:
                    try:
                        record = json.loads(payload)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    video_id = str(record.get("video_id") or "")
                    if video_id not in patches:
                        continue
                    result["matched"] += 1
                    if video_id not in result["matched_ids"]:
                        result["matched_ids"].append(video_id)
                    status = metric_patch_status(record, patches[video_id])
                    if status != "ready":
                        result[status] += 1
                        if status == "stale" and video_id not in result["stale_ids"]:
                            result["stale_ids"].append(video_id)
                        continue
                    merged, changed = merge_existing_metrics(record, patches[video_id])
                    if changed:
                        conn.execute("UPDATE videos SET data=? WHERE dedup_key=? AND platform='douyin'",
                                     (json.dumps(merged, ensure_ascii=False), key))
                        result["updated"] += 1
                        if video_id not in result["updated_ids"]:
                            result["updated_ids"].append(video_id)
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        result["updated"], result["updated_ids"] = 0, []
        result["errors"].append({"store": "collected", "error": type(exc).__name__})
    return result
