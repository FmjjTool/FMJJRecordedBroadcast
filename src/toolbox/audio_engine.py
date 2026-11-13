"""音频提取引擎"""
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from .utils import is_video_file, find_ffmpeg_exe


AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".m4b", ".opus", ".amr"}
DATE_FOLDER_RE = re.compile(r"^20\d{6}$")


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def probe_audio_tracks(src: Path) -> List[dict]:
    """列出文件内全部音轨（参考 LosslessCut 的轨道选择）。"""
    from .edit_engine import probe_tracks
    return [t for t in probe_tracks(Path(src)) if t.get("codec_type") == "audio"]


def _audio_output(src: Path, stream: dict, tagged: bool) -> Path:
    """音轨输出路径：首条 AAC/ALAC 沿用 <原名>.m4a，其余带音轨号与真实编码后缀。"""
    from .edit_engine import track_extension
    ext = track_extension(stream)
    if tagged:
        return src.parent / f"{src.stem}_音轨{stream.get('index')}{ext}"
    return src.parent / f"{src.stem}{ext}"


def plan_audio_outputs(src: Path, all_tracks: bool = False) -> List[Path]:
    """预测提取产物路径（用于「已存在跳过」判定，不写文件）。"""
    src = Path(src)
    try:
        tracks = probe_audio_tracks(src)
    except Exception:
        return []
    if not tracks:
        return []
    chosen = tracks if all_tracks else tracks[:1]
    tagged = all_tracks or len(chosen) > 1
    return [_audio_output(src, t, tagged) for t in chosen]


def extract_audio_tracks(src: Path, all_tracks: bool = False) -> List[Path]:
    """按音轨提取音频（-c copy，不重新编码）。

    all_tracks=False 时只取第一条；True 时逐条输出。
    输出扩展名按编码自动选择，避免 AAC 以外的音轨塞进 m4a 失败。
    """
    src = Path(src)
    tracks = probe_audio_tracks(src)
    if not tracks:
        raise RuntimeError("文件内没有音轨")
    chosen = tracks if all_tracks else tracks[:1]
    tagged = all_tracks or len(chosen) > 1
    outputs: List[Path] = []
    for stream in chosen:
        dst = _audio_output(src, stream, tagged)
        if dst.exists() and dst.stat().st_size > 0:
            outputs.append(dst)
            continue
        cmd = [
            find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-map", f"0:{stream.get('index')}",
            "-c", "copy", str(dst),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=1800,
                              creationflags=_no_window())
        if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            err = (proc.stderr or b"").decode("utf-8", errors="ignore")[-200:]
            raise RuntimeError(err.strip() or f"提取失败: {dst.name}")
        outputs.append(dst)
    return outputs


def extract_audio_one(src: Path, dst: Path) -> Tuple[bool, str]:
    """提取单个视频的音频（兼容旧调用：只取第一条音轨，输出到 dst）。

    目标扩展名与音轨编码不兼容时自动改用真实编码后缀，避免 -c copy 失败。
    """
    src = Path(src)
    if not src.exists():
        return False, "源视频不存在（可能已被移动或改名），请重新扫描"
    try:
        tracks = probe_audio_tracks(src)
    except Exception as exc:  # noqa: BLE001
        return False, f"读取音轨失败: {exc}"
    if not tracks:
        return False, "文件内没有音轨"
    stream = tracks[0]
    from .edit_engine import track_extension
    real_ext = track_extension(stream)
    out = Path(dst)
    if real_ext != out.suffix.lower():
        out = out.with_suffix(real_ext)
    cmd = [
        find_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-map", f"0:{stream.get('index')}",
        "-c", "copy", str(out),
    ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=_no_window())
        _stdout, stderr = p.communicate(timeout=300)
        if p.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return True, ""
        stderr_str = stderr.decode("utf-8", errors="ignore")
        return False, stderr_str[-300:] if len(stderr_str) > 300 else stderr_str
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return False, "提取超时"
    except FileNotFoundError:
        return False, "ffmpeg 未找到"
    except Exception as e:
        return False, str(e)


def find_audio_videos(root: Path) -> List[Path]:
    """查找所有视频文件用于音频提取"""
    videos = []
    for path in root.rglob("*"):
        if path.is_file() and is_video_file(str(path)):
            videos.append(path)
    return sorted(videos)






def scan_audio_by_date(root: Path) -> Dict[str, List[Path]]:
    """按日期文件夹分组扫描音频文件。

    返回 {日期(YYYYMMDD) 或 "根目录": [Path, ...]}，按日期升序。
    """
    root = Path(root)
    groups: Dict[str, List[Path]] = {}
    if not root.is_dir():
        return groups
    # 工作目录根下直接摆放的音频
    root_audios = sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    if root_audios:
        groups["根目录"] = root_audios
    # 各日期文件夹（YYYYMMDD）内的音频
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not DATE_FOLDER_RE.match(d.name):
            continue
        audios = sorted(
            p for p in d.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS
        )
        if audios:
            groups[d.name] = audios
    return groups


def delete_files_safe(paths: List[Path]) -> Tuple[int, List[str]]:
    """安全删除文件：优先移入系统回收站（send2trash），失败则记录错误。

    返回 (成功删除数, 失败描述列表)。若 send2trash 不可用则退化为永久删除。

    注意：send2trash 在 Windows 某些路径（如系统盘 Temp、含 8.3 短路径名）
    下会误报"系统找不到指定的文件"，但文件实际已移入回收站——因此以
    原路径是否还存在作为最终判定标准。
    """
    try:
        import send2trash
        has_trash = True
    except Exception:
        has_trash = False
    ok = 0
    failed: List[str] = []
    for p in paths:
        try:
            if has_trash:
                send2trash.send2trash(str(p))
            else:
                os.remove(p)
            ok += 1
        except Exception as e:
            if not os.path.exists(p):
                # 误报：文件实际已被删除
                ok += 1
            else:
                failed.append(f"{p.name}: {e}")
    return ok, failed
