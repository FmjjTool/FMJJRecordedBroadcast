# -*- coding: utf-8 -*-
"""增量采集数据库：账号获取「增量模式」抓取的内容分平台分表入库。

config/increment.db，每个平台一张表（表名=平台英文标识，首次写入时自动建表）。
字段逐列存储（获取时间/标题/作者/发布时间/各计数/链接/正文等），
完整原始数据另存 raw_json 列兜底。

去重规则：仅表内按 dedup_key 去重（优先用 video_id 稳定 ID，避免抖音 CDN 直链过期导致重复入库）。
跨平台互发内容不去重——按平台分表浏览时各表须完整（快手全是互发内容，
跨表拦截曾把整张快手表拦成 0 行）；content_key 仅供检索/排查，不参与任何去重。

归属：collected.db 是「已获取」页面展示/导出快照，唯一写入方
toolbox/collected_store.py；increment.db 是账号增量采集的分平台归档，
唯一写入方本模块（由 collected_store.ingest_items 触发投影）。两者暂不合并。
内部表 _meta 是迁移标记，不出现在平台下拉里；历史 _content_keys 表可能残留在旧库，
已停止维护且不参与去重。旧 download/已获取数据/_增量汇总.json 首次使用时自动迁移入库。

会被后台线程调用 → 每次操作短连接（开-关），禁止模块级共享连接。
"""

import json
import re
import sqlite3
import threading
import time
from dataclasses import asdict, is_dataclass

from toolbox.dedup import collect_key

_lock = threading.Lock()

# 统一列定义（所有平台表同构）：(列名, 类型)
COLUMNS = [
    ("dedup_key", "TEXT PRIMARY KEY"),   # 去重键（优先 video_id 稳定 ID）
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
    ("content_key", "TEXT"),              # 跨平台内容指纹（标题+作者归一化）
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


def _platform_tables(conn) -> list:
    """平台表名列表（排除 sqlite 内部表与本模块内部表）。"""
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return [n for n in names if not n.startswith(("sqlite_", "_"))]


def _ensure_table(conn, platform: str):
    cols = ", ".join(f'"{n}" {t}' for n, t in COLUMNS)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{platform}" ({cols})')
    # 老表补 content_key 列（值由迁移回填）
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{platform}")')}
    if "content_key" not in have:
        conn.execute(f'ALTER TABLE "{platform}" ADD COLUMN "content_key" TEXT')


def _norm_platform(platform: str) -> str:
    """表名只留安全字符（防注入：平台标识来自采集器，仍统一白名单清洗）。"""
    p = "".join(ch for ch in (platform or "").strip().lower() if ch.isalnum() or ch == "_")
    return p or "unknown"


def _content_key(title: str, author: str) -> str:
    """内容指纹：标题+作者 归一化（去【】前缀/#话题/空白标点，小写）。

    仅写入 content_key 列供检索/排查，不参与跨平台去重；无有效标题返回 ""。
    """
    t = title or ""
    t = re.sub(r"【[^】]*】", "", t)     # 【作者名】前缀（B站标题习惯）
    t = re.sub(r"#[^\s#]+", "", t)      # #话题 标签（平台各异）
    t = re.sub(r"[\W_]+", "", t).lower()  # 空白/标点/emoji
    a = re.sub(r"[\W_]+", "", author or "").lower()
    return f"{t}|{a}" if t else ""


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


def _to_int(v) -> int:
    """计数/时长容错转 int：'0:11'/'01:02:03' 时长文本 → 秒；'1.7万' → 17000；其余原样。"""
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if ":" in s:
        secs = 0
        for part in s.split(":"):
            secs = secs * 60 + int(float(part or 0))
        return secs
    m = re.match(r"([\d.]+)\s*万", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"([\d.]+)\s*亿", s)
    if m:
        return int(float(m.group(1)) * 100000000)
    try:
        return int(float(s))
    except ValueError:
        return 0


def _row_of(info, now_str: str) -> tuple:
    if isinstance(info, dict):
        data = {_CN_KEY_MAP.get(k, k): v for k, v in info.items()}
        key = collect_key(data)
        platform = data.get("platform") or ""
    else:
        key = collect_key(info)
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
        int(_to_int(d("play_count"))), int(_to_int(d("like_count"))),
        int(_to_int(d("comment_count"))), int(_to_int(d("share_count"))),
        int(_to_int(d("favorite_count"))), int(_to_int(d("danmaku_count"))),
        int(_to_int(d("coin_count"))), int(_to_int(d("duration"))),
        d("description"), d("cover_url"), d("video_url"),
        tags or "", d("category"), d("music"),
        json.dumps(data, ensure_ascii=False) if data else "{}",
        _content_key(d("title"), d("author")),
    )
    return _norm_platform(platform), row


def _ensure_migrated(conn):
    """历史表结构迁移：补 content_key 列并回填，不删除任何行。幂等。

    dedup_v1 是历史迁移标记，名称保留；旧版本曾在该标记下执行跨表删重，
    本次起不再删除重复行，已删除的历史行无法恢复。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
    if conn.execute("SELECT 1 FROM _meta WHERE key='dedup_v1'").fetchone():
        return
    # 空表 unknown 是旧版空平台查询时误建的残留
    try:
        if conn.execute('SELECT COUNT(*) FROM "unknown"').fetchone()[0] == 0:
            conn.execute('DROP TABLE IF EXISTS "unknown"')
    except sqlite3.OperationalError:
        pass
    for name in _platform_tables(conn):
        _ensure_table(conn, name)
        updates = []
        for rid, title, author in conn.execute(
                f'SELECT rowid, "title", "author" FROM "{name}" ORDER BY rowid'):
            ck = _content_key(title, author)
            if ck:
                updates.append((ck, rid))
        if updates:
            conn.executemany(
                f'UPDATE "{name}" SET content_key=? WHERE rowid=?'
                ' AND ("content_key" IS NULL OR "content_key"="")', updates)
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('dedup_v1', ?)",
                 (str(int(time.time())),))
    conn.commit()


def save_incremental(posts) -> dict:
    """批量入库（分平台分表，表内按 dedup_key 去重，跨平台不去重）。

    posts: VideoInfo 或 to_dict() 字典列表。返回 {平台: 新增条数}。
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
                _ensure_migrated(conn)
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
                for name in _platform_tables(conn):
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
    """[(表名, 行数)]，按表名排序；首次调用顺带迁移旧汇总 json 与历史表结构。"""
    try:
        migrate_from_summary()
    except Exception:
        pass
    out = []
    try:
        with _lock:
            conn = _connect()
            try:
                _ensure_migrated(conn)
                for name in _platform_tables(conn):
                    n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                    out.append((name, n))
            finally:
                conn.close()
    except Exception:
        pass
    return out


def query_rows(platform: str, keyword: str = "", author: str = "",
               limit: int = 500, offset: int = 0) -> tuple:
    """查询平台表（关键词过滤 + 作者精确筛选）。返回 (行列表, 总条数)。"""
    if not (platform or "").strip():
        return [], 0
    platform = _norm_platform(platform)
    kw = (keyword or "").strip().lower()
    author = (author or "").strip()
    conds, params = [], []
    if kw:
        conds.append('(LOWER("title") LIKE ? OR LOWER("author") LIKE ? '
                     'OR LOWER("description") LIKE ? OR LOWER("video_id") LIKE ?)')
        params += [f"%{kw}%"] * 4
    if author:
        conds.append('"author" = ?')
        params.append(author)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows, total = [], 0
    try:
        with _lock:
            conn = _connect()
            try:
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


def iter_items(since_date: str = "") -> list:
    """读取全部平台表的条目（dict 形式，附 platform 字段）。

    since_date: 按发布时间过滤（YYYY-MM-DD，空 = 不过滤）；用于中间层同步器
    一次性取全量数据做匹配，避免逐平台分页调用。
    """
    out = []
    try:
        with _lock:
            conn = _connect()
            try:
                _ensure_migrated(conn)
                cols = ", ".join(f'"{name}"' for name in _COL_NAMES)
                for table in _platform_tables(conn):
                    sql = f'SELECT {cols} FROM "{table}"'
                    params = []
                    if since_date:
                        sql += ' WHERE "publish_time" >= ?'
                        params.append(since_date)
                    for row in conn.execute(sql, params):
                        item = dict(zip(_COL_NAMES, row))
                        item["platform"] = table
                        out.append(item)
            finally:
                conn.close()
    except Exception:
        return out
    return out


def list_authors(platform: str) -> list:
    """平台表内作者分布 [(作者, 条数)]，按条数降序（作者快速筛选用）。"""
    if not (platform or "").strip():
        return []
    platform = _norm_platform(platform)
    out = []
    try:
        with _lock:
            conn = _connect()
            try:
                out = conn.execute(
                    f'SELECT "author", COUNT(*) FROM "{platform}" '
                    f'WHERE "author" IS NOT NULL AND "author" != "" '
                    f'GROUP BY "author" ORDER BY COUNT(*) DESC, "author"').fetchall()
            finally:
                conn.close()
    except Exception:
        pass
    return out


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
