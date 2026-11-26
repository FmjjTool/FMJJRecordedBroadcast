"""WebUI 票务解析：最近缓存 / 导出目录、关键词或链接解析、导出全量 JSON。

解析与导出复用桌面 `tickets` 模块（大麦 / 秀动 / B站会员购），
结果写回 `票务演出/_latest.json` 缓存，导出到 `票务演出/票务导出_时间戳.json`。
本模块 Qt-free；真实解析需要网络与对应登录态。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import get_export_dir

_MAX_EVENTS = 30


def _base() -> Path:
    return Path(get_export_dir()) / "票务演出"


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _count_images(folder: Path) -> int:
    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    count = 0
    try:
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                count += 1
    except OSError:
        pass
    return count


def overview() -> dict:
    base = _base()
    payload = {}
    if (base / "_latest.json").is_file():
        try:
            payload = json.loads(
                (base / "_latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
    events = []
    for item in payload.get("events") or []:
        if not isinstance(item, dict):
            continue
        events.append({
            "title": item.get("title") or "-",
            "platform": item.get("platform_cn") or item.get("platform") or "-",
            "city": item.get("city") or "-",
            "venue": item.get("venue") or "-",
            "show_time": item.get("show_time") or "-",
            "price": item.get("price_range") or "-",
            "status": item.get("status") or "-",
            "url": item.get("url") or "",
        })
    folders = []
    if base.is_dir():
        for folder in sorted(base.iterdir(), key=lambda item: item.name):
            if not folder.is_dir():
                continue
            try:
                stat = folder.stat()
            except OSError:
                continue
            folders.append({
                "name": folder.name,
                "images": _count_images(folder),
                "mtime": _ts(stat.st_mtime),
            })
    folders.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "saved_at": str(payload.get("saved_at") or ""),
        "events": events,
        "total": len(events),
        "folders": folders[:200],
        "folder_total": len(folders),
        "base": str(base),
        "note": "可输入链接或关键词解析（大麦 / 秀动 / B站会员购），并可导出全量 JSON。",
    }


def _event_row(event) -> dict:
    data = event.to_dict() if hasattr(event, "to_dict") else dict(event or {})
    return {
        "key": f"{data.get('平台') or ''}:{data.get('演出ID') or ''}",
        "platform": str(data.get("平台") or ""),
        "event_id": str(data.get("演出ID") or ""),
        "title": str(data.get("标题") or ""),
        "city": str(data.get("城市") or ""),
        "venue": str(data.get("场馆") or ""),
        "time": str(data.get("时间") or ""),
        "price": str(data.get("票价") or data.get("价格") or ""),
        "status": str(data.get("状态") or ""),
        "url": str(data.get("链接") or ""),
    }


def parse(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """解析票务链接或关键词，保存中间层缓存并返回演出列表。"""
    import tickets as tickets_mod

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("请输入票务链接或关键词")
    scope = str(payload.get("scope") or "all")
    if scope not in ("all", "recent"):
        scope = "all"

    def say(message) -> None:
        if on_log:
            on_log(str(message))

    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")
    try:
        kind, _ = tickets_mod.detect_ticket_platform(text)
        if kind:
            say(f"识别为 {kind} 链接，开始解析…")
            result = tickets_mod.parse_ticket_url(text, scope=scope)
        else:
            looks_like_url = ("://" in text or "/" in text
                              or any(ch.isdigit() for ch in text[:5]))
            if looks_like_url:
                raise ValueError(
                    "无法识别的票务链接（支持大麦艺人/演出、秀动艺人/事件、"
                    "B站会员购搜索/项目）")
            say("按关键词三平台搜索…")
            result = tickets_mod.search_tickets_by_keyword(text, scope=scope)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - 平台接口异常类型较多
        raise ValueError(f"解析失败：{exc}") from exc
    if isinstance(result, tuple):
        events, diag = result
    else:
        events, diag = result, {}
    events = list(events or [])
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消（未保存缓存）")
    try:
        tickets_mod.save_latest_events(events)
    except Exception as exc:  # noqa: BLE001 - 缓存失败不影响结果
        say(f"中间层缓存保存失败：{str(exc)[:60]}")
    rows = [_event_row(event) for event in events]
    scope_cn = "近期（在售）" if scope == "recent" else "所有（含历史）"
    return {
        "events": rows,
        "total": len(rows),
        "scope": scope,
        "scope_label": scope_cn,
        "diag": {str(key): value for key, value in (diag or {}).items()},
        "summary": f"解析到 {len(rows)} 场演出（{scope_cn}）",
        "note": ("结果已写入票务缓存；可勾选后导出全量 JSON（含详情与图片，"
                 "单次最多 30 场）。" if rows else
                 "没有解析到演出，可切换范围或更换关键词/链接。"),
    }


def export(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """按当前条件解析并导出全量 JSON（含演出图片下载）。"""
    import tickets as tickets_mod

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("请输入票务链接或关键词")
    scope = "recent" if str(payload.get("scope")) == "recent" else "all"
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")
    try:
        kind, _ = tickets_mod.detect_ticket_platform(text)
        raw = (tickets_mod.parse_ticket_url(text, scope=scope) if kind
               else tickets_mod.search_tickets_by_keyword(text, scope=scope))
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"解析失败：{exc}") from exc
    events = list((raw[0] if isinstance(raw, tuple) else raw) or [])[:_MAX_EVENTS]
    if not events:
        raise ValueError("没有可导出的演出")
    base = _base()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"导出目录不可用：{exc}") from exc
    data, failed = [], []
    for index, event in enumerate(events, 1):
        if is_cancelled and is_cancelled():
            raise ValueError("任务已取消（未写入文件）")
        if on_log:
            on_log(f"导出进度 {index}/{len(events)}：{str(event.title)[:26]}")
        if index > 1:
            time.sleep(1.5)  # 与桌面一致：逐场限速，降低风控
        data.append(event.to_dict())
        try:
            tickets_mod.download_event_images(event, base, log=on_log)
        except Exception as exc:  # noqa: BLE001 - 图片失败不影响 JSON
            failed.append(f"{str(event.title)[:20]}：图片下载失败"
                          f"（{str(exc)[:40]}）")
    payload_data = {"演出": data, "导出告警": failed} if failed else data
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = base / f"票务导出_{stamp}.json"
    try:
        path.write_text(json.dumps(payload_data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"导出失败：{exc}") from exc
    return {
        "file": path.name,
        "total": len(data),
        "failed": len(failed),
        "dir": base.name,
        "summary": f"已导出 {len(data)} 场演出全量 JSON"
                   + (f"，{len(failed)} 场图片失败" if failed else ""),
        "note": "文件写入「票务演出」导出目录；图片按演出分目录保存。",
    }
