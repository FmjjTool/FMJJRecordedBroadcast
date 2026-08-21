"""B站链接解析：输入链接/短链/分享文本 → (类型, ID)。

对齐原版 BiliResourceDownloader 的 utils/linkResolver.ts 全部格式：
直播间/空间/动态/视频/装扮/收藏集（blackboard + h5/mall 系列）+ b23.tv 短链。
"""

from __future__ import annotations

import re

import httpx

# 类型：liveroom / user / dynamic / video / suit / lottery

_BLACKBOARD = re.compile(
    r"https?://(?:www\.)?bilibili\.com/blackboard/activity-[\w-]+\.html", re.I)
_DLC_ACT = re.compile(
    r"https?://(?:www\.)?bilibili\.com/h5/mall/(?:equity-link/digital-card|digital-card/home)", re.I)
_SUIT_ITEM = re.compile(
    r"https?://(?:www\.)?bilibili\.com/h5/mall/(?:equity-link/collect-home|equity-link/home)", re.I)
_SUIT_DETAIL = re.compile(
    r"https?://(?:www\.)?bilibili\.com/h5/mall/suit/detail", re.I)
_LIVE = re.compile(r"https?://live\.bilibili\.com/(\d+)", re.I)
_SPACE = re.compile(r"https?://space\.bilibili\.com/(\d+)", re.I)
_DYNAMIC = re.compile(r"https?://(?:www\.)?(?:t\.bilibili\.com|bilibili\.com/opus)/(\d+)", re.I)
_VIDEO = re.compile(r"https?://(?:www\.)?bilibili\.com/(?:video/)?((?:av|AV)\d+|BV[0-9A-Za-z]+)", re.I)
_SHORT = re.compile(r"https?://(b23\.tv|bili2233\.cn)/[\w\-]+", re.I)
_SHARE = re.compile(r"^【.+】 ?http.+$", re.S)


def resolve_short(url: str) -> str:
    """b23.tv / bili2233.cn 短链 → 跟随重定向取真实地址。"""
    try:
        with httpx.Client(timeout=10, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as c:
            return str(c.get(url).url)
    except Exception:
        return ""


def parse_link(text: str) -> tuple[str, str] | None:
    """解析输入文本 → (类型, ID)；无法识别返回 None。"""
    s = (text or "").strip()
    if not s:
        return None
    # 分享文本格式：【标题】 链接
    if _SHARE.match(s):
        s = re.sub(r"^【.+】 ?", "", s)
    if _SHORT.match(s):
        s = resolve_short(s) or s

    def q(url_re: re.Pattern, full: str, *keys: str) -> str | None:
        """取 URL query 中第一个存在的参数。"""
        m = url_re.match(full)
        if not m:
            return None
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(full).query)
        for k in keys:
            v = qs.get(k, [""])[0]
            if v:
                return v
        return None

    # 装扮/收藏集系列链接（先匹配专属路径）
    if _BLACKBOARD.match(s.split("?")[0]):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(s).query)
        typ = qs.get("type", [""])[0]
        oid = qs.get("id", [""])[0]
        if oid:
            if typ == "dlc":
                return ("lottery", oid)
            if typ == "suit":
                return ("suit", oid)
        return None
    if _DLC_ACT.match(s.split("?")[0]):
        v = q(_DLC_ACT, s, "act_id")
        return ("lottery", v) if v and v.isdigit() else None
    if _SUIT_ITEM.match(s.split("?")[0]):
        v = q(_SUIT_ITEM, s, "item_id")
        return ("suit", v) if v and v.isdigit() else None
    if _SUIT_DETAIL.match(s.split("?")[0]):
        v = q(_SUIT_DETAIL, s, "id")
        return ("suit", v) if v else None

    for pat, typ in ((_LIVE, "liveroom"), (_SPACE, "user"), (_DYNAMIC, "dynamic")):
        m = pat.match(s)
        if m:
            return (typ, m.group(1))
    m = _VIDEO.match(s)
    if m:
        return ("video", m.group(1))
    # 纯编号输入
    if re.fullmatch(r"(?:av|AV)\d+|BV[0-9A-Za-z]+", s):
        return ("video", s)
    return None
