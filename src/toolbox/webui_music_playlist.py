"""歌单创建 WebUI 适配（L3，Qt-free）。

对齐桌面「歌单」页：选来源（本地完整歌曲快照 / 线下活动 / 平台歌单链接 /
粘贴清单）→ 选目标平台与歌单名 → 预览匹配 → 确认写入平台。

预览结果缓存在进程内（30 分钟），写入时按 preview_id 取回，避免重复匹配。
同时保留只读的报告列表与报告内容读取。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from music import playlist_service as service

_MAX_TEXT = 512 * 1024

_PLANS: dict[str, dict] = {}
_LOCK = threading.Lock()
_PLAN_TTL = 30 * 60
_MAX_PREVIEW_ROWS = 1200

_STATE_LABELS = {
    "matched": "已匹配",
    "uncertain": "待确认",
    "missing": "未找到",
    "duplicate": "重复",
}


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _report_dir() -> Path:
    return Path(service.report_dir())


def overview() -> dict:
    root = _report_dir()
    reports = []
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            name = path.name
            if not (name.startswith("preview_")
                    or name.startswith("journal_")
                    or name.startswith("result_")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if name.startswith("preview_"):
                kind = "预览"
            elif name.startswith("journal_"):
                kind = "执行日志"
            else:
                kind = "执行结果"
            reports.append({
                "name": name,
                "kind": kind,
                "size": int(stat.st_size),
                "mtime": _ts(stat.st_mtime),
            })
    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "report_dir": str(root),
        "reports": reports,
        "total": len(reports),
        "note": "可生成预览并创建歌单；写入需要对应平台登录态。",
    }


def report_text(name: str) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("缺少报告文件名")
    root = _report_dir().resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("文件不在报告目录内") from exc
    if not target.is_file() or target.suffix.lower() != ".json":
        raise ValueError("报告文件不存在")
    try:
        if target.stat().st_size > _MAX_TEXT:
            raise ValueError("报告超过 512KB，请到桌面端查看")
        data = json.loads(target.read_text(encoding="utf-8"))
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except (OSError, ValueError) as exc:
        raise ValueError("报告文件读取失败") from exc
    return {"name": target.name, "text": text}


def _prune() -> None:
    now = time.time()
    with _LOCK:
        for key in [k for k, v in _PLANS.items()
                    if now - v["created"] > _PLAN_TTL]:
            _PLANS.pop(key, None)


def capabilities() -> list[dict]:
    rows = []
    for row in service.capabilities():
        rows.append({
            "platform": row["platform"],
            "name": row["name"],
            "supports_create": bool(row["supports_create"]),
            "available": bool(row.get("available", True)),
            "experimental": bool(row.get("experimental")),
            "note": row.get("note") or "",
        })
    return rows


def events() -> list[dict]:
    out = []
    for event in service.load_offline_events():
        out.append({
            "row_no": event.get("row_no"),
            "name": event.get("name") or "",
            "date": event.get("date") or "",
            "city": event.get("city") or "",
            "song_count": int(event.get("song_count") or 0),
        })
    return out


def _text_sources(text: str) -> list:
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
        elif " - " in line:
            parts = [p.strip() for p in line.split(" - ", 1)]
        else:
            parts = [line]
        title = parts[0]
        artist = parts[1] if len(parts) > 1 else ""
        if title:
            rows.append(service.SourceSong(title=title, artist=artist))
    return rows


def _load_sources(payload: dict):
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    kind = str(source.get("kind") or "snapshot")
    value = str(source.get("value") or "").strip()
    if kind == "snapshot":
        return service.read_source_complete_songs()
    if kind == "event":
        row_no = source.get("row_no")
        if row_no is None:
            raise ValueError("请先选择一场线下活动")
        return service.read_source_event(int(row_no))
    if kind == "text":
        songs = _text_sources(value)
        if not songs:
            raise ValueError("粘贴的清单里没有可用的歌曲行")
        return songs, f"粘贴清单 {len(songs)} 首"
    if kind == "playlist":
        if not value:
            raise ValueError("请填写歌单链接或 ID")
        platform = service.detect_platform(value)
        if not platform:
            raise ValueError("无法识别歌单链接所属平台")
        sources, name = service.read_source_playlist(platform, value)
        label = f"{service.PLATFORM_NAMES.get(platform, platform)}歌单「{name}」"
        return sources, label
    raise ValueError("未知的来源类型")


def _rows(plans) -> list[dict]:
    rows = []
    for plan in plans:
        for index, item in enumerate(plan.items, 1):
            target = item.target
            rows.append({
                "platform": plan.platform,
                "platform_name": plan.platform_name,
                "index": index,
                "source_title": item.source.title,
                "source_artist": item.source.artist or "",
                "state": _STATE_LABELS.get(item.status, item.status),
                "target_name": (target.song_name or target.title) if target else "",
                "target_artist": (target.singer or "") if target else "",
                "score": round(item.score, 2) if target else None,
                "note": item.note or "",
            })
    return rows


def _event_of(payload: dict) -> dict | None:
    """来源是线下活动时取回该活动（含 record_id），供写入后回填歌单链接。"""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    if str(source.get("kind") or "") != "event":
        return None
    row_no = source.get("row_no")
    if row_no is None:
        return None
    try:
        for event in service.load_offline_events():
            if int(event.get("row_no")) == int(row_no):
                return event
    except (TypeError, ValueError, OSError):
        return None
    return None


def has_preview(preview_id: str) -> bool:
    _prune()
    with _LOCK:
        return bool(preview_id) and preview_id in _PLANS


def preview(payload: dict, on_log=None, is_cancelled=None) -> dict:
    log = on_log or (lambda _msg: None)
    _prune()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("请填写歌单名称")
    targets = [str(p) for p in (payload.get("platforms") or [])]
    if not targets:
        raise ValueError("请至少选择一个目标平台")
    available = {row["platform"]: row for row in capabilities()}
    for platform in targets:
        row = available.get(platform)
        if row is None or not row["supports_create"] or not row["available"]:
            raise ValueError(f"{platform} 暂不支持创建歌单")

    log("开始读取来源…")
    sources, label = _load_sources(payload)
    if not sources:
        raise ValueError("来源里没有歌曲")
    log(f"来源：{label} · {len(sources)} 首")

    cancel = is_cancelled or (lambda: False)
    plans = []
    for platform in targets:
        if cancel():
            break
        log(f"正在匹配 {available[platform]['name']}…")
        plan = service.build_plan(
            platform, sources, name,
            progress=lambda index, total, source, item, p=platform:
                log(f"{available[p]['name']} {index}/{total}：{source.title}"
                    f"（{_STATE_LABELS.get(item.status, item.status)}）"),
            cancel=_CancelFlag(cancel))
        plan.source_label = label
        plans.append(plan)

    counts = {plan.platform: plan.counts() for plan in plans}
    matched = sum(item["matched"] for item in counts.values())
    total = sum(len(plan.items) for plan in plans)
    rows = _rows(plans)
    truncated = len(rows) > _MAX_PREVIEW_ROWS
    if truncated:
        rows = rows[:_MAX_PREVIEW_ROWS]

    preview_id = uuid.uuid4().hex
    with _LOCK:
        _PLANS[preview_id] = {"created": time.time(), "plans": plans,
                              "source": label, "name": name,
                              "event": _event_of(payload)}
    report = _write_report({
        "mode": "preview",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": label,
        "plans": [plan.to_dict() for plan in plans],
    })
    log(f"预览完成：{len(plans)} 个平台，已匹配 {matched}/{total} 首")
    return {
        "preview_id": preview_id,
        "name": name,
        "source": label,
        "source_count": len(sources),
        "plans": [{
            "platform": plan.platform,
            "platform_name": plan.platform_name,
            "playlist_name": plan.playlist_name,
            "counts": counts[plan.platform],
        } for plan in plans],
        "rows": rows,
        "total": total,
        "matched": matched,
        "truncated": truncated,
        "report": report,
        "summary": f"{label} · {matched}/{total} 首可写入",
    }


def apply(payload: dict, on_log=None, is_cancelled=None) -> dict:
    log = on_log or (lambda _msg: None)
    _prune()
    preview_id = str(payload.get("preview_id") or "")
    with _LOCK:
        cached = _PLANS.get(preview_id)
    if not cached:
        raise ValueError("预览结果已过期，请重新预览")
    targets = [str(p) for p in (payload.get("platforms") or [])]
    plans = [plan for plan in cached["plans"]
             if not targets or plan.platform in targets]
    if not plans:
        raise ValueError("所选平台没有预览结果")
    include_uncertain = bool(payload.get("include_uncertain"))
    cancel = is_cancelled or (lambda: False)

    results = []
    report_dir = service.report_dir()
    for plan in plans:
        if cancel():
            break
        log(f"写入 {plan.platform_name}：{plan.playlist_name}")
        try:
            cookie = service.get_login_jar(plan.platform)
        except Exception as exc:  # noqa: BLE001 - 未登录等按跳过记录
            results.append({
                "platform": plan.platform,
                "platform_name": plan.platform_name,
                "playlist_name": plan.playlist_name,
                "stopped": str(exc),
            })
            log(f"{plan.platform_name} 跳过：{exc}")
            continue
        slot_note = service.login_slot_label(plan.platform)
        if slot_note:
            log(f"{plan.platform_name} 建单账号：{slot_note}")
        journal = report_dir / f"journal_{plan.platform}.json"
        try:
            result = service.apply_plan(
                plan, cookie,
                include_uncertain=include_uncertain,
                journal_path=journal,
                on_progress=lambda done, total, message, p=plan.platform_name:
                    log(f"{p} {done}/{total}：{message}"),
                cancel=_CancelFlag(cancel))
        except Exception as exc:  # noqa: BLE001
            results.append({
                "platform": plan.platform,
                "platform_name": plan.platform_name,
                "playlist_name": plan.playlist_name,
                "stopped": str(exc),
            })
            log(f"{plan.platform_name} 失败：{exc}")
            continue
        data = result.to_dict()
        data["platform_name"] = plan.platform_name
        results.append(data)
        log(f"{plan.platform_name}：已添加 {len(data.get('added') or [])} 首，"
            f"失败 {len(data.get('failed') or [])} 首，"
            f"跳过 {len(data.get('skipped') or [])} 首")

    writeback = None
    event = cached.get("event")
    if event and results:
        log("正在回写活动歌单链接…")
        try:
            from toolbox.tcdoc import event_playlist

            writeback = event_playlist.write_results(event, results)
            log(event_playlist.summary_text(writeback))
        except Exception as exc:  # noqa: BLE001 - 回写失败不影响歌单已创建
            writeback = {"ok": False, "items": [],
                         "message": f"回写失败：{exc}"}
            log(f"活动歌单回写失败：{exc}")

    report = _write_report({
        "mode": "apply",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": cached.get("source") or "",
        "results": results,
        "writeback": writeback,
    })
    added = sum(len(item.get("added") or []) for item in results)
    stopped = sum(1 for item in results if item.get("stopped"))
    with _LOCK:
        _PLANS.pop(preview_id, None)
    summary = f"已添加 {added} 首"
    if stopped:
        summary += f"，{stopped} 个平台未写入"
    return {"results": results, "report": report, "summary": summary,
            "writeback": writeback}


class _CancelFlag:
    """把 is_cancelled 回调包装成 apply_plan 需要的 is_set() 接口。"""

    def __init__(self, check):
        self._check = check

    def is_set(self) -> bool:
        return bool(self._check())


def _write_report(data: dict) -> str:
    try:
        target = service.report_dir() / (
            f"{data['mode']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        service.write_report(target, data)
        return str(target)
    except OSError:
        return ""