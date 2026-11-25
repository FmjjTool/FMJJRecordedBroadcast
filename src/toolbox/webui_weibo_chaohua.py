"""WebUI 微博超话（只读）：读取本地 SQLite（config/weibo_chaohua.db）。

不解析超话、不触发采集；解析与采集仍在桌面端。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from platforms import weibo_chaohua_store as chs


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _connect_readonly():
    path = chs.db_path()
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def overview() -> dict:
    conn = _connect_readonly()
    if conn is None:
        return {"topics": [], "total": 0,
                "note": "本地暂无超话数据库。桌面端完成一次采集后入库，刷新本页即可查看。"}
    try:
        rows = conn.execute(
            "SELECT topic_id, nick, post_count_str, fans_label, fans_str,"
            " last_seen, url FROM topics ORDER BY last_seen DESC").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    topics = [{
        "topic_id": row["topic_id"],
        "nick": row["nick"] or row["topic_id"],
        "post_count": row["post_count_str"] or "-",
        "fans": (f'{row["fans_label"] or ""}{row["fans_str"] or ""}'.strip()
                 or "-"),
        "last_seen": _ts(row["last_seen"]),
        "url": row["url"] or "",
    } for row in rows]
    return {"topics": topics, "total": len(topics),
            "note": "只读：展示本地累计数据；解析超话与采集仍在桌面端。"}


def analysis(topic_id: str) -> dict:
    topic_id = str(topic_id or "").strip()
    if not topic_id:
        raise ValueError("缺少超话 ID")
    known = {item["topic_id"] for item in overview()["topics"]}
    if topic_id not in known:
        raise ValueError("未找到该超话的本地记录")
    result = chs.load_analysis(topic_id)
    topic = result.get("topic") or {}
    last_run = result.get("last_run") or {}
    return {
        "topic": {
            "topic_id": topic.get("topic_id") or topic_id,
            "nick": topic.get("nick") or topic_id,
            "desc": str(topic.get("desc") or "")[:200],
            "url": topic.get("url") or "",
        },
        "total_posts": result.get("total_posts") or 0,
        "total_runs": result.get("total_runs") or 0,
        "first_seen": _ts(result.get("first_seen")),
        "last_seen": _ts(result.get("last_seen")),
        "last_run": {
            "finished_at": _ts(last_run.get("finished_at")),
            "new_posts": last_run.get("new_posts") or 0,
            "unique_total": last_run.get("unique_total") or 0,
            "status": last_run.get("status") or "",
        } if last_run else {},
        "runs": [{
            "finished_at": _ts(row.get("finished_at")),
            "channels": row.get("channels") or "-",
            "raw_total": row.get("raw_total") or 0,
            "unique_total": row.get("unique_total") or 0,
            "new_posts": row.get("new_posts") or 0,
            "status": row.get("status") or "",
        } for row in result.get("runs") or []],
        "snapshots": [{
            "captured_at": _ts(row.get("captured_at")),
            "checkin": row.get("checkin_text") or "-",
            "category": row.get("category_text") or "-",
            "post_count": row.get("post_count_str") or "-",
            "fans": (f'{row.get("fans_label") or ""}'
                     f'{row.get("fans_str") or ""}'.strip() or "-"),
        } for row in result.get("snapshots") or []],
        "daily_new": [{"day": day, "count": count}
                      for day, count in result.get("daily_new") or []],
        "top_posts": [{
            "author": row.get("author") or "-",
            "dt": row.get("dt") or "-",
            "text": str(row.get("text") or "")[:120],
            "likes": row.get("last_likes"),
            "comments": row.get("last_comments"),
            "reposts": row.get("last_reposts"),
            "url": row.get("url") or "",
        } for row in result.get("top_posts") or []],
        "growth": [{
            "author": row.get("author") or "-",
            "dt": row.get("dt") or "-",
            "text": str(row.get("text") or "")[:120],
            "min_likes": row.get("min_likes"),
            "max_likes": row.get("max_likes"),
            "captures": row.get("captures"),
        } for row in result.get("growth") or []],
    }
