"""小红书 (Xiaohongshu) 解析器：笔记（视频/图文）+ 主页作品列表。

数据源：页面内嵌 window.__INITIAL_STATE__（游客 HTTP 可拿全量）。
- 笔记页：note.noteDetailMap[firstNoteId].note（标题/描述/互动/图片/视频流）
- 主页：user.userPageData.basicInfo + user.notes（作品卡片列表，含 xsecToken）
"""

import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qs

import httpx

from .base import PlatformBase, VideoInfo, now_str

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

_NOTE_URL_RE = re.compile(r"xiaohongshu\.com/(?:discovery/item|explore)/([a-f0-9]+)", re.I)
_USER_URL_RE = re.compile(r"xiaohongshu\.com/user/profile/([a-f0-9]+)", re.I)


def _balanced_json(html: str, marker: str):
    """从 HTML 中提取 marker = {...} 的完整 JSON（花括号配平扫描）。"""
    m = re.search(marker + r"\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    depth = 0
    j = start
    instr = False
    esc = False
    q = ""
    while j < len(html):
        c = html[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif instr:
            if c == q:
                instr = False
        elif c in ('"', "'"):
            instr = True
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    raw = html[start:j + 1]
    if not raw:
        return None
    for cand in (raw, re.sub(r",\s*([}\]])", r"\1", raw).replace("undefined", "null")):
        try:
            import json
            return json.loads(cand)
        except Exception:
            continue
    return None


def _parse_cn_count(v) -> int:
    """'1.7万'/'418'/None → int（无法解析返回 0）。"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*万", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"([\d.]+)\s*亿", s)
    if m:
        return int(float(m.group(1)) * 100000000)
    m = re.match(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _ms_to_str(ms) -> str:
    try:
        ts = int(ms)
        if ts > 10 ** 12:
            ts //= 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return ""


def _get_cookie() -> str:
    try:
        from auth import get_cookie
        return get_cookie("xiaohongshu") or ""
    except Exception:
        return ""


def _fetch(url: str, cookie: str = "") -> str:
    headers = {"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"小红书页面请求失败（HTTP {resp.status_code}）")
    return resp.text


def _video_url_of(note: dict) -> str:
    """从笔记 JSON 提取最高清 h264 直链。"""
    media = ((note.get("video") or {}).get("media") or {})
    stream = media.get("stream") or {}
    cands = []
    for codec in ("h264", "h265", "av1"):
        for s in (stream.get(codec) or []):
            url = s.get("masterUrl") or ""
            if url:
                cands.append((int(s.get("height") or 0), url))
            for b in (s.get("backupUrls") or []):
                if b:
                    cands.append((int(s.get("height") or 0) - 1, b))
    if not cands:
        return ""
    return max(cands, key=lambda x: x[0])[1]


def _note_to_info(note: dict, raw_url: str = "") -> Optional[VideoInfo]:
    if not note:
        return None
    user = note.get("user") or {}
    interact = note.get("interactInfo") or {}
    images = []
    for img in (note.get("imageList") or []):
        u = img.get("urlDefault") or img.get("url") or ""
        if u:
            images.append(u.replace("\\u002F", "/"))
    video_url = _video_url_of(note)
    tags = [t.get("name", "") for t in (note.get("tagList") or []) if t.get("name")]
    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    # 视频笔记标题常为空，用 desc 首行兜底
    if not title and desc:
        title = desc.splitlines()[0][:40]
    return VideoInfo(
        platform="xiaohongshu",
        kind="video" if video_url else "image",
        video_id=str(note.get("noteId") or ""),
        title=title,
        description=desc,
        author=user.get("nickname") or "",
        author_id=str(user.get("userId") or ""),
        play_count=0,
        like_count=_parse_cn_count(interact.get("likedCount")),
        comment_count=_parse_cn_count(interact.get("commentCount")),
        share_count=_parse_cn_count(interact.get("shareCount")),
        favorite_count=_parse_cn_count(interact.get("collectedCount")),
        duration=int(((note.get("video") or {}).get("media") or {})
                     .get("stream", {}).get("h264", [{}])[0].get("duration", 0) or 0) // 1000
        if video_url else 0,
        cover_url=images[0] if images else "",
        video_url=video_url,
        publish_time=_ms_to_str(note.get("time")),
        tags=tags,
        raw_url=raw_url,
        raw_data={},
        fetched_at=now_str(),
        images=images if not video_url else [],
    )


class XiaohongshuParser(PlatformBase):
    name = "xiaohongshu"

    def extract_id(self, url: str) -> str:
        m = _NOTE_URL_RE.search(url)
        return m.group(1) if m else ""

    def parse(self, url: str) -> Optional[VideoInfo]:
        m = _NOTE_URL_RE.search(url)
        if not m:
            raise ValueError("不是小红书笔记链接")
        html = _fetch(url)
        data = _balanced_json(html, r"window\.__INITIAL_STATE__")
        if not data:
            raise RuntimeError("小红书页面解析失败（可能需要 Cookie，或链接含失效 xsec_token）")
        note_map = (data.get("note") or {}).get("noteDetailMap") or {}
        first_id = (data.get("note") or {}).get("firstNoteId") or ""
        entry = note_map.get(first_id) or {}
        if entry.get("note") is None:
            # 失效/风控时 entry 为 {"note": null}
            raise RuntimeError(entry.get("errorMsg") or "小红书笔记数据为空（xsec_token 可能已失效）")
        info = _note_to_info(entry.get("note") or {}, raw_url=url)
        if info:
            info.raw_url = url
        return info

    # ==================== 账号数据（主页信息） ====================

    def fetch_profile(self, url: str) -> dict:
        """主页 → 账号信息 dict（昵称/简介/粉丝数等）。"""
        html = _fetch(url)
        data = _balanced_json(html, r"window\.__INITIAL_STATE__")
        if not data:
            raise RuntimeError("小红书主页解析失败（可能需要 Cookie）")
        user = data.get("user") or {}
        upd = user.get("userPageData") or {}
        basic = upd.get("basicInfo") or {}
        if not basic:
            raise RuntimeError("小红书主页无用户数据（可能需要 Cookie）")
        fans = following = likes = 0
        for it in (upd.get("interactions") or []):
            t = it.get("type")
            if t == "fans":
                fans = _parse_cn_count(it.get("count"))
            elif t == "follows":
                following = _parse_cn_count(it.get("count"))
            elif t == "interaction":
                likes = _parse_cn_count(it.get("count"))
        return {
            "platform": "xiaohongshu",
            "name": basic.get("nickname") or "",
            "fans": fans,
            "following": following,
            "likes": likes,
            "sign": basic.get("desc") or "",
            "avatar": basic.get("imageb") or basic.get("images") or "",
            "uid": basic.get("redId") or "",
        }

    # ==================== 主页作品列表 ====================

    def fetch_user_posts(self, url: str, count: int = 20, **kwargs) -> list:
        """主页 → 笔记卡片列表（首批 SSR 数据）。

        游客态 notes 常为空壳（list of 空list），需 Cookie 才有卡片内容。
        """
        m = _USER_URL_RE.search(url)
        if not m:
            raise ValueError("不是小红书主页链接")

        def _extract(html: str) -> list:
            data = _balanced_json(html, r"window\.__INITIAL_STATE__")
            if not data:
                return []
            notes = ((data.get("user") or {}).get("notes")) or []
            result = []
            for wrapper in notes[:max(1, count)]:
                if not isinstance(wrapper, dict):
                    continue
                card = wrapper.get("noteCard") or {}
                if not isinstance(card, dict) or not card:
                    continue
                interact = card.get("interactInfo") or {}
                cover = card.get("cover") or {}
                cover_url = cover.get("urlDefault") or cover.get("urlPre") or ""
                xsec = card.get("xsecToken") or wrapper.get("xsecToken") or ""
                note_url = ""
                nid = card.get("noteId") or wrapper.get("id") or ""
                if nid:
                    note_url = f"https://www.xiaohongshu.com/discovery/item/{nid}"
                    if xsec:
                        note_url += f"?xsec_token={xsec}&xsec_source=pc_search"
                result.append(VideoInfo(
                    platform="xiaohongshu",
                    kind="video",
                    video_id=str(nid),
                    title=(card.get("displayTitle") or "")[:60],
                    author=(card.get("user") or {}).get("nickname") or "",
                    author_id=str((card.get("user") or {}).get("userId") or ""),
                    like_count=_parse_cn_count(interact.get("likedCount")),
                    cover_url=cover_url,
                    video_url=note_url,
                    publish_time=_ms_to_str(card.get("time")),
                    raw_url=note_url or url,
                    fetched_at=now_str(),
                ))
            return result

        # 一级：游客
        result = _extract(_fetch(url))
        if result:
            return result
        # 二级：Cookie（游客态 notes 为空壳）
        ck = _get_cookie()
        if ck:
            try:
                result = _extract(_fetch(url, cookie=ck))
                if result:
                    return result
            except Exception:
                pass
        raise RuntimeError("小红书主页作品需要登录：请在「Cookie 管理」中登录小红书后重试")
