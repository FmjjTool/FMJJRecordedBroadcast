"""WebUI 抖音指数：读取本地快照，并支持达人搜索与抓取。

读取本地 `latest.json`；搜索走 `platforms.douyin_index.search_daren`，
抓取走 `fetch_daren_all` + `save_snapshot`（写入「抖音指数」导出目录）。
需要桌面端配置的抖音指数 Cookie。本模块 Qt-free。
"""

from __future__ import annotations

import json
from pathlib import Path

from platforms import douyin_index as dxi

_CORE_ROWS = [
    ("新增点赞量", "like_info", "new_like_count_by_"),
    ("净增粉丝量", "fans_info", "new_fans_count_by_"),
    ("新增分享量", "share_info", "new_share_count_by_"),
    ("新增评论量", "comment_info", "new_comment_count_by_"),
    ("新增作品量", "item_info", "new_item_count_by_"),
]
_WINDOWS = [("day", "昨日"), ("week", "近7天"),
            ("half_month", "近15天"), ("month", "近30天")]
_AVG_ROWS = [
    ("稿均点赞量", "like_info", "like_average_count_by_"),
    ("稿均涨粉量", "fans_info", "fans_average_count_by_"),
    ("稿均分享量", "share_info", "share_average_count_by_"),
    ("稿均评论量", "comment_info", "comment_average_count_by_"),
]
_FANS_DIMS = ("省份分布", "城市级别", "城市分布", "年龄分布", "性别分布",
              "手机品牌", "手机价格", "兴趣标签")


def _root() -> Path:
    return dxi.index_dir()


def _reports() -> list[dict]:
    root = _root()
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        latest = folder / "latest.json"
        if not latest.is_file():
            continue
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta = data.get("meta") or {}
        author = data.get("author") or {}
        rows.append({
            "name": folder.name,
            "nick": str(author.get("user_name") or folder.name),
            "uid": str(author.get("aweme_id") or meta.get("uid") or ""),
            "fetched_at": str(meta.get("fetched_at") or ""),
            "days": meta.get("days"),
        })
    rows.sort(key=lambda item: item["fetched_at"], reverse=True)
    return rows


def overview() -> dict:
    rows = _reports()
    return {"reports": rows, "total": len(rows),
            "note": "只读：展示本地已保存的快照；搜索与抓取仍在桌面端。"}


def _find(name: str) -> Path:
    name = str(name or "").strip()
    if not name:
        raise ValueError("缺少达人名称")
    for row in _reports():
        if row["name"] == name:
            path = _root() / name / "latest.json"
            if path.is_file():
                return path
    raise ValueError("未找到该达人的本地快照")


def _count(value) -> str:
    return dxi.fmt_count(value) if value is not None else "-"


def _pct(value) -> str:
    return dxi.fmt_pct(value) if value is not None else "-"


def load_report(name: str) -> dict:
    path = _find(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("快照文件读取失败") from exc
    meta = data.get("meta") or {}
    author = data.get("author") or {}
    core = data.get("core_metrics") or {}
    item_metrics = data.get("item_metrics") or {}

    core_rows = []
    for label, seg, prefix in _CORE_ROWS:
        info = core.get(seg) or {}
        cells = []
        for suffix, _title in _WINDOWS:
            value = info.get(prefix + suffix)
            exchange = info.get("exchange_by_" + suffix)
            cells.append({
                "value": _count(value),
                "exchange": _pct(exchange),
                "exchange_raw": exchange if isinstance(exchange, (int, float))
                else None,
            })
        core_rows.append({"label": label, "cells": cells})

    avg_rows = []
    for label, seg, prefix in _AVG_ROWS:
        items = item_metrics.get(seg) or []
        info = items[0] if items else {}
        week = info.get(prefix + "week")
        month = info.get(prefix + "month")
        week_ex = info.get("exchange_by_week")
        month_ex = info.get("exchange_by_month")
        avg_rows.append({
            "label": label,
            "week": f"{week:.2f}" if isinstance(week, (int, float)) else "-",
            "month": f"{month:.2f}" if isinstance(month, (int, float)) else "-",
            "week_exchange": _pct(week_ex),
            "month_exchange": _pct(month_ex),
            "week_exchange_raw": week_ex if isinstance(week_ex, (int, float)) else None,
            "month_exchange_raw": month_ex if isinstance(month_ex, (int, float)) else None,
        })

    by_date = {}
    for key in ("likelistday", "fanslistday", "sharelistday",
                "commentlistday", "itemlistday"):
        for item in core.get(key) or []:
            day = str(item.get("date") or "")
            if len(day) != 8:
                continue
            label = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            by_date.setdefault(label, {})[key] = item.get("count")
    trend = []
    for day in sorted(by_date, reverse=True):
        row = by_date[day]
        trend.append({
            "date": day,
            "like": _count(row.get("likelistday")),
            "fans": _count(row.get("fanslistday")),
            "share": _count(row.get("sharelistday")),
            "comment": _count(row.get("commentlistday")),
            "item": _count(row.get("itemlistday")),
        })

    videos = []
    for video in sorted(data.get("videos") or [],
                        key=lambda v: str(v.get("create_time") or ""),
                        reverse=True):
        created = str(video.get("create_time") or "")
        day = (f"{created[:4]}-{created[4:6]}-{created[6:]}"
               if len(created) == 8 else "-")
        item_id = str(video.get("item_id") or "")
        videos.append({
            "date": day,
            "text": str(video.get("video_text") or ""),
            "like": _count(video.get("like_cnt")),
            "comment": _count(video.get("coment_cnt")),
            "share": _count(video.get("share_cnt")),
            "favorite": _count(video.get("follow_cnt")),
            "index": _count(video.get("video_index")),
            "url": f"https://www.douyin.com/video/{item_id}" if item_id else "",
        })

    fans = data.get("fans_profile") or {}
    fan_rows = []
    for label in _FANS_DIMS:
        items = fans.get(label) or []
        tgi = {str(x.get("name")): x.get("value")
               for x in fans.get(label + "TGI") or []}
        for item in items:
            value = item.get("value")
            fan_rows.append({
                "dimension": label,
                "name": str(item.get("name") or ""),
                "pct": (f"{value * 100:.2f}%"
                        if isinstance(value, (int, float)) else "-"),
                "tgi": (f"{tgi.get(str(item.get('name'))):.2f}"
                        if isinstance(tgi.get(str(item.get("name"))),
                                      (int, float)) else "-"),
            })

    similar = []
    for user in data.get("similar_users") or []:
        similar.append({
            "rank": user.get("rank"),
            "name": str(user.get("user_name") or ""),
            "uid": str(user.get("aweme_id") or "-"),
            "fans": _count(user.get("follow_count")),
            "likes": _count(user.get("like_count")),
            "items": user.get("item_count"),
            "tags": (f'{user.get("first_tag_name") or ""}/'
                     f'{user.get("second_tag_name") or ""}'),
        })

    return {
        "name": name,
        "meta": {"fetched_at": meta.get("fetched_at") or "",
                 "days": meta.get("days"),
                 "start_date": meta.get("start_date") or "",
                 "end_date": meta.get("end_date") or ""},
        "author": {
            "name": str(author.get("user_name") or name),
            "uid": str(author.get("aweme_id") or ""),
            "tags": (f'{author.get("first_tag_name") or ""}/'
                     f'{author.get("second_tag_name") or ""}').strip("/"),
            "fans": _count(author.get("fans_count")),
            "likes": _count(author.get("like_count")),
            "items": author.get("item_count"),
            "intro": str(author.get("user_introduction") or "")[:200],
        },
        "core": core_rows,
        "avg": avg_rows,
        "trend": trend,
        "videos": videos,
        "fans": fan_rows,
        "similar": similar,
    }


def search(payload: dict) -> dict:
    """按名称 / 抖音号搜索达人（只读，需要抖音指数 Cookie）。"""
    from platforms import douyin_index as dxi

    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("请输入达人名称或抖音号")
    try:
        total = int(payload.get("limit") or 20)
    except (TypeError, ValueError):
        total = 20
    total = max(1, min(total, 50))
    try:
        rows = dxi.search_daren(keyword, total) or []
    except Exception as exc:  # noqa: BLE001 - 搜索接口异常类型较多
        raise ValueError(f"搜索失败：{exc}") from exc
    items = []
    for row in rows:
        uid = str(row.get("user_id") or row.get("uid") or "")
        if not uid:
            continue
        try:
            fans = int(row.get("fans_count") or row.get("fans") or 0)
        except (TypeError, ValueError):
            fans = 0
        items.append({
            "uid": uid,
            "name": str(row.get("user_name") or row.get("nick_name") or ""),
            "douyin_id": str(row.get("unique_id") or row.get("short_id") or ""),
            "fans": fans,
            "avatar": str(row.get("avatar_uri") or row.get("avatar_url") or ""),
        })
    return {"items": items, "total": len(items), "keyword": keyword,
            "note": "只读搜索；选一个达人抓取指数快照。"}


def fetch(payload: dict, on_progress=None, on_log=None,
          is_cancelled=None) -> dict:
    """抓取一个达人的完整抖音指数并保存快照。"""
    from platforms import douyin_index as dxi

    uid = str(payload.get("uid") or "").strip()
    if not uid:
        raise ValueError("请选择要抓取的达人")
    try:
        days = int(payload.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(7, min(days, 365))
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")

    def say(message) -> None:
        if on_log:
            on_log(str(message))
        if on_progress:
            on_progress(None, str(message))

    try:
        data = dxi.fetch_daren_all(uid, days, progress=say)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"抓取失败：{exc}") from exc
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消（数据未保存）")
    try:
        path = dxi.save_snapshot(data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"保存快照失败：{exc}") from exc
    name = str((data.get("author") or {}).get("user_name") or uid)
    videos = len(data.get("videos") or [])
    fetched_at = str((data.get("meta") or {}).get("fetched_at") or "")
    return {
        "name": name,
        "file": Path(path).name,
        "videos": videos,
        "days": days,
        "fetched_at": fetched_at,
        "summary": f"已抓取「{name}」抖音指数：{videos} 个视频 · 近 {days} 天",
        "note": "快照已写入「抖音指数」导出目录，页面刷新后可查看。",
    }
