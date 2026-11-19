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


# 会员表情栏（房间一次性接口，需登录态）固定 7 个，id → 中文名
_VIP_NAMES = {1000: "哈哈", 1001: "爱了", 1002: "比心", 1003: "666",
              1004: "优秀", 1005: "感谢", 1006: "酷"}

_ENTER_BASE = {
    "aid": "6383", "app_name": "douyin_web", "live_id": "1",
    "device_platform": "web", "language": "zh-CN", "browser_language": "zh-CN",
    "browser_platform": "Win32", "browser_name": "Mozilla",
}

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

def fetch_vip_emojis(room_num, timeout=20):
    """一次性拉全「会员表情栏」，不用等有人发。

    链路：room/web/enter 取 room_id + anchor_id → privilege/subscribe/info
    取 emoji_list（房间页进房时前端也走这条）。需要抖音登录 Cookie，
    未登录返回 status_code=20003（User doesn't login）。

    返回 {"items": [...], "error": "", "describe": "", "member": bool}
    """
    import urllib.parse as up

    import httpx

    from toolbox.douyin_live import UA, _get_douyin_cookie

    res = {"items": [], "error": "", "describe": "", "member": False}
    cookie = _get_douyin_cookie() or ""
    base = dict(_ENTER_BASE)
    base["browser_version"] = UA
    ref = "https://live.douyin.com/%s" % room_num
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": UA, "Referer": ref}) as c:
        try:
            c.get(ref, timeout=timeout)
            r = c.get("https://live.douyin.com/webcast/room/web/enter/?"
                      + up.urlencode({**base, "web_rid": str(room_num)}))
            if not r.text.strip().startswith("{"):
                res["error"] = "房间信息接口异常（HTTP %s）" % r.status_code
                return res
            rooms = ((r.json().get("data") or {}).get("data")) or []
            node = rooms[0] if rooms else {}
            room_id = node.get("id_str") or ""
            owner = node.get("owner") or {}
            anchor_id = owner.get("id_str") or ""
            if not room_id or not anchor_id:
                res["error"] = "未取到 room_id/anchor_id（直播间可能已结束）"
                return res

            h = {"Referer": ref}
            if cookie:
                h["Cookie"] = cookie
            q = dict(base, purchase_type="1", room_id=str(room_id),
                     anchor_id=str(anchor_id))
            r2 = c.get("https://live.douyin.com/webcast/privilege/subscribe/info/?"
                       + up.urlencode(q), headers=h)
            j = r2.json() if r2.text.strip().startswith("{") else {}
        except Exception as e:  # noqa: BLE001 - 网络/解析失败统一回给界面
            res["error"] = "%s: %s" % (type(e).__name__, e)
            return res

    if j.get("status_code") == 20003:
        res["error"] = "未登录抖音（会员表情栏需要登录态：先在 Cookie 管理里登录抖音）"
        return res
    data = j.get("data")
    if not isinstance(data, dict):
        res["error"] = "接口返回异常（status_code=%s）" % j.get("status_code")
        return res
    tab = data.get("emoji_tab_info") or {}
    res["describe"] = tab.get("describe") or ""
    res["member"] = bool((data.get("user_data") or {}).get("expired_time"))
    for e in data.get("emoji_list") or []:
        img = e.get("img") or {}
        urls = img.get("url_list") or []
        if not urls:
            continue
        uri = img.get("uri") or ""
        stem = uri.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        name = _VIP_NAMES.get(e.get("id")) or _clean_name(stem or "会员表情")
        res["items"].append({"url": urls[0], "key": url_key(urls[0]),
                             "name": name, "nick": "", "ts": 0})
    if not res["items"]:
        res["error"] = res["error"] or "接口没有返回表情（该主播可能未开通会员表情）"
    return res
