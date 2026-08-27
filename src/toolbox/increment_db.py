# -*- coding: utf-8 -*-
"""增量采集数据库：账号追踪「增量模式」抓取的内容分平台分表入库。

config/increment.db，每个平台一张表（表名=平台英文标识，首次写入时自动建表）。
字段逐列存储（获取时间/标题/作者/发布时间/各计数/链接/正文等），
完整原始数据另存 raw_json 列兜底；去重键 dedup_key 唯一，重复抓取自动忽略。
旧 download/已获取数据/_增量汇总.json 首次使用时自动迁移入库。

会被后台线程调用 → 每次操作短连接（开-关），禁止模块级共享连接。
"""

import json
import sqlite3
import threading
import time
from dataclasses import asdict, is_dataclass

_lock = threading.Lock()

# 统一列定义（所有平台表同构）：(列名, 类型)
COLUMNS = [
    ("dedup_key", "TEXT PRIMARY KEY"),   # 去重键（链接/视频ID）
    ("fetched_at", "TEXT"),               # 获取时间 YYYY-MM-DD HH:MM:SS
    ("kind", "TEXT"),                     # video/dynamic/image
    ("video_id", "TEXT"),
    ("title", "TEXT"),
    ("author", "TEXT"),
    ("publish_time", "TEXT"),             # 发布时间
    ("play_count", "INTEGER"),            # 播放量/阅览量
    ("like_count", "INTEGER"),
    ("comment_count", "INTEGER"),
    ("share_count", "INTEGER"),
    ("favorite_count", "INTEGER"),
    ("danmaku_count", "INTEGER"),
    ("coin_count", "INTEGER"),
    ("duration", "INTEGER"),              # 视频时长（秒）
    ("description", "TEXT"),              # 正文
    ("cover_url", "TEXT"),
    ("video_url", "TEXT"),
    ("tags", "TEXT"),                     # JSON 数组字符串
    ("category", "TEXT"),
    ("music", "TEXT"),
    ("raw_json", "TEXT"),                 # 完整 asdict JSON（兜底全字段）
]

_COL_NAMES = [c for c, _ in COLUMNS]


def db_path():
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "increment.db"


def _connect():
    p = db_path()
    import os
    os.makedirs(p.parent, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table(conn, platform: str):
    cols = ", ".join(f'"{n}" {t}' for n, t in COLUMNS)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{platform}" ({cols})')


def _norm_platform(platform: str) -> str:
    """表名只留安全字符（防注入：平台标识来自采集器，仍统一白名单清洗）。"""
    p = "".join(ch for ch in (platform or "").strip().lower() if ch.isalnum() or ch == "_")
    return p or "unknown"


_CN_KEY_MAP = {  # to_dict() 中文键 → 字段名（dict 入参用）
    "平台": "platform", "类型": "kind", "视频ID": "video_id", "标题": "title",
    "正文": "description", "作者": "author", "作者ID": "author_id",
    "播放量": "play_count", "阅览量": "play_count", "点赞数": "like_count",
    "评论数": "comment_count", "分享数": "share_count", "收藏数": "favorite_count",
    "弹幕数": "danmaku_count", "投币数": "coin_count", "封面URL": "cover_url",
    "链接": "video_url", "发布时间": "publish_time", "标签": "tags",
    "分类": "category", "音乐": "music", "原始URL": "raw_url",
    "获取时间": "fetched_at", "时长": "duration", "分辨率": "dimension",
}


def _row_of(info, now_str: str) -> tuple:
    if isinstance(info, dict):
        data = {_CN_KEY_MAP.get(k, k): v for k, v in info.items()}
        key = (data.get("video_url") or data.get("raw_url")
               or data.get("video_id") or "").strip()
        platform = data.get("platform") or ""
    else:
        key = (getattr(info, "video_url", "") or getattr(info, "raw_url", "")
               or getattr(info, "video_id", "") or "").strip()
        platform = getattr(info, "platform", "") or ""
        data = asdict(info) if is_dataclass(info) else {}
    if not key:
        key = f"nokey_{time.time_ns()}"
    def d(k):
        v = data.get(k)
        return v if v is not None else ""
    tags = d("tags")
    if isinstance(tags, (list, tuple)):
        tags = ",".join(str(t) for t in tags)
    row = (
        key, d("fetched_at") or now_str, d("kind"), d("video_id"), d("title"),
        d("author"), d("publish_time"),
        int(d("play_count") or 0), int(d("like_count") or 0),
        int(d("comment_count") or 0), int(d("share_count") or 0),
        int(d("favorite_count") or 0), int(d("danmaku_count") or 0),
        int(d("coin_count") or 0), int(d("duration") or 0),
        d("description"), d("cover_url"), d("video_url"),
        tags or "", d("category"), d("music"),
        json.dumps(data, ensure_ascii=False) if data else "{}",
    )
    return _norm_platform(platform), row


def save_incremental(posts) -> dict:
    """批量入库（分平台分表，主键冲突自动忽略）。

    posts: VideoInfo 对象列表（或 to_dict() 中文字典列表）。
    返回 {平台: 新增条数}。
    """
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_by_platform: dict[str, list] = {}
    for p in posts or []:
        if p is None:
            continue
        try:
            platform, row = _row_of(p, now_str)
        except Exception:
            continue
        rows_by_platform.setdefault(platform, []).append(row)
    if not rows_by_platform:
        return {}
    added = {}
    try:
        with _lock:
            conn = _connect()
            try:
                for platform, rows in rows_by_platform.items():
                    _ensure_table(conn, platform)
                    cur = conn.executemany(
                        f'INSERT OR IGNORE INTO "{platform}" ({", ".join(_COL_NAMES)}) '
                        f'VALUES ({", ".join("?" * len(_COL_NAMES))})', rows)
                    added[platform] = cur.rowcount
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass
    return added


def migrate_from_summary() -> int:
    """旧 _增量汇总.json → 增量库（一次性，成功后改名 .bak）。返回迁移条数。"""
    from toolbox.config_manager import get_export_subdir
    from pathlib import Path
    jp = get_export_subdir("video_data") / "_增量汇总.json"
    if not jp.exists():
        return 0
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return 0
    save_incremental([d for d in data if isinstance(d, dict)])
    try:
        with _lock:
            conn = _connect()
            try:
                total = 0
                for (name,) in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    total += conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            finally:
                conn.close()
    except Exception:
        total = len(data)
    if total:
        try:
            jp.rename(jp.with_suffix(".json.bak"))
        except OSError:
            pass
    return total


def list_tables() -> list:
    """[(表名, 行数)]，按表名排序；首次调用顺带迁移旧汇总 json。"""
    try:
        migrate_from_summary()
    except Exception:
        pass
    out = []
    try:
        with _lock:
            conn = _connect()
            try:
                names = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                for name in names:
                    n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                    out.append((name, n))
            finally:
                conn.close()
    except Exception:
        pass
    return out


def query_rows(platform: str, keyword: str = "", limit: int = 500, offset: int = 0) -> tuple:
    """查询平台表（标题/作者/正文/ID 关键词过滤）。返回 (行列表, 总条数)。"""
    platform = _norm_platform(platform)
    kw = (keyword or "").strip().lower()
    where, params = "", []
    if kw:
        where = ('WHERE LOWER("title") LIKE ? OR LOWER("author") LIKE ? '
                 'OR LOWER("description") LIKE ? OR LOWER("video_id") LIKE ?')
        params = [f"%{kw}%"] * 4
    rows, total = [], 0
    try:
        with _lock:
            conn = _connect()
            try:
                _ensure_table(conn, platform)
                total = conn.execute(
                    f'SELECT COUNT(*) FROM "{platform}" {where}', params).fetchone()[0]
                q = (f'SELECT {", ".join(_COL_NAMES)} FROM "{platform}" {where} '
                     f'ORDER BY rowid DESC LIMIT ? OFFSET ?')
                rows = conn.execute(q, params + [int(limit), int(offset)]).fetchall()
            finally:
                conn.close()
    except Exception:
        pass
    return rows, total


def delete_row(platform: str, dedup_key: str) -> bool:
    platform = _norm_platform(platform)
    try:
        with _lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    f'DELETE FROM "{platform}" WHERE "dedup_key" = ?', (dedup_key,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()
    except Exception:
        return False
