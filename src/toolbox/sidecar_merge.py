# -*- coding: utf-8 -*-
"""同一天多场录制的弹幕 / 时间轴合并（L4 纯逻辑，不依赖 Qt）。

- 弹幕：读取 `_弹幕.jsonl`，按 timestamp 排序，按
  (timestamp, method, 用户, 内容, 礼物) 去重后写成一个新的 JSONL。
- 时间轴：读取 `_时间轴.json`，按首锚点墙钟排序；目标视频是这些录制按顺序
  拼接时，用每段时长累计出偏移，把各段锚点换算到目标视频 PTS，写成一条
  指向目标视频的记录，现有弹幕对齐 / 写时间码逻辑无需改动。
- 只新建或原子替换目标文件；目标已存在时先复制 `.bak-时间` 备份，不删除
  任何原始弹幕 / 时间轴文件。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

DANMAKU_SUFFIX = "_弹幕.jsonl"
TIMELINE_SUFFIX = "_时间轴.json"


def list_danmaku_files(folder) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.is_file() and path.name.endswith(DANMAKU_SUFFIX))


def list_timeline_files(folder) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.is_file() and path.name.endswith(TIMELINE_SUFFIX))


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def backup_existing(path) -> str:
    """覆盖前备份目标文件；返回备份路径，没有旧文件返回空串。"""
    path = Path(path)
    if not path.is_file():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    number = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{stamp}-{number}")
        number += 1
    shutil.copy2(path, backup)
    return str(backup)


def _timestamp(message):
    value = message.get("timestamp") or message.get("ts") or 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _event_key(message):
    user = message.get("user") or {}
    gift = message.get("gift") or {}
    return (
        _timestamp(message),
        str(message.get("method") or ""),
        str(user.get("name") or "") if isinstance(user, dict) else "",
        str(message.get("content") or message.get("text") or ""),
        str(gift.get("name") or "") if isinstance(gift, dict) else "",
        str(gift.get("count") or "") if isinstance(gift, dict) else "",
    )


def merge_danmaku(paths, out_path) -> dict:
    """多个 `_弹幕.jsonl` → 一个按时间排序、去重的新 JSONL。"""
    paths = [Path(path) for path in paths]
    if not paths:
        raise ValueError("没有选择弹幕文件")
    messages, invalid = [], 0
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except ValueError:
                        invalid += 1
                        continue
                    if isinstance(message, dict):
                        messages.append(message)
                    else:
                        invalid += 1
        except OSError as error:
            raise ValueError(f"读取失败：{path.name}（{error}）") from error
    if not messages:
        raise ValueError("没有可合并的弹幕")
    messages.sort(key=_timestamp)
    seen = set()
    merged = []
    for message in messages:
        key = _event_key(message)
        if key in seen:
            continue
        seen.add(key)
        merged.append(message)
    out = Path(out_path)
    backup = backup_existing(out)
    text = "\n".join(json.dumps(message, ensure_ascii=False, separators=(",", ":"))
                     for message in merged) + "\n"
    _atomic_write(out, text)
    return {"out": str(out), "files": len(paths), "total": len(messages),
            "merged": len(merged), "duplicates": len(messages) - len(merged),
            "invalid": invalid, "backup": backup}


def _probe_duration(path) -> float:
    from .timecode import probe
    return float(probe(str(path)).get("duration") or 0)


def _anchors(entry):
    anchors = []
    for item in entry.get("anchors") or []:
        if not isinstance(item, dict):
            continue
        try:
            anchors.append({"pts_ms": int(item.get("pts_ms") or 0),
                            "wall_ms": int(item.get("wall_ms") or 0)})
        except (TypeError, ValueError):
            continue
    return sorted(anchors, key=lambda item: (item["pts_ms"], item["wall_ms"]))


def _first_wall(entry):
    anchors = _anchors(entry)
    return min((item["wall_ms"] for item in anchors), default=None)


def merge_timelines(paths, out_path, target_video, duration_of=None) -> dict:
    """多个 `_时间轴.json` → 目标视频的一条时间轴（锚点按拼接顺序偏移）。

    目标视频应是这些录制按首锚点时间顺序拼接的结果；源视频已删除时，
    用相邻场次的首锚点间隔估算上一段时长，最后一段用目标视频时长兜底。
    """
    paths = [Path(path) for path in paths]
    target = Path(target_video)
    if not paths:
        raise ValueError("没有选择时间轴文件")
    if not target.is_file():
        raise ValueError("目标视频不存在")
    duration_of = duration_of or _probe_duration

    loaded = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        entries = [entry for entry in data.get("files") or []
                   if isinstance(entry, dict) and _first_wall(entry) is not None]
        if entries:
            loaded.append((path, data, entries))
    if not loaded:
        raise ValueError("没有可合并的时间轴")

    flat, seen = [], set()
    for path, data, entries in loaded:
        for entry in entries:
            anchors = _anchors(entry)
            key = (os.path.normcase(os.path.abspath(str(entry.get("file") or ""))),
                   tuple((item["pts_ms"], item["wall_ms"]) for item in anchors))
            if key in seen:
                continue
            seen.add(key)
            flat.append({"path": path, "data": data, "entry": entry,
                         "anchors": anchors, "wall": _first_wall(entry)})
    flat.sort(key=lambda item: (item["wall"], str(item["entry"].get("file") or "")))

    target_duration = 0.0
    try:
        target_duration = max(0.0, float(duration_of(str(target)) or 0))
    except Exception:  # noqa: BLE001 探测失败只影响最后一段的估算
        target_duration = 0.0

    offsets, estimated = [], 0
    cumulative = 0
    for index, item in enumerate(flat):
        duration = 0.0
        source = str(item["entry"].get("file") or "")
        if source:
            try:
                if os.path.isfile(source):
                    duration = max(0.0, float(duration_of(source) or 0))
            except Exception:  # noqa: BLE001
                duration = 0.0
        if duration <= 0:
            estimated += 1
            if index + 1 < len(flat):
                duration = max(0.0, (flat[index + 1]["wall"] - item["wall"]) / 1000.0)
            elif target_duration:
                duration = max(0.0, target_duration - cumulative)
        offsets.append(cumulative)
        cumulative += int(round(duration * 1000))

    merged_anchors, anchor_seen = [], set()
    for item, offset in zip(flat, offsets):
        for anchor in item["anchors"]:
            key = (anchor["pts_ms"] + offset, anchor["wall_ms"])
            if key in anchor_seen:
                continue
            anchor_seen.add(key)
            merged_anchors.append({"pts_ms": key[0], "wall_ms": key[1]})
    merged_anchors.sort(key=lambda item: (item["pts_ms"], item["wall_ms"]))
    if not merged_anchors:
        raise ValueError("时间轴里没有有效锚点")

    first = loaded[0][1]
    target_abs = os.path.abspath(str(target))
    data = {
        "version": 2,
        "platform": first.get("platform") or "",
        "room": str(first.get("room") or ""),
        "base": target.stem,
        "created": int(first.get("created") or time.time() * 1000),
        "target": target_abs,
        "merged": True,
        "sources": [path.name for path, _data, _entries in loaded],
        "files": [{"file": target_abs, "anchors": merged_anchors}],
    }
    out = Path(out_path)
    backup = backup_existing(out)
    _atomic_write(out, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return {"out": str(out), "files": len(paths), "entries": len(flat),
            "anchors": len(merged_anchors), "offsets": offsets,
            "estimated": estimated, "duration_ms": cumulative,
            "target_duration_ms": int(round(target_duration * 1000)),
            "backup": backup}
