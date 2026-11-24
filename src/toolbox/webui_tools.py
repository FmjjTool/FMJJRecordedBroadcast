"""WebUI 工具用例（L3）：音频提取与字幕转换的扫描与执行。

只允许工作目录内的文件；输出沿用桌面引擎（音频同目录、按音轨编码；
字幕按配置输出到原目录或统一导出目录）。转换只生成新文件，输出与源文件
同名时跳过，避免覆盖源文件。
"""

from __future__ import annotations

import os
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