"""WebUI 文件时间修改（L3）：扫描文件夹、计算目标时间、预览与写入。

规则与桌面「批量改名 → 时间修改」页共用 `toolbox.file_time_rules`：
只改文件的修改 / 创建 / 访问时间，不改文件名与内容。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from toolbox.file_time_rules import compute_times, set_file_times
from toolbox.webui_rename_batch import _scan

_MODES = ("set", "offset", "name")
_FILTERS = (("all", "全部文件"), ("image", "图片"), ("video", "视频"),
            ("audio", "音频"), ("subtitle", "字幕"), ("doc", "文档"))


def _fmt(value: float | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _options(options: dict | None) -> dict:
    options = options or {}
    mode = str(options.get("mode") or "set")
    if mode not in _MODES:
        mode = "set"
    filter_key = str(options.get("filter") or "all")
    if filter_key not in tuple(key for key, _ in _FILTERS):
        filter_key = "all"
    return {
        "recursive": bool(options.get("recursive")),
        "filter": filter_key,
        "mode": mode,
        "value": str(options.get("value") or "").strip(),
        "offset": str(options.get("offset") or "").strip(),
    }


def overview() -> dict:
    from toolbox.config_manager import load_convert_config

    cfg = load_convert_config()
    return {
        "work_dir": str(cfg.get("work_dir") or "").strip(),
        "modes": [{"key": "set", "name": "指定时间"},
                  {"key": "offset", "name": "偏移量"},
                  {"key": "name", "name": "按文件名时间"}],
        "filters": [{"key": key, "name": name} for key, name in _FILTERS],
        "note": "只改文件时间（修改 / 创建 / 访问），不改文件名与内容；"
                "扫描范围沿用批量改名页的文件夹、子文件夹与类型筛选。",
    }


def _root(path: str) -> Path:
    root = Path(str(path or "").strip())
    if not path or not root.is_dir():
        raise ValueError("文件夹不存在或不可访问")
    return root.resolve()


def preview(path: str, options: dict | None = None) -> dict:
    opts = _options(options)
    root = _root(path)
    found, truncated = _scan(str(root), opts["recursive"], opts["filter"])
    rows = []
    ready = 0
    index = 0
    for file_path, kind in found:
        if kind != "file":
            continue
        index += 1
        try:
            stat = file_path.stat()
        except OSError:
            rows.append({"index": index, "rel": str(file_path.name),
                         "name": file_path.name, "mtime": "", "ctime": "",
                         "new_mtime": "文件不可访问", "new_ctime": "",
                         "status": "失败"})
            continue
        result = compute_times(str(file_path), opts["mode"], opts["value"],
                               opts["offset"])
        row = {
            "index": index,
            "rel": str(file_path.relative_to(root)),
            "name": file_path.name,
            "mtime": _fmt(stat.st_mtime),
            "ctime": _fmt(stat.st_ctime),
        }
        if isinstance(result, str):
            row.update({"new_mtime": result, "new_ctime": "", "status": "跳过"})
        else:
            atime, mtime, ctime = result
            row.update({
                "new_mtime": _fmt(mtime),
                "new_ctime": _fmt(ctime) or "（不变）",
                "status": "待应用",
            })
            ready += 1
        rows.append(row)
    return {
        "path": str(root),
        "rows": rows,
        "total": len(rows),
        "ready": ready,
        "truncated": truncated,
        "mode": opts["mode"],
        "note": "预览只计算时间，不写盘；应用前会再次计算并二次确认。",
    }


def apply(path: str, options: dict | None = None) -> dict:
    opts = _options(options)
    root = _root(path)
    found, _truncated = _scan(str(root), opts["recursive"], opts["filter"])
    applied = 0
    failed = []
    for file_path, kind in found:
        if kind != "file":
            continue
        result = compute_times(str(file_path), opts["mode"], opts["value"],
                               opts["offset"])
        if isinstance(result, str):
            failed.append({"name": file_path.name, "error": result})
            continue
        atime, mtime, ctime = result
        try:
            set_file_times(str(file_path), atime, mtime, ctime)
        except OSError as exc:
            failed.append({"name": file_path.name, "error": str(exc)[:120]})
            continue
        applied += 1
    return {
        "path": str(root),
        "applied": applied,
        "failed": failed,
        "total": applied + len(failed),
        "ok": True,
    }
