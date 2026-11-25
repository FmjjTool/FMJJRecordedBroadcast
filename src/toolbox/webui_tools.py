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
import subprocess
import threading
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
# 与桌面「视频操作」页 COMPRESS_PRESETS 同值：标签, 清晰度, 压缩率。
_COMPRESS_PRESETS = {
    "light": ("轻度（画质优先）", 88, 12),
    "normal": ("标准", 75, 35),
    "strong": ("高强度（体积优先）", 60, 62),
}
_COMPRESS_RESOLUTIONS = (
    ("keep", "保持原始"),
    ("1920x1080", "1920x1080"),
    ("1280x720", "1280x720"),
    ("854x480", "854x480"),
)
_MAX_CUT_SEGMENTS = 50


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
        "note": "只生成新文件，不删除、不改写源视频；压缩 / 截图输出到源视频同目录，"
                "合并 / 切割输出到工作目录根；已有同名文件自动加序号，不覆盖。"
                "转换输出格式与跳过规则跟随桌面端全局设置。",
        "compress_presets": [
            {"value": key, "label": label}
            for key, (label, _q, _r) in _COMPRESS_PRESETS.items()
        ],
        "resolutions": [
            {"value": value, "label": label}
            for value, label in _COMPRESS_RESOLUTIONS
        ],
        "merge_modes": [
            {"value": "copy", "label": "无损拼接（推荐）"},
            {"value": "encode", "label": "兼容转码（参数不一致时）"},
        ],
        "merge_exts": [".mp4", ".mkv"],
        "cut_exports": [
            {"value": "each", "label": "逐段导出"},
            {"value": "merge", "label": "拼接成一个文件"},
            {"value": "complement", "label": "删除选中片段（导出剩余）"},
        ],
        "cut_modes": [
            {"value": "copy", "label": "无损（关键帧边界，快）"},
            {"value": "precise", "label": "精确（重编码，准）"},
        ],
        "snapshot_formats": [
            {"value": "jpg", "label": "JPG"},
            {"value": "png", "label": "PNG"},
        ],
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


def validate_compress_options(preset: str, resolution: str) -> tuple[str, str]:
    """校验压缩预设与分辨率，返回规范化后的 (preset, resolution)。"""
    key = str(preset or "normal")
    if key not in _COMPRESS_PRESETS:
        raise ValueError("未知压缩强度")
    res = str(resolution or "keep")
    if res not in {value for value, _label in _COMPRESS_RESOLUTIONS}:
        raise ValueError("不支持的压缩分辨率")
    return key, res


def compress_videos(rel_paths, preset: str = "normal", resolution: str = "keep",
                    clear_metadata: bool = True, on_progress=None,
                    is_cancelled=None) -> dict:
    """批量压缩视频（ffmpeg 重编码，输出 <名>_compressed_video，源文件不动）。

    参数与桌面压缩预设一致：清晰度 / 压缩率换算 CRF，可选缩放分辨率与
    清除元数据。不 import 桌面的 QThread 实现，避免 WebUI 依赖 PySide6。
    """
    from toolbox.utils import find_ffmpeg_exe

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    key, resolution = validate_compress_options(preset, resolution)
    _label, quality, ratio = _COMPRESS_PRESETS[key]
    raw = [str(item or "").strip() for item in (rel_paths or [])]
    raw = [item for item in raw if item][:_MAX_TASK_FILES]
    if not raw:
        raise ValueError("请选择要压缩的视频")
    paths = [_resolve(root, rel) for rel in raw]
    ffmpeg_bin = find_ffmpeg_exe()
    if not ffmpeg_bin:
        raise ValueError("未找到 ffmpeg，请先在全局设置中配置 FFmpeg 位置")
    results = []
    ok = 0
    for index, path in enumerate(paths, 1):
        if is_cancelled and is_cancelled():
            raise ToolCancelled("任务已取消")
        rel = _rel(root, path)
        out = _compressed_output(path)
        try:
            before = int(path.stat().st_size)
        except OSError:
            before = 0
        try:
            _run_compress_ffmpeg(
                ffmpeg_bin, path, out, quality, ratio, resolution,
                bool(clear_metadata),
                lambda pct, idx=index, name=rel: _report(
                    on_progress, idx - 1 + pct, len(paths), name),
                is_cancelled)
            after = int(out.stat().st_size)
            results.append({"file": rel, "ok": True, "output": out.name,
                            "before": before, "after": after})
            ok += 1
        except ToolCancelled:
            _remove_quietly(out)
            raise
        except Exception as exc:
            _remove_quietly(out)
            results.append({"file": rel, "ok": False, "output": "",
                            "before": before, "after": 0,
                            "error": str(exc)[:300]})
        _report(on_progress, index, len(paths), rel)
    return {"summary": f"压缩完成 {ok}/{len(paths)} 个文件",
            "results": results, "ok": ok, "failed": len(paths) - ok}


def _run_compress_ffmpeg(ffmpeg_bin: str, src: Path, out: Path, quality: int,
                         ratio: int, resolution: str, clear_metadata: bool,
                         on_percent, is_cancelled) -> None:
    from toolbox.concat_engine import media_duration

    duration = media_duration(src)
    crf = max(16, min(35 - int(quality * 0.2) + int(ratio * 0.15), 42))
    webm = out.suffix.lower() == ".webm"
    cmd = [ffmpeg_bin, "-y", "-progress", "pipe:1", "-nostats",
           "-i", str(src)]
    if clear_metadata:
        cmd += ["-map_metadata", "-1"]
    if webm:
        cmd += ["-c:v", "libvpx-vp9", "-crf", str(crf)]
    else:
        cmd += ["-c:v", "libx264", "-crf", str(crf)]
    scale = _scale_filter(resolution)
    if scale:
        cmd += ["-vf", scale]
    if webm:
        cmd += ["-c:a", "libopus", "-b:a", "96k"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    if out.suffix.lower() in (".mp4", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out))

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="ignore", creationflags=creationflags)
    stderr_lines: list[str] = []

    def drain() -> None:
        try:
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    drain_thread = threading.Thread(target=drain, daemon=True)
    drain_thread.start()
    out_time_us = 0
    last_bucket = -1
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if line.startswith("out_time_us="):
                try:
                    out_time_us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if duration > 0 and on_percent:
                    pct = min(out_time_us / 1_000_000.0 / duration, 0.999)
                    bucket = int(pct * 100)
                    if bucket != last_bucket:
                        last_bucket = bucket
                        on_percent(pct)
            elif line.startswith("progress="):
                if is_cancelled and is_cancelled():
                    proc.kill()
                    proc.wait(timeout=5)
                    raise ToolCancelled("任务已取消")
        code = proc.wait()
    finally:
        drain_thread.join(timeout=2)
    if code != 0 or not out.is_file() or out.stat().st_size == 0:
        detail = "".join(stderr_lines).strip()[-300:]
        raise RuntimeError(detail or "ffmpeg 压缩失败")


def _compressed_output(src: Path) -> Path:
    ext = src.suffix.lower()
    candidate = src.with_name(f"{src.stem}_compressed_video{ext}")
    idx = 1
    while candidate.exists():
        candidate = src.with_name(f"{src.stem}_compressed_video_{idx}{ext}")
        idx += 1
    return candidate


def _scale_filter(resolution: str) -> str:
    text = str(resolution or "").strip().lower()
    if text in ("", "keep") or "x" not in text:
        return ""
    width, height = text.split("x", 1)
    try:
        return (f"scale={max(int(width), 2)}:{max(int(height), 2)}"
                ":force_original_aspect_ratio=decrease")
    except ValueError:
        return ""


def merge_videos(rel_paths, mode: str = "copy", output_name: str = "",
                 output_ext: str = ".mp4", on_progress=None,
                 is_cancelled=None) -> dict:
    """按顺序合并视频（复用桌面 concat_engine；输出到工作目录根）。"""
    from toolbox.concat_engine import (ConcatCancelled, ConcatError,
                                       MODE_COPY, MODE_ENCODE,
                                       check_compatibility, concat_media,
                                       probe_media, unique_output_path)

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    raw = [str(item or "").strip() for item in (rel_paths or [])]
    raw = [item for item in raw if item][:_MAX_TASK_FILES]
    if len(raw) < 2:
        raise ValueError("至少选择 2 个视频才能合并")
    paths = [_resolve(root, rel) for rel in raw]
    mode = str(mode or MODE_COPY)
    if mode not in (MODE_COPY, MODE_ENCODE):
        raise ValueError("未知合并方式")
    ext = str(output_ext or ".mp4").lower()
    if ext not in (".mp4", ".mkv"):
        raise ValueError("合并输出只支持 mp4 / mkv")
    name = str(output_name or "").strip() or \
        f"合并_{time.strftime('%Y%m%d_%H%M%S')}"
    name = re.sub(r'[\\/:*?"<>|]', " ", name).strip() or "合并视频"
    if Path(name).suffix.lower() != ext:
        name += ext
    out = unique_output_path(root / name)

    try:
        infos = [probe_media(path) for path in paths]
    except ConcatError as exc:
        raise ValueError(str(exc)) from exc
    problems = check_compatibility(infos)
    if problems and mode == MODE_COPY:
        note = "参数不一致，无损拼接可能失败或花屏，建议改用兼容转码"
    else:
        note = "编码参数一致" if not problems else "按兼容转码合并"
    if on_progress:
        on_progress(0, 1, f"开始合并 {len(paths)} 个文件")

    def progress(percent, message):
        _report(on_progress, max(0, min(100, int(percent))) / 100.0, 1,
                message or "合并中")

    try:
        concat_media(paths, out, mode, progress=progress,
                     cancel=lambda: bool(is_cancelled and is_cancelled()))
    except ConcatCancelled as exc:
        raise ToolCancelled(str(exc)) from exc
    except ConcatError as exc:
        raise RuntimeError(str(exc)) from exc
    return {"summary": f"合并完成：{out.name}（{note}）",
            "output": out.name, "problems": problems[:10], "ok": True}


def cut_video(rel_path: str, segments, mode: str = "copy",
              export: str = "each", merged_name: str = "",
              on_progress=None, is_cancelled=None) -> dict:
    """按片段切割单个视频（复用桌面 edit_engine；输出到工作目录根）。"""
    from toolbox.concat_engine import probe_media
    from toolbox.edit_engine import (EditCancelled, EditError,
                                     complement_segments, cut_segments)

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    mode = str(mode or "copy")
    if mode not in ("copy", "precise"):
        raise ValueError("未知切割方式")
    export = str(export or "each")
    if export not in ("each", "merge", "complement"):
        raise ValueError("未知导出方式")
    rows = []
    for item in segments or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("切割片段格式不正确")
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("切割时间必须是毫秒数") from exc
        if start < 0 or end <= start:
            raise ValueError("切割片段结束时间必须大于开始时间")
        rows.append((start, end))
    if not rows:
        raise ValueError("请至少填写一个切割片段")
    if len(rows) > _MAX_CUT_SEGMENTS:
        raise ValueError(f"单次最多切割 {_MAX_CUT_SEGMENTS} 个片段")
    try:
        total_ms = int(probe_media(path).duration * 1000)
    except Exception as exc:
        raise ValueError(f"无法读取视频时长：{exc}") from exc
    if total_ms <= 0:
        raise ValueError("无法读取视频时长，文件可能损坏")
    clamped = []
    for start, end in rows:
        start = max(0, min(start, total_ms))
        end = max(0, min(end, total_ms))
        if end > start:
            clamped.append((start, end))
    if not clamped:
        raise ValueError("切割片段超出视频时长")
    if export == "complement":
        clamped = complement_segments(clamped, total_ms)
        if not clamped:
            raise ValueError("选中片段覆盖了整段视频，没有可导出的内容")
    merge = export in ("merge", "complement")
    name = re.sub(r'[\\/:*?"<>|]', " ", str(merged_name or "")).strip()
    name = name or f"{path.stem}_片段合并"
    if not Path(name).suffix:
        name += ".mp4"
    if on_progress:
        on_progress(0, 1, f"开始切割 {len(clamped)} 段")

    def progress(percent, message):
        _report(on_progress, max(0, min(100, int(percent))) / 100.0, 1,
                message or "切割中")

    try:
        outputs = cut_segments(
            path, clamped, root, mode, merge=merge,
            merged_name=name if merge else "", progress=progress,
            cancel=lambda: bool(is_cancelled and is_cancelled()))
    except EditCancelled as exc:
        raise ToolCancelled(str(exc)) from exc
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    return {"summary": f"切割完成：输出 {len(outputs)} 个文件",
            "outputs": [item.name for item in outputs], "ok": True}


def snapshot_video(rel_path: str, mode: str = "single", position: str = "",
                   interval_sec: float = 0, image_format: str = "jpg",
                   on_progress=None, is_cancelled=None) -> dict:
    """截图：单帧导出到视频同目录，或按间隔抽帧到「<文件名>_frames」。"""
    from toolbox.edit_engine import (EditCancelled, EditError, snapshot,
                                     snapshot_every)

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    fmt = "png" if str(image_format or "").lower() == "png" else "jpg"
    mode = str(mode or "single")
    try:
        if mode == "interval":
            interval = float(interval_sec or 0)
            if interval < 0.1:
                raise ValueError("抽帧间隔至少 0.1 秒")
            if interval > 3600:
                raise ValueError("抽帧间隔不能超过 3600 秒")
            outputs = snapshot_every(
                path, interval, progress=lambda percent, message: _report(
                    on_progress,
                    max(0, min(100, int(percent))) / 100.0, 1,
                    message or "抽帧中"),
                cancel=lambda: bool(is_cancelled and is_cancelled()))
            return {"summary": f"已导出 {len(outputs)} 帧",
                    "folder": f"{path.stem}_frames",
                    "outputs": [item.name for item in outputs][:200],
                    "ok": True}
        if mode != "single":
            raise ValueError("未知截图方式")
        time_ms = _parse_time_ms(position)
        if time_ms is None:
            raise ValueError("时间点格式不正确，可用 90、01:30 或 00:01:30")
        out = snapshot(path, time_ms, path.parent, image_format=fmt)
        return {"summary": f"已截图：{out.name}", "outputs": [out.name],
                "ok": True}
    except EditCancelled as exc:
        raise ToolCancelled(str(exc)) from exc
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc


def _parse_time_ms(text) -> int | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parts = raw.split(":")
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = (int(parts[0]) * 3600 + int(parts[1]) * 60 +
                       float(parts[2]))
        else:
            return None
    except ValueError:
        return None
    if seconds < 0:
        return None
    return int(seconds * 1000)


def _report(on_progress, done, total, name) -> None:
    if on_progress:
        on_progress(done, total, name)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ==================== 视频操作：轨道 / 音轨字幕 / 元数据 / 章节 ====================

_AUDIO_EXTS = {".m4a", ".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".ac3"}
_SUBTITLE_VIDEO_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
_META_KEYS = ("title", "artist", "comment", "date")
_ROTATIONS = (0, 90, 180, 270)


def video_tracks(rel_path: str) -> dict:
    """列出视频内可提取的音轨 / 字幕轨（只读）。"""
    from toolbox.edit_engine import probe_tracks

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    tracks = []
    for stream in probe_tracks(path):
        tags = stream.get("tags") or {}
        tracks.append({
            "index": int(stream.get("index") or -1),
            "kind": "audio" if stream.get("codec_type") == "audio" else "subtitle",
            "codec": str(stream.get("codec_name") or ""),
            "language": str(tags.get("language") or ""),
            "title": str(tags.get("title") or ""),
        })
    return {"path": _rel(root, path), "tracks": tracks,
            "note": "提取或删除轨道都只生成新文件，不修改源视频。"}


def extract_video_tracks(rel_path: str, on_progress=None,
                         is_cancelled=None) -> dict:
    from toolbox.edit_engine import EditError, extract_all_tracks

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    try:
        outputs = extract_all_tracks(
            path, root,
            progress=lambda percent, message: _report(
                on_progress, max(0, min(100, int(percent))) / 100.0, 1,
                message or "提取轨道"))
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    return {"summary": f"已提取 {len(outputs)} 条轨道",
            "outputs": [item.name for item in outputs], "ok": True}


def remove_video_tracks(rel_path: str, indices, on_progress=None,
                        is_cancelled=None) -> dict:
    from toolbox.concat_engine import probe_media
    from toolbox.edit_engine import EditError, probe_tracks, remove_tracks

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    chosen = []
    for value in indices or []:
        try:
            chosen.append(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("轨道编号不正确") from exc
    chosen = sorted(set(chosen))
    if not chosen:
        raise ValueError("请先选择要删除的轨道")
    allowed = {int(item.get("index") or -1) for item in probe_tracks(path)}
    if any(index not in allowed for index in chosen):
        raise ValueError("所选轨道不存在，请刷新轨道列表")
    total = len(probe_media(path).streams)
    if total - len(chosen) <= 0:
        raise ValueError("不能删除全部轨道")
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    out = _video_output(path, "删轨")
    _report(on_progress, 0.3, 1, "正在删除轨道")
    try:
        result = remove_tracks(path, chosen, out)
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    _report(on_progress, 1, 1, "完成")
    return {"summary": f"已删除 {len(chosen)} 条轨道", "output": result.name,
            "outputs": [result.name], "ok": True}


def add_video_audio(rel_path: str, audio_rel: str, replace: bool = False,
                    on_progress=None, is_cancelled=None) -> dict:
    from toolbox.edit_engine import EditError, add_external_audio

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    audio = _resolve_extra(root, audio_rel, _AUDIO_EXTS, "音频")
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    tag = "替换音轨" if replace else "追加音轨"
    out = _video_output(path, tag)
    _report(on_progress, 0.3, 1, tag)
    try:
        result = add_external_audio(path, audio, out, replace=bool(replace))
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    _report(on_progress, 1, 1, "完成")
    return {"summary": f"{tag}完成：{result.name}",
            "output": result.name, "outputs": [result.name], "ok": True}


def add_video_subtitle(rel_path: str, subtitle_rel: str, on_progress=None,
                       is_cancelled=None) -> dict:
    from toolbox.edit_engine import EditError, add_external_subtitle

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    subtitle = _resolve_extra(root, subtitle_rel, _SUBTITLE_VIDEO_EXTS, "字幕")
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    out = _video_output(path, "加字幕")
    _report(on_progress, 0.3, 1, "正在添加字幕")
    try:
        result = add_external_subtitle(path, subtitle, out)
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    _report(on_progress, 1, 1, "完成")
    return {"summary": f"字幕已添加：{result.name}",
            "output": result.name, "outputs": [result.name], "ok": True}


def video_metadata(rel_path: str) -> dict:
    from toolbox.edit_engine import read_metadata

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    meta = read_metadata(path)
    return {"path": _rel(root, path),
            "tags": {key: str(meta.get(key) or "") for key in _META_KEYS},
            "rotation": int(meta.get("rotation") or 0),
            "rotations": list(_ROTATIONS),
            "note": "写入元数据会重新封装为新文件（不转码），源视频保留。"}


def write_video_metadata(rel_path: str, tags, rotation=0, on_progress=None,
                         is_cancelled=None) -> dict:
    from toolbox.edit_engine import EditError, write_metadata

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    raw = tags if isinstance(tags, dict) else {}
    values = {}
    for key in _META_KEYS:
        text = str(raw.get(key) or "").strip()
        if len(text) > 500:
            raise ValueError("元数据字段过长（每个最多 500 字）")
        values[key] = text
    try:
        rotation_value = int(rotation or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("旋转角度不正确") from exc
    if rotation_value not in _ROTATIONS:
        raise ValueError("旋转角度只支持 0 / 90 / 180 / 270")
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    out = _video_output(path, "meta")
    _report(on_progress, 0.3, 1, "正在写入元数据")
    try:
        result = write_metadata(path, out, values, rotation_value)
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    _report(on_progress, 1, 1, "完成")
    return {"summary": f"元数据已写入：{result.name}",
            "output": result.name, "outputs": [result.name], "ok": True}


def write_video_chapters(rel_path: str, segments, on_progress=None,
                         is_cancelled=None) -> dict:
    from toolbox.edit_engine import EditError, write_chapters

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    rows = _segment_rows(segments)
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    out = _video_output(path, "章节")
    _report(on_progress, 0.3, 1, "正在写入章节")
    try:
        result = write_chapters(path, rows, out)
    except EditError as exc:
        raise RuntimeError(str(exc)) from exc
    _report(on_progress, 1, 1, "完成")
    return {"summary": f"章节已写入：{result.name}",
            "output": result.name, "outputs": [result.name], "ok": True}


_DETECT_KINDS = ("silence", "black", "scene")


# ==================== 片段清单导入 / 导出（CSV / EDL / 章节） ====================

_SEGMENT_DOC_FORMATS = {
    "csv": ("csv", "text/csv; charset=utf-8"),
    "edl": ("edl", "text/plain; charset=utf-8"),
    "chapters": ("txt", "text/plain; charset=utf-8"),
}


def _plain_segment_rows(segments) -> list[tuple[int, int]]:
    """校验片段但不套用切割任务的条数上限（导出只读，不生成文件）。"""
    rows = []
    for item in segments or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("片段格式不正确")
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("片段时间必须是毫秒数") from exc
        if start < 0 or end <= start:
            raise ValueError("片段结束时间必须大于开始时间")
        rows.append((start, end))
    if not rows:
        raise ValueError("请至少填写一个片段")
    return rows


_VIDEO_PREVIEW_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".avi": "video/x-msvideo", ".flv": "video/x-flv", ".wmv": "video/x-ms-wmv",
}


def video_preview_target(rel_path: str) -> dict:
    """视频预览：只允许工作目录内、浏览器可尝试播放的视频文件。"""
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    if not path.is_file():
        raise ValueError("视频不存在或已被移动")
    mime = _VIDEO_PREVIEW_MIME.get(path.suffix.lower())
    if not mime:
        raise ValueError("该文件不是可预览的视频格式")
    return {"path": path, "content_type": mime, "size": path.stat().st_size}


def segment_clock(ms: int) -> str:
    """毫秒 → HH:MM:SS.s，与网页片段输入框一致。"""
    total = max(0, int(ms)) / 1000
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    return f"{hours:02d}:{minutes:02d}:{total - hours * 3600 - minutes * 60:04.1f}"


def export_segments_document(segments, fmt: str = "csv", name: str = "") -> dict:
    """生成片段清单文本并在内存返回，不写磁盘、不改源文件。"""
    from toolbox import edit_engine

    rows = _plain_segment_rows(segments)
    fmt = str(fmt or "csv").strip().lower()
    if fmt not in _SEGMENT_DOC_FORMATS:
        raise ValueError("未知导出格式，只支持 csv / edl / chapters")
    if fmt == "csv":
        text = edit_engine.segments_csv_text(rows)
    elif fmt == "edl":
        text = edit_engine.edl_text(rows)
    else:
        text = edit_engine.chapters_text(rows)
    stem = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "").strip()).strip(" ._")
    ext, mime = _SEGMENT_DOC_FORMATS[fmt]
    return {"text": text, "filename": f"{stem or '片段清单'}.{ext}",
            "content_type": mime, "count": len(rows)}


def import_segments_document(text: str) -> dict:
    """解析片段 CSV 文本（本工具导出格式），返回毫秒片段与时间码行。"""
    from toolbox.edit_engine import EditError, parse_segments_csv

    raw = str(text or "")
    if not raw.strip():
        raise ValueError("请先选择或粘贴 CSV 内容")
    try:
        rows = parse_segments_csv(raw)
    except EditError as exc:
        raise ValueError(str(exc)) from exc
    return {"segments": [[start, end] for start, end in rows],
            "text": "\n".join(segment_clock(start) + "-" + segment_clock(end)
                              for start, end in rows),
            "count": len(rows)}


def detect_video(rel_path: str, kind: str, threshold=None, min_sec=0.5,
                 on_progress=None, is_cancelled=None) -> dict:
    """检测静音段 / 黑帧段 / 场景切换点（只读，不生成文件）。"""
    from toolbox.concat_engine import media_duration
    from toolbox.edit_engine import (EditCancelled, cut_points_from_ranges,
                                     detect_black, detect_scenes,
                                     detect_silence)

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    kind = str(kind or "")
    if kind not in _DETECT_KINDS:
        raise ValueError("未知检测类型")
    try:
        seconds = float(min_sec if min_sec is not None else 0.5)
    except (TypeError, ValueError) as exc:
        raise ValueError("最短时长不正确") from exc
    if not 0.1 <= seconds <= 10:
        raise ValueError("最短时长需在 0.1 到 10 秒之间")
    try:
        value = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("检测阈值不正确") from exc
    if kind == "silence" and not -60 <= value <= 0:
        raise ValueError("静音阈值需在 -60 到 0 dB 之间")
    if kind in ("black", "scene") and not 0.01 <= value <= 1:
        raise ValueError("阈值需在 0.01 到 1 之间")
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")

    def progress(percent, message):
        _report(on_progress, max(0, min(100, int(percent))) / 100.0, 1,
                message or "检测中")

    try:
        if kind == "silence":
            ranges = detect_silence(path, value, seconds, progress=progress,
                                    cancel=is_cancelled)
            points = cut_points_from_ranges(ranges)
            summary = f"检测到 {len(ranges)} 段静音，{len(points)} 个切点"
        elif kind == "black":
            ranges = detect_black(path, seconds, value, progress=progress,
                                  cancel=is_cancelled)
            points = cut_points_from_ranges(ranges)
            summary = f"检测到 {len(ranges)} 段黑帧，{len(points)} 个切点"
        else:
            ranges = []
            points = detect_scenes(path, value, progress=progress,
                                   cancel=is_cancelled)
            summary = f"检测到 {len(points)} 个场景切换点"
    except EditCancelled as exc:
        raise ToolCancelled(str(exc)) from exc
    if is_cancelled and is_cancelled():
        raise ToolCancelled("任务已取消")
    return {"kind": kind, "path": _rel(root, path),
            "ranges": [[int(a), int(b)] for a, b in ranges],
            "points": [int(item) for item in points],
            "duration_ms": int(media_duration(path) * 1000),
            "summary": summary, "ok": True}


def video_extra_sources(kind: str, limit: int = 200) -> dict:
    """列出工作目录内可用的外部音轨 / 字幕文件。"""
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    kind = str(kind or "")
    if kind == "audio":
        exts, label = _AUDIO_EXTS, "音频"
    elif kind == "subtitle":
        exts, label = _SUBTITLE_VIDEO_EXTS, "字幕"
    else:
        raise ValueError("未知外部文件类型")
    limit = max(1, min(500, int(limit)))
    files = _scan_by_exts(root, exts)
    return {"kind": kind, "label": label,
            "items": _file_items(root, files[:limit]),
            "total": len(files),
            "truncated": len(files) > limit,
            "note": f"只列出工作目录内的{label}文件。"}


def _segment_rows(segments) -> list[tuple[int, int]]:
    rows = []
    for item in segments or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("片段格式不正确")
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("片段时间必须是毫秒数") from exc
        if start < 0 or end <= start:
            raise ValueError("片段结束时间必须大于开始时间")
        rows.append((start, end))
    if not rows:
        raise ValueError("请至少填写一个片段")
    if len(rows) > _MAX_CUT_SEGMENTS:
        raise ValueError(f"单次最多处理 {_MAX_CUT_SEGMENTS} 个片段")
    return rows


def _video_output(src: Path, tag: str) -> Path:
    from toolbox.concat_engine import unique_output_path

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    ext = src.suffix.lower() if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
    return unique_output_path(root / f"{src.stem}_{tag}{ext}")


def _resolve_extra(root: Path, rel: str, exts: set[str], label: str) -> Path:
    path = (root / str(rel or "").strip()).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}文件不在工作目录内") from exc
    if not path.is_file():
        raise ValueError(f"{label}文件不存在或已被移动")
    if path.suffix.lower() not in exts:
        raise ValueError(f"不是支持的{label}格式")
    return path


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
    options = rename_options()
    return {
        "has_work_dir": bool(root),
        "work_dir": root.name if root else "",
        "files": len(files),
        "standard": standard,
        "nonstandard": len(files) - standard,
        "limited": len(files) >= _MAX_FILES,
        "accounts": options["accounts"],
        "types": options["types"],
        "platforms": options["platforms"],
        "copyright_enabled": options["copyright_enabled"],
        "copyright_text": options["copyright_text"],
        "note": "改名为标准命名（日期_时间_账户_类型_版本_平台_标题），"
                "附属文件同步改名；可选写入版权标签（使用桌面命名投稿"
                "设置的附加信息）。",
    }


def rename_options() -> dict:
    from toolbox.config_manager import get_rename_config

    cfg = get_rename_config()
    return {
        "accounts": [str(item) for item in (cfg.account_list or []) if item],
        "types": [str(item) for item in (cfg.live_types or []) if item],
        "platforms": [str(item) for item in (cfg.platform_list or []) if item],
        "copyright_enabled": bool(cfg.append_extra_info),
        "copyright_text": str(cfg.extra_info or "").strip(),
    }


def _rename_copyright_text() -> str:
    from toolbox.config_manager import get_rename_config

    return str(get_rename_config().extra_info or "").strip()


def rename_execute(rel_path: str, account: str, types, danmu: bool,
                   platform: str, title: str, copyright: bool = False,
                   on_progress=None) -> dict:
    """按桌面模板生成标准名并改名（单文件，含附属同步与可选版权标签）。

    版权标签复用桌面 `video_meta.write_copyright`：写标准 copyright，
    MP4 家族同时写「备注」；文本取桌面命名投稿设置的附加信息。版权
    写入失败时保留已改名的视频并在结果中返回失败原因。
    """
    import time as _time

    from toolbox import video_meta
    from toolbox.rename_engine import (extract_file_title, extract_time_str,
                                       rebase_companions)
    from toolbox.utils import clean_special_chars

    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在桌面端全局设置中配置")
    path = _resolve(root, str(rel_path or ""))
    account = str(account or "").strip()
    if not account:
        raise ValueError("请选择直播账户")
    chosen = [str(item or "").strip() for item in (types or [])
              if str(item or "").strip()]
    if not chosen:
        raise ValueError("请至少选择一种直播类型")
    copyright_text = _rename_copyright_text()
    if copyright and not copyright_text:
        raise ValueError("已勾选写入版权属性，但桌面端命名投稿设置的附加信息为空")
    _report(on_progress, 1, 3, "正在改名")
    version = "有弹幕" if danmu else "无弹幕"
    platform = str(platform or "").strip()
    name = path.stem
    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", name)
    date = date_match.group(1) if date_match else "0000-00-00"
    time_str = extract_time_str(name)
    if time_str == "00-00-00":
        time_str = _time.strftime("%H-%M-%S")
    final_title = str(title or "").strip() or extract_file_title(name)
    parts = [date, time_str, account, "+".join(chosen), version]
    if platform:
        parts.append(platform)
    cleaned = clean_special_chars(final_title)
    if cleaned:
        parts.append(cleaned)
    new_name = "_".join(parts) + path.suffix.lower()
    if any(character in new_name for character in '<>:"/\\|?*'):
        raise ValueError("新文件名包含非法字符，请检查账户/类型/标题")
    dst = path.with_name(new_name)
    renamed = dst.resolve() != path.resolve()
    if renamed:
        if dst.exists():
            raise ValueError(f"目标已存在：{new_name}")
        for attempt in range(6):
            try:
                path.rename(dst)
                break
            except PermissionError:
                if attempt == 5:
                    raise ValueError(
                        "视频仍被占用，改名未执行（可能有程序正在预览/播放"
                        "或同步该文件），请关闭后重试")
                _time.sleep(0.3)
    sidecars, skipped = [], []
    if renamed:
        try:
            sidecars, skipped = rebase_companions(str(path), str(dst))
        except Exception:
            sidecars, skipped = [], []
    copyright_result = {"requested": bool(copyright), "ok": True, "message": ""}
    if copyright:
        _report(on_progress, 2, 3, "正在写入版权标签")
        ok, message = video_meta.write_copyright(str(dst), copyright_text)
        copyright_result = {"requested": True, "ok": bool(ok),
                            "message": str(message or "")}
    _report(on_progress, 3, 3, "完成")
    message = "文件名已一致，无需改名" if not renamed else f"已改名为 {new_name}"
    if copyright:
        message += "；" + ("版权标签已写入" if copyright_result["ok"]
                          else f"版权写入失败：{copyright_result['message']}")
    return {"ok": True, "message": message, "new_name": new_name,
            "sidecars": len(sidecars or []), "skipped": len(skipped or []),
            "renamed": renamed, "copyright": copyright_result}


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
    """继续操作：列出桌面端配置的快捷项（链接 / 软件）。"""
    cfg = load_convert_config()
    items = cfg.get("launcher_items") or []
    rows = []
    for index, item in enumerate(items, 1):
        item = item or {}
        path = str(item.get("path") or "").strip()
        is_link = path.lower().startswith(("http://", "https://"))
        rows.append({
            "index": index,
            "name": item.get("name") or f"软件{index}",
            "type": "link" if is_link else "app",
            "exists": bool(path) and (is_link or Path(path).is_file()),
            "url": path if is_link else "",
        })
    return {"items": rows, "total": len(rows),
            "note": "只启动桌面端已配置的快捷项：链接在浏览器新标签打开；"
                    "软件在运行 WebUI 的电脑上启动（会二次确认）。"}


def launcher_launch(index) -> dict:
    """启动继续操作的快捷项：链接返回 URL 由前端打开，软件在本机启动。"""
    cfg = load_convert_config()
    items = cfg.get("launcher_items") or []
    try:
        idx = int(index)
    except (TypeError, ValueError) as exc:
        raise ValueError("快捷项编号不正确") from exc
    if not (0 <= idx < len(items)):
        raise ValueError("快捷项不存在，请刷新后重试")
    item = items[idx] or {}
    name = str(item.get("name") or f"软件{idx + 1}")
    path = str(item.get("path") or "").strip()
    if not path:
        raise ValueError("该快捷项没有配置路径，请在桌面端补齐")
    if path.lower().startswith(("http://", "https://")):
        return {"ok": True, "mode": "link", "name": name, "url": path,
                "message": f"链接已准备在浏览器打开：{name}"}
    if not Path(path).is_file():
        raise ValueError("快捷项的文件不存在，请在桌面端检查路径")
    _start_detached(path)
    return {"ok": True, "mode": "app", "name": name,
            "message": f"已在运行 WebUI 的电脑上启动：{name}"}


def _start_detached(path: str) -> None:
    folder = os.path.dirname(path) or None
    lower = path.lower()
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        if lower.endswith((".exe", ".com")):
            subprocess.Popen([path], cwd=folder, creationflags=flags)
        elif lower.endswith((".bat", ".cmd")):
            extra = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
            subprocess.Popen(["cmd", "/c", path], cwd=folder,
                             creationflags=flags | extra)
        elif hasattr(os, "startfile"):
            os.startfile(path)
        else:
            subprocess.Popen([path], cwd=folder)
    except OSError as exc:
        raise ValueError(f"启动失败：{exc}") from exc


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
                 "目标目录可在「全局设置 → 目录设置」里修改。"),
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