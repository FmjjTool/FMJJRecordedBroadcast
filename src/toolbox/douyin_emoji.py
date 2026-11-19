# -*- coding: utf-8 -*-
"""抖音表情图收集与导出（会员表情 / 粉丝团表情 / 合并表情）。

会员表情不在官方公开表情库里，只会随聊天消息以图片直链下发（rtf 片段
type=2 + url），所以这里只做「已经收到的表情」收集：
  1. 直播间实时消息（弹幕姬页把 rtf 片段喂进来）；
  2. 本地已保存的弹幕包（.jsonl 实时记录 / 回放包 .zip / data.json）。
收集到的表情图按 URL 去重（忽略签名参数），导出成图片文件（保留原格式
webp/gif/png），文件名取表情名（如「[点赞]」→ 点赞.webp）。

不依赖 Qt：GUI 只负责把 piece 喂进来、展示和调 download_items()。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")
_REFERER = "https://live.douyin.com/"

_BAD_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _clean_name(text, fallback="表情"):
    """表情名规整：去掉 [ ] 括号、非法文件名字符，限长 24。"""
    s = str(text or "").strip().strip("[]［］").strip()
    s = _BAD_NAME.sub("_", s)
    return (s or fallback)[:24]


def url_key(url):
    """去重键：去掉签名/过期参数，同一张图不同签名视为同一个。"""
    return str(url or "").split("?", 1)[0]


def piece_items(pieces, nick="", ts=0):
    """rtf 片段 → 表情条目（只要带 url 的图片片段）。"""
    out = []
    for p in pieces or []:
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        out.append({"url": url, "key": url_key(url),
                    "name": _clean_name(p.get("text")), "nick": nick or "",
                    "ts": int(ts or 0)})
    return out


def casts_items(casts):
    """弹幕记录列表（dycast 结构）→ 表情条目。"""
    out = []
    for c in casts or []:
        if not isinstance(c, dict):
            continue
        nick = ((c.get("user") or {}).get("name") or "")[:40]
        out.extend(piece_items(c.get("rtfContent") or c.get("rtf") or [],
                               nick, c.get("timestamp") or 0))
    return out


def load_casts(path):
    """读弹幕包 → casts 列表；支持 .jsonl / .txt / .zip / .json，失败返回 []。"""
    path = str(path)
    if not os.path.isfile(path):
        return []
    low = path.lower()
    if low.endswith((".jsonl", ".txt")):
        casts = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    casts.append(json.loads(line))
                except ValueError:
                    continue
        return casts
    if low.endswith((".zip", ".json")):
        from toolbox.replay_bundle import parse_replay_file
        parsed = parse_replay_file(path)
        return list((parsed or {}).get("casts") or [])
    return []


def collect_files(paths):
    """批量读多个弹幕包 → 去重后的表情条目（保持首次出现顺序）。"""
    seen, items = set(), []
    for p in paths or []:
        for it in casts_items(load_casts(p)):
            if it["key"] in seen:
                continue
            seen.add(it["key"])
            items.append(it)
    return items


def _sniff_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    return ".bin"


def _unique_path(out_dir: Path, stem: str, ext: str, used: dict):
    """同名表情按出现顺序加序号：点赞.webp / 点赞_2.webp。"""
    n = used.get(stem, 0) + 1
    used[stem] = n
    name = stem if n == 1 else f"{stem}_{n}"
    return out_dir / (name + ext)


def download_items(items, out_dir, *, on_progress=None, stop=None):
    """下载表情图到 out_dir。

    on_progress(done, total, name, ok) 在每个条目结束时回调；
    stop() 返回 True 时提前停止。返回 {"saved": [...], "failed": [...]}。
    """
    import httpx

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved, failed, used = [], [], {}
    total = len(items or [])
    with httpx.Client(timeout=20, follow_redirects=True, trust_env=False,
                      headers={"User-Agent": _UA, "Referer": _REFERER}) as c:
        for i, it in enumerate(items or [], 1):
            if stop and stop():
                break
            url, name = it.get("url"), it.get("name") or "表情"
            try:
                r = c.get(url)
                if r.status_code != 200 or not r.content:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ext = _sniff_ext(r.content)
                if ext == ".bin":
                    raise RuntimeError("不是图片")
                fp = _unique_path(out, _clean_name(name), ext, used)
                fp.write_bytes(r.content)
                saved.append(str(fp))
                ok = True
            except Exception as e:  # noqa: BLE001 - 单条失败不影响其余
                failed.append({"url": url, "name": name,
                               "error": f"{type(e).__name__}: {e}"[:120]})
                ok = False
            if on_progress:
                try:
                    on_progress(i, total, name, ok)
                except Exception:
                    pass
    return {"saved": saved, "failed": failed}