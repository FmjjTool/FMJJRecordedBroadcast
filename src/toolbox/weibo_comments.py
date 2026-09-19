"""微博评论区：抓取（文本原始数据）/ 搜索筛选 / 导出长图。

- 视频号 fid（1034:xxx）→ h5 playinfo 游客拿 mid/标题/作者
- 普通微博 bid → m.weibo.cn 详情游客拿 mid
- 评论：m.weibo.cn/api/comments/show 翻页（游客可用；被拒且配置了
  微博 Cookie 时带登录态重试）
"""

from __future__ import annotations

import html as _html
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from platforms.weibo import WeiboParser

PAGE_SIZE = 10
IMG_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/134.0.0.0 Safari/537.36",
              "Referer": "https://weibo.com/"}
M_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
M_COMMENTS_API = "https://m.weibo.cn/api/comments/show"
HOTFLOW_API = "https://m.weibo.cn/comments/hotflow"

_FID_RE = re.compile(
    r"(?:video\.weibo\.com/show\?fid=|h5\.video\.weibo\.com/show/|weibo\.com/tv/show/)"
    r"(\d+:\d+)")
_BID_RE = re.compile(r"(?:weibo\.com|m\.weibo\.cn)/(?:\d+/|status/|detail/)([A-Za-z0-9]{8,16})")


def _login_cookie() -> str:
    try:
        from auth import get_cookie
        return get_cookie("weibo") or ""
    except Exception:
        return ""


# ==================== 输入解析 ====================

def parse_input(text: str) -> tuple[str, str]:
    """解析输入 → (kind, id)。kind: fid(视频号) / bid(普通微博) / ''。

    支持：tv/show、video.weibo.com/show?fid=、h5 播放器链接、普通微博链接、
    m.weibo.cn 链接、纯 1034:xxx、纯 base62 bid、t.cn 短链（网络跳转解析）。
    """
    s = (text or "").strip()
    if not s:
        return "", ""
    if re.fullmatch(r"\d+:\d+", s):
        return "fid", s
    m = re.search(r"t\.cn/([A-Za-z0-9]+)", s)
    if m:
        try:
            with httpx.Client(timeout=10, follow_redirects=True,
                              headers={"User-Agent": M_UA}) as c:
                r = c.get(f"https://t.cn/{m.group(1)}")
                s = str(r.url)
        except Exception:
            return "", ""
    m = _FID_RE.search(s)
    if m:
        return "fid", m.group(1)
    m = _BID_RE.search(s)
    if m:
        return "bid", m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{8,16}", s):
        return "bid", s
    return "", ""


# ==================== 抓取 ====================

def fetch_video_info(ident: str, cookie: str = "") -> dict:
    """视频详情预检。返回 {exists, unknown, desc, author, comment_count, mid}。"""
    out = {"exists": False, "unknown": False, "desc": "", "author": "",
           "comment_count": 0, "mid": ""}
    if re.fullmatch(r"\d+:\d+", ident or ""):
        # 视频号：h5 playinfo（游客可用；client 不能用移动端 UA）
        try:
            with httpx.Client(timeout=15) as c:
                play = WeiboParser()._fetch_playinfo(ident, c)
        except Exception:
            play = None
        if not play:
            return {**out, "unknown": True}
        out.update({
            "exists": True,
            "desc": WeiboParser._clean_rich(play.get("text") or "")
            or str(play.get("title") or "")[:40],
            "author": play.get("nickname") or play.get("author") or "",
            "comment_count": int(play.get("comments_count") or 0),
            "mid": str(play.get("mid") or play.get("media_id") or ""),
        })
        return out
    # 普通微博：m端详情（游客可用）。
    # statuses/show 顶层直接是 status 对象（无 ok/data 包装），与 comments/show 不同。
    try:
        with httpx.Client(timeout=15,
                          headers={"User-Agent": M_UA,
                                   "Referer": "https://m.weibo.cn/",
                                   "X-Requested-With": "XMLHttpRequest"},
                          follow_redirects=True) as c:
            c.get("https://m.weibo.cn/")
            r = c.get(f"https://m.weibo.cn/api/statuses/show?id={ident}")
            d = r.json()
    except Exception:
        return {**out, "unknown": True}
    st = d.get("data") or d
    if not (st.get("mid") or st.get("idstr") or st.get("id")):
        return out
    out.update({
        "exists": True,
        "desc": WeiboParser._clean_rich(st.get("text") or "")[:60] or "微博",
        "author": (st.get("user") or {}).get("screen_name") or "",
        "comment_count": int(st.get("comments_count") or 0),
        "mid": str(st.get("mid") or st.get("id") or ""),
    })
    return out


def fetch_comments(mid: str, cookie: str = "", pages: int = 10,
                   progress=None, should_stop=None) -> list:
    """翻页抓顶层评论。返回原始评论 dict 列表。

    hotflow（m端网页同款，max_id 链式翻页）优先：有登录 Cookie 直接
    登录态翻页（游客 max_id 与登录会话不互通，无法续翻）；无 Cookie
    游客翻到被拒为止（通常第 1 页约 20 条）。
    hotflow 整体不可用（无热评流的老微博）时回退 api/comments/show。
    """
    if not str(mid).isdigit():
        info = fetch_video_info(mid)
        mid = info.get("mid") or ""
        if not mid:
            raise RuntimeError("无法解析微博 mid")
    ck = cookie or _login_cookie()
    if ck:
        out, _, _ = _fetch_hotflow(mid, pages, _parse_cookies(ck),
                                    progress=progress, should_stop=should_stop)
    else:
        out, _, _ = _fetch_hotflow(mid, pages, {}, progress=progress,
                                    should_stop=should_stop)
    if not out:
        out, _ = _fetch_legacy(mid, pages, ck, progress, should_stop)
    return out


def _fetch_hotflow(mid: str, pages: int, cookies: dict,
                   resume: dict | None = None,
                   progress=None, should_stop=None):
    """m端 comments/hotflow 翻页（max_id 链式，每页约 20 条）。

    resume：断点续翻 {max_id, max_id_type, used}。
    返回 (评论列表, 被拒标记, 断点 state)。
    """
    headers = {"User-Agent": M_UA,
               "Referer": f"https://m.weibo.cn/detail/{mid}",
               "X-Requested-With": "XMLHttpRequest"}
    out, denied = [], False
    max_id = (resume or {}).get("max_id") or ""
    mtype = (resume or {}).get("max_id_type") or 0
    used = (resume or {}).get("used") or 0
    state = {"max_id": "", "max_id_type": 0, "used": used}
    with httpx.Client(timeout=15, cookies=cookies, headers=headers,
                      follow_redirects=True) as c:
        c.get("https://m.weibo.cn/")
        while used < pages:
            if should_stop and should_stop():
                break
            pn = used + 1
            url = (f"{HOTFLOW_API}?id={mid}&mid={mid}"
                   + (f"&max_id={max_id}&max_id_type={mtype}" if max_id
                      else "&max_id_type=0"))
            try:
                r = c.get(url)
                d = r.json()
            except Exception as e:
                if progress:
                    progress(f"第{pn}页请求失败：{e}", pn, pages)
                break
            if d.get("ok") != 1:
                msg = str(d.get("msg") or "")
                if "还没有人评论" in msg or "暂无数据" in msg:
                    break  # 确实没有评论，不算被拒
                denied = True
                if progress:
                    progress(f"hotflow 第{pn}页被拒（{msg[:20] or '需登录'}）",
                             pn, pages)
                break
            data = d.get("data") or {}
            items = data.get("data") or []
            out.extend(items)
            used += 1
            max_id = data.get("max_id") or ""
            mtype = data.get("max_id_type") or 0
            state.update({"max_id": max_id, "max_id_type": mtype, "used": used})
            if progress:
                progress(f"已获取 {len(out)} 条评论", used, pages)
            if not items or not max_id:
                state["max_id"] = ""
                break
            time.sleep(0.25)
    return out, denied, state


def _fetch_legacy(mid: str, pages: int, cookie: str,
                  progress=None, should_stop=None):
    """老接口 api/comments/show 翻页（page 翻页，游客可翻前 2 页）兜底。"""
    headers = {"User-Agent": M_UA, "Referer": "https://m.weibo.cn/",
               "X-Requested-With": "XMLHttpRequest"}
    out, denied = [], False
    with httpx.Client(timeout=15, headers=headers,
                      cookies=_parse_cookies(cookie), follow_redirects=True) as c:
        c.get("https://m.weibo.cn/")
        for pn in range(1, pages + 1):
            if should_stop and should_stop():
                break
            try:
                r = c.get(f"{M_COMMENTS_API}?id={mid}&page={pn}")
                d = r.json()
            except Exception as e:
                if progress:
                    progress(f"第{pn}页请求失败：{e}", pn, pages)
                break
            if d.get("ok") != 1:
                msg = str(d.get("msg") or "")
                if progress and pn == 1:
                    progress(f"m端接口被拒：{msg[:30]}", pn, pages)
                if msg and "暂无数据" not in msg:
                    denied = True
                break
            data = ((d.get("data") or {}).get("data")) or []
            out.extend(data)
            if progress:
                progress(f"已获取 {len(out)} 条评论", pn, pages)
            if not data:
                break
            time.sleep(0.25)
    return out, denied


def _parse_cookies(cookie: str) -> dict:
    out = {}
    for part in (cookie or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ==================== 字段提取 ====================

def _strip_html(text: str) -> str:
    """评论 HTML → 纯文本：emoji img 取 alt、@链接保留文字、<br> 换行。"""
    if not text:
        return ""
    t = re.sub(r"<br\s*/?\s*>", "\n", text, flags=re.I)
    t = re.sub(r'<img[^>]*alt="([^"]+)"[^>]*>', r"\1", t)
    t = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    return _html.unescape(t).strip()


def comment_text(c: dict) -> str:
    text = _strip_html(c.get("text") or c.get("text_raw") or "")
    # 回复引用前缀（"回复@xxx:"）——HTML 清洗后再剥离，避免链接内冒号干扰
    if c.get("reply_id"):
        m = re.match(r"回复[^:：]{1,30}[:：]\s*", text)
        if m:
            text = text[m.end():]
    return text


def comment_user(c: dict) -> str:
    return (c.get("user") or {}).get("screen_name") or "?"


def comment_likes(c: dict) -> int:
    try:
        return int(c.get("like_counts") or c.get("like_count")
                   or c.get("attitudes_count") or 0)
    except (TypeError, ValueError):
        return 0


def avatar_url(c: dict) -> str:
    u = c.get("user") or {}
    return u.get("avatar_hd") or u.get("profile_image_url") or ""


def image_urls(c: dict) -> list:
    """微博评论配图（pic_infos / pic 兜底）。"""
    out = []
    pics = c.get("pic_infos")
    items = pics.values() if isinstance(pics, dict) else (
        pics if isinstance(pics, list) else [])
    for p in items:
        u = ((p or {}).get("large") or {}).get("url") or (p or {}).get("bmiddle") or ""
        if u:
            out.append(u)
    if not out:
        p = c.get("pic") or {}
        u = (p.get("large") or {}).get("url") or p.get("bmiddle") or ""
        if u:
            out.append(u)
    return out


def comment_ts(c: dict) -> int:
    ts = c.get("created_at")
    if isinstance(ts, (int, float)):
        return int(ts)
    s = str(ts or "").strip()
    now = int(time.time())
    try:
        return int(datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").timestamp())
    except ValueError:
        pass
    m = re.fullmatch(r"(\d+)秒前", s)
    if m:
        return now - int(m.group(1))
    m = re.fullmatch(r"(\d+)分钟前", s)
    if m:
        return now - int(m.group(1)) * 60
    m = re.fullmatch(r"(\d+)小时前", s)
    if m:
        return now - int(m.group(1)) * 3600
    m = re.fullmatch(r"(\d+)天前", s)
    if m:
        return now - int(m.group(1)) * 86400
    if s.startswith("刚刚"):
        return now
    m = re.fullmatch(r"昨天\s*(\d{1,2}):(\d{2})", s)
    if m:
        t = datetime.now() - timedelta(days=1)
        return int(t.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                             second=0).timestamp())
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", s)
    if m:
        t = datetime.now()
        try:
            return int(t.replace(month=int(m.group(1)), day=int(m.group(2)),
                                 hour=12, minute=0, second=0).timestamp())
        except ValueError:
            return 0
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return int(datetime(int(m.group(1)), int(m.group(2)),
                                int(m.group(3))).timestamp())
        except ValueError:
            return 0
    return 0


def comment_time(c: dict) -> str:
    raw = str(c.get("created_at") or "").strip()
    # m端评论常只给日期（YYYY-MM-DD），原样显示避免补 00:00 误导
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", raw):
        return raw
    ts = comment_ts(c)
    if not ts:
        return raw
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return ""


def comment_extra(c: dict) -> str:
    src = _strip_html(c.get("source") or "")
    if src.startswith("来自"):
        src = src[2:]
    parts = [x for x in (comment_time(c), src) if x]
    return " · ".join(parts)


# ==================== 搜索 / 筛选 / 保存 / 导出（共用层） ====================

from toolbox import comments_common as _cc


def filter_comments(comments: list, keyword: str = "", user: str = "",
                    image_mode: str = "all", min_likes: int = 0,
                    sort_by: str = "default") -> list:
    return _cc.filter_comments(sys.modules[__name__], comments, keyword, user,
                               image_mode, min_likes, sort_by)


def save_raw(comments: list, out_dir: Path, progress=None) -> dict:
    return _cc.save_raw(sys.modules[__name__], comments, out_dir, progress)


def export_comments_png(comments: list, meta: dict, out_path: Path,
                        progress=None) -> Path:
    return _cc.export_comments_png(sys.modules[__name__], comments, meta,
                                   out_path, progress)
