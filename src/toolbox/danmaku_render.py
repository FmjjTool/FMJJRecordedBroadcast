# -*- coding: utf-8 -*-
"""弹幕烧录成片：视频 + 弹幕包 → 带弹幕的 mp4。

- 输入视频：本软件录制的 .ts/.flv/.mp4（有 `<视频主干>_时间轴.json` 时按 P1 锚点对齐，
  没有时间轴则退回文件名起始时间 + 线性映射）；
- 输入弹幕：弹幕姬导出的 `.jsonl`（逐条 epoch 毫秒）或回放包 `.zip`（data.json）；
- 输出：H.264 mp4，画面沿用弹幕画布版式（右栏：北京时间/在线观众/礼物榜/弹幕列表），
  原音轨直接复制，不重编码。

渲染用 Pillow 逐帧合成（静态侧栏缓存 + 每条弹幕文本只渲染一次），帧管道直接喂给
ffmpeg（优先 NVENC，回退 x264），不落中间帧文件。
"""

from __future__ import annotations

import bisect
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from PIL import Image, ImageDraw

from . import image_render
from . import timeline as TL
from . import timecode as TC

BEIJING = timezone(timedelta(hours=8))
CHAT_COLORS = ("#7dd3fc", "#c4b5fd", "#f9a8d4", "#86efac", "#fde68a")
CHAT_TYPES = {"WebcastChatMessage": "chat", "WebcastGiftMessage": "gift",
              "DANMU_MSG": "chat", "SEND_GIFT": "gift"}


class DanmakuRenderError(RuntimeError):
    """弹幕合成失败（视频/弹幕无法读取、ffmpeg 未就绪等）。"""


@dataclass
class RenderOptions:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    font_size: float = 16          # 1280×720 基准字号，按画布缩放
    rows: int = 8
    sidebar_ratio: float = 0.21
    background_alpha: int = 235
    footer: str = "使用信息请标明来源"
    encoder: str = "auto"          # auto / nvenc / x264
    audio: bool = True


def _ffmpeg() -> str:
    from .utils import find_ffmpeg_exe
    return find_ffmpeg_exe()


def _run(args, timeout=None):
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)


def _ts_ms(m: dict) -> Optional[int]:
    try:
        v = float(m.get("timestamp") or m.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    return int(v) if math.isfinite(v) and 0 < v < 253402185600000 else None


def load_events(path) -> list[dict]:
    """读弹幕包 → 统一事件列表（按时间排序）。"""
    path = str(path)
    if not os.path.isfile(path):
        raise DanmakuRenderError("弹幕文件不存在：%s" % path)
    casts = None
    if path.lower().endswith((".jsonl", ".txt")):
        casts = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    casts.append(json.loads(line))
                except ValueError:
                    continue
    else:
        from .replay_bundle import parse_replay_file
        parsed = parse_replay_file(path)
        if not parsed:
            raise DanmakuRenderError("无法解析回放包：%s" % path)
        casts = parsed.get("casts") or []

    events = []
    for m in casts:
        method = str(m.get("method") or "")
        kind = CHAT_TYPES.get(method)
        if not kind:
            continue
        ts = _ts_ms(m)
        if not ts:
            continue
        user = m.get("user") or {}
        nick = str(user.get("name") or m.get("nickname") or "观众")[:80]
        gift = m.get("gift") or {}
        if kind == "gift" and not gift:
            continue
        if kind == "gift":
            body = "送出了 %s ×%s" % (gift.get("name") or "礼物",
                                      gift.get("count") or 1)
        else:
            body = str(m.get("content") or "")[:600]
            if not body:
                continue
        events.append({"ts": ts, "kind": kind, "nick": nick, "body": body,
                       "gift_name": str(gift.get("name") or ""),
                       "gift_count": int(gift.get("count") or 1),
                       "gift_price": int(gift.get("price") or 0)})
    events.sort(key=lambda e: e["ts"])
    return events


# ==================== 时间映射 ====================

def resolve_mapping(video, events) -> dict:
    """返回墙钟↔视频时间映射：{"timeline": tl|None, "base_wall": ms}。"""
    tl = TL.load(video)
    if tl:
        return {"timeline": tl, "base_wall": TL.anchor_wall_ms(tl, video)}
    base = TC.parse_start_time(video)
    if base is None and events:
        base = datetime.fromtimestamp(events[0]["ts"] / 1000)
    if base is None:
        raise DanmakuRenderError("视频没有时间轴、文件名也没有录制时间，无法对齐弹幕")
    return {"timeline": None, "base_wall": int(base.timestamp() * 1000)}


def _wall_to_video_ms(mapping, wall_ms, video) -> int:
    tl = mapping["timeline"]
    if tl:
        got = TL.video_ms_for_wall(tl, wall_ms, video)
        if got is not None:
            return got
    return int(wall_ms - mapping["base_wall"])


def _video_to_wall_ms(mapping, video_ms, video) -> int:
    tl = mapping["timeline"]
    if tl:
        got = TL.wall_ms_for_video(tl, video_ms, video)
        if got is not None:
            return got
    return int(mapping["base_wall"] + video_ms)


def pick_window(events, mapping, video, seconds: float) -> tuple[int, int]:
    """没有指定起点时，挑弹幕最密集的一段（chat 权重 2、礼物 1）。"""
    times = [_wall_to_video_ms(mapping, e["ts"], video) for e in events]
    best, best_score = 0, -1
    for i, t in enumerate(times):
        if t < 0:
            continue
        j = bisect.bisect_right(times, t + int(seconds * 1000))
        score = sum(2 if events[k]["kind"] == "chat" else 1 for k in range(i, j))
        if score > best_score:
            best, best_score = t, score
    return max(0, best), max(0, best) + int(seconds * 1000)


# ==================== 画面 ====================

class _Layout:
    def __init__(self, opt: RenderOptions):
        self.w, self.h = opt.width, opt.height
        self.scale = min(self.w / 1280, self.h / 720)
        self.sidebar_w = max(220, int(self.w * opt.sidebar_ratio))
        self.sidebar_x = self.w - self.sidebar_w
        self.top = int(200 * self.scale)
        self.footer_h = int(85 * self.scale)
        self.chat_h = self.h - self.top - self.footer_h
        self.font = opt.font_size * self.scale
        self.rows = opt.rows


def _font(size: float, bold=False):
    return image_render.font(max(10, int(size)), bold)


def _wrap(draw, text, fnt, first_w, rest_w):
    lines, cur, width = [], "", first_w
    for ch in text:
        if draw.textlength(cur + ch, font=fnt) > width and cur:
            lines.append(cur)
            cur, width = ch, rest_w
        else:
            cur += ch
    lines.append(cur)
    return lines


class _ChatBlock:
    """一条弹幕的预渲染小图（昵称彩色 + 正文，首行给昵称让位）。"""

    def __init__(self, nick, body, gift, lay: _Layout):
        self.nick = nick
        self.head = nick + "："
        size = lay.font
        head_font = _font(size, True)
        body_font = _font(size)
        pad = 12 * lay.scale
        inner = lay.sidebar_w - pad * 2
        self.line_h = int(size * 1.34)
        # 先用临时画布量宽度，再定小图尺寸
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        head_w = probe.textlength(self.head, font=head_font)
        lines = _wrap(probe, body, body_font, max(40, inner - head_w), inner)
        width = max([head_w] + [probe.textlength(t, font=body_font) for t in lines])
        width = int(min(inner, max(width, 10))) + 2
        height = self.line_h * len(lines) + 2
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = CHAT_COLORS[sum(map(ord, nick)) % len(CHAT_COLORS)]
        draw.text((0, 0), self.head, font=head_font, fill=color)
        x, y = head_w, 0
        body_color = "#facc15" if gift else "#ffffff"
        for line in lines:
            draw.text((x, y), line, font=body_font, fill=body_color)
            x, y = 0, y + self.line_h
        self.image = img
        self.height = height


def _sidebar_overlay(lay: _Layout, opt: RenderOptions) -> Image.Image:
    """静态部分：右侧底色、顶部条、右下角来源文字（只覆盖侧栏，粘贴更快）。"""
    layer = Image.new("RGBA", (lay.sidebar_w, lay.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    alpha = max(0, min(255, opt.background_alpha))
    d.rectangle((0, 0, lay.sidebar_w, lay.h), fill=(36, 37, 48, alpha))
    d.rectangle((0, 0, lay.sidebar_w, lay.top), fill=(55, 56, 69, alpha))
    d.text((12 * lay.scale, lay.h - 78 * lay.scale),
           opt.footer, font=_font(14 * lay.scale), fill="#d4d4d8")
    return layer


def _gift_ledger_add(sessions, rank, e, wall_s):
    """抖音礼物重复推送按会话增量累计（同 GiftLedger 的简化版）。"""
    key = (e["nick"], e["gift_name"])
    sess = sessions.get(key)
    if sess and (wall_s - sess["time"] > 15 or wall_s < sess["time"]):
        sess = None
    settled = sess["count"] if sess else 0
    delta = max(0, e["gift_count"] - settled)
    if e["gift_count"] == 1 and sess:
        delta = 1
    sessions[key] = {"count": max(e["gift_count"], settled), "time": wall_s}
    if delta:
        row = rank.setdefault(e["nick"], {"giftCount": 0, "totalValue": 0})
        row["giftCount"] += delta
        row["totalValue"] += e["gift_price"] * delta


def _clock_text(wall_ms) -> str:
    return datetime.fromtimestamp(wall_ms / 1000, BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _frame_with_overlay(base_rgb: Image.Image, lay: _Layout, opt: RenderOptions,
                        static: Image.Image, wall_ms, blocks, rank, online="—"):
    img = base_rgb
    img.paste(static, (lay.sidebar_x, 0), static)
    draw = ImageDraw.Draw(img, "RGBA")
    inset = int(40 * lay.scale)
    draw.rectangle((inset, inset, lay.w - lay.sidebar_w - inset, lay.h - inset),
                   outline=(255, 255, 255, 40), width=2)

    def shadow(xy, text, size):
        draw.text((xy[0] + 2, xy[1] + 2), text, font=_font(size * lay.scale),
                  fill=(0, 0, 0, 190))
        draw.text(xy, text, font=_font(size * lay.scale), fill="#ffffff")

    # 时钟/来源：压在主画面左上（与弹幕画布一致）
    shadow((20 * lay.scale, 12 * lay.scale), "北京时间（UTC+8）", 24)
    shadow((20 * lay.scale, 45 * lay.scale), _clock_text(wall_ms), 24)

    draw.text((lay.sidebar_x + 12 * lay.scale, 6 * lay.scale),
              "在线观众：" + online, font=_font(14 * lay.scale), fill="#e5e7eb")
    draw.text((lay.sidebar_x + 12 * lay.scale, 34 * lay.scale),
              "礼物贡献榜", font=_font(18 * lay.scale), fill="#ffffff")

    if not rank:
        draw.text((lay.sidebar_x + 12 * lay.scale, 95 * lay.scale), "等待礼物",
                  font=_font(14 * lay.scale), fill="#a1a1aa")
    for i, (user, data) in enumerate(rank[:3]):
        y = (65 + i * 43) * lay.scale
        draw.text((lay.sidebar_x + 12 * lay.scale, y), str(i + 1),
                  font=_font(18 * lay.scale), fill=("#fb7185", "#fb923c", "#facc15")[i])
        draw.ellipse((lay.sidebar_x + 34 * lay.scale, y + 3 * lay.scale,
                      lay.sidebar_x + 64 * lay.scale, y + 33 * lay.scale),
                     fill=CHAT_COLORS[sum(map(ord, user)) % len(CHAT_COLORS)])
        draw.text((lay.sidebar_x + 49 * lay.scale, y + 18 * lay.scale), user[:1],
                  font=_font(15 * lay.scale), fill="#22232c", anchor="mm")
        draw.text((lay.sidebar_x + 74 * lay.scale, y), user,
                  font=_font(15 * lay.scale), fill="#ffffff")
        draw.text((lay.sidebar_x + 74 * lay.scale, y + 20 * lay.scale),
                  "%s 件 · %s 抖币" % (format(data["giftCount"], ","),
                                     format(data["totalValue"], ",")),
                  font=_font(11 * lay.scale), fill="#d4d4d8")

    bottom = lay.top + lay.chat_h - 8 * lay.scale
    for block in reversed(blocks[-lay.rows:]):
        bottom -= block.height
        if bottom < lay.top:
            break
        img.paste(block.image, (int(lay.sidebar_x + 12 * lay.scale), int(bottom)),
                  block.image)
        bottom -= 14 * lay.scale
    return img


def _pick_encoder(opt: RenderOptions) -> list[str]:
    choice = (opt.encoder or "auto").lower()
    if choice in ("nvenc", "auto"):
        res = _run([_ffmpeg(), "-hide_banner", "-encoders"])
        if b"h264_nvenc" in (res.stdout or b""):
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", "-b:v", "0"]
        if choice == "nvenc":
            raise DanmakuRenderError("当前 ffmpeg 不支持 NVENC")
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def render(video, danmaku, out, *, start_ms: Optional[int] = None,
           duration_ms: Optional[int] = None, options: Optional[RenderOptions] = None,
           on_progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """把一个片段合成成带弹幕的 mp4；返回统计信息。"""
    opt = options or RenderOptions()
    video = os.path.abspath(str(video))
    if not os.path.isfile(video):
        raise DanmakuRenderError("视频不存在：%s" % video)
    events = load_events(danmaku)
    if not events:
        raise DanmakuRenderError("弹幕包里没有可渲染的弹幕/礼物")
    info = TC.probe(video)
    total_ms = int(info["duration"] * 1000)
    mapping = resolve_mapping(video, events)

    if start_ms is None:
        start_ms, end_ms = pick_window(events, mapping, video,
                                      (duration_ms or 60_000) / 1000)
        if duration_ms:
            end_ms = start_ms + duration_ms
    else:
        end_ms = total_ms if duration_ms is None else start_ms + duration_ms
    start_ms = max(0, min(int(start_ms), max(0, total_ms - 1000)))
    end_ms = min(int(end_ms), total_ms)
    if end_ms - start_ms < 1000:
        raise DanmakuRenderError("片段太短（%d ms），至少 1 秒" % (end_ms - start_ms))

    # 预计算每条弹幕在视频里的出现时间
    prepared = []
    for e in events:
        at = _wall_to_video_ms(mapping, e["ts"], video)
        if at < start_ms - 2000:
            # 起点之前的礼物仍要进榜
            prepared.append({"event": e, "at": at, "before": True})
            continue
        if at > end_ms:
            continue
        prepared.append({"event": e, "at": at, "before": False})
    prepared.sort(key=lambda x: (x["before"], x["at"]))
    upcoming = [p for p in prepared if not p["before"]]
    before = [p for p in prepared if p["before"]]

    lay = _Layout(opt)
    static = _sidebar_overlay(lay, opt)
    block_cache: dict[int, _ChatBlock] = {}
    sessions, rank = {}, {}
    for p in before:
        e = p["event"]
        if e["kind"] == "gift":
            _gift_ledger_add(sessions, rank, e, e["ts"] / 1000)

    fps = max(1, int(opt.fps))
    frames = max(1, int(round((end_ms - start_ms) / 1000 * fps)))
    enc = _pick_encoder(opt)

    decode = [_ffmpeg(), "-hide_banner", "-loglevel", "error",
              "-ss", "%.3f" % (start_ms / 1000), "-i", video,
              "-t", "%.3f" % ((end_ms - start_ms) / 1000),
              "-vf", "fps=%d,scale=%d:%d:force_original_aspect_ratio=decrease,"
                     "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (fps, opt.width, opt.height,
                                                       opt.width, opt.height),
              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    encode = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
              "-f", "rawvideo", "-pix_fmt", "rgb24",
              "-s", "%dx%d" % (opt.width, opt.height), "-r", str(fps), "-i", "-"]
    if opt.audio and info.get("has_audio"):
        encode += ["-ss", "%.3f" % (start_ms / 1000),
                   "-t", "%.3f" % ((end_ms - start_ms) / 1000), "-i", video,
                   "-map", "0:v:0", "-map", "1:a?"]
    encode += enc + ["-pix_fmt", "yuv420p"]
    if opt.audio and info.get("has_audio"):
        encode += ["-c:a", "copy", "-shortest"]
    encode += ["-movflags", "+faststart", out]

    kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    dec = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           **kwargs)
    encp = subprocess.Popen(encode, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, **kwargs)
    frame_size = opt.width * opt.height * 3
    cursor = 0
    last_raw = None
    try:
        for idx in range(frames):
            raw = dec.stdout.read(frame_size)
            if len(raw) < frame_size:      # 源视频比预期短：重复上一帧
                raw = last_raw or raw + b"\x00" * (frame_size - len(raw))
            last_raw = raw
            base = Image.frombytes("RGB", (opt.width, opt.height), raw)
            now_ms = start_ms + idx * 1000 / fps
            while cursor < len(upcoming) and upcoming[cursor]["at"] <= now_ms:
                p = upcoming[cursor]
                e = p["event"]
                cursor += 1
                if e["kind"] == "gift":
                    _gift_ledger_add(sessions, rank, e,
                                     _video_to_wall_ms(mapping, p["at"], video) / 1000)
                key = cursor - 1
                block_cache[key] = _ChatBlock(e["nick"], e["body"], e["kind"] == "gift", lay)
            blocks = [block_cache[k] for k in sorted(block_cache)[-lay.rows * 3:]]
            wall_ms = _video_to_wall_ms(mapping, now_ms, video)
            frame = _frame_with_overlay(
                base, lay, opt, static, wall_ms, blocks,
                sorted(rank.items(), key=lambda kv: (-kv[1]["totalValue"],
                                                     -kv[1]["giftCount"], kv[0])))
            encp.stdin.write(frame.tobytes())
            if on_progress:
                on_progress(idx + 1, frames)
    finally:
        try:
            encp.stdin.close()
        except Exception:
            pass
        for proc in (dec, encp):
            try:
                proc.wait(timeout=60)
            except Exception:
                proc.kill()
    err = (encp.stderr.read() or b"").decode("utf-8", "replace").strip()
    for stream in (dec.stdout, dec.stderr, encp.stderr):
        try:
            stream.close()
        except Exception:
            pass
    if encp.returncode not in (0, None):
        raise DanmakuRenderError("ffmpeg 编码失败：%s" % (err[-400:] or "未知错误"))
    if not os.path.exists(out) or os.path.getsize(out) < 1024:
        raise DanmakuRenderError("没有生成有效输出：%s" % out)
    return {"ok": True, "out": out, "start_ms": start_ms, "end_ms": end_ms,
            "frames": frames, "events": len(upcoming),
            "timeline": bool(mapping["timeline"])}