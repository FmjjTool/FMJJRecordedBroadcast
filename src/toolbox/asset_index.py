# -*- coding: utf-8 -*-
"""资产管理「已获取资源」磁盘索引缓存（config/asset_index.db）。

为什么需要：列表加载原本每次都把 download/已获取数据/ 下的全部 json（实测
6500+ 个 / 90MB）重新读一遍解析，再为算「预期图片数」把对应的 *_raw.json
再读一遍（另 78MB），最后还要做「条目 × 媒体组」的双循环匹配。单次加载 20s
起步，而切回资产管理页（on_show）每次都会触发一次。

这里把「json 解析结果 + 派生字段」按 (路径, mtime, size) 缓存进 sqlite：
- 未改动的 json 直接命中缓存（一条 SELECT 批量取回，无需逐个解析）；
- 新增/改动的 json 才重新解析，解析后回写；
- 关联的 *_raw.json 的 mtime/size 一并记录，raw 变了同样判失效
  （否则缓存的「预期图片数」会过期，影响「下载不完整」筛选与批量跳过判定）；
- 原始数据（「原始数据」字段，占 json 体积 95%）不进缓存，按需现读。

会被后台线程调用 → 每次操作短连接（开-关），禁止模块级共享连接。
"""

import json
import os
import sqlite3
import threading

_lock = threading.Lock()

# 缓存格式版本：派生字段口径变化时 +1，旧缓存自动全量重建
VER = 1

_db_path = None


def db_path():
    """索引库路径（进程内解析一次：get_config_dir 每次都要做写探针，不必反复解析）。"""
    global _db_path
    if _db_path is not None:
        return _db_path
    from toolbox.config_manager import get_config_dir
    _db_path = get_config_dir() / "asset_index.db"
    return _db_path


def _connect():
    p = db_path()
    os.makedirs(p.parent, exist_ok=True)
    conn = sqlite3.connect(p, timeout=15.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS asset_json ("
        "path TEXT PRIMARY KEY, mtime REAL, size INTEGER, "
        "raw_mtime REAL, raw_size INTEGER, payload TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS media_dir ("
        "dir TEXT PRIMARY KEY, mtime REAL, names TEXT, subs TEXT)")
    return conn


# ==================== 媒体目录内容缓存 ====================

def fetch_media_dirs() -> dict:
    """{目录路径: (mtime, [媒体文件名], [(子目录名, 子目录 mtime)])}"""
    out = {}
    try:
        with _lock:
            conn = _connect()
            try:
                for d, mtime, names, subs in conn.execute(
                        "SELECT dir, mtime, names, subs FROM media_dir"):
                    try:
                        out[d] = (mtime, json.loads(names), json.loads(subs))
                    except Exception:
                        continue
            finally:
                conn.close()
    except Exception:
        return {}
    return out


def upsert_media_dirs(rows):
    """rows: [(目录, mtime, [媒体文件名], [(子目录名, 子目录 mtime)])]"""
    if not rows:
        return
    data = [(_key(d), m, json.dumps(names, ensure_ascii=False),
             json.dumps(subs, ensure_ascii=False)) for d, m, names, subs in rows]
    try:
        with _lock:
            conn = _connect()
            try:
                conn.executemany(
                    "INSERT INTO media_dir(dir, mtime, names, subs) VALUES(?,?,?,?) "
                    "ON CONFLICT(dir) DO UPDATE SET mtime=excluded.mtime, "
                    "names=excluded.names, subs=excluded.subs", data)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def prune_media_dirs(keep_dirs):
    """删除已不存在的目录缓存行（keep_dirs 为空时跳过，防目录临时不可读清空）。"""
    keep = {_key(d) for d in keep_dirs}
    if not keep:
        return 0
    gone = 0
    try:
        with _lock:
            conn = _connect()
            try:
                stale = [r[0] for r in conn.execute("SELECT dir FROM media_dir")
                         if r[0] not in keep]
                if stale:
                    conn.executemany("DELETE FROM media_dir WHERE dir=?",
                                     [(p,) for p in stale])
                    conn.commit()
                    gone = len(stale)
            finally:
                conn.close()
    except Exception:
        return 0
    return gone


def _key(path):
    """路径归一化为缓存键（Windows 大小写不敏感）。"""
    return os.path.normcase(str(path))


def fetch_all() -> dict:
    """{normcase(path): (mtime, size, raw_mtime, raw_size, payload_str)} 批量取回。"""
    out = {}
    try:
        with _lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    "SELECT path, mtime, size, raw_mtime, raw_size, payload "
                    "FROM asset_json")
                for path, mtime, size, rm, rs, payload in cur:
                    if payload:
                        out[path] = (mtime, size, rm, rs, payload)
            finally:
                conn.close()
    except Exception:
        return {}
    return out


def upsert_many(rows):
    """rows: [(path, mtime, size, raw_mtime, raw_size, payload_str), ...]"""
    if not rows:
        return
    data = [(_key(p), m, s, rm, rs, pay) for p, m, s, rm, rs, pay in rows]
    try:
        with _lock:
            conn = _connect()
            try:
                conn.executemany(
                    "INSERT INTO asset_json"
                    "(path, mtime, size, raw_mtime, raw_size, payload) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, "
                    "size=excluded.size, raw_mtime=excluded.raw_mtime, "
                    "raw_size=excluded.raw_size, payload=excluded.payload", data)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def prune(keep_paths):
    """删除已不存在的 json 的缓存行（keep_paths 为空时跳过，防目录临时不可读清空）。"""
    keep = {_key(p) for p in keep_paths}
    if not keep:
        return 0
    gone = 0
    try:
        with _lock:
            conn = _connect()
            try:
                stale = [r[0] for r in conn.execute("SELECT path FROM asset_json")
                         if r[0] not in keep]
                if stale:
                    conn.executemany("DELETE FROM asset_json WHERE path=?",
                                     [(p,) for p in stale])
                    conn.commit()
                    gone = len(stale)
            finally:
                conn.close()
    except Exception:
        return 0
    return gone


def clear():
    """清空缓存（用户手动「重建索引」或排查问题时用）。"""
    try:
        with _lock:
            conn = _connect()
            try:
                conn.execute("DELETE FROM asset_json")
                conn.execute("DELETE FROM media_dir")
                conn.commit()
            finally:
                conn.close()
        return True
    except Exception:
        return False


def stats():
    """缓存概览：(json 缓存行数, 媒体目录缓存行数, 数据库文件字节数)。"""
    try:
        with _lock:
            conn = _connect()
            try:
                n1 = conn.execute("SELECT COUNT(*) FROM asset_json").fetchone()[0]
                n2 = conn.execute("SELECT COUNT(*) FROM media_dir").fetchone()[0]
            finally:
                conn.close()
        return n1, n2, db_path().stat().st_size
    except Exception:
        return 0, 0, 0


__all__ = ["VER", "db_path", "fetch_all", "upsert_many", "prune", "clear",
           "stats", "fetch_media_dirs", "upsert_media_dirs", "prune_media_dirs"]
