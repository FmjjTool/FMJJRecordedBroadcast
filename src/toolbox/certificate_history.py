"""证书历史记录（L4 存储）：只保留字段数据，不保存图片。

需求：历史生成过的证书要能回填数据，但图片只属于当次作品，不随历史保存。
因此每条记录只存 `CertificateData.to_dict()`，页面重新打开时图片列表为空。

存储位置：配置目录下 `certificate_history.json`，按认证号 / 证书编号去重，
最新的排在最前，最多保留 `MAX_RECORDS` 条。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from toolbox import certificate_engine as engine
from toolbox.config_manager import get_config_dir

FILE_NAME = "certificate_history.json"
MAX_RECORDS = 300


def history_path() -> Path:
    return get_config_dir() / FILE_NAME


def _read(path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path else history_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        records = payload.get("records")
    else:
        records = payload
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _write(records: list[dict[str, Any]], path: Path | None = None) -> None:
    target = Path(path) if path else history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.stem}.tmp.json")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump({"records": records[:MAX_RECORDS]}, handle, indent=2, ensure_ascii=False)
    temp.replace(target)


def load_records(path: Path | None = None) -> list[dict[str, Any]]:
    """按保存时间倒序返回历史记录。"""
    records = _read(path)
    records.sort(key=lambda record: str(record.get("saved_at") or ""), reverse=True)
    return records


def _same_certificate(record: dict[str, Any], data: engine.CertificateData) -> bool:
    archive_no = str(data.archive_no or "").strip()
    archive_no = str(data.archive_no or "").strip()
    if archive_no and str(record.get("archive_no") or "").strip() == archive_no:
        return True
    return bool(archive_no) and str(record.get("archive_no") or "").strip() == archive_no


def save_record(data: engine.CertificateData, path: Path | None = None) -> dict[str, Any]:
    """新增或更新（同认证号视为同一张证书）一条记录，返回该记录。"""
    records = _read(path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "archive_no": data.archive_no,

        "title": data.cert_title_text(),
        "art_type": data.effective_type(),
        "author_name": data.cert_author_text(),
        "status": data.status,
        "saved_at": now,
        "data": data.to_dict(),
    }
    for record in records:
        if _same_certificate(record, data):
            record.update(payload)
            record["id"] = str(record.get("id") or uuid.uuid4().hex[:12])
            record["revision"] = int(record.get("revision") or 1) + 1
            _write(records, path)
            return record
    payload["id"] = uuid.uuid4().hex[:12]
    payload["revision"] = 1
    records.insert(0, payload)
    _write(records, path)
    return payload


def delete_record(record_id: str, path: Path | None = None) -> bool:
    records = _read(path)
    remaining = [record for record in records if str(record.get("id")) != record_id]
    if len(remaining) == len(records):
        return False
    _write(remaining, path)
    return True


def clear_records(path: Path | None = None) -> None:
    _write([], path)