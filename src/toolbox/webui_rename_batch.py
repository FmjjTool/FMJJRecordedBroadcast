"""WebUI 批量改名（只读预览）：扫描文件夹并按规则生成新文件名。

不执行改名；执行仍在桌面端。规则引擎与桌面共用 `toolbox.rename_rules`。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from toolbox.rename_rules import apply_rules, join_name, split_name

_MAX_FILES = 2000
_EXT_GROUPS = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"),
    "video": (".mp4", ".flv", ".mkv", ".avi", ".mov", ".ts", ".webm"),
    "audio": (".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg"),
    "subtitle": (".srt", ".ass", ".vtt", ".lrc"),
    "doc": (".txt", ".md", ".doc", ".docx", ".pdf", ".xlsx"),
}


def _cfg_from_options(options: dict) -> dict:
    options = options or {}

    def text(key: str, default: str = "") -> str:
        return str(options.get(key) or default)

    def flag(key: str) -> bool:
        return bool(options.get(key))

    def integer(key: str, default: int) -> int:
        try:
            return int(options.get(key))
        except (TypeError, ValueError):
            return default

    return {
        "whole": {"case": "", "num": "", "convert": "",
                  "ext_case": "", "new_ext": ""},
        "replace": {"find": text("find"), "to": text("to"),
                    "all": True, "case": False, "regex": False},
        "add": {"prefix": text("prefix"), "suffix": text("suffix"),
                "pos_idx": 0, "pos_text": "", "from_right": False,
                "kw": "", "kw_text": "", "kw_after": True},
        "del": {"types": []},
        "num": {"on": flag("numbering"), "mode": "suffix",
                "start": max(0, integer("start", 1)), "step": 1,
                "width": max(1, min(6, integer("width", 2)))},
        "template": text("template"),
        "options": {"ignore_ext": options.get("ignore_ext", True) is not False,
                    "conflict": options.get("conflict", True) is not False},
    }


def _scan(path: str, recursive: bool, filter_key: str) -> tuple[list[Path], bool]:
    root = Path(str(path or "").strip())
    if not path or not root.is_dir():
        raise ValueError("文件夹不存在或不可访问")
    allowed = _EXT_GROUPS.get(filter_key)
    found: list[Path] = []
    truncated = False
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                candidate = Path(dirpath) / name
                if allowed and candidate.suffix.lower() not in allowed:
                    continue
                found.append(candidate)
                if len(found) >= _MAX_FILES:
                    truncated = True
                    return found, truncated
    else:
        for candidate in sorted(root.iterdir()):
            if not candidate.is_file():
                continue
            if allowed and candidate.suffix.lower() not in allowed:
                continue
            found.append(candidate)
            if len(found) >= _MAX_FILES:
                truncated = True
                return found, truncated
    return found, truncated


def preview(path: str, options: dict | None = None) -> dict:
    options = options or {}
    recursive = bool(options.get("recursive"))
    filter_key = str(options.get("filter") or "all")
    if filter_key not in tuple(["all", *_EXT_GROUPS]):
        filter_key = "all"
    files, truncated = _scan(path, recursive, filter_key)
    cfg = _cfg_from_options(options)
    ignore_ext = cfg["options"]["ignore_ext"]
    conflict = cfg["options"]["conflict"]
    now = datetime.now()
    rows = []
    used: dict[str, int] = {}
    changed = 0
    name_counts: dict[str, int] = {}
    for item in files:
        key = item.name.lower()
        name_counts[key] = name_counts.get(key, 0) + 1
    for index, file_path in enumerate(files):
        name = file_path.name
        stem, ext = split_name(name, ignore_ext)
        new_stem, new_ext = apply_rules(stem, ext, cfg, index, now)
        if not new_stem or new_stem in (".", ".."):
            new_stem = stem or "未命名"
        new_name = join_name(new_stem, new_ext)
        status = "不变"
        if conflict:
            key = new_name.lower()
            same_other = name_counts.get(key, 0) - (
                1 if key == name.lower() else 0)
            if key in used or same_other > 0:
                used[key] = used.get(key, 0) + 1
                dot = new_name.rfind(".")
                base, suffix = ((new_name[:dot], new_name[dot:])
                                if dot > 0 else (new_name, ""))
                new_name = f"{base}_{used[key]}{suffix}"
                status = "冲突已加序号"
        if new_name != name:
            changed += 1
            if status == "不变":
                status = "将改名"
        try:
            size = int(file_path.stat().st_size)
        except OSError:
            size = 0
        rows.append({
            "index": index + 1,
            "old": name,
            "new": new_name,
            "status": status,
            "ext": new_ext or "（无）",
            "size": size,
        })
    return {
        "path": str(Path(path).resolve()),
        "rows": rows,
        "total": len(rows),
        "changed": changed,
        "truncated": truncated,
        "recursive": recursive,
        "filter": filter_key,
        "note": "只读预览；执行改名仍在桌面端。",
    }


def overview() -> dict:
    from toolbox.config_manager import load_convert_config

    cfg = load_convert_config()
    work_dir = str(cfg.get("work_dir") or "").strip()
    return {
        "work_dir": work_dir,
        "filters": [{"key": "all", "name": "全部文件"}]
        + [{"key": key, "name": name}
           for key, name in (("image", "图片"), ("video", "视频"),
                             ("audio", "音频"), ("subtitle", "字幕"),
                             ("doc", "文档"))],
        "note": "只读预览；执行改名仍在桌面端。",
    }
