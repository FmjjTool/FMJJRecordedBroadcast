"""性能自动报告存储（L5，唯一写入方，存储 schema 1）。

主数据是进程内诊断 recorder；本模块只保存可重建的 JSON 诊断日志，不读取业务
数据、配置内容或登录态。桌面自动保存是唯一 writer，桌面/Web 通过本模块读取。
每个会话一个文件，周期与最终保存覆盖同一 ID；保留最近 20 份本模块的报告，
不处理手动导出、无本模块标记的文件或其它日志。读取不创建目录，也不迁移旧数据。

报告采用合法 JSON：首行是有界 autosave 摘要，其余行是 recorder DTO，列表无需
载入全部采样。调用者必须只传递已脱敏的 recorder 报告，不传异常正文或业务参数。
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


STORAGE_SCHEMA_VERSION = 1
RETENTION_LIMIT = 20
MAX_REPORT_BYTES = 32 * 1024 * 1024
_HEADER_BYTES = 8192
_TIMEZONE = timezone(timedelta(hours=8))
_ID_RE = re.compile(r"performance_\d{8}_\d{6}_\d{1,10}_[0-9a-f]{12}\Z")
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_STATUSES = {"running", "stopped", "normal_exit", "startup_failure", "uncaught_exception", "process_exit"}
_WRITE_LOCK = threading.RLock()


def new_report_id() -> str:
    """返回不带 .json 的会话 ID；路径和扩展名不由调用方指定。"""
    stamp = datetime.now(_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return f"performance_{stamp}_{os.getpid()}_{uuid4().hex[:12]}"


def _reports_dir() -> Path:
    # app_logger 的导入与 LOG_DIR 求值无 mkdir/日志初始化副作用。
    from app_logger import LOG_DIR

    return Path(LOG_DIR) / "performance"


def _validate_id(report_id: str) -> str:
    if not isinstance(report_id, str) or not _ID_RE.fullmatch(report_id):
        raise ValueError("报告标识无效")
    return report_id


def _reject_links(path: Path) -> None:
    """拒绝符号链接、Windows junction/reparse point，不跟随到其它目录。"""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
            raise ValueError("报告路径不安全")


def _target(report_id: str, *, create: bool = False) -> Path:
    report_id = _validate_id(report_id)
    folder = _reports_dir()
    _reject_links(folder)
    if create:
        folder.mkdir(parents=True, exist_ok=True)
        _reject_links(folder)
    target = folder / f"{report_id}.json"
    _reject_links(target)
    if target.resolve().parent != folder.resolve():
        raise ValueError("报告路径不安全")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("报告文件不安全")
    return target


def _time_text(value) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TIMEZONE)
    return parsed.isoformat(timespec="milliseconds")


def _status(report: dict, reason: str) -> str:
    status = report.get("status")
    if isinstance(status, str) and status in _STATUSES:
        return status
    if reason in {"startup_failure", "uncaught_exception"}:
        return reason
    if report.get("active") is True:
        return "running"
    return reason if reason in {"normal_exit", "process_exit"} else "stopped"


def _metadata(report_id: str, report: dict, reason: str) -> dict:
    startup = report.get("startup")
    return {
        "storage_schema_version": STORAGE_SCHEMA_VERSION,
        "id": report_id,
        "saved_at": datetime.now(_TIMEZONE).isoformat(timespec="milliseconds"),
        "reason": reason,
        "started_at": _time_text(report.get("started_at")),
        "stopped_at": _time_text(report.get("stopped_at")),
        "active": report.get("active") is True,
        "status": _status(report, reason),
        "samples": len(report.get("samples", [])),
        "events": len(report.get("events", [])),
        "startup_complete": isinstance(startup, dict) and startup.get("complete") is True,
    }


def _encode(report_id: str, report: dict, reason: str) -> tuple[bytes, dict]:
    if not isinstance(report, dict):
        raise ValueError("报告内容无效")
    if not isinstance(report.get("samples", []), list) or not isinstance(report.get("events", []), list):
        raise ValueError("报告记录格式无效")
    if not isinstance(reason, str) or not _REASON_RE.fullmatch(reason):
        raise ValueError("报告保存原因无效")
    metadata = _metadata(report_id, report, reason)
    # 不修改原 report 或其原 autosave 字段；序列化立即取得稳定副本。
    body = {key: value for key, value in report.items() if key != "autosave"}
    try:
        header = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ValueError("报告内容无法保存") from None
    payload = b'{"autosave":' + header + b',\n' + content[1:]
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError("报告超过大小上限")
    return payload, metadata


def _open_checked(report_id: str):
    target = _target(report_id)
    before = target.lstat()
    if before.st_size > MAX_REPORT_BYTES:
        raise ValueError("报告超过大小上限")
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        after = os.fstat(descriptor)
        if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise ValueError("报告文件不安全")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _validate_metadata(value, report_id: str) -> dict:
    if (not isinstance(value, dict) or type(value.get("storage_schema_version")) is not int
            or value.get("storage_schema_version") != STORAGE_SCHEMA_VERSION
            or value.get("id") != report_id or not isinstance(value.get("status"), str)
            or value.get("status") not in _STATUSES
            or _time_text(value.get("saved_at")) is None
            or type(value.get("active")) is not bool or type(value.get("startup_complete")) is not bool
            or any(type(value.get(key)) is not int or value[key] < 0 for key in ("samples", "events"))):
        raise ValueError("报告格式无效")
    return value


def _read_summary(report_id: str) -> tuple[dict, int, int]:
    with _open_checked(report_id) as stream:
        info = os.fstat(stream.fileno())
        line = stream.readline(_HEADER_BYTES + 1)
    if len(line) > _HEADER_BYTES or not line.endswith(b",\n"):
        raise ValueError("报告摘要格式无效")
    try:
        header = json.loads(line[:-2] + b"}")
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("报告摘要格式无效") from None
    meta = _validate_metadata(header.get("autosave") if isinstance(header, dict) else None, report_id)
    return dict(meta), info.st_size, info.st_mtime_ns


def _entries() -> list[tuple[str, dict, int, int]]:
    folder = _reports_dir()
    _reject_links(folder)
    if not folder.exists():
        return []
    if not folder.is_dir():
        raise ValueError("报告目录不可用")
    rows = []
    for path in folder.iterdir():
        if path.suffix != ".json" or not _ID_RE.fullmatch(path.stem):
            continue
        try:
            meta, size, modified = _read_summary(path.stem)
        except (OSError, ValueError):
            # 损坏/不安全/非本模块报告留给人工排查，不读取正文，也不自动删除。
            continue
        rows.append((path.stem, meta, size, modified))
    return sorted(rows, key=lambda row: (row[3], row[0]), reverse=True)


def _prune_locked(current_id: str) -> tuple[int, bool]:
    entries = _entries()
    keep = {current_id}
    # 总数最多 20；当前保存的会话必留，避免文件系统时间精度影响本次报告。
    for report_id, _meta, _size, _modified in entries:
        if len(keep) >= RETENTION_LIMIT:
            break
        keep.add(report_id)
    removed, warning = 0, False
    for report_id, _meta, _size, _modified in entries:
        if report_id in keep:
            continue
        try:
            _target(report_id).unlink()
            removed += 1
        except (OSError, ValueError):
            warning = True
    return removed, warning


def save_report(report_id: str, report: dict, reason: str = "periodic") -> dict:
    """原子保存同一会话；替换失败时保留上一份完整文件，异常不带真实路径。"""
    report_id = _validate_id(report_id)
    with _WRITE_LOCK:
        payload, meta = _encode(report_id, report, reason)
        temporary = None
        try:
            target = _target(report_id, create=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{report_id}.", suffix=".tmp",
                                             dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                _reject_links(temporary)
                if temporary.resolve().parent != target.parent.resolve():
                    raise ValueError("报告路径不安全")
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            target = _target(report_id)
            os.replace(temporary, target)
            temporary = None
        except OSError:
            raise OSError("自动报告保存失败") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            removed, warning = _prune_locked(report_id)
        except (OSError, ValueError):
            removed, warning = 0, True
        return {"id": report_id, "filename": f"{report_id}.json", "saved_at": meta["saved_at"],
                "size": len(payload), "retention_removed": removed, "retention_warning": warning}


def list_reports(limit: int = 30) -> dict:
    """返回按最后保存时间倒序的安全摘要；无历史时不创建任何文件或目录。"""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("报告数量范围无效")
    try:
        with _WRITE_LOCK:
            entries = _entries()[:limit]
    except OSError:
        raise OSError("读取自动报告列表失败") from None
    rows = [{"id": report_id, "filename": f"{report_id}.json", "started_at": meta.get("started_at"),
             "saved_at": meta["saved_at"], "active": meta.get("active") is True,
             "status": meta["status"], "samples": meta.get("samples", 0), "events": meta.get("events", 0),
             "startup_complete": meta.get("startup_complete") is True, "size": size}
            for report_id, meta, size, _modified in entries]
    return {"reports": rows, "directory_label": "logs/performance", "retention_limit": RETENTION_LIMIT}


def load_report(report_id: str) -> dict:
    """受控读取完整报告；不接受文件路径或扩展名，不回显磁盘位置。"""
    return _decode_report(_read_bytes(report_id), report_id)


def _reject_constant(_value):
    raise ValueError("报告内容无效")


def _decode_report(raw: bytes, report_id: str) -> dict:
    try:
        report = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("报告内容无效") from None
    if not isinstance(report, dict):
        raise ValueError("报告内容无效")
    _validate_metadata(report.get("autosave"), report_id)
    return report


def _read_bytes(report_id: str) -> bytes:
    report_id = _validate_id(report_id)
    try:
        with _WRITE_LOCK, _open_checked(report_id) as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)
    except FileNotFoundError:
        raise FileNotFoundError("自动报告不存在") from None
    except OSError:
        raise OSError("读取自动报告失败") from None
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("报告超过大小上限")
    # 附件也校验元数据，避免把同名的非本模块文件直接输出。
    first = raw.split(b"\n", 1)[0]
    try:
        header = json.loads(first[:-1] + b"}") if first.endswith(b",") else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        header = None
    _validate_metadata(header.get("autosave") if isinstance(header, dict) else None, report_id)
    return raw


def read_report_bytes(report_id: str) -> bytes:
    """读取大小受限且格式有效的 JSON 附件，供上层统一响应工具下载。"""
    raw = _read_bytes(report_id)
    _decode_report(raw, report_id)
    return raw
