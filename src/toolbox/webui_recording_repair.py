"""WebUI 录制文件修复用例（Qt-free，与桌面进程内状态隔离）。

只读已保存的录制目录配置；源文件只读，输出仅写入本次 Job workspace。
不启动录制、不依赖桌面控制桥、不创建第二份配置或写入真实主播列表。
Web 无法确认桌面当前正在写哪个文件，调用方必须确认源文件已停止录制。
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import time

from toolbox import live_record
from toolbox.config_manager import get_export_dir
from toolbox.recording_repair import repair_recording

_MAX_FILES = 200
_MAX_SCAN_ENTRIES = 4000
_MAX_SCAN_DIRS = 400
_MAX_SCAN_DEPTH = 8
_MAX_SCAN_SECONDS = 2.0
_FORMATS = {".flv", ".ts"}
_STOPPED_NOTE = "只处理已停止录制的 FLV / TS 文件；Web 端不能确认桌面当前录制状态。源文件保留，结果通过任务下载。"


class RepairCancelled(Exception):
    """共享修复用例收到取消；服务层映射为 Web JobCancelled。"""


def _platform(value):
    key = str(value or "").strip()
    if key not in live_record.PLATFORMS:
        raise ValueError("请选择有效的录制平台")
    return key


def _is_link(path):
    """不跟随 symlink / junction / 其他 Windows reparse point。"""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _root(platform):
    state = live_record.load_state() or {}
    data = (state.get("platforms") or {}).get(platform) or {}
    settings = data.get("settings") or {}
    configured = str(settings.get("outdir") or "").strip()
    path = (Path(configured).expanduser().absolute() if configured else
            (Path(get_export_dir()) / "直播录制" / live_record.PLATFORMS[platform]["name"]).absolute())
    try:
        for part in (path, *path.parents):
            if part.exists() or part.is_symlink():
                if _is_link(part):
                    raise ValueError("录制目录包含链接，无法安全读取，请在桌面端使用普通目录")
        return path.resolve()
    except OSError:
        raise ValueError("无法读取录制目录，请检查目录是否可访问") from None


def _relative(value):
    if not isinstance(value, str):
        raise ValueError("请选择录制目录内的文件")
    text = value.strip()
    if (not text or len(text) > 2048 or "\\" in text or ":" in text
            or text.startswith("/") or any(ord(char) < 32 for char in text)
            or any(part in ("", ".", "..") for part in text.split("/"))):
        raise ValueError("只支持录制目录内的相对文件路径")
    if Path(text).suffix.lower() not in _FORMATS:
        raise ValueError("请选择 FLV 或 TS 录制文件")
    return text


def validate_request(payload):
    """在排队前校验参数；文件是否存在与边界在实际任务开始时再次核对。"""
    if not isinstance(payload, dict):
        raise ValueError("修复参数无效，请重新选择文件")
    platform = _platform(payload.get("platform"))
    rel = _relative(payload.get("rel"))
    if payload.get("confirmed_stopped") is not True:
        raise ValueError("请先确认所选文件已经停止录制")
    return platform, rel


def _source(platform, rel):
    root = _root(platform)
    target = root
    try:
        for part in _relative(rel).split("/"):
            target = target / part
            if _is_link(target):
                raise ValueError("文件路径包含链接，请选择普通录制文件")
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            raise ValueError("所选文件不存在，请刷新列表")
        return resolved
    except (OSError, ValueError):
        raise ValueError("所选文件不可访问或不在录制目录内，请刷新后重新选择") from None


def repair_sources(platform):
    """有界枚举最多 200 个源文件，DTO 仅含相对路径。"""
    platform = _platform(platform)
    root = _root(platform)
    files = []
    truncated = False
    scanned = directories = 0
    started = time.monotonic()
    stack = [(root, 0)] if root.is_dir() else []
    while stack:
        if directories >= _MAX_SCAN_DIRS or time.monotonic() - started >= _MAX_SCAN_SECONDS:
            truncated = True
            break
        folder, depth = stack.pop()
        directories += 1
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    scanned += 1
                    if (scanned > _MAX_SCAN_ENTRIES
                            or time.monotonic() - started >= _MAX_SCAN_SECONDS):
                        truncated = True
                        break
                    path = Path(entry.path)
                    if _is_link(path):
                        continue
                    resolved = path.resolve()
                    try:
                        resolved.relative_to(root)
                    except ValueError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            if depth < _MAX_SCAN_DEPTH:
                                stack.append((path, depth + 1))
                            else:
                                truncated = True
                        continue
                    if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in _FORMATS:
                        continue
                    if len(files) >= _MAX_FILES:
                        truncated = True
                        break
                    info = path.stat()
                    files.append({"rel": path.relative_to(root).as_posix(),
                                  "name": path.name, "size": int(info.st_size),
                                  "mtime": info.st_mtime, "format": path.suffix.lower()[1:]})
        except OSError:
            continue
        if len(files) >= _MAX_FILES or scanned > _MAX_SCAN_ENTRIES:
            truncated = bool(stack) or truncated
            break
    files.sort(key=lambda item: (-item["mtime"], item["rel"]))
    return {"platform": platform, "files": files, "total": len(files),
            "truncated": truncated, "note": _STOPPED_NOTE}


def repair_job(platform, rel, workspace, on_progress=None, cancel=None):
    """只读源文件并写 Job workspace；返回不包含绝对路径的结果。"""
    platform, rel = _platform(platform), _relative(rel)
    source = _source(platform, rel)
    destination = Path(workspace) / (source.stem + "_修复" + source.suffix.lower())
    try:
        before = source.stat()
        result = repair_recording(source, destination, on_progress=on_progress, cancel=cancel)
        if result.get("cancelled"):
            raise RepairCancelled("已取消修复")
        if not result.get("ok"):
            raise ValueError("修复失败，请检查文件是否包含有效音视频、已停止录制，以及 FFmpeg 依赖是否可用")
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            destination.unlink(missing_ok=True)
            raise ValueError("源文件在修复期间发生变化，请停止录制后重新修复")
        return {"ok": True, "platform": platform, "source": rel,
                "output": destination.name, "format": result.get("format") or source.suffix[1:],
                "summary": "修复已完成，源文件保留，请下载修复结果",
                "stats": {key: result[key] for key in
                          ("tags", "dropped", "ts_fixed", "resync", "seam_dropped") if key in result}}
    except OSError:
        raise ValueError("无法读取源文件或写入任务结果，请检查文件占用与磁盘空间") from None
