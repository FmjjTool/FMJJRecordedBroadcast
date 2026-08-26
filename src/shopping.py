"""购物平台解析：淘宝店铺 + 商品详情。

淘宝对游客收紧店铺商品列表（search.htm 强制登录跳转），策略分三级（按可用性自动降级）：
- 游客 HTTP：PC 店铺首页（view_shop.htm）可访问 → 店铺名/sellerId/shopId
- Cookie + 公开店铺 API：mtop.taobao.wireless.shop.index.wandering.get 按 userId
  精确拉店铺在售商品（推荐，带分页）
- Cookie + Playwright：注入 Cookie 渲染店铺页 → 兜底

商品详情（必须 Cookie——游客 item.htm 只有壳页、getdesc 被 RGV587 风控）：
- Cookie HTTP 请求 item.htm → SSR 内嵌 window.__ICE_APP_CONTEXT__（标题/主图/
  SKU 组合价/物流/店铺评分/参数表）
- mtop.taobao.detail.getdesc 6.0 → itemProperties（参数信息）+ pcDescContent
  （图文详情长图列表）
- Playwright 兜底（HTTP 通道被风控时同页提 SSR）

数据模型 ShopInfo（店铺）+ ShopProduct（商品）+ TaobaoItemDetail（商品详情）。
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


@dataclass
class ShopProduct:
    item_id: str = ""
    title: str = ""
    price: str = ""      # "¥25.00"
    sales: str = ""      # "月销 12" / "1万+"
    pic: str = ""
    url: str = ""
    coupon: str = ""          # 优惠券（如 "5" = 减5元）
    reserve_price: str = ""   # 原价（与现价不同时有意义）

    def to_dict(self) -> dict:
        d = {
            "商品ID": self.item_id,
            "标题": self.title,
            "价格": self.price,
            "销量": self.sales,
            "图片": self.pic,
            "链接": self.url,
            "获取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if self.reserve_price and self.reserve_price != self.price:
            d["原价"] = self.reserve_price
        if self.coupon:
            d["优惠券"] = self.coupon
        return d


@dataclass
class ShopInfo:
    shop_name: str = ""
    seller_id: str = ""
    shop_id: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        d = {"店铺名": self.shop_name, "卖家ID": self.seller_id,
             "店铺ID": self.shop_id, "店铺链接": self.url}
        return {k: v for k, v in d.items() if v}


@dataclass
class ItemSku:
    """单个 SKU 组合（选购参数的一档）。"""
    sku_id: str = ""
    names: str = ""        # 选项名（多规格用 / 连接），如 "3巡海报全款16张（含9亲签）"
    price: str = ""        # "¥288"
    quantity: str = ""     # 库存数量文本


@dataclass
class TaobaoItemDetail:
    """淘宝商品详情：主图 / 选购参数(SKU) / 参数信息 / 图文详情。"""
    item_id: str = ""
    title: str = ""
    price: str = ""              # "¥75 起"
    sales: str = ""              # "已售 64"
    shop_name: str = ""
    seller_id: str = ""
    shop_id: str = ""
    main_images: list = field(default_factory=list)   # 主图 URL
    skus: list = field(default_factory=list)          # [ItemSku]
    sku_props: list = field(default_factory=list)     # [{name, values:[{name,image}]}]
    params: list = field(default_factory=list)        # [(名称, 值)]
    desc_images: list = field(default_factory=list)   # 图文详情长图 URL
    desc_text: list = field(default_factory=list)     # 详情内文字（通常为空，内容都在长图里）
    delivery: str = ""           # "预售，15天内发货 | 快递: 10.00 | 江苏南京"
    shop_scores: str = ""        # "宝贝描述5.0 高 / 卖家服务5.0 高 / 物流服务5.0 高"
    url: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # 图片下载结果：分类 → 本地路径列表
    local_main: list = field(default_factory=list)
    local_sku: list = field(default_factory=list)
    local_desc: list = field(default_factory=list)
    image_dir: str = ""          # 图片下载目录（{商品标题}_{商品ID}_{获取时间}）

    def to_dict(self) -> dict:
        d = {
            "商品ID": self.item_id, "标题": self.title, "价格": self.price,
            "销量": self.sales, "店铺": self.shop_name,
            "主图": self.main_images,
            "规格参数": [{"名称": g.get("name") or "规格",
                        "选项": [v.get("name") for v in g.get("values") or []]}
                       for g in self.sku_props],
            "选购参数": [{"选项": s.names, "价格": s.price, "库存": s.quantity}
                       for s in self.skus],
            "参数信息": [{"名称": n, "值": v} for n, v in self.params],
            "图文详情": self.desc_images,
            "链接": self.url, "获取时间": self.fetched_at,
        }
        if self.delivery:
            d["物流"] = self.delivery
        if self.shop_scores:
            d["店铺评分"] = self.shop_scores
        if self.image_dir:
            d["本地图片目录"] = self.image_dir
        if self.local_main or self.local_sku or self.local_desc:
            d["本地图片"] = {"主图": self.local_main, "规格图": self.local_sku,
                          "图文详情": self.local_desc}
        return d


# ==================== URL 路由 ====================

def detect_shopping_platform(url: str):
    """识别购物链接 → (kind, key)。
    taobao_item: item.taobao.com/item.htm?id=xxx（商品详情）
    taobao_shop: {domain}.taobao.com/shop/view_shop.htm 或 shop{id}.taobao.com
    """
    url = (url or "").strip()
    if not url:
        return None, None
    if re.search(r"(?:item\.taobao\.com|detail\.tmall\.com)/item\.htm", url):
        m = re.search(r"[?&]id=(\d+)", url)
        if m:
            return "taobao_item", m.group(1)
    m = re.search(r"https?://([\w-]+)\.taobao\.com/shop/view_shop\.htm", url)
    if m:
        return "taobao_shop", m.group(1)   # 店铺个性化域名
    m = re.search(r"https?://shop(\d+)\.taobao\.com", url)
    if m:
        return "taobao_shop_id", m.group(1)
    m = re.search(r"taobao\.com", url)
    if m:
        return "taobao_unknown", ""
    return None, None


def _shop_base(url: str) -> str:
    """提取店铺 base URL。"""
    m = re.search(r"https?://[\w.-]+\.taobao\.com", url or "")
    return m.group(0) if m else ""


# ==================== 店铺信息（游客 HTTP） ====================

def fetch_taobao_shop_info(url: str) -> ShopInfo:
    """游客抓 PC 店铺首页 → 店铺名/sellerId/shopId。
    首页为服务端渲染的壳页，g_config/内嵌 script 中含卖家数字 ID。
    """
    r = httpx.get(url, headers={"User-Agent": UA_PC,
                                "Accept-Language": "zh-CN,zh;q=0.9"},
                  timeout=25, follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"淘宝店铺页请求失败（HTTP {r.status_code}）")
    html = r.text
    shop = ShopInfo(url=str(r.url))
    m = re.search(r"<title>(?:首页-)?(.+?)(?:-淘宝网)?</title>", html)
    if m:
        shop.shop_name = m.group(1).strip()
    m = re.search(r'sellerId["\s:=]+["\']?(\d+)', html)
    if m:
        shop.seller_id = m.group(1)
    m = re.search(r'shopId["\s:=]+["\']?(\d+)', html)
    if m:
        shop.shop_id = m.group(1)
    # 标题兜底：店铺名常嵌在 g_config shopName
    if not shop.shop_name:
        m = re.search(r'"shopName"\s*:\s*"([^"]{2,40})"', html)
        if m:
            shop.shop_name = m.group(1)
    if not shop.seller_id and not shop.shop_name:
        raise RuntimeError("淘宝店铺信息解析失败（页面结构变化或需要登录）")
    return shop


def _cookie_pairs(cookie_str: str) -> list:
    """cookie 字符串 → Playwright add_cookies 列表（.taobao.com 域）。"""
    out = []
    for pair in (cookie_str or "").split(";"):
        if "=" in pair:
            name, _, value = pair.strip().partition("=")
            if name:
                out.append({"name": name, "value": value,
                            "domain": ".taobao.com", "path": "/"})
    return out


def _cookie_dict(cookie_str: str) -> dict:
    """cookie 字符串 → 单层 dict（name->value）。"""
    out = {}
    for pair in (cookie_str or "").split(";"):
        if "=" in pair:
            name, _, value = pair.strip().partition("=")
            if name:
                out[name] = value
    return out


# ==================== 商品列表（公开店铺搜索接口） ====================

def _taobao_get_token(cookie: str) -> tuple:
    """访问 taobao.com 首页拿 _m_h5_tk 令牌；返回 (token, cookie_dict)。"""
    c = _cookie_dict(cookie)
    try:
        r = httpx.get("https://h5api.m.taobao.com/h5/mtop.relationrecommend.wirelessrecommend.recommend/2.0/",
                      params={"appKey": "12574478"},
                      headers={"User-Agent": UA_M,
                               "Referer": "https://main.m.taobao.com/",
                               "Cookie": "; ".join(f"{k}={v}" for k, v in c.items())},
                      timeout=20)
        # 合并返回的 cookie
        for ck in r.cookies.jar:
            c[ck.name] = ck.value
    except Exception:
        pass
    tk = c.get("_m_h5_tk", "").split("_")[0]
    return tk, c


def _taobao_mtop(api: str, v: str, data: dict, cookie_dict: dict, tk: str) -> dict:
    """淘宝 mtop 调用（带 _m_h5_tk 签名）。"""
    import hashlib, time
    APPKEY = "12574478"
    data_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    t = str(int(time.time() * 1000))
    if tk:
        sign = hashlib.md5(f"{tk}&{t}&{APPKEY}&{data_str}".encode()).hexdigest()
    else:
        sign = hashlib.md5(f"&{t}&{APPKEY}&{data_str}".encode()).hexdigest()
    r = httpx.get(
        f"https://h5api.m.taobao.com/h5/{api}/{v}/",
        params={"jsv": "2.5.1", "appKey": APPKEY, "t": t, "sign": sign,
                "api": api, "v": v, "type": "json", "dataType": "json",
                "data": data_str},
        headers={"User-Agent": UA_M,
                 "Referer": "https://main.m.taobao.com/",
                 "Cookie": "; ".join(f"{k}={v2}" for k, v2 in cookie_dict.items())},
        timeout=20)
    # 更新 cookie（mtop 可能返回新 cookie）
    for ck in r.cookies.jar:
        cookie_dict[ck.name] = ck.value
    try:
        return r.json()
    except Exception:
        return {}


def _try_mtop_shop_items(shop_id: str, seller_id: str, cookie: str,
                         count: int = 60, log=None) -> dict:
    """登录态 mtop 拉店铺商品（HTTP，绕过 secSDK，**绕过 headless 反爬**）。

    主路径 mtop.taobao.wireless.shop.index.wandering.get（移动版店铺首页商品流）：
    按 userId 精确返回该店铺在售商品，curPage/preItemId/hasNext 分页。
    ⚠️ 勿再用 mtop.taobao.wsearch.appsearch：其 params.s（店铺过滤）已被淘宝
    服务端忽略，返回的是与店铺无关的推荐流（实测拿到的是 Steam CDKEY 类目）。

    返回 {"items":[...], "api":...} 或 {}。
    """
    if not seller_id:
        return {}
    try:
        sid = int(seller_id)
    except (TypeError, ValueError):
        return {}
    tk, ck = _taobao_get_token(cookie)
    api = "mtop.taobao.wireless.shop.index.wandering.get"
    raw: list = []
    cur_page = 1
    pre_item_id = 0
    # 每页 20 件，最多翻 10 页（防死循环）；hasNext 控制提前终止
    while len(raw) < count and cur_page <= 10:
        data = {"direction": 1, "userId": sid, "pageSize": 20,
                "needPreNew": True, "id": 0, "curPage": cur_page,
                "preItemId": pre_item_id}
        if log:
            log(f"mtop wandering 第 {cur_page} 页 ...")
        try:
            resp = _taobao_mtop(api, "1.0", data, ck, tk)
        except Exception as e:
            if log:
                log(f"  mtop 异常: {str(e)[:60]}")
            break
        ret = str(resp.get("ret") or "")
        if "SUCCESS" not in ret:
            if log:
                if "RGV587" in ret:
                    log("  mtop 触发滑块风控（RGV587），转 Playwright 通道")
                else:
                    log(f"  mtop 失败: {ret[:60]}")
            break
        d = resp.get("data") or {}
        blocks = d.get("result") or []
        page_items = [b.get("blockContent") for b in blocks
                      if isinstance(b, dict) and b.get("blockType") == "item"
                      and isinstance(b.get("blockContent"), dict)]
        if not page_items:
            break
        raw.extend(page_items)
        if log:
            log(f"  +{len(page_items)} 件（累计 {len(raw)}）")
        last_iid = str(page_items[-1].get("itemId") or "0")
        try:
            pre_item_id = int(float(last_iid))
        except (TypeError, ValueError):
            break
        if str(d.get("hasNext", "")).lower() != "true":
            break
        cur_page += 1
        if len(raw) < count:
            time.sleep(1.2)   # 翻页间隔，降低风控概率
    if raw:
        return {"items": raw[:count], "api": api}
    return {}


def _harvest_mtop_product(obj, items: dict, depth: int = 0):
    """递归收割商品字典：含数字 itemId 且带 title/price 之一的对象。"""
    if depth > 8 or not obj:
        return
    if isinstance(obj, dict):
        iid = ""
        for k in ("itemId", "nid", "auctionId", "item_id"):
            v = obj.get(k)
            if v and str(v).lstrip("-").isdigit():
                iid = str(v)
                break
        if iid and (obj.get("title") or obj.get("itemTitle") or obj.get("price") is not None):
            cur = items.setdefault(iid, {})
            for src, dst in (("title", "title"), ("itemTitle", "title"),
                             ("price", "price"), ("reservePrice", "price"),
                             ("sold", "sales"), ("soldQuantity", "sales"),
                             ("monthSold", "sales"), ("month_sold", "sales"),
                             ("realSales", "sales"),
                             ("vagueTotalSoldQuantity", "sales"),
                             ("couponValue", "coupon"),
                             ("pic", "pic"), ("picUrl", "pic"), ("img", "pic"),
                             ("mainPic", "pic"), ("itemPic", "pic"),
                             ("pic_path", "pic"), ("imageUrl", "pic")):
                v = obj.get(src)
                if v and not cur.get(dst):
                    if dst == "pic" and isinstance(v, str):
                        v = re.sub(r"^//", "https://", v)
                    cur[dst] = str(v)
        for v in obj.values():
            _harvest_mtop_product(v, items, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _harvest_mtop_product(v, items, depth + 1)


# ==================== 商品列表（Cookie + Playwright 多策略） ====================

# 调试截图保存目录（失败时）
DEBUG_DIR = Path(__file__).parent.parent / "_taobao_debug"


def _try_click_tabs(page, log=None):
    """尝试点击切换商品列表的 tab（"全部宝贝" / "全部商品" / "全部"）。"""
    for label in ("全部宝贝", "全部商品", "全部", "在售商品", "All"):
        try:
            page.click(f"text={label}", timeout=1500)
            if log:
                log(f"  点击了「{label}」tab")
            page.wait_for_timeout(1500)
            return True
        except Exception:
            continue
    return False


def _playwright_fetch_products(shop_info: ShopInfo, cookie: str,
                               count: int = 60, log=None) -> list:
    """注入 Cookie 多 URL 多 UA 渲染店铺页 → [ShopProduct]（兜底策略）。

    淘宝商品列表页需要登录且会触发反爬验证，按以下顺序尝试（命中即返回）：
    1. {domain}/search.htm?orderType=hotsell (PC UA + cookie)
    2. {domain}/search.htm (PC UA)
    3. shop.m.taobao.com/shop/shop_index.htm?shop_id=XXX (移动 UA + cookie)
    4. {domain}/search.htm?orderType=newinsell (PC UA，换排序)
    5. {domain}/shop/view_shop.htm (店铺首页兜底)

    失败时在 _taobao_debug/ 下保存截图与 HTML 用于排查。
    """
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("需要安装 Playwright：pip install playwright && playwright install chromium")

    def _log(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    m = re.search(r"https?://([\w.-]+)\.taobao\.com", shop_info.url or "")
    domain = m.group(0) if m else (shop_info.url or "")
    shop_id = shop_info.shop_id or ""

    # ★ 移动版优先：mobile 版淘宝对 headless 反爬更松，且 shop_index.htm 不强制登录跳转
    # ★ mtop 拦截的 PC 搜索页即使拿到 API 响应，也常因 secSDK 风控被强制登录
    candidates = [
        (f"https://shop.m.taobao.com/shop/shop_index.htm?shop_id={shop_id}", UA_M,
         "M+shop_index"),
        (f"{domain}/search.htm?orderType=hotsell", UA_PC, "PC+hotsell"),
        (f"{domain}/search.htm", UA_PC, "PC+search"),
        (f"{domain}/search.htm?orderType=newinsell", UA_PC, "PC+newinsell"),
        (f"{domain}/shop/view_shop.htm", UA_PC, "PC+view_shop"),
    ]

    DEBUG_DIR.mkdir(exist_ok=True)
    last_err = ""
    for url, ua, tag in candidates:
        try:
            _log(f"尝试: {tag} → {url}")
            products, diag = _playwright_one(url, ua, cookie, count, _log)
            if products:
                return products
            last_err = diag or f"{tag} 未拿到商品"
        except Exception as e:
            last_err = f"{tag} 异常: {str(e)[:80]}"
            _log(f"  {last_err}")
            continue
    raise RuntimeError(
        f"未解析到商品：{last_err}。"
        "淘宝商品列表对游客关闭，对登录用户也会触发风控验证（截图请查看 _taobao_debug/ 目录）。"
        "建议在浏览器登录淘宝 → 打开该店铺 → 等所有图片/商品加载完毕 →"
        "再从 Cookie 管理刷新 Cookie 后重试")


def _playwright_one(url: str, ua: str, cookie: str, count: int, log) -> tuple:
    """单次 Playwright 渲染尝试。返回 (products, diag)。"""
    items = {}

    def on_response(resp):
        if resp.status != 200:
            return
        u = resp.url
        if "mtop" not in u and "h5api" not in u and "taobao" not in u:
            return
        try:
            body = resp.json()
            ret = str(body.get("ret") or "")
            if "SUCCESS" in ret or "::OK" in ret:
                _harvest_mtop_product(body.get("data"), items)
        except Exception:
            pass

    # ====== Playwright stealth: 应对淘宝 secSDK (securitySDK) ======
    # 淘宝的 window.secSdk = new securitySDK({piskMode:"ALL"}) 会检测：
    #  - navigator.webdriver=true
    #  - 缺失 window.chrome.runtime
    #  - navigator.plugins 为空
    #  - navigator.languages 为空数组
    #  - document.hasFocus() / visibilityState 异常
    # 通过 add_init_script 在每页加载前注入修复，绕过检测。
    STEALTH_INIT = """
        (() => {
            // 1. 隐藏 webdriver
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            // 2. 伪装 chrome 对象（secSDK 会调 window.chrome.runtime）
            window.chrome = {
                runtime: {
                    PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
                    PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
                    RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
                    OnInstallReason: { CHROME_UPDATE: 'chrome_update', ONBOARDING_FROM_SYNCED_PROMO: 'onboarding_from_synced_promo', INSTALLABLE_TRIGGER: 'installable_trigger', ACCEPT_GLOBALLY: 'accept_globally' },
                    OnUpdateReason: { UNKNOWN: 'unknown', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                    connect: () => {},
                    sendMessage: () => {}
                },
                loadTimes: () => ({}),
                csi: () => ({}),
                app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } }
            };
            // 3. plugins
            const makePlugins = () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
            ];
            Object.defineProperty(navigator, 'plugins', { get: () => makePlugins(), configurable: true });
            // 4. languages
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: true });
            // 5. permissions 修正（notifications 状态）
            try {
                const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
                window.navigator.permissions.query = (params) => (
                    params.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission, onchange: null }) :
                        origQuery(params)
                );
            } catch(e) {}
            // 6. webdriver 痕迹兜底
            ['driver', 'selenium', '_Selenium_IDE_Recorder', '__webdriver_evaluate', '__driver_evaluate', '__selenium_evaluate', '__selenium_unwrap'].forEach(k => {
                try { delete document.documentElement.dataset[k]; } catch(e){}
                try { delete window[k]; } catch(e){}
            });
            // 7. Connection API（淘宝偶尔会查 navigator.connection.effectiveType）
            try {
                if (!navigator.connection) {
                    Object.defineProperty(navigator, 'connection', { get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }) });
                }
            } catch(e) {}
        })();
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
                "--no-sandbox",
                "--disable-web-security",
                "--disable-dev-shm-usage",
                "--window-size=1400,900",
            ])
        try:
            ctx = browser.new_context(
                user_agent=ua,
                viewport={"width": 1400, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                color_scheme="light",
                device_scale_factor=1,
                # JavaScript 启用（默认就是 true，但显式声明便于排查）
                java_script_enabled=True)
            # 注册 stealth：每页加载前注入修复（new_page 之前生效）
            ctx.add_init_script(STEALTH_INIT)
            # 同时注入 .taobao.com 和 shop 域名的 cookie
            domain_match = re.search(r"https?://([\w.-]+\.taobao\.com)", url)
            shop_domain = domain_match.group(1) if domain_match else ".taobao.com"
            cookies = []
            for pair in (cookie or "").split(";"):
                if "=" in pair:
                    name, _, value = pair.strip().partition("=")
                    if not name:
                        continue
                    # 注入到 .taobao.com (覆盖所有子域) 和具体店铺域
                    cookies.append({"name": name, "value": value,
                                    "domain": ".taobao.com", "path": "/"})
                    cookies.append({"name": name, "value": value,
                                    "domain": shop_domain, "path": "/"})
            ctx.add_cookies(cookies)
        except Exception as e:
            browser.close()
            raise RuntimeError(f"Cookie 注入失败：{e}")

        page = ctx.new_page()
        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            browser.close()
            raise RuntimeError(f"页面加载失败：{str(e)[:80]}")

        try:
            # 等页面"看似"稳定后再操作
            page.wait_for_timeout(3000)
            # 尝试点击 "全部宝贝" 等 tab 触发商品列表
            _try_click_tabs(page, log)
            # 滚动触发懒加载
            for y in (600, 1200, 1800, 2400, 3000, 3600, 4200):
                page.evaluate(f"window.scrollTo(0, {y})")
                page.wait_for_timeout(900)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            current_url = page.url
            log(f"  最终 URL: {current_url[:80]}")
            if "login" in current_url or "login.taobao.com" in current_url:
                # 调试截图
                tag = re.sub(r"\W+", "_", url)[:30]
                try:
                    page.screenshot(path=str(DEBUG_DIR / f"login_{tag}.png"))
                except:
                    pass
                browser.close()
                raise RuntimeError("被重定向到登录页（Cookie 失效）")

            # 检查是否在风控验证页
            page_text = page.inner_text("body", timeout=3000) or ""
            if any(s in page_text for s in ("滑动验证", "安全验证", "拖动滑块", "请完成验证")):
                tag = re.sub(r"\W+", "_", url)[:30]
                try:
                    page.screenshot(path=str(DEBUG_DIR / f"captcha_{tag}.png"))
                except:
                    pass
                browser.close()
                raise RuntimeError("触发风控验证（截图已保存到 _taobao_debug/）")

            # DOM 兜底提取（多种 selector 兼容新旧版）
            dom_items = _extract_from_dom(page)
            log(f"  DOM 提取: {len(dom_items)} 个候选")
            for iid, d in dom_items.items():
                cur = items.setdefault(iid, {})
                for k, v in d.items():
                    if v and not cur.get(k):
                        cur[k] = v

            # 如果还没商品，保存调试截图
            if not items:
                tag = re.sub(r"\W+", "_", url)[:30]
                try:
                    page.screenshot(path=str(DEBUG_DIR / f"empty_{tag}.png"),
                                    full_page=True)
                    with open(DEBUG_DIR / f"empty_{tag}.html", "w",
                              encoding="utf-8") as f:
                        f.write(page.content())
                except:
                    pass
        finally:
            browser.close()

    products = []
    for iid, d in items.items():
        title = d.get("title") or ""
        if not title:
            continue
        products.append(ShopProduct(
            item_id=iid,
            title=re.sub(r"<[^>]+>", "", title).strip()[:120],
            price=_fmt_price(d.get("price")),
            sales=_fmt_sales(d.get("sales")),
            pic=d.get("pic") or "",
            url=f"https://item.taobao.com/item.htm?id={iid}",
            coupon=str(d.get("coupon") or "").strip(),
        ))
    products.sort(key=lambda x: x.item_id)
    return products[:max(1, count)], ""


def _extract_from_dom(page) -> dict:
    """DOM 解析：淘宝店铺商品卡片的多种选择器兼容。"""
    out = {}
    # 综合选择器：抓所有含 item id 的锚点
    selectors = [
        "a[href*='item.htm?id=']",
        "a[href*='detail.tmall.com/item']",
        "a[href*='item.taobao.com/item.htm']",
        "a[href*='//item.taobao.com']",
    ]
    for sel in selectors:
        try:
            anchors = page.eval_on_selector_all(
                sel,
                """els => els.map(el => {
                    const href = el.getAttribute('href') || '';
                    const text = (el.innerText || el.getAttribute('title') || el.getAttribute('alt') || '').trim();
                    let pic = '';
                    const img = el.querySelector('img');
                    if (img) pic = img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-ks-lazyload') || '';
                    // 向上找价格和销量
                    let card = el;
                    for (let i = 0; i < 6 && card.parentElement; i++) {
                        const t = card.innerText || '';
                        if (/¥\\s?\\d+\\.?\\d*/.test(t) || /月销\\s?\\d+/.test(t)) break;
                        card = card.parentElement;
                    }
                    const cardText = card ? (card.innerText || '') : '';
                    const pm = cardText.match(/¥\\s?([\\d.]+)/) || cardText.match(/￥\\s?([\\d.]+)/);
                    const price = pm ? pm[1] : '';
                    const sm = cardText.match(/月销\\s?(\\d+)/);
                    const sales = sm ? sm[1] : '';
                    return {href, text: text.slice(0, 150), pic, price, sales};
                })""")
        except Exception:
            continue
        for a in anchors or []:
            href = a.get("href") or ""
            text = (a.get("text") or "").strip()
            if len(text) < 2:
                continue
            m = re.search(r"[?&]id=(\d{10,15})", href)
            if not m:
                continue
            iid = m.group(1)
            if iid in out:
                continue
            pic = a.get("pic") or ""
            if pic.startswith("//"):
                pic = "https:" + pic
            out[iid] = {"title": text,
                        "price": a.get("price") or "",
                        "sales": a.get("sales") or "",
                        "pic": pic}
    return out


def _fmt_price(v) -> str:
    if v in (None, ""):
        return ""
    s = str(v).strip()
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return ""
    return f"¥{m.group(1)}"


def _fmt_sales(v) -> str:
    """销量显示：保留 "1万+"/"100+人付款" 原样（淘宝模糊销量），纯数字转 "月销 N"。"""
    if v in (None, ""):
        return ""
    s = str(v).strip()
    if "万" in s or s.endswith("+"):
        return f"已售 {s}" if not s.startswith(("月销", "已售")) else s
    if "+" in s:   # "100+人付款" 之类完整短语，原样保留
        return s
    m = re.search(r"(\d+)", s)
    if not m:
        return ""
    return f"月销 {m.group(1)}"


# ==================== 统一入口 ====================

def fetch_taobao_shop_products(shop_info: ShopInfo, cookie: str, count: int = 60,
                               log=None) -> list:
    """三级降级拿商品列表：mtop API → Playwright 兜底。

    返回商品列表；全部失败时 raise RuntimeError(详细原因)。
    """
    if not cookie:
        raise RuntimeError("淘宝商品列表需要登录：请在 Cookie 管理中添加淘宝 Cookie")

    # 一级：mtop API（wandering.get 按 userId 精确拉店铺商品）
    try:
        result = _try_mtop_shop_items(
            shop_info.shop_id, shop_info.seller_id, cookie, count=count, log=log)
        if result.get("items"):
            return _products_from_mtop_items(result["items"])
    except Exception as e:
        if log:
            log(f"mtop 通道失败: {e}")

    # 二级：Playwright（多 URL 多 UA 兜底）
    try:
        products = _playwright_fetch_products(shop_info, cookie, count=count, log=log)
        if products:
            return products
        last_err = "Playwright 未解析到商品"
    except Exception as e:
        last_err = str(e)
        if log:
            log(f"Playwright 失败: {e}")
        # 登录问题直接抛出，不继续尝试
        if "Cookie" in str(e) or "登录" in str(e) or "失效" in str(e) or "重定向" in str(e):
            raise

    raise RuntimeError(
        f"未解析到商品：{last_err or 'Cookie 可能失效，或店铺暂无在售商品'}。"
        "请确认 Cookie 包含 cookie2/_tb_token_/isg，且浏览器仍是登录状态")


def _products_from_mtop_items(items: list) -> list:
    """mtop API 返回的商品列表 → [ShopProduct]。

    支持多种字段模式（按淘宝接口演进的实际格式）：
    - wandering.get（当前主路径）：blockContent 平铺——itemId/title/itemPic/price/
      reservePrice/vagueTotalSoldQuantity("1万+")/couponValue
    - 经典 PC：price="¥25.00" 字符串、pic/picUrl、itemId
    - getshoptabdata：priceShowWithIcon、extraParams
    """
    products = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # 商品 ID（多个名字兼容；浮点值去掉 ".0" 尾巴——wsearch 的 nidlong 是 float）
        iid = (it.get("itemId") or it.get("itemIdStr") or it.get("nid") or
               it.get("auctionId") or it.get("nidlong") or "")
        if isinstance(iid, float):
            iid = int(iid)
        iid = str(iid).strip()
        if iid.endswith(".0"):
            iid = iid[:-2]
        if not iid or not iid.isdigit():
            continue
        # 标题（多个字段）
        title = (it.get("title") or it.get("itemTitle") or
                 it.get("shortTitle") or
                 it.get("short_title_c2c") or it.get("wfTwoLineTitle") or "").strip()
        if not title:
            # 部分接口把标题放在 extraParams 里
            ep = it.get("extraParams") or {}
            if isinstance(ep, dict):
                title = (ep.get("title") or ep.get("itemTitle") or "").strip()

        # 价格（wandering/wsearch: price="198.00" 元单位字符串；
        # priceShow / priceShowWithIcon 是 dict{price}）
        raw_price = (it.get("price") or it.get("reservePrice") or
                     it.get("priceShow") or it.get("priceShowWithIcon") or 0)
        if isinstance(raw_price, dict):
            raw_price = raw_price.get("price") or raw_price.get("originPrice") or 0
        if isinstance(raw_price, str):
            raw_price = re.sub(r"[^\d.]", "", raw_price)
        try:
            p = float(raw_price) if raw_price else 0
            price = f"¥{p:g}" if p > 0 else ""
        except (ValueError, TypeError):
            price = _fmt_price(raw_price)

        # 原价（wandering: reservePrice，与现价不同才有展示价值）
        reserve = ""
        rp = it.get("reservePrice")
        if rp is not None:
            if isinstance(rp, dict):
                rp = rp.get("price") or ""
            rp_s = re.sub(r"[^\d.]", "", str(rp))
            try:
                rp_f = float(rp_s) if rp_s else 0
                if rp_f > 0 and f"¥{rp_f:g}" != price:
                    reserve = f"¥{rp_f:g}"
            except (ValueError, TypeError):
                pass

        # 优惠券（wandering: couponValue，数值字符串=减免金额）
        coupon = str(it.get("couponValue") or "").strip()

        # 销量（wandering: vagueTotalSoldQuantity "1万+"；wsearch: realSales
        # "100+人付款"——两者都是模糊销量，直接采用）
        sales = ""
        for k in ("realSales", "vagueTotalSoldQuantity"):
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                sales = v.strip()
                break
        if not sales:
            sales_raw = (it.get("sold") or it.get("soldQuantity") or
                         it.get("monthSold") or it.get("sale_count") or 0)
            try:
                sales = f"月销 {int(sales_raw)}" if int(sales_raw) else ""
            except (ValueError, TypeError):
                sales = _fmt_sales(sales_raw)

        # 图片（wsearch.appsearch: imageInfo=[{imageUrl}] 是 **list**；
        # pic_path 带 "_60x60.jpg" 缩略后缀，去掉升级原图）
        pic = (it.get("pic") or it.get("picUrl") or it.get("img") or
               it.get("itemPic") or it.get("mainPic") or "")
        if isinstance(pic, dict):
            pic = pic.get("imgUrl") or pic.get("url") or ""
        if not pic:
            ii = it.get("imageInfo")
            if isinstance(ii, list) and ii and isinstance(ii[0], dict):
                pic = ii[0].get("imageUrl") or ii[0].get("imgUrl") or ""
            elif isinstance(ii, dict):
                pic = ii.get("imageUrl") or ii.get("imgUrl") or ii.get("url") or ""
        if not pic:
            pic = it.get("pic_path") or ""
        if isinstance(pic, str) and pic:
            pic = re.sub(r"_\d+x\d+\.(jpe?g|png|webp)$", r".\1", pic)
            if pic.startswith("//"):
                pic = "https:" + pic
            # 注意：g.search*.alicdn.com 的 https 证书不覆盖该域名，
            # 保留原始 http:// 不强制升级

        products.append(ShopProduct(
            item_id=iid, title=re.sub(r"<[^>]+>", "", title)[:120],
            price=price, sales=sales, pic=pic,
            url=f"https://item.taobao.com/item.htm?id={iid}",
            coupon=coupon, reserve_price=reserve))
    return products


def parse_shopping_url(url: str, cookie: str = "", count: int = 60, log=None):
    """购物链接 → (ShopInfo, [ShopProduct])。
    无 Cookie 时只返回店铺信息（商品列表需要登录）。
    """
    kind, key = detect_shopping_platform(url)
    if not kind:
        raise ValueError("无法识别的购物链接（当前支持淘宝店铺）")
    if kind == "taobao_unknown":
        raise ValueError("淘宝链接无法定位店铺（请提供 shop/view_shop.htm 或 shop{id}.taobao.com 链接）")

    info = fetch_taobao_shop_info(url)
    if not cookie:
        return info, []
    products = fetch_taobao_shop_products(info, cookie, count=count, log=log)
    return info, products


# ==================== 商品详情（主图 / 选购参数 / 参数信息 / 图文详情） ====================

def _extract_ice_ssr(html: str) -> dict:
    """从 item.htm HTML 提取 window.__ICE_APP_CONTEXT__ 的 SSR JSON。

    赋值形式（tbpc-pc-detail-ssr-2025）：
      <script>!(function () {var a = window.__ICE_APP_CONTEXT__ || {};var b = {...};
              for (var k in a) {b[k] = a[k]}window.__ICE_APP_CONTEXT__=b;})();</script>
    用「var b = 」起点做平衡大括号扫描。
    """
    m = re.search(r'var b = (\{)', html)
    if not m:
        return {}
    start = m.start(1)
    depth = 0
    in_str = None
    esc = False
    for i in range(start, min(len(html), start + 500000)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except Exception:
                    return {}
    return {}


def _ssr_res(data: dict) -> dict:
    """SSR JSON → res 数据节点。"""
    try:
        return (data.get("loaderData", {}).get("home", {})
                .get("data", {}).get("res", {})) or {}
    except Exception:
        return {}


def _detail_from_ssr(item_id: str, res: dict) -> TaobaoItemDetail:
    """SSR res 节点 → TaobaoItemDetail（不含图文详情，详情走 getdesc）。"""
    d = TaobaoItemDetail(item_id=str(item_id),
                         url=f"https://item.taobao.com/item.htm?id={item_id}")
    item = res.get("item") or {}
    seller = res.get("seller") or {}
    comp = res.get("componentsVO") or {}
    d.title = str(item.get("title") or "").strip()
    d.main_images = [u for u in (item.get("images") or []) if isinstance(u, str)]
    cnt = item.get("vagueSellCount")
    d.sales = _fmt_sales(cnt) if str(cnt).strip() else ""
    d.shop_name = str(seller.get("shopName") or "").strip()
    d.seller_id = str(seller.get("sellerId") or "").strip()
    d.shop_id = str(seller.get("shopId") or "").strip()
    d.url = f"https://item.taobao.com/item.htm?id={d.item_id}"

    # 价格（priceVO.price.priceText，priceDesc="起" 时追加）
    pv = ((comp.get("priceVO") or {}).get("price") or {})
    pt = str(pv.get("priceText") or "").strip()
    if pt:
        d.price = f"¥{pt}"
        if str(pv.get("priceDesc") or "").strip():
            d.price += f" {str(pv['priceDesc']).strip()}"

    # 物流
    dv = comp.get("deliveryVO") or {}
    parts = []
    if dv.get("agingDesc"):
        parts.append(str(dv["agingDesc"]))
    if dv.get("freight"):
        parts.append(str(dv["freight"]))
    if dv.get("deliveryFromAddr"):
        parts.append(f"发货地 {dv['deliveryFromAddr']}")
    d.delivery = " | ".join(parts)

    # 店铺评分
    ev = seller.get("evaluates") or []
    if ev:
        d.shop_scores = " / ".join(
            f"{e.get('title')}{str(e.get('score')).strip()}{e.get('levelText', '')}"
            for e in ev if isinstance(e, dict))

    # SKU（选购参数）：props 定义 + skus 组合 + sku2info 价格
    sb = res.get("skuBase") or {}
    sc = res.get("skuCore") or {}
    s2i = sc.get("sku2info") or {}
    props = sb.get("props") or []
    d.sku_props = []
    vid_name = {}          # vid → value 名（跨 props 查找）
    for p in props:
        if not isinstance(p, dict):
            continue
        vals = []
        for v in (p.get("values") or []):
            if not isinstance(v, dict):
                continue
            img = str(v.get("image") or "")
            if img.startswith("//"):
                img = "https:" + img
            vals.append({"name": str(v.get("name") or ""), "image": img})
            vid_name[str(v.get("vid") or "")] = str(v.get("name") or "")
        if vals:
            d.sku_props.append({"name": str(p.get("name") or "规格"), "values": vals})

    default_price = ""
    info0 = s2i.get("0") or {}
    if isinstance(info0, dict):
        default_price = str((info0.get("price") or {}).get("priceText") or "").strip()
    for s in (sb.get("skus") or []):
        if not isinstance(s, dict):
            continue
        sku_id = str(s.get("skuId") or "")
        prop_path = str(s.get("propPath") or "")
        names = []
        for pair in prop_path.split(";"):
            vid = pair.split(":")[-1] if ":" in pair else ""
            if vid and vid != "-1" and vid in vid_name:
                names.append(vid_name[vid])
        info = s2i.get(sku_id) or {}
        price = str((info.get("price") or {}).get("priceText") or "").strip() if isinstance(info, dict) else ""
        if not price:
            price = default_price
        qty = str(info.get("quantity") or "").strip() if isinstance(info, dict) else ""
        d.skus.append(ItemSku(
            sku_id=sku_id,
            names=" / ".join(names) if names else "默认",
            price=f"¥{price}" if price else "",
            quantity=qty))
    # 无 SKU 组合（单规格商品）：用 skuItem 默认价展示一行
    if not d.skus:
        si = ((sc.get("skuItem") or {}).get("infoVO") or {})
        price = str((si.get("price") or {}).get("priceText") or "") if isinstance(si, dict) else ""
        if not price:
            price = default_price
        d.skus.append(ItemSku(sku_id="0", names="默认",
                              price=f"¥{price}" if price else ""))

    # 参数信息（SSR 的 industryParamVO：重点参数 + 基本参数）
    ip = ((res.get("plusViewVO") or {}).get("industryParamVO") or {})
    for key in ("enhanceParamList", "basicParamList"):
        for p in (ip.get(key) or []):
            if isinstance(p, dict) and p.get("propertyName"):
                d.params.append((str(p["propertyName"]),
                                 str(p.get("valueName") or "")))
    return d


def _try_getdesc(item_id: str, cookie: str, log=None) -> dict:
    """mtop.taobao.detail.getdesc 6.0 → 参数信息 + 图文详情长图（需 Cookie）。

    返回 {"params": [(名称, 值)], "desc_images": [URL], "desc_text": [str]} 或 {}。
    """
    tk, ck = _taobao_get_token(cookie)
    try:
        resp = _taobao_mtop("mtop.taobao.detail.getdesc", "6.0",
                            {"id": str(item_id), "type": "1"}, ck, tk)
    except Exception as e:
        if log:
            log(f"getdesc 异常: {str(e)[:60]}")
        return {}
    ret = str(resp.get("ret") or "")
    if "SUCCESS" not in ret:
        if log:
            log(f"getdesc 失败: {ret[:60]}")
        return {}
    data = resp.get("data") or {}
    out = {"params": [], "desc_images": [], "desc_text": []}
    for p in (data.get("itemProperties") or []):
        if isinstance(p, dict) and p.get("name"):
            out["params"].append((str(p["name"]), str(p.get("value") or "")))
    pc = str(data.get("pcDescContent") or "")
    if pc:
        for u in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', pc):
            u = u.strip()
            if u.startswith("//"):
                u = "https:" + u
            if u:
                out["desc_images"].append(u)
        for t in re.findall(r'>([^<>]{4,})<', pc):
            t = re.sub(r"\s+", " ", t).strip()
            if t and t not in out["desc_text"]:
                out["desc_text"].append(t)
    return out


def _playwright_item_ssr(item_id: str, cookie: str, log=None) -> dict:
    """Playwright 兜底：打开 PC item 页提取 SSR JSON（httpx 通道被风控时）。"""
    if not HAS_PLAYWRIGHT:
        return {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=UA_PC,
                                      viewport={"width": 1280, "height": 900},
                                      locale="zh-CN", timezone_id="Asia/Shanghai")
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            cookies = _cookie_pairs(cookie)
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(f"https://item.taobao.com/item.htm?id={item_id}",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            html = page.content()
            ctx.close()
        finally:
            browser.close()
    return _extract_ice_ssr(html)


def fetch_taobao_item_detail(item_id: str, cookie: str, log=None) -> TaobaoItemDetail:
    """商品详情三级通道：Cookie HTTP SSR → Playwright SSR → getdesc 补详情。

    淘宝详情页强制登录态（游客 item.htm 只有壳页、getdesc 游客被 RGV587 风控），
    本功能属于「必须用 Cookie 的接口」。
    """
    item_id = str(item_id).strip()
    if not item_id.isdigit():
        raise ValueError(f"无效的淘宝商品ID：{item_id}")
    if not cookie:
        raise RuntimeError("淘宝商品详情需要登录：请在 Cookie 管理中添加淘宝 Cookie")

    # 一级：带 Cookie 的 httpx 请求 item.htm → SSR
    res = {}
    try:
        if log:
            log("请求商品页 SSR ...")
        r = httpx.get(f"https://item.taobao.com/item.htm?id={item_id}",
                      headers={"User-Agent": UA_PC,
                               "Accept-Language": "zh-CN,zh;q=0.9",
                               "Cookie": cookie},
                      timeout=25, follow_redirects=True)
        res = _ssr_res(_extract_ice_ssr(r.text))
    except Exception as e:
        if log:
            log(f"SSR 请求失败: {str(e)[:60]}")

    # 二级：Playwright 兜底
    if not res:
        if log:
            log("HTTP 通道未拿到数据，转 Playwright ...")
        try:
            res = _ssr_res(_playwright_item_ssr(item_id, cookie, log=log))
        except Exception as e:
            if log:
                log(f"Playwright 失败: {str(e)[:60]}")
    if not res:
        raise RuntimeError(
            "商品页数据获取失败：Cookie 可能失效（请重新登录淘宝），或触发风控稍后再试")

    detail = _detail_from_ssr(item_id, res)
    if not detail.title:
        raise RuntimeError("商品已下架或页面结构变化（SSR 无标题）")

    # 三级：getdesc 补参数信息 + 图文详情
    if log:
        log("请求图文详情（getdesc）...")
    gd = _try_getdesc(item_id, cookie, log=log)
    if gd:
        # 参数合并去重（getdesc 优先）
        merged = list(gd.get("params") or [])
        seen = {n for n, _ in merged}
        for n, v in detail.params:
            if n not in seen:
                merged.append((n, v))
        detail.params = merged
        detail.desc_images = gd.get("desc_images") or []
        detail.desc_text = gd.get("desc_text") or []
    if log:
        log(f"主图 {len(detail.main_images)} 张 | SKU {len(detail.skus)} 档 | "
            f"参数 {len(detail.params)} 项 | 详情图 {len(detail.desc_images)} 张")
    return detail


def _img_ext(url: str) -> str:
    m = re.search(r"\.(jpe?g|png|webp|gif)(?:[?~]|$)", url, re.I)
    return "." + m.group(1).lower().replace("jpeg", "jpg") if m else ".jpg"


def _download_images(urls: list, folder: Path, prefix: str, log=None) -> list:
    """下载图片到目录（失败跳过不致命），返回本地路径列表。"""
    if not urls:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, u in enumerate(urls, 1):
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        path = folder / f"{prefix}{i:02d}{_img_ext(u)}"
        try:
            r = httpx.get(u, timeout=30,
                          headers={"User-Agent": UA_PC,
                                   "Referer": "https://item.taobao.com/"})
            r.raise_for_status()
            path.write_bytes(r.content)
            out.append(str(path))
        except Exception as e:
            if log:
                log(f"图片下载失败 {u[-40:]}: {str(e)[:40]}")
    return out


def _safe_folder_name(s: str, maxlen: int = 40) -> str:
    """商品标题 → 合法目录名（去 Windows 非法字符，截断长度）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "", str(s or "")).strip(" ._")
    return s[:maxlen] or "未命名"


def download_item_images(detail: TaobaoItemDetail, base_dir, log=None) -> TaobaoItemDetail:
    """下载主图 / SKU 图 / 图文详情长图。

    目录：base_dir/{商品标题}_{商品ID}_{获取时间 YYYYMMDD_HHMMSS}/{main,sku,desc}/
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{_safe_folder_name(detail.title)}_{detail.item_id}_{ts}"
    base = Path(base_dir) / folder_name
    if log:
        log(f"下载图片到 {base} ...")
    detail.local_main = _download_images(detail.main_images, base / "main",
                                         "主图", log=log)
    sku_urls = []
    for grp in detail.sku_props:
        for v in grp.get("values") or []:
            if v.get("image"):
                sku_urls.append(v["image"])
    detail.local_sku = _download_images(sku_urls, base / "sku", "规格", log=log)
    detail.local_desc = _download_images(detail.desc_images, base / "desc",
                                         "详情", log=log)
    if detail.local_main or detail.local_sku or detail.local_desc:
        detail.image_dir = str(base)
    if log:
        log(f"图片下载完成：主图 {len(detail.local_main)} / 规格 "
            f"{len(detail.local_sku)} / 详情 {len(detail.local_desc)}")
    return detail


def export_item_xlsx(detail: TaobaoItemDetail, path: str) -> str:
    """单商品导出 xlsx：商品信息 / 参数信息 / 选购参数 / 主图 / 图文详情 五个 sheet。

    本地已下载的图片直接插入表格（openpyxl + Pillow）。
    """
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="FFF1E6")
    head_font = Font(bold=True)

    def _sheet(ws, title, headers, rows, widths=None):
        ws.title = title
        for c, h in enumerate(headers, 1):
            cell = ws.cell(1, c, h)
            cell.fill = head_fill
            cell.font = head_font
        for r, row in enumerate(rows, 2):
            for c, v in enumerate(row, 1):
                ws.cell(r, c, v)
        for c in range(1, len(headers) + 1):
            w = (widths[c - 1] if widths and c <= len(widths)
                 else (46 if c == 1 else 30))
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.freeze_panes = "A2"

    # 1) 商品信息
    info_rows = [
        ("标题", detail.title), ("价格", detail.price), ("销量", detail.sales),
        ("店铺", detail.shop_name), ("卖家ID", detail.seller_id),
        ("店铺ID", detail.shop_id), ("商品ID", detail.item_id),
        ("链接", detail.url), ("物流", detail.delivery),
        ("店铺评分", detail.shop_scores),
        ("主图数量", len(detail.local_main) or len(detail.main_images)),
        ("规格图数量", len(detail.local_sku)),
        ("图文详情图数量", len(detail.local_desc) or len(detail.desc_images)),
        ("获取时间", detail.fetched_at),
    ]
    _sheet(wb.active, "商品信息", ["项目", "内容"],
           [(k, v) for k, v in info_rows if v])

    # 2) 参数信息（全部参数一张表：商品基础 + 规格参数 + 参数明细 + 物流评分）
    all_params: list = []
    all_params.append(("商品", "标题", detail.title))
    if detail.price:
        all_params.append(("商品", "价格", detail.price))
    if detail.sales:
        all_params.append(("商品", "销量", detail.sales))
    if detail.shop_name:
        all_params.append(("商品", "店铺", detail.shop_name))
    if detail.seller_id:
        all_params.append(("商品", "卖家ID", detail.seller_id))
    if detail.shop_id:
        all_params.append(("商品", "店铺ID", detail.shop_id))
    if detail.url:
        all_params.append(("商品", "链接", detail.url))
    for grp in detail.sku_props:
        vals = " / ".join(v.get("name") or "" for v in grp.get("values") or [])
        if vals:
            all_params.append(("规格参数", grp.get("name") or "规格", vals))
    for n, v in detail.params:
        all_params.append(("参数", n, v))
    if detail.delivery:
        all_params.append(("物流", "发货/运费", detail.delivery))
    if detail.shop_scores:
        all_params.append(("店铺", "评分", detail.shop_scores))
    _sheet(wb.create_sheet(), "参数信息(全部)", ["类别", "参数", "值"], all_params,
           widths=(10, 20, 80))

    # 3) 选购参数（SKU 明细）
    sku_rows = [(s.names, s.price, s.quantity) for s in detail.skus]
    _sheet(wb.create_sheet(), "选购参数", ["选项", "价格", "库存"],
           sku_rows or [("（单规格商品）", detail.price, "")])

    # 图片 sheet：逐行插图，缩放到统一宽 380px
    def _img_sheet(title: str, local_paths: list, urls: list):
        ws = wb.create_sheet(title)
        ws.column_dimensions["A"].width = 56
        ws.column_dimensions["B"].width = 46
        ws.cell(1, 1, "图片").fill = head_fill
        ws.cell(1, 1).font = head_font
        ws.cell(1, 2, "说明").fill = head_fill
        ws.cell(1, 2).font = head_font
        ws.freeze_panes = "A2"
        row = 2
        for i, p in enumerate(local_paths, 1):
            try:
                from PIL import Image as PILImage
                with PILImage.open(p) as im:
                    w0, h0 = im.size
                img = XLImage(p)
                img.width = 380
                img.height = int(h0 * 380 / max(w0, 1))
                ws.add_image(img, f"A{row}")
                ws.cell(row, 2, f"{i}/{len(local_paths)}")
                # 行高（磅）= 像素高 × 0.75，多留 8 磅边距
                ws.row_dimensions[row].height = img.height * 0.75 + 8
            except Exception:
                ws.cell(row, 2, f"图片插入失败：{Path(p).name}")
            row += 1
        if not local_paths:
            for i, u in enumerate(urls, 1):
                ws.cell(i + 1, 2, u)
            ws.cell(1, 2, "（图片未下载，仅列 URL）")

    _img_sheet("主图", detail.local_main, detail.main_images)
    _img_sheet("图文详情", detail.local_desc, detail.desc_images)

    wb.save(path)
    return path


def parse_taobao_item_url(url: str, cookie: str, log=None) -> TaobaoItemDetail:
    """商品链接 → TaobaoItemDetail（入口，页面直接调用）。"""
    kind, key = detect_shopping_platform(url)
    if kind != "taobao_item" or not key:
        raise ValueError("请输入淘宝商品链接（item.taobao.com/item.htm?id=xxx）")
    return fetch_taobao_item_detail(key, cookie, log=log)