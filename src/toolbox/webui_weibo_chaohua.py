"""WebUI 微博超话：本地 SQLite 累计数据，以及解析 / 采集 / 导出 Excel。

本地数据只读（config/weibo_chaohua.db）；解析与采集走平台同一套实现，
采集结果入库后可导出与桌面同格式的 Excel。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

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


_CACHE: dict[str, dict] = {}


def _wch():
    from platforms import weibo_chaohua as wch
    return wch


def parse(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """解析超话链接 / ID，返回概览与频道列表。"""
    wch = _wch()
    log = on_log or (lambda _msg: None)
    raw = str(payload.get("text") or "").strip()
    if not raw:
        raise ValueError("请输入超话链接或 ID")
    try:
        topic_id = wch.extract_topic_id(raw)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    log(f"解析超话 {topic_id} …")
    info = wch.fetch_overview(topic_id)
    info.setdefault("topic_id", topic_id)
    channels = [{
        "name": channel.get("name") or "频道",
        "flow_id": channel.get("flow_id") or "",
        "max_pages": int(channel.get("max_pages") or 10),
    } for channel in info.get("channels") or []]
    _CACHE[topic_id] = {"info": info, "data": None}
    return {
        "topic_id": topic_id,
        "nick": info.get("nick") or topic_id,
        "desc": str(info.get("desc") or "")[:300],
        "labels": info.get("labels") or [],
        "post_count": info.get("post_count_str") or "-",
        "fans": (f'{info.get("fans_label") or ""}{info.get("fans_str") or ""}'
                 .strip() or "-"),
        "channels": channels,
        "summary": f'已解析「{info.get("nick") or topic_id}」'
                   f'，{len(channels)} 个频道',
    }


def collect(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """采集所选频道并入库；结果缓存供导出。"""
    import time

    wch = _wch()
    log = on_log or (lambda _msg: None)
    topic_id = str(payload.get("topic_id") or "").strip()
    if not topic_id:
        raise ValueError("请先解析超话")
    channels = payload.get("channels") or []
    selected = [{
        "name": str(item.get("name") or "频道"),
        "flow_id": str(item.get("flow_id") or ""),
        "max_pages": max(1, min(500, int(item.get("max_pages") or 10))),
    } for item in channels if isinstance(item, dict) and item.get("flow_id")]
    if not selected:
        raise ValueError("请至少勾选一个频道")

    cached = _CACHE.get(topic_id) or {}
    info = cached.get("info")
    if not info:
        log("重新解析超话概览…")
        info = wch.fetch_overview(topic_id)
        info.setdefault("topic_id", topic_id)
        _CACHE[topic_id] = {"info": info, "data": None}

    class _Flag:
        def is_set(self):
            return bool(is_cancelled and is_cancelled())

    started = time.time()
    log(f"开始采集 {len(selected)} 个频道…")
    client = wch.ChaohuaClient(topic_id, progress=lambda msg: log(str(msg)),
                               stop_event=_Flag())
    data = client.crawl(selected)
    stopped = bool(is_cancelled and is_cancelled())
    db_result = {"error": ""}
    try:
        db_result = chs.save_crawl(info, data, started, time.time(),
                                   status="stopped" if stopped else "done")
    except Exception as exc:  # noqa: BLE001 - 入库失败不影响本次结果
        db_result = {"error": str(exc)}
        log(f"入库失败：{exc}")

    _CACHE[topic_id] = {"info": info, "data": data}
    merged = wch.merge_posts(data)
    channel_counts = [{"name": name, "count": len(posts)}
                      for name, posts in (data or {}).items()]
    summary = (f"采集完成：{len(merged)} 条（{len(selected)} 个频道）"
               if not stopped else f"已停止：{len(merged)} 条")
    return {
        "topic_id": topic_id,
        "nick": info.get("nick") or topic_id,
        "total": len(merged),
        "channels": channel_counts,
        "saved": not (db_result or {}).get("error"),
        "db_error": str((db_result or {}).get("error") or ""),
        "stopped": stopped,
        "summary": summary,
    }


def export(payload: dict) -> dict:
    """把最近一次采集结果导出为 Excel（返回字节）。"""
    wch = _wch()
    topic_id = str(payload.get("topic_id") or "").strip()
    cached = _CACHE.get(topic_id) or {}
    if not cached.get("data"):
        raise ValueError("请先采集一次再导出")
    info, data = cached["info"], cached["data"]
    try:
        path = wch.export_xlsx(info, data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"导出失败：{exc}") from exc
    target = Path(str(path))
    try:
        payload_bytes = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取导出文件失败：{exc}") from exc
    return {"data": payload_bytes, "filename": target.name,
            "summary": f"已导出 {target.name}"}
