# -*- coding: utf-8 -*-
"""抖音会员表情栏拉取与导出。

会员表情不在官方公开表情库里，只在房间进房接口
`webcast/privilege/subscribe/info` 的 emoji_list 里一次性返回（需登录态）。
本模块只负责：确定主播 ID → 拉全表情栏 → 按「CDN 归一后的路径」去重
（同一张图会在 lf3/lf5/lf6、p3/p11 之间换域名下发，域名不同也算同一张）
→ 导出成图片文件（保留原格式 webp/gif/png），文件名取表情名，同名加序号。

不依赖 Qt：GUI 只负责展示和调 fetch_vip_emojis() / download_items()。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit


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

# 未开播/已结束的直播间页面里仍有这段（转义 JSON）：
#   "roomId":"7709824534500608818","web_rid":"582775732015","anchor":{"id_str":"78087831246",...
_ROOM_ANCHOR_RE = re.compile(
    r'"roomId"\s*:\s*"(\d+)"\s*,\s*"web_rid"\s*:\s*"[^"]*"\s*,\s*"anchor"\s*:\s*\{\s*"id_str"\s*:\s*"(\d+)"')


def _clean_name(text, fallback="表情"):
    """表情名规整：去掉 [ ] 括号、非法文件名字符，限长 24。"""
    s = str(text or "").strip().strip("[]［］").strip()
    s = _BAD_NAME.sub("_", s)
    return (s or fallback)[:24]


def url_key(url):
    """去重键：同一张表情会在 lf3/lf5/lf6、p3/p11 等 CDN 域名间轮换下发，
    路径（对象 key + `~tplv-` 图床样式）相同即同一张图。只保留 path，
    忽略域名与签名参数；path 为空时退回去掉 query 的整段。"""
    s = str(url or "").strip()
    if not s:
        return ""
    path = urlsplit(s).path
    return path or s.split("?", 1)[0]


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

def _page_room_ids(room_num, timeout=20):
    """直播间页面兜底：SSR 里拿 room_id + 主播 ID。

    未开播 / 已结束的页面同样带着这段数据（`room/enter` 被风控返回 200 空包、
    或房间已下播清除时，网页仍能确定房间和主播）。返回 ("", "") 表示没拿到。
    """
    import httpx

    try:
        r = httpx.get("https://live.douyin.com/%s" % room_num,
                      headers={"User-Agent": _UA, "Referer": "https://live.douyin.com/"},
                      timeout=timeout, follow_redirects=True)
    except Exception:
        return "", ""
    text = (r.text or "").replace('\\"', '"')
    m = _ROOM_ANCHOR_RE.search(text)
    return (m.group(1), m.group(2)) if m else ("", "")


def _reflow_room(room_id, timeout=20):
    """19 位房间号走 reflow（游客可用）：未开播也能拿到 room_id + 主播 ID。"""
    import httpx

    try:
        r = httpx.get(
            "https://webcast.amemv.com/webcast/room/reflow/info/"
            "?type_id=0&live_id=1&app_id=1128&room_id=%s" % room_id,
            headers={"User-Agent": _UA, "Referer": "https://live.douyin.com/"},
            timeout=timeout, follow_redirects=True)
        room = (r.json().get("data") or {}).get("room") or {}
    except Exception:
        return "", ""
    owner = room.get("owner") or {}
    return (str(room.get("id_str") or room.get("id") or room_id),
            str(owner.get("id_str") or room.get("owner_user_id") or ""))


def fetch_vip_emojis(room_num, timeout=20):
    """一次性拉全「会员表情栏」，不用等有人发。

    链路：先确定 room_id + anchor_id → privilege/subscribe/info 取 emoji_list
    （房间页进房时前端也走这条）。主播 ID 依次尝试：
      ① 19 位长号 → reflow 接口（未开播也能拿）；
      ② room/web/enter（开播时最稳，顺带拿主播）；
      ③ 直播间页面 SSR（未开播/已结束页面仍有 roomId + anchor.id_str）。
    需要抖音登录 Cookie，未登录返回 status_code=20003（User doesn't login）。

    返回 {"items": [...], "error": "", "describe": "", "member": bool}
    """
    import urllib.parse as up

    import httpx

    from toolbox.douyin_live import UA, _get_douyin_cookie, room_id_kind

    res = {"items": [], "error": "", "describe": "", "member": False}
    room_num = str(room_num or "").strip()
    m = re.search(r"live\.douyin\.com/([A-Za-z0-9_\-]+)", room_num)
    if m:
        room_num = m.group(1)
    if not room_num:
        res["error"] = "先填直播间号或直播间链接"
        return res

    cookie = _get_douyin_cookie() or ""
    base = dict(_ENTER_BASE)
    base["browser_version"] = UA
    ref = "https://live.douyin.com/%s" % room_num
    room_id = anchor_id = ""
    notes = []

    # 长号 room/enter 不认，reflow 最直接（已结束也有 room + 主播）
    if room_id_kind(room_num) == "room_id":
        room_id, anchor_id = _reflow_room(room_num, timeout)

    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": UA, "Referer": ref}) as c:
        try:
            c.get(ref, timeout=timeout)
            if not anchor_id:
                h = {"Referer": ref}
                if cookie:
                    h["Cookie"] = cookie
                r = c.get("https://live.douyin.com/webcast/room/web/enter/?"
                          + up.urlencode({**base, "web_rid": str(room_num)}), headers=h)
                if r.text.strip().startswith("{"):
                    rooms = ((r.json().get("data") or {}).get("data")) or []
                    node = rooms[0] if rooms else {}
                    room_id = room_id or node.get("id_str") or ""
                    anchor_id = (node.get("owner") or {}).get("id_str") or ""
                else:
                    notes.append("房间接口空响应")
            if not anchor_id:
                # 未开播 / 已结束：页面 SSR 兜底（enter 已被下播清除或被风控）
                page_room, page_anchor = _page_room_ids(room_num, timeout)
                room_id = room_id or page_room
                anchor_id = page_anchor
            if not anchor_id:
                res["error"] = ("未取到主播 ID（直播间不存在，或抖音接口被风控）"
                                + ("：" + "、".join(notes) if notes else ""))
                return res

            h = {"Referer": ref}
            if cookie:
                h["Cookie"] = cookie
            # room_id 传 0 也能取到主播的会员表情（实测），有真实房间号时优先用它
            q = dict(base, purchase_type="1", room_id=str(room_id or "0"),
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
    res["describe"] = tab.get("describe") or data.get("emoji_tip") or ""
    res["member"] = bool(data.get("is_member")) or \
        bool((data.get("user_data") or {}).get("expired_time"))
    for e in data.get("emoji_list") or []:
        img = e.get("img") or {}
        urls = img.get("url_list") or []
        if not urls:
            continue
        uri = img.get("uri") or ""
        stem = uri.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        name = _VIP_NAMES.get(e.get("id")) or _clean_name(
            e.get("text") or stem or "会员表情")
        res["items"].append({"url": urls[0], "key": url_key(urls[0]),
                             "name": name, "nick": "", "ts": 0})
    if not res["items"]:
        res["error"] = res["error"] or "接口没有返回表情（该主播可能未开通会员表情）"
    return res
