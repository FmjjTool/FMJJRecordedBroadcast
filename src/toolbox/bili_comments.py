"""B站评论区：抓取（图片+文本原始数据）/ 搜索筛选 / 导出长图。

- 视频：/x/web-interface/view 解析 BV/av + 标题（游客 buvid 指纹 / 登录 Cookie）
- 评论：/x/v2/reply WBI 签名翻页（热评排序，游客可翻前几页）
- 图片：content.pictures（B站评论配图）
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

from bili.wbi import UA, build_cookie_header, sign_wbi

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
REPLY_API = "https://api.bilibili.com/x/v2/reply"
PAGE_SIZE = 20
IMG_HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

# 风控码（游客指纹被拦）：无法判定，不当作「视频不存在」
_RISK_CODES = {-352, -412, 412, -509, -799}


def _login_cookie() -> str:
    try:
        from auth import get_cookie
        return get_cookie("bilibili") or ""
    except Exception:
        return ""


def _headers() -> dict:
    return {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
            "Accept-Language": "zh-CN,zh;q=0.9"}


# ==================== 输入解析 ====================

def parse_input(text: str) -> tuple[str, str]:
    """解析输入 → (kind, id)。kind: bvid / aid / ''。

    支持：视频链接（bilibili.com/video/BV... 或 b23.tv 短链，短链在此网络跳转解析）、
    纯 BV 号、纯 av 号/纯数字 aid、含 ?bvid= 的链接。
    """
    s = (text or "").strip()
    if not s:
        return "", ""
    m = re.search(r"b23\.tv/([A-Za-z0-9]+)", s)
    if m:
        try:
            with httpx.Client(timeout=10, follow_redirects=True,
                              headers=_headers()) as c:
                r = c.get(f"https://b23.tv/{m.group(1)}")
                s = str(r.url)
        except Exception:
            return "", ""
    m = (re.search(r"bilibili\.com/video/(BV[A-Za-z0-9]{10})", s)
         or re.search(r"[?&]bvid=(BV[A-Za-z0-9]{10})", s, re.I))
    if m:
        return "bvid", m.group(1)
    m = re.search(r"(?:^|[^\d])[aA][vV](\d{5,12})", s)
    if m:
        return "aid", m.group(1)
    m = re.fullmatch(r"(BV[A-Za-z0-9]{10})", s)
    if m:
        return "bvid", m.group(1)
    if re.fullmatch(r"\d{5,12}", s):
        return "aid", s
    return "", ""


# ==================== 抓取 ====================

def fetch_video_info(ident: str, cookie: str = "") -> dict:
    """视频详情预检。返回 {exists, unknown, desc, author, comment_count, aid}。"""
    out = {"exists": False, "unknown": False, "desc": "", "author": "",
           "comment_count": 0, "aid": ""}
    param = {"bvid": ident} if ident.upper().startswith("BV") else {"aid": ident}
    try:
        with httpx.Client(timeout=15, headers=_headers()) as c:
            ck = cookie or build_cookie_header(c, _login_cookie())
            r = c.get(VIEW_API, params=param, headers={"Cookie": ck})
            d = r.json()
    except Exception:
        return {**out, "unknown": True}
    code = d.get("code")
    if code != 0:
        if code in _RISK_CODES:
            out["unknown"] = True
        return out
    data = d.get("data") or {}
    stat = data.get("stat") or {}
    out.update({
        "exists": True,
        "desc": str(data.get("title") or ""),
        "author": (data.get("owner") or {}).get("name") or "",
        "comment_count": int(stat.get("reply") or 0),
        "aid": str(data.get("aid") or ""),
    })
    return out


def fetch_comments(oid: str, cookie: str = "", pages: int = 10,
                   progress=None, should_stop=None) -> list:
    """翻页抓顶层评论（含图片）。返回原始 reply dict 列表。

    oid 支持 aid 或 BV 号（自动转 aid）。热评排序（sort=2）。
    游客可翻前若干页，深翻页 B 站要求登录（会返回空 replies）。
    """
    if not oid.isdigit():
        info = fetch_video_info(oid, cookie)
        oid = info.get("aid") or ""
        if not oid:
            raise RuntimeError("无法解析视频 aid（视频不存在或被风控）")
    if not cookie:
        cookie = _login_cookie()
    out = []
    with httpx.Client(timeout=15, headers=_headers()) as c:
        ck = build_cookie_header(c, cookie)
        for pn in range(1, pages + 1):
            if should_stop and should_stop():
                break
            params = {"type": 1, "oid": int(oid), "pn": pn,
                      "ps": PAGE_SIZE, "sort": 2}
            try:
                url = sign_wbi(REPLY_API, params, c)
                r = c.get(url, headers={"Cookie": ck,
                                        "Referer": "https://www.bilibili.com/"})
                d = r.json()
            except Exception as e:
                if progress:
                    progress(f"第{pn}页请求失败：{e}", pn, pages)
                break
            code = d.get("code")
            if code != 0:
                if progress:
                    progress(f"第{pn}页 code={code}，停止"
                             + ("（游客深翻页需登录 Cookie）" if pn > 1 else ""), pn, pages)
                break
            data = d.get("data") or {}
            replies = data.get("replies") or []
            out.extend(replies)
            if progress:
                progress(f"已获取 {len(out)} 条评论", pn, pages)
            if not replies:
                break
            time.sleep(0.2)
    return out


# ==================== 字段提取 ====================

def comment_text(c: dict) -> str:
    return (c.get("content") or {}).get("message") or ""


def comment_user(c: dict) -> str:
    return (c.get("member") or {}).get("uname") or "?"


def comment_likes(c: dict) -> int:
    try:
        return int(c.get("like") or 0)
    except (TypeError, ValueError):
        return 0


def avatar_url(c: dict) -> str:
    return (c.get("member") or {}).get("avatar") or ""


def image_urls(c: dict) -> list:
    out = []
    for p in ((c.get("content") or {}).get("pictures") or []):
        u = p.get("img_src") or ""
        if u:
            out.append(u)
    return out


def comment_ts(c: dict) -> int:
    try:
        return int(c.get("ctime") or 0)
    except (TypeError, ValueError):
        return 0


def comment_time(c: dict) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(comment_ts(c)))
    except (TypeError, ValueError, OSError):
        return ""


def comment_extra(c: dict) -> str:
    loc = ((c.get("reply_control") or {}).get("location") or "").replace("IP属地：", "")
    parts = [x for x in (comment_time(c), loc) if x]
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
