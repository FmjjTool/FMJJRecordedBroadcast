"""B站小店解析：分享短链 / UP主 UID → 小店信息 + 商品列表（游客可用）。

链路（2026-11 实测）：
  b23.tv 短链 302 → mall.bilibili.com/neul-next/detailshop/index.html
    ?page=detailshop_detail&smallShopMid=<UP主 mid>&msource=cps_showcase_<mid>
  小店信息：POST https://mall.bilibili.com/community-hub/small_shop/home/info
            body {"smallShopMid": <mid>}
  商品列表：POST https://mall.bilibili.com/community-hub/small_shop/feed/item
            body {"upMid": "<mid>", "sortType": "total_rank|sale|price|pub_time",
                  "searchAfter": <游标>, "pageSize": 20, ...}

两个坑（已在实现里处理）：
  1. feed 接口必须带访客 buvid3/buvid4（/x/frontend/finger/spi 获取），
     否则 200 但 data.data 为空；
  2. sortType=total_rank 时 searchAfter 必须传数字 0，传空串会报
     code=81103001 server runtime error。

能拿到：小店名 / 头像 / 粉丝数 / 商品数 + 每件商品的 ID / 标题 / 价格 /
封面 / 跳转链 / 来源类型。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")

HOME_API = "https://mall.bilibili.com/community-hub/small_shop/home/info"
FEED_API = "https://mall.bilibili.com/community-hub/small_shop/feed/item"
SPI_API = "https://api.bilibili.com/x/frontend/finger/spi"

SORT_LABELS = {
    "total_rank": "综合",
    "sale": "销量",
    "price": "价格",
    "pub_time": "新品",
}

_SHORT_RE = re.compile(r"https?://b23\.tv/[\w\-]+", re.I)
_MALL_RE = re.compile(r"https?://mall\.bilibili\.com/\S+", re.I)
_MID_IN_URL_RE = re.compile(r"[?&]smallShopMid=(\d+)", re.I)
_SPACE_RE = re.compile(r"space\.bilibili\.com/(\d+)", re.I)
_RAW_MID_RE = re.compile(r"^\s*(\d{2,})\s*$")


@dataclass
class BiliShopItem:
    item_id: str = ""
    title: str = ""
    price: str = ""
    price_symbol: str = "¥"
    cover: str = ""
    card_url: str = ""
    source: str = ""
    sub_title: str = ""
    content_type: int = 0
    sell_out: bool = False

    @property
    def price_text(self) -> str:
        if not self.price:
            return "-"
        return f"{self.price_symbol or '¥'}{self.price}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price_text"] = self.price_text
        return d


@dataclass
class BiliShop:
    mid: str = ""
    name: str = ""
    avatar: str = ""
    fans: str = ""
    item_count_text: str = ""
    space_url: str = ""
    share_url: str = ""
    parsed_at: str = ""
    sort: str = "total_rank"
    items: list[BiliShopItem] = field(default_factory=list)
    local_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "shop": {
                "mid": self.mid,
                "name": self.name,
                "avatar": self.avatar,
                "fans": self.fans,
                "item_count_text": self.item_count_text,
                "space_url": self.space_url,
                "share_url": self.share_url,
                "parsed_at": self.parsed_at,
                "sort": self.sort,
                "sort_label": SORT_LABELS.get(self.sort, self.sort),
            },
            "items": [i.to_dict() for i in self.items],
        }


# ==================== 链接 / UID 解析 ====================

def extract_share_url(text: str) -> str:
    """从分享文案或纯链接里抠出 b23 / mall 链接。"""
    m = _SHORT_RE.search(text or "")
    if m:
        return m.group(0)
    m = _MALL_RE.search(text or "")
    if m:
        return m.group(0).rstrip("，。!！;；")
    return ""


def resolve_mid(text: str, timeout: float = 20.0) -> tuple[str, str]:
    """输入 → (smallShopMid, 原始分享链)。

    支持：b23 短链、mall.bilibili.com 商品页/小店页、space 主页、纯数字 mid。
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("请输入 B站小店链接或 UP主 UID")

    m = _MID_IN_URL_RE.search(raw)
    if m:
        return m.group(1), extract_share_url(raw) or raw

    share_url = extract_share_url(raw)
    if share_url:
        with httpx.Client(follow_redirects=False, timeout=timeout) as cli:
            resp = cli.get(share_url, headers={"User-Agent": UA_MOBILE})
            loc = resp.headers.get("location") or ""
            if not loc:
                raise ValueError(f"短链没有跳转（HTTP {resp.status_code}），可能已失效")
            m = _MID_IN_URL_RE.search(loc)
            if not m:
                raise ValueError("跳转后不是 B站小店页面（没有 smallShopMid）")
            return m.group(1), share_url

    m = _SPACE_RE.search(raw)
    if m:
        return m.group(1), raw

    m = _RAW_MID_RE.search(raw)
    if m:
        return m.group(1), raw

    raise ValueError("没识别到 B站小店链接或 UP主 UID")


# ==================== 会话 ====================

def _cookie(platform: str) -> str:
    """登录 Cookie；总开关关闭时返回空（小店不需要登录，只需访客 buvid）。"""
    try:
        from auth import get_cookie, is_cookie_enabled, load_cookies
        if not is_cookie_enabled():
            return ""
        return get_cookie(platform) or load_cookies().get(platform, "")
    except Exception:
        return ""


def make_session() -> httpx.Client:
    """带 B站访客 buvid 的会话（不登录也能拿小店数据）。"""
    s = httpx.Client(timeout=20.0, follow_redirects=True, headers={
        "content-type": "application/json",
        "scene": "decorate",
        "origin": "https://mall.bilibili.com",
        "referer": "https://mall.bilibili.com/",
        "user-agent": UA_MOBILE,
    })
    stored = _cookie("bilibili")
    if stored:
        s.headers["cookie"] = stored
    if "buvid3=" not in (s.headers.get("cookie") or ""):
        try:
            # 用独立请求取访客票据：在 with 之前先打开 client 会导致
            # 「Cannot open a client instance more than once」
            d = httpx.get(SPI_API, timeout=15.0,
                          headers={"user-agent": UA_MOBILE}).json().get("data") or {}
            b3, b4 = d.get("b_3"), d.get("b_4")
            if b3 and b4:
                part = f"buvid3={b3}; buvid4={b4}"
                old = s.headers.get("cookie") or ""
                s.headers["cookie"] = f"{old}; {part}" if old else part
        except Exception:
            pass
    return s


def _post_json(session: httpx.Client, url: str, payload: dict) -> dict:
    r = session.post(url, json=payload)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise ValueError(d.get("message") or f"接口错误 code={d.get('code')}")
    return d.get("data") or {}


# ==================== 抓取 ====================

def fetch_shop(text: str, sort: str = "total_rank", max_pages: int = 20,
               progress=None) -> BiliShop:
    """输入链接/UID → BiliShop（含全部商品）。"""
    mid, share_url = resolve_mid(text)
    sort = sort if sort in SORT_LABELS else "total_rank"
    if progress:
        progress(f"小店 UID：{mid}")

    with make_session() as s:
        home = _post_json(s, HOME_API, {"smallShopMid": int(mid)})
        shop = BiliShop(
            mid=str(home.get("smallShopMid") or mid),
            name=str(home.get("smallShopName") or ""),
            avatar=str(home.get("upPortrait") or ""),
            fans=str(home.get("fans") or ""),
            item_count_text=str(home.get("smallShopItems") or ""),
            space_url=str(home.get("upJumpLink") or f"https://space.bilibili.com/{mid}"),
            share_url=share_url,
            parsed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            sort=sort,
        )

        cursor = 0
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            if progress:
                progress(f"读取商品第 {page} 页...")
            data = _post_json(s, FEED_API, {
                "activityFilter": False,
                "sortType": sort,
                "keyword": "",
                "sortOrder": "",
                "searchAfter": cursor,
                "pageSize": 20,
                "upMid": str(mid),
                "jxsFilter": False,
                "drawGoodsMachineFilter": False,
                "clickHideButton": False,
            })
            batch = data.get("data") or []
            for raw in batch:
                item = _parse_item(raw)
                if item.item_id and item.item_id not in seen:
                    seen.add(item.item_id)
                    shop.items.append(item)
            nxt = data.get("nextSearchAfter")
            if not data.get("haveNextPage") or nxt in (None, cursor) or not batch:
                break
            cursor = nxt

    if not shop.name and not shop.items:
        raise ValueError("没有解析到小店数据（链接可能不是小店页）")
    return shop


def _parse_item(raw: dict) -> BiliShopItem:
    np = raw.get("netPrice") or {}
    cover = (raw.get("cover") or {}).get("url") or ""
    return BiliShopItem(
        item_id=str(raw.get("contentId") or raw.get("itemCode") or ""),
        title=str(raw.get("title") or ""),
        price=str(np.get("netPrice") or raw.get("lrpriceStr") or "").strip(),
        price_symbol=str(np.get("priceSymbol") or "¥"),
        cover=str(cover),
        card_url=str(raw.get("cardUrl") or raw.get("outSchemaUrl") or ""),
        source=str(raw.get("itemSourceName") or ""),
        sub_title=str(raw.get("subTitle") or ""),
        content_type=int(raw.get("contentType") or 0),
        sell_out=bool(raw.get("sellOut")),
    )


# ==================== 本地落盘 ====================

def _safe_name(name: str, max_len: int = 50) -> str:
    from toolbox.utils import sanitize_filename
    return sanitize_filename(str(name or ""), max_len, "未命名", collapse_ws=False)


def shop_dir() -> Path:
    """小店数据根目录：download/B站小店/。"""
    from toolbox.config_manager import get_export_subdir
    return get_export_subdir("bili_shop")


def shop_subdir(shop: BiliShop, create: bool = True) -> Path:
    d = shop_dir() / f"{_safe_name(shop.name or shop.mid)}_{shop.mid or 'unknown'}"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def save_shop_json(shop: BiliShop, dest_dir: Path | None = None) -> Path:
    d = dest_dir or shop_subdir(shop)
    d.mkdir(parents=True, exist_ok=True)
    shop.local_dir = str(d)
    path = d / "小店信息.json"
    path.write_text(json.dumps(shop.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_items_xlsx(shop: BiliShop, path: Path | None = None) -> Path:
    """导出商品列表 Excel（B站小店）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = shop_subdir(shop) / f"商品列表_{ts}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "商品列表"
    headers = ["序号", "商品标题", "价格", "商品ID", "来源", "封面链接",
               "商品链接", "小店名", "店铺UID", "粉丝数", "采集时间"]
    ws.append(headers)
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="FB7299")
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = [6, 46, 10, 16, 14, 50, 50, 24, 14, 12, 18]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for idx, it in enumerate(shop.items, 1):
        ws.append([idx, it.title, it.price_text, it.item_id, it.source,
                   it.cover, it.card_url, shop.name, shop.mid, shop.fans,
                   shop.parsed_at])
    wb.save(str(path))
    return path
