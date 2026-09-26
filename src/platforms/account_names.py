"""账号名自动获取：主页 URL → 账号昵称。

视频平台（B站/抖音/微博/快手/小红书）走各平台轻量接口；
音乐平台（网易云/QQ/酷狗/酷我/5sing）复用 music_account.collectors.collect。
统一返回 (name, error)，两者恰好一个非空。

游客优先原则：游客接口可用就不动 Cookie（B站 card / 微博访客通行证）；
抖音 profile/other、快手、小红书游客被风控时才接入 Cookie。
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

PLATFORM_LABELS = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博",
                   "kuaishou": "快手", "xiaohongshu": "小红书"}


def _cookie(platform: str) -> str:
    try:
        from auth import get_cookie
        return get_cookie(platform) or ""
    except Exception:
        return ""


# ==================== B站 ====================

def _bili_name(url: str) -> tuple[str, str]:
    m = re.search(r"space\.bilibili\.com/(\d+)", url)
    if not m:
        return "", "不是B站主页链接"
    r = httpx.get("https://api.bilibili.com/x/web-interface/card",
                  params={"mid": m.group(1)},
                  headers={"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"},
                  timeout=15)
    d = r.json()
    if d.get("code") == 0:
        name = ((d.get("data") or {}).get("card") or {}).get("name") or ""
        if name:
            return name, ""
        return "", "接口未返回昵称"
    if d.get("code") == -404:
        return "", "用户不存在（可能已注销或被封）"
    return "", f"接口错误 code={d.get('code')}"


# ==================== 抖音 ====================

def _douyin_name(url: str) -> tuple[str, str]:
    m = re.search(r"douyin\.com/user/([A-Za-z0-9_-]{20,})", url)
    if not m:
        return "", "不是抖音主页链接"
    sec_uid = m.group(1)

    import requests
    from multi_downloader import UA_PC as _UA, _douyin_abogus

    # profile/other：Cookie 优先（游客态该接口被风控）
    for cookie in filter(None, [_cookie("douyin"), ""]):
        params = {
            "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
            "publish_video_strategy_type": "2", "source": "channel_pc_web",
            "sec_user_id": sec_uid, "personal_center_strategy": "1",
            "version_code": "170808",
        }
        query = urlencode(params)
        a_bogus = _douyin_abogus(query, _UA)
        if not a_bogus:
            return "", "a_bogus 签名失败（node 异常）"
        try:
            r = requests.get(
                f"https://www.douyin.com/aweme/v1/web/user/profile/other/?{query}&a_bogus={a_bogus}",
                headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/",
                         "Cookie": cookie},
                timeout=15)
            if not r.text.strip():
                continue  # 风控空响应，试下一态
            d = r.json()
            if d.get("status_code") == 0:
                name = (d.get("user") or {}).get("nickname") or ""
                if name:
                    return name, ""
        except Exception:
            continue

    # 兜底：aweme/post 首条作品的作者昵称（复用已修好的采集链路）
    try:
        from platforms.douyin import DouyinParser
        posts = DouyinParser().fetch_user_posts(
            f"https://www.douyin.com/user/{sec_uid}", 1)
        if posts and posts[0].author:
            return posts[0].author, ""
    except Exception:
        pass
    return "", "未获取到昵称（游客被风控，可在「Cookie 管理」登录抖音后重试）"


# ==================== 微博 ====================

def _weibo_name(url: str) -> tuple[str, str]:
    m = re.search(r"weibo\.com/(?:u/)?(\d{5,15})", url)
    if not m:
        return "", "不是微博主页链接（需 u/数字 形式）"
    uid = m.group(1)

    ck = ""
    try:
        from platforms.weibo import WeiboParser
        ck = WeiboParser()._get_visitor_cookie() or ""
    except Exception:
        pass
    headers = {"User-Agent": UA_MOBILE, "Referer": "https://m.weibo.cn/"}
    if ck:
        headers["Cookie"] = ck

    # 107603 容器（帖子流）：首条 mblog.user.screen_name
    try:
        r = httpx.get("https://m.weibo.cn/api/container/getIndex",
                      params={"type": "uid", "value": uid,
                              "containerid": f"107603{uid}"},
                      headers=headers, timeout=15)
        cards = ((r.json().get("data") or {}).get("cards")) or []
        for card in cards:
            user = ((card.get("mblog") or {}).get("user")) or {}
            if user.get("screen_name"):
                return user["screen_name"], ""
    except Exception:
        pass

    # 230283 容器（资料卡）：任意位置找 screen_name
    try:
        r = httpx.get("https://m.weibo.cn/api/container/getIndex",
                      params={"type": "uid", "value": uid,
                              "containerid": f"230283{uid}"},
                      headers=headers, timeout=15)
        found = []

        def _find(obj, depth=0):
            if depth > 6 or found:
                return
            if isinstance(obj, dict):
                v = obj.get("screen_name")
                if isinstance(v, str) and v.strip():
                    found.append(v.strip())
                for v in obj.values():
                    _find(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj:
                    _find(v, depth + 1)

        _find(r.json().get("data") or {})
        if found:
            return found[0], ""
    except Exception:
        pass
    return "", "未获取到昵称（访客接口被拦或账号无内容）"


# ==================== 快手 ====================

def _kuaishou_name(url: str) -> tuple[str, str]:
    from platforms.kuaishou import _balanced_json, _fetch

    ck = _cookie("kuaishou")
    for cookie in (["", ck] if ck else [""]):
        try:
            html = _fetch(url, cookie=cookie)
            data = _balanced_json(html, r"window\.__APOLLO_STATE__")
            dc = (data or {}).get("defaultClient") or {}
            for k, v in dc.items():
                if k.startswith("VisionProfileAuthor:") and isinstance(v, dict):
                    name = v.get("name") or v.get("user_name") or ""
                    if name:
                        return name, ""
        except Exception:
            continue
    return "", "未获取到昵称（快手主页需登录，请在「Cookie 管理」登录快手后重试）"


# ==================== 小红书 ====================

def _xhs_name(url: str) -> tuple[str, str]:
    from platforms.xiaohongshu import _balanced_json, _fetch

    ck = _cookie("xiaohongshu")
    for cookie in (["", ck] if ck else [""]):
        try:
            html = _fetch(url, cookie=cookie)
            data = _balanced_json(html, r"window\.__INITIAL_STATE__")
            basic = ((((data or {}).get("user") or {}).get("userPageData"))
                     or {}).get("basicInfo") or {}
            name = basic.get("nickname") or ""
            if name:
                return name, ""
        except Exception:
            continue
    return "", "未获取到昵称（小红书主页需登录，请在「Cookie 管理」登录小红书后重试）"


# ==================== 统一入口 ====================

_FETCHERS = {
    "bilibili": _bili_name,
    "douyin": _douyin_name,
    "weibo": _weibo_name,
    "kuaishou": _kuaishou_name,
    "xiaohongshu": _xhs_name,
}


def fetch_account_name(url: str) -> tuple[str, str]:
    """视频平台主页 URL → (账号昵称, 错误信息)。恰好一个非空。"""
    url = (url or "").strip()
    if not url:
        return "", "请输入链接"
    from platforms.base import URLRecognizer
    platform = URLRecognizer.recognize(url)
    if not platform or platform not in _FETCHERS:
        return "", "无法识别链接平台（支持：B站/抖音/微博/快手/小红书主页）"
    label = PLATFORM_LABELS.get(platform, platform)
    try:
        name, err = _FETCHERS[platform](url)
    except Exception as e:
        return "", f"{label}获取失败：{e}"
    if name:
        return name, ""
    return "", err or f"{label}未获取到昵称"


def fetch_music_account_name(url: str) -> tuple[str, str]:
    """音乐平台主页/歌手链接 → (账号昵称, 错误信息)。恰好一个非空。"""
    url = (url or "").strip()
    if not url:
        return "", "请输入链接"
    from music_account.accounts import parse_url
    platform, pid, kind = parse_url(url)
    if not platform:
        return "", "无法识别音乐平台链接（支持：网易云/QQ音乐/酷狗/酷我/5sing）"
    from music_account.accounts import PLATFORM_NAMES
    label = PLATFORM_NAMES.get(platform, platform)
    try:
        from music_account.collectors import collect
        rec = collect(platform, pid, kind)
    except Exception as e:
        return "", f"{label}采集失败：{e}"
    if rec.get("error"):
        return "", f"{label}：{rec['error']}"
    name = rec.get("name") or ""
    if name:
        return name, ""
    return "", f"{label}未返回昵称"


def _append_platform(name: str, label: str) -> str:
    """昵称 + 平台标签（昵称已以平台结尾则不重复拼，如「封茗囧菌抖音」）。"""
    if not label or name.endswith(label):
        return name
    return f"{name}{label}"


def fetch_display_name(url: str) -> tuple[str, str]:
    """任意平台主页链接 → 「昵称+平台」默认名（如 封茗囧菌抖音 / 封茗囧菌网易云）。

    供添加账号时名称留空的自动命名：先按视频平台识别，失败再试音乐平台。
    返回 (默认名, 错误信息)，恰好一个非空。
    """
    from platforms.base import URLRecognizer
    platform = URLRecognizer.recognize(url or "")
    if platform:
        name, err = fetch_account_name(url)
        if name:
            return _append_platform(name, PLATFORM_LABELS.get(platform, platform)), ""
        return "", err
    name, err = fetch_music_account_name(url)
    if name:
        from music_account.accounts import parse_url
        mplat, _, _ = parse_url(url)
        from music_account.accounts import PLATFORM_NAMES
        return _append_platform(name, PLATFORM_NAMES.get(mplat, mplat or "")), ""
    return "", err
