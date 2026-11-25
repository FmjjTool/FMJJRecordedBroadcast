"""WebUI 工具用例（L3）：音频提取、字幕转换、日历、迁移的扫描与执行。

只允许工作目录内的文件；输出沿用桌面引擎（音频同目录、按音轨编码；
字幕按配置输出到原目录或统一导出目录）。转换只生成新文件，输出与源文件
同名时跳过，避免覆盖源文件。

本模块是 Web 专用中间层，与桌面端隔离：只依赖 L4 引擎 / L5 设施，
不 import PySide6 / gui_qt，不改桌面页面行为。文件迁移复用桌面
「文件迁移」页的合并语义：文件级合并、同名跳过记录重复、移动后
清理空目录；Web 端不提供重复项的覆盖 / 删除本地处理。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from toolbox import audio_engine
from toolbox.config_manager import get_export_subdir, load_convert_config
from toolbox.date_folders import today_str
from toolbox.subtitle_engine import SubtitleConverter
from toolbox.utils import VIDEO_EXTS

_MAX_FILES = 2000
_MAX_TASK_FILES = 200
_SUBTITLE_EXTS = {".bcc", ".json", ".ass", ".srt", ".txt"}
_SUBTITLE_FORMAT_LABELS = {
    "text": "纯文字 TXT",
    "timed_text": "带时间轴 TXT",
    "lrc": "LRC",
    "ass": "ASS",
    "srt": "SRT",
}


class ToolCancelled(Exception):
    """任务被取消（由调用方在 is_cancelled 回调中确认）。"""


def audio_overview() -> dict:
    root = _work_root()
    files = _scan_videos(root)
    all_tracks = load_convert_config().get("audio_extract_mode") == "all"
    return {
        "work_dir": root.name if root else "",
        "has_work_dir": bool(root),
        "all_tracks": bool(all_tracks),
        "mode": "全部音轨" if all_tracks else "第一条音轨",
        "files": len(files),
        "limited": len(files) >= _MAX_FILES,
        "note": "输出与桌面端一致：视频同目录，按音轨编码生成音频文件；只处理工作目录内的视频。",
    }


def list_audio_sources(limit: int = 200) -> dict:
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    limit = max(1, min(500, int(limit)))
    files = _scan_videos(root)
    items = []
    for path in files[:limit]:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "path": _rel(root, path),
            "name": path.name,
            "dir": path.parent.name,
            "size": int(stat.st_size),
            "mtime": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(stat.st_mtime)),
        })
    return {"items": items, "total": len(files),
            "limited": len(files) >= _MAX_FILES,
            "truncated": len(files) > limit,
            "note": "只列出工作目录下的视频文件。"}


def extract_audio(rel_paths, all_tracks: bool = False, on_progress=None,
                  is_cancelled=None) -> dict:
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    raw = [str(item or "").strip() for item in (rel_paths or [])]
    raw = [item for item in raw if item][:_MAX_TASK_FILES]
    if not raw:
        raise ValueError("请选择要提取的视频")
    paths = [_resolve(root, rel) for rel in raw]
    results = []
    ok = 0
    for index, path in enumerate(paths, 1):
        if is_cancelled and is_cancelled():
            raise ToolCancelled("任务已取消")
        rel = _rel(root, path)
        try:
            outputs = audio_engine.extract_audio_tracks(
                path, all_tracks=bool(all_tracks))
            results.append({"file": rel, "ok": True,
                            "outputs": [item.name for item in outputs]})
            ok += 1
        except Exception as exc:
            results.append({"file": rel, "ok": False,
                            "error": str(exc)[:300]})
        if on_progress:
            on_progress(index, len(paths), rel)
    return {"summary": f"完成 {ok}/{len(paths)} 个文件",
            "results": results, "ok": ok, "failed": len(paths) - ok}


def subtitle_overview() -> dict:
    root = _work_root()
    files = _scan_by_exts(root, _SUBTITLE_EXTS)
    cfg = load_convert_config()
    configured = [fmt for fmt in (cfg.get("subtitle_formats") or [])
                  if fmt in _SUBTITLE_FORMAT_LABELS]
    if not configured:
        configured = ["text"]
    same_dir = bool(cfg.get("subtitle_use_same_dir", True))
    return {
        "work_dir": root.name if root else "",
        "has_work_dir": bool(root),
        "files": len(files),
        "limited": len(files) >= _MAX_FILES,
        "formats": [{"key": key, "label": label}
                    for key, label in _SUBTITLE_FORMAT_LABELS.items()],
        "default_formats": configured,
        "output": "原文件同目录" if same_dir else "统一导出目录/字幕",
        "note": "转换只生成新文件；输出与源文件同名时跳过，避免覆盖源文件。",
    }


def list_subtitle_sources(limit: int = 200) -> dict:
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    limit = max(1, min(500, int(limit)))
    files = _scan_by_exts(root, _SUBTITLE_EXTS)
    return {"items": _file_items(root, files[:limit]), "total": len(files),
            "limited": len(files) >= _MAX_FILES,
            "truncated": len(files) > limit,
            "note": "支持的字幕格式：" + " / ".join(sorted(_SUBTITLE_EXTS))}


def convert_subtitles(rel_paths, formats, on_progress=None,
                      is_cancelled=None) -> dict:
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    chosen = [str(item) for item in (formats or [])
              if str(item) in _SUBTITLE_FORMAT_LABELS]
    if not chosen:
        raise ValueError("请至少选择一种输出格式")
    raw = [str(item or "").strip() for item in (rel_paths or [])]
    raw = [item for item in raw if item][:_MAX_TASK_FILES]
    if not raw:
        raise ValueError("请选择要转换的字幕文件")
    paths = [_resolve_subtitle(root, rel) for rel in raw]
    cfg = load_convert_config()
    same_dir = bool(cfg.get("subtitle_use_same_dir", True))
    output_dir = None
    if not same_dir:
        output_dir = get_export_subdir("subtitle")
        if cfg.get("subtitle_use_date_folder"):
            output_dir = output_dir / today_str()
        output_dir.mkdir(parents=True, exist_ok=True)
    converter = SubtitleConverter()
    results = []
    ok = 0
    for index, path in enumerate(paths, 1):
        if is_cancelled and is_cancelled():
            raise ToolCancelled("任务已取消")
        rel = _rel(root, path)
        try:
            content = path.read_bytes()
            result = converter.convert_file(path.name, content, chosen)
            if not result.get("success"):
                raise RuntimeError(result.get("message") or "转换失败")
            outputs = []
            skipped = []
            for item in result.get("output_files") or []:
                target = (path.parent if same_dir else output_dir) / item["filename"]
                if target.resolve() == path.resolve():
                    skipped.append(item["filename"])
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = item.get("content")
                if isinstance(payload, str):
                    target.write_text(payload, encoding="utf-8")
                else:
                    target.write_bytes(payload or b"")
                outputs.append(target.name)
            results.append({"file": rel, "ok": True, "outputs": outputs,
                            "skipped": skipped})
            ok += 1
        except Exception as exc:
            results.append({"file": rel, "ok": False, "error": str(exc)[:300]})
        if on_progress:
            on_progress(index, len(paths), rel)
    return {"summary": f"完成 {ok}/{len(paths)} 个文件",
            "results": results, "ok": ok, "failed": len(paths) - ok}


def video_overview() -> dict:
    root = _work_root()
    files = _scan_videos(root)
    cfg = load_convert_config()
    ext = str(cfg.get("output_extension") or ".mp4").lower()
    return {
        "has_work_dir": bool(root),
        "work_dir": root.name if root else "",
        "output_extension": ext,
        "files": len(files),
        "limited": len(files) >= _MAX_FILES,
        "note": "只生成新文件，不删除、不改写源视频；输出与原文件同目录；"
                "目标格式与源相同则跳过。默认输出格式在桌面端全局设置修改。",
    }


def list_video_sources(limit: int = 200) -> dict:
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    limit = max(1, min(500, int(limit)))
    files = _scan_videos(root)
    return {"items": _file_items(root, files[:limit]), "total": len(files),
            "limited": len(files) >= _MAX_FILES,
            "truncated": len(files) > limit,
            "note": "只列出工作目录下的视频文件。"}


def convert_videos(rel_paths, on_progress=None, is_cancelled=None) -> dict:
    """批量格式转换（无损优先，按桌面转换配置；强制同目录、不删源）。"""
    from toolbox.convert_engine import convert_one_file

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    raw = [str(item or "").strip() for item in (rel_paths or [])]
    raw = [item for item in raw if item][:_MAX_TASK_FILES]
    if not raw:
        raise ValueError("请选择要转换的视频")
    paths = [_resolve(root, rel) for rel in raw]
    cfg = dict(load_convert_config())
    cfg["output_dir"] = ""
    cfg["delete_source_after_success"] = False
    cfg["preserve_timestamps"] = True
    results = []
    ok = 0
    for index, path in enumerate(paths, 1):
        if is_cancelled and is_cancelled():
            raise ToolCancelled("任务已取消")
        rel = _rel(root, path)
        result = convert_one_file(
            path, root, cfg,
            should_stop=lambda: bool(is_cancelled and is_cancelled()))
        if getattr(result, "interrupted", False):
            raise ToolCancelled("任务已取消")
        success = bool(getattr(result, "success", False))
        results.append({
            "file": rel,
            "ok": success,
            "mode": getattr(result, "mode", "") or "",
            "output": getattr(getattr(result, "dst", None), "name", "") or "",
            "error": (getattr(result, "error_detail", "") or
                      getattr(result, "error_type", "") or ""),
        })
        ok += 1 if success else 0
        if on_progress:
            on_progress(index, len(paths), rel)
    return {"summary": f"完成 {ok}/{len(paths)} 个文件",
            "results": results, "ok": ok, "failed": len(paths) - ok}


def organize_overview() -> dict:
    from toolbox.organize_engine import scan_files

    root = _work_root()
    if root is None:
        return {"has_work_dir": False, "work_dir": "", "files": 0,
                "groups": 0, "no_date": 0,
                "note": "尚未设置工作目录，请先在桌面端全局设置中配置。"}
    groups, no_date = scan_files(str(root))
    files = sum(len(items) for items in groups.values()) + len(no_date)
    return {"has_work_dir": True, "work_dir": root.name,
            "files": files, "groups": len(groups),
            "no_date": len(no_date),
            "note": "只读预览：按文件名日期分组；执行整理"
                    "（移动视频到日期文件夹）仍在桌面端。"}


def list_organize_preview(limit: int = 100) -> dict:
    from toolbox.organize_engine import scan_files

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    limit = max(1, min(500, int(limit)))
    groups, no_date = scan_files(str(root))
    rows = []
    for date_str in sorted(groups)[:limit]:
        items = groups[date_str]
        size = sum(int(item.get("size") or 0) for item in items)
        rows.append({
            "date": date_str,
            "count": len(items),
            "size": size,
            "files": [str(item.get("name") or "")
                      for item in items][:50],
        })
    return {"groups": rows,
            "no_date": [str(name) for name in no_date][:200],
            "note": "只读预览：不会移动或改名；执行整理需在桌面端操作。"}


def organize_run(dates, on_progress=None, is_cancelled=None) -> dict:
    """执行整理：移动选中日期分组的视频到日期文件夹（复用桌面引擎）。

    不信任客户端提交的文件列表——执行前重新扫描，仅按选中的日期键
    过滤；无日期文件不参与（桌面端同口径）。
    """
    from toolbox.organize_engine import execute_organize, scan_files

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    chosen = [str(item or "").strip() for item in (dates or [])]
    chosen = [item for item in chosen if item]
    if not chosen:
        raise ValueError("请选择要整理的日期分组")
    groups, _no_date = scan_files(str(root))
    selected = {}
    for date_str in chosen:
        items = groups.get(date_str)
        if not items:
            continue
        selected[date_str] = items
    if not selected:
        raise ValueError("所选分组没有可整理的视频（可能已整理）")
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    total = sum(len(items) for items in selected.values())
    if on_progress:
        on_progress(0, total, "开始整理")
    success, failed, failed_files = execute_organize(str(root), selected)
    if on_progress:
        on_progress(total, total, "整理完成")
    return {"summary": f"整理完成：成功 {success} 个，失败 {failed} 个",
            "moved": int(success), "failed": int(failed),
            "failed_files": [str(name) for name in failed_files][:100],
            "note": "视频已移动到对应日期文件夹；附属文件随视频一起移动。"}


def rename_overview() -> dict:
    from toolbox.rename_engine import is_standard_recording_name

    root = _work_root()
    files = _scan_videos(root)
    standard = sum(1 for path in files
                   if is_standard_recording_name(path.name))
    return {
        "has_work_dir": bool(root),
        "work_dir": root.name if root else "",
        "files": len(files),
        "standard": standard,
        "nonstandard": len(files) - standard,
        "limited": len(files) >= _MAX_FILES,
        "note": "只读预览：提取日期 / 时间 / 标题与平台；完整改名"
                "（账户、类型、弹幕、版权）需桌面端「命名投稿」页。",
    }


def list_rename_preview(limit: int = 200) -> dict:
    from toolbox.rename_engine import (extract_file_title, extract_time_str,
                                       is_standard_recording_name)
    from toolbox.utils import norm_platform_dir

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    limit = max(1, min(500, int(limit)))
    files = _scan_videos(root)
    items = []
    for path in files[:limit]:
        rel = _rel(root, path)
        platform = ""
        for part in Path(rel).parts[:-1]:
            platform = norm_platform_dir(part)
            if platform:
                break
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
        items.append({
            "path": rel,
            "name": path.name,
            "dir": path.parent.name,
            "platform": platform,
            "date": date_match.group(1) if date_match else "",
            "time": extract_time_str(path.name),
            "title": extract_file_title(path.name),
            "standard": is_standard_recording_name(path.name),
        })
    return {"items": items, "total": len(files),
            "limited": len(files) >= _MAX_FILES,
            "truncated": len(files) > limit,
            "note": "只读预览，不会改名；最终名称需在桌面端确认"
                    "账户 / 类型 / 弹幕后生成。"}


def calendar_days() -> dict:
    """列出工作目录下的日期文件夹（名称、日期、文件数与总大小）。"""
    from toolbox.date_folders import scan_date_folders

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    rows = []
    for name, date_text in scan_date_folders(str(root)):
        files, total = _folder_brief(root / name)
        rows.append({"folder": name, "date": date_text,
                     "files": files, "size": total})
    rows.sort(key=lambda row: row["folder"], reverse=True)
    return {"days": rows, "total": len(rows),
            "note": "按日期文件夹浏览工作目录；点击日期查看当天文件。"}


def calendar_day_files(folder: str, limit: int = 500) -> dict:
    """列出某个日期文件夹内的文件（名称、大小、修改时间）。"""
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    name = str(folder or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("日期文件夹名不合法")
    from toolbox.date_folders import is_date_folder
    if not is_date_folder(name):
        raise ValueError("不是日期文件夹")
    target = root / name
    if not target.is_dir():
        raise ValueError("日期文件夹不存在")
    limit = max(1, min(1000, int(limit)))
    files = []
    for path in sorted(target.iterdir()):
        if len(files) >= limit:
            break
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({"name": path.name, "size": int(stat.st_size),
                      "mtime": time.strftime("%Y-%m-%d %H:%M",
                                             time.localtime(stat.st_mtime))})
    total = sum(1 for path in target.iterdir() if path.is_file())
    return {"folder": name, "files": files, "total": total,
            "truncated": total > limit,
            "note": "只列出该日期文件夹内的文件；打开文件请在桌面端操作。"}


def launcher_overview() -> dict:
    """继续操作：只读列出快捷项（名称、类型、路径状态）。"""
    cfg = load_convert_config()
    items = cfg.get("launcher_items") or []
    rows = []
    for index, item in enumerate(items, 1):
        item = item or {}
        path = str(item.get("path") or "")
        rows.append({
            "index": index,
            "name": item.get("name") or f"软件{index}",
            "type": "link" if item.get("type") == "link" else "app",
            "exists": bool(path) and Path(path).exists(),
        })
    return {"items": rows, "total": len(rows),
            "note": "只读展示快捷项；启动外部软件请在桌面端「继续操作」页。"}


def _folder_brief(folder: Path) -> tuple[int, int]:
    try:
        entries = [item for item in folder.iterdir() if item.is_file()]
    except OSError:
        return 0, 0
    size = 0
    for item in entries:
        try:
            size += item.stat().st_size
        except OSError:
            pass
    return len(entries), size


# ==================== 文件迁移（复用桌面合并语义，独立实现） ====================

_MAX_MIGRATE_FOLDERS = 200


def migrate_overview() -> dict:
    """迁移概览：工作目录、目标目录与模式说明。"""
    cfg = load_convert_config()
    root = _work_root()
    target = str(cfg.get("migrate_target_dir") or "").strip()
    return {
        "has_work_dir": root is not None,
        "work_dir": root.name if root else "",
        "target_dir": Path(target).name if target else "",
        "has_target_dir": bool(target) and Path(target).is_dir(),
        "note": ("复制保留源文件，剪切移动并在迁移后清理空目录；"
                 "同名文件跳过并计入重复，不覆盖目标。"
                 "目标目录请在桌面端「文件迁移」页设置。"),
    }


def migrate_scan() -> dict:
    """扫描工作目录下可迁移的日期文件夹（名称、日期、文件数、大小）。"""
    from toolbox.date_folders import is_date_folder

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    rows = []
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        raise ValueError(f"无法读取工作目录: {exc}") from exc
    today = today_str()
    for name in names:
        if not is_date_folder(name):
            continue
        files, size = _folder_stats_deep(root / name)
        rows.append({
            "folder": name,
            "date": _folder_date_text(name),
            "files": files,
            "size": size,
            "is_today": name[:8] == today,
        })
    return {"folders": rows, "total": len(rows),
            "today": today,
            "note": "默认勾选当天；迁移按文件级合并，同名跳过计重复。"}


def migrate_run(mode: str, folders, on_progress=None,
                is_cancelled=None) -> dict:
    """执行迁移：文件级合并（同名跳过记重复），move 后清理空目录。"""
    import shutil

    if mode not in ("copy", "move"):
        raise ValueError("迁移模式只支持复制或剪切")
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    cfg = load_convert_config()
    target_text = str(cfg.get("migrate_target_dir") or "").strip()
    if not target_text:
        raise ValueError("尚未设置迁移目标目录，请先在桌面端「文件迁移」页选择")
    target = Path(target_text)
    if not target.is_dir():
        raise ValueError("迁移目标目录不存在")
    if _paths_nested(root, target):
        raise ValueError("目标目录不能是工作目录的父目录/子目录（会递归迁移）")
    raw = [str(item or "").strip() for item in (folders or [])]
    raw = [item for item in raw if item][:_MAX_MIGRATE_FOLDERS]
    if not raw:
        raise ValueError("请选择要迁移的日期文件夹")
    from toolbox.date_folders import is_date_folder
    for name in raw:
        if not is_date_folder(name):
            raise ValueError(f"不是日期文件夹: {name}")

    results = []
    ok = 0
    duplicates = []
    for index, name in enumerate(raw, 1):
        if is_cancelled and is_cancelled():
            raise ToolCancelled("任务已取消")
        src_dir = root / name
        dst_dir = target / name
        if not src_dir.is_dir():
            results.append({"folder": name, "ok": False,
                            "error": "源文件夹不存在"})
            continue
        moved = 0
        dup = 0
        error = ""
        for file_root, dirs, files in os.walk(src_dir):
            rel_root = os.path.relpath(file_root, src_dir)
            dst_root = dst_dir if rel_root == "." else dst_dir / rel_root
            for file_name in files:
                if is_cancelled and is_cancelled():
                    raise ToolCancelled("任务已取消")
                src_file = Path(file_root) / file_name
                dst_file = dst_root / file_name
                if dst_file.exists():
                    duplicates.append({
                        "folder": name,
                        "name": str(src_file.relative_to(src_dir)).replace("\\", "/"),
                    })
                    dup += 1
                    continue
                try:
                    dst_root.mkdir(parents=True, exist_ok=True)
                    if mode == "copy":
                        shutil.copy2(str(src_file), str(dst_file))
                    else:
                        shutil.move(str(src_file), str(dst_file))
                    moved += 1
                except OSError as exc:
                    error = str(exc)[:300]
        if mode == "move" and not error:
            for file_root, dirs, files in os.walk(src_dir, topdown=False):
                try:
                    os.rmdir(file_root)
                except OSError:
                    pass
        ok += 1 if not error else 0
        results.append({"folder": name, "ok": not error,
                        "moved": moved, "duplicates": dup,
                        "error": error})
        if on_progress:
            on_progress(index, len(raw), name)
    total_moved = sum(row["moved"] for row in results)
    summary = (f"迁移 {ok}/{len(raw)} 个文件夹，共 {total_moved} 个文件，"
               f"重复跳过 {len(duplicates)} 个文件")
    return {"summary": summary, "results": results, "ok": ok,
            "failed": len(raw) - ok, "moved": total_moved,
            "duplicates": duplicates[:500]}


def _folder_stats_deep(folder: Path) -> tuple[int, int]:
    """递归统计文件夹的文件数与总大小（对齐桌面迁移页口径）。"""
    count = 0
    total = 0
    for file_root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                stat = (Path(file_root) / name).stat()
            except OSError:
                continue
            count += 1
            total += stat.st_size
    return count, total


def _folder_date_text(name: str) -> str:
    text = name[:8]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return name


def _paths_nested(a: Path, b: Path) -> bool:
    try:
        common = os.path.commonpath([str(a.resolve()), str(b.resolve())])
    except ValueError:
        return False
    return common == str(a.resolve()) or common == str(b.resolve())


def _work_root() -> Path | None:
    text = str(load_convert_config().get("work_dir") or "").strip()
    if not text:
        return None
    root = Path(text)
    return root if root.is_dir() else None


def _scan_videos(root: Path | None) -> list[Path]:
    return _scan_by_exts(root, VIDEO_EXTS)


def _scan_by_exts(root: Path | None, exts: set[str]) -> list[Path]:
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames
                             if not name.startswith("."))
        for name in sorted(filenames):
            if Path(name).suffix.lower() in exts:
                found.append(Path(dirpath) / name)
                if len(found) >= _MAX_FILES:
                    return sorted(found)
    return sorted(found)


def _file_items(root: Path, files: list[Path]) -> list[dict]:
    items = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "path": _rel(root, path),
            "name": path.name,
            "dir": path.parent.name,
            "size": int(stat.st_size),
            "mtime": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(stat.st_mtime)),
        })
    return items


def _resolve(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("文件不在工作目录内") from exc
    if not path.is_file():
        raise ValueError("文件不存在或已被移动")
    if path.suffix.lower() not in VIDEO_EXTS:
        raise ValueError("不是可提取的视频文件")
    return path


def _resolve_subtitle(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("文件不在工作目录内") from exc
    if not path.is_file():
        raise ValueError("文件不存在或已被移动")
    if path.suffix.lower() not in _SUBTITLE_EXTS:
        raise ValueError("不是支持的字幕文件")
    return path


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")