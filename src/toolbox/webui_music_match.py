"""Qt-free automatic-tag preview and confirmed application for WebUI.

Preview plans are derived, process-local drafts (three / 40 MiB / 30 minutes),
never configuration or a second history store. The shared tag rules and engines
own matching and tag serialization; tag_history remains the history writer.
Existing picture bytes are read only while building a row and never cached.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

from music import tag_history, tag_match_rules, tag_search, tags_io
from toolbox import webui_music_tag as tag_api
from toolbox.file_output import create_bytes
from toolbox.webui_music_pictures import _checked_image

MAX_FILES = 20
MAX_PLANS = 3
MAX_MEMORY = 40 * 1024 * 1024
MAX_SIDECAR = 20 * 1024 * 1024
MAX_LYRICS = 2 * 1024 * 1024
TTL_SECONDS = 30 * 60
_FIELDS = tag_match_rules.TEXT_MATCH_FIELDS + [("cover", "封面"), ("lyrics", "歌词")]
_FIELD_KEYS = {key for key, _label in _FIELDS}
_EXTENSIONS = (".png", ".jpg", ".lrc")


class _PlanError(ValueError):
    """Only deliberately public, path-free error messages use this type."""


def _progress(callback, message, value):
    # Some platform adapters swallow progress exceptions. Cancellation is always
    # read from the separate callback at file boundaries, never from this hook.
    try:
        callback(message, value)
    except Exception:
        pass


def _settings(incoming, *, strict_sources=True):
    defaults = {"use_filename_only": False, "use_artist": True,
                "use_album": True, "threads": 3,
                "sources": list(tag_search.DEFAULT_SOURCES),
                "modes": {key: 0 for key in _FIELD_KEYS},
                "overwrite": {key: False for key in _FIELD_KEYS}}
    if not isinstance(incoming, dict) or set(incoming) - set(defaults):
        raise _PlanError("自动匹配设置无效，请重新选择设置")
    result = copy.deepcopy(defaults)
    for key in ("use_filename_only", "use_artist", "use_album"):
        if key in incoming:
            if type(incoming[key]) is not bool:
                raise _PlanError("搜索条件必须为开关值")
            result[key] = incoming[key]
    threads = incoming.get("threads", defaults["threads"])
    if type(threads) is not int or not 1 <= threads <= 8:
        raise _PlanError("匹配并发数必须为 1 到 8 的整数")
    result["threads"] = threads
    sources = incoming.get("sources") if strict_sources else incoming.get("sources", defaults["sources"])
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(key, str) or key not in tag_search.DEFAULT_SOURCES
                   for key in sources)):
        raise _PlanError("请明确选择至少一个有效标签源")
    result["sources"] = list(dict.fromkeys(sources))
    for group in ("modes", "overwrite"):
        values = incoming.get(group, {})
        if not isinstance(values, dict) or set(values) - _FIELD_KEYS:
            raise _PlanError("字段匹配设置无效")
        for key, value in values.items():
            if group == "overwrite":
                if type(value) is not bool:
                    raise _PlanError("覆盖已有值必须为开关值")
            elif (type(value) is not int or value not in
                  ((0, 1, 2, 3) if key in ("cover", "lyrics") else (0, 1))):
                raise _PlanError("字段写入方式无效")
            result[group][key] = value
    return result


def _relative_values(values):
    if not isinstance(values, list) or not values or len(values) > MAX_FILES:
        raise _PlanError("请选择 1 到 20 个音乐文件")
    result = []
    for value in values:
        if (not isinstance(value, str) or not value.strip() or len(value) > 2048
                or value != value.strip() or ":" in value or "\x00" in value):
            raise _PlanError("音乐文件路径无效，请重新选择文件")
        normal = value.replace("\\", "/")
        if normal.startswith("/") or ".." in normal.split("/"):
            raise _PlanError("只允许音乐目录内的相对路径")
        normal = str(PurePosixPath(normal))
        if normal not in result:
            result.append(normal)
    return result


def _file_state(path: Path, *, optional=False, max_bytes=None):
    """Stable streaming SHA-256, including absent sidecar state."""
    if optional and not os.path.lexists(path):
        return None
    first = path.lstat()
    if not stat.S_ISREG(first.st_mode) or path.is_symlink():
        raise _PlanError("文件类型不安全，请重新选择音乐文件")
    if max_bytes is not None and first.st_size > max_bytes:
        raise _PlanError("已有同名文件超过 20 MB，请先检查该文件")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    last = path.lstat()
    stamp = lambda value: (value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino)
    if stamp(first) != stamp(last):
        raise _PlanError("文件正在变化，请重新预览后重试")
    return (*stamp(last), digest.hexdigest())


def _sidecar_path(path, suffix, root):
    if suffix not in _EXTENSIONS:
        raise _PlanError("匹配产物类型无效")
    result = path.with_suffix(suffix)
    if result.parent.resolve() != path.parent or not result.is_relative_to(root):
        raise _PlanError("匹配产物位置已变化，请重新预览")
    return result


def _picture_key(pictures):
    return [(picture.type, picture.mime, picture.desc,
             hashlib.sha256(picture.data).hexdigest()) for picture in pictures]


def _safe_match(match):
    """Keep only data needed by shared rules; omit ranked rows, URLs and errors."""
    candidate = match.candidate
    if candidate is None:
        return None
    values = {key: str(getattr(candidate, key, "") or "")[:100_000]
              for key, _label in tag_match_rules.TEXT_MATCH_FIELDS
              if key not in ("track", "disc")}
    for key in ("track", "disc"):
        raw = getattr(candidate, key, 0)
        values[key] = int(raw or 0)
    lyric = str(getattr(candidate, "lyric", "") or "")
    if len(lyric.encode("utf-8")) > MAX_LYRICS:
        raise _PlanError("匹配歌词过大，请改用单文件编辑")
    source = str(getattr(candidate, "source", ""))
    values.update(lyric=lyric, source_name=tag_search.SOURCE_NAMES.get(source, "音乐平台"))
    scores = []
    for score in tuple(getattr(candidate, "scores", ()))[:3]:
        score = float(score)
        scores.append(score if math.isfinite(score) else 0.0)
    values["scores"] = scores
    cover = match.cover or b""
    mime = ""
    if cover:
        try:
            cover, mime = _checked_image(cover)
        except ValueError:
            raise _PlanError("匹配封面无效或超过大小限制，请关闭封面处理后重试") from None
    return SimpleNamespace(candidate=SimpleNamespace(**values), cover=cover,
                           cover_mime=mime)


def _changes(before, after):
    labels = dict(_FIELDS)
    return [{"key": key, "label": labels.get(key, key),
             "before": str(before.get(key) or ""), "after": str(value or "")}
            for key, value in after.items()
            if str(before.get(key) or "") != str(value or "")]


def _match_size(match):
    # Count every retained string, including multibyte text; do not account
    # only for image bytes while leaving unusually large candidate tags free.
    return len(match.cover) + sum(len(value.encode("utf-8"))
        for value in vars(match.candidate).values() if isinstance(value, str))


def _public_row(rel, status, message=""):
    return {"rel": rel, "name": PurePosixPath(rel).name, "status": status,
            "candidate": None, "changes": [], "cover_changed": False,
            "pictures_changed": False, "sidecars": [], "message": message}


def _sidecar_summary(path, plan, states, root):
    rows = []
    for sidecar in plan["sidecars"]:
        suffix = sidecar["suffix"]
        target = _sidecar_path(path, suffix, root)
        state = states[suffix]
        same = state is not None and state[-1] == hashlib.sha256(sidecar["data"]).hexdigest()
        action = "skip" if same or (state is not None and not sidecar["overwrite"]) else (
            "replace" if state is not None else "create")
        rows.append({"name": target.name, "action": action, "size": len(sidecar["data"])})
    return rows


def _replace_sidecar(target, data, expected, root):
    """Keep a complete sibling backup before atomic replacement and readback."""
    if _file_state(target, optional=True, max_bytes=MAX_SIDECAR) != expected:
        raise _PlanError("同名文件已变化，请重新预览")
    backup = target.with_name(target.name + ".fmjj-auto-backup-" + secrets.token_hex(8) + ".bak")
    with target.open("rb") as source, backup.open("xb") as dest:
        while block := source.read(1024 * 1024):
            dest.write(block)
        dest.flush()
        os.fsync(dest.fileno())
    if _file_state(backup)[-1] != expected[-1]:
        raise _PlanError("原文件安全副本校验失败，已停止覆盖")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".fmjj-auto-", suffix=".tmp", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if target.parent.resolve() != target.parent or not target.is_relative_to(root):
            raise _PlanError("目标目录已变化，已停止覆盖")
        if _file_state(target, optional=True, max_bytes=MAX_SIDECAR) != expected:
            raise _PlanError("同名文件已变化，已停止覆盖；安全副本已保留")
        os.replace(temporary, target)
        if _file_state(target)[-1] != hashlib.sha256(data).hexdigest():
            raise _PlanError("同名文件写入校验失败；原始安全副本已保留")
    except _PlanError:
        raise
    except Exception:
        raise _PlanError("同名文件写入失败；原始安全副本已保留") from None
    finally:
        if temporary and os.path.lexists(temporary):
            os.unlink(temporary)
    return backup.name


class MusicMatchPlans:
    """Bounded, one-use selections from in-memory automatic matching previews."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._plans = {}

    def options(self):
        settings = _settings({}, strict_sources=False)
        # The same configuration owner supplies desktop defaults. No writes or
        # credential reads are needed to show these non-secret options.
        try:
            saved = tag_api.load_convert_config().get("music_tag", {})
            if isinstance(saved, dict):
                selected = {key: saved[key] for key in settings if key in saved}
                settings = _settings(selected, strict_sources=False)
        except Exception:
            pass
        return {"settings": settings, "max_files": MAX_FILES,
                "sources": [{"key": key, "label": tag_search.SOURCE_NAMES[key]}
                            for key in tag_search.DEFAULT_SOURCES],
                "fields": [{"key": key, "label": label, "modes":
                    [{"value": index, "label": name} for index, name in enumerate(
                        ["写入标签", "保存同名图片" if key == "cover" else "保存同名 .lrc",
                         "两者都写", "不处理"] if key in ("cover", "lyrics")
                        else ["写入标签", "不写入"])]} for key, label in _FIELDS]}

    def _prune(self):
        now = self._clock()
        for key in list(self._plans):
            if self._plans[key]["expires"] <= now:
                self._plans.pop(key)

    def _store(self, entries, settings):
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._prune()
            size = sum(entry["memory"] for entry in entries.values())
            while self._plans and (len(self._plans) >= MAX_PLANS or
                    sum(plan["memory"] for plan in self._plans.values()) + size > MAX_MEMORY):
                self._plans.pop(next(iter(self._plans)))
            self._plans[key] = {"entries": entries, "settings": settings,
                                "expires": self._clock() + TTL_SECONDS, "memory": size}
        return key

    def preview(self, payload, *, is_cancelled=lambda: False,
                on_progress=lambda message, progress: None):
        if not isinstance(payload, dict) or set(payload) - {"rels", "settings"}:
            raise _PlanError("匹配预览参数无效")
        rels = _relative_values(payload.get("rels"))
        settings = _settings(payload.get("settings"))
        resolved = []
        seen = set()
        for rel in rels:
            try:
                path = tag_api._resolve(rel)
                canonical = os.path.normcase(str(path))
                rel = path.relative_to(tag_api._root()).as_posix()
            except Exception:
                raise _PlanError("所选音乐文件不存在或超出音乐目录，请重新选择") from None
            if canonical not in seen:
                resolved.append((rel, path))
                seen.add(canonical)
        rows = [None] * len(resolved)
        entries = {}
        memory = 0
        cancelled = False
        with ThreadPoolExecutor(max_workers=settings["threads"]) as pool:
            futures = {pool.submit(self._preview_one, rel, path, settings, is_cancelled): index
                       for index, (rel, path) in enumerate(resolved)}
            for future in as_completed(futures):
                index = futures[future]
                public, entry = future.result()
                if entry is not None:
                    if memory + entry["memory"] > MAX_MEMORY:
                        public = _public_row(public["rel"], "error", "匹配结果超出预览容量，请减少文件后重试")
                    else:
                        memory += entry["memory"]
                        entries[public["rel"]] = entry
                rows[index] = public
                cancelled = cancelled or public["status"] == "cancelled" or bool(is_cancelled())
                _progress(on_progress, "已匹配 %d/%d 个文件" % (sum(row is not None for row in rows), len(rows)),
                          sum(row is not None for row in rows) / len(rows))
        preview_id = self._store(entries, settings)
        ready = sum(row["status"] == "ready" for row in rows)
        return {"preview_id": preview_id, "rows": rows, "cancelled": cancelled,
                "summary": f"已预览 {len(rows)} 个文件，{ready} 个可应用" + ("；已停止后续匹配" if cancelled else ""),
                "expires_in": TTL_SECONDS}

    def _preview_one(self, rel, path, settings, is_cancelled):
        if is_cancelled():
            return _public_row(rel, "cancelled", "已取消，未开始匹配"), None
        try:
            with tag_api._WRITE_LOCK:
                root = tag_api._root()
                state = _file_state(path)
                io_settings = tag_api._settings()
                tags = tags_io.load_tags(str(path), io_settings["connector"]) or {}
                pictures = tags_io.load_pictures(str(path))
                if _file_state(path) != state:
                    raise _PlanError("音乐文件正在变化，请重新预览")
            row = {"path": str(path), "tags": tags, "pictures": pictures}
            title, artist, album, used_filename = tag_match_rules.build_query(row, settings)
            match = tag_search.auto_match(title, artist, album, sources=settings["sources"],
                used_filename=used_filename, need_lyric=settings["modes"]["lyrics"] != 3,
                need_cover=settings["modes"]["cover"] != 3, connector=io_settings["connector"])
            # Preserve a completed result even if the user cancelled during its
            # request. Queued files observe cancellation before starting.
            if _file_state(path) != state:
                raise _PlanError("音乐文件在匹配期间发生变化，请重新预览")
            candidate = _safe_match(match)
            if candidate is None:
                failed = [tag_search.SOURCE_NAMES[key] + "暂时无法匹配"
                          for key in settings["sources"] if key in (match.errors or {})]
                return _public_row(rel, "unmatched", "；".join(failed) or "没有匹配到候选"), None
            plan = tag_match_rules.build_update_plan(row, candidate, settings)
            # The candidate determines the actual picture suffix. Only paths
            # the shared plan would use participate in conflict protection;
            # unrelated large files/directories must not block tag-only work.
            with tag_api._WRITE_LOCK:
                if _file_state(path) != state:
                    raise _PlanError("音乐文件在匹配期间发生变化，请重新预览")
                side_states = {item["suffix"]: _file_state(
                    _sidecar_path(path, item["suffix"], root),
                    optional=True, max_bytes=MAX_SIDECAR) for item in plan["sidecars"]}
            changes = _changes(tags, plan["tags"])
            cover_changed = plan["pictures_dirty"] and _picture_key(pictures) != _picture_key(plan["pictures"])
            sidecars = _sidecar_summary(path, plan, side_states, root)
            ready = bool(changes or cover_changed or any(s["action"] != "skip" for s in sidecars))
            public = _public_row(rel, "ready" if ready else "unchanged",
                                 "请核对变化后确认应用" if ready else "已有值保持不变")
            cand = candidate.candidate
            public.update(candidate={"source_name": cand.source_name, "title": cand.title,
                "artist": cand.artist, "album": cand.album, "scores": cand.scores},
                changes=changes, cover_changed=bool(cover_changed), pictures_changed=bool(cover_changed),
                sidecars=sidecars)
            if not ready:
                return public, None
            size = _match_size(candidate)
            return public, {"path": path, "state": state, "side_states": side_states,
                "match": candidate, "io_settings": io_settings, "memory": size}
        except _PlanError as exc:
            return _public_row(rel, "error", str(exc)), None
        except Exception:
            return _public_row(rel, "error", "匹配失败，请检查文件或稍后重试"), None

    def apply(self, payload, *, is_cancelled=lambda: False,
              on_progress=lambda message, progress: None):
        if (not isinstance(payload, dict) or set(payload) - {"preview_id", "rels", "confirmed"}
                or payload.get("confirmed") is not True):
            raise _PlanError("请先确认应用所选匹配结果")
        preview_id = payload.get("preview_id")
        if not isinstance(preview_id, str) or not preview_id or len(preview_id) > 128:
            raise _PlanError("匹配预览无效，请重新预览")
        rels = _relative_values(payload.get("rels"))
        with self._lock:
            self._prune()
            plan = self._plans.get(preview_id)
            if not plan:
                raise _PlanError("匹配预览已过期或已应用，请重新预览")
            if any(rel not in plan["entries"] for rel in rels):
                raise _PlanError("仅能应用尚未处理的可应用结果，请重新预览")
            claimed = [(rel, plan["entries"].pop(rel)) for rel in rels]
            plan["memory"] -= sum(entry["memory"] for _rel, entry in claimed)
            settings = plan["settings"]
            if not plan["entries"]:
                self._plans.pop(preview_id)
        rows = []
        cancelled = False
        for rel, entry in claimed:
            if is_cancelled():
                cancelled = True
                rows.append({"rel": rel, "name": entry["path"].name,
                             "status": "cancelled", "message": "已取消，未写入该文件"})
                continue
            rows.append(self._apply_one(rel, entry, settings))
            _progress(on_progress, "已处理 %d/%d 个文件" % (len(rows), len(claimed)), len(rows) / len(claimed))
        saved = sum(row["status"] == "saved" for row in rows)
        return {"rows": rows, "cancelled": cancelled,
                "summary": f"已保存 {saved}/{len(rows)} 个文件" + ("；已取消其余文件" if cancelled else "")}

    def _apply_one(self, rel, entry, settings):
        result = {"rel": rel, "name": entry["path"].name, "status": "error", "message": ""}
        completed = []
        backups = []
        attempted_audio = False
        try:
            with tag_api._WRITE_LOCK:
                path = tag_api._resolve(rel)
                root = tag_api._root()
                if path != entry["path"] or _file_state(path) != entry["state"]:
                    raise _PlanError("音乐文件已变化，请重新预览后再应用")
                before = tags_io.load_tags(str(path), entry["io_settings"]["connector"]) or {}
                pictures = tags_io.load_pictures(str(path))
                row = {"path": str(path), "tags": before, "pictures": pictures}
                plan = tag_match_rules.build_update_plan(row, entry["match"], settings)
                for sidecar in plan["sidecars"]:
                    target = _sidecar_path(path, sidecar["suffix"], root)
                    if _file_state(target, optional=True, max_bytes=MAX_SIDECAR) != entry["side_states"][sidecar["suffix"]]:
                        raise _PlanError("同名图片或歌词文件已变化，请重新预览后再应用")
                if _file_state(path) != entry["state"]:
                    raise _PlanError("音乐文件已变化，请重新预览后再应用")
                changes = _changes(before, plan["tags"])
                pictures_changed = plan["pictures_dirty"] and _picture_key(pictures) != _picture_key(plan["pictures"])
                if changes or pictures_changed:
                    attempted_audio = True
                    tags_io.save_tags(str(path), plan["tags"],
                        id3_version=entry["io_settings"]["id3_version"],
                        pictures=plan["pictures"] if pictures_changed else None)
                    completed.append("标签")
                    fresh = tags_io.load_tags(str(path), entry["io_settings"]["connector"]) or {}
                    if any(str(fresh.get(item["key"]) or "") != item["after"] for item in changes):
                        raise _PlanError("标签写入后读回不一致，请检查该音乐文件")
                    if pictures_changed and not any(p.data == entry["match"].cover
                                                    for p in tags_io.load_pictures(str(path))):
                        raise _PlanError("封面写入后读回不一致，请检查该音乐文件")
                    tag_history.record(str(path), before, fresh)
                for sidecar in plan["sidecars"]:
                    suffix = sidecar["suffix"]
                    target = _sidecar_path(path, suffix, root)
                    expected = entry["side_states"][suffix]
                    if expected is not None and not sidecar["overwrite"]:
                        continue
                    data = sidecar["data"]
                    if expected is not None and expected[-1] == hashlib.sha256(data).hexdigest():
                        continue
                    if expected is None:
                        create_bytes(target, data, root=root, max_bytes=MAX_SIDECAR)
                        if _file_state(target)[-1] != hashlib.sha256(data).hexdigest():
                            raise _PlanError("同名文件写入后校验失败，请检查结果")
                    else:
                        backups.append(_replace_sidecar(target, data, expected, root))
                    completed.append(target.name)
                fields = tags_io.load_tags(str(path), entry["io_settings"]["connector"]) or {}
                result.update(status="saved" if completed else "skipped",
                    fields={key: str(fields.get(key) or "") for key in tag_api.FIELDS},
                    message=("已应用匹配结果" if completed else "没有需要写入的变化")
                    + ("；同目录原始安全副本已保留" if backups else ""))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, _PlanError) else "应用失败，请检查文件权限或稍后重试"
            if completed:
                reason = "部分已完成（" + "、".join(completed) + "）；" + reason
            elif attempted_audio:
                reason = "写入未完成，音乐文件可能已部分变化，请检查；" + reason
            if backups:
                reason += "；同目录原始安全副本已保留"
            result["message"] = reason
        return result
