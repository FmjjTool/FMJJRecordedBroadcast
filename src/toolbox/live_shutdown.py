"""关闭录制前的收尾打包：把正在写的 TS/FLV 定稿，再合并分段、重封装成 mp4。

为什么需要：
- 录制中的文件由 ffmpeg 持续写入，直接结束进程可能丢掉文件尾（moov/索引），
  表现为「打不开 / 时长不对 / 花屏」。
- 关闭窗口或退出软件时先 `stop()`（发 q → 等 ffmpeg 自己收尾），再走这里把
  同场分段合并、重封装成 mp4，用户拿到的是可直接播放的成品。

约束：
- **不改动、不删除原始录制文件**，只额外产出 `<主干>_打包.mp4`；打包失败保留原文件。
- 纯 ffmpeg `-c copy`，不重编码，画质不变；找不到 ffmpeg 时如实返回失败原因。
- 本模块不依赖 Qt，供界面关闭流程与测试共用。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

# 音轨录制产物不转 mp4
_AUDIO_EXTS = {".m4a", ".aac", ".mp3", ".wav"}
_SEGMENT_RE = re.compile(r"^(?P<base>.+)_(?P<index>\d{3})$")


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def base_stem(path) -> str:
    """分段文件的场次主干：`xxx_001` → `xxx`；普通文件返回自身主干。"""
    stem = Path(path).stem
    m = _SEGMENT_RE.match(stem)
    return m.group("base") if m else stem


def collect_segments(path) -> list[Path]:
    """同一场的全部分段（含主干文件），按文件名排序。"""
    path = Path(path)
    stem = path.stem
    base = base_stem(path)
    folder = path.parent
    if not folder.is_dir():
        return [path] if path.exists() else []

    found = []
    for item in folder.iterdir():
        if not item.is_file() or item.suffix.lower() != path.suffix.lower():
            continue
        name = item.stem
        if name == base or re.match(r"^" + re.escape(base) + r"_\d{3}$", name):
            if _size(item) > 0:
                found.append(item)
    if not found:
        return [path] if _size(path) > 0 else []
    found.sort(key=lambda p: (0 if p.stem == base else 1, p.stem))
    return found


def output_path(source) -> Path:
    """打包输出：`<主干>_打包.mp4`（音轨保留原后缀），重名自动加序号。"""
    source = Path(source)
    suffix = source.suffix if source.suffix.lower() in _AUDIO_EXTS else ".mp4"
    folder = source.parent
    stem = base_stem(source)
    if stem.endswith("_打包"):
        stem = stem[:-3]
    candidate = folder / f"{stem}_打包{suffix}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{stem}_打包_{index}{suffix}"
        index += 1
    return candidate


def _concat_list(paths: list[Path], workdir: Path) -> Path:
    """写 ffmpeg concat 清单（路径里的单引号按官方规则转义）。"""
    list_path = workdir / "concat.txt"
    lines = []
    for p in paths:
        safe = str(p).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def package_file(path, progress=None) -> dict:
    """把一场录制打包成 mp4。返回 {ok, source, output, message, segments}。"""
    sources = collect_segments(path)
    result = {"ok": False, "source": str(path), "output": "", "message": "",
              "segments": [str(p) for p in sources]}
    if not sources:
        result["message"] = "没有可打包的录制文件（可能还没写入内容）"
        return result

    from toolbox.live_video import find_ffmpeg
    exe = find_ffmpeg()
    if not exe:
        result["message"] = "未找到 ffmpeg，无法打包（原文件已保留）"
        return result

    out = output_path(sources[0])
    if progress:
        progress(f"打包 {sources[0].stem}（{len(sources)} 个文件）…")

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    with tempfile.TemporaryDirectory(prefix="fmjj_pack_") as tmp:
        if len(sources) == 1:
            cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(sources[0]), "-c", "copy",
                   "-movflags", "+faststart", str(out)]
        else:
            list_path = _concat_list(sources, Path(tmp))
            cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
                   "-f", "concat", "-safe", "0", "-i", str(list_path),
                   "-c", "copy", "-movflags", "+faststart", str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=1800, **kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["message"] = f"打包失败：{type(exc).__name__}"
            return result

    if proc.returncode != 0 or not out.exists() or _size(out) <= 0:
        detail = (proc.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        result["message"] = "打包失败：" + (detail[-1][:120] if detail else "ffmpeg 返回错误")
        return result

    result.update({"ok": True, "output": str(out),
                   "message": f"已打包 {len(sources)} 个文件 → {out.name}"})
    return result


def package_many(paths, progress=None) -> list[dict]:
    """批量打包（按场次去重），progress(done, total, text) 用于界面进度。"""
    seen, results = set(), []
    files = []
    for path in paths or []:
        if not path:
            continue
        key = str(Path(path).parent / base_stem(path))
        if key in seen:
            continue
        seen.add(key)
        files.append(path)

    total = len(files)
    for index, path in enumerate(files, 1):
        def tick(text, _i=index, _t=total):
            if progress:
                progress(_i, _t, text)
        tick(f"准备打包 {Path(path).name}…")
        results.append(package_file(path, progress=tick))
    return results