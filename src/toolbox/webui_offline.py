"""WebUI 线下现场视频中间层（L3）：活动清单与现场视频搜索。

只读：活动清单来自腾讯文档本地快照（`offline_events.load_events`）；
搜索复用 `offline_events.search_event_videos`（B站实时搜索 + 抖音本地归档，
库内已登记链接直接并入）。不写文件、不触碰真实配置与下载目录。
"""

from __future__ import annotations

from toolbox import offline_events

PLATFORMS = ("bilibili", "douyin_archive")
_MAX_RESULTS = 200


def overview() -> dict:
    """线下活动清单（按日期倒序）；快照缺失时返回可读错误。"""
    try:
        events = offline_events.load_events()
    except offline_events.OfflineEventsUnavailable as exc:
        return {"available": False, "error": str(exc), "events": [],
                "years": [], "total": 0}
    rows = [{
        "date": event.date,
        "name": event.name,
        "year": event.year,
        "province": event.province,
        "city": event.city,
        "venue": event.venue,
        "status": event.status,
        "kind": event.kind,
        "songs": list(event.songs),
        "existing": len(event.existing_links),
    } for event in events]
    years = sorted({row["year"] for row in rows if row["year"]}, reverse=True)
    return {"available": True, "error": "", "events": rows,
            "years": years, "total": len(rows),
            "note": "清单来自腾讯文档本地快照；搜索会实时访问 B站并读取抖音本地归档。"}


def search(event_date: str, event_name: str, platforms,
           on_progress=None, is_cancelled=None) -> dict:
    """搜索单场活动现场视频；取消时返回 cancelled=True。"""
    chosen = [str(item) for item in (platforms or [])
              if str(item) in PLATFORMS]
    if not chosen:
        raise ValueError("请至少选择一个搜索来源")
    date = str(event_date or "").strip()
    name = str(event_name or "").strip()
    if not date or not name:
        raise ValueError("请选择线下活动")
    try:
        events = offline_events.load_events()
    except offline_events.OfflineEventsUnavailable as exc:
        raise ValueError(str(exc)) from exc
    event = next((item for item in events
                  if item.date == date and item.name == name), None)
    if event is None:
        raise ValueError("活动不存在或已更新，请刷新清单")

    class _Cancel:
        def is_set(self):
            return bool(is_cancelled and is_cancelled())

    def progress(done, total, text):
        if on_progress:
            on_progress(done, total, text)

    outcome = offline_events.search_event_videos(
        event, platforms=chosen, progress=progress, cancel=_Cancel())
    results = [_result_payload(item)
               for item in (getattr(outcome, "results", None) or [])]
    results = results[:_MAX_RESULTS]
    errors = [str(item) for item in (getattr(outcome, "errors", None) or [])]
    cancelled = bool(getattr(outcome, "cancelled", False))
    return {"summary": f"找到 {len(results)} 条现场视频",
            "results": results, "errors": errors[:3],
            "cancelled": cancelled, "platforms": chosen}


def _result_payload(item) -> dict:
    return {
        "source": getattr(item, "source", "") or "",
        "platform": getattr(item, "platform", "") or "",
        "title": getattr(item, "title", "") or "",
        "author": getattr(item, "author", "") or "",
        "duration": getattr(item, "duration_text", "-") or "-",
        "publish_time": getattr(item, "publish_time", "") or "",
        "kind": getattr(item, "kind", "") or "",
        "score": int(getattr(item, "score", 0) or 0),
        "reasons": [str(reason) for reason in
                    (getattr(item, "reasons", None) or [])][:3],
        "url": getattr(item, "url", "") or "",
        "note": getattr(item, "note", "") or "",
    }