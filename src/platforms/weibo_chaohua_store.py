# -*- coding: utf-8 -*-
"""微博超话长效数据库（SQLite）。

存储位置：config/weibo_chaohua.db

表结构：
  topics          超话基础信息（最后采集时的名称/简介/文案）
  runs            每次采集批次（时间、频道、条数、状态）
  posts           帖子主表（按 topic_id + post_id 去重，保留首次/最近入库时间）
  post_metrics    每次采集的互动指标快照（点赞/评论/转发/图片），用于增长分析
  topic_snapshots 每次采集的超话头部快照（今日签到/分类排名/帖子数/粉丝数）

写入策略：帖子 upsert（不改 first_seen），每次采集追加一条互动指标与超话快照。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    topic_id        TEXT PRIMARY KEY,
    nick            TEXT,
    first_seen      REAL,
    last_seen       REAL,
    labels          TEXT,
    "desc"          TEXT,
    post_count_str  TEXT,
    fans_label      TEXT,
    fans_str        TEXT,
    url             TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        TEXT NOT NULL,
    started_at      REAL NOT NULL,
    finished_at     REAL NOT NULL,
    channels        TEXT,
    raw_total       INTEGER,
    unique_total    INTEGER,
    new_posts       INTEGER,
    status          TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    topic_id          TEXT NOT NULL,
    post_id           TEXT NOT NULL,
    mblogid           TEXT,
    author            TEXT,
    uid               TEXT,
    author_followers  INTEGER,
    author_verified   INTEGER,
    created_at        TEXT,
    dt                TEXT,
    text              TEXT,
    source            TEXT,
    region            TEXT,
    pics              INTEGER,
    is_long           INTEGER,
    url               TEXT,
    last_likes        INTEGER,
    last_comments     INTEGER,
    last_reposts      INTEGER,
    first_seen        REAL,
    last_seen         REAL,
    last_channel      TEXT,
    PRIMARY KEY (topic_id, post_id)
);

CREATE TABLE IF NOT EXISTS post_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    topic_id     TEXT NOT NULL,
    post_id      TEXT NOT NULL,
    captured_at  REAL NOT NULL,
    likes        INTEGER,
    comments     INTEGER,
    reposts      INTEGER,
    pics         INTEGER
);

CREATE TABLE IF NOT EXISTS topic_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER,
    topic_id        TEXT NOT NULL,
    captured_at     REAL NOT NULL,
    labels          TEXT,
    category_text   TEXT,
    checkin_text    TEXT,
    post_count_str  TEXT,
    fans_label      TEXT,
    fans_str        TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_first_seen
    ON posts(topic_id, first_seen);
CREATE INDEX IF NOT EXISTS idx_metrics_post
    ON post_metrics(topic_id, post_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_topic
    ON topic_snapshots(topic_id, captured_at);
"""


def db_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "weibo_chaohua.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _fmt_ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def save_crawl(info: dict, data: dict[str, list[dict]], started_at: float,
               finished_at: float, status: str = "done") -> dict:
    """把一次采集结果写入长效库，返回本次新增/累计统计。"""
    from platforms.weibo_chaohua import merge_posts

    merged = merge_posts(data)
    topic_id = info.get("topic_id") or ""
    now = float(finished_at or time.time())
    channels = list(data.keys())
    labels = info.get("labels") or []
    category_text = next((x for x in labels if "签到" not in x), "")
    checkin_text = next((x for x in labels if "签到" in x), "")

    conn = _connect()
    try:
        with conn:
            existing = {row[0] for row in conn.execute(
                "SELECT post_id FROM posts WHERE topic_id = ?", (topic_id,))}
            seen = {post.get("id") for post in merged if post.get("id")}
            new_posts = len(seen - existing)

            conn.execute(
                'INSERT INTO topics (topic_id, nick, first_seen, last_seen, labels, "desc",'
                " post_count_str, fans_label, fans_str, url)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(topic_id) DO UPDATE SET"
                " nick=excluded.nick, last_seen=excluded.last_seen, labels=excluded.labels,"
                ' "desc"=excluded."desc", post_count_str=excluded.post_count_str,'
                " fans_label=excluded.fans_label, fans_str=excluded.fans_str, url=excluded.url",
                (topic_id, info.get("nick") or "", now, now,
                 json.dumps(labels, ensure_ascii=False), info.get("desc") or "",
                 info.get("post_count_str") or "", info.get("fans_label") or "",
                 info.get("fans_str") or "", info.get("url") or ""),
            )

            cursor = conn.execute(
                "INSERT INTO runs (topic_id, started_at, finished_at, channels,"
                " raw_total, unique_total, new_posts, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (topic_id, float(started_at or now), now,
                 json.dumps(channels, ensure_ascii=False),
                 sum(len(posts) for posts in data.values()), len(merged), new_posts, status),
            )
            run_id = int(cursor.lastrowid)

            for post in merged:
                post_id = post.get("id") or ""
                if not post_id:
                    continue
                conn.execute(
                    "INSERT INTO posts (topic_id, post_id, mblogid, author, uid,"
                    " author_followers, author_verified, created_at, dt, text, source, region,"
                    " pics, is_long, url, last_likes, last_comments, last_reposts,"
                    " first_seen, last_seen, last_channel)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(topic_id, post_id) DO UPDATE SET"
                    " mblogid=excluded.mblogid, author=excluded.author, uid=excluded.uid,"
                    " author_followers=excluded.author_followers,"
                    " author_verified=excluded.author_verified,"
                    " created_at=excluded.created_at, dt=excluded.dt, text=excluded.text,"
                    " source=excluded.source, region=excluded.region, pics=excluded.pics,"
                    " is_long=excluded.is_long, url=excluded.url,"
                    " last_likes=excluded.last_likes, last_comments=excluded.last_comments,"
                    " last_reposts=excluded.last_reposts, last_seen=excluded.last_seen,"
                    " last_channel=excluded.last_channel",
                    (topic_id, post_id, post.get("mblogid") or "", post.get("author") or "",
                     post.get("uid") or "", post.get("author_followers") or 0,
                     int(bool(post.get("author_verified"))), post.get("created_at_raw") or "",
                     post.get("dt_str") or "", post.get("text") or "",
                     post.get("source") or "", post.get("region") or "",
                     post.get("pics") or 0, int(bool(post.get("is_long"))),
                     post.get("url") or "", post.get("likes") or 0,
                     post.get("comments") or 0, post.get("reposts") or 0,
                     now, now, post.get("channel") or ""),
                )
                conn.execute(
                    "INSERT INTO post_metrics (run_id, topic_id, post_id, captured_at,"
                    " likes, comments, reposts, pics) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, topic_id, post_id, now, post.get("likes") or 0,
                     post.get("comments") or 0, post.get("reposts") or 0,
                     post.get("pics") or 0),
                )

            conn.execute(
                "INSERT INTO topic_snapshots (run_id, topic_id, captured_at, labels,"
                " category_text, checkin_text, post_count_str, fans_label, fans_str)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, topic_id, now, json.dumps(labels, ensure_ascii=False),
                 category_text, checkin_text, info.get("post_count_str") or "",
                 info.get("fans_label") or "", info.get("fans_str") or ""),
            )

            total_posts = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE topic_id = ?", (topic_id,)).fetchone()[0]
            total_runs = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE topic_id = ?", (topic_id,)).fetchone()[0]
    finally:
        conn.close()

    return {
        "run_id": run_id,
        "new_posts": new_posts,
        "unique_total": len(merged),
        "total_posts": int(total_posts),
        "total_runs": int(total_runs),
        "db_path": str(db_path()),
    }


def load_analysis(topic_id: str, days: int = 90) -> dict:
    """读取某个超话在库中的长期积累数据（累计、批次、快照、每日新增、增长榜）。"""
    conn = _connect()
    try:
        topic = conn.execute(
            "SELECT * FROM topics WHERE topic_id = ?", (topic_id,)).fetchone()
        total_posts = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE topic_id = ?", (topic_id,)).fetchone()[0]
        total_runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE topic_id = ?", (topic_id,)).fetchone()[0]
        span = conn.execute(
            "SELECT MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen"
            " FROM posts WHERE topic_id = ?", (topic_id,)).fetchone()
        last_run = conn.execute(
            "SELECT * FROM runs WHERE topic_id = ? ORDER BY run_id DESC LIMIT 1",
            (topic_id,)).fetchone()
        runs = conn.execute(
            "SELECT * FROM runs WHERE topic_id = ? ORDER BY run_id DESC LIMIT 20",
            (topic_id,)).fetchall()
        snapshots = conn.execute(
            "SELECT * FROM topic_snapshots WHERE topic_id = ?"
            " ORDER BY captured_at DESC LIMIT 60", (topic_id,)).fetchall()[::-1]
        cutoff = time.time() - max(1, days) * 86400
        daily_new = conn.execute(
            "SELECT date(first_seen, 'unixepoch', 'localtime') AS day, COUNT(*) AS count"
            " FROM posts WHERE topic_id = ? AND first_seen >= ?"
            " GROUP BY day ORDER BY day", (topic_id, cutoff)).fetchall()
        top_posts = conn.execute(
            "SELECT author, dt, text, last_likes, last_comments, last_reposts, url"
            " FROM posts WHERE topic_id = ? ORDER BY last_likes DESC LIMIT 10",
            (topic_id,)).fetchall()
        growth = conn.execute(
            "SELECT p.author, p.dt, p.text, p.last_likes,"
            " MIN(m.likes) AS min_likes, MAX(m.likes) AS max_likes, COUNT(m.id) AS captures"
            " FROM posts p JOIN post_metrics m"
            " ON m.topic_id = p.topic_id AND m.post_id = p.post_id"
            " WHERE p.topic_id = ? GROUP BY p.topic_id, p.post_id"
            " HAVING COUNT(m.id) >= 2 AND MAX(m.likes) > MIN(m.likes)"
            " ORDER BY (MAX(m.likes) - MIN(m.likes)) DESC LIMIT 10",
            (topic_id,)).fetchall()
    finally:
        conn.close()

    return {
        "topic": dict(topic) if topic else None,
        "total_posts": int(total_posts),
        "total_runs": int(total_runs),
        "first_seen": span["first_seen"] if span else None,
        "last_seen": span["last_seen"] if span else None,
        "last_run": dict(last_run) if last_run else None,
        "runs": [dict(row) for row in runs],
        "snapshots": [dict(row) for row in snapshots],
        "daily_new": [(row["day"], int(row["count"])) for row in daily_new],
        "top_posts": [dict(row) for row in top_posts],
        "growth": [dict(row) for row in growth],
        "db_path": str(db_path()),
    }


def format_history(result: dict) -> str:
    """把 load_analysis 结果格式化成历史分析页文本。"""
    if not result or not result.get("topic"):
        return "数据库中暂无该超话记录。\n完成一次采集后会自动入库，此页显示累计数据与长期趋势。"

    topic = result["topic"]
    lines = [
        f'超话：{topic.get("nick") or "-"}（{topic.get("topic_id") or "-"}）',
        f'数据库累计：{result["total_posts"]} 个帖子 ｜ {result["total_runs"]} 次采集',
        f'首次入库：{_fmt_ts(result.get("first_seen")) or "-"}',
        f'最近入库：{_fmt_ts(result.get("last_seen")) or "-"}',
        f'数据库：{result.get("db_path") or "-"}',
    ]
    last_run = result.get("last_run")
    if last_run:
        lines.append(
            f'最近一次：{_fmt_ts(last_run.get("finished_at"))} ｜ '
            f'新增 {last_run.get("new_posts") or 0} 条 ｜ '
            f'去重后 {last_run.get("unique_total") or 0} 条 ｜ '
            f'状态 {last_run.get("status") or "-"}'
        )

    snapshots = result.get("snapshots") or []
    if snapshots:
        lines.extend(["", "【超话指标快照（最近 60 次）】"])
        for row in snapshots[-30:]:
            parts = [row.get("checkin_text") or "", row.get("category_text") or "",
                     f'帖子 {row.get("post_count_str") or "-"}',
                     f'{row.get("fans_label") or "粉丝"} {row.get("fans_str") or "-"}']
            lines.append(f'{_fmt_ts(row.get("captured_at"))}  ' + " ｜ ".join(p for p in parts if p))

    daily = result.get("daily_new") or []
    if daily:
        lines.extend(["", "【每日新增帖子（首次入库口径）】"])
        for day, count in daily[-30:]:
            lines.append(f"{day}  +{count}")

    growth = result.get("growth") or []
    if growth:
        lines.extend(["", "【互动增长 TOP10（多次采集的点赞增量）】"])
        for index, row in enumerate(growth, 1):
            gain = (row.get("max_likes") or 0) - (row.get("min_likes") or 0)
            text = (row.get("text") or "").replace("\n", " ")[:42]
            lines.append(
                f'{index:>2}. +{gain} 赞（{row.get("min_likes")} → {row.get("max_likes")}，'
                f'{row.get("captures")} 次）  {row.get("author") or "-"}  {text}'
            )

    top = result.get("top_posts") or []
    if top:
        lines.extend(["", "【库内高赞 TOP10（最近一次指标）】"])
        for index, row in enumerate(top, 1):
            text = (row.get("text") or "").replace("\n", " ")[:42]
            lines.append(
                f'{index:>2}. {row.get("last_likes") or 0} 赞 / '
                f'{row.get("last_comments") or 0} 评 / {row.get("last_reposts") or 0} 转  '
                f'{row.get("author") or "-"}  {text}'
            )

    runs = result.get("runs") or []
    if runs:
        lines.extend(["", "【最近 20 次采集记录】"])
        for row in runs:
            lines.append(
                f'{_fmt_ts(row.get("started_at"))} ｜ 新增 {row.get("new_posts") or 0} ｜ '
                f'去重 {row.get("unique_total") or 0} ｜ 原始 {row.get("raw_total") or 0} ｜ '
                f'{row.get("status") or "-"}'
            )

    return "\n".join(lines)
