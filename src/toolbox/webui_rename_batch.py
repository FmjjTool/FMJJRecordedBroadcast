"""WebUI 批量改名：扫描文件夹、按规则生成预览、执行改名。

规则引擎与桌面共用 `toolbox.rename_rules`；执行前需二次确认。
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
_ILLEGAL_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


def _name_error(name: str) -> str:
    if not name or name in (".", ".."):
        return "文件名为空"
    if any(ch in _ILLEGAL_CHARS for ch in name):
        return "文件名包含非法字符"
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        return "文件名是系统保留名"
    if len(name) > 240:
        return "文件名过长"
    return ""


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

    case = text("case")
    if case not in ("upper", "lower", "capital"):
        case = ""
    convert = text("convert")
    if convert not in ("full2half", "half2full"):
        convert = ""
    pos_text = text("pos_text")
    valid_types = {"space", "digit", "lower", "upper", "chinese", "special"}
    del_types = [str(item) for item in (options.get("del_types") or [])
                 if str(item) in valid_types]
    return {
        "whole": {"case": case, "num": "", "convert": convert,
                  "ext_case": "", "new_ext": ""},
        "replace": {"find": text("find"), "to": text("to"),
                    "all": True, "case": False, "regex": False},
        "add": {"prefix": text("prefix"), "suffix": text("suffix"),
                "pos_idx": max(0, integer("pos_idx", 0))
                if pos_text else 0,
                "pos_text": pos_text, "from_right": flag("from_right"),
                "kw": "", "kw_text": "", "kw_after": True},
        "del": {"types": del_types,
                "pos": max(0, integer("del_pos", 0)),
                "cnt": max(0, integer("del_cnt", 0)),
                "from_right": flag("del_from_right"),
                "kw": text("del_kw"),
                "kw_cnt": max(0, integer("del_kw_cnt", 0)),
                "dir_right": text("del_dir", "right") != "left",
                "incl_kw": flag("del_incl")},
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
    root = Path(str(path or "").strip())
    if not path or not root.is_dir():
        raise ValueError("文件夹不存在或不可访问")
    root = root.resolve()
    files, truncated = _scan(str(root), recursive, filter_key)
    cfg = _cfg_from_options(options)
    ignore_ext = cfg["options"]["ignore_ext"]
    conflict = cfg["options"]["conflict"]
    text_lines = [line.strip() for line in
                  str(options.get("textmode") or "").splitlines()]
    now = datetime.now()
    rows = []
    used: dict[tuple[str, str], int] = {}
    changed = 0
    name_counts: dict[tuple[str, str], int] = {}
    for item in files:
        key = (_rel_parent(item, root), item.name.lower())
        name_counts[key] = name_counts.get(key, 0) + 1
    for index, file_path in enumerate(files):
        name = file_path.name
        rel = str(file_path.relative_to(root))
        rel_parent = _rel_parent(file_path, root)
        stem, ext = split_name(name, ignore_ext)
        new_stem, new_ext = apply_rules(stem, ext, cfg, index, now)
        if not new_stem or new_stem in (".", ".."):
            new_stem = stem or "未命名"
        new_name = join_name(new_stem, new_ext)
        status = "不变"
        if conflict:
            key = (rel_parent, new_name.lower())
            same_other = name_counts.get(key, 0) - (
                1 if key == (rel_parent, name.lower()) else 0)
            if key in used or same_other > 0:
                used[key] = used.get(key, 0) + 1
                dot = new_name.rfind(".")
                base, suffix = ((new_name[:dot], new_name[dot:])
                                if dot > 0 else (new_name, ""))
                new_name = f"{base}_{used[key]}{suffix}"
                status = "冲突已加序号"
        override = text_lines[index] if index < len(text_lines) else ""
        if override:
            new_name = override
            status = "文本模式" if override != name else "不变"
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
            "rel": rel,
            "new": new_name,
            "status": status,
            "ext": new_ext or "（无）",
            "size": size,
        })
    _mark_duplicates(rows)
    return {
        "path": str(root),
        "rows": rows,
        "total": len(rows),
        "changed": changed,
        "truncated": truncated,
        "recursive": recursive,
        "filter": filter_key,
        "note": "预览不执行改名；执行前会再次校验并二次确认。",
    }


def _rel_parent(path: Path, root: Path) -> str:
    parent = path.parent
    try:
        return str(parent.relative_to(root)).lower()
    except ValueError:
        return ""


def _mark_duplicates(rows: list[dict]) -> None:
    """同一目录下最终重名的行标记出来（执行时会跳过）。"""
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        rel_parent = str(Path(row["rel"]).parent).lower()
        key = (rel_parent, row["new"].lower())
        counts[key] = counts.get(key, 0) + 1
    for row in rows:
        rel_parent = str(Path(row["rel"]).parent).lower()
        key = (rel_parent, row["new"].lower())
        if counts[key] > 1 and row["new"] != row["old"]:
            row["status"] = "重名，执行会跳过"



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
        "note": "预览不执行改名；执行前会再次校验并二次确认。",
    }


def execute(path: str, options: dict | None = None) -> dict:
    """执行改名：重算预览后两阶段改名（先临时名，失败回滚）。"""
    options = options or {}
    root = Path(str(path or "").strip())
    if not path or not root.is_dir():
        raise ValueError("文件夹不存在或不可访问")
    root = root.resolve()
    result = preview(str(root), options)
    plan = []
    failures = []
    for row in result["rows"]:
        if row["new"] == row["old"]:
            continue
        error = _name_error(row["new"])
        if error:
            failures.append({"old": row["old"], "error": error})
            continue
        source = root / row["rel"]
        if not source.is_file():
            failures.append({"old": row["old"], "error": "源文件不存在"})
            continue
        plan.append({"old": row["old"], "rel": row["rel"],
                     "new": row["new"]})

    sources = {(str(Path(item["rel"]).parent).lower(), item["old"].lower())
               for item in plan}
    seen: set[tuple[str, str]] = set()
    valid = []
    for item in plan:
        parent = Path(item["rel"]).parent
        key = (str(parent).lower(), item["new"].lower())
        if key in seen:
            failures.append({"old": item["old"], "error": "目标文件名重复"})
            continue
        target = root / parent / item["new"]
        if target.exists() and key not in sources:
            failures.append({"old": item["old"], "error": "目标已存在"})
            continue
        seen.add(key)
        valid.append(item)

    renamed = 0
    temp_paths: dict[str, Path] = {}
    temp_names: dict[str, Path] = {}
    try:
        for index, item in enumerate(valid):
            source = root / item["rel"]
            temp = source.parent / f".fmjj_rename_{os.getpid()}_{index}"
            if temp.exists():
                raise OSError("临时文件名冲突")
            source.rename(temp)
            temp_paths[item["rel"]] = temp
        for item in valid:
            temp = temp_paths[item["rel"]]
            final = root / Path(item["rel"]).parent / item["new"]
            temp.rename(final)
            temp_names[item["rel"]] = final
            renamed += 1
    except Exception as exc:  # noqa: BLE001 - 回滚后统一转成可读错误
        for item in reversed(valid):
            final = temp_names.get(item["rel"])
            temp = temp_paths.get(item["rel"])
            original = root / item["rel"]
            try:
                if temp is not None and temp.exists():
                    temp.rename(original)
                elif final is not None and final.exists():
                    final.rename(original)
            except OSError:
                pass
        raise ValueError(f"改名失败，已尝试回滚：{str(exc)[:120]}") from exc
    return {"path": str(root), "renamed": renamed, "failed": failures,
            "total": len(valid) + len(failures), "ok": True}
