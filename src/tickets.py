"""票务平台解析：大麦 / 秀动 / B站会员购。

数据模型 ShowEvent（演出）+ ShowSession（场次）+ TicketTier（票档）。
- 大麦：mtop API（两步令牌签名，游客可用）——艺人页演出列表 + 演出详情
- 秀动：HTML 解析（游客可用）——艺人页演出列表 + 事件详情（含票种）
- B站会员购：公开 API（游客可用）——关键词搜索 + 项目详情（场次+票档）
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


@dataclass
class TicketTier:
    name: str = ""      # 票档名（预售票 / VIP预售票 / 【9.25】180）
    price: str = ""     # 价格
    status: str = ""    # 已售罄/预售中/未开售/售票中
    desc: str = ""      # 附加说明


@dataclass
class ShowSession:
    name: str = ""      # 场次名（10月1日 / 【9.25】）
    start_time: str = ""
    tickets: list = field(default_factory=list)   # TicketTier 列表


@dataclass
class ShowEvent:
    platform: str = ""       # damai / showstart / bilibili_show
    platform_cn: str = ""
    event_id: str = ""
    title: str = ""
    subtitle: str = ""       # 副标题（如 苏州音乐会 → "【深圳】..."）
    artists: str = ""
    city: str = ""
    venue: str = ""
    address: str = ""
    show_time: str = ""
    show_duration: str = ""  # 演出时长
    sale_start: str = ""     # 开售时间
    price_range: str = ""
    status: str = ""
    url: str = ""
    cover: str = ""
    want_count: str = ""
    description_html: str = ""   # 演出介绍（HTML，含宣传图）
    description_text: str = ""   # 纯文本演出介绍
    images: list = field(default_factory=list)            # 宣传图/剧照 URL 列表
    seat_images: list = field(default_factory=list)       # 座位图 URL 列表
    notice_items: list = field(default_factory=list)      # 购票须知列表 [{"标题":..., "内容":...}]
    faqs: list = field(default_factory=list)              # FAQ 列表
    refund_policy: str = ""   # 退票/换票规则
    merchant: str = ""       # 主办方/项目方
    contact: str = ""        # 客服/联系方式
    venue_lat: str = ""      # 场馆经纬度
    venue_lng: str = ""
    category: str = ""       # 演出分类（音乐会/话剧/体育...）
    tags: list = field(default_factory=list)              # 标签
    sessions: list = field(default_factory=list)          # ShowSession 列表
    extra: dict = field(default_factory=dict)             # 平台特有字段

    def to_dict(self) -> dict:
        d = {
            "平台": self.platform_cn or self.platform,
            "演出ID": self.event_id,
            "标题": self.title,
            "副标题": self.subtitle,
            "艺人": self.artists,
            "城市": self.city,
            "场馆": self.venue,
            "地址": self.address,
            "经纬度": (f"{self.venue_lat},{self.venue_lng}"
                       if self.venue_lat and self.venue_lng else ""),
            "时间": self.show_time,
            "演出时长": self.show_duration,
            "开售时间": self.sale_start,
            "票价": self.price_range,
            "状态": self.status,
            "想看": self.want_count,
            "分类": self.category,
            "主办方": self.merchant,
            "联系方式": self.contact,
            "退票政策": self.refund_policy,
            "演出介绍": self.description_text,
            "演出介绍HTML": self.description_html,
            "宣传图": self.images,
            "座位图": self.seat_images,
            "购票须知": self.notice_items,
            "常见问题": self.faqs,
            "标签": self.tags,
            "链接": self.url,
            "封面": self.cover,
            "获取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        d.update(self.extra)
        if self.sessions:
            d["场次明细"] = [
                {"场次": s.name, "开始时间": s.start_time,
                 "票档": [{"名称": t.name, "价格": t.price, "状态": t.status,
                           "说明": t.desc} for t in s.tickets]}
                for s in self.sessions]
        return {k: v for k, v in d.items() if v not in ("", [], None)}


# ==================== 大麦（mtop 两步令牌） ====================

_DAMAI_APPKEY = "12574478"


def _damai_mtop(api: str, version: str, payload: dict):
    """大麦 mtop H5 调用：第一步拿 _m_h5_tk 令牌，第二步带签名重试。"""
    s = httpx.Client(headers={"User-Agent": UA, "Referer": "https://m.damai.cn/",
                              "Origin": "https://m.damai.cn"},
                     timeout=25, follow_redirects=True)
    data_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    def _call():
        t = str(int(time.time() * 1000))
        sign = hashlib.md5(f"&{t}&{_DAMAI_APPKEY}&{data_str}".encode()).hexdigest()
        s.get(f"https://mtop.damai.cn/h5/{api}/{version}/",
              params={"jsv": "2.7.5", "appKey": _DAMAI_APPKEY, "t": t, "sign": sign,
                      "api": api, "v": version, "type": "json", "dataType": "json",
                      "data": data_str})
        tk = ""
        for c in s.cookies.jar:
            if c.name == "_m_h5_tk":
                tk = c.value.split("_")[0]
        if not tk:
            return None
        t2 = str(int(time.time() * 1000))
        sign2 = hashlib.md5(f"{tk}&{t2}&{_DAMAI_APPKEY}&{data_str}".encode()).hexdigest()
        r = s.get(f"https://mtop.damai.cn/h5/{api}/{version}/",
                  params={"jsv": "2.7.5", "appKey": _DAMAI_APPKEY, "t": t2, "sign": sign2,
                          "api": api, "v": version, "H5Request": "true",
                          "type": "json", "timeout": "10000", "dataType": "json",
                          "valueType": "string", "forceAntiCreep": "true",
                          "AntiCreep": "true", "data": data_str})
        try:
            return r.json()
        except Exception:
            return None

    d = _call()
    if d is None:
        # 令牌为空 → 用艺人页 API 热身（该接口必发令牌），再重试
        warm = {"args": json.dumps({"comboArtisteId": "0", "comboChannel": "2",
                                    "dmChannel": "damai@damaih5_h5",
                                    "comboCityId": "", "platform": "8"}),
                "patternVersion": "1.2", "patternName": "app_artiste_home",
                "platform": "8", "comboChannel": "2", "dmChannel": "damai@damaih5_h5"}
        warm_str = json.dumps(warm, separators=(",", ":"), ensure_ascii=False)
        t = str(int(time.time() * 1000))
        sign = hashlib.md5(f"&{t}&{_DAMAI_APPKEY}&{warm_str}".encode()).hexdigest()
        s.get("https://mtop.damai.cn/h5/mtop.damai.mec.aristotle.get/3.0/",
              params={"jsv": "2.7.5", "appKey": _DAMAI_APPKEY, "t": t, "sign": sign,
                      "api": "mtop.damai.mec.aristotle.get", "v": "3.0",
                      "type": "originaljson", "dataType": "json", "data": warm_str})
        d = _call()
    return d


def _walk_nodes(nodes: list):
    """递归展开大麦动态卡片 nodes 树。"""
    for n in nodes or []:
        if isinstance(n, dict):
            yield n
            yield from _walk_nodes(n.get("nodes") or [])


def parse_damai_artist(artist_id: str) -> tuple:
    """大麦艺人页 → (艺人名, [ShowEvent])。"""
    payload = {
        "args": json.dumps({"comboArtisteId": str(artist_id), "comboChannel": "2",
                            "dmChannel": "damai@damaih5_h5", "comboCityId": "852",
                            "platform": "8"}),
        "patternVersion": "1.2", "patternName": "app_artiste_home",
        "platform": "8", "comboChannel": "2", "dmChannel": "damai@damaih5_h5",
    }
    d = _damai_mtop("mtop.damai.mec.aristotle.get", "3.0", payload)
    if not d or not str((d.get("ret") or [""])[0]).startswith("SUCCESS"):
        err = (d or {}).get("ret") or ["大麦接口调用失败"]
        raise RuntimeError(f"大麦艺人页获取失败：{str(err[0])[:60]}")
    outer = d.get("data") or {}
    inner = outer.get("data") or {}
    artist_name = ((inner.get("top") or {}).get("artistName")) or ""
    events = []
    for node in _walk_nodes(outer.get("nodes") or []):
        data = node.get("data") or {}
        if data.get("type") != "PERFORMANCE":
            continue
        schema = data.get("schema") or ""
        item_id = ""
        m = re.search(r"itemId=(\d+)", schema)
        if m:
            item_id = m.group(1)
        events.append(ShowEvent(
            platform="damai", platform_cn="大麦",
            event_id=item_id,
            title=data.get("name") or "",
            city=data.get("cityName") or "",
            venue=data.get("venueName") or "",
            show_time=data.get("showTime") or "",
            price_range=data.get("priceStr") or "",
            status=data.get("buttonText") or "",
            url=schema or f"https://m.damai.cn/damai/detail/item.html?itemId={item_id}",
            cover=data.get("verticalPic") or "",
            extra={"热度": data.get("ipvuv") or ""} if data.get("ipvuv") else {},
        ))
    if not events:
        raise RuntimeError("该艺人暂无在售/即将开售的演出")
    return artist_name, events


def parse_damai_item(item_id: str) -> ShowEvent:
    """大麦演出详情 → ShowEvent（含场次 ID、限购、退票规则、艺人阵容、演出介绍HTML）。"""
    payload = {"itemId": str(item_id), "platform": "8", "comboChannel": "2",
               "dmChannel": "damai@damaih5_h5"}
    d = _damai_mtop("mtop.damai.item.detail.getdetail", "1.0", payload)
    if not d or not str((d.get("ret") or [""])[0]).startswith("SUCCESS"):
        err = (d or {}).get("ret") or ["大麦接口调用失败"]
        raise RuntimeError(f"大麦详情获取失败：{str(err[0])[:60]}")
    data = d.get("data") or {}
    item = data.get("item") or {}
    if not item:
        raise RuntimeError("大麦未返回演出数据")
    guide = data.get("guide") or {}
    venue = data.get("venue") or {}
    artists = "、".join(a.get("name", "") for a in (guide.get("artists") or [])
                        if a.get("name"))
    extra = {}
    if item.get("purchaseLimitation"):
        extra["限购"] = f"每单限购 {item['purchaseLimitation']} 张"
    real = (item.get("realName") or {})
    if real.get("type") == "1":
        extra["实名制"] = "需实名制购票入场"
    if item.get("showDuration"):
        extra["演出时长"] = item["showDuration"]
    want = (guide.get("want") or {})
    if want.get("wantNum"):
        extra["想看人数"] = want.get("wantNumStr") or want.get("wantNum")
    # ====== 演出介绍（HTML + 纯文本 + 图片列表） ======
    desc_html = ""
    desc_text = ""
    images = []
    desc = data.get("desc") or {}
    intro = desc.get("introduce") or ""
    if intro:
        desc_html = intro
        # 提取 <img src="...">
        images = re.findall(r'src="(https?://[^"]+)"', intro)
        # 纯文本（去标签）
        desc_text = re.sub(r"<[^>]+>", " ", intro)
        desc_text = re.sub(r"\s+", " ", desc_text).strip()
    # ====== 完整购票须知（按子标题归类） ======
    notice_items = []
    for t in ((data.get("serviceTips") or {}).get("importantNotes") or []):
        title = (t.get("title") or "").strip()
        content = (t.get("content") or "").strip()
        if title and content:
            notice_items.append({"标题": title, "内容": content[:300]})
    # ====== 座位图 ======
    seat_images = list(item.get("seatImages") or [])
    # ====== FAQ ======
    faqs = list(data.get("faqs") or [])
    # ====== 退票/换票政策 ======
    refund_policy = ""
    service_term = (data.get("serviceTips") or {}).get("serviceTerm") or []
    refund_tags = [t.get("tagName") for t in service_term if t.get("tagName")]
    if refund_tags:
        refund_policy = " / ".join(refund_tags)
    if not refund_policy:
        for n in notice_items:
            if "退" in n["标题"] or "换" in n["标题"]:
                refund_policy = n["内容"]
                break
    # ====== 场次（performLimits：场次ID + 单场限购） ======
    sessions = []
    for pl in (data.get("performLimits") or []):
        sessions.append(ShowSession(
            name=f"场次 {pl.get('performId', '')}",
            start_time=item.get("showTime") or "",
            tickets=[],
        ))
        if pl.get("singleLimitation"):
            extra.setdefault("限购", "")
            extra["限购"] = f"单场限购 {pl['singleLimitation']} 张"
    # ====== 副标题：常见为 "【城市】xxx" ======
    subtitle = item.get("itemNameDisplay") or ""
    if not subtitle and item.get("subTitle"):
        subtitle = item["subTitle"]
    return ShowEvent(
        platform="damai", platform_cn="大麦",
        event_id=str(item.get("itemId") or item_id),
        title=item.get("itemName") or "",
        subtitle=subtitle,
        artists=artists,
        city=item.get("cityName") or "",
        venue=venue.get("venueName") or "",
        address=venue.get("venueAddr") or "",
        venue_lat=str(venue.get("lat") or ""),
        venue_lng=str(venue.get("lng") or ""),
        show_time=item.get("showTime") or "",
        show_duration=item.get("showDuration") or "",
        sale_start=(data.get("saleInfo") or {}).get("saleStartTime") or "",
        price_range=(data.get("price") or {}).get("range") or "",
        status=((data.get("buyButton") or {}).get("text")) or "",
        url=f"https://m.damai.cn/damai/detail/item.html?itemId={item_id}",
        cover=((item.get("itemPics") or {}).get("itemPicList") or [{}])[0].get("picUrl", ""),
        want_count=want.get("wantNumStr") or "",
        description_html=desc_html,
        description_text=desc_text,
        images=images,
        seat_images=seat_images,
        notice_items=notice_items,
        faqs=faqs,
        refund_policy=refund_policy,
        sessions=sessions,
        extra=extra,
    )


# ==================== 秀动（HTML 解析） ====================

def _showstart_get(path: str) -> str:
    r = httpx.get(f"https://www.showstart.com{path}",
                  headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                  timeout=20, follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"秀动页面请求失败（HTTP {r.status_code}）")
    return r.text


def _strip_tags(s: str) -> str:
    from html import unescape
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or ""))).strip()


def parse_showstart_artist(artist_id: str) -> list:
    """秀动艺人页 → [ShowEvent]。"""
    html = _showstart_get(f"/artist/{artist_id}")
    events = []
    # 卡片结构：<a href="/event/306602"> <p class="name">..</p> <p class="performerName">..
    #          <p class="price"><em>¥180起</em></p> <p class="time">2026/09/25 17:20</p>
    #          <p class="addr">场馆名</p></a>
    card_re = re.compile(
        r'<a href="(/event/\d+)"[^>]*>.*?<p class="name"[^>]*>(.*?)</p>'
        r'.*?<p class="performerName"[^>]*>(.*?)</p>'
        r'.*?<p class="price"[^>]*>.*?<em[^>]*>(.*?)</em>'
        r'.*?<p class="time"[^>]*>(.*?)</p>'
        r'.*?<p class="addr"[^>]*>(.*?)</p>', re.S)
    for m in card_re.finditer(html):
        url, name, performers, price, stime, addr = m.groups()
        events.append(ShowEvent(
            platform="showstart", platform_cn="秀动",
            event_id=url.split("/")[-1],
            title=_strip_tags(name),
            artists=_strip_tags(performers).replace("艺人：", ""),
            show_time=_strip_tags(stime),
            price_range=_strip_tags(price),
            venue=_strip_tags(addr),
            url=f"https://www.showstart.com{url}",
        ))
    if not events:
        raise RuntimeError("秀动艺人页未解析到演出（可能无演出或页面结构变化）")
    return events


def parse_showstart_event(event_id: str) -> ShowEvent:
    """秀动事件页 → ShowEvent（含票种列表）。

    页面为 Nuxt SSR，关键数据在 window.__NUXT__ 的 detail 对象里
    （IIFE 参数化压缩，用正则取字段而非整体 JSON 解析）。
    """
    html = _showstart_get(f"/event/{event_id}")
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = _strip_tags(m.group(1)) if m else ""
    if not title:
        # NUXT 里 title 值可能含 \" 转义引号（如 “国风起时\"苏州），须一并匹配
        m = re.search(r'detail\s*:\s*\{[^{}]{0,40}title\s*:\s*"((?:[^"\\]|\\.){2,120})"', html)
        if m:
            title = m.group(1).replace('\\"', '"')
    if not title:
        title = f"秀动演出 {event_id}"
    # ====== 时间（多种兜底） ======
    stime = ""
    m = re.search(r"演出时间：([^<｜|]{4,40})", _strip_tags(html).replace("|", "｜")) or \
        re.search(r"showTime[\"']?\s*[:=]\s*[\"']([^\"']+)", html)
    if m:
        stime = m.group(1).strip()
    # ====== 演出介绍（HTML/纯文本） ======
    desc_html = ""
    desc_text = ""
    images = []
    # 匹配 detail.content 或 detail.desc
    for pat in (r'detail\s*:\s*\{[^{}]{0,40}content\s*:\s*"((?:[^"\\]|\\.){20,3000})"',
                r'detail\s*:\s*\{[^{}]{0,40}desc\s*:\s*"((?:[^"\\]|\\.){20,3000})"',
                r'description\s*:\s*"((?:[^"\\]|\\.){20,3000})"'):
        m = re.search(pat, html)
        if m:
            raw = m.group(1).replace('\\"', '"').replace("\\n", "\n")
            desc_html = raw
            images = re.findall(r'src="([^"]+)"', raw)
            desc_text = re.sub(r"<[^>]+>", " ", raw)
            desc_text = re.sub(r"\s+", " ", desc_text).strip()
            break
    # 兜底：直接从 HTML 取 <div class="content"> 段落
    if not desc_text:
        m = re.search(r'<div class="(?:detail|intro|content)[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if m:
            raw = m.group(1)
            images = re.findall(r'src="([^"]+)"', raw)
            desc_text = _strip_tags(raw)[:1500]
    # ====== 艺人 ======
    performers = []
    pm = re.search(r"performers\s*:\s*\[(.*?)\]", html, re.S)
    if pm:
        performers = re.findall(r'name\s*:\s*"([^"]{1,40})"', pm.group(1))
    if not performers:
        pm = re.search(r"<p class=\"performerName\"[^>]*>(.*?)</p>", html, re.S)
        if pm:
            performers = [_strip_tags(x) for x in re.findall(r">([^<>]{2,30})<", pm.group(1))]
    # ====== 场馆 + 地址 ======
    venue = addr = ""
    vm = re.search(r'href="/venue/\d+"[^>]*>([^<]{2,50})', html)
    if vm:
        venue = vm.group(1).strip()
    am = re.search(r"地址：([^<]{6,80})", html)
    if am:
        addr = am.group(1).strip()
    # ====== 票种 ======
    tiers = []
    for tm in re.finditer(
            r"[¥￥]\s*(\d+(?:\.\d+)?)\s*([^<｜]{0,40})", html):
        price = tm.group(1)
        desc = _strip_tags(tm.group(2))[:40]
        tiers.append(TicketTier(name=desc or price, price=f"¥{price}", desc=desc))
    # 去重（同名同价）
    seen = set()
    uniq_tiers = []
    for t in tiers:
        key = (t.price, t.desc)
        if key not in seen:
            seen.add(key)
            uniq_tiers.append(t)
    sessions = [ShowSession(name="售票票种", tickets=uniq_tiers)] if uniq_tiers else []
    # ====== 演出时长 ======
    duration = ""
    dm = re.search(r"(?:演出时长|时长)[\s:：]*([^<｜|]{2,30})", _strip_tags(html))
    if dm:
        duration = dm.group(1).strip()
    # ====== 开售时间 ======
    sale_start = ""
    sm = re.search(r"(?:开售时间|开售)[\s:：]*([^<｜|]{4,40})", _strip_tags(html))
    if sm:
        sale_start = sm.group(1).strip()
    # ====== 票务政策 ======
    refund_policy = ""
    rm = re.search(r"退票政策[\s:：]*([^<｜|]{6,80})", _strip_tags(html))
    if rm:
        refund_policy = rm.group(1).strip()
    # ====== 主办方 / 项目方 ======
    merchant = ""
    mm = re.search(r"(?:主办方|项目方|承办方)[\s:：]*([^\s<｜|]{2,40})", _strip_tags(html))
    if mm:
        merchant = mm.group(1).strip()
    return ShowEvent(
        platform="showstart", platform_cn="秀动",
        event_id=str(event_id),
        title=title,
        artists="、".join(x for x in performers if x and x != "/")[:200],
        venue=venue,
        address=addr,
        show_time=stime,
        show_duration=duration,
        sale_start=sale_start,
        price_range=f"¥{uniq_tiers[0].price[1:]}" if uniq_tiers else "",
        url=f"https://www.showstart.com/event/{event_id}",
        description_html=desc_html,
        description_text=desc_text,
        images=images,
        refund_policy=refund_policy,
        merchant=merchant,
        sessions=sessions,
    )


# ==================== B站会员购（公开 API） ====================

def _bili_show_headers() -> dict:
    return {"User-Agent": UA, "Referer": "https://show.bilibili.com/"}


def search_bilibili_show(keyword: str) -> list:
    """B站会员购关键词搜索 → [ShowEvent]。
    搜索接口要求 buvid cookie（游客可），先访问首页种 cookie 再搜；
    pagesize>20 接口返回空，固定 16。
    """
    s = httpx.Client(headers=_bili_show_headers(), timeout=20,
                     follow_redirects=True)
    try:
        s.get("https://www.bilibili.com/")  # 种 buvid3
    except Exception:
        pass
    r = s.get("https://show.bilibili.com/api/ticket/search/list",
              params={"version": "134", "keyword": keyword, "pagesize": 16,
                      "page": 1, "platform": "web"})
    d = r.json()
    if d.get("errno") != 0:
        raise RuntimeError(f"B站会员购搜索失败：{d.get('msg')}")
    events = []
    for it in (d.get("data") or {}).get("result") or []:
        pid = it.get("id") or it.get("ticket_id") or ""
        stime = it.get("start_time") or ""
        if isinstance(stime, (int, float)):
            stime = _bili_time(stime)
        events.append(ShowEvent(
            platform="bilibili_show", platform_cn="B站会员购",
            event_id=str(pid),
            title=it.get("project_name") or it.get("title") or "",
            city=it.get("city") or "",
            venue=it.get("venue_name") or "",
            show_time=stime,
            price_range=_bili_price(it.get("price_low"), it.get("price_high")),
            status=it.get("sale_flag") or "",
            url=it.get("url") or f"https://show.bilibili.com/platform/detail.html?id={pid}",
            cover=it.get("cover") or "",
            want_count=str(it.get("want_num") or "") if it.get("want_num") else "",
        ))
    if not events:
        raise RuntimeError(f"「{keyword}」无搜索结果")
    return events


def parse_bilibili_project(project_id: str) -> ShowEvent:
    """B站会员购项目详情 → ShowEvent（含场次+票档+演出介绍+公告）。"""
    r = httpx.get("https://show.bilibili.com/api/ticket/project/get",
                  params={"id": project_id, "project_id": project_id},
                  headers=_bili_show_headers(), timeout=20)
    d = r.json()
    if d.get("errno") != 0:
        raise RuntimeError(f"B站会员购详情获取失败：{d.get('msg')}")
    data = d.get("data") or {}

    # ====== 演出介绍（performance_desc，可能含 HTML） ======
    raw_desc = data.get("performance_desc") or data.get("description") or ""
    if isinstance(raw_desc, dict):
        # 部分项目把 desc 包在 dict 里（{content: "..."} 或 {text: "..."}）
        raw_desc = (raw_desc.get("content") or raw_desc.get("text") or
                    raw_desc.get("html") or raw_desc.get("desc") or "")
    desc_html = raw_desc if isinstance(raw_desc, str) else str(raw_desc)
    desc_text = re.sub(r"<[^>]+>", " ", desc_html)
    desc_text = re.sub(r"\s+", " ", desc_text).strip()
    images = re.findall(r'src="([^"]+)"', desc_html) if desc_html else []

    # ====== 公告 / 通知 / 免责声明 ======
    notice_items = []
    for key, label in (("notice", "购票须知"), ("bulletin", "项目公告"),
                        ("disclaimer", "免责声明"), ("refund_desc", "退款说明"),
                        ("refund_desc_detail", "退款详情"),
                        ("ticket_desc", "票务说明"),
                        ("contact_notice", "联系须知")):
        v = data.get(key)
        if not v:
            continue
        if isinstance(v, list):
            v = "\n".join(str(x) for x in v if x)
        if isinstance(v, dict):
            v = (v.get("content") or v.get("text") or v.get("desc") or "")
        v = str(v).strip()
        if v:
            notice_items.append({"标题": label, "内容": v[:500]})

    # ====== 退票政策 ======
    refund_policy = ""
    is_refund = data.get("is_refund")
    all_refund = data.get("all_refund")
    if is_refund == 10:
        refund_policy = "支持有条件退款（详见退款说明）"
    elif is_refund == 0 or all_refund == 1:
        refund_policy = "不支持退款"
    elif is_refund == 1:
        refund_policy = "支持退款"
    rdd_raw = data.get("refund_desc_detail") or data.get("refund_desc") or ""
    if isinstance(rdd_raw, list):
        rdd = "\n".join(str(x) for x in rdd_raw if x)
    elif isinstance(rdd_raw, dict):
        rdd = (rdd_raw.get("content") or rdd_raw.get("text") or
               rdd_raw.get("desc") or "")
    else:
        rdd = str(rdd_raw) if rdd_raw else ""
    if rdd and rdd not in refund_policy:
        refund_policy = (refund_policy + "；" + rdd[:200]).strip("；")

    # ====== 座位图 / 演出图 ======
    seat_images = []
    pi = data.get("performance_image") or {}
    if isinstance(pi, dict):
        for k in ("url", "image", "img"):
            v = pi.get(k)
            if v:
                if isinstance(v, str):
                    seat_images.append(("https:" + v) if v.startswith("//") else v)
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, str):
                            seat_images.append(("https:" + x) if x.startswith("//") else x)
                break
    elif isinstance(pi, str):
        seat_images.append(("https:" + pi) if pi.startswith("//") else pi)
    cover = data.get("cover") or ""
    if cover.startswith("//"):
        cover = "https:" + cover
    banner = data.get("banner") or ""
    if banner.startswith("//"):
        banner = "https:" + banner
    if banner and banner not in images:
        images.insert(0, banner)

    # ====== 实名制 ======
    id_bind = data.get("id_bind") or {}
    real_name = ""
    if isinstance(id_bind, dict):
        if id_bind.get("open") == 1 or id_bind.get("type"):
            real_name = "需实名购票"
    if not real_name:
        bi = data.get("buyer_info") or {}
        if isinstance(bi, dict) and bi.get("type") == 1:
            real_name = "需实名购票"

    # ====== 限购 ======
    limit = ""
    for sk in (data.get("screen_list") or []):
        for tk in (sk.get("ticket_list") or []):
            sl = tk.get("static_limit") or {}
            if sl.get("num"):
                limit = f"单笔限购 {sl['num']} 张"
                break
        if limit:
            break

    # ====== 主办方 ======
    merchant = ""
    m = data.get("merchant") or {}
    if isinstance(m, dict):
        merchant = m.get("name") or m.get("account_name") or m.get("account") or ""
    elif isinstance(m, str):
        merchant = m

    # ====== 标签 ======
    tags = []
    for t in (data.get("tags") or []):
        if isinstance(t, dict):
            n = t.get("name") or t.get("title")
        else:
            n = str(t)
        if n:
            tags.append(n)
    sale_tags = []
    for t in (data.get("sale_tags") or []):
        if isinstance(t, dict):
            n = t.get("name") or t.get("title") or t.get("text")
        else:
            n = str(t)
        if n:
            sale_tags.append(n)

    # ====== 场次+票档 ======
    sessions = []
    for sk in (data.get("screen_list") or []):
        tiers = []
        for tk in (sk.get("ticket_list") or []):
            sale = tk.get("sale_flag") or {}
            status = sale.get("display_name") if isinstance(sale, dict) else str(sale)
            tiers.append(TicketTier(
                name=tk.get("desc") or "",
                price=_bili_price(tk.get("price")),
                status=status or ("可购" if tk.get("clickable") else "不可购"),
                desc=(f"id={tk.get('id','')} saleStart={tk.get('sale_start','')} saleEnd={tk.get('sale_end','')} "
                      f"limit={((tk.get('static_limit') or {}).get('num') or '-')}")[:200],
            ))
        sessions.append(ShowSession(
            name=sk.get("name") or "",
            start_time=_bili_time(sk.get("start_time")),
            tickets=tiers,
        ))

    # ====== 开售时间 ======
    sale_start = _bili_time(data.get("sale_begin") or data.get("sale_start") or
                            data.get("sale_begin_time"))

    extra = {}
    if limit:
        extra["限购"] = limit
    if real_name:
        extra["实名制"] = real_name
    if sale_tags:
        extra["销售标签"] = " / ".join(sale_tags[:8])
    pi = data.get("project_label") or {}
    if isinstance(pi, dict) and pi.get("title"):
        extra["项目标签"] = pi["title"]
    if data.get("keywords"):
        extra["关键词"] = data["keywords"]

    # 想去人数
    wish = data.get("wish_info") or {}
    want_count = ""
    if isinstance(wish, dict):
        w = wish.get("wish_count") or wish.get("wishCount")
        if w:
            want_count = str(w)

    return ShowEvent(
        platform="bilibili_show", platform_cn="B站会员购",
        event_id=str(data.get("id") or project_id),
        title=data.get("name") or data.get("title") or "",
        subtitle=data.get("name") or "",
        artists=_strip_tags(str(data.get("performers") or ""))[:200]
        if data.get("performers") else "",
        city=data.get("city_name") or "",
        venue=data.get("venue_name") or "",
        address=data.get("venue_address") or "",
        show_time=f"{_bili_time(data.get('start_time'))} ~ {_bili_time(data.get('end_time'))}"
        if data.get("end_time") else _bili_time(data.get("start_time")),
        show_duration=str(data.get("duration") or ""),
        sale_start=sale_start,
        price_range=_bili_price(data.get("price_low"), data.get("price_high")),
        status=data.get("sale_flag") or "",
        url=f"https://show.bilibili.com/platform/detail.html?id={project_id}",
        cover=cover,
        want_count=want_count,
        description_html=desc_html,
        description_text=desc_text,
        images=images,
        seat_images=seat_images,
        notice_items=notice_items,
        refund_policy=refund_policy,
        merchant=merchant,
        tags=tags,
        sessions=sessions,
        extra=extra,
    )


def _bili_price(low, high=None) -> str:
    """分 → 元。"""
    try:
        lo = int(low or 0)
    except (ValueError, TypeError):
        return ""
    if lo <= 0:
        return ""
    lo_s = f"{lo / 100:g}"
    if high in (None, "", low):
        return f"¥{lo_s}"
    try:
        hi = int(high)
        return f"¥{lo_s}-{hi / 100:g}"
    except (ValueError, TypeError):
        return f"¥{lo_s}"


def _bili_time(ts) -> str:
    try:
        ts = int(ts)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return ""


# ==================== URL 路由 ====================

def detect_ticket_platform(url: str) -> tuple:
    """识别票务链接 → (platform, id)。
    damai: artist.html?artistId= / detail/item.html?itemId=
    showstart: /artist/8982311 / /event/306602
    bilibili_show: show.bilibili.com detail?id= / search.html?searchValue=
    """
    url = (url or "").strip()
    m = re.search(r"damai\.cn/shows/artist\.html\?artistId=(\d+)", url)
    if m:
        return "damai_artist", m.group(1)
    m = re.search(r"damai\.cn/damai/detail/item\.html\?itemId=(\d+)", url)
    if m:
        return "damai_item", m.group(1)
    m = re.search(r"showstart\.com/artist/(\d+)", url)
    if m:
        return "showstart_artist", m.group(1)
    m = re.search(r"showstart\.com/event/(\d+)", url)
    if m:
        return "showstart_event", m.group(1)
    m = re.search(r"show\.bilibili\.com/platform/detail\.html\?id=(\d+)", url)
    if m:
        return "bili_project", m.group(1)
    m = re.search(r"show\.bilibili\.com/platform/detail\.html\?project_id=(\d+)", url)
    if m:
        return "bili_project", m.group(1)
    m = re.search(r"show\.bilibili\.com/platform/search\.html\?searchValue=([^&#]+)", url)
    if m:
        from urllib.parse import unquote
        return "bili_search", unquote(m.group(1))
    if "damai.cn" in url or "showstart.com" in url or "show.bilibili.com" in url:
        return "unknown_url", ""
    return None, None


def parse_ticket_url(url: str, keyword: str = "") -> list:
    """统一入口：URL 或关键词 → [ShowEvent]。
    无 URL 时用关键词走 B站会员购搜索。
    """
    kind, pid = detect_ticket_platform(url)
    if kind == "damai_artist":
        _name, events = parse_damai_artist(pid)
        return events
    if kind == "damai_item":
        return [parse_damai_item(pid)]
    if kind == "showstart_artist":
        return parse_showstart_artist(pid)
    if kind == "showstart_event":
        return [parse_showstart_event(pid)]
    if kind == "bili_project":
        return [parse_bilibili_project(pid)]
    if kind == "bili_search":
        return search_bilibili_show(pid)
    if kind == "unknown_url":
        raise ValueError("无法识别的票务链接（支持大麦艺人/演出、秀动艺人/事件、B站会员购搜索/项目）")
    # 纯关键词 → B站会员购搜索
    if keyword.strip():
        return search_bilibili_show(keyword.strip())
    raise ValueError("请输入票务链接或搜索关键词")


# ==================== 跨平台关键词搜索（统一入口） ====================

# 用户常用艺人/活动关键词 → 大麦艺人 ID 映射（手动维护，非必需）。
# 未命中映射的关键词走 _damai_search_keyword 游客搜索（Playwright）；
# 命中映射的关键词走艺人详情页通道（数据更全：含场次+票档）。
_DAMAI_ARTIST_ID_HINTS = {
    "封茗囧菌": "33879518699",
    "封茗囧菌咨讯站": "33879518699",
    # 后面追加同类歌手的映射...
}

# 已知常用艺人对应秀动艺人 ID（同样手动维护）。
_SHOWSTART_ARTIST_ID_HINTS = {
    # "封茗囧菌": "8982311",
}


def _damai_search_keyword(keyword: str, log=None) -> list:
    """大麦游客关键词搜索 → [ShowEvent]（轻量列表：标题/城市/场馆/票价/封面）。

    mtop.damai.wireless.search.search 游客 HTTP 直调会被 RGV587 滑块风控拦
    （带淘宝 Cookie 也无效——环境风控而非登录风控），必须真实浏览器。
    直达 https://m.damai.cn/shows/search.html?keyword=...，页面加载即自动
    触发搜索；拦截 data 含 keyword 的 search.search 响应 → projectInfo。
    """
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse, parse_qs, quote

    keyword = (keyword or "").strip()
    if not keyword:
        return []

    def _run_once() -> tuple:
        """单次浏览器搜索 → (hits, blocked)。blocked=True 表示被 RGV587 风控拦。"""
        hits: list = []
        state = {"blocked": False}

        def _events_from(data: dict) -> list:
            evts = []
            for pr in (data.get("projectInfo") or []):
                if not isinstance(pr, dict):
                    continue
                pid = str(pr.get("id") or "")
                if not pid:
                    continue
                ss = pr.get("showStatus")
                evts.append(ShowEvent(
                    platform="damai", platform_cn="大麦",
                    event_id=pid,
                    title=str(pr.get("name") or ""),
                    artists=str(pr.get("actores") or "").replace(";", " / "),
                    city=str(pr.get("cityName") or pr.get("venueCity") or ""),
                    venue=str(pr.get("venueName") or ""),
                    show_time=str(pr.get("showTime") or ""),
                    price_range=str(pr.get("priceStr") or
                                    pr.get("formattedPriceStr") or ""),
                    status=str(ss.get("desc") or "") if isinstance(ss, dict) else "",
                    category=str(pr.get("categoryName") or ""),
                    cover=str(pr.get("verticalPic") or ""),
                    url=f"https://m.damai.cn/damai/detail/item.html?projectId={pid}",
                ))
            return evts

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled", "--no-sandbox"])
            try:
                ctx = browser.new_context(
                    user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                                "Mobile/15E148 Safari/604.1"),
                    viewport={"width": 414, "height": 896}, locale="zh-CN",
                    is_mobile=True, has_touch=True)
                page = ctx.new_page()

                def on_response(resp):
                    u = resp.url
                    if "mtop.damai.wireless.search.search" not in u:
                        return
                    try:
                        qs = parse_qs(urlparse(u).query)
                        data_param = (qs.get("data") or [""])[0]
                        if keyword not in data_param:
                            return  # 热搜榜等无关键词调用
                        body = resp.json()
                        ret = str(body.get("ret"))
                        if "RGV587" in ret or "FAIL_SYS_USER_VALIDATE" in ret:
                            state["blocked"] = True
                            return
                        if "SUCCESS" in ret:
                            hits.extend(_events_from(body.get("data") or {}))
                    except Exception:
                        pass

                page.on("response", on_response)
                # 直达搜索页：URL 带 keyword，页面加载即自动触发搜索
                # （fill+回车在 SPA 受控输入框上不触发状态更新，实测无效）
                page.goto(
                    f"https://m.damai.cn/shows/search.html?keyword={quote(keyword)}",
                    wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_selector("input", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(6000)
                page.mouse.wheel(0, 1000)   # 滚一屏触发懒加载
                page.wait_for_timeout(2000)
            finally:
                browser.close()
        return hits, state["blocked"]

    # RGV587 滑块风控有波动性（连续高频请求会触发），被拦时隔几秒重试一次
    for attempt in range(2):
        try:
            hits, blocked = _run_once()
        except Exception as e:
            msg = str(e)
            if "ERR_CONNECTION" in msg or "ERR_NAME" in msg or "Timeout" in msg:
                raise RuntimeError("大麦连接失败（可能触发风控限流，请稍后再试）")
            raise
        if hits or not blocked:
            return hits
        if attempt == 0:
            time.sleep(5)
    return hits


def _showstart_search_keyword(keyword: str, log=None) -> list:
    """秀动游客关键词搜索 → [ShowEvent]（HTTP SSR 解析，无需浏览器）。

    https://www.showstart.com/event/list?keyword=... 游客可用（Nuxt SSR）。
    卡片结构：<a href="/event/{id}"> <div class="title"> <div class="artist">
    艺人：... <div class="price"><span>¥180起 <div class="time">时间：...
    <div class="addr">[苏州]场馆名
    """
    from urllib.parse import quote

    keyword = (keyword or "").strip()
    if not keyword:
        return []
    html = _showstart_get(f"/event/list?keyword={quote(keyword)}")
    card_re = re.compile(
        r'<a href="(/event/\d+)"[^>]*>.*?<div class="title"[^>]*>(.*?)</div>'
        r'.*?<div class="artist"[^>]*>(.*?)</div>'
        r'.*?<div class="price"[^>]*>.*?<span[^>]*>(.*?)</span>'
        r'.*?<div class="time"[^>]*>(.*?)</div>'
        r'.*?<div class="addr"[^>]*>(.*?)</div>', re.S)
    events = []
    for m in card_re.finditer(html):
        url, name, performers, price, stime, addr = m.groups()
        addr_s = _strip_tags(addr)
        vm = re.match(r"\[([^\]]+)\]\s*(.*)", addr_s)
        city = vm.group(1) if vm else ""
        venue = vm.group(2).strip() if vm else addr_s
        events.append(ShowEvent(
            platform="showstart", platform_cn="秀动",
            event_id=url.split("/")[-1],
            title=_strip_tags(name),
            artists=_strip_tags(performers).replace("艺人：", ""),
            city=city,
            venue=venue,
            show_time=_strip_tags(stime).replace("时间：", ""),
            price_range=_strip_tags(price),
            url=f"https://www.showstart.com{url}",
        ))
    return events


def search_tickets_by_keyword(keyword: str, *,
                              platform: str = "all",
                              damai_artist_id: str = "",
                              showstart_artist_id: str = "") -> tuple:
    """跨平台统一关键词搜索 → ([ShowEvent], {platform: error_or_count})。

    platform: 'all' | 'bilibili' | 'damai' | 'showstart' —— 多平台时分别走各平台通道

    平台特性：
    - B站会员购：纯 API 关键词搜索，**无需登录**，覆盖率最高
    - 大麦：游客 Playwright 搜索（_damai_search_keyword）；命中艺人 ID 映射表或
            调用方显式传 damai_artist_id 时走艺人详情页（数据更全）
    - 秀动：HTTP SSR 解析 event/list?keyword=...（游客可用）；显式
            showstart_artist_id 时走艺人页

    失败时不抛错，会把错误信息塞到第二项 dict 里，方便 UI 多平台结果汇总展示。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return [], {"_error": "请输入关键词"}

    results: list = []
    diag: dict = {}

    # 1. B 站会员购（主路径，API 关键词搜索）
    if platform in ("all", "bilibili"):
        try:
            bili = search_bilibili_show(keyword)
            results.extend(bili)
            diag["bilibili"] = len(bili)
        except Exception as e:
            diag["bilibili"] = f"❌ {str(e)[:80]}"

    # 2. 大麦：显式艺人 ID / 命中映射表 → 艺人详情通道（数据全：含场次+票档）；
    #    否则游客 Playwright 关键词搜索（轻量列表）
    if platform in ("all", "damai"):
        did = damai_artist_id or _DAMAI_ARTIST_ID_HINTS.get(keyword) or ""
        if did:
            try:
                _name, evts = parse_damai_artist(did)
                results.extend(evts)
                diag["damai"] = len(evts)
            except Exception as e:
                diag["damai"] = f"❌ {str(e)[:80]}"
        else:
            try:
                evts = _damai_search_keyword(keyword)
                results.extend(evts)
                diag["damai"] = len(evts) if evts else "⚠️ 大麦无结果"
            except Exception as e:
                diag["damai"] = f"❌ {str(e)[:80]}"

    # 3. 秀动：显式艺人 ID / 命中映射表 → 艺人页通道；否则 HTTP SSR 关键词搜索
    if platform in ("all", "showstart"):
        sid = showstart_artist_id or _SHOWSTART_ARTIST_ID_HINTS.get(keyword) or ""
        if sid:
            try:
                events_ss = parse_showstart_artist(sid)
                results.extend(events_ss)
                diag["showstart"] = len(events_ss)
            except Exception as e:
                diag["showstart"] = f"❌ {str(e)[:80]}"
        else:
            try:
                events_ss = _showstart_search_keyword(keyword)
                results.extend(events_ss)
                diag["showstart"] = (len(events_ss) if events_ss
                                     else "⚠️ 秀动无结果")
            except Exception as e:
                diag["showstart"] = f"❌ {str(e)[:80]}"

    # 去重（按 platform + event_id + title 前缀）
    seen = set()
    unique = []
    for ev in results:
        # 构造唯一 key：event_id 优先，否则 title + show_time
        key = (ev.platform, ev.event_id or "") or (
            (ev.platform, ev.title[:30], ev.show_time or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    return unique, diag
