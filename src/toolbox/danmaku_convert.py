# -*- coding: utf-8 -*-
"""弹幕文件 → 弹幕机 TXT（L4 纯格式转换，不依赖 Qt）。

输入（按扩展名分派）：
- `.jsonl` / `.txt`：抖音弹幕姬、录播姬导出的逐行 JSON
  `{"method","timestamp","content","user":{"name"}}`；
- `.xml`：B站弹幕 XML，`<d p="相对秒,模式,字号,颜色,时间戳ms,弹幕池,用户ID,行ID">内容</d>`。

输出（UTF-8，每行一条）：
    HH:mm:ss.SSS|[昵称]：内容

与回放包内分类 TXT 同一行格式。默认把最早一条弹幕对齐到 `00:00:00.000`
（播放器按视频时间轴播放，不依赖录制钟点）；`zero_based=False` 时保留录制时的
绝对钟点。只保留聊天 / 表情弹幕，礼物、进场、点赞、关注等事件不写入。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from xml.etree import ElementTree as ET

# 聊天弹幕的 method；空 method 的 JSONL 行也按弹幕处理
CHAT_METHODS = {"WebcastChatMessage", "WebcastEmojiChatMessage", "DANMU_MSG", ""}

_BAD_NAME = str.maketrans({"|": "｜", "：": " ", ":": " ", "\r": " ", "\n": " "})
_BAD_TEXT = str.maketrans({"\r": " ", "\n": " "})


class DanmakuConvertError(ValueError):
    """弹幕文件无法读取或没有可转换的弹幕。"""


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    s, milli = divmod(ms, 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}.{milli:03d}"


def _to_ms(value) -> int | None:
    """秒 / 毫秒时间戳 → 毫秒；无效返回 None。"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return None
    if not v or v < 0 or v > 253402185600000:
        return None
    if v < 100000000000:        # B站 XML 的 date 是秒
        v *= 1000
    return int(v)


def _clean_name(name) -> str:
    text = str(name or "").translate(_BAD_NAME).strip()
    return text or "观众"


def _clean_text(text) -> str:
    return str(text or "").translate(_BAD_TEXT).strip()


def _jsonl_events(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            if str(m.get("method") or "") not in CHAT_METHODS:
                continue
            ts = _to_ms(m.get("timestamp") or m.get("ts"))
            body = _clean_text(m.get("content") or m.get("text"))
            if not ts or not body:
                continue
            user = m.get("user") or {}
            name = user.get("name") if isinstance(user, dict) else ""
            out.append({"ts": ts, "nick": _clean_name(name or m.get("nickname")),
                        "body": body})
    return out


def _xml_events(path) -> list[dict]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise DanmakuConvertError(f"XML 解析失败：{e}") from e
    rows = []
    for node in root.iter():
        if node.tag.lower() != "d":
            continue
        parts = str(node.get("p") or "").split(",")
        if len(parts) < 5:
            continue
        try:
            rel = max(0.0, float(parts[0]))
            mode = int(float(parts[1]))
        except ValueError:
            continue
        if mode >= 7:           # 高级 / 脚本弹幕，弹幕机不识别
            continue
        body = _clean_text(node.text)
        if not body:
            continue
        rel_ms = int(rel * 1000)
        abs_ms = _to_ms(parts[4])
        author = parts[6] if len(parts) > 6 else ""
        rows.append({"rel": rel_ms, "abs": abs_ms, "nick": _clean_name(author),
                     "body": body})
    if not rows:
        return []
    valid = [(r["abs"], r["rel"]) for r in rows if r["abs"]]
    if valid and len(valid) * 2 >= len(rows):
        # 绝大多数带 date：date 缺失的行用同场基准换算，避免混排错序
        base = min(abs_ms - rel for abs_ms, rel in valid)
        for r in rows:
            r["ts"] = r["abs"] or base + r["rel"]
    else:
        for r in rows:
            r["ts"] = r["rel"]
    return [{"ts": r["ts"], "nick": r["nick"], "body": r["body"]} for r in rows]


def load_events(path) -> list[dict]:
    """读取弹幕文件 → [{ts, nick, body}]（按时间排序、去重）。"""
    path = str(path)
    if not os.path.isfile(path):
        raise DanmakuConvertError("文件不存在：%s" % path)
    low = path.lower()
    if low.endswith(".xml"):
        events = _xml_events(path)
    elif low.endswith((".jsonl", ".txt")):
        events = _jsonl_events(path)
    else:
        raise DanmakuConvertError("仅支持 .jsonl / .xml：%s" % os.path.basename(path))

    seen = set()
    unique = []
    for e in sorted(events, key=lambda x: x["ts"]):
        key = (e["ts"], e["nick"], e["body"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique


def render_txt(events, *, zero_based: bool = True) -> str:
    """事件列表 → 弹幕机 TXT 文本。"""
    if not events:
        return ""
    base = min(e["ts"] for e in events) if zero_based else 0
    lines = []
    for e in events:
        if zero_based:
            stamp = _fmt_ms(e["ts"] - base)
        else:
            stamp = datetime.fromtimestamp(e["ts"] / 1000).strftime("%H:%M:%S.") + \
                f"{e['ts'] % 1000:03d}"
        lines.append(f"{stamp}|[{e['nick']}]：{e['body']}")
    return "\n".join(lines)


def _target_path(src, out_dir) -> str:
    stem = os.path.splitext(os.path.basename(src))[0]
    name = stem + ".txt" if stem.endswith("_弹幕") else stem + "_弹幕.txt"
    folder = str(out_dir or os.path.dirname(os.path.abspath(src)))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}_弹幕_{n}.txt")
        n += 1
    return path


def convert_file(src, out_dir=None, *, zero_based: bool = True) -> dict:
    """转换单个弹幕文件 → 返回 {"out", "count"}。无弹幕时抛 DanmakuConvertError。"""
    events = load_events(src)
    if not events:
        raise DanmakuConvertError("没有可转换的聊天弹幕：%s" % os.path.basename(src))
    out = _target_path(src, out_dir)
    with open(out, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(render_txt(events, zero_based=zero_based))
        fh.write("\n")
    return {"out": out, "count": len(events)}


def convert_many(paths, out_dir=None, *, zero_based: bool = True) -> dict:
    """批量转换，单个文件失败不影响其余。"""
    ok, failed = [], []
    for path in paths:
        try:
            ok.append(convert_file(path, out_dir, zero_based=zero_based))
        except DanmakuConvertError as e:
            failed.append({"src": path, "error": str(e)})
        except OSError as e:
            failed.append({"src": path, "error": "写入失败：%s" % e})
    return {"files": ok, "failed": failed}