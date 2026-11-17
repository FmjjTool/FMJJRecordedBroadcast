package cn.fmjj.toolbox.engine.platform.shopping;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.browser.CdpClient;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.net.ServerSocket;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 购物平台解析：淘宝店铺 + 商品详情。
 * 移植自 Python 版 shopping.py 的主链路（游客店铺信息 / Cookie+mtop 商品列表 /
 * item.htm SSR + getdesc 商品详情 / 图片下载）；Playwright 兜底改为 CDP。
 */
public final class Shopping {

    public static final String UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
    public static final String UA_M = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
            + " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
    private static final String APPKEY = "12574478";
    private static final ObjectMapper MAPPER = JsonMapper.get();
    private static final DateTimeFormatter FETCHED_AT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final DateTimeFormatter FOLDER_TS = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss");

    // ==================== 数据模型 ====================

    public static final class ShopProduct {
        public String itemId = "";
        public String title = "";
        public String price = "";
        public String sales = "";
        public String pic = "";
        public String url = "";
        public String coupon = "";
        public String reservePrice = "";

        public ObjectNode toDict() {
            ObjectNode d = MAPPER.createObjectNode();
            put(d, "商品ID", itemId);
            put(d, "标题", title);
            put(d, "价格", price);
            put(d, "销量", sales);
            put(d, "图片", pic);
            put(d, "链接", url);
            d.put("获取时间", LocalDateTime.now().format(FETCHED_AT));
            if (!reservePrice.isEmpty() && !reservePrice.equals(price)) d.put("原价", reservePrice);
            if (!coupon.isEmpty()) d.put("优惠券", coupon);
            return d;
        }
    }

    public static final class ShopInfo {
        public String shopName = "";
        public String sellerId = "";
        public String shopId = "";
        public String url = "";

        public ObjectNode toDict() {
            ObjectNode d = MAPPER.createObjectNode();
            put(d, "店铺名", shopName);
            put(d, "卖家ID", sellerId);
            put(d, "店铺ID", shopId);
            put(d, "店铺链接", url);
            return d;
        }
    }

    public static final class ItemSku {
        public String skuId = "";
        public String names = "";
        public String price = "";
        public String quantity = "";
    }

    public static final class TaobaoItemDetail {
        public String itemId = "";
        public String title = "";
        public String price = "";
        public String sales = "";
        public String shopName = "";
        public String sellerId = "";
        public String shopId = "";
        public final List<String> mainImages = new ArrayList<>();
        public final List<ItemSku> skus = new ArrayList<>();
        public final List<ObjectNode> skuProps = new ArrayList<>();
        public final List<String[]> params = new ArrayList<>();
        public final List<String> descImages = new ArrayList<>();
        public final List<String> descText = new ArrayList<>();
        public String delivery = "";
        public String shopScores = "";
        public String url = "";
        public String fetchedAt = LocalDateTime.now().format(FETCHED_AT);
        public final List<String> localMain = new ArrayList<>();
        public final List<String> localSku = new ArrayList<>();
        public final List<String> localDesc = new ArrayList<>();
        public String imageDir = "";

        public ObjectNode toDict() {
            ObjectNode d = MAPPER.createObjectNode();
            put(d, "商品ID", itemId);
            put(d, "标题", title);
            put(d, "价格", price);
            put(d, "销量", sales);
            put(d, "店铺", shopName);
            putList(d, "主图", mainImages);
            ArrayNode props = d.putArray("规格参数");
            for (ObjectNode g : skuProps) {
                ObjectNode o = props.addObject();
                o.put("名称", g.path("name").asText("规格"));
                ArrayNode opts = o.putArray("选项");
                for (JsonNode v : g.path("values")) opts.add(v.path("name").asText(""));
            }
            if (props.isEmpty()) d.remove("规格参数");
            ArrayNode skusArr = d.putArray("选购参数");
            for (ItemSku s : skus) {
                ObjectNode o = skusArr.addObject();
                o.put("选项", s.names);
                o.put("价格", s.price);
                o.put("库存", s.quantity);
            }
            if (skusArr.isEmpty()) d.remove("选购参数");
            ArrayNode paramsArr = d.putArray("参数信息");
            for (String[] p : params) {
                ObjectNode o = paramsArr.addObject();
                o.put("名称", p[0]);
                o.put("值", p[1]);
            }
            if (paramsArr.isEmpty()) d.remove("参数信息");
            putList(d, "图文详情", descImages);
            put(d, "链接", url);
            put(d, "获取时间", fetchedAt);
            if (!delivery.isEmpty()) d.put("物流", delivery);
            if (!shopScores.isEmpty()) d.put("店铺评分", shopScores);
            if (!imageDir.isEmpty()) d.put("本地图片目录", imageDir);
            if (!localMain.isEmpty() || !localSku.isEmpty() || !localDesc.isEmpty()) {
                ObjectNode local = d.putObject("本地图片");
                putList(local, "主图", localMain);
                putList(local, "规格图", localSku);
                putList(local, "图文详情", localDesc);
            }
            return d;
        }
    }

    private final HttpClient http = new HttpClient();

    // ==================== URL 路由 ====================

    /** 识别购物链接 → {kind, key}；未识别返回 null。 */
    public static String[] detectShoppingPlatform(String url) {
        String s = url == null ? "" : url.strip();
        if (s.isEmpty()) return null;
        if (Pattern.compile("(?:item\\.taobao\\.com|detail\\.tmall\\.com)/item\\.htm").matcher(s).find()) {
            Matcher m = Pattern.compile("[?&]id=(\\d+)").matcher(s);
            if (m.find()) return new String[]{"taobao_item", m.group(1)};
        }
        Matcher m = Pattern.compile("https?://([\\w-]+)\\.taobao\\.com/shop/view_shop\\.htm").matcher(s);
        if (m.find()) return new String[]{"taobao_shop", m.group(1)};
        m = Pattern.compile("https?://shop(\\d+)\\.taobao\\.com").matcher(s);
        if (m.find()) return new String[]{"taobao_shop_id", m.group(1)};
        if (s.contains("taobao.com")) return new String[]{"taobao_unknown", ""};
        return null;
    }

    // ==================== 店铺信息（游客 HTTP） ====================

    public ShopInfo fetchShopInfo(String url) throws IOException, InterruptedException {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA_PC);
        h.put("Accept-Language", "zh-CN,zh;q=0.9");
        var resp = http.get(url, h);
        if (resp.statusCode() != 200) throw new IOException("淘宝店铺页请求失败（HTTP " + resp.statusCode() + "）");
        String html = resp.body();
        ShopInfo shop = new ShopInfo();
        shop.url = resp.uri().toString();
        Matcher m = Pattern.compile("<title>(?:首页-)?(.+?)(?:-淘宝网)?</title>").matcher(html);
        if (m.find()) shop.shopName = m.group(1).strip();
        m = Pattern.compile("sellerId[\"\\s:=]+[\"']?(\\d+)").matcher(html);
        if (m.find()) shop.sellerId = m.group(1);
        m = Pattern.compile("shopId[\"\\s:=]+[\"']?(\\d+)").matcher(html);
        if (m.find()) shop.shopId = m.group(1);
        if (shop.shopName.isEmpty()) {
            m = Pattern.compile("\"shopName\"\\s*:\\s*\"([^\"]{2,40})\"").matcher(html);
            if (m.find()) shop.shopName = m.group(1);
        }
        if (shop.sellerId.isEmpty() && shop.shopName.isEmpty()) {
            throw new IOException("淘宝店铺信息解析失败（页面结构变化或需要登录）");
        }
        return shop;
    }

    // ==================== 淘宝 mtop（Cookie + _m_h5_tk） ====================

    private record Token(String tk, Map<String, String> cookies) {}

    private static Map<String, String> cookieDict(String cookie) {
        Map<String, String> out = new LinkedHashMap<>();
        for (String pair : (cookie == null ? "" : cookie).split(";")) {
            int eq = pair.indexOf('=');
            if (eq > 0) out.put(pair.substring(0, eq).strip(), pair.substring(eq + 1).strip());
        }
        return out;
    }

    private static String cookieHeader(Map<String, String> cookies) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : cookies.entrySet()) {
            if (sb.length() > 0) sb.append("; ");
            sb.append(e.getKey()).append('=').append(e.getValue());
        }
        return sb.toString();
    }

    private Token taobaoGetToken(String cookie) {
        Map<String, String> c = cookieDict(cookie);
        try {
            String url = "https://h5api.m.taobao.com/h5/mtop.relationrecommend.wirelessrecommend.recommend/2.0/"
                    + "?appKey=" + APPKEY;
            Map<String, String> h = new LinkedHashMap<>();
            h.put("User-Agent", UA_M);
            h.put("Referer", "https://main.m.taobao.com/");
            if (!c.isEmpty()) h.put("Cookie", cookieHeader(c));
            collectCookies(http.get(url, h), c);
        } catch (Exception ignored) {
        }
        String token = c.getOrDefault("_m_h5_tk", "");
        return new Token(token.isEmpty() ? "" : token.split("_")[0], c);
    }

    private JsonNode taobaoMtop(String api, String v, ObjectNode data,
                                Map<String, String> cookies, String tk)
            throws IOException, InterruptedException {
        String dataStr = MAPPER.writeValueAsString(data);
        String t = String.valueOf(System.currentTimeMillis());
        String sign = md5((tk == null ? "" : tk) + "&" + t + "&" + APPKEY + "&" + dataStr);
        String url = "https://h5api.m.taobao.com/h5/" + api + "/" + v + "/?jsv=2.5.1&appKey=" + APPKEY
                + "&t=" + t + "&sign=" + sign + "&api=" + api + "&v=" + v
                + "&type=json&dataType=json&data=" + URLEncoder.encode(dataStr, StandardCharsets.UTF_8);
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA_M);
        h.put("Referer", "https://main.m.taobao.com/");
        if (!cookies.isEmpty()) h.put("Cookie", cookieHeader(cookies));
        var resp = http.get(url, h);
        collectCookies(resp, cookies);
        try {
            return HttpClient.parseJson(resp.body());
        } catch (Exception e) {
            return MAPPER.createObjectNode();
        }
    }

    private static void collectCookies(HttpResponse<String> resp, Map<String, String> jar) {
        for (String c : resp.headers().allValues("Set-Cookie")) {
            int semi = c.indexOf(';');
            String pair = semi >= 0 ? c.substring(0, semi) : c;
            int eq = pair.indexOf('=');
            if (eq > 0) jar.put(pair.substring(0, eq).strip(), pair.substring(eq + 1).strip());
        }
    }

    private static String md5(String s) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            StringBuilder sb = new StringBuilder();
            for (byte b : md.digest(s.getBytes(StandardCharsets.UTF_8))) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    // ==================== 商品列表 ====================

    /** 登录态 mtop 拉店铺商品（wandering.get，按 userId 精确返回在售商品）。 */
    public List<JsonNode> tryMtopShopItems(String sellerId, String cookie, int count,
                                           Consumer<String> log) {
        if (sellerId == null || sellerId.isEmpty()) return List.of();
        long sid;
        try {
            sid = Long.parseLong(sellerId);
        } catch (Exception e) {
            return List.of();
        }
        Token token = taobaoGetToken(cookie);
        String api = "mtop.taobao.wireless.shop.index.wandering.get";
        List<JsonNode> raw = new ArrayList<>();
        int curPage = 1;
        long preItemId = 0;
        int pageFail = 0;
        while (raw.size() < count && curPage <= 10) {
            if (log != null) log.accept("mtop wandering 第 " + curPage + " 页 ...");
            PageResult page = mtopPage(api, token, sid, curPage, preItemId, log);
            if (!page.ok || page.items.isEmpty()) {
                if (!raw.isEmpty()) break;
                pageFail++;
                if (pageFail > 2) break;
                if (log != null) log.accept("  首页为空/失败，等待 6 秒重试（" + pageFail + "/2）...");
                sleep(6000);
                continue;
            }
            raw.addAll(page.items);
            if (log != null) log.accept("  +" + page.items.size() + " 件（累计 " + raw.size() + "）");
            String lastId = page.items.get(page.items.size() - 1).path("itemId").asText("0");
            try {
                preItemId = (long) Double.parseDouble(lastId);
            } catch (Exception e) {
                break;
            }
            if (!"true".equals(page.hasNext)) break;
            curPage++;
            if (raw.size() < count) sleep(1500);
        }
        if (!raw.isEmpty() && raw.size() < 20) {
            if (log != null) log.accept("  首页仅 " + raw.size() + " 件（<20），3 秒后补拉一轮合并去重 ...");
            sleep(3000);
            PageResult extra = mtopPage(api, token, sid, 1, 0, log);
            if (extra.ok) {
                Set<String> seen = new LinkedHashSet<>();
                for (JsonNode it : raw) seen.add(normalizeItemId(it.path("itemId").asText("")));
                int added = 0;
                for (JsonNode it : extra.items) {
                    String iid = normalizeItemId(it.path("itemId").asText(""));
                    if (!iid.isEmpty() && seen.add(iid)) {
                        raw.add(it);
                        added++;
                    }
                }
                if (log != null && added > 0) log.accept("  补拉合并 +" + added + " 件（共 " + raw.size() + "）");
            }
        }
        return raw.size() > count ? raw.subList(0, count) : raw;
    }

    private record PageResult(List<JsonNode> items, String hasNext, boolean ok) {}

    private PageResult mtopPage(String api, Token token, long sid, int page, long preItemId,
                                Consumer<String> log) {
        ObjectNode data = MAPPER.createObjectNode();
        data.put("direction", 1);
        data.put("userId", sid);
        data.put("pageSize", 20);
        data.put("needPreNew", true);
        data.put("id", 0);
        data.put("curPage", page);
        data.put("preItemId", preItemId);
        try {
            JsonNode resp = taobaoMtop(api, "1.0", data, token.cookies(), token.tk());
            String ret = resp.path("ret").toString();
            if (!ret.contains("SUCCESS")) {
                if (log != null) log.accept("  wandering 第" + page + "页 ret=" + trunc(ret, 50));
                return new PageResult(List.of(), "", false);
            }
            JsonNode d = resp.path("data");
            List<JsonNode> items = new ArrayList<>();
            for (JsonNode b : d.path("result")) {
                if (b.isObject() && "item".equals(b.path("blockType").asText(""))
                        && b.path("blockContent").isObject()) {
                    items.add(b.path("blockContent"));
                }
            }
            return new PageResult(items, d.path("hasNext").asText("").toLowerCase(Locale.ROOT), true);
        } catch (Exception e) {
            if (log != null) log.accept("  mtop 异常: " + trunc(e.getMessage(), 60));
            return new PageResult(List.of(), "", false);
        }
    }

    /** mtop / Playwright 兜底返回的商品字段 → ShopProduct。 */
    public static List<ShopProduct> productsFromMtopItems(List<JsonNode> items) {
        List<ShopProduct> products = new ArrayList<>();
        for (JsonNode it : items) {
            if (!it.isObject()) continue;
            String iid = normalizeItemId(firstText(it, "itemId", "itemIdStr", "nid", "auctionId", "nidlong"));
            if (iid.isEmpty()) continue;
            String title = firstText(it, "title", "itemTitle", "shortTitle", "short_title_c2c", "wfTwoLineTitle");
            if (title.isEmpty()) title = firstText(it.path("extraParams"), "title", "itemTitle");
            JsonNode rawPrice = it.has("price") ? it.get("price")
                    : it.has("reservePrice") ? it.get("reservePrice")
                    : it.has("priceShow") ? it.get("priceShow") : it.get("priceShowWithIcon");
            if (rawPrice != null && rawPrice.isObject()) {
                rawPrice = rawPrice.has("price") ? rawPrice.get("price") : rawPrice.get("originPrice");
            }
            String price = "";
            double p = parseNum(rawPrice);
            if (p > 0) price = "¥" + fmtNum(p);
            else price = fmtPrice(rawPrice == null ? "" : rawPrice.asText(""));
            String reserve = "";
            JsonNode rp = it.get("reservePrice");
            if (rp != null) {
                if (rp.isObject()) rp = rp.get("price");
                double rpf = parseNum(rp);
                if (rpf > 0 && !("¥" + fmtNum(rpf)).equals(price)) reserve = "¥" + fmtNum(rpf);
            }
            String coupon = it.path("couponValue").asText("").strip();
            String sales = "";
            for (String k : List.of("realSales", "vagueTotalSoldQuantity")) {
                String v = it.path(k).asText("").strip();
                if (!v.isEmpty()) {
                    sales = v;
                    break;
                }
            }
            if (sales.isEmpty()) {
                JsonNode soldRaw = it.has("sold") ? it.get("sold")
                        : it.has("soldQuantity") ? it.get("soldQuantity")
                        : it.has("monthSold") ? it.get("monthSold") : it.get("sale_count");
                if (soldRaw == null) sales = "";
                else {
                    String s = soldRaw.asText("");
                    try {
                        long n = (long) Double.parseDouble(s);
                        sales = n > 0 ? "月销 " + n : "";
                    } catch (Exception e) {
                        sales = fmtSales(s);
                    }
                }
            }
            String pic = firstText(it, "pic", "picUrl", "img", "itemPic", "mainPic");
            if (pic.isEmpty()) {
                JsonNode ii = it.path("imageInfo");
                if (ii.isArray() && !ii.isEmpty()) pic = firstText(ii.get(0), "imageUrl", "imgUrl");
                else if (ii.isObject()) pic = firstText(ii, "imageUrl", "imgUrl", "url");
            }
            if (pic.isEmpty()) pic = it.path("pic_path").asText("");
            if (!pic.isEmpty()) {
                pic = pic.replaceAll("_\\d+x\\d+\\.(jpe?g|png|webp)$", ".$1");
                if (pic.startsWith("//")) pic = "https:" + pic;
            }
            ShopProduct pr = new ShopProduct();
            pr.itemId = iid;
            pr.title = trunc(title.replaceAll("<[^>]+>", ""), 120);
            pr.price = price;
            pr.sales = sales;
            pr.pic = pic;
            pr.url = "https://item.taobao.com/item.htm?id=" + iid;
            pr.coupon = coupon;
            pr.reservePrice = reserve;
            products.add(pr);
        }
        return products;
    }

    private static String normalizeItemId(String raw) {
        String s = raw == null ? "" : raw.strip();
        if (s.matches("-?\\d+\\.0+")) s = s.substring(0, s.indexOf('.'));
        return s.matches("\\d+") ? s : "";
    }

    /** 三级降级拿商品列表：mtop → CDP 店铺页兜底。 */
    public List<ShopProduct> fetchShopProducts(ShopInfo shop, String cookie, int count,
                                               Consumer<String> log) throws IOException {
        if (cookie == null || cookie.isBlank()) {
            throw new IOException("淘宝商品列表需要登录：请在 Cookie 管理中添加淘宝 Cookie");
        }
        List<JsonNode> raw = tryMtopShopItems(shop.sellerId, cookie, count, log);
        if (!raw.isEmpty()) return productsFromMtopItems(raw);
        try {
            List<ShopProduct> products = cdpShopProducts(shop, cookie, log);
            if (!products.isEmpty()) return products;
            throw new IOException("CDP 未解析到商品");
        } catch (IOException e) {
            throw new IOException("未解析到商品：" + e.getMessage()
                    + "。请确认 Cookie 含 cookie2/_tb_token_/isg，且浏览器仍登录");
        } catch (Exception e) {
            throw new IOException("未解析到商品：" + e.getMessage(), e);
        }
    }

    /** CDP 兜底：移动店铺页 / PC 搜索页，拦截 mtop 响应递归收割商品。 */
    private List<ShopProduct> cdpShopProducts(ShopInfo shop, String cookie,
                                              Consumer<String> log) throws Exception {
        String[] candidates = {
                "https://shop.m.taobao.com/shop/shop_index.htm?shop_id=" + shop.shopId,
                shop.url + (shop.url.contains("?") ? "&" : "?") + "orderType=hotsell"
        };
        for (String url : candidates) {
            boolean mobile = url.contains("shop.m.taobao.com");
            List<ShopProduct> products = cdpShopOnce(url, cookie, mobile, log);
            if (!products.isEmpty()) return products;
        }
        return List.of();
    }

    private List<ShopProduct> cdpShopOnce(String url, String cookie, boolean mobile,
                                          Consumer<String> log) throws Exception {
        Map<String, ObjectNode> harvested = new LinkedHashMap<>();
        int port;
        try (ServerSocket s = new ServerSocket(0)) {
            port = s.getLocalPort();
        }
        try (CdpClient browser = CdpClient.launch(port, 25)) {
            ObjectNode ua = MAPPER.createObjectNode();
            ua.put("userAgent", mobile ? UA_M : UA_PC);
            ua.put("acceptLanguage", "zh-CN,zh;q=0.9");
            ua.put("platform", mobile ? "iPhone" : "Win32");
            browser.call("Emulation.setUserAgentOverride", ua, 10);
            ObjectNode metrics = MAPPER.createObjectNode();
            metrics.put("width", mobile ? 414 : 1400);
            metrics.put("height", mobile ? 896 : 900);
            metrics.put("deviceScaleFactor", 1);
            metrics.put("mobile", mobile);
            browser.call("Emulation.setDeviceMetricsOverride", metrics, 10);
            browser.call("Network.enable", null, 10);
            injectCookies(browser, cookie, "https://shop.m.taobao.com/");
            List<String> requestIds = new CopyOnWriteArrayList<>();
            CdpClient.EventListener listener = (method, params) -> {
                if (!"Network.responseReceived".equals(method)) return;
                String u = params.path("response").path("url").asText("");
                if (!u.contains("mtop") && !u.contains("h5api")) return;
                String rid = params.path("requestId").asText("");
                if (!rid.isEmpty()) requestIds.add(rid);
            };
            browser.addEventListener(listener);
            ObjectNode nav = MAPPER.createObjectNode();
            nav.put("url", url);
            browser.send("Page.navigate", nav);
            Thread.sleep(8000);
            try {
                browser.evalString("window.scrollTo(0,1200); String(window.scrollY)");
            } catch (Exception ignored) {
            }
            Thread.sleep(3000);
            for (String rid : requestIds) {
                try {
                    byte[] body = browser.responseBody(rid);
                    JsonNode d = MAPPER.readTree(new String(body, StandardCharsets.UTF_8));
                    String ret = d.path("ret").toString();
                    if (ret.contains("SUCCESS") || ret.contains("::OK")) {
                        harvestProduct(d.path("data"), harvested, 0);
                    }
                } catch (Exception ignored) {
                }
            }
        }
        List<JsonNode> items = new ArrayList<>(harvested.values());
        if (log != null) log.accept("CDP 收割到 " + items.size() + " 件商品");
        return productsFromHarvested(items);
    }

    private static void harvestProduct(JsonNode obj, Map<String, ObjectNode> items, int depth) {
        if (depth > 8 || obj == null || obj.isNull() || obj.isMissingNode()) return;
        if (obj.isObject()) {
            String iid = "";
            for (String k : List.of("itemId", "nid", "auctionId", "item_id")) {
                String v = obj.path(k).asText("");
                if (v.matches("-?\\d+")) {
                    iid = v;
                    break;
                }
            }
            if (!iid.isEmpty() && (obj.has("title") || obj.has("itemTitle") || obj.has("price"))) {
                ObjectNode cur = items.computeIfAbsent(iid, k -> MAPPER.createObjectNode());
                copyFirst(obj, cur, "title", "title");
                copyFirst(obj, cur, "itemTitle", "title");
                copyFirst(obj, cur, "price", "price");
                copyFirst(obj, cur, "reservePrice", "price");
                for (String k : List.of("sold", "soldQuantity", "monthSold", "month_sold",
                        "realSales", "vagueTotalSoldQuantity")) {
                    copyFirst(obj, cur, k, "sales");
                }
                copyFirst(obj, cur, "couponValue", "coupon");
                for (String k : List.of("pic", "picUrl", "img", "mainPic", "itemPic", "pic_path", "imageUrl")) {
                    copyFirst(obj, cur, k, "pic");
                }
            }
            obj.fields().forEachRemaining(e -> harvestProduct(e.getValue(), items, depth + 1));
        } else if (obj.isArray()) {
            for (JsonNode v : obj) harvestProduct(v, items, depth + 1);
        }
    }

    private static void copyFirst(JsonNode src, ObjectNode dst, String srcKey, String dstKey) {
        JsonNode v = src.get(srcKey);
        if (v == null || v.isNull()) return;
        String s = v.isValueNode() ? v.asText("") : v.toString();
        if (!s.isEmpty() && dst.path(dstKey).asText("").isEmpty()) dst.put(dstKey, s);
    }

    private static List<ShopProduct> productsFromHarvested(List<JsonNode> items) {
        List<ShopProduct> products = new ArrayList<>();
        for (JsonNode it : items) {
            String iid = normalizeItemId(it.path("itemId").asText(""));
            if (iid.isEmpty()) continue;
            ShopProduct pr = new ShopProduct();
            pr.itemId = iid;
            pr.title = trunc(it.path("title").asText(""), 120);
            double p = parseNum(it.path("price"));
            if (p > 0) pr.price = "¥" + fmtNum(p);
            pr.sales = it.path("sales").asText("");
            String pic = it.path("pic").asText("");
            if (pic.startsWith("//")) pic = "https:" + pic;
            pr.pic = pic;
            pr.coupon = it.path("coupon").asText("");
            pr.url = "https://item.taobao.com/item.htm?id=" + iid;
            products.add(pr);
        }
        return products;
    }

    public record ShopResult(ShopInfo shop, List<ShopProduct> products) {}

    /** 购物链接 → (店铺, 商品列表)；无 Cookie 时只返回店铺信息。 */
    public ShopResult parseShoppingUrl(String url, String cookie, int count, Consumer<String> log)
            throws IOException, InterruptedException {
        String[] kind = detectShoppingPlatform(url);
        if (kind == null) throw new IOException("无法识别的购物链接（当前支持淘宝店铺）");
        if ("taobao_unknown".equals(kind[0])) {
            throw new IOException("淘宝链接无法定位店铺（请提供 shop/view_shop.htm 或 shop{id}.taobao.com 链接）");
        }
        ShopInfo info = fetchShopInfo(url);
        if (cookie == null || cookie.isBlank()) return new ShopResult(info, List.of());
        return new ShopResult(info, fetchShopProducts(info, cookie, count, log));
    }

    // ==================== 商品详情（SSR + getdesc） ====================

    /** 从 item.htm HTML 提取 window.__ICE_APP_CONTEXT__ 的 SSR JSON。 */
    static ObjectNode extractIceSsr(String html) {
        if (html == null) return null;
        Matcher m = Pattern.compile("var b = (\\{)").matcher(html);
        if (!m.find()) return null;
        int start = m.start(1);
        int depth = 0;
        char inStr = 0;
        boolean esc = false;
        int end = Math.min(html.length(), start + 500_000);
        for (int i = start; i < end; i++) {
            char ch = html.charAt(i);
            if (inStr != 0) {
                if (esc) esc = false;
                else if (ch == '\\') esc = true;
                else if (ch == inStr) inStr = 0;
                continue;
            }
            if (ch == '"' || ch == '\'') inStr = ch;
            else if (ch == '{') depth++;
            else if (ch == '}') {
                depth--;
                if (depth == 0) {
                    try {
                        JsonNode n = MAPPER.readTree(html.substring(start, i + 1));
                        return n.isObject() ? (ObjectNode) n : null;
                    } catch (Exception e) {
                        return null;
                    }
                }
            }
        }
        return null;
    }

    /** SSR JSON → loaderData.home.data.res。 */
    static ObjectNode ssrRes(JsonNode data) {
        JsonNode res = data.path("loaderData").path("home").path("data").path("res");
        return res.isObject() ? (ObjectNode) res : null;
    }

    static TaobaoItemDetail detailFromSsr(String itemId, JsonNode res) {
        TaobaoItemDetail d = new TaobaoItemDetail();
        d.itemId = itemId;
        d.url = "https://item.taobao.com/item.htm?id=" + itemId;
        JsonNode item = res.path("item");
        JsonNode seller = res.path("seller");
        JsonNode comp = res.path("componentsVO");
        d.title = item.path("title").asText("").strip();
        for (JsonNode u : item.path("images")) if (u.isTextual()) d.mainImages.add(u.asText());
        String cnt = item.path("vagueSellCount").asText("");
        d.sales = cnt.strip().isEmpty() ? "" : fmtSales(cnt);
        d.shopName = seller.path("shopName").asText("").strip();
        d.sellerId = seller.path("sellerId").asText("").strip();
        d.shopId = seller.path("shopId").asText("").strip();

        JsonNode pv = comp.path("priceVO").path("price");
        String pt = pv.path("priceText").asText("").strip();
        if (!pt.isEmpty()) {
            d.price = "¥" + pt;
            String desc = pv.path("priceDesc").asText("").strip();
            if (!desc.isEmpty()) d.price += " " + desc;
        }
        JsonNode dv = comp.path("deliveryVO");
        List<String> parts = new ArrayList<>();
        addIf(parts, dv.path("agingDesc").asText(""));
        addIf(parts, dv.path("freight").asText(""));
        String from = dv.path("deliveryFromAddr").asText("");
        if (!from.isEmpty()) parts.add("发货地 " + from);
        d.delivery = String.join(" | ", parts);

        List<String> scores = new ArrayList<>();
        for (JsonNode e : seller.path("evaluates")) {
            if (!e.isObject()) continue;
            scores.add(e.path("title").asText("") + e.path("score").asText("").strip()
                    + e.path("levelText").asText(""));
        }
        d.shopScores = String.join(" / ", scores);

        JsonNode sb = res.path("skuBase");
        JsonNode sc = res.path("skuCore");
        JsonNode s2i = sc.path("sku2info");
        Map<String, String> vidName = new LinkedHashMap<>();
        for (JsonNode p : sb.path("props")) {
            if (!p.isObject()) continue;
            ArrayNode values = MAPPER.createArrayNode();
            for (JsonNode v : p.path("values")) {
                if (!v.isObject()) continue;
                String img = v.path("image").asText("");
                if (img.startsWith("//")) img = "https:" + img;
                ObjectNode vo = MAPPER.createObjectNode();
                vo.put("name", v.path("name").asText(""));
                vo.put("image", img);
                values.add(vo);
                vidName.put(v.path("vid").asText(""), v.path("name").asText(""));
            }
            if (!values.isEmpty()) {
                ObjectNode grp = MAPPER.createObjectNode();
                grp.put("name", p.path("name").asText("规格"));
                grp.set("values", values);
                d.skuProps.add(grp);
            }
        }
        String defaultPrice = s2i.path("0").path("price").path("priceText").asText("").strip();
        for (JsonNode s : sb.path("skus")) {
            if (!s.isObject()) continue;
            String skuId = s.path("skuId").asText("");
            String propPath = s.path("propPath").asText("");
            List<String> names = new ArrayList<>();
            for (String pair : propPath.split(";")) {
                String vid = pair.contains(":") ? pair.substring(pair.lastIndexOf(':') + 1) : "";
                if (!vid.isEmpty() && !"-1".equals(vid) && vidName.containsKey(vid)) {
                    names.add(vidName.get(vid));
                }
            }
            JsonNode info = s2i.path(skuId);
            String price = info.path("price").path("priceText").asText("").strip();
            if (price.isEmpty()) price = defaultPrice;
            String qty = info.path("quantity").asText("").strip();
            ItemSku sku = new ItemSku();
            sku.skuId = skuId;
            sku.names = names.isEmpty() ? "默认" : String.join(" / ", names);
            sku.price = price.isEmpty() ? "" : "¥" + price;
            sku.quantity = qty;
            d.skus.add(sku);
        }
        if (d.skus.isEmpty()) {
            String price = sc.path("skuItem").path("infoVO").path("price").path("priceText")
                    .asText("").strip();
            if (price.isEmpty()) price = defaultPrice;
            ItemSku sku = new ItemSku();
            sku.skuId = "0";
            sku.names = "默认";
            sku.price = price.isEmpty() ? "" : "¥" + price;
            d.skus.add(sku);
        }

        JsonNode ip = res.path("plusViewVO").path("industryParamVO");
        for (String key : List.of("enhanceParamList", "basicParamList")) {
            for (JsonNode p : ip.path(key)) {
                if (p.isObject() && !p.path("propertyName").asText("").isEmpty()) {
                    d.params.add(new String[]{p.path("propertyName").asText(""),
                            p.path("valueName").asText("")});
                }
            }
        }
        return d;
    }

    /** mtop.taobao.detail.getdesc 6.0 → 参数信息 + 图文详情长图。 */
    private DescResult tryGetdesc(String itemId, String cookie, Consumer<String> log) {
        Token token = taobaoGetToken(cookie);
        try {
            ObjectNode data = MAPPER.createObjectNode();
            data.put("id", itemId);
            data.put("type", "1");
            JsonNode resp = taobaoMtop("mtop.taobao.detail.getdesc", "6.0", data,
                    token.cookies(), token.tk());
            String ret = resp.path("ret").toString();
            if (!ret.contains("SUCCESS")) {
                if (log != null) log.accept("getdesc 失败: " + trunc(ret, 60));
                return null;
            }
            JsonNode d = resp.path("data");
            DescResult out = new DescResult();
            for (JsonNode p : d.path("itemProperties")) {
                if (p.isObject() && !p.path("name").asText("").isEmpty()) {
                    out.params.add(new String[]{p.path("name").asText(""), p.path("value").asText("")});
                }
            }
            String pc = d.path("pcDescContent").asText("");
            if (!pc.isEmpty()) {
                Matcher im = Pattern.compile("<img[^>]+src=[\"']([^\"']+)[\"']").matcher(pc);
                while (im.find()) {
                    String u = im.group(1).strip();
                    if (u.startsWith("//")) u = "https:" + u;
                    if (!u.isEmpty()) out.descImages.add(u);
                }
                Matcher tm = Pattern.compile(">([^<>]{4,})<").matcher(pc);
                while (tm.find()) {
                    String t = tm.group(1).replaceAll("\\s+", " ").strip();
                    if (!t.isEmpty() && !out.descText.contains(t)) out.descText.add(t);
                }
            }
            return out;
        } catch (Exception e) {
            if (log != null) log.accept("getdesc 异常: " + trunc(e.getMessage(), 60));
            return null;
        }
    }

    private static final class DescResult {
        final List<String[]> params = new ArrayList<>();
        final List<String> descImages = new ArrayList<>();
        final List<String> descText = new ArrayList<>();
    }

    /** 商品详情：Cookie HTTP SSR → CDP SSR → getdesc 补图文详情。 */
    public TaobaoItemDetail fetchItemDetail(String itemId, String cookie, Consumer<String> log)
            throws IOException {
        if (itemId == null || !itemId.matches("\\d+")) throw new IOException("无效的淘宝商品ID：" + itemId);
        if (cookie == null || cookie.isBlank()) {
            throw new IOException("淘宝商品详情需要登录：请在 Cookie 管理中添加淘宝 Cookie");
        }
        ObjectNode ssr = null;
        try {
            if (log != null) log.accept("请求商品页 SSR ...");
            Map<String, String> h = new LinkedHashMap<>();
            h.put("User-Agent", UA_PC);
            h.put("Accept-Language", "zh-CN,zh;q=0.9");
            h.put("Cookie", cookie);
            var resp = http.get("https://item.taobao.com/item.htm?id=" + itemId, h);
            ssr = extractIceSsr(resp.body());
        } catch (Exception e) {
            if (log != null) log.accept("SSR 请求失败: " + trunc(e.getMessage(), 60));
        }
        if (ssr == null || ssrRes(ssr) == null) {
            if (log != null) log.accept("HTTP 通道未拿到数据，转 CDP ...");
            try {
                ssr = cdpItemSsr(itemId, cookie);
            } catch (Exception e) {
                if (log != null) log.accept("CDP 失败: " + trunc(e.getMessage(), 60));
            }
        }
        ObjectNode res = ssr == null ? null : ssrRes(ssr);
        if (res == null) {
            throw new IOException("商品页数据获取失败：Cookie 可能失效（请重新登录淘宝），或触发风控稍后再试");
        }
        TaobaoItemDetail detail = detailFromSsr(itemId, res);
        if (detail.title.isEmpty()) throw new IOException("商品已下架或页面结构变化（SSR 无标题）");
        if (log != null) log.accept("请求图文详情（getdesc）...");
        DescResult gd = tryGetdesc(itemId, cookie, log);
        if (gd != null) {
            List<String[]> merged = new ArrayList<>(gd.params);
            Set<String> seen = new LinkedHashSet<>();
            for (String[] p : merged) seen.add(p[0]);
            for (String[] p : detail.params) {
                if (!seen.contains(p[0])) merged.add(p);
            }
            detail.params.clear();
            detail.params.addAll(merged);
            detail.descImages.clear();
            detail.descImages.addAll(gd.descImages);
            detail.descText.clear();
            detail.descText.addAll(gd.descText);
        }
        if (log != null) {
            log.accept("主图 " + detail.mainImages.size() + " 张 | SKU " + detail.skus.size()
                    + " 档 | 参数 " + detail.params.size() + " 项 | 详情图 "
                    + detail.descImages.size() + " 张");
        }
        return detail;
    }

    private ObjectNode cdpItemSsr(String itemId, String cookie) throws Exception {
        int port;
        try (ServerSocket s = new ServerSocket(0)) {
            port = s.getLocalPort();
        }
        try (CdpClient browser = CdpClient.launch(port, 25)) {
            ObjectNode ua = MAPPER.createObjectNode();
            ua.put("userAgent", UA_PC);
            ua.put("acceptLanguage", "zh-CN,zh;q=0.9");
            ua.put("platform", "Win32");
            browser.call("Emulation.setUserAgentOverride", ua, 10);
            ObjectNode metrics = MAPPER.createObjectNode();
            metrics.put("width", 1280);
            metrics.put("height", 900);
            metrics.put("deviceScaleFactor", 1);
            metrics.put("mobile", false);
            browser.call("Emulation.setDeviceMetricsOverride", metrics, 10);
            browser.call("Network.enable", null, 10);
            injectCookies(browser, cookie, "https://item.taobao.com/");
            ObjectNode nav = MAPPER.createObjectNode();
            nav.put("url", "https://item.taobao.com/item.htm?id=" + itemId);
            browser.send("Page.navigate", nav);
            Thread.sleep(6000);
            String html = browser.evalString("document.documentElement.outerHTML");
            return extractIceSsr(html);
        }
    }

    private void injectCookies(CdpClient browser, String cookie, String url) {
        if (cookie == null || cookie.isBlank()) return;
        for (String pair : cookie.split(";")) {
            int eq = pair.indexOf('=');
            if (eq <= 0) continue;
            ObjectNode p = MAPPER.createObjectNode();
            p.put("name", pair.substring(0, eq).strip());
            p.put("value", pair.substring(eq + 1).strip());
            p.put("domain", ".taobao.com");
            p.put("path", "/");
            try {
                browser.call("Network.setCookie", p, 10);
            } catch (Exception ignored) {
            }
        }
    }

    public TaobaoItemDetail parseTaobaoItemUrl(String url, String cookie, Consumer<String> log)
            throws IOException {
        String[] kind = detectShoppingPlatform(url);
        if (kind == null || !"taobao_item".equals(kind[0]) || kind[1].isEmpty()) {
            throw new IOException("请输入淘宝商品链接（item.taobao.com/item.htm?id=xxx）");
        }
        return fetchItemDetail(kind[1], cookie, log);
    }

    // ==================== 图片下载 ====================

    public void downloadItemImages(TaobaoItemDetail detail, Path baseDir, Consumer<String> log) {
        String ts = LocalDateTime.now().format(FOLDER_TS);
        Path base = baseDir.resolve(safeFolderName(detail.title, 40) + "_" + detail.itemId + "_" + ts);
        if (log != null) log.accept("下载图片到 " + base + " ...");
        detail.localMain.clear();
        detail.localMain.addAll(downloadImages(detail.mainImages, base.resolve("main"), "主图", log));
        List<String> skuUrls = new ArrayList<>();
        for (ObjectNode grp : detail.skuProps) {
            for (JsonNode v : grp.path("values")) {
                String u = v.path("image").asText("");
                if (!u.isEmpty()) skuUrls.add(u);
            }
        }
        detail.localSku.clear();
        detail.localSku.addAll(downloadImages(skuUrls, base.resolve("sku"), "规格", log));
        detail.localDesc.clear();
        detail.localDesc.addAll(downloadImages(detail.descImages, base.resolve("desc"), "详情", log));
        if (!detail.localMain.isEmpty() || !detail.localSku.isEmpty() || !detail.localDesc.isEmpty()) {
            detail.imageDir = base.toString();
        }
        if (log != null) {
            log.accept("图片下载完成：主图 " + detail.localMain.size() + " / 规格 "
                    + detail.localSku.size() + " / 详情 " + detail.localDesc.size());
        }
    }

    private List<String> downloadImages(List<String> urls, Path folder, String prefix,
                                        Consumer<String> log) {
        List<String> out = new ArrayList<>();
        if (urls == null || urls.isEmpty()) return out;
        try {
            Files.createDirectories(folder);
        } catch (Exception ignored) {
        }
        List<String> urlsFiltered = new ArrayList<>();
        for (String u : urls) if (u != null && !u.isEmpty()) urlsFiltered.add(u);
        ExecutorService pool = Executors.newFixedThreadPool(Math.min(6, Math.max(1, urlsFiltered.size())));
        List<Future<Boolean>> futures = new ArrayList<>();
        List<Path> paths = new ArrayList<>();
        for (int i = 0; i < urlsFiltered.size(); i++) {
            String u = urlsFiltered.get(i);
            if (u.startsWith("//")) u = "https:" + u;
            Path path = folder.resolve(prefix + String.format("%02d", i + 1) + imgExt(u));
            paths.add(path);
            final String url = u;
            futures.add(pool.submit(() -> {
                try {
                    Files.write(path, downloadBytes(url, "https://item.taobao.com/"));
                    return true;
                } catch (Exception e) {
                    if (log != null) log.accept("图片下载失败 " + tail(url, 40) + ": "
                            + trunc(e.getMessage(), 40));
                    return false;
                }
            }));
        }
        pool.shutdown();
        for (int i = 0; i < futures.size(); i++) {
            try {
                if (futures.get(i).get()) out.add(paths.get(i).toString());
            } catch (Exception ignored) {
            }
        }
        return out;
    }

    private byte[] downloadBytes(String url, String referer) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url))
                .timeout(java.time.Duration.ofSeconds(30))
                .header("User-Agent", UA_PC).GET();
        if (referer != null && !referer.isEmpty()) b.header("Referer", referer);
        return http.jdkClient().send(b.build(), HttpResponse.BodyHandlers.ofByteArray()).body();
    }

    public static Path defaultItemXlsxPath(TaobaoItemDetail detail, Path baseDir) {
        if (!detail.imageDir.isEmpty()) {
            Path base = Path.of(detail.imageDir);
            if (Files.isDirectory(base)) return base.resolve(base.getFileName() + ".xlsx");
        }
        return baseDir.resolve(safeFolderName(detail.title, 40) + "_" + detail.itemId + ".xlsx");
    }

    // ==================== 格式化 ====================

    static String fmtPrice(Object v) {
        if (v == null || String.valueOf(v).isEmpty()) return "";
        Matcher m = Pattern.compile("(\\d+(?:\\.\\d+)?)").matcher(String.valueOf(v));
        return m.find() ? "¥" + m.group(1) : "";
    }

    static String fmtSales(Object v) {
        if (v == null || String.valueOf(v).strip().isEmpty()) return "";
        String s = String.valueOf(v).strip();
        if (s.contains("万") || s.endsWith("+")) {
            return s.startsWith("月销") || s.startsWith("已售") ? s : "已售 " + s;
        }
        if (s.contains("+")) return s;
        Matcher m = Pattern.compile("(\\d+)").matcher(s);
        return m.find() ? "月销 " + m.group(1) : "";
    }

    static String safeFolderName(String s, int maxlen) {
        String cleaned = (s == null ? "" : s).replaceAll("[\\\\/:*?\"<>|\\r\\n\\t]", "")
                .strip().replaceAll("^[._\\s]+|[._\\s]+$", "");
        if (cleaned.isEmpty()) return "未命名";
        return cleaned.length() > maxlen ? cleaned.substring(0, maxlen) : cleaned;
    }

    static String imgExt(String url) {
        Matcher m = Pattern.compile("\\.(jpe?g|png|webp|gif)(?:[?~]|$)", Pattern.CASE_INSENSITIVE)
                .matcher(url);
        if (!m.find()) return ".jpg";
        String ext = m.group(1).toLowerCase(Locale.ROOT);
        return "." + ("jpeg".equals(ext) ? "jpg" : ext);
    }

    static String fmtNum(double v) {
        if (v == Math.rint(v)) return String.valueOf((long) v);
        return String.format(Locale.ROOT, "%.2f", v).replaceAll("0+$", "").replaceAll("\\.$", "");
    }

    static double parseNum(JsonNode v) {
        if (v == null || v.isNull() || v.isMissingNode()) return 0;
        String s = v.isValueNode() ? v.asText("") : "";
        s = s.replaceAll("[^\\d.]", "");
        try {
            return s.isEmpty() ? 0 : Double.parseDouble(s);
        } catch (Exception e) {
            return 0;
        }
    }

    private static String firstText(JsonNode node, String... keys) {
        if (node == null || node.isMissingNode()) return "";
        for (String k : keys) {
            String v = node.path(k).asText("");
            if (!v.isEmpty()) return v;
        }
        return "";
    }

    private static void addIf(List<String> list, String v) {
        if (v != null && !v.isEmpty()) list.add(v);
    }

    private static void put(ObjectNode node, String key, String value) {
        if (value != null && !value.isEmpty()) node.put(key, value);
    }

    private static void putList(ObjectNode node, String key, List<String> values) {
        if (values == null || values.isEmpty()) return;
        ArrayNode arr = node.putArray(key);
        values.forEach(arr::add);
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }

    private static String tail(String s, int n) {
        if (s == null) return "";
        return s.length() <= n ? s : s.substring(s.length() - n);
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
