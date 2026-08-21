"""B站资源 API 封装：表情包 / 装扮 / 收藏集 / 视频 / 直播间 / 动态 / 空间 / 搜索。

复刻自 BiliResourceDownloader 的 APIFetch.ts，统一处理 WBI/App 签名、登录 Cookie、
设备指纹与 -352 风控重试。
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse

import httpx

try:
    from auth import get_cookie  # 开发模式（src 在 sys.path）
except ImportError:
    from ..auth import get_cookie  # 打包/包内相对导入

from . import wbi

UA = wbi.UA
_cache_lock = __import__("threading").Lock()
_api_cache: dict[str, tuple[float, object]] = {}   # url -> (expire_ts, data)
CACHE_TTL = 300


class BiliApiError(Exception):
    pass


def _cache_get(url: str):
    with _cache_lock:
        hit = _api_cache.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
    return None


def _cache_put(url: str, data):
    with _cache_lock:
        _api_cache[url] = (time.time() + CACHE_TTL, data)


def clear_cache():
    with _cache_lock:
        _api_cache.clear()


def api_get(url: str, params: dict | None = None, *,
            wbi_sign: bool = False, app_sign: bool = False,
            use_cache: bool = True, login_required: bool = False) -> dict:
    """统一 GET。返回 data 字段；code!=0 抛 BiliApiError。"""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    if app_sign:
        params = wbi.sign_app(params)
    client = httpx.Client(timeout=15, follow_redirects=True)
    try:
        login_cookie = get_cookie("bilibili")
        if login_required and not login_cookie:
            raise BiliApiError("该接口需要登录 B站 Cookie（设置页 → Cookie 管理）")

        def _build_full() -> str:
            if wbi_sign:
                return wbi.sign_wbi(url, params, client)
            qs = urllib.parse.urlencode(params)
            return f"{url}?{qs}" if qs else url

        full = _build_full()
        if use_cache:
            cached = _cache_get(full)
            if cached is not None:
                return cached

        headers = {
            "User-Agent": UA,
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com/",
            "Cookie": wbi.build_cookie_header(client, login_cookie),
        }
        for attempt in range(2):
            r = client.get(full, headers=headers)
            try:
                js = r.json()
            except Exception:
                raise BiliApiError(f"[{r.status_code}] 接口返回异常（可能已下线）")
            code = js.get("code", -1)
            if code == 0:
                if js.get("data") is None:
                    raise BiliApiError("接口返回空数据（该内容可能已下架或 ID 无效）")
                if use_cache:
                    _cache_put(full, js["data"])
                return js["data"]
            if code in (-352, -403) and wbi_sign and attempt == 0:
                wbi.refresh_wbi()   # 刷新 WBI 密钥重试一次
                full = _build_full()
                continue
            if code == -101:
                raise BiliApiError("B站登录已失效，请重新登录（设置页 → Cookie 管理）")
            raise BiliApiError(f"[{code}] {js.get('message', '请求失败')}")
        raise BiliApiError("请求失败（WBI 风控重试后仍被拒绝）")
    finally:
        client.close()


# ==================== 表情包 ====================

def emote_panel() -> list[dict]:
    """全部表情包（all_packages），需登录。"""
    d = api_get("https://api.bilibili.com/x/emote/setting/panel",
                {"business": "reply"}, login_required=True)
    return d.get("all_packages", [])


def emote_package(ids: list[str]) -> list[dict]:
    """表情包详情（含每个表情的 url/gif_url/webp_url）。"""
    d = api_get("https://api.bilibili.com/x/emote/package",
                {"ids": ",".join(ids), "business": "reply"})
    return d.get("packages", [])


def room_emoticons(room_id: str) -> list[dict]:
    """直播间表情（需登录）。响应为嵌套结构 data.data → [{pkg_name, emoticons:[{emoji,url}]}]。

    短号/不存在房号直接请求会得到 -500，先经 get_info 换成真实房号。"""
    room_id = str(room_id).strip()
    try:
        info = live_room_info(room_id)
    except BiliApiError as e:
        raise BiliApiError(f"房间 {room_id} 不存在或已关停（{e}），请检查房号")
    real = str(info.get("room_id") or room_id)
    d = api_get("https://api.live.bilibili.com/xlive/web-ucenter/v2/emoticon/GetEmoticons",
                {"platform": "pc", "room_id": real}, login_required=True)
    return d.get("data", []) or []


# ==================== 装扮 / 收藏集 ====================

def garb_search(keyword: str, page: int = 1) -> dict:
    """装扮商城搜索（收藏集 + 装扮）。"""
    return api_get("https://api.bilibili.com/x/garb/v2/mall/home/search",
                   {"key_word": keyword, "pn": page})


def garb_item(item_id: str) -> dict:
    """装扮/套装详情（suit_items 含表情/头像框/粉丝牌等全部资源）。"""
    return api_get("https://api.bilibili.com/x/garb/v2/user/suit/benefit",
                   {"item_id": item_id, "part": "cards"})


def act_basic(act_id: str) -> dict:
    """收藏集活动基础信息（标题/lottery_list）。"""
    return api_get("https://api.bilibili.com/x/vas/dlc_act/act/basic", {"act_id": act_id})


def asset_bag(act_id: str) -> dict:
    """收藏集资产包（含隐藏收藏集/勋章兑换列表）。"""
    return api_get("https://api.bilibili.com/x/vas/dlc_act/asset_bag",
                   {"act_id": act_id})


def lottery_home_detail(act_id: str, lottery_id: str) -> dict:
    """收藏集首页详情。"""
    return api_get("https://api.bilibili.com/x/vas/dlc_act/lottery_home_detail",
                   {"act_id": act_id, "lottery_id": lottery_id})


# ==================== 视频 / 搜索 ====================

def video_view(vid: str) -> dict:
    """视频信息（支持 BV号 / av号）。"""
    params = {"bvid": vid} if vid.upper().startswith("BV") else {"aid": vid.lstrip("avAv")}
    return api_get("https://api.bilibili.com/x/web-interface/view", params)


def search_all(keyword: str, page: int = 1) -> dict:
    """综合搜索（用户+视频，WBI 签名）。"""
    return api_get("https://api.bilibili.com/x/web-interface/wbi/search/all/v2",
                   {"keyword": keyword, "page": page}, wbi_sign=True)


def search_type(keyword: str, search_type: str, page: int = 1) -> dict:
    """分类搜索（video/user/bili_user/live...，WBI 签名）。"""
    return api_get("https://api.bilibili.com/x/web-interface/wbi/search/type",
                   {"keyword": keyword, "search_type": search_type, "page": page}, wbi_sign=True)


# ==================== 直播间 ====================

def live_room_info(room_id: str) -> dict:
    """网页端房间信息（标题/封面/背景/关键帧）。"""
    return api_get("https://api.live.bilibili.com/room/v1/Room/get_info", {"room_id": room_id})


def live_room_app(room_id: str) -> dict:
    """App 端房间详情（App 签名）。"""
    return api_get("https://api.live.bilibili.com/xlive/app-room/v1/index/getInfoByRoom",
                   {"room_id": room_id, "platform": "android", "device": "android"}, app_sign=True)


def live_master_info(uid: str) -> dict:
    """主播信息（粉丝数/粉丝牌/公告）。"""
    return api_get("https://api.live.bilibili.com/live_user/v1/Master/info", {"uid": uid})


# ==================== 动态 / 空间 ====================

def dynamic_detail(dynamic_id: str) -> dict:
    """动态详情（配图/视频封面/转发内容）。"""
    return api_get("https://api.bilibili.com/x/polymer/web-dynamic/v1/detail",
                   {"id": dynamic_id, "features": "itemOpusStyle"})


def space_feed(mid: str, offset: str = "") -> dict:
    """用户动态列表（WBI 签名）。"""
    return api_get("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                   {"host_mid": mid, "offset": offset}, wbi_sign=True)


def user_card(mid: str) -> dict:
    """用户基础信息卡。"""
    return api_get("https://api.bilibili.com/x/web-interface/card", {"mid": mid, "photo": "true"})


def space_notice(mid: str) -> str:
    """空间公告（接口直接返回纯文本）。"""
    client = httpx.Client(timeout=15, follow_redirects=True)
    try:
        r = client.get(f"https://api.bilibili.com/x/space/notice?mid={mid}",
                       headers={"User-Agent": UA, "Referer": "https://space.bilibili.com/"})
        js = r.json()
        return js["data"] if js.get("code") == 0 else ""
    except Exception:
        return ""
    finally:
        client.close()


def upower_emote(mid: str) -> list:
    """充电专属表情（取最高充电等级的 emote 权益，字段为 icon）。"""
    try:
        d = api_get("https://api.bilibili.com/x/upowerv2/gw/rights/index", {"up_mid": mid})
    except BiliApiError:
        return []
    rights = d.get("privilege_rights", {}) or {}
    levels = sorted(rights.keys(), key=lambda k: int(k) if str(k).isdigit() else 0, reverse=True)
    if not levels:
        return []
    return rights[levels[0]].get("emote", {}).get("emojis", []) or []


def app_space(mid: str) -> dict:
    """App 端空间信息（App头图/夜间头图/投稿封面/收藏集卡牌封面，App 签名）。"""
    return api_get("https://app.bilibili.com/x/v2/space",
                   {"vmid": mid, "mobi_app": "android"}, app_sign=True)


# ==================== 工具 ====================

def sanitize(name: str) -> str:
    """清理文件/目录名非法字符。"""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip().rstrip(".") or "unnamed"
