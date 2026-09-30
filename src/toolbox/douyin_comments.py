"""抖音评论区：抓取（图片+文本原始数据）/ 搜索筛选 / 导出长图。

链路复刻自已验证的测试脚本（src/_test_douyin_comments.py）：
- 评论：/aweme/v1/web/comment/list/ + a_bogus 签名，游客 ttwid 优先、Cookie 兜底
- 作者模式：HTTP 直调 post 列表（常被 Argus 风控拦截）→ Playwright 回退
- 导出：PIL 绘制评论区长图（头像/昵称/文本/图片内嵌，仿抖音 App 浅色样式）
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

from multi_downloader import UA_PC, _douyin_abogus

COMMENT_API = "https://www.douyin.com/aweme/v1/web/comment/list/"
DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
PAGE_SIZE = 20
IMG_HEADERS = {"User-Agent": UA_PC, "Referer": "https://www.douyin.com/"}


# ==================== 抓取 ====================

def _sess(cookie: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA_PC, "Referer": "https://www.douyin.com/"})
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def _cookie_or_guest() -> str:
    """优先登录 Cookie；无则空串（游客模式）。"""
    try:
        from auth import get_cookie
        return get_cookie("douyin") or ""
    except Exception:
        return ""


def fetch_video_info(aweme_id: str, cookie: str = "") -> dict:
    """视频详情预检（aweme/detail 完整浏览器指纹参数）。

    返回 {exists, unknown, desc, author, comment_count, aweme_type}。
    exists=False 且 unknown=False：接口正常返回但无详情 → 视频确实不存在。
    unknown=True：请求被风控（空响应/解析失败），无法判定，调用方不应据此报「视频不存在」。
    """
    if not cookie:
        cookie = _cookie_or_guest()
    base = (DETAIL_API + "?"
            "device_platform=webapp&aid=6383&channel=channel_pc_web"
            f"&aweme_id={aweme_id}&update_version_code=170400&pc_client_type=1"
            "&version_code=190500&version_name=19.5.0&cookie_enabled=true"
            "&screen_width=1536&screen_height=864&browser_language=zh-CN"
            "&browser_platform=Win32&browser_name=Chrome&browser_version=127.0.0.0"
            "&browser_online=true&engine_name=Blink&engine_version=127.0.0.0"
            "&os_name=Windows&os_version=10&cpu_core_num=8&device_memory=8"
            "&platform=PC&downlink=1.25&effective_type=4g&round_trip_time=50")
    out = {"exists": False, "unknown": False, "desc": "", "author": "",
           "comment_count": 0, "aweme_type": None}
    d = None
    for attempt in range(2):
        # 第 2 次降级游客（登录 Cookie 失效/被风控的兜底）
        use_ck = cookie if attempt == 0 else ""
        s = _sess(use_ck)
        if not use_ck:
            try:
                s.get("https://www.douyin.com/", timeout=15)
            except Exception:
                pass
        a_bogus = _douyin_abogus(base.split("?")[1], UA_PC)
        if not a_bogus:
            return {**out, "unknown": True}
        try:
            r = s.get(f"{base}&a_bogus={a_bogus}", timeout=15)
            if not r.text.strip():
                raise ValueError("空响应（风控）")
            d = r.json()
            break
        except Exception:
            d = None
            time.sleep(1.2)
    if d is None:
        return {**out, "unknown": True}
    aw = d.get("aweme_detail") or {}
    if not aw:
        return out
    stat = aw.get("statistics") or {}
    out.update({
        "exists": True,
        "desc": str(aw.get("desc") or ""),
        "author": (aw.get("author") or {}).get("nickname") or "",
        "comment_count": int(stat.get("comment_count") or 0),
        "aweme_type": aw.get("aweme_type"),
    })
    return out


def fetch_comments(aweme_id: str, cookie: str = "", pages: int = 10,
                   progress=None, should_stop=None) -> list:
    """翻页抓顶层评论（含图片）。返回原始 comment dict 列表。

    progress(stage: str, cur, total)：进度回调；should_stop() 返回 True 时中止。
    登录 Cookie 优先（游客态常被风控返回空响应）。
    风控空响应重试策略：退避后重建会话，最多 3 次；登录 Cookie 连续被拦时
    降级纯游客（Cookie 失效/被风控的兜底）。重试不消耗页数配额。
    """
    if not cookie:
        cookie = _cookie_or_guest()
    state = {"cookie": cookie}

    def _new_sess():
        s = _sess(state["cookie"])
        if not state["cookie"]:
            try:
                s.get("https://www.douyin.com/", timeout=15)
            except Exception:
                pass
        return s

    s = _new_sess()
    out, cursor, got_pages, fails = [], 0, 0, 0
    while got_pages < pages:
        if should_stop and should_stop():
            break
        params = {
            "device_platform": "webapp", "aid": "6383", "app_name": "aweme",
            "version_code": "170808", "aweme_id": aweme_id,
            "cursor": str(cursor), "count": str(PAGE_SIZE), "item_type": "0",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        a_bogus = _douyin_abogus(query, UA_PC)
        if not a_bogus:
            if progress:
                progress("a_bogus 签名失败（node 异常）", got_pages, pages)
            break
        d = None
        try:
            r = s.get(f"{COMMENT_API}?{query}&a_bogus={a_bogus}", timeout=15)
            if r.text.strip():
                d = r.json()
        except Exception as e:
            d = None
            if progress:
                progress(f"第{got_pages + 1}页请求失败：{e}", got_pages, pages)
        if d is None:
            # 风控空响应：退避 + 重建会话；登录态连续被拦则降级游客，
            # 游客也被拦（IP 级风控）时恢复登录态做最后尝试
            fails += 1
            if fails > 4:
                if progress:
                    progress(f"第{got_pages + 1}页连续被风控拦截，停止",
                             got_pages, pages)
                break
            if fails == 2 and state["cookie"]:
                state["cookie"] = ""
                if progress:
                    progress("登录 Cookie 疑似失效，降级游客模式重试...",
                             got_pages, pages)
            elif fails == 4 and cookie and not state["cookie"]:
                state["cookie"] = cookie
                if progress:
                    progress("游客通道也被拦，恢复登录 Cookie 最后尝试...",
                             got_pages, pages)
            elif progress:
                progress(f"第{got_pages + 1}页被风控（空响应），"
                         f"刷新会话重试 {fails}/4...", got_pages, pages)
            time.sleep(1.5 if fails < 3 else 3)
            s = _new_sess()
            continue
        fails = 0
        if d.get("status_code") != 0:
            if progress:
                progress(f"第{got_pages + 1}页 status_code={d.get('status_code')}，停止",
                         got_pages, pages)
            break
        comments = d.get("comments") or []
        out.extend(comments)
        has_more = int(d.get("has_more") or 0)
        cursor = int(d.get("cursor") or (cursor + PAGE_SIZE))
        got_pages += 1
        if progress:
            progress(f"已获取 {len(out)} 条评论", got_pages, pages)
        if not has_more:
            break
        time.sleep(0.2)
    return out


def fetch_author_videos(sec_uid: str, limit: int = 3) -> list:
    """作者主页最新视频 → [(aweme_id, desc), ...]。

    HTTP 直调 post 接口常被 Argus 风控拦截 → 回退 Playwright（Cookie 优先游客兜底）。
    """
    # ---- 尝试1：HTTP 直调 ----
    try:
        from auth import get_cookie
        cookie = get_cookie("douyin") or ""
        s = _sess(cookie)
        if not cookie:
            try:
                s.get("https://www.douyin.com/", timeout=15)
            except Exception:
                pass
        params = {
            "device_platform": "webapp", "aid": "6383", "sec_user_id": sec_uid,
            "max_cursor": "0", "locate_query": "false", "show_live_replay_strategy": "1",
            "need_time_list": "1", "time_list_query": "0", "whale_cut_token": "",
            "cut_version": "1", "count": str(limit),
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        a_bogus = _douyin_abogus(query, UA_PC)
        r = s.get(f"https://www.douyin.com/aweme/v1/web/aweme/post/?{query}&a_bogus={a_bogus}",
                  timeout=15)
        d = r.json()
        if d.get("status_code") == 0:
            vids = [(str(aw.get("aweme_id")), (aw.get("desc") or "")[:25])
                    for aw in (d.get("aweme_list") or [])][:limit]
            if vids:
                return vids
    except Exception:
        pass

    # ---- 尝试2：Playwright ----
    try:
        from platforms.douyin import DouyinParser
        infos = DouyinParser().fetch_user_posts(
            f"https://www.douyin.com/user/{sec_uid}", limit)
        return [(i.video_id, (i.title or "")[:25]) for i in infos if i.video_id][:limit]
    except Exception:
        return []


def parse_input(text: str) -> tuple[str, str]:
    """解析输入 → (kind, id)。kind: aweme / sec_uid / ''。

    支持：视频链接 / note 链接 / modal_id / 纯 aweme_id / 主页链接 / sec_uid= / 纯 sec_uid
    """
    s = (text or "").strip()
    if not s:
        return "", ""
    m = re.search(r"(?:video/|note/|modal_id=|aweme_id=)(\d{15,})", s) or \
        re.search(r"/(\d{15,})/?$", s)
    if m:
        return "aweme", m.group(1)
    if s.isdigit() and len(s) >= 15:
        return "aweme", s
    m = re.search(r"sec_uid=([A-Za-z0-9_-]{20,})", s)
    if m:
        return "sec_uid", m.group(1)
    m = re.search(r"douyin\.com/user/([A-Za-z0-9_-]{20,})", s)
    if m:
        return "sec_uid", m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", s):
        return "sec_uid", s
    return "", ""


# ==================== 字段提取 ====================

def image_urls(comment: dict) -> list:
    """一条评论里的所有图片地址（清晰度优先：origin > download > medium > crop > thumb）。

    每张图的 url_list 是同一图片的多个镜像，只取首个，避免重复下载。
    """
    urls = []
    for img in comment.get("image_list") or []:
        for key in ("origin_url", "download_url", "medium_url", "crop_url", "thumb_url"):
            obj = img.get(key)
            if isinstance(obj, dict):
                ul = obj.get("url_list") or []
                if ul and ul[0] not in urls:
                    urls.append(ul[0])
                break
    return urls


def avatar_url(comment: dict) -> str:
    u = comment.get("user") or {}
    for key in ("avatar_medium", "avatar_thumb", "avatar_larger"):
        obj = u.get(key) or {}
        ul = obj.get("url_list") or []
        if ul:
            return ul[0]
    return ""


def comment_text(c: dict) -> str:
    return c.get("text") or ""


def comment_user(c: dict) -> str:
    return (c.get("user") or {}).get("nickname") or "?"


def comment_likes(c: dict) -> int:
    try:
        return int(c.get("digg_count") or 0)
    except (TypeError, ValueError):
        return 0


def comment_time(c: dict) -> str:
    ts = c.get("create_time")
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (TypeError, ValueError):
        return ""


def comment_ts(c: dict) -> int:
    try:
        return int(c.get("create_time") or 0)
    except (TypeError, ValueError):
        return 0


def comment_extra(c: dict) -> str:
    parts = [x for x in (comment_time(c), c.get("ip_label") or "") if x]
    return " · ".join(parts)


# ==================== 搜索 / 筛选 / 保存 / 导出（共用层） ====================

from toolbox import comments_common as _cc


def filter_comments(comments: list, keyword: str = "", user: str = "",
                     image_mode: str = "all", min_likes: int = 0,
                     sort_by: str = "default") -> list:
    return _cc.filter_comments(sys.modules[__name__], comments, keyword, user,
                              image_mode, min_likes, sort_by)


def save_raw(comments: list, out_dir: Path, progress=None,
             task_event=None, threads: int = 4) -> dict:
    return _cc.save_raw(sys.modules[__name__], comments, out_dir, progress,
                        task_event=task_event, threads=threads)


def export_comments_png(comments: list, meta: dict, out_path: Path,
                        progress=None) -> Path:
    return _cc.export_comments_png(sys.modules[__name__], comments, meta,
                                   out_path, progress)

