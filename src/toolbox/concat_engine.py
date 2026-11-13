"""视频合并引擎。

参考 LosslessCut 的合并实现（src/main/ffmpeg.ts runFfmpegConcat / ConcatDialog.tsx）：
- 用 concat demuxer 从 stdin 读文件列表（`-f concat -safe 0 -protocol_whitelist file,pipe,fd -i -`）
- 列表项写成 `file 'file:<绝对路径>'`，单引号按 `'\''` 转义
- 无损拼接前按轨道逐项比对编码参数（codec/分辨率/像素格式/帧率/time_base 等），不一致给出提示
- 不检查 avg_frame_rate（同参数文件之间常有微小差异，参考 LosslessCut issue #2759）

与 LosslessCut 的差异：参数不一致时提供「兼容转码」模式，先把每个文件归一化到
第一文件的画面参数，再用 concat demuxer 无损拼接，保证产物可播放。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .utils import find_ffmpeg_exe, find_ffprobe_exe

MODE_COPY = "copy"          # 无损拼接（仅重新封装，不转码）
MODE_ENCODE = "encode"      # 兼容转码（参数不一致时使用）

ProgressCb = Optional[Callable[[int, str], None]]
CancelCb = Optional[Callable[[], bool]]

# 与 LosslessCut 一致的轨道参数比对项
_COMPARE_KEYS = (
    "codec_name", "width", "height", "pix_fmt", "level", "profile",
    "sample_fmt", "r_frame_rate", "time_base",
)


class ConcatError(RuntimeError):
    """合并失败。"""


class ConcatCancelled(RuntimeError):
    """用户中止。"""


@dataclass
class MediaInfo:
    path: Path
    duration: float = 0.0
    streams: List[dict] = field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return any(s.get("codec_type") == "video" for s in self.streams)

    @property
    def has_audio(self) -> bool:
        return any(s.get("codec_type") == "audio" for s in self.streams)

    @property
    def video_stream(self) -> Optional[dict]:
        return next((s for s in self.streams if s.get("codec_type") == "video"), None)


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run_probe(path: Path) -> dict:
    cmd = [
        find_ffprobe_exe(), "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120,
                              creationflags=_no_window())
    except FileNotFoundError as exc:
        raise ConcatError("ffprobe 未找到，请先在全局设置指定 FFmpeg 位置") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-200:]
        raise ConcatError(f"读取媒体信息失败: {detail.strip() or path.name}")
    try:
        return json.loads((proc.stdout or b"").decode("utf-8", errors="ignore"))
    except Exception as exc:
        raise ConcatError(f"解析媒体信息失败: {path.name}") from exc


def probe_json(path: Path) -> dict:
    """ffprobe 原始 JSON（含 tags / side_data，供元数据与旋转读取）。"""
    return _run_probe(Path(path))


def probe_media(path: Path) -> MediaInfo:
    path = Path(path)
    if not path.exists():
        raise ConcatError(f"文件不存在: {path}")
    data = probe_json(path)
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    streams = [s for s in (data.get("streams") or [])
               if s.get("codec_type") in ("video", "audio", "subtitle")]
    return MediaInfo(path=path, duration=duration, streams=streams)


def check_compatibility(infos: List[MediaInfo]) -> List[str]:
    """按 LosslessCut 的规则逐轨比对，返回可读的问题列表（空 = 参数一致）。"""
    if len(infos) < 2:
        return []
    problems: List[str] = []
    first = infos[0]
    for info in infos[1:]:
        if len(info.streams) != len(first.streams):
            problems.append(
                f"{info.path.name}: 轨道数 {len(info.streams)} ≠ {first.path.name} 的 {len(first.streams)}")
        for idx, stream in enumerate(info.streams):
            if idx >= len(first.streams):
                break
            ref = first.streams[idx]
            if ref.get("codec_type") != stream.get("codec_type"):
                problems.append(
                    f"{info.path.name}: 第{idx + 1}轨类型 {stream.get('codec_type')} ≠ {ref.get('codec_type')}")
                continue
            for key in _COMPARE_KEYS:
                a, b = stream.get(key), ref.get(key)
                if a != b:
                    problems.append(
                        f"{info.path.name}: 第{idx + 1}轨 {key} {a} ≠ {b}")
    return problems


def _escape_concat_path(path: Path) -> str:
    """concat 列表转义：单引号写成 '\\''，并加 file: 前缀避免协议解析问题。"""
    resolved = str(Path(path).resolve())
    escaped = resolved.replace("'", "'\\''")
    return f"file 'file:{escaped}'"


def _parse_fps(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ffmpeg_progress_stream(cmd: List[str], total_sec: float,
                            progress: ProgressCb, cancel: CancelCb,
                            label: str = "", base: int = 0, span: int = 100,
                            stdin_text: Optional[str] = None) -> None:
    """执行 ffmpeg 并用 -progress 输出计算百分比；取消时杀进程。"""
    progress = progress or (lambda _p, _m: None)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="ignore",
        creationflags=_no_window(),
    )
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except Exception:
            pass

    stderr_tail: deque = deque(maxlen=60)

    def _read_stderr():
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if line:
                stderr_tail.append(line)

    threading.Thread(target=_read_stderr, daemon=True).start()

    total_us = max(1.0, total_sec * 1_000_000)
    last_percent = base
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel and cancel():
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait()
            raise ConcatCancelled("已停止合并")
        line = line.strip()
        if not line.startswith("out_time"):
            continue
        key, _, value = line.partition("=")
        if key == "out_time_us" and value.isdigit():
            ratio = min(1.0, int(value) / total_us)
        elif key == "out_time_ms" and value.isdigit():
            # 旧版 ffmpeg 的 out_time_ms 实际是微秒
            ratio = min(1.0, int(value) / total_us)
        else:
            continue
        percent = base + int(span * ratio)
        if percent > last_percent:
            last_percent = percent
            progress(min(99, percent), f"{label} {percent}%" if label else f"{percent}%")

    returncode = proc.wait()
    if returncode != 0:
        detail = "\n".join(stderr_tail)[-600:] or f"ffmpeg 返回码 {returncode}"
        raise ConcatError(detail)


def _concat_copy(paths: List[Path], out_path: Path, total_sec: float,
                 progress: ProgressCb, cancel: CancelCb,
                 base: int = 0, span: int = 100) -> None:
    concat_txt = "\n".join(_escape_concat_path(p) for p in paths) + "\n"
    cmd = [
        find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
        # 与 LosslessCut 相同：列表从 stdin 读，避免 concat 协议白名单报错
        "-f", "concat", "-safe", "0",
        "-protocol_whitelist", "file,pipe,fd", "-i", "-",
        "-map", "0", "-ignore_unknown",
        "-map_metadata", "0", "-map_chapters", "0",
        "-c", "copy",
    ]
    if out_path.suffix.lower() in (".mp4", ".mov", ".m4v"):
        cmd += ["-movflags", "+faststart"]
    cmd += ["-y", str(out_path)]
    _ffmpeg_progress_stream(cmd, total_sec, progress, cancel,
                            label="合并", base=base, span=span,
                            stdin_text=concat_txt)


def _normalize_one(src: Path, dst: Path, target_w: int, target_h: int,
                   fps: str, progress: ProgressCb, cancel: CancelCb,
                   info: MediaInfo, base: int, span: int) -> None:
    """把单个文件归一化到目标画面参数（无音轨补静音），供转码模式拼接。"""
    vf = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
          f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    if fps:
        vf += f",fps={fps}"
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", "-i", str(src)]
    if info.has_audio:
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        audio_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        audio_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", *audio_args,
            "-movflags", "+faststart", "-y", str(dst)]
    _ffmpeg_progress_stream(cmd, info.duration, progress, cancel,
                            label=f"转码 {src.name}", base=base, span=span)


def concat_media(paths: List[Path], out_path: Path, mode: str = MODE_COPY,
                 progress: ProgressCb = None, cancel: CancelCb = None) -> Path:
    """按顺序拼接多个媒体文件，返回输出路径。

    mode=copy：无损拼接（要求编码参数一致，否则可能失败/花屏）
    mode=encode：先统一转码到第一文件的画面参数，再无损拼接
    """
    progress = progress or (lambda _p, _m: None)
    paths = [Path(p) for p in paths]
    if len(paths) < 2:
        raise ConcatError("至少需要 2 个文件才能合并")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    progress(1, "读取文件信息")
    infos = [probe_media(p) for p in paths]
    total_sec = sum(i.duration for i in infos)
    if total_sec <= 0:
        raise ConcatError("无法读取视频时长，文件可能损坏")

    existed_before = out_path.exists()
    try:
        if mode == MODE_ENCODE:
            ref = infos[0]
            vstream = ref.video_stream
            if vstream is None:
                raise ConcatError("第一个文件没有视频轨，无法作为转码基准")
            target_w = int(vstream.get("width") or 1920)
            target_h = int(vstream.get("height") or 1080)
            fps = vstream.get("r_frame_rate") or ""
            workdir = Path(tempfile.mkdtemp(prefix="fmjj_concat_"))
            parts: List[Path] = []
            try:
                for idx, info in enumerate(infos):
                    if cancel and cancel():
                        raise ConcatCancelled("已停止合并")
                    base = 2 + int(82 * idx / len(infos))
                    span = max(1, int(82 / len(infos)))
                    part = workdir / f"part_{idx:03d}.mp4"
                    _normalize_one(info.path, part, target_w, target_h, fps,
                                   progress, cancel, info, base, span)
                    parts.append(part)
                concat_sec = sum(media_duration(p) for p in parts)
                _concat_copy(parts, out_path, concat_sec, progress, cancel,
                             base=85, span=14)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        else:
            _concat_copy(paths, out_path, total_sec, progress, cancel,
                         base=2, span=97)
    except Exception:
        # 中途失败/取消：删掉半截产物（只删本次新建的文件）
        if not existed_before:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise ConcatError("输出文件为空，合并失败")
    progress(100, f"合并完成：{out_path.name}")
    return out_path


def media_duration(path: Path) -> float:
    try:
        return probe_media(path).duration
    except ConcatError:
        return 0.0


def unique_output_path(path: Path) -> Path:
    """输出文件重名时追加 _2、_3 …"""
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    idx = 2
    while True:
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def find_videos_in(folder: str) -> List[Path]:
    """递归收集文件夹内的视频文件（按文件名排序）。"""
    from .utils import is_video_file
    found: List[Path] = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            fp = os.path.join(root, name)
            if is_video_file(fp):
                found.append(Path(fp))
    return sorted(found, key=lambda p: str(p).lower())
