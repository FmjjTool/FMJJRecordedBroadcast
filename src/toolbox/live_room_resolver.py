# -*- coding: utf-8 -*-
"""账号主页链接 → 直播间号（供「账号配置」自动预填直播间号）。

能力矩阵（比的是「主页链接能不能反推出直播房间号」）：
  · B站      space UID → roomid：游客接口 getRoomInfoOld，无需登录
  · 快手     主页 principalId 就是 live.kuaishou.com/u/{id}，离线可得
  · 抖音     主页 sec_uid → 抖音号（unique_id），而抖音号本身就是直播间 web_rid：
              纯 httpx 就能取（iesdouyin 用户接口 + room/enter 校验），未开播同样可用；
              抖音号取不到时才开浏览器读主页 room_data 兜底
  · 微博 / 小红书 / 网易云  主页链接没有固定房间号，只有链接本身就是直播链接时才能解析

结果缓存到 config/live_rooms.json：{account_id: {platform, url, room, note, ts}}。
空结果也写缓存，默认 6 小时内不重复开浏览器（抖音未开播时不会反复重试）；
点「刷新」会强制重试。

未开播的抖音账号也能直接映射：抖音号（unique_id）就是 room/enter 认的 web_rid，
两个 httpx 请求即可确认，不需要开浏览器。只有抖音号拿不到时才退回浏览器读主页。
"""

from __future__ import annotations

import json
import re
import threading
import time

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

CACHE_NAME = "live_rooms.json"
# 没解析出房间号的结果多久后允许再试（抖音未开播时拿不到，开播后要能补上）
EMPTY_TTL = 6 * 3600
# 抖音改走离线解析（两个 httpx 请求），失败不必压 6 小时，10 分钟后即可重试
DOUYIN_EMPTY_TTL = 10 * 60
# 解析算法版本：升级后旧缓存（可能是抖音号/空结果）会自动重解析一次
CACHE_VERSION = 2

_LOCK = threading.Lock()


def _platform(value: str) -> str:
    value = (value or "").strip().lower()
    return "bilibili" if value == "bili" else value


# ==================== 链接解析（纯离线） ====================

def parse_account_url(url):
    """账号 URL → (platform, room, uid, sec_uid)。识别不了返回 ("", "", "", "")。"""
    u = (url or "").strip()
    if not u:
        return "", "", "", ""
    m = re.search(r"live\.douyin\.com/(\d+)", u)
    if m:
        return "douyin", m.group(1), "", ""
    m = re.search(r"live\.bilibili\.com/(\d+)", u)
    if m:
        return "bili", m.group(1), "", ""
    m = re.search(r"space\.bilibili\.com/(\d+)", u)
    if m:
        return "bili", "", m.group(1), ""
    m = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", u)
    if m:
        return "douyin", "", "", m.group(1)
    m = re.search(r"live\.kuaishou\.com/u/([0-9A-Za-z_\-]+)", u)
    if m:
        return "kuaishou", m.group(1), "", ""
    m = (re.search(r"weibo\.com/l/wblive/p/show/([0-9A-Za-z:_\-]+)", u)
         or re.search(r"live\.weibo\.com/(?:show|room)\?(?:[^#]*&)?id=([0-9A-Za-z:_\-]+)", u))
    if m:
        return "weibo", m.group(1), "", ""
    m = re.search(r"xiaohongshu\.com/livestream/([0-9A-Za-z_\-]+)", u)
    if m:
        return "xiaohongshu", m.group(1), "", ""
    m = re.search(r"(?:look|music)\.163\.com/live\?(?:[^#]*&)?id=([0-9A-Za-z_\-]+)", u)
    if m:
        return "netease", m.group(1), "", ""
    return "", "", "", ""


def douyin_sec_uid(value):
    """从抖音主页链接/纯 sec_uid 里取 sec_uid（取不到返回空串）。"""
    s = str(value or "").strip()
    m = (re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", s)
         or re.search(r"sec_user_id=([A-Za-z0-9_\-]+)", s)
         or re.search(r"sec_uid=([A-Za-z0-9_\-]+)", s))
    if m:
        return m.group(1)
    return s if re.fullmatch(r"MS4wLjABAAAA[A-Za-z0-9_\-]+", s) else ""


def _douyin_room_from_text(s):
    m = (re.search(r"live\.douyin\.com/(\d+)", s)
         or re.search(r"web_rid=(\d+)", s)
         or re.search(r"webcast\.amemv\.com/douyin/webcast/reflow/(\d{6,})", s)
         or re.search(r"reflow/info/?(?:\?[^#]*?room_id=)(\d{6,})", s))
    return m.group(1) if m else ""


def expand_short_url(text):
    """跟一次短链重定向（v.douyin.com / b23.tv 等），失败返回空串。"""
    m = re.search(r"https?://(?:v\.douyin\.com|b23\.tv|bili2233\.cn)/[A-Za-z0-9_\-]+", text or "")
    if not m:
        return ""
    try:
        r = httpx.get(m.group(0), headers={"User-Agent": UA}, timeout=6,
                      follow_redirects=True)
        return str(r.url)
    except Exception:
        return ""


def parse_room_input(platform, text):
    """把用户填的内容规整成房间号：房间号本身或直播间链接都接受。

    抖音支持直播间长链、web_rid、reflow 房间 ID、v.douyin.com 短链、抖音号，
    以及整段分享文案（「复制打开抖音…」里夹着的链接）。
    识别不了（比如抖音主页链接）返回空串，由调用方提示手填房间号。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    plat = _platform(platform)
    if plat == "bilibili":
        if s.isdigit():
            return s
        m = (re.search(r"live\.bilibili\.com/(\d+)", s)
             or re.search(r"(?:roomid|room_id)=(\d+)", s))
        return m.group(1) if m else ""
    if plat == "douyin":
        if s.isdigit():
            return s
        room = _douyin_room_from_text(s)
        if room:
            return room
        if "v.douyin.com" in s:
            final = expand_short_url(s)
            if final:
                return _douyin_room_from_text(final) or (
                    final if final.isdigit() else "")
            return ""
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{1,29}", s):
            # 抖音号本身就是直播间号（room/enter 认这个 web_rid）
            return s
        m = re.search(r"(\d{6,})", s)
        return m.group(1) if m else ""
    from toolbox import live_stream
    return live_stream.parse_room(plat, s)


def kuaishou_principal(url):
    """快手主页链接（profile/{id} 或 /u/{id}）里的 principalId，也就是直播房间号。"""
    u = (url or "").strip()
    m = (re.search(r"kuaishou\.com/profile/([0-9A-Za-z_\-]+)", u)
         or re.search(r"kuaishou\.com/u/([0-9A-Za-z_\-]+)", u)
         or re.search(r"live\.kuaishou\.com/u/([0-9A-Za-z_\-]+)", u))
    if m and m.group(1) not in ("profile", "u"):
        return m.group(1)
    return ""


def bili_uid_to_room(uid, timeout=8):
    """B站 UID → 直播间号（游客接口，绝大多数主播 roomid≠uid 时也能取到）。"""
    try:
        r = httpx.get("https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld",
                      params={"mid": str(uid)},
                      headers={"User-Agent": UA, "Referer": "https://live.bilibili.com/"},
                      timeout=timeout)
        d = r.json()
        if d.get("code") == 0:
            rid = (d.get("data") or {}).get("roomid")
            if rid:
                return str(rid)
    except Exception:
        pass
    return ""


def _douyin_api(sec_uid):
    import urllib.parse
    qs = urllib.parse.urlencode({
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "sec_user_id": sec_uid, "publish_video_strategy_type": "2",
        "version_code": "170400", "version_name": "17.4.0",
        "cookie_enabled": "true", "platform": "PC", "downlink": "10",
    })
    return "https://www.douyin.com/aweme/v1/web/user/profile/other/?" + qs


def _parse_douyin_profile(txt):
    """profile/other 响应 → (昵称, web_rid, 是否直播中)。"""
    if not txt or not txt.strip().startswith("{"):
        return "", "", False
    try:
        d = json.loads(txt)
    except Exception:
        return "", "", False
    u = d.get("user") or {}
    nick = u.get("nickname") or ""
    live = bool(int(u.get("live_status") or 0))
    rd = u.get("room_data")
    if isinstance(rd, str) and rd.strip():
        try:
            rd = json.loads(rd)
        except Exception:
            rd = None
    web_rid = ((rd or {}).get("owner") or {}).get("web_rid") or ""
    return nick, str(web_rid), live


def resolve_douyin_many(sec_uids, timeout_ms=30000):
    """批量解析抖音主页：{sec_uid: (昵称, web_rid, 是否直播中)}。

    纯 httpx 请求 profile/other 会返回 200 + 空体（Argus 校验浏览器指纹），
    必须在 Playwright 页面上下文里 fetch 才拿得到数据（与下载器的兜底同源）。
    一批账号只起一次浏览器，逐个 fetch：4 个主播约 5 秒，比逐个起浏览器快一倍多。
    未开播时 room_data 为空 → web_rid 为空，只能拿到昵称。
    """
    secs = [s for s in dict.fromkeys(sec_uids or []) if s]
    result = {s: ("", "", False) for s in secs}
    if not secs:
        return result
    try:
        from playwright.sync_api import sync_playwright
        from toolbox.browser import launch as pw_launch
    except Exception:
        return result
    try:
        with sync_playwright() as p:
            browser = pw_launch(p, headless=True,
                                args=["--disable-blink-features=AutomationControlled",
                                      "--no-sandbox", "--disable-dev-shm-usage"])
            try:
                ctx = browser.new_context(user_agent=UA, viewport={"width": 1920,
                                                                  "height": 1080},
                                          locale="zh-CN")
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>false});")
                page = ctx.new_page()
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                page.wait_for_timeout(2500)
                for sec in secs:
                    try:
                        txt = page.evaluate(
                            """async (u) => {
                                const r = await fetch(u, {credentials: 'include'});
                                return await r.text();
                            }""", _douyin_api(sec))
                    except Exception:
                        continue
                    parsed = _parse_douyin_profile(txt)
                    if parsed[0] or parsed[1]:
                        result[sec] = parsed
            finally:
                browser.close()
    except Exception:
        return result
    return result


def douyin_unique_id(sec_uid, timeout=8):
    """sec_uid → 抖音号（iesdouyin 公开接口，纯 httpx，不开浏览器）。"""
    s = str(sec_uid or "").strip()
    if not s:
        return ""
    try:
        r = httpx.get(
            "https://www.iesdouyin.com/web/api/v2/user/info/?sec_uid=" + s,
            headers={"User-Agent": UA, "Referer": "https://www.douyin.com/"},
            timeout=timeout, follow_redirects=True)
        info = (r.json() or {}).get("user_info") or {}
        return str(info.get("unique_id") or "")
    except Exception:
        return ""


def douyin_room_id(unique_id, timeout=8):
    """抖音号当 web_rid 请求 room/enter：有房间返回 19 位 roomId（未开播也算）。

    实测（2026-11）：web_rid 就是主播的抖音号，未开播时 room/enter 依然返回
    data.data[0].id_str；乱填的抖音号返回 status_code 10011、无房间数据。
    """
    s = str(unique_id or "").strip()
    if not s:
        return ""
    import urllib.parse
    params = {
        "aid": "6383", "app_name": "douyin_web", "live_id": "1",
        "device_platform": "web", "language": "zh-CN", "browser_language": "zh-CN",
        "browser_platform": "Win32", "browser_name": "Mozilla",
        "browser_version": UA, "web_rid": s,
    }
    try:
        with httpx.Client(headers={"User-Agent": UA,
                                   "Referer": "https://live.douyin.com/" + s},
                          follow_redirects=True, timeout=timeout) as c:
            c.get("https://live.douyin.com/" + s, timeout=timeout)
            r = c.get("https://live.douyin.com/webcast/room/web/enter/?"
                      + urllib.parse.urlencode(params), timeout=timeout)
            rooms = ((r.json() or {}).get("data") or {}).get("data") or []
    except Exception:
        return ""
    return str(((rooms[0] or {}).get("id_str")) or "") if rooms else ""


def douyin_reflow_owner(room_id, timeout=8):
    """19 位 roomId → (直播间短号 web_rid, 主播 sec_uid, 昵称)；拿不到返回空串。

    room/enter 的离线响应里 owner 是空的，拿不到短号；reflow 接口对「有历史
    直播场次」的房间会返回 owner.web_rid（例如 封茗囧菌 → 582775732015）。
    """
    s = str(room_id or "").strip()
    if not s:
        return "", "", ""
    try:
        r = httpx.get(
            "https://webcast.amemv.com/webcast/room/reflow/info/"
            "?type_id=0&live_id=1&app_id=1128&room_id=" + s,
            headers={"User-Agent": UA, "Referer": "https://live.douyin.com/"},
            timeout=timeout, follow_redirects=True)
        d = r.json()
    except Exception:
        return "", "", ""
    room = (d.get("data") or {}).get("room") or {}
    owner = room.get("owner") or {}
    return (str(owner.get("web_rid") or ""), str(owner.get("sec_uid") or ""),
            str(owner.get("nickname") or ""))


def resolve_douyin_offline(sec_uid, timeout=8):
    """抖音主页 → 直播间号（不开浏览器）：sec_uid → 抖音号 → 房间 → 直播间短号。

    返回 (房间号, 说明)；拿不到返回 ("", 原因)。
    """
    uid = douyin_unique_id(sec_uid, timeout=timeout)
    if not uid:
        return "", "抖音主页没取到抖音号"
    room_id = douyin_room_id(uid, timeout=timeout)
    if not room_id:
        return "", "该抖音号没有直播间（可能从未开播）"
    web_rid, owner_sec, _nick = douyin_reflow_owner(room_id, timeout=timeout)
    if web_rid:
        if owner_sec and owner_sec != sec_uid:
            return "", "直播间归属与账号不一致（可能是同名账号）"
        return web_rid, "抖音号 → 直播间短号"
    # reflow 只对有过直播场次的房间有数据；拿不到短号时抖音号同样能被 room/enter 认
    return uid, "抖音号即直播间号（未取到短号）"


def resolve_douyin_sec(sec_uid, timeout_ms=30000):
    """抖音主页 sec_uid → (昵称, web_rid, 是否直播中)。"""
    return resolve_douyin_many([sec_uid], timeout_ms=timeout_ms).get(
        sec_uid, ("", "", False))


# ==================== 解析入口 ====================

def can_resolve(platform, url) -> bool:
    """这条账号链接有没有可能自动拿到直播间号（决定要不要排进解析队列）。"""
    plat, room, uid, sec = parse_account_url(url)
    if room:
        return True
    plat = _platform(plat) or _platform(platform)
    if plat == "bilibili":
        return bool(uid)
    if plat == "douyin":
        return bool(sec)
    if plat == "kuaishou":
        return bool(kuaishou_principal(url))
    return False


def resolve_room(platform, url, allow_browser=True):
    """解析直播间号，返回 (room, note)。room 为空时 note 说明原因。"""
    plat, room, uid, sec = parse_account_url(url)
    if room:
        return room, "链接里直接带直播间号"
    plat = _platform(plat) or _platform(platform)
    if plat == "bilibili" and uid:
        rid = bili_uid_to_room(uid)
        if rid:
            return rid, "B站空间自动解析"
        return "", "B站接口没查到直播间"
    if plat == "kuaishou":
        pid = kuaishou_principal(url)
        if pid:
            return pid, "快手主页 ID 即直播间号"
        return "", "快手链接里没有 ID"
    if plat == "douyin" and sec:
        room, note = resolve_douyin_offline(sec)
        if room:
            return room, note
        if not allow_browser:
            return "", note or "抖音需要浏览器解析"
        nick, rid, live = resolve_douyin_sec(sec)
        if rid:
            return rid, "抖音主页自动解析"
        if nick:
            return "", f"抖音未开播（{nick}），开播后再试"
        return "", note or "抖音主页没取到数据"
    return "", "该平台主页链接推不出直播间号"


def resolve_many(items, allow_browser=True):
    """批量解析 [(platform, url)] → [(room, note)]（与 items 一一对应）。

    抖音先走「抖音号即直播间号」的离线链路（两个 httpx 请求，不开浏览器）；
    离线拿不到的再共用一次浏览器会话兜底，其余平台各走 resolve_room。
    """
    items = list(items or [])
    out = [("", "该平台主页链接推不出直播间号") for _ in items]
    browser_secs = {}
    for index, (platform, url) in enumerate(items):
        plat, room, _uid, sec = parse_account_url(url)
        if room:
            out[index] = (room, "链接里直接带直播间号")
            continue
        if (_platform(plat) or _platform(platform)) == "douyin" and sec:
            got, note = resolve_douyin_offline(sec)
            if got:
                out[index] = (got, note)
            else:
                out[index] = ("", note)
                browser_secs.setdefault(sec, []).append(index)
        else:
            out[index] = resolve_room(platform, url, allow_browser=False)
    if browser_secs and allow_browser:
        profiles = resolve_douyin_many(list(browser_secs))
        for sec, idxs in browser_secs.items():
            nick, rid, _live = profiles.get(sec, ("", "", False))
            if rid:
                value = (rid, "抖音主页自动解析")
            elif nick:
                value = ("", "抖音未开播（%s），开播后再试" % nick)
            else:
                value = out[idxs[0]]
            for index in idxs:
                out[index] = value
    return out


# ==================== 缓存 ====================

def _cache_path():
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / CACHE_NAME


def load_cache() -> dict:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def cached_room(account_id, url="") -> str:
    """缓存里该账号已知的直播间号（链接变了就不复用旧结果）。"""
    entry = load_cache().get(account_id) or {}
    if url and entry.get("url") and entry.get("url") != url:
        return ""
    return str(entry.get("room") or "")


def stale(account_id) -> bool:
    """缓存是旧版算法写的（例如把抖音号当房间号）：已有房间号也要重解析一次。"""
    entry = load_cache().get(account_id) or {}
    return bool(entry) and int(entry.get("v") or 0) != CACHE_VERSION


def cached_note(account_id, url="") -> str:
    """缓存里上一轮解析的说明（用于输入框占位提示：为什么还没号）。"""
    entry = load_cache().get(account_id) or {}
    if url and entry.get("url") and entry.get("url") != url:
        return ""
    if entry.get("room"):
        return ""
    return str(entry.get("note") or "")


def should_try(account_id, platform, url) -> bool:
    """是否需要（重新）解析：没记录 / 链接变了 / 上次是空结果且过了重试间隔。"""
    entry = load_cache().get(account_id)
    if not entry:
        return True
    if int(entry.get("v") or 0) != CACHE_VERSION:
        return True  # 老版本缓存（如把抖音号当房间号）重解析一次
    if url and entry.get("url") and entry.get("url") != url:
        return True
    if entry.get("room"):
        return False
    try:
        age = time.time() - float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        return True
    ttl = DOUYIN_EMPTY_TTL if _platform(platform) == "douyin" else EMPTY_TTL
    return age >= ttl


def remember(account_id, platform, url, room, note=""):
    """记录解析结果（空结果也记录，用于重试节流）。"""
    if not account_id:
        return
    with _LOCK:
        data = load_cache()
        data[account_id] = {
            "platform": _platform(platform),
            "url": url or "",
            "room": str(room or ""),
            "note": note or "",
            "ts": time.time(),
            "v": CACHE_VERSION,
        }
        try:
            path = _cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except Exception:
            pass