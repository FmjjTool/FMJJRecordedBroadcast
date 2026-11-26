"""Certificate evidence export use cases (Qt-free).

History remains owned by certificate_history. This module only captures file
metadata and reads one selected record; exporting never changes history/source
files. Local refs may contain absolute paths, but portable records/manifests do
not. Exports are standard WinZip AES-256 ZIPs; every entry is encrypted and no
password is persisted. There is no plaintext fallback or credential-key reuse.

Archive schema v1: record.json, manifest.json, 说明.txt, files/0001.ext.
At most 128 entries (125 attachments), 64 MiB of plaintext, 1 MiB per JSON.
Refs are not retained copies: files must still match their captured hash.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import io
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone

from . import certificate_engine as engine
from . import certificate_history as history

MAX_FILES = 128
MAX_ATTACHMENTS = MAX_FILES - 3
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
CHUNK_SIZE = 64 * 1024
ALLOWED_SUFFIXES = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif",
    ".tiff", ".docx", ".xlsx", ".txt", ".md",
})
_PRIVATE_DIRS = frozenset({
    "config", "logs", "profiles", "profile", "user data", "local storage",
    "session storage", "__pycache__",
})
_PRIVATE_NAME = re.compile(
    r"cookie|token|credential|secret|password|passwd|storage[_ -]?state|"
    r"session|(?:^|[_. -])auth(?:[_. -]|$)|密码|凭据|登录态|登录快照|令牌|密钥", re.I)
_HASH = re.compile(r"^[a-fA-F0-9]{64}$")
_TZ = timezone(timedelta(hours=8))


class EvidenceError(ValueError):
    """A safe, user-facing evidence validation/export error."""


class EvidenceCancelled(EvidenceError):
    """The caller cancelled before publishing an archive."""


class EvidenceLimitError(EvidenceError):
    """Never silently drop files merely to fit an export limit."""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def configured_output_root() -> Path:
    """Read the existing configuration owner; never change saved preferences."""
    from . import config_manager

    payload = config_manager._read_json(config_manager._cfg_path())
    section = payload.get("certificate_maker") or {}
    configured = section.get("out_dir") if isinstance(section, dict) else None
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser()
    return Path(config_manager.get_export_dir()) / "证书"


def _dependency():
    try:
        import pyzipper
    except (ImportError, OSError):
        raise EvidenceError("加密导出组件不可用，请在全局设置中检查依赖后重试") from None
    return pyzipper


def _cancel(check):
    if check():
        raise EvidenceCancelled("已取消证据导出")


def _path(raw) -> Path:
    try:
        value = os.fspath(raw)
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError
        candidate = Path(value)
        if ".." in candidate.parts:
            raise ValueError
        return Path(os.path.abspath(candidate))
    except (TypeError, ValueError, OSError):
        raise EvidenceError("附件路径无效") from None


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400)


def _components(path: Path, *, private: bool = True):
    """Check every ancestor with lstat; do not resolve through links/junctions."""
    for part in reversed((path, *path.parents)):
        if private and part.name and (
                part.name.startswith(".") or part.name.casefold() in _PRIVATE_DIRS
                or _PRIVATE_NAME.search(part.name)):
            raise EvidenceError("隐藏文件或登录资料不能作为证据附件")
        try:
            info = part.lstat()
        except OSError:
            raise EvidenceError("附件不存在或无法读取") from None
        if _linked(info):
            raise EvidenceError("证据附件不支持符号链接或目录联接")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise EvidenceError("附件路径包含非目录项")
    return info  # the final iteration is path


def _checked_file(raw):
    path = _path(raw)
    info = _components(path)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) > 1:
        raise EvidenceError("证据附件必须是普通文件，不能是链接或特殊文件")
    if getattr(info, "st_file_attributes", 0) & (0x2 | 0x4):
        raise EvidenceError("隐藏文件或系统文件不能作为证据附件")
    if path.suffix.casefold() not in ALLOWED_SUFFIXES:
        raise EvidenceError("不支持此附件类型，请选择文档或图片")
    if info.st_size > MAX_TOTAL_BYTES:
        raise EvidenceLimitError("证据附件总大小不能超过 64 MiB")
    return path, info


def _state(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read_file(raw, *, expected=None, consume=None,
               is_cancelled=lambda: False, budget=MAX_TOTAL_BYTES):
    path, before = _checked_file(raw)
    if before.st_size > budget:
        raise EvidenceLimitError("证据附件总大小不能超过 64 MiB")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            # Recheck before the first read as well as after the last read.
            _, opened_path = _checked_file(path)
            opened = os.fstat(handle.fileno())
            if _state(before) != _state(opened) or _state(before) != _state(opened_path):
                raise EvidenceError("附件在读取期间发生变化，请重试")
            while True:
                _cancel(is_cancelled)
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > budget or size > before.st_size:
                    raise EvidenceError("附件在读取期间发生变化或超过大小上限")
                digest.update(chunk)
                if consume is not None:
                    consume(chunk)
            _, after_path = _checked_file(path)
            if (_state(before) != _state(os.fstat(handle.fileno()))
                    or _state(before) != _state(after_path) or size != before.st_size):
                raise EvidenceError("附件在读取期间发生变化，请重试")
    except OSError:
        raise EvidenceError("附件无法读取或在读取期间发生变化") from None
    checksum = digest.hexdigest()
    if expected is not None and (
            size != expected["size"] or not hmac.compare_digest(checksum, expected["sha256"])):
        raise EvidenceError("附件内容已变更，已停止导出")
    return {"path": str(path), "name": path.name, "size": size, "sha256": checksum}


def capture_evidence(paths) -> dict:
    """Hash explicitly supplied outputs; return private refs, without copying them."""
    if isinstance(paths, (str, bytes, Path)):
        raise EvidenceError("请提供附件文件列表")
    captured = _now()
    files, seen, total = [], set(), 0
    for raw in paths:
        path = _path(raw)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        if len(files) >= MAX_ATTACHMENTS:
            raise EvidenceLimitError("每个证据包最多包含 125 个附件")
        item = _read_file(path, budget=MAX_TOTAL_BYTES - total)
        item["captured_at"] = captured
        files.append(item)
        seen.add(key)
        total += item["size"]
    return {"version": 1, "captured_at": captured, "files": files}


def _revision(record) -> int:
    try:
        return max(1, int(record.get("revision") or 1))
    except (TypeError, ValueError, OverflowError):
        return 1


def _filename(record) -> str:
    key = hashlib.sha256(str(record.get("id") or "record").encode("utf-8")).hexdigest()[:12]
    return f"certificate-evidence-{key}-r{_revision(record)}.zip"


def _select(record_id, history_path):
    if not isinstance(record_id, str) or not record_id or len(record_id) > 256:
        raise EvidenceError("请先选择一条证书记录")
    matches = [record for record in history.load_records(history_path)
               if str(record.get("id") or "") == record_id]
    if len(matches) != 1:
        raise EvidenceError("所选证书记录不存在或标识重复，请刷新后重试")
    return copy.deepcopy(matches[0])


def _discovered(record, output_root):
    root = _path(output_root)
    try:
        if not stat.S_ISDIR(_components(root).st_mode):
            return []
    except EvidenceError:
        return []
    data = engine.CertificateData.from_dict(record.get("data") or {})
    folder, stem = engine.certificate_package(data, root)
    if folder.parent != root:
        raise EvidenceError("证书目录超出允许范围")
    candidates = [folder / (stem + ".pdf"), folder / (stem + ".docx")]
    candidates.extend(folder / f"{stem}_{i:02d}.png" for i in (1, 2))
    water_stem = "_".join(part for part in (
        engine.image_render.safe_filename_part(data.archive_no, 40),
        engine.image_render.safe_filename_part(data.title, 30),
    ) if part) or "证书作品"
    candidates.extend(folder / "水印件" / f"{water_stem}_已加水印_{i:02d}.png"
                      for i in range(1, engine.MAX_IMAGES + 1))
    return [path for path in candidates if os.path.lexists(path)]


def plan_export(record_id, *, output_root=None, extra_files=(), history_path=None) -> dict:
    """Return a PRIVATE plan with source paths. Never expose it as a Web DTO."""
    record = _select(record_id, history_path)
    files, seen, warnings, total = [], set(), [], 0
    rejected_refs = set()
    failed = {"missing": 0, "changed": 0, "unsafe": 0}

    def add(raw, source, expected=None, captured_at=""):
        nonlocal total
        path = _path(raw)
        key = os.path.normcase(str(path))
        if key in seen:
            return
        if len(files) >= MAX_ATTACHMENTS:
            raise EvidenceLimitError("每个证据包最多包含 125 个附件")
        item = _read_file(path, expected=expected, budget=MAX_TOTAL_BYTES - total)
        item.update(source=source, captured_at=str(captured_at or ""))
        files.append(item)
        seen.add(key)
        total += item["size"]

    evidence = record.get("evidence")
    if isinstance(evidence, dict) and evidence.get("version") == 1:
        refs = evidence.get("files") or []
        if not isinstance(refs, list):
            refs = []
        if evidence.get("revision") != _revision(record):
            warnings.append("历史附件未绑定当前字段版本，未纳入留存证据。")
            refs = []
        for ref in refs:
            if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                    or not isinstance(ref.get("size"), int) or isinstance(ref.get("size"), bool)
                    or ref["size"] < 0 or not _HASH.fullmatch(str(ref.get("sha256") or ""))):
                failed["unsafe"] += 1
                continue
            try:
                path, info = _checked_file(ref["path"])
                if info.st_size != ref["size"]:
                    failed["changed"] += 1
                    rejected_refs.add(os.path.normcase(str(path)))
                    continue
                add(path, "历史留存引用（本次已校验）", expected={
                    "size": ref["size"], "sha256": ref["sha256"].lower()},
                    captured_at=ref.get("captured_at") or evidence.get("captured_at"))
            except EvidenceLimitError:
                raise
            except EvidenceError as exc:
                rejected_refs.add(os.path.normcase(str(_path(ref["path"]))))
                message = str(exc)
                failed["changed" if "变更" in message or "变化" in message else
                       "missing" if "不存在" in message or "无法读取" in message else "unsafe"] += 1
    else:
        warnings.append("此记录未留存当时的附件引用与校验信息，证据材料不完整。")
        if output_root is not None:
            discovered = 0
            for path in _discovered(record, output_root):
                try:
                    add(path, "本次发现，非历史留存")
                    discovered += 1
                except EvidenceLimitError:
                    raise
                except EvidenceError:
                    failed["unsafe"] += 1
            if discovered:
                warnings.append("本次发现的现有产物不是历史留存，无法证明属于当前记录版本。")
    for kind, label in (("missing", "不存在或无法读取"), ("changed", "内容已变更"),
                        ("unsafe", "未通过安全校验")):
        if failed[kind]:
            warnings.append(f"{failed[kind]} 个历史附件{label}，未纳入证据包，材料不完整。")
    if isinstance(extra_files, (str, bytes, Path)):
        raise EvidenceError("请提供补充文件列表")
    supplemented = len(files)
    for path in extra_files:
        if os.path.normcase(str(_path(path))) in rejected_refs:
            raise EvidenceError("补充文件与已变更或无法核验的历史附件重合，不能作为替代材料导出")
        add(path, "本次补充")
    if len(files) > supplemented:
        warnings.append("本次补充的文件并非当时留存的原始证据。")
    if not files:
        warnings.append("没有可用附件，本包仅含历史字段快照，证据材料不完整。")
    return {"record": record, "files": files, "warnings": warnings,
            "filename": _filename(record), "file_count": len(files), "total_size": total}


def list_exports(*, output_root=None, history_path=None) -> dict:
    """List candidate counts WITHOUT hashing all records; export rechecks hashes."""
    try:
        _dependency()
        available = True
    except EvidenceError:
        available = False
    records = []
    for record in history.load_records(history_path):
        warnings, candidates, count, total, seen = [], [], 0, 0, set()
        evidence = record.get("evidence")
        if (isinstance(evidence, dict) and evidence.get("version") == 1
                and evidence.get("revision") == _revision(record)):
            refs = evidence.get("files") or []
            if isinstance(refs, list):
                candidates = [ref for ref in refs if isinstance(ref, dict)]
        else:
            warnings.append("此记录未留存当前版本的附件引用，证据材料不完整。")
            if output_root is not None and not evidence:
                try:
                    candidates = [{"path": str(path)} for path in _discovered(record, output_root)]
                except EvidenceError:
                    candidates = []
                if candidates:
                    warnings.append("现有产物仅为本次发现，非历史留存。")
        for ref in candidates:
            try:
                path, info = _checked_file(ref.get("path"))
                key = os.path.normcase(str(path))
                if key in seen:
                    continue
                if "size" in ref and (info.st_size != ref["size"]
                        or not _HASH.fullmatch(str(ref.get("sha256") or ""))):
                    raise EvidenceError("历史附件已变更或缺少校验信息")
                seen.add(key)
                count += 1
                total += info.st_size
            except EvidenceError:
                if "部分附件不存在、已变更或未通过安全校验，材料不完整。" not in warnings:
                    warnings.append("部分附件不存在、已变更或未通过安全校验，材料不完整。")
        if count:
            warnings.append("附件数量为候选数，导出时将重新校验文件内容。")
        else:
            warnings.append("没有可用附件，可导出历史字段快照。")
        if count > MAX_ATTACHMENTS or total > MAX_TOTAL_BYTES:
            warnings.append("候选附件超过数量或大小上限，当前无法导出。")
        records.append({key: _public_string(str(record.get(key) or "")) for key in
                        ("id", "title", "archive_no", "saved_at")})
        records[-1].update(revision=_revision(record), warnings=warnings,
                           file_count=count, file_count_is_estimate=True, filename=_filename(record))
    return {"records": records, "available": available}


def _public_string(value):
    if isinstance(value, str) and (ntpath.isabs(value) or Path(value).is_absolute()):
        return ntpath.basename(value.replace("/", "\\"))
    return value


def _public_record(record):
    data = engine.CertificateData.from_dict(record.get("data") or {}).to_dict()
    def scrub(value):
        if isinstance(value, dict):
            return {str(key): scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return _public_string(value)
    result = {key: scrub(record.get(key) or "") for key in
              ("id", "archive_no", "title", "art_type", "author_name", "status", "saved_at")}
    result.update(revision=_revision(record), data=scrub(data))
    return result


def _json(value):
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise EvidenceError("证据清单或记录字段超过 1 MiB，无法导出")
    return payload


def _password(value):
    if not isinstance(value, str) or not value or value.isspace() or not 12 <= len(value) <= 128:
        raise EvidenceError("导出口令须为 12–128 个字符，不能全为空白")
    try:
        return value.encode("utf-8")
    except UnicodeError:
        raise EvidenceError("导出口令包含无效字符") from None


def _write_archive(stream, plan, password, is_cancelled, on_progress):
    pyzipper = _dependency()
    _cancel(is_cancelled)
    record_json = _json(_public_record(plan["record"]))
    entries = []
    for i, item in enumerate(plan["files"], 1):
        entries.append({"entry": f"files/{i:04d}{Path(item['path']).suffix.casefold()}",
                        "name": item["name"], "size": item["size"], "sha256": item["sha256"],
                        "source": item["source"], "captured_at": item["captured_at"]})
    manifest_json = _json({"format": "fmjj-certificate-evidence", "version": 1,
                          "created_at": _now(), "encryption": "WinZip AES-256",
                          "record_id": str(plan["record"].get("id") or ""),
                          "revision": _revision(plan["record"]), "files": entries,
                          "warnings": plan["warnings"]})
    readme = ("证书证据包（格式 1）\n使用支持 AES-256 ZIP 的解压工具并输入导出口令。\n"
              "record.json 为所选历史记录的字段快照，manifest.json 含附件来源和 SHA-256。\n"
              "历史留存引用仅表示文件与保存时记录的校验值一致，并非独立保存的原件副本。\n"
              "本次发现/本次补充不代表历史原件；从字段重新生成的文档也不能替代原始协议。\n"
              "\n提示：\n" + "\n".join(plan["warnings"])).encode("utf-8")
    total_size = plan["total_size"] + len(record_json) + len(manifest_json) + len(readme)
    if total_size > MAX_TOTAL_BYTES:
        raise EvidenceError("证据包全部内容不能超过 64 MiB")
    total = len(entries) + 3
    on_progress(0, total)
    with pyzipper.AESZipFile(stream, "w", compression=pyzipper.ZIP_DEFLATED,
                            encryption=pyzipper.WZ_AES, allowZip64=False) as archive:
        archive.setpassword(password)
        archive.setencryption(pyzipper.WZ_AES, nbits=256, force_wz_aes_version=2)
        for done, (name, payload) in enumerate((("record.json", record_json),
                ("manifest.json", manifest_json), ("说明.txt", readme)), 1):
            _cancel(is_cancelled)
            archive.writestr(name, payload)
            on_progress(done, total)
        for i, (item, entry) in enumerate(zip(plan["files"], entries), 4):
            _cancel(is_cancelled)
            with archive.open(entry["entry"], "w") as target:
                _read_file(item["path"], expected=item, consume=target.write,
                           is_cancelled=is_cancelled)
            on_progress(i, total)
        if any(not (item.flag_bits & 1) or getattr(item, "wz_aes_strength", None) != 3
               or getattr(item, "wz_aes_version", None) != 2
               for item in archive.infolist()):
            raise EvidenceError("加密校验失败，证据包未导出")
    _cancel(is_cancelled)


def bundle_bytes(record_id, password, *, output_root=None, extra_files=(), history_path=None,
                 is_cancelled=lambda: False, on_progress=lambda done, total: None) -> dict:
    """Return portable encrypted bytes; source paths never enter the response."""
    secret = _password(password)
    _dependency()
    _cancel(is_cancelled)
    plan = plan_export(record_id, output_root=output_root, extra_files=extra_files,
                       history_path=history_path)
    stream = io.BytesIO()
    _write_archive(stream, plan, secret, is_cancelled, on_progress)
    data = stream.getvalue()
    return {"data": data, "filename": plan["filename"], "warnings": plan["warnings"],
            "file_count": plan["file_count"], "bytes": len(data)}


def export_bundle(record_id, password, target, *, output_root=None, extra_files=(),
                  history_path=None, is_cancelled=lambda: False,
                  on_progress=lambda done, total: None) -> dict:
    """Publish a same-directory temporary ZIP atomically, WITHOUT replacement."""
    secret = _password(password)
    _dependency()
    _cancel(is_cancelled)
    destination = _path(target)
    if destination.suffix.casefold() != ".zip":
        raise EvidenceError("证据包文件名必须以 .zip 结尾")
    if os.path.lexists(destination):
        raise EvidenceError("目标文件已存在，请使用新的文件名")
    parent_info = _components(destination.parent, private=False)
    if not stat.S_ISDIR(parent_info.st_mode):
        raise EvidenceError("导出目录不存在")
    plan = plan_export(record_id, output_root=output_root, extra_files=extra_files,
                       history_path=history_path)
    if any(os.path.normcase(str(destination)) == os.path.normcase(item["path"])
           for item in plan["files"]):
        raise EvidenceError("导出目标不能与证据原文件相同")
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".certificate-evidence-", suffix=".tmp",
                                            dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w+b") as output:
            _write_archive(output, plan, secret, is_cancelled, on_progress)
            output.flush()
            os.fsync(output.fileno())
            size = output.tell()
        _cancel(is_cancelled)
        if _state(parent_info)[:2] != _state(_components(destination.parent, private=False))[:2]:
            raise EvidenceError("导出目录在写入期间发生变化")
        # Windows rename fails when target exists. POSIX link publishes a complete
        # inode with O_EXCL semantics; neither branch overwrites a concurrent file.
        if os.name == "nt":
            os.rename(temporary, destination)
            temporary = None
        else:
            os.link(temporary, destination)
        return {"path": str(destination), "filename": destination.name,
                "warnings": plan["warnings"], "file_count": plan["file_count"], "bytes": size}
    except FileExistsError:
        raise EvidenceError("目标文件已存在，请使用新的文件名") from None
    except OSError:
        raise EvidenceError("导出失败：无法安全写入目标目录，请更换目录后重试") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
