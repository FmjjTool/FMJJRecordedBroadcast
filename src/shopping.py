"""购物平台解析：淘宝店铺。

淘宝对游客收紧店铺商品列表（search.htm 强制登录跳转），策略分三级（按可用性自动降级）：
- 游客 HTTP：PC 店铺首页（view_shop.htm）可访问 → 店铺名/sellerId/shopId
- Cookie + 公开店铺 API：通过 mtop.taobao.shop.items 系列 API 拉商品列表（推荐）
- Cookie + Playwright：注入 Cookie 渲染 search.htm → 兜底

数据模型 ShopInfo（店铺）+ ShopProduct（商品）。
"""

import json
import re
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
    sales: str = ""      # "月销 12"
    pic: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "商品ID": self.item_id,
            "标题": self.title,
            "价格": self.price,
            "销量": self.sales,
            "图片": self.pic,
            "链接": self.url,
            "获取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


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


# ==================== URL 路由 ====================

def detect_shopping_platform(url: str):
    """识别购物链接 → (kind, key)。
    taobao_shop: {domain}.taobao.com/shop/view_shop.htm 或 shop{id}.taobao.com
    """
    url = (url or "").strip()
    if not url:
        return None, None
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
                         log=None) -> dict:
    """登录态 mtop 拉店铺商品（HTTP，绕过 secSDK，**绕过 headless 反爬**）。

    实测可用的接口优先级（先试成功率高的）：
    1. mtop.taobao.wsearch.appsearch + params{s:seller_id}   ★ 主路径，返回 data.itemsArray
    2. mtop.taobao.wireless.shop.getshoptabdata              ★ 移动版店铺 tab 数据（带 skuList/items）
    3. mtop.taobao.shop.items.search / mtop.taobao.shop.pc.search  ※ 旧版接口，现已 FAIL_SYS_API_NOT_FOUNDED

    返回 {"items":[...], "raw":..., "api":...} 或 {}。
    """
    tk, ck = _taobao_get_token(cookie)
    # 多套接口尝试（任一命中即返回）
    endpoints = []
    if seller_id:
        # 主路径：店铺内商品检索（空 q 即全店，等价于按店铺内全部商品获取）
        endpoints.append((
            "mtop.taobao.wsearch.appsearch", "1.0",
            {"params": json.dumps({"s": str(seller_id), "pageSize": 40,
                                   "pageNum": 1, "sort": "_sale"},
                                  separators=(",", ":"))}))
    if shop_id and seller_id:
        # 备路径：移动版店铺 tab 数据（含在售商品 + 店铺分组）
        endpoints.append((
            "mtop.taobao.wireless.shop.getshoptabdata", "1.0",
            {"shopId": str(shop_id), "sellerId": str(seller_id)}))
    if shop_id:
        # 备路径：PC 店铺搜索
        endpoints.append((
            "mtop.taobao.shop.pc.search", "1.0",
            {"shopId": str(shop_id), "pageSize": 20, "pageNum": 1}))
    if seller_id:
        # 备路径：店铺 items 搜索
        endpoints.append((
            "mtop.taobao.shop.items.search", "1.0",
            {"sellerId": str(seller_id), "shopId": str(shop_id),
             "pageSize": 20, "pageNum": 1}))

    for api, v, data in endpoints:
        try:
            if log:
                log(f"mtop: {api}")
            resp = _taobao_mtop(api, v, data, ck, tk)
            if not resp:
                continue
            ret = str(resp.get("ret") or "")
            if "SUCCESS" not in ret and "::OK" not in ret:
                if log:
                    log(f"  ↳ {ret[:60]}")
                continue
            data_resp = resp.get("data") or {}
            # 多路径扫描（移动版 itemsArray / PC 版 items / tab 数据 / result.items）
            items = (data_resp.get("itemsArray") or
                     data_resp.get("items") or
                     (data_resp.get("result") or {}).get("items") or
                     data_resp.get("itemList") or [])
            # getshoptabdata 是另一种结构（{tabs:[{sections:[{items:[...]}]}]}）
            if not items and api.endswith("getshoptabdata"):
                tabs = data_resp.get("tabs") or []
                for tab in tabs:
                    for sec in (tab.get("sections") or []):
                        if sec.get("items"):
                            items = sec["items"]
                            break
                    if items:
                        break
            if items:
                if log:
                    log(f"  ⮕ 命中 {len(items)} 件（API={api}）")
                return {"items": items, "raw": resp, "api": api}
        except Exception as e:
            if log:
                log(f"  异常: {e}")
            continue
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
    seller_id = shop_info.seller_id or ""

    # ★ 移动版优先：mobile 版淘宝对 headless 反爬更松，且 shop_index.htm 不强制登录跳转
    # ★ mtop 拦截的 PC 搜索页即使拿到 API 响应，也常因 secSDK 风控被强制登录
    candidates = [
        (f"https://shop.m.taobao.com/shop/shop_index.htm?shop_id={shop_id}", UA_M,
         "M+shop_index"),
        (f"https://h5api.m.taobao.com/awp/core/detail.htm?id={seller_id}", UA_M,
         "M+h5_detail"),
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
    if v in (None, ""):
        return ""
    s = str(v).strip()
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

    # 一级：mtop API
    try:
        result = _try_mtop_shop_items(
            shop_info.shop_id, shop_info.seller_id, cookie, log=log)
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
    - 经典 PC：price="¥25.00" 字符串、pic/picUrl、itemId
    - wsearch.appsearch（当前主路径）：price=数字、imageInfo=dict{imgUrl}、nidlong
    - getshoptabdata：priceShowWithIcon、extraParams
    """
    products = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # 商品 ID（多个名字兼容）
        iid = (it.get("itemId") or it.get("nid") or it.get("auctionId") or
               it.get("nidlong") or "")
        if not iid:
            continue
        iid = str(iid)
        # 标题（多个字段）
        title = (it.get("title") or it.get("itemTitle") or
                 it.get("shortTitle") or
                 it.get("short_title_c2c") or it.get("wfTwoLineTitle") or "").strip()
        if not title:
            # 部分接口把标题放在 extraParams 里
            ep = it.get("extraParams") or {}
            if isinstance(ep, dict):
                title = (ep.get("title") or ep.get("itemTitle") or "").strip()

        # 价格（wsearch.appsearch: price="198.00" 元单位字符串；
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

        # 销量（wsearch.appsearch: realSales="100+人付款" 直接采用）
        sales = ""
        rs = it.get("realSales")
        if isinstance(rs, str) and rs.strip():
            sales = rs.strip()
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
               it.get("mainPic") or "")
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
            url=f"https://item.taobao.com/item.htm?id={iid}"))
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