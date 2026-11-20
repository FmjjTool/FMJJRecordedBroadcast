# -*- coding: utf-8 -*-
"""账号主页链接 → 直播间号（供「账号配置」自动预填直播间号）。

能力矩阵（比的是「主页链接能不能反推出直播房间号」）：
  · B站      space UID → roomid：游客接口 getRoomInfoOld，无需登录
  · 快手     主页 principalId 就是 live.kuaishou.com/u/{id}，离线可得
  · 抖音     主页 sec_uid → 直播间 web_rid：必须开浏览器请求主页；
             接口只在开播时返回 room_data，未开播只能拿到昵称
  · 微博 / 小红书 / 网易云  主页链接没有固定房间号，只有链接本身就是直播链接时才能解析

结果缓存到 config/live_rooms.json：{account_id: {platform, url, room, note, ts}}。
空结果也写缓存，默认 6 小时内不重复开浏览器（抖音未开播时不会反复重试）；
点「刷新」会强制重试。
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


def parse_room_input(platform, text):
    """把用户填的内容规整成房间号：房间号本身或直播间链接都接受。

    识别不了（比如抖音分享短链、主页链接）返回空串，由调用方提示手填房间号。
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
        m = (re.search(r"live\.douyin\.com/(\d+)", s)
             or re.search(r"web_rid=(\d+)", s)
             or re.search(r"(\d{6,})", s))
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


def resolve_douyin_sec(sec_uid, timeout_ms=30000):
    """抖音主页 sec_uid → (昵称, web_rid, 是否直播中)。

    纯 httpx 请求 profile/other 会返回 200 + 空体（Argus 校验浏览器指纹），
    必须在 Playwright 页面上下文里 fetch 才拿得到数据（与下载器的兜底同源）。
    未开播时 room_data 为空 → web_rid 为空，只能拿到昵称。
    """
    import urllib.parse
    try:
        from playwright.sync_api import sync_playwright
        from toolbox.browser import launch as pw_launch
    except Exception:
        return "", "", False
    qs = urllib.parse.urlencode({
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "sec_user_id": sec_uid, "publish_video_strategy_type": "2",
        "version_code": "170400", "version_name": "17.4.0",
        "cookie_enabled": "true", "platform": "PC", "downlink": "10",
    })
    api = "https://www.douyin.com/aweme/v1/web/user/profile/other/?" + qs
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
                txt = page.evaluate(
                    """async (u) => {
                        const r = await fetch(u, {credentials: 'include'});
                        return await r.text();
                    }""", api)
            finally:
                browser.close()
    except Exception:
        return "", "", False
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
    plat = plat or _platform(platform)
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
        if not allow_browser:
            return "", "抖音需要浏览器解析"
        nick, rid, live = resolve_douyin_sec(sec)
        if rid:
            return rid, "抖音主页自动解析"
        if nick:
            return "", f"抖音未开播（{nick}），开播后再试"
        return "", "抖音主页没取到数据"
    return "", "该平台主页链接推不出直播间号"


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


def should_try(account_id, platform, url) -> bool:
    """是否需要（重新）解析：没记录 / 链接变了 / 上次是空结果且过了重试间隔。"""
    entry = load_cache().get(account_id)
    if not entry:
        return True
    if url and entry.get("url") and entry.get("url") != url:
        return True
    if entry.get("room"):
        return False
    try:
        age = time.time() - float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        return True
    return age >= EMPTY_TTL


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
        }
        try:
            path = _cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except Exception:
            pass