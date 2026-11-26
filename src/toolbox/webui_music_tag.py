"""WebUI 标签：音乐目录文件与标签快照、历史，以及编辑 / 写入 / 歌词抓取。

所有可写操作都限定在「音乐下载目录」之内：路径只接受该目录下的相对路径，
绝对路径、`..` 逃逸与目录外文件一律拒绝；写入前后各读一次标签，
变化会记录到标签历史（桌面端「标签历史」同样可见）。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import (get_config_dir, get_music_download_dir,
                                    load_convert_config)

_AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".ape", ".wv",
               ".aac", ".opus", ".wma"}
_MAX_FILES = 300
_MAX_HISTORY = 60
_MAX_SCAN = 500
FIELDS = {
    "title": {"type": "str", "default": "", "label": "标题"},
    "artist": {"type": "str", "default": "", "label": "艺术家"},
    "album": {"type": "str", "default": "", "label": "专辑"},
    "albumartist": {"type": "str", "default": "", "label": "专辑艺术家"},
    "year": {"type": "str", "default": "", "label": "年份"},
    "track": {"type": "str", "default": "", "label": "音轨"},
    "track_count": {"type": "str", "default": "", "label": "音轨总数"},
    "disc": {"type": "str", "default": "", "label": "碟号"},
    "disc_count": {"type": "str", "default": "", "label": "碟数"},
    "genre": {"type": "str", "default": "", "label": "流派"},
    "composer": {"type": "str", "default": "", "label": "作曲"},
    "lyricist": {"type": "str", "default": "", "label": "作词"},
    "comment": {"type": "str", "default": "", "label": "备注"},
    "lyrics": {"type": "str", "default": "", "label": "歌词"},
}
_FIELDS = tuple(FIELDS)


def _field_schema() -> list[dict]:
    return [{"name": name, **definition} for name, definition in FIELDS.items()]


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _history() -> list[dict]:
    path = Path(get_config_dir()) / "music_tag_history.db"
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                               timeout=3)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT createtime, filepath, title, artist, album FROM tag_history"
            " ORDER BY serial DESC LIMIT ?", (_MAX_HISTORY,)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    result = []
    for row in rows:
        result.append({
            "time": _ts(row["createtime"]),
            "file": os.path.basename(str(row["filepath"] or "")),
            "title": row["title"] or "",
            "artist": row["artist"] or "",
            "album": row["album"] or "",
        })
    return result


def _settings() -> dict:
    try:
        section = load_convert_config().get("music_tag")
    except Exception:  # noqa: BLE001
        section = {}
    section = section if isinstance(section, dict) else {}
    return {
        "connector": str(section.get("connector") or "/"),
        "id3_version": int(section.get("id3_version") or 3),
    }


def _root() -> Path:
    return Path(get_music_download_dir()).resolve()


def _resolve(rel: str) -> Path:
    value = str(rel or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("缺少文件路径")
    if value.startswith("/") or ":" in value or value.startswith(".."):
        raise ValueError("只支持音乐目录内的相对路径")
    root = _root()
    target = (root / value).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("路径超出音乐目录") from None
    if not target.is_file():
        raise ValueError(f"文件不存在：{value}")
    if target.suffix.lower() not in _AUDIO_EXTS:
        raise ValueError("不是受支持的音频文件")
    return target


def _file_row(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {"rel": path.relative_to(root).as_posix(), "name": path.name,
            "size": int(stat.st_size), "mtime": _ts(stat.st_mtime)}


def overview(limit: int = 120) -> dict:
    limit = max(1, min(_MAX_FILES, int(limit)))
    root = _root()
    from music.tags_io import load_tags

    files = []
    truncated = False
    if root.is_dir():
        scanned = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in _AUDIO_EXTS:
                    continue
                scanned.append(Path(dirpath) / name)
        for path in scanned[:limit]:
            try:
                stat = path.stat()
                rel = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            try:
                tags = load_tags(str(path)) or {}
            except Exception:  # noqa: BLE001
                tags = {}
            files.append({
                "name": path.name,
                "rel": rel,
                "size": int(stat.st_size),
                "mtime": _ts(stat.st_mtime),
                "title": str(tags.get("title") or ""),
                "artist": str(tags.get("artist") or ""),
                "album": str(tags.get("album") or ""),
                "year": str(tags.get("year") or ""),
                "genre": str(tags.get("genre") or ""),
            })
        truncated = len(scanned) > limit
    files.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "root": str(root),
        "files": files,
        "total": len(files),
        "truncated": truncated,
        "history": _history(),
        "note": "可编辑标签、抓取歌词；路径限制在音乐目录内。",
    }


def scan(payload: dict) -> dict:
    """「添加文件 / 添加文件夹」：按相对路径扫描音乐目录，返回可加入列表的音频。"""
    value = str(payload.get("path") or "").strip().replace("\\", "/")
    if value.startswith("/") or ":" in value or value.startswith(".."):
        raise ValueError("只支持音乐目录内的相对路径")
    folder = bool(payload.get("folder"))
    root = _root()
    target = (root / value).resolve() if value else root
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("路径超出音乐目录") from None
    if not target.exists():
        raise ValueError(f"路径不存在：{value or '.'}")
    found: list[Path] = []
    if target.is_file():
        if target.suffix.lower() in _AUDIO_EXTS:
            found.append(target)
    elif folder:
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if Path(name).suffix.lower() in _AUDIO_EXTS:
                    found.append(Path(dirpath) / name)
            if len(found) >= _MAX_SCAN:
                break
    else:
        for name in sorted(os.listdir(target)):
            path = target / name
            if path.is_file() and path.suffix.lower() in _AUDIO_EXTS:
                found.append(path)
    if not found:
        raise ValueError("该路径下没有音频文件")
    rows = [_file_row(path, root) for path in found[:_MAX_SCAN]]
    return {"files": rows, "total": len(rows), "root": str(root)}


def detail(payload: dict) -> dict:
    from music.tags_io import load_tags

    path = _resolve(payload.get("rel") or "")
    settings = _settings()
    tags = load_tags(str(path), settings["connector"]) or {}
    return {
        "rel": path.relative_to(_root()).as_posix(),
        "name": path.name,
        "fields": {key: str(tags.get(key) or "") for key in _FIELDS},
        "field_schema": _field_schema(),
    }


def history(payload: dict, limit: int = 5) -> dict:
    """读取单个音乐文件的完整标签历史快照，只读、不初始化或改写历史库。"""
    path = _resolve(payload.get("rel") or "")
    limit = max(1, min(5, int(limit)))
    db_path = Path(get_config_dir()) / "music_tag_history.db"
    if not db_path.is_file():
        return {"rel": path.relative_to(_root()).as_posix(), "rows": [], "total": 0}
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True,
                               timeout=3)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise ValueError("读取标签历史失败，请稍后重试") from exc
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("tag_history",)).fetchone()
        if not has_table:
            rows = []
        else:
            columns = ", ".join(("createtime",) + _FIELDS)
            rows = conn.execute(
                f"SELECT {columns} FROM tag_history WHERE filepath=? "
                "ORDER BY serial DESC LIMIT ?", (str(path), limit)).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("读取标签历史失败，请稍后重试") from exc
    finally:
        conn.close()
    result = []
    for row in rows:
        item = {"time": _ts(row["createtime"])}
        item.update({key: str(row[key] or "") for key in _FIELDS})
        result.append(item)
    return {"rel": path.relative_to(_root()).as_posix(),
            "rows": result, "total": len(result),
            "field_schema": _field_schema()}


def write(payload: dict) -> dict:
    """写入标签：只处理提交的字段（空串表示清除该字段）。"""
    from music import tag_history
    from music.tags_io import load_tags, save_tags

    path = _resolve(payload.get("rel") or "")
    incoming = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    changed = {key: str(incoming[key]) for key in _FIELDS if key in incoming}
    if not changed:
        raise ValueError("没有要写入的字段")
    settings = _settings()
    before = load_tags(str(path), settings["connector"]) or {}
    after = dict(before)
    after.update(changed)
    save_tags(str(path), after, id3_version=settings["id3_version"])
    tag_history.record(str(path), before, after)
    fresh = load_tags(str(path), settings["connector"]) or {}
    return {
        "rel": path.relative_to(_root()).as_posix(),
        "fields": {key: str(fresh.get(key) or "") for key in _FIELDS},
        "summary": f"已写入 {path.name}（{len(changed)} 个字段）",
    }


def lyric_candidates(payload: dict) -> dict:
    from music import tag_search

    path = _resolve(payload.get("rel") or "")
    stem = os.path.splitext(path.name)[0]
    title = str(payload.get("title") or "").strip() or stem
    artist = str(payload.get("artist") or "").strip()
    errors: dict = {}
    candidates = tag_search.search_tracks(title, artist, "", errors=errors,
                                          used_filename=not payload.get("title"))
    rows = []
    for index, cand in enumerate(candidates[:12]):
        rows.append({
            "index": index,
            "source": cand.source,
            "source_id": cand.source_id,
            "title": cand.title,
            "artist": cand.artist,
            "album": cand.album,
            "has_lyric": bool(cand.lyric),
        })
    return {
        "rel": path.relative_to(_root()).as_posix(),
        "query": {"title": title, "artist": artist},
        "candidates": rows,
        "errors": {k: str(v)[:120] for k, v in (errors or {}).items()},
        "summary": f"{title} · {len(rows)} 个候选",
    }


def lyric_apply(payload: dict) -> dict:
    """按候选的来源与 ID 重新搜索并写入歌词（含翻译时合并到同一歌词块）。"""
    from music import tag_history, tag_search
    from music.tags_io import load_tags, save_tags

    path = _resolve(payload.get("rel") or "")
    source = str(payload.get("source") or "")
    source_id = str(payload.get("source_id") or "")
    title = str(payload.get("title") or "").strip()
    artist = str(payload.get("artist") or "").strip()
    if not source or not source_id:
        raise ValueError("请选择歌词候选")
    candidates = tag_search.search_tracks(title, artist, "",
                                          used_filename=not title)
    picked = next((c for c in candidates
                   if c.source == source and c.source_id == source_id), None)
    if picked is None:
        raise ValueError("候选已失效，请重新搜索")
    lyric, trans = tag_search.load_lyric(picked)
    text = lyric or ""
    if trans:
        text = (text + "\n\n" if text else "") + trans
    if not text.strip():
        raise ValueError("该候选没有歌词")
    settings = _settings()
    before = load_tags(str(path), settings["connector"]) or {}
    after = dict(before)
    after["lyrics"] = text
    save_tags(str(path), after, id3_version=settings["id3_version"])
    tag_history.record(str(path), before, after)
    return {
        "rel": path.relative_to(_root()).as_posix(),
        "lyrics": text,
        "length": len(text),
        "summary": f"已写入歌词：{picked.title} · {picked.artist}",
    }
