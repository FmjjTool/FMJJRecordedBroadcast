"""证书历史记录（L4 存储）：字段数据及可选附件引用的唯一写入方。

需求：历史生成过的证书要能回填数据，但图片只属于当次作品，不随历史保存。
字段存 `CertificateData.to_dict()`，页面重新打开时不从历史恢复当前图片列表。
可选 evidence v1 只记录当次导出产物的路径、大小、哈希和捕获时间，不复制文件；
导出证据包前必须重新核验。旧记录只读兼容，不做自动迁移；本模块不导出加密包。

存储位置：配置目录下 `certificate_history.json`，按认证号 / 证书编号去重，
最新的排在最前，最多保留 `MAX_RECORDS` 条。
"""

from __future__ import annotations

import copy
import json
import os
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


def _read_payload(path: Path | None = None) -> dict[str, Any]:
    """读取历史文件：`records` 为记录，`retired` 为已保留/已作废的序号。"""
    target = Path(path) if path else history_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"records": [], "retired": []}
    if isinstance(payload, dict):
        records = payload.get("records")
        retired = payload.get("retired")
    else:
        records, retired = payload, []
    records = records if isinstance(records, list) else []
    retired = retired if isinstance(retired, list) else []
    return {
        "records": [record for record in records if isinstance(record, dict)],
        "retired": [item for item in retired if isinstance(item, dict)],
    }


def _read(path: Path | None = None) -> list[dict[str, Any]]:
    return _read_payload(path)["records"]


def _write_payload(payload: dict[str, Any], path: Path | None = None) -> None:
    target = Path(path) if path else history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.stem}.tmp.json")
    body = {
        "records": list(payload.get("records") or [])[:MAX_RECORDS],
        "retired": list(payload.get("retired") or []),
    }
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2, ensure_ascii=False)
    temp.replace(target)


def _write(records: list[dict[str, Any]], path: Path | None = None) -> None:
    # 记录增删改都要保留序号作废/保留清单
    retired = _read_payload(path).get("retired") or []
    _write_payload({"records": records, "retired": retired}, path)


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


def save_record(data: engine.CertificateData, path: Path | None = None, *,
                evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """保存字段及可选 evidence v1；同字段合并引用，字段变化不沿用旧附件。"""
    if evidence is not None and (not isinstance(evidence, dict)
            or evidence.get("version") != 1 or not isinstance(evidence.get("files"), list)):
        raise ValueError("证书附件引用格式无效")
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
            try:
                previous_revision = max(1, int(record.get("revision") or 1))
            except (TypeError, ValueError, OverflowError):
                previous_revision = 1
            unchanged = record.get("data") == payload["data"]
            previous = record.get("evidence") if unchanged else None
            if (not isinstance(previous, dict) or previous.get("version") != 1
                    or previous.get("revision") != previous_revision
                    or not isinstance(previous.get("files"), list)):
                previous = None
            record.update(payload)
            record["id"] = str(record.get("id") or uuid.uuid4().hex[:12])
            record["revision"] = previous_revision + 1
            if evidence is not None:
                merged = copy.deepcopy(evidence)
                refs = {}
                for ref in ((previous or {}).get("files") or []) + merged["files"]:
                    if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                        refs[os.path.normcase(os.path.normpath(ref["path"]))] = copy.deepcopy(ref)
                merged["files"] = list(refs.values())
                merged["revision"] = record["revision"]
                record["evidence"] = merged
                record.pop("evidence_previous_revision", None)
            elif unchanged and isinstance(previous, dict):
                record["evidence"] = copy.deepcopy(previous)
                record["evidence"]["revision"] = record["revision"]
            else:
                if record.pop("evidence", None) is not None:
                    record["evidence_previous_revision"] = previous_revision
            _write(records, path)
            return record
    payload["id"] = uuid.uuid4().hex[:12]
    payload["revision"] = 1
    if evidence is not None:
        payload["evidence"] = copy.deepcopy(evidence)
        payload["evidence"]["revision"] = 1
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


def load_retired(path: Path | None = None) -> list[dict[str, Any]]:
    """已保留 / 已作废的序号清单。"""
    return _read_payload(path).get("retired") or []


def retired_codes(path: Path | None = None) -> list[str]:
    """把保留/作废序号转成编号主干，供自动编号顺延时跳过。"""
    codes: list[str] = []
    for item in load_retired(path):
        try:
            year, serial = int(item.get("year")), int(item.get("serial"))
        except (TypeError, ValueError):
            continue
        if year and serial:
            codes.append(engine.archive_base(year, serial))
    return codes


def retired_serials(year: int, path: Path | None = None) -> set[int]:
    """某年份已保留 / 已作废的序号（不再出现在可选列表里）。"""
    serials: set[int] = set()
    for item in load_retired(path):
        try:
            if int(item.get("year")) == int(year):
                serials.add(int(item.get("serial")))
        except (TypeError, ValueError):
            continue
    return serials


def retire_serial(year: int, serial: int, *, voided: bool,
                  note: str = "", path: Path | None = None) -> dict[str, Any]:
    """删除历史记录后保留 / 作废序号：之后自动编号不会再发到它。

    `voided=True` 表示彻底作废（人工标记不再使用）；`False` 表示只保留占用。
    """
    payload = _read_payload(path)
    key = (int(year), int(serial))
    retired = [
        item for item in payload["retired"]
        if (int(item.get("year") or 0), int(item.get("serial") or 0)) != key
    ]
    entry = {
        "year": key[0],
        "serial": key[1],
        "voided": bool(voided),
        "note": str(note or ""),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    retired.append(entry)
    retired.sort(key=lambda item: (int(item.get("year") or 0), int(item.get("serial") or 0)))
    payload["retired"] = retired
    _write_payload(payload, path)
    return entry
