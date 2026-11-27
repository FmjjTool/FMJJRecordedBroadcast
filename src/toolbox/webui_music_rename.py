"""Qt-free, confirmed music-tag renaming with bounded one-use preview plans.

Music files and sidecars remain primary user files; this module owns only their
confirmed rename operation. Plans are derived in-memory state (three plans,
100 files each, 30 minutes), never another configuration or history store.
Web writes share the tag service lock; other processes use optimistic checks.
"""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import stat
import threading
import time

from music import tags_io
from music.tag_rename_rules import DEFAULT_PATTERN, PRESETS, TOKENS, render_rename_pattern
from toolbox import webui_music_tag as tag_api

MAX_FILES = 100
MAX_PLANS = 3
TTL_SECONDS = 30 * 60
RELATED_EXTENSIONS = (".lrc", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


class _RenameError(ValueError):
    """Deliberately public error text; never raw filesystem exceptions."""


class _MoveIssue(_RenameError):
    """An already-moved source still needs a clear mapping in the result."""


def _progress(callback, message, value):
    try:
        callback(message, value)
    except Exception:
        pass


def _pattern(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 240
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise _RenameError("文件名模板须为 1 到 240 个字符，请重新填写")
    value = value.strip()
    if value in (".", "..") or value.endswith(".") or PureWindowsPath(value).is_reserved():
        raise _RenameError("文件名模板不能是保留名称或以句点结尾")
    return value


def _rels(values):
    if not isinstance(values, list) or not values or len(values) > MAX_FILES:
        raise _RenameError("请选择 1 到 100 个音乐文件")
    result = []
    for value in values:
        if (not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 2048 or ":" in value
                or any(ord(char) < 32 for char in value)):
            raise _RenameError("音乐文件路径无效，请重新选择文件")
        value = value.replace("\\", "/")
        if value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
            raise _RenameError("只允许音乐目录内的相对路径")
        parts = PurePosixPath(value).parts
        if any(PureWindowsPath(part).is_reserved() or part.endswith((".", " ")) for part in parts):
            raise _RenameError("音乐文件路径含保留名称，请重新选择文件")
        if value.casefold() not in {item.casefold() for item in result}:
            result.append(value)
    return result


def _key(path):
    # Conservative Windows identity also keeps preview reservations portable.
    return str(path).casefold()


def _check_reparse(path):
    value = path.lstat()
    if (stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
        raise _RenameError("文件或目录包含链接，请选择普通音乐文件")
    return value


def _root():
    from toolbox.config_manager import get_music_download_dir_readonly

    raw = Path(get_music_download_dir_readonly()).absolute()
    for component in reversed((raw, *raw.parents)):
        if component.exists() or os.path.lexists(component):
            _check_reparse(component)
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise _RenameError("音乐目录不存在，请先添加音乐文件")
    return root


def _inside(root, path):
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise _RenameError("文件位置超出音乐目录，请重新选择") from None
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if not stat.S_ISDIR(_check_reparse(cursor).st_mode):
            raise _RenameError("文件目录已变化，请重新预览")
    if path.parent.resolve(strict=True) != path.parent:
        raise _RenameError("文件目录已变化，请重新预览")
    return path


def _identity(path):
    info = _check_reparse(path)
    if not stat.S_ISDIR(info.st_mode):
        raise _RenameError("音乐目录已变化，请重新预览")
    return info.st_dev, info.st_ino


def _snapshot(root, path, *, optional=False, is_cancelled=lambda: False):
    _inside(root, path)
    if optional and not os.path.lexists(path):
        return None
    first = _check_reparse(path)
    if not stat.S_ISREG(first.st_mode):
        raise _RenameError("文件类型不安全，请重新选择音乐文件")
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if stamp(os.fstat(stream.fileno())) != stamp(first):
            raise _RenameError("文件正在变化，请重新预览")
        while block := stream.read(1024 * 1024):
            if is_cancelled():
                raise InterruptedError()
            digest.update(block)
    last = _check_reparse(path)
    if stamp(first) != stamp(last):
        raise _RenameError("文件正在变化，请重新预览")
    return (*stamp(last), digest.hexdigest())


def _valid_name(name):
    if (not name or name in (".", "..") or name.endswith((".", " "))
            or PureWindowsPath(name).is_reserved()
            or any(ord(char) < 32 or char in '\\/:*?"<>|' for char in name)
            or len(name.encode("utf-16-le")) // 2 > 240):
        raise _RenameError("标签生成的文件名无效或过长，请调整模板或标签")


def _move_no_replace(root, source, target, expected):
    """Move one regular file, failing atomically if the target already exists."""
    _inside(root, source)
    _inside(root, target)
    if _snapshot(root, source) != expected:
        raise _RenameError("文件已变化，请重新预览后执行")
    if os.name == "nt":
        # Unlike os.replace, Windows rename never replaces an existing target.
        os.rename(source, target)
    else:
        # A same-directory hard link publishes without replacement on POSIX.
        os.link(source, target, follow_symlinks=False)
        if _snapshot(root, source) != expected:
            raise _RenameError("原文件已变化，目标副本已保留，请检查后重新预览")
        try:
            source.unlink()
        except OSError:
            raise _RenameError("目标副本已保留，原文件未移除，请检查文件权限") from None
    try:
        actual = _snapshot(root, target)
        if actual[2:] != expected[2:]:
            raise ValueError()
    except Exception:
        raise _MoveIssue("文件已改名，但目标回读校验失败，请检查文件后重新预览") from None
    return actual


class MusicRenamePlans:
    """Preview exact targets, then consume an explicitly selected plan once."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._plans = {}

    def options(self):
        return {"default_pattern": DEFAULT_PATTERN, "presets": list(PRESETS),
                "tokens": [{"token": token, "label": label} for token, label in TOKENS],
                "max_files": MAX_FILES}

    def _expire(self):
        now = self._clock()
        self._plans = {key: plan for key, plan in self._plans.items()
                       if now - plan["created"] < TTL_SECONDS}

    def preview(self, payload, *, is_cancelled=lambda: False,
                on_progress=lambda message, progress: None):
        if not isinstance(payload, dict) or set(payload) - {"rels", "pattern"}:
            raise _RenameError("改名参数无效，请重新预览")
        pattern = _pattern(payload.get("pattern", DEFAULT_PATTERN))
        rels = _rels(payload.get("rels"))
        try:
            root = _root()
            identity = _identity(root)
        except (OSError, RuntimeError):
            raise _RenameError("音乐目录不可用，请先添加音乐文件") from None
        rows, entries, reserved, claimed = [], {}, set(), set()
        cancelled = False
        for index, rel in enumerate(rels):
            if is_cancelled():
                cancelled = True
                break
            public = {"id": str(index), "rel": rel, "target_rel": "", "status": "error",
                      "message": "", "related": []}
            try:
                with tag_api._WRITE_LOCK:
                    entry = self._preview_one(root, rel, pattern, public, reserved, claimed, is_cancelled)
                if entry:
                    entries[public["id"]] = entry
            except InterruptedError:
                cancelled = True
                break
            except _RenameError as error:
                public.update(status="error", message=str(error))
            except Exception:
                public.update(status="error", message="读取音乐文件失败，请检查文件后重试")
            rows.append(public)
            _progress(on_progress, f"已预览 {len(rows)}/{len(rels)} 个文件", len(rows) / len(rels))
        plan_id = secrets.token_urlsafe(24)
        with self._lock:
            self._expire()
            while len(self._plans) >= MAX_PLANS:
                self._plans.pop(next(iter(self._plans)))
            self._plans[plan_id] = {"created": self._clock(), "root": root, "identity": identity,
                                    "entries": entries}
        return {"plan_id": plan_id, "rows": rows, "ready": len(entries), "total": len(rels),
                "cancelled": cancelled, "pattern": pattern}

    def _preview_one(self, root, rel, pattern, public, reserved, claimed, is_cancelled):
        source = _inside(root, root / rel)
        if source.suffix.lower() not in tag_api._AUDIO_EXTS:
            raise _RenameError("不是受支持的音乐文件")
        before = _snapshot(root, source, is_cancelled=is_cancelled)
        tags = tags_io.load_tags(str(source)) or {}
        if (("@1" in pattern and not str(tags.get("title") or "").strip())
                or ("@2" in pattern and not str(tags.get("artist") or "").strip())):
            public.update(status="skipped", message="标题或艺术家为空，已跳过")
            return None
        stem = render_rename_pattern(pattern, tags)
        _valid_name(stem)
        _valid_name(stem + source.suffix)
        target = source.with_name(stem + source.suffix)
        if _key(target) == _key(source):
            public.update(target_rel=rel, status="unchanged", message="文件名无变化")
            return None
        number = 0
        while _key(target) in reserved or os.path.lexists(target):
            number += 1
            if number > 10_000:
                raise _RenameError("同名文件过多，请调整模板后重试")
            name = f"{stem} ({number}){source.suffix}"
            _valid_name(name)
            target = source.with_name(name)
        snapshots = {source: before, target: None}
        related, moves = [], [(source, target, before)]
        staged_targets, staged_sources = {_key(target)}, set()
        for suffix in RELATED_EXTENSIONS:
            old = source.with_suffix(suffix)
            new = target.with_suffix(suffix)
            old_state = _snapshot(root, old, optional=True, is_cancelled=is_cancelled)
            new_state = _snapshot(root, new, optional=True, is_cancelled=is_cancelled)
            snapshots[old], snapshots[new] = old_state, new_state
            if old_state is None:
                continue
            status, message = "ready", "随音乐文件改名"
            if _key(old) in claimed:
                status, message = "skipped", "已分配给本批其他音乐文件，保留原同名文件"
            elif new_state is not None or _key(new) in reserved or _key(new) in staged_targets:
                status, message = "skipped", "目标已存在或已分配，保留原同名文件"
            else:
                moves.append((old, new, old_state))
                staged_targets.add(_key(new))
                staged_sources.add(_key(old))
            related.append({"rel": old.relative_to(root).as_posix(),
                            "target_rel": new.relative_to(root).as_posix(),
                            "status": status, "message": message})
        if _snapshot(root, source, is_cancelled=is_cancelled) != before:
            raise _RenameError("音乐文件已变化，请重新预览")
        reserved.update(staged_targets)
        claimed.update(staged_sources)
        public.update(target_rel=target.relative_to(root).as_posix(), status="ready", related=related,
                      message="同名冲突已追加编号" if number else "待改名")
        return {"public": copy.deepcopy(public), "snapshots": snapshots, "moves": moves,
                "parent_identity": _identity(source.parent)}

    def apply(self, payload, *, is_cancelled=lambda: False,
              on_progress=lambda message, progress: None):
        if (not isinstance(payload, dict) or set(payload) - {"plan_id", "ids", "confirm"}
                or payload.get("confirm") is not True):
            raise _RenameError("请先确认勾选文件的改名操作")
        plan_id, ids = payload.get("plan_id"), payload.get("ids")
        if (not isinstance(plan_id, str) or not isinstance(ids, list) or not ids
                or len(ids) > MAX_FILES or any(not isinstance(item, str) for item in ids)
                or len(ids) != len(set(ids))):
            raise _RenameError("改名选择无效，请重新预览")
        with self._lock:
            self._expire()
            plan = self._plans.get(plan_id)
            if plan is None:
                raise _RenameError("改名预览已失效，请重新预览")
            if any(item not in plan["entries"] for item in ids):
                raise _RenameError("勾选文件不在当前可执行预览中，请重新选择")
            selected = [entry for key, entry in plan["entries"].items() if key in ids]
            self._plans.pop(plan_id)
        rows, overlay = [], {}
        cancelled = False
        with tag_api._WRITE_LOCK:
            try:
                if _root() != plan["root"] or _identity(plan["root"]) != plan["identity"]:
                    raise _RenameError("音乐目录已变化，请重新预览")
            except Exception:
                raise _RenameError("音乐目录已变化或不可用，请重新预览") from None
            for entry in selected:
                result = copy.deepcopy(entry["public"])
                result.update(moved=False, status="error")
                if is_cancelled():
                    cancelled = True
                    result.update(status="cancelled", message="已取消，未改名该文件")
                    for row in result["related"]:
                        if row["status"] == "ready":
                            row.update(status="cancelled", message="已取消，未改名该同名文件")
                else:
                    self._apply_one(plan["root"], entry, result, overlay)
                rows.append(result)
                _progress(on_progress, f"已处理 {len(rows)}/{len(selected)} 个文件", len(rows) / len(selected))
        moved = sum(row["moved"] for row in rows)
        return {"rows": rows, "moved": moved, "total": len(rows), "cancelled": cancelled,
                "summary": f"已改名 {moved}/{len(rows)} 个文件" + ("；已取消其余文件" if cancelled else "")}

    def _apply_one(self, root, entry, result, overlay):
        try:
            if _identity(entry["moves"][0][0].parent) != entry["parent_identity"]:
                raise _RenameError("文件目录已变化，请重新预览")
            for path, expected in entry["snapshots"].items():
                actual = _snapshot(root, path, optional=True)
                if actual != overlay.get(path, expected):
                    raise _RenameError("音乐文件或同名文件已变化，请重新预览后执行")
            for index, (source, target, expected) in enumerate(entry["moves"]):
                try:
                    actual = _move_no_replace(root, source, target, expected)
                    if index == 0:
                        result["moved"] = True
                    overlay[source] = None
                    overlay[target] = actual
                    if index:
                        next(row for row in result["related"]
                             if row["rel"] == source.relative_to(root).as_posix()).update(
                                 status="done", message="已随音乐文件改名")
                except Exception as error:
                    if index == 0:
                        if isinstance(error, _MoveIssue):
                            result["moved"] = True
                            overlay[source] = None
                        raise
                    related = next(row for row in result["related"]
                                   if row["rel"] == source.relative_to(root).as_posix())
                    related.update(status="error", message=str(error) if isinstance(error, _RenameError)
                                   else "同名文件改名失败，已保留原文件，请检查文件权限")
            failed = sum(row["status"] == "error" for row in result["related"])
            result.update(status="done", message=(f"音乐文件已改名；{failed} 个同名文件失败，请检查"
                                                   if failed else "已改名，文件内容保持不变"))
        except _RenameError as error:
            result.update(message=str(error))
        except Exception:
            result.update(message="改名失败，目标不会被覆盖，请检查文件占用与权限后重新预览")
        if result["status"] == "error":
            for row in result["related"]:
                if row["status"] == "ready":
                    row.update(status="skipped", message="音乐改名未全部通过校验，未执行该同名文件")
