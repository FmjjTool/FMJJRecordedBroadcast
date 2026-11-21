"""推送历史：按表记录程序每次向腾讯文档写入的结果。

每次真实写入（批量上传 / 右键单行 / 直播单条试写 / 资产页单条上传）落一行
JSONL，包含时间、目标表、来源、新增/跳过/图片/失败数量、recordID 与备注文本；
界面「推送历史」按表汇总展示。文件跟随全局导出目录：

  download/腾讯文档数据/推送历史.jsonl

历史只追加、不删除已有记录；写入历史失败绝不影响正常上传。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as tc_cfg

HISTORY_NAME = "推送历史.jsonl"
BEIJING = timezone(timedelta(hours=8))
MAX_RECORD_IDS = 50
_TEXT_LIMIT = 300


def history_path() -> Path:
    return tc_cfg.data_dir() / HISTORY_NAME


def _now() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def record(*, tab_name: str = "", tab_id: str = "", doc_key: str = "",
           profile: str = "", source: str = "上传", status: str = "成功",
           added: int = 0, skipped: int = 0, images: int = 0, failed: int = 0,
           record_ids=(), remark: str = "", note: str = "") -> dict | None:
    """追加一条推送记录；返回写入的条目（失败时返回 None，不抛异常）。"""
    entry = {
        "time": _now(),
        "doc_key": str(doc_key or ""),
        "tab_name": str(tab_name or ""),
        "tab_id": str(tab_id or ""),
        "profile": str(profile or ""),
        "source": str(source or "上传"),
        "status": str(status or "成功"),
        "added": max(0, int(added or 0)),
        "skipped": max(0, int(skipped or 0)),
        "images": max(0, int(images or 0)),
        "failed": max(0, int(failed or 0)),
        "record_ids": [str(rid) for rid in list(record_ids or [])[:MAX_RECORD_IDS]],
        "remark": str(remark or "")[:_TEXT_LIMIT],
        "note": str(note or "")[:_TEXT_LIMIT],
    }
    try:
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - 历史失败不能打断上传
        return None
    return entry


def load(limit: int = 300) -> list[dict]:
    """读取历史（新的在前）；坏行跳过，不抛异常。"""
    try:
        path = history_path()
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:  # noqa: BLE001 - 没有历史/文件被占用 → 空列表
        return []
    entries = []
    for line in lines[-max(1, int(limit or 300)):]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("time"):
            entries.append(item)
    entries.reverse()
    return entries


def summary(entries: list[dict] | None = None) -> list[dict]:
    """按目标表汇总：推送批次、累计新增、最近一次时间（按最近推送排序）。"""
    entries = load() if entries is None else entries
    buckets: dict[tuple, dict] = {}
    for item in entries:
        key = (str(item.get("doc_key") or ""), str(item.get("tab_id") or ""),
               str(item.get("tab_name") or ""))
        bucket = buckets.setdefault(key, {
            "doc_key": key[0], "tab_id": key[1], "tab_name": key[2] or key[1],
            "batches": 0, "added": 0, "skipped": 0, "images": 0, "failed": 0,
            "last_time": "",
        })
        bucket["batches"] += 1
        bucket["added"] += int(item.get("added") or 0)
        bucket["skipped"] += int(item.get("skipped") or 0)
        bucket["images"] += int(item.get("images") or 0)
        if str(item.get("status") or "") != "成功":
            bucket["failed"] += 1
        bucket["last_time"] = max(bucket["last_time"], str(item.get("time") or ""))
    return sorted(buckets.values(), key=lambda item: item["last_time"], reverse=True)