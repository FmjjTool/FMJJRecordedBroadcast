"""视频剪辑引擎：无损切割、片段导出、轨道提取、帧截图。

切割策略参考 LosslessCut：
- 无损切割：`-ss` 放在输入前（关键帧定位）+ `-c copy`，速度快、不损画质，
  代价是起点会对齐到前一个关键帧；
- 精确切割：重新编码视频轨（音频同样转码），起点准确但慢；
- 片段拼接复用 concat_engine 的 concat demuxer 实现。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .concat_engine import (
    ConcatCancelled, ConcatError, _ffmpeg_progress_stream, _no_window,
    concat_media, media_duration, probe_media,
)
from .utils import find_ffmpeg_exe

ProgressCb = Optional[Callable[[int, str], None]]
CancelCb = Optional[Callable[[], bool]]

_AUDIO_EXT = {
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "dts": ".dts",
    "truehd": ".thd",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_s32le": ".wav",
    "pcm_f32le": ".wav",
}
_BITMAP_SUB = {"hdmv_pgs_subtitle": ".sup", "dvd_subtitle": ".sub", "dvb_subtitle": ".sub"}


class EditError(RuntimeError):
    """剪辑操作失败。"""


class EditCancelled(RuntimeError):
    """用户中止。"""


# ==================== 轨道 ====================

def probe_tracks(path: Path) -> List[dict]:
    """返回音频/字幕轨列表（含 ffprobe 原始字段）。"""
    info = probe_media(Path(path))
    return [s for s in info.streams if s.get("codec_type") in ("audio", "subtitle")]


def track_extension(stream: dict) -> str:
    """轨道对应的输出扩展名（音轨按编码，字幕按文字/位图）。"""
    codec = str(stream.get("codec_name") or "").lower()
    if stream.get("codec_type") == "audio":
        return _AUDIO_EXT.get(codec, ".mka")
    if codec in _BITMAP_SUB:
        return _BITMAP_SUB[codec]
    return ".srt"


def extract_track(src: Path, stream: dict, out_dir: Path = None) -> Path:
    """提取单条音轨/字幕轨到源文件同目录（不重新编码）。"""
    src = Path(src)
    if not src.exists():
        raise EditError(f"文件不存在: {src.name}")
    stream_index = int(stream.get("index", -1))
    if stream_index < 0:
        raise EditError("无效的轨道编号")
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = "音轨" if stream.get("codec_type") == "audio" else "字幕"
    ext = track_extension(stream)
    base = out_dir / f"{src.stem}_{kind}{stream_index}{ext}"
    out = base
    idx = 2
    while out.exists():
        out = base.with_name(f"{base.stem}_{idx}{base.suffix}")
        idx += 1

    codec = str(stream.get("codec_name") or "").lower()
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-map", f"0:{stream_index}"]
    if stream.get("codec_type") == "subtitle" and codec not in _BITMAP_SUB:
        cmd += ["-c:s", "srt"]
    else:
        cmd += ["-c", "copy"]
    cmd += [str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=3600,
                              creationflags=_no_window())
    except FileNotFoundError as exc:
        raise EditError("ffmpeg 未找到，请先在全局设置指定 FFmpeg 位置") from exc
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise EditError(f"提取失败: {detail.strip() or out.name}")
    return out


def extract_all_tracks(src: Path, out_dir: Path = None,
                       progress: ProgressCb = None) -> List[Path]:
    """提取文件内全部音轨与字幕轨。"""
    progress = progress or (lambda _p, _m: None)
    tracks = probe_tracks(src)
    if not tracks:
        raise EditError("该文件没有音轨或字幕轨")
    outputs: List[Path] = []
    for idx, stream in enumerate(tracks):
        progress(int(5 + 90 * idx / len(tracks)),
                 f"提取轨道 {idx + 1}/{len(tracks)}")
        outputs.append(extract_track(src, stream, out_dir))
    progress(100, f"已提取 {len(outputs)} 条轨道")
    return outputs


# ==================== 切割 ====================

_COPY_CONTAINERS = {".mp4", ".mkv", ".mov", ".ts", ".flv", ".webm", ".avi", ".wmv"}


def _cut_ext(src: Path) -> str:
    """切割输出容器：尽量沿用源容器，避免字幕等轨道在 mp4 里不兼容。"""
    suffix = src.suffix.lower()
    return suffix if suffix in _COPY_CONTAINERS else ".mp4"


def cut_one(src: Path, out: Path, start_ms: int, end_ms: int,
            mode: str = "copy", progress: ProgressCb = None,
            cancel: CancelCb = None, base: int = 0, span: int = 100) -> Path:
    """切割单个片段。mode=copy 无损（关键帧对齐）/ precise 精确重编码。"""
    src, out = Path(src), Path(out)
    start_ms = max(0, int(start_ms))
    duration_sec = max(0.05, (int(end_ms) - start_ms) / 1000)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats",
           "-ss", f"{start_ms / 1000:.3f}", "-i", str(src),
           "-t", f"{duration_sec:.3f}"]
    if mode == "precise":
        cmd += ["-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        # 无损模式：视频/音频直接复制；mp4 容器不支持 subrip/ass，
        # 文字字幕转 mov_text，位图字幕（pgs/dvd）直接丢弃避免封装失败
        subs = [s for s in probe_media(src).streams
                if s.get("codec_type") == "subtitle"]
        mp4_out = out.suffix.lower() in (".mp4", ".mov", ".m4v")
        if mp4_out and subs:
            text_subs = all(
                str(s.get("codec_name") or "").lower()
                in ("subrip", "ass", "ssa", "mov_text", "webvtt")
                for s in subs)
            if text_subs:
                cmd += ["-map", "0", "-ignore_unknown",
                        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
            else:
                cmd += ["-map", "0:v?", "-map", "0:a?", "-ignore_unknown", "-c", "copy"]
        else:
            cmd += ["-map", "0", "-ignore_unknown", "-c", "copy"]
        cmd += ["-avoid_negative_ts", "make_zero"]
    if out.suffix.lower() in (".mp4", ".mov", ".m4v"):
        cmd += ["-movflags", "+faststart"]
    cmd += ["-y", str(out)]
    try:
        _ffmpeg_progress_stream(cmd, duration_sec, progress, cancel,
                                label=out.name, base=base, span=span)
    except ConcatCancelled as exc:
        raise EditCancelled(str(exc)) from exc
    except ConcatError as exc:
        raise EditError(str(exc)) from exc
    if not out.exists() or out.stat().st_size == 0:
        raise EditError(f"切割输出为空: {out.name}")
    return out


def complement_segments(segments: List[Tuple[int, int]],
                        duration_ms: int) -> List[Tuple[int, int]]:
    """返回被选中片段之外的区间（用于「删除选中片段」）。"""
    result: List[Tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(segments):
        start = max(0, min(int(start), duration_ms))
        if start - cursor >= 200:
            result.append((cursor, start))
        cursor = max(cursor, int(end))
    if duration_ms - cursor >= 200:
        result.append((cursor, duration_ms))
    return result


def cut_segments(src: Path, segments: List[Tuple[int, int]], out_dir: Path,
                 mode: str = "copy", merge: bool = False,
                 merged_name: str = "", progress: ProgressCb = None,
                 cancel: CancelCb = None) -> List[Path]:
    """批量切割片段。

    merge=False：每段单独输出到 out_dir；
    merge=True：先切到临时文件再按顺序拼成一个文件（输出到 out_dir/merged_name）。
    """
    progress = progress or (lambda _p, _m: None)
    src = Path(src)
    if not segments:
        raise EditError("没有可导出的片段")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _cut_ext(src)
    total = len(segments)
    parts: List[Path] = []
    tmpdir: Optional[Path] = None
    try:
        for idx, (start, end) in enumerate(segments):
            if cancel and cancel():
                raise EditCancelled("已停止切割")
            if end <= start:
                continue
            base = int(5 + (80 if merge else 92) * idx / total)
            span = max(1, int((80 if merge else 92) / total))
            if merge:
                if tmpdir is None:
                    tmpdir = Path(tempfile.mkdtemp(prefix="fmjj_cut_"))
                dst = tmpdir / f"part_{idx:03d}{ext}"
            else:
                dst = out_dir / f"{src.stem}_片段{idx + 1:02d}{ext}"
            progress(base, f"切割 {idx + 1}/{total}")
            parts.append(cut_one(src, dst, start, end, mode, progress, cancel,
                                 base=base, span=span))
        if merge:
            if len(parts) == 1:
                final = out_dir / (merged_name or f"{src.stem}_片段合并{ext}")
                if final.suffix.lower() != ext:
                    final = final.with_suffix(ext)
                if final.exists():
                    final.unlink()
                parts[0].replace(final)
                return [final]
            name = merged_name or f"{src.stem}_片段合并{ext}"
            final = out_dir / name
            # 片段是无损裁切的同一容器，合并输出沿用它（避免 mp4 装不下字幕轨）
            if final.suffix.lower() != ext:
                final = final.with_suffix(ext)
            if final.exists():
                final.unlink()
            concat_media(parts, final, "copy", progress, cancel)
            return [final]
        return parts
    finally:
        if tmpdir is not None:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ==================== 截图 ====================

def snapshot(src: Path, time_ms: int, out_dir: Path = None,
             image_format: str = "jpg") -> Path:
    """导出指定时间点的一帧。"""
    src = Path(src)
    if not src.exists():
        raise EditError(f"文件不存在: {src.name}")
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time_ms) // 1000
    h, rem = divmod(stamp, 3600)
    m, s = divmod(rem, 60)
    ext = ".png" if image_format.lower() == "png" else ".jpg"
    out = out_dir / f"{src.stem}_帧{h:02d}-{m:02d}-{s:02d}{ext}"
    idx = 2
    while out.exists():
        out = out_dir / f"{src.stem}_帧{h:02d}-{m:02d}-{s:02d}_{idx}{ext}"
        idx += 1
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{max(0, int(time_ms)) / 1000:.3f}", "-i", str(src),
           "-frames:v", "1"]
    cmd += ["-q:v", "2"] if ext == ".jpg" else []
    cmd += [str(out)]
    proc = subprocess.run(cmd, capture_output=True, timeout=120,
                          creationflags=_no_window())
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-200:]
        raise EditError(f"截图失败: {detail.strip()}")
    return out


def snapshot_every(src: Path, interval_sec: float, out_dir: Path = None,
                   progress: ProgressCb = None,
                   cancel: CancelCb = None) -> List[Path]:
    """按固定间隔批量抽帧。"""
    progress = progress or (lambda _p, _m: None)
    src = Path(src)
    duration = media_duration(src)
    if duration <= 0:
        raise EditError("无法读取视频时长")
    interval = max(0.1, float(interval_sec))
    out_dir = Path(out_dir) if out_dir else src.parent / f"{src.stem}_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, int(duration // interval))
    outputs: List[Path] = []
    for idx in range(count):
        if cancel and cancel():
            raise EditCancelled("已停止抽帧")
        t_ms = int(idx * interval * 1000)
        progress(int(5 + 90 * idx / count), f"抽帧 {idx + 1}/{count}")
        outputs.append(snapshot(src, t_ms, out_dir))
    progress(100, f"已导出 {len(outputs)} 帧")
    return outputs


def media_info(path: Path):
    """兼容性别名：给页面读取时长/轨道。"""
    return probe_media(Path(path))


# ==================== 检测：静音 / 黑帧 / 场景 ====================

def _run_detect(cmd: List[str], handler, cancel: CancelCb) -> None:
    """执行检测类 ffmpeg 命令，逐行回调 stderr（可取消）。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="ignore",
        creationflags=_no_window())
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            if cancel and cancel():
                proc.kill()
                proc.wait()
                raise EditCancelled("已停止检测")
            handler(line)
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()


def _base_detect_cmd(src: Path, filter_args: List[str]) -> List[str]:
    return [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-nostats",
            "-i", str(src), *filter_args, "-f", "null", "-"]


def detect_silence(src: Path, noise_db: float = -30.0, min_sec: float = 0.5,
                   progress: ProgressCb = None, cancel: CancelCb = None
                   ) -> List[Tuple[int, int]]:
    """检测静音区间（毫秒），用于按静音切分长录播。"""
    progress = progress or (lambda _p, _m: None)
    src = Path(src)
    progress(2, "检测静音…")
    ranges: List[Tuple[float, float]] = []
    state = {"start": None}

    def handler(line: str):
        if "silence_start:" in line:
            match = re.search(r"silence_start:\s*(-?[\d.]+)", line)
            if match:
                state["start"] = max(0.0, float(match.group(1)))
        elif "silence_end:" in line:
            match = re.search(r"silence_end:\s*([\d.]+)", line)
            if match and state["start"] is not None:
                ranges.append((state["start"], float(match.group(1))))
                state["start"] = None

    cmd = _base_detect_cmd(
        src, ["-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-vn"])
    _run_detect(cmd, handler, cancel)
    duration = media_duration(src)
    if state["start"] is not None:
        ranges.append((state["start"], duration))
    progress(100, f"检测到 {len(ranges)} 段静音")
    return [(int(a * 1000), int(b * 1000)) for a, b in ranges if b > a]


def detect_black(src: Path, min_sec: float = 0.5, pix_th: float = 0.10,
                 progress: ProgressCb = None, cancel: CancelCb = None
                 ) -> List[Tuple[int, int]]:
    """检测黑帧区间（毫秒）。"""
    progress = progress or (lambda _p, _m: None)
    src = Path(src)
    progress(2, "检测黑帧…")
    ranges: List[Tuple[float, float]] = []

    def handler(line: str):
        if "black_start:" not in line:
            return
        start = re.search(r"black_start:\s*([\d.]+)", line)
        end = re.search(r"black_end:\s*([\d.]+)", line)
        if start and end:
            ranges.append((float(start.group(1)), float(end.group(1))))

    cmd = _base_detect_cmd(
        src, ["-vf", f"blackdetect=d={min_sec}:pix_th={pix_th}", "-an"])
    _run_detect(cmd, handler, cancel)
    progress(100, f"检测到 {len(ranges)} 段黑帧")
    return [(int(a * 1000), int(b * 1000)) for a, b in ranges if b > a]


def detect_scenes(src: Path, threshold: float = 0.4,
                  progress: ProgressCb = None, cancel: CancelCb = None
                  ) -> List[int]:
    """检测场景切换时间点（毫秒）。"""
    progress = progress or (lambda _p, _m: None)
    src = Path(src)
    progress(2, "检测场景切换…")
    points: List[int] = []

    def handler(line: str):
        if "pts_time:" in line and "Parsed_showinfo" in line:
            match = re.search(r"pts_time:\s*([\d.]+)", line)
            if match:
                points.append(int(float(match.group(1)) * 1000))

    cmd = _base_detect_cmd(
        src, ["-vf", f"select='gt(scene,{threshold})',showinfo", "-an"])
    _run_detect(cmd, handler, cancel)
    progress(100, f"检测到 {len(points)} 个切换点")
    return sorted(set(points))


def cut_points_from_ranges(ranges: List[Tuple[int, int]]) -> List[int]:
    """把区间转成切点（取中点，避免切掉语音首尾）。"""
    return sorted({int((a + b) // 2) for a, b in ranges if b > a})


def segments_from_points(points: List[int], duration_ms: int,
                         min_len_ms: int = 400) -> List[Tuple[int, int]]:
    """由切点生成首尾相接的片段列表。"""
    bounds = [0] + sorted({p for p in points if 0 < p < duration_ms}) + [duration_ms]
    segments = []
    for start, end in zip(bounds, bounds[1:]):
        if end - start >= min_len_ms:
            segments.append((int(start), int(end)))
    return segments


# ==================== 片段导出：CSV / EDL / 章节 ====================

def _timecode(ms: int, fps: int = 30) -> str:
    total_frames = int(round(max(0, ms) / 1000 * fps))
    frames = total_frames % fps
    total_sec = total_frames // fps
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def export_segments_csv(segments: List[Tuple[int, int]], path: Path) -> Path:
    path = Path(path)
    lines = ["序号,开始秒,结束秒,时长秒,开始时间,结束时间"]
    for idx, (start, end) in enumerate(segments, 1):
        lines.append(
            f"{idx},{start/1000:.3f},{end/1000:.3f},{(end-start)/1000:.3f},"
            f"{start/1000:.2f},{end/1000:.2f}")
    path.write_text("\ufeff" + "\n".join(lines), encoding="utf-8")
    return path


def import_segments_csv(path: Path) -> List[Tuple[int, int]]:
    """导入本工具导出的片段 CSV（序号,开始秒,结束秒,...）。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    segments: List[Tuple[int, int]] = []
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            start = float(parts[1])
            end = float(parts[2])
        except ValueError:
            continue
        if end > start:
            segments.append((int(start * 1000), int(end * 1000)))
    if not segments:
        raise EditError("CSV 里没有可识别的片段")
    return segments


def export_edl(segments: List[Tuple[int, int]], path: Path, fps: int = 30) -> Path:
    path = Path(path)
    lines = ["TITLE: FMJJ Segments", "FCM: NON-DROP FRAME", ""]
    record = 0
    for idx, (start, end) in enumerate(segments, 1):
        src_in = _timecode(start, fps)
        src_out = _timecode(end, fps)
        rec_in = _timecode(record, fps)
        record += end - start
        rec_out = _timecode(record, fps)
        lines.append(
            f"{idx:03d}  AX       V     C        "
            f"{src_in} {src_out} {rec_in} {rec_out}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_chapters(segments: List[Tuple[int, int]], path: Path) -> Path:
    """导出 ffmetadata 章节文件（可被播放器/剪辑软件识别）。"""
    path = Path(path)
    lines = [";FFMETADATA1"]
    for idx, (start, end) in enumerate(segments, 1):
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}",
                  f"END={end}", f"title=片段 {idx}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_chapters(src: Path, segments: List[Tuple[int, int]], out_path: Path) -> Path:
    """把片段写成章节并封装进输出视频（不重新编码）。"""
    src, out_path = Path(src), Path(out_path)
    meta = Path(tempfile.mkstemp(prefix="fmjj_chapters_", suffix=".txt")[1])
    try:
        export_chapters(segments, meta)
        cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src), "-i", str(meta),
               "-map", "0", "-map_metadata", "1", "-map_chapters", "1",
               "-c", "copy", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=3600,
                              creationflags=_no_window())
        if proc.returncode != 0 or not out_path.exists():
            detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
            raise EditError(f"写入章节失败: {detail.strip()}")
        return out_path
    finally:
        try:
            meta.unlink(missing_ok=True)
        except OSError:
            pass


# ==================== 元数据 / 旋转 ====================

def read_metadata(src: Path) -> dict:
    from .concat_engine import probe_json
    data = probe_json(Path(src))
    tags = (data.get("format") or {}).get("tags") or {}
    rotation = 0
    for stream in data.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        for side in stream.get("side_data_list") or []:
            if "rotation" in side:
                try:
                    rotation = int(float(side["rotation"])) % 360
                except (TypeError, ValueError):
                    rotation = 0
        break
    return {
        "title": tags.get("title", ""),
        "artist": tags.get("artist", ""),
        "comment": tags.get("comment", ""),
        "date": tags.get("date", ""),
        "rotation": rotation,
    }


def write_metadata(src: Path, out_path: Path, tags: dict,
                   rotation: Optional[int] = None) -> Path:
    """无损改元数据/旋转（重新封装，不转码）。"""
    src, out_path = Path(src), Path(out_path)
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
    # ffmpeg 7+ 用输入侧 -display_rotation 写显示矩阵（rotate 元数据已不生效）
    if rotation is not None:
        cmd += ["-display_rotation", str(rotation)]
    cmd += ["-i", str(src), "-map", "0", "-c", "copy"]
    for key in ("title", "artist", "comment", "date"):
        value = tags.get(key)
        if value is not None:
            cmd += ["-metadata", f"{key}={value}"]
    cmd += [str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800,
                          creationflags=_no_window())
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise EditError(f"写入元数据失败: {detail.strip()}")
    return out_path


# ==================== 轨道增删替换 ====================

def _pick_container(src: Path, need_mkv: bool = False) -> str:
    suffix = src.suffix.lower()
    if need_mkv:
        return ".mkv"
    return suffix if suffix in (".mp4", ".mkv", ".mov") else ".mkv"


def add_external_audio(src: Path, audio_file: Path, out_path: Path,
                       replace: bool = False) -> Path:
    """追加/替换音轨（不转码）。外部音轨编码不适合 mp4 时自动改用 mkv。"""
    src, audio_file = Path(src), Path(audio_file)
    tracks = [t for t in probe_tracks(audio_file) if t.get("codec_type") == "audio"]
    if not tracks:
        raise EditError("外部文件没有音轨")
    codec = str(tracks[0].get("codec_name") or "").lower()
    mp4_ok = codec in ("aac", "mp3", "alac", "ac3", "eac3")
    out_path = Path(out_path)
    if out_path.suffix.lower() in (".mp4", ".mov") and not mp4_ok:
        out_path = out_path.with_suffix(".mkv")
    if replace:
        maps = ["-map", "0:v?", "-map", "1:a:0", "-map", "0:s?"]
    else:
        maps = ["-map", "0", "-map", "1:a:0"]
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-i", str(audio_file), *maps, "-c", "copy",
           str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600,
                          creationflags=_no_window())
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise EditError(f"音轨处理失败: {detail.strip()}")
    return out_path


def add_external_subtitle(src: Path, sub_file: Path, out_path: Path) -> Path:
    """给视频添加字幕轨（mp4 自动转 mov_text，其他容器直接复制）。"""
    src, sub_file = Path(src), Path(sub_file)
    out_path = Path(out_path)
    mp4_out = out_path.suffix.lower() in (".mp4", ".mov")
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-i", str(sub_file),
           "-map", "0", "-map", "1:s:0", "-c", "copy"]
    if mp4_out:
        cmd += ["-c:s", "mov_text"]
    cmd += [str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600,
                          creationflags=_no_window())
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise EditError(f"添加字幕失败: {detail.strip()}")
    return out_path


def remove_tracks(src: Path, stream_indices: List[int], out_path: Path) -> Path:
    """删除指定轨道（不转码）。"""
    if not stream_indices:
        raise EditError("没有选中要删除的轨道")
    src, out_path = Path(src), Path(out_path)
    cmd = [find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-map", "0"]
    for index in stream_indices:
        cmd += ["-map", f"-0:{int(index)}"]
    cmd += ["-c", "copy", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600,
                          creationflags=_no_window())
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise EditError(f"删除轨道失败: {detail.strip()}")
    return out_path
