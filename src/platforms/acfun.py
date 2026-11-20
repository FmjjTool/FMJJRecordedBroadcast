"""AcFun 解析器：主页作品列表（动态接口 + Cookie）。

接口：GET https://www.acfun.cn/u/{uid}/dynamics?pcursor=返回
{"result":0,"feedList":[...],"pcursor":"..."}，按 pcursor 翻页。
作品字段：caption(标题) / coverUrl(封面) / createTime(毫秒) / videoId /
playDuration(毫秒) / viewCount / likeCount / commentCount / bananaCount /
detail(正文) / user.userName(作者)。

未登录也能拿到部分数据；登录后（Cookie 管理 → AcFun）条数与字段更完整。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

import httpx

from .base import PlatformBase, VideoInfo, now_str

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
USER_URL_RE = re.compile(r"acfun\.cn/u/(\d+)", re.I)
VIDEO_URL_RE = re.compile(r"acfun\.cn/v/(ac\d+)", re.I)
API = "https://www.acfun.cn/u/{uid}/dynamics"


def _cookie() -> str:
    try:
        from auth import get_cookie
        return get_cookie("acfun") or ""
    except Exception:
        return ""


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def _ms_to_str(ms) -> str:
    try:
        ts = int(ms)
        if ts > 10 ** 12:
            ts //= 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _cover(item: dict) -> str:
    url = item.get("coverUrl") or ""
    if url:
        return url
    info = item.get("coverImgInfo") or {}
    thumb = (info.get("thumbnailImage") or {})
    for c in thumb.get("cdnUrls") or []:
        if c.get("url"):
            return c["url"]
    return ""


def _item_to_info(item: dict, uid: str) -> Optional[VideoInfo]:
    vid = str(item.get("videoId") or "").strip()
    title = str(item.get("caption") or "").strip()
    if not vid or not title:
        return None
    user = item.get("user") or {}
    ac_id = str(item.get("resourceId") or item.get("videoId") or "").strip()
    info = VideoInfo(
        platform="acfun",
        kind="video",
        video_id=vid,
        title=title,
        description=str(item.get("detail") or ""),
        author=str(user.get("userName") or user.get("name") or ""),
        author_id=str(item.get("authorId") or user.get("userId") or uid),
        author_face=str(user.get("userImg") or ""),
        play_count=_to_int(item.get("viewCount")),
        like_count=_to_int(item.get("likeCount")),
        comment_count=_to_int(item.get("commentCount")),
        share_count=_to_int(item.get("shareCount")),
        favorite_count=_to_int(item.get("stowCount")),
        duration=_to_int(item.get("playDuration")) // 1000,
        cover_url=_cover(item),
        video_url=f"https://www.acfun.cn/v/ac{ac_id}",
        publish_time=_ms_to_str(item.get("createTime") or item.get("time")),
        fetched_at=now_str(),
        raw_data=item,
    )
    return info


def _balanced_json(text: str, marker: str):
    """取 marker 之后第一个配平的 {...}（字符串内的花括号不计数）。"""
    i = text.find(marker)
    if i < 0:
        return None
    i += len(marker)
    depth, j, instr, esc = 0, i, False, False
    while j < len(text):
        c = text[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif instr:
            if c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    try:
        return json.loads(text[i:j + 1])
    except Exception:
        return None


def fetch_streams(ac_id: str, cookie: str = "") -> list[tuple]:
    """返回 [(height, bitrate, m3u8_url)]，按清晰度从高到低。

    ac_id 是作品页 `ac` 后面的数字（即 feed 里的 resourceId）。
    """
    headers = {
        "User-Agent": UA,
        "Referer": f"https://www.acfun.cn/v/ac{ac_id}",
    }
    if cookie:
        headers["Cookie"] = cookie
    resp = httpx.get(f"https://www.acfun.cn/v/ac{ac_id}", headers=headers,
                     timeout=25, follow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"AcFun 作品页请求失败（HTTP {resp.status_code}）")
    page = _balanced_json(resp.text, "window.pageInfo = window.videoInfo = ")
    if not page:
        raise RuntimeError("AcFun 作品页未解析到 pageInfo（可能已下架或需要登录）")
    cvi = page.get("currentVideoInfo") or {}
    ks_raw = cvi.get("ksPlayJson") or ""
    try:
        ks = json.loads(ks_raw) if isinstance(ks_raw, str) else (ks_raw or {})
    except Exception:
        raise RuntimeError("AcFun ksPlayJson 解析失败")
    out = []
    for a in ks.get("adaptationSet") or []:
        for r in a.get("representation") or []:
            url = r.get("url") or ""
            if url:
                out.append((int(r.get("height") or 0), int(r.get("avgBitrate") or 0), url))
    out.sort(key=lambda x: (-x[0], -x[1]))
    if not out:
        raise RuntimeError("AcFun 未返回可用播放地址")
    return out


def download_video(ac_id: str, out_path: str, cookie: str = "", on_log=None) -> str:
    """用 ffmpeg 把 m3u8 拉成 mp4（-c copy，不转码）。返回输出路径。"""
    import subprocess

    from toolbox.utils import find_ffmpeg_exe

    exe = find_ffmpeg_exe() or "ffmpeg"
    streams = fetch_streams(ac_id, cookie)
    url = streams[0][2]
    headers = f"Referer: https://www.acfun.cn/\r\nUser-Agent: {UA}\r\n"
    args = [exe, "-y", "-loglevel", "error",
            "-headers", headers,
            "-allowed_extensions", "ALL",
            "-i", url,
            "-c", "copy", "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            out_path]
    if on_log:
        on_log(f"  清晰度 {streams[0][0]}p")
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
        raise RuntimeError(f"ffmpeg 失败：{err}")
    return out_path


class AcfunPlatform(PlatformBase):
    name = "acfun"
    display_name = "AcFun"

    # ==================== URL 识别 ====================

    def match_user(self, url: str) -> bool:
        return bool(USER_URL_RE.search(url or ""))

    def match_video(self, url: str) -> bool:
        return bool(VIDEO_URL_RE.search(url or ""))

    def normalize_user_url(self, url: str) -> str:
        m = USER_URL_RE.search(url or "")
        return f"https://www.acfun.cn/u/{m.group(1)}" if m else url

    # ==================== 主页作品 ====================

    def fetch_user_posts(self, url: str, count: int = 20, **kwargs) -> list:
        m = USER_URL_RE.search(url or "")
        if not m:
            raise ValueError("不是 AcFun 主页链接（应形如 https://www.acfun.cn/u/550551）")
        uid = m.group(1)
        want = max(1, count)
        ck = _cookie()
        headers = {
            "User-Agent": UA,
            "Referer": f"https://www.acfun.cn/u/{uid}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if ck:
            headers["Cookie"] = ck
        out, cursor, seen = [], "", set()
        stop_check = kwargs.get("stop_check")
        with httpx.Client(timeout=20, follow_redirects=True) as cli:
            for page in range(200):
                if stop_check is not None and stop_check():
                    break
                params = {"pcursor": cursor} if cursor else {}
                resp = cli.get(API.format(uid=uid), params=params, headers=headers)
                if resp.status_code != 200:
                    raise RuntimeError(f"AcFun 接口失败（HTTP {resp.status_code}）")
                data = resp.json()
                if data.get("result") not in (0, None):
                    raise RuntimeError(f"AcFun 接口返回异常：{data.get('error_msg') or data.get('result')}")
                feeds = data.get("feedList") or []
                if not feeds:
                    break
                for item in feeds:
                    info = _item_to_info(item, uid)
                    if info and info.video_id not in seen:
                        seen.add(info.video_id)
                        out.append(info)
                        if len(out) >= want:
                            return out
                nxt = str(data.get("pcursor") or "")
                if not nxt or nxt == cursor:
                    break
                cursor = nxt
        return out

    # ==================== 单条解析（基类接口） ====================

    def parse(self, url: str) -> Optional[VideoInfo]:
        """单条稿件：作品页字段要浏览器/播放接口才全，这里先给基础信息。"""
        return self.fetch_video(url)

    def extract_id(self, url: str) -> str:
        m = VIDEO_URL_RE.search(url or "")
        return m.group(1)[2:] if m else ""

    def fetch_video(self, url: str, **kwargs) -> VideoInfo:
        m = VIDEO_URL_RE.search(url or "")
        if not m:
            raise ValueError("不是 AcFun 稿件链接（应形如 https://www.acfun.cn/v/ac123456）")
        ac = m.group(1)
        vid = ac[2:]
        info = VideoInfo(
            platform="acfun", kind="video", video_id=vid,
            video_url=f"https://www.acfun.cn/v/{ac}", raw_url=url,
            fetched_at=now_str(),
        )
        return info
