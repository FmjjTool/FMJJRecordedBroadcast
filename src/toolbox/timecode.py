# -*- coding: utf-8 -*-
"""录制完成后给视频写容器时间码（QuickTime timecode / tmcd 轨）。

- 输入：.ts / .flv / .mp4 等 ffmpeg 能读的录制文件；
- 输出：.mp4（无损重封装 -c copy，画质不变），附带一条 tmcd 数据轨；
- 时间码：默认取文件名里的录制起始时间（如 `主播_20261116-183009.ts`），
  由调用方显式指定时以参数为准。

只做「重封装 + 写轨」，不烧录画面、不重编码，一小时素材通常几秒到一分钟。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from typing import Callable, Optional

_EXT_IN = (".ts", ".flv", ".mp4", ".mkv", ".m4v")

# 20261116-183009 / 2026-11-16_18-30-09 / 20261116_183009
_NAME_RE = re.compile(
    r"(?P<y>\d{4})-?(?P<mo>\d{2})-?(?P<d>\d{2})[_\-\sT]+"
    r"(?P<h>\d{2})[-:]?(?P<mi>\d{2})[-:]?(?P<s>\d{2})")
_SEGMENT_RE = re.compile(r"_\d{3}$")


class TimecodeError(RuntimeError):
    """写时间码失败（ffmpeg/ffprobe 不可用、源文件异常、输出校验不通过）。"""


def _run(args, timeout=None):
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)


def _ffmpeg() -> str:
    from .utils import find_ffmpeg_exe
    return find_ffmpeg_exe()


def _ffprobe() -> str:
    from .utils import find_ffprobe_exe
    return find_ffprobe_exe()


def parse_start_time(path) -> Optional[datetime]:
    """从文件名解析录制起始时间；解析不到返回 None。"""
    m = _NAME_RE.search(os.path.basename(str(path)))
    if not m:
        return None
    try:
        return datetime(int(m["y"]), int(m["mo"]), int(m["d"]),
                        int(m["h"]), int(m["mi"]), int(m["s"]))
    except ValueError:
        return None


def is_segment_file(path) -> bool:
    """是否是分段文件（`…_001.ts`）：起始时间无法从文件名直接得出。"""
    return bool(_SEGMENT_RE.search(os.path.splitext(os.path.basename(str(path)))[0]))


def probe(path) -> dict:
    """读取源文件参数：fps / 时长 / 视频与音频编码。"""
    src = str(path)
    if not os.path.isfile(src):
        raise TimecodeError("文件不存在：%s" % src)
    res = _run([_ffprobe(), "-v", "error", "-show_streams", "-show_format",
                "-of", "json", src])
    if res.returncode != 0:
        raise TimecodeError("ffprobe 读取失败：%s" % _tail(res.stderr))
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace"))
    except ValueError as exc:
        raise TimecodeError("ffprobe 输出无法解析：%s" % exc) from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise TimecodeError("没有视频流，无法写时间码")
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str(video.get(key) or "")
        try:
            value = float(Fraction(raw))
        except (ValueError, ZeroDivisionError):
            continue
        if value > 1:
            fps = value
            break
    if not fps:
        raise TimecodeError("读不到帧率，无法确定时间码帧位")
    duration = 0.0
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"fps": fps, "duration": duration,
            "video": (video.get("codec_name") or ""),
            "audio": (audio.get("codec_name") or "") if audio else "",
            "has_audio": bool(audio)}


def format_timecode(start: datetime, fps: float) -> str:
    """时间码字符串 HH:MM:SS:FF（24 小时制，本段从第 00 帧起算）。"""
    if not isinstance(start, datetime):
        raise TimecodeError("起始时间必须是 datetime")
    return "%02d:%02d:%02d:00" % (start.hour % 24, start.minute, start.second)


def default_output(src) -> str:
    """默认输出：同目录、同名 + `_时间码.mp4`（重名自动加序号）。"""
    root, _ = os.path.splitext(str(src))
    base = root + "_时间码.mp4"
    if not os.path.exists(base):
        return base
    for i in range(2, 1000):
        cand = "%s_时间码(%d).mp4" % (root, i)
        if not os.path.exists(cand):
            return cand
    return base


def _timeline_start(src):
    """有录制时间轴就用首帧锚点（比文件名秒级时间准）。"""
    try:
        from . import timeline as TL
        tl = TL.load(src)
        return TL.start_datetime(tl, src) if tl else None
    except Exception:  # noqa: BLE001
        return None


def _tail(raw: bytes, limit: int = 600) -> str:
    text = (raw or b"").decode("utf-8", "replace").strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-3:])[:limit] or "未知错误"


def _has_timecode_track(path) -> tuple[bool, str]:
    res = _run([_ffprobe(), "-v", "error", "-show_streams", "-of", "json", str(path)])
    if res.returncode != 0:
        return False, ""
    try:
        streams = json.loads(res.stdout.decode("utf-8", "replace")).get("streams") or []
    except ValueError:
        return False, ""
    for s in streams:
        if str(s.get("codec_tag_string") or "") == "tmcd":
            tags = s.get("tags") or {}
            return True, str(tags.get("timecode") or "")
    return False, ""


def write_timecode(src, dst=None, start: Optional[datetime] = None, *,
                   overwrite: bool = False,
                   on_log: Optional[Callable[[str], None]] = None) -> dict:
    """给录制文件写 tmcd 时间码轨（重封装为 mp4）。

    返回 {"ok", "out", "timecode", "fps", "duration", "error"}；失败抛 TimecodeError。
    """
    log = on_log or (lambda _t: None)
    src = os.path.abspath(str(src))
    if not os.path.isfile(src):
        raise TimecodeError("文件不存在：%s" % src)
    if os.path.splitext(src)[1].lower() not in _EXT_IN:
        raise TimecodeError("不支持的格式：%s（可用 .ts/.flv/.mp4）"
                            % os.path.splitext(src)[1])
    info = probe(src)
    if start is None:
        start = _timeline_start(src)
        if start is not None:
            log("起始时间取自录制时间轴（首帧）：%s" % start.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            start = parse_start_time(src)
            if start is None:
                raise TimecodeError("文件名里没有录制时间，请手动指定起始时间")
            log("起始时间取自文件名：%s" % start.strftime("%Y-%m-%d %H:%M:%S"))
    if is_segment_file(src):
        log("分段文件：起始时间按给定值写入，请确认是否是该段自己的起点")

    tc = format_timecode(start, info["fps"])
    out = os.path.abspath(str(dst)) if dst else default_output(src)
    if os.path.normcase(out) == os.path.normcase(src):
        raise TimecodeError("输出不能覆盖源文件")
    if os.path.exists(out) and not overwrite:
        raise TimecodeError("输出文件已存在：%s（可勾选覆盖或换名字）" % out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    log("写入时间码 %s（%.3f fps，时长 %.1f 秒）" % (tc, info["fps"], info["duration"]))
    base = [_ffmpeg(), "-hide_banner", "-loglevel", "warning", "-y",
            "-i", src, "-map", "0:v:0", "-map", "0:a?",
            "-c", "copy", "-timecode", tc, "-movflags", "+faststart", out]
    res = _run(base)
    if res.returncode != 0:
        # 音轨不是 mp4 兼容编码时（如部分平台 opus），只转音频、视频仍不重编码
        log("原样封装失败，尝试音轨转 AAC：%s" % _tail(res.stderr, 200))
        retry = [a for a in base]
        idx = retry.index("-c")
        retry[idx:idx + 2] = ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
        res = _run(retry)
    if res.returncode != 0:
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        raise TimecodeError("ffmpeg 封装失败：%s" % _tail(res.stderr))

    ok, written = _has_timecode_track(out)
    if not ok:
        raise TimecodeError("输出里没有 tmcd 时间码轨（%s）" % out)
    if written and written != tc:
        log("注意：读回时间码为 %s（期望 %s）" % (written, tc))
    log("完成：%s" % out)
    return {"ok": True, "out": out, "timecode": tc, "readback": written,
            "fps": info["fps"], "duration": info["duration"], "error": ""}