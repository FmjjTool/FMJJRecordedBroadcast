package cn.fmjj.toolbox.engine.platform.tickets;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.engine.browser.CdpClient;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.net.ServerSocket;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 票务平台解析：大麦 / 秀动 / B站会员购。
 * 移植自 Python 版 tickets.py（数据模型 + 三平台解析 + 跨平台关键词搜索 + 图片下载）。
 */
public final class Tickets {

    public static final String UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
    private static final String UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
            + " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
    private static final String DAMAI_APPKEY = "12574478";
    private static final ObjectMapper MAPPER = JsonMapper.get();
    private static final DateTimeFormatter BILI_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");
    private static final DateTimeFormatter FETCHED_AT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private static final Map<String, String> DAMAI_ARTIST_ID_HINTS = Map.of(
            "封茗囧菌", "33879518699",
            "封茗囧菌咨讯站", "33879518699");
    private static final Map<String, String> SHOWSTART_ARTIST_ID_HINTS = Map.of();

    public static final class TicketTier {
        public String name = "";
        public String price = "";
        public String status = "";
        public String desc = "";
    }

    public static final class ShowSession {
        public String name = "";
        public String startTime = "";
        public final List<TicketTier> tickets = new ArrayList<>();
    }

    /** 演出（字段对齐 Python ShowEvent；可变对象，便于回填图片本地路径）。 */
    public static final class ShowEvent {
        public String platform = "";
        public String platformCn = "";
        public String eventId = "";
        public String title = "";
        public String subtitle = "";
        public String artists = "";
        public String city = "";
        public String venue = "";
        public String address = "";
        public String showTime = "";
        public String showDuration = "";
        public String saleStart = "";
        public String priceRange = "";
        public String status = "";
        public String url = "";
        public String cover = "";
        public String wantCount = "";
        public String descriptionHtml = "";
        public String descriptionText = "";
        public final List<String> images = new ArrayList<>();
        public final List<String> seatImages = new ArrayList<>();
        public final List<ObjectNode> noticeItems = new ArrayList<>();
        public final List<String> faqs = new ArrayList<>();
        public String refundPolicy = "";
        public String merchant = "";
        public String contact = "";
        public String venueLat = "";
        public String venueLng = "";
        public String category = "";
        public final List<String> tags = new ArrayList<>();
        public final List<ShowSession> sessions = new ArrayList<>();
        public final List<ObjectNode> guestsDetail = new ArrayList<>();
        public String poster = "";
        public String venueImage = "";
        public final ObjectNode extra = MAPPER.createObjectNode();
        public String imageDir = "";
        public final List<String> localCover = new ArrayList<>();
        public final List<String> localPoster = new ArrayList<>();
        public final List<String> localImages = new ArrayList<>();
        public final List<String> localSeat = new ArrayList<>();
        public final List<String> localGuests = new ArrayList<>();
        public final List<String> localVenue = new ArrayList<>();

        public ObjectNode toDict() {
            ObjectNode d = MAPPER.createObjectNode();
            put(d, "平台", platformCn.isEmpty() ? platform : platformCn);
            put(d, "演出ID", eventId);
            put(d, "标题", title);
            put(d, "副标题", subtitle);
            put(d, "艺人", artists);
            put(d, "城市", city);
            put(d, "场馆", venue);
            put(d, "地址", address);
            put(d, "经纬度", !venueLat.isEmpty() && !venueLng.isEmpty() ? venueLat + "," + venueLng : "");
            put(d, "时间", showTime);
            put(d, "演出时长", showDuration);
            put(d, "开售时间", saleStart);
            put(d, "票价", priceRange);
            put(d, "状态", status);
            put(d, "想看", wantCount);
            put(d, "分类", category);
            put(d, "主办方", merchant);
            put(d, "联系方式", contact);
            put(d, "退票政策", refundPolicy);
            put(d, "演出介绍", descriptionText);
            put(d, "演出介绍HTML", descriptionHtml);
            putList(d, "宣传图", images);
            putList(d, "座位图", seatImages);
            if (!noticeItems.isEmpty()) {
                ArrayNode arr = d.putArray("购票须知");
                noticeItems.forEach(arr::add);
            }
            putList(d, "常见问题", faqs);
            putList(d, "标签", tags);
            put(d, "链接", url);
            put(d, "封面", cover);
            put(d, "海报", poster);
            put(d, "场馆图", venueImage);
            ArrayNode guests = d.putArray("嘉宾明细");
            for (ObjectNode g : guestsDetail) {
                if (g.path("名字").asText("").isEmpty()) continue;
                ObjectNode out = MAPPER.createObjectNode();
                out.put("名字", g.path("名字").asText(""));
                out.put("头像", g.path("头像").asText(""));
                out.put("简介", g.path("简介").asText(""));
                out.put("标签", g.path("标签").asText(""));
                if (!g.path("艺人海报").asText("").isEmpty()) {
                    out.put("艺人海报", g.path("艺人海报").asText(""));
                }
                guests.add(out);
            }
            if (guests.isEmpty()) d.remove("嘉宾明细");
            d.put("获取时间", LocalDateTime.now().format(FETCHED_AT));
            d.setAll(extra);
            if (!sessions.isEmpty()) {
                ArrayNode arr = d.putArray("场次明细");
                for (ShowSession s : sessions) {
                    ObjectNode so = arr.addObject();
                    so.put("场次", s.name);
                    so.put("开始时间", s.startTime);
                    ArrayNode tiers = so.putArray("票档");
                    for (TicketTier t : s.tickets) {
                        ObjectNode to = tiers.addObject();
                        to.put("名称", t.name);
                        to.put("价格", t.price);
                        to.put("状态", t.status);
                        to.put("说明", t.desc);
                    }
                }
            }
            if (!imageDir.isEmpty()) {
                d.put("本地图片目录", imageDir);
                putList(d, "本地封面", localCover);
                putList(d, "本地海报", localPoster);
                putList(d, "本地宣传图", localImages);
                putList(d, "本地座位图", localSeat);
                putList(d, "本地嘉宾头像", localGuests);
                putList(d, "本地场馆图", localVenue);
            }
            return d;
        }
    }

    private final Session damai = new Session();
    private final Session bili = new Session();
    private final HttpClient plain = new HttpClient();

    // ==================== 大麦（mtop 两步令牌） ====================

    /** 带 Cookie 罐的小会话（大麦令牌 / B站 buvid 都依赖 Set-Cookie 回传）。 */
    private static final class Session {
        final HttpClient http = new HttpClient();
        final Map<String, String> cookies = new LinkedHashMap<>();

        HttpResponse<String> get(String url, Map<String, String> headers)
                throws IOException, InterruptedException {
            Map<String, String> h = new LinkedHashMap<>();
            if (headers != null) h.putAll(headers);
            if (!cookies.isEmpty()) h.put("Cookie", cookieHeader());
            HttpResponse<String> resp = http.get(url, h);
            collect(resp);
            return resp;
        }

        private void collect(HttpResponse<String> resp) {
            for (String c : resp.headers().allValues("Set-Cookie")) {
                int semi = c.indexOf(';');
                String pair = semi >= 0 ? c.substring(0, semi) : c;
                int eq = pair.indexOf('=');
                if (eq > 0) cookies.put(pair.substring(0, eq).trim(), pair.substring(eq + 1).trim());
            }
        }

        String cookieHeader() {
            StringBuilder sb = new StringBuilder();
            for (Map.Entry<String, String> e : cookies.entrySet()) {
                if (sb.length() > 0) sb.append("; ");
                sb.append(e.getKey()).append('=').append(e.getValue());
            }
            return sb.toString();
        }
    }

    private JsonNode damaiMtop(String api, String version, ObjectNode payload)
            throws IOException, InterruptedException {
        String dataStr = MAPPER.writeValueAsString(payload);
        JsonNode d = damaiCall(api, version, dataStr);
        if (d == null) {
            ObjectNode warmArgs = MAPPER.createObjectNode();
            warmArgs.put("comboArtisteId", "0");
            warmArgs.put("comboChannel", "2");
            warmArgs.put("dmChannel", "damai@damaih5_h5");
            warmArgs.put("comboCityId", "");
            warmArgs.put("platform", "8");
            ObjectNode warm = MAPPER.createObjectNode();
            warm.put("args", MAPPER.writeValueAsString(warmArgs));
            warm.put("patternVersion", "1.2");
            warm.put("patternName", "app_artiste_home");
            warm.put("platform", "8");
            warm.put("comboChannel", "2");
            warm.put("dmChannel", "damai@damaih5_h5");
            String warmStr = MAPPER.writeValueAsString(warm);
            damaiGet("mtop.damai.mec.aristotle.get", "3.0", warmStr, false);
            d = damaiCall(api, version, dataStr);
        }
        return d;
    }

    /** 两步令牌调用：第一步拿 `_m_h5_tk`，第二步带 tk 签名重发。 */
    private JsonNode damaiCall(String api, String version, String dataStr)
            throws IOException, InterruptedException {
        damaiGet(api, version, dataStr, false);
        String token = damai.cookies.getOrDefault("_m_h5_tk", "");
        if (token.isEmpty()) return null;
        return damaiGet(api, version, dataStr, true);
    }

    private JsonNode damaiGet(String api, String version, String dataStr, boolean withToken)
            throws IOException, InterruptedException {
        String t = String.valueOf(System.currentTimeMillis());
        String tk = damai.cookies.getOrDefault("_m_h5_tk", "");
        String tkPart = withToken && !tk.isEmpty() ? tk.split("_")[0] : "";
        String sign = md5(tkPart + "&" + t + "&" + DAMAI_APPKEY + "&" + dataStr);
        Map<String, String> q = new LinkedHashMap<>();
        q.put("jsv", "2.7.5");
        q.put("appKey", DAMAI_APPKEY);
        q.put("t", t);
        q.put("sign", sign);
        q.put("api", api);
        q.put("v", version);
        if (withToken) {
            q.put("H5Request", "true");
            q.put("timeout", "10000");
            q.put("valueType", "string");
            q.put("forceAntiCreep", "true");
            q.put("AntiCreep", "true");
        }
        q.put("type", "json");
        q.put("dataType", "json");
        q.put("data", dataStr);
        StringBuilder url = new StringBuilder("https://mtop.damai.cn/h5/").append(api)
                .append('/').append(version).append("/?");
        boolean first = true;
        for (Map.Entry<String, String> e : q.entrySet()) {
            if (!first) url.append('&');
            first = false;
            url.append(e.getKey()).append('=').append(URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8));
        }
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA);
        h.put("Referer", "https://m.damai.cn/");
        h.put("Origin", "https://m.damai.cn");
        HttpResponse<String> resp = damai.get(url.toString(), h);
        try {
            return HttpClient.parseJson(resp.body());
        } catch (Exception e) {
            return null;
        }
    }

    private static String md5(String s) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] out = md.digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : out) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private static void walkNodes(JsonNode nodes, List<JsonNode> out) {
        if (nodes == null || !nodes.isArray()) return;
        for (JsonNode n : nodes) {
            if (!n.isObject()) continue;
            out.add(n);
            walkNodes(n.path("nodes"), out);
        }
    }

    /** 大麦艺人页 → (艺人名, 演出列表)。 */
    public Object[] parseDamaiArtist(String artistId) throws IOException, InterruptedException {
        ObjectNode args = MAPPER.createObjectNode();
        args.put("comboArtisteId", artistId);
        args.put("comboChannel", "2");
        args.put("dmChannel", "damai@damaih5_h5");
        args.put("comboCityId", "852");
        args.put("platform", "8");
        ObjectNode payload = MAPPER.createObjectNode();
        payload.put("args", MAPPER.writeValueAsString(args));
        payload.put("patternVersion", "1.2");
        payload.put("patternName", "app_artiste_home");
        payload.put("platform", "8");
        payload.put("comboChannel", "2");
        payload.put("dmChannel", "damai@damaih5_h5");
        JsonNode d = damaiMtop("mtop.damai.mec.aristotle.get", "3.0", payload);
        if (d == null || !d.path("ret").isArray()
                || !d.path("ret").get(0).asText("").startsWith("SUCCESS")) {
            String err = d == null ? "大麦接口调用失败"
                    : d.path("ret").path(0).asText("大麦接口调用失败");
            throw new IOException("大麦艺人页获取失败：" + trunc(err, 60));
        }
        JsonNode outer = d.path("data");
        JsonNode inner = outer.path("data");
        String artistName = inner.path("top").path("artistName").asText("");
        List<ShowEvent> events = new ArrayList<>();
        List<JsonNode> nodes = new ArrayList<>();
        walkNodes(outer.path("nodes"), nodes);
        for (JsonNode node : nodes) {
            JsonNode data = node.path("data");
            if (!"PERFORMANCE".equals(data.path("type").asText(""))) continue;
            String schema = data.path("schema").asText("");
            String itemId = "";
            Matcher m = Pattern.compile("itemId=(\\d+)").matcher(schema);
            if (m.find()) itemId = m.group(1);
            ShowEvent ev = new ShowEvent();
            ev.platform = "damai";
            ev.platformCn = "大麦";
            ev.eventId = itemId;
            ev.title = data.path("name").asText("");
            ev.city = data.path("cityName").asText("");
            ev.venue = data.path("venueName").asText("");
            ev.showTime = data.path("showTime").asText("");
            ev.priceRange = data.path("priceStr").asText("");
            ev.status = data.path("buttonText").asText("");
            ev.url = schema.isEmpty()
                    ? "https://m.damai.cn/damai/detail/item.html?itemId=" + itemId : schema;
            ev.cover = data.path("verticalPic").asText("");
            if (!data.path("ipvuv").asText("").isEmpty()) {
                ev.extra.put("热度", data.path("ipvuv").asText(""));
            }
            events.add(ev);
        }
        if (events.isEmpty()) throw new IOException("该艺人暂无在售/即将开售的演出");
        return new Object[]{artistName, events};
    }

    /** 大麦演出详情。 */
    public ShowEvent parseDamaiItem(String itemId) throws IOException, InterruptedException {
        ObjectNode payload = MAPPER.createObjectNode();
        payload.put("itemId", itemId);
        payload.put("platform", "8");
        payload.put("comboChannel", "2");
        payload.put("dmChannel", "damai@damaih5_h5");
        JsonNode d = damaiMtop("mtop.damai.item.detail.getdetail", "1.0", payload);
        if (d == null || !d.path("ret").isArray()
                || !d.path("ret").get(0).asText("").startsWith("SUCCESS")) {
            String err = d == null ? "大麦接口调用失败"
                    : d.path("ret").path(0).asText("大麦接口调用失败");
            throw new IOException("大麦详情获取失败：" + trunc(err, 60));
        }
        JsonNode data = d.path("data");
        JsonNode item = data.path("item");
        if (item.isMissingNode() || item.isEmpty()) throw new IOException("大麦未返回演出数据");
        JsonNode guide = data.path("guide");
        JsonNode venue = data.path("venue");

        ShowEvent ev = new ShowEvent();
        ev.platform = "damai";
        ev.platformCn = "大麦";
        ev.eventId = item.path("itemId").asText(itemId);
        ev.title = item.path("itemName").asText("");
        ev.subtitle = item.path("itemNameDisplay").asText("");
        if (ev.subtitle.isEmpty()) ev.subtitle = item.path("subTitle").asText("");
        List<String> artistNames = new ArrayList<>();
        for (JsonNode a : guide.path("artists")) {
            String n = a.path("name").asText("");
            if (n.isEmpty()) continue;
            artistNames.add(n);
            ObjectNode g = MAPPER.createObjectNode();
            g.put("名字", n);
            g.put("头像", a.path("picUrl").asText(""));
            g.put("简介", a.path("desc").asText("").strip());
            g.put("标签", a.path("tag").asText(""));
            ev.guestsDetail.add(g);
        }
        ev.artists = String.join("、", artistNames);
        ev.city = item.path("cityName").asText("");
        ev.venue = venue.path("venueName").asText("");
        ev.address = venue.path("venueAddr").asText("");
        ev.venueLat = venue.path("lat").asText("");
        ev.venueLng = venue.path("lng").asText("");
        ev.showTime = item.path("showTime").asText("");
        ev.showDuration = item.path("showDuration").asText("");
        ev.saleStart = data.path("saleInfo").path("saleStartTime").asText("");
        ev.priceRange = data.path("price").path("range").asText("");
        ev.status = data.path("buyButton").path("text").asText("");
        ev.url = "https://m.damai.cn/damai/detail/item.html?itemId=" + itemId;
        JsonNode pics = item.path("itemPics").path("itemPicList");
        ev.cover = pics.isArray() && !pics.isEmpty() ? pics.get(0).path("picUrl").asText("") : "";

        if (item.path("purchaseLimitation").asInt(0) > 0) {
            ev.extra.put("限购", "每单限购 " + item.path("purchaseLimitation").asInt() + " 张");
        }
        if ("1".equals(item.path("realName").path("type").asText(""))) {
            ev.extra.put("实名制", "需实名制购票入场");
        }
        if (!ev.showDuration.isEmpty()) ev.extra.put("演出时长", ev.showDuration);
        JsonNode want = guide.path("want");
        if (!want.path("wantNum").asText("").isEmpty()) {
            ev.extra.put("想看人数", want.path("wantNumStr").asText(
                    want.path("wantNum").asText("")));
        }

        String intro = data.path("desc").path("introduce").asText("");
        if (!intro.isEmpty()) {
            ev.descriptionHtml = intro;
            for (String u : findImages(intro)) if (!ev.images.contains(u)) ev.images.add(u);
            ev.descriptionText = stripTags(intro);
        }
        for (JsonNode t : data.path("serviceTips").path("importantNotes")) {
            String title = t.path("title").asText("").strip();
            String content = t.path("content").asText("").strip();
            if (!title.isEmpty() && !content.isEmpty()) {
                ObjectNode n = MAPPER.createObjectNode();
                n.put("标题", title);
                n.put("内容", trunc(content, 300));
                ev.noticeItems.add(n);
            }
        }
        for (JsonNode u : item.path("seatImages")) ev.seatImages.add(u.asText(""));
        for (JsonNode f : data.path("faqs")) ev.faqs.add(f.asText(""));
        List<String> refundTags = new ArrayList<>();
        for (JsonNode t : data.path("serviceTips").path("serviceTerm")) {
            String tag = t.path("tagName").asText("");
            if (!tag.isEmpty()) refundTags.add(tag);
        }
        ev.refundPolicy = String.join(" / ", refundTags);
        if (ev.refundPolicy.isEmpty()) {
            for (ObjectNode n : ev.noticeItems) {
                String title = n.path("标题").asText("");
                if (title.contains("退") || title.contains("换")) {
                    ev.refundPolicy = n.path("内容").asText("");
                    break;
                }
            }
        }
        for (JsonNode pl : data.path("performLimits")) {
            ShowSession s = new ShowSession();
            s.name = "场次 " + pl.path("performId").asText("");
            s.startTime = ev.showTime;
            ev.sessions.add(s);
            if (!pl.path("singleLimitation").asText("").isEmpty()) {
                ev.extra.put("限购", "单场限购 " + pl.path("singleLimitation").asText() + " 张");
            }
        }
        ev.poster = ev.images.isEmpty() ? "" : ev.images.get(0);
        ev.wantCount = want.path("wantNumStr").asText("");
        return ev;
    }

    // ==================== 秀动（HTML 解析） ====================

    private String showstartGet(String path) throws IOException, InterruptedException {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA);
        h.put("Accept-Language", "zh-CN,zh;q=0.9");
        var resp = plain.get("https://www.showstart.com" + path, h);
        if (resp.statusCode() != 200) throw new IOException("秀动页面请求失败（HTTP " + resp.statusCode() + "）");
        return resp.body();
    }

    public List<ShowEvent> parseShowstartArtist(String artistId) throws IOException, InterruptedException {
        String html = showstartGet("/artist/" + artistId);
        List<ShowEvent> events = new ArrayList<>();
        Pattern card = Pattern.compile(
                "<a href=\"(/event/\\d+)\"[^>]*>.*?<p class=\"name\"[^>]*>(.*?)</p>"
                        + ".*?<p class=\"performerName\"[^>]*>(.*?)</p>"
                        + ".*?<p class=\"price\"[^>]*>.*?<em[^>]*>(.*?)</em>"
                        + ".*?<p class=\"time\"[^>]*>(.*?)</p>"
                        + ".*?<p class=\"addr\"[^>]*>(.*?)</p>", Pattern.DOTALL);
        Matcher m = card.matcher(html);
        while (m.find()) {
            String url = m.group(1);
            ShowEvent ev = baseShowstart(url.split("/")[url.split("/").length - 1],
                    stripTags(m.group(2)));
            ev.artists = stripTags(m.group(3)).replace("艺人：", "");
            ev.showTime = stripTags(m.group(5));
            ev.priceRange = stripTags(m.group(4));
            ev.venue = stripTags(m.group(6));
            ev.url = "https://www.showstart.com" + url;
            events.add(ev);
        }
        if (events.isEmpty()) throw new IOException("秀动艺人页未解析到演出（可能无演出或页面结构变化）");
        return events;
    }

    private static ShowEvent baseShowstart(String eventId, String title) {
        ShowEvent ev = new ShowEvent();
        ev.platform = "showstart";
        ev.platformCn = "秀动";
        ev.eventId = eventId;
        ev.title = title;
        return ev;
    }

    public ShowEvent parseShowstartEvent(String eventId) throws IOException, InterruptedException {
        String html = showstartGet("/event/" + eventId);
        ShowEvent ev = baseShowstart(eventId, "");
        Matcher m = Pattern.compile("<h1[^>]*>(.*?)</h1>", Pattern.DOTALL).matcher(html);
        if (m.find()) ev.title = stripTags(m.group(1));
        if (ev.title.isEmpty()) {
            m = Pattern.compile("detail\\s*:\\s*\\{[^{}]{0,40}title\\s*:\\s*\"((?:[^\"\\\\]|\\\\.){2,120})\"")
                    .matcher(html);
            if (m.find()) ev.title = m.group(1).replace("\\\"", "\"");
        }
        if (ev.title.isEmpty()) ev.title = "秀动演出 " + eventId;

        String plainText = stripTags(html).replace("|", "｜");
        m = Pattern.compile("演出时间：([^<｜|]{4,40})").matcher(plainText);
        if (m.find()) ev.showTime = m.group(1).strip();
        else {
            m = Pattern.compile("showTime[\"']?\\s*[:=]\\s*[\"']([^\"']+)").matcher(html);
            if (m.find()) ev.showTime = m.group(1).strip();
        }

        for (String pat : List.of(
                "detail\\s*:\\s*\\{[^{}]{0,40}content\\s*:\\s*\"((?:[^\"\\\\]|\\\\.){20,3000})\"",
                "detail\\s*:\\s*\\{[^{}]{0,40}desc\\s*:\\s*\"((?:[^\"\\\\]|\\\\.){20,3000})\"",
                "description\\s*:\\s*\"((?:[^\"\\\\]|\\\\.){20,3000})\"")) {
            m = Pattern.compile(pat).matcher(html);
            if (m.find()) {
                String raw = m.group(1).replace("\\\"", "\"").replace("\\n", "\n");
                ev.descriptionHtml = raw;
                ev.images.addAll(findImages(raw));
                ev.descriptionText = stripTags(raw);
                break;
            }
        }
        if (ev.descriptionText.isEmpty()) {
            m = Pattern.compile("<div class=\"(?:detail|intro|content)[^\"]*\"[^>]*>(.*?)</div>",
                    Pattern.DOTALL).matcher(html);
            if (m.find()) {
                ev.images.addAll(findImages(m.group(1)));
                ev.descriptionText = trunc(stripTags(m.group(1)), 1500);
            }
        }

        List<String> performers = new ArrayList<>();
        Matcher pm = Pattern.compile("performers\\s*:\\s*\\[(.*?)\\]\\s*,\\s*\\w+\\s*:",
                Pattern.DOTALL).matcher(html);
        if (!pm.find()) {
            pm = Pattern.compile("performers\\s*:\\s*\\[(.{0,8000}?)\\]", Pattern.DOTALL).matcher(html);
            if (!pm.find()) pm = null;
        }
        if (pm != null) {
            Matcher gm = Pattern.compile(
                    "\\{\\s*userType:[^{}]*?name:\"((?:[^\"\\\\]|\\\\.){1,60})\""
                            + "[^{}]*?avatar:\"((?:[^\"\\\\]|\\\\.)+)\""
                            + "(?:[^{}]*?poster:\"((?:[^\"\\\\]|\\\\.)+)\")?").matcher(pm.group(1));
            while (gm.find()) {
                String name = gm.group(1).replace("\\\"", "\"");
                String av = urlUnescape(gm.group(2));
                String po = gm.group(3) == null ? "" : urlUnescape(gm.group(3));
                performers.add(name);
                ObjectNode g = MAPPER.createObjectNode();
                g.put("名字", name);
                g.put("头像", av);
                g.put("简介", "");
                g.put("标签", "");
                if (!po.isEmpty()) g.put("艺人海报", po);
                ev.guestsDetail.add(g);
            }
        }
        if (performers.isEmpty()) {
            pm = Pattern.compile("<p class=\"performerName\"[^>]*>(.*?)</p>", Pattern.DOTALL).matcher(html);
            if (pm.find()) {
                Matcher nm = Pattern.compile(">([^<>]{2,30})<").matcher(pm.group(1));
                while (nm.find()) performers.add(stripTags(nm.group(1)));
            }
        }

        m = Pattern.compile("detail\\s*:\\s*\\{[^{}]{0,120}?poster\\s*:\\s*\"((?:[^\"\\\\]|\\\\.){10,300})\"")
                .matcher(html);
        if (m.find()) ev.poster = urlUnescape(m.group(1));
        if (ev.poster.isEmpty()) {
            m = Pattern.compile("og:image\"\\s+content=\"([^\"]+)\"").matcher(html);
            if (m.find()) ev.poster = m.group(1);
        }
        if (ev.images.isEmpty()) {
            Matcher im = Pattern.compile(
                    "(https?://s\\d?\\.showstart\\.com/img/\\d{4}/[^\"'?]+\\.(?:jpg|png|webp))").matcher(html);
            while (im.find()) {
                String u = im.group(1);
                if (!ev.images.contains(u) && !u.contains("logo") && !u.contains("beian")) {
                    ev.images.add(u);
                }
            }
        }

        m = Pattern.compile("href=\"/venue/\\d+\"[^>]*>([^<]{2,50})").matcher(html);
        if (m.find()) ev.venue = m.group(1).strip();
        m = Pattern.compile("地址：([^<]{6,80})").matcher(html);
        if (m.find()) ev.address = m.group(1).strip();

        List<TicketTier> tiers = new ArrayList<>();
        Matcher tm = Pattern.compile("[¥￥]\\s*(\\d+(?:\\.\\d+)?)\\s*([^<｜]{0,40})").matcher(html);
        Set<String> seen = new LinkedHashSet<>();
        while (tm.find()) {
            String price = tm.group(1);
            String desc = trunc(stripTags(tm.group(2)), 40);
            if (!seen.add(price + "|" + desc)) continue;
            TicketTier t = new TicketTier();
            t.name = desc.isEmpty() ? price : desc;
            t.price = "¥" + price;
            t.desc = desc;
            tiers.add(t);
        }
        if (!tiers.isEmpty()) {
            ShowSession s = new ShowSession();
            s.name = "售票票种";
            s.tickets.addAll(tiers);
            ev.sessions.add(s);
            ev.priceRange = tiers.get(0).price;
        }
        m = Pattern.compile("(?:演出时长|时长)[\\s:：]*([^<｜|]{2,30})").matcher(plainText);
        if (m.find()) ev.showDuration = m.group(1).strip();
        m = Pattern.compile("(?:开售时间|开售)[\\s:：]*([^<｜|]{4,40})").matcher(plainText);
        if (m.find()) ev.saleStart = m.group(1).strip();
        m = Pattern.compile("退票政策[\\s:：]*([^<｜|]{6,80})").matcher(plainText);
        if (m.find()) ev.refundPolicy = m.group(1).strip();
        m = Pattern.compile("(?:主办方|项目方|承办方)[\\s:：]*([^\\s<｜|]{2,40})").matcher(plainText);
        if (m.find()) ev.merchant = m.group(1).strip();
        ev.artists = trunc(String.join("、", performers.stream()
                .filter(x -> !x.isEmpty() && !x.equals("/")).toList()), 200);
        return ev;
    }

    // ==================== B站会员购（公开 API） ====================

    private static Map<String, String> biliHeaders() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA);
        h.put("Referer", "https://show.bilibili.com/");
        return h;
    }

    public List<ShowEvent> searchBilibiliShow(String keyword) throws IOException, InterruptedException {
        try {
            bili.get("https://www.bilibili.com/", biliHeaders());
        } catch (Exception ignored) {
        }
        String url = "https://show.bilibili.com/api/ticket/search/list?version=134&keyword="
                + URLEncoder.encode(keyword, StandardCharsets.UTF_8) + "&pagesize=16&page=1&platform=web";
        var resp = bili.get(url, biliHeaders());
        JsonNode d = HttpClient.parseJson(resp.body());
        if (d.path("errno").asInt(-1) != 0) {
            throw new IOException("B站会员购搜索失败：" + d.path("msg").asText(""));
        }
        List<ShowEvent> events = new ArrayList<>();
        for (JsonNode it : d.path("data").path("result")) {
            String pid = !it.path("id").asText("").isEmpty() ? it.path("id").asText("")
                    : it.path("ticket_id").asText("");
            ShowEvent ev = new ShowEvent();
            ev.platform = "bilibili_show";
            ev.platformCn = "B站会员购";
            ev.eventId = pid;
            ev.title = it.path("project_name").asText(it.path("title").asText(""));
            ev.city = it.path("city").asText("");
            ev.venue = it.path("venue_name").asText("");
            ev.showTime = it.path("start_time").isNumber()
                    ? biliTime(it.path("start_time").asLong()) : it.path("start_time").asText("");
            ev.priceRange = biliPrice(it.path("price_low"), it.path("price_high"));
            ev.status = it.path("sale_flag").asText("");
            ev.url = it.path("url").asText("https://show.bilibili.com/platform/detail.html?id=" + pid);
            ev.cover = it.path("cover").asText("");
            ev.wantCount = it.path("want_num").asText("");
            events.add(ev);
        }
        if (events.isEmpty()) throw new IOException("「" + keyword + "」无搜索结果");
        return events;
    }

    public ShowEvent parseBilibiliProject(String projectId) throws IOException, InterruptedException {
        String url = "https://show.bilibili.com/api/ticket/project/get?id=" + projectId
                + "&project_id=" + projectId;
        var resp = plain.get(url, biliHeaders());
        JsonNode d = HttpClient.parseJson(resp.body());
        if (d.path("errno").asInt(-1) != 0) {
            throw new IOException("B站会员购详情获取失败：" + d.path("msg").asText(""));
        }
        JsonNode data = d.path("data");
        ShowEvent ev = new ShowEvent();
        ev.platform = "bilibili_show";
        ev.platformCn = "B站会员购";
        ev.eventId = data.path("id").asText(projectId);
        ev.title = data.path("name").asText(data.path("title").asText(""));
        ev.subtitle = data.path("name").asText("");
        ev.artists = trunc(stripTags(data.path("performers").asText("")), 200);

        String descHtml = "";
        List<String> images = new ArrayList<>();
        List<String[]> baseInfo = new ArrayList<>();
        List<String[]> custom = new ArrayList<>();
        JsonNode rawPd = data.path("performance_desc");
        if (rawPd.isObject()) {
            for (JsonNode mod : rawPd.path("list")) {
                JsonNode det = mod.path("details");
                String module = mod.path("module").asText("");
                if ("activity_content".equals(module) && det.isTextual()) {
                    descHtml += det.asText();
                    for (String u : findImages(det.asText())) {
                        if (u.startsWith("//")) u = "https:" + u;
                        if (!u.isEmpty() && !images.contains(u)) images.add(u);
                    }
                } else if (det.isArray()) {
                    for (JsonNode it : det) {
                        if (it.isObject() && !it.path("title").asText("").isEmpty()) {
                            baseInfo.add(new String[]{it.path("title").asText(""),
                                    it.path("content").asText("")});
                        }
                    }
                } else if (det.isTextual() && !det.asText().strip().isEmpty()) {
                    String text = stripTags(det.asText());
                    if (!text.isEmpty()) {
                        String name = mod.path("module_name").asText(module);
                        custom.add(new String[]{name.isEmpty() ? "补充说明" : name, text});
                    }
                }
            }
        } else if (rawPd.isTextual() && !rawPd.asText().strip().isEmpty()) {
            descHtml = rawPd.asText();
        }
        if (descHtml.isEmpty() && images.isEmpty()) {
            String legacy = data.path("description").asText("");
            if (legacy.contains("<")) {
                descHtml = legacy;
                for (String u : findImages(legacy)) {
                    if (u.startsWith("//")) u = "https:" + u;
                    if (!u.isEmpty() && !images.contains(u)) images.add(u);
                }
            }
        }
        ev.descriptionHtml = descHtml;
        ev.descriptionText = descHtml.isEmpty() ? "" : stripTags(descHtml);

        for (JsonNode g : data.path("guests")) {
            if (!g.isObject() || g.path("name").asText("").isEmpty()) continue;
            String img = g.path("guest_img").asText(
                    g.path("guestImg").asText(g.path("avatar").asText("")));
            if (img.startsWith("//")) img = "https:" + img;
            ObjectNode out = MAPPER.createObjectNode();
            out.put("名字", g.path("name").asText(""));
            out.put("头像", img);
            out.put("简介", g.path("description").asText("").strip());
            out.put("标签", g.path("tag").asText(g.path("guest_type").asText("")));
            ev.guestsDetail.add(out);
        }

        String[][] noticeKeys = {
                {"notice", "购票须知"}, {"bulletin", "项目公告"}, {"disclaimer", "免责声明"},
                {"refund_desc", "退款说明"}, {"refund_desc_detail", "退款详情"},
                {"ticket_desc", "票务说明"}, {"contact_notice", "联系须知"}};
        for (String[] key : noticeKeys) {
            JsonNode v = data.path(key[0]);
            if (v.isMissingNode() || v.isNull()) continue;
            String text;
            if (v.isArray()) {
                List<String> parts = new ArrayList<>();
                for (JsonNode x : v) if (!x.asText("").isEmpty()) parts.add(x.asText(""));
                text = String.join("\n", parts);
            } else if (v.isObject()) {
                text = v.path("content").asText(v.path("text").asText(v.path("desc").asText("")));
            } else {
                text = v.asText("");
            }
            text = text.strip();
            if (!text.isEmpty()) {
                addNotice(ev, key[1], text);
            }
        }
        for (String[] b : baseInfo) {
            if (!b[1].isEmpty()) addNotice(ev, "基本信息·" + b[0], b[1]);
        }
        for (String[] c : custom) addNotice(ev, c[0], c[1]);

        int isRefund = data.path("is_refund").asInt(-1);
        int allRefund = data.path("all_refund").asInt(0);
        if (isRefund == 10) ev.refundPolicy = "支持有条件退款（详见退款说明）";
        else if (isRefund == 0 || allRefund == 1) ev.refundPolicy = "不支持退款";
        else if (isRefund == 1) ev.refundPolicy = "支持退款";
        String rdd = data.path("refund_desc_detail").asText(data.path("refund_desc").asText(""));
        if (!rdd.isEmpty() && !ev.refundPolicy.contains(rdd)) {
            ev.refundPolicy = (ev.refundPolicy + "；" + trunc(rdd, 200)).replaceAll("^；|；$", "");
        }

        JsonNode pi = data.path("performance_image");
        if (pi.isTextual() && pi.asText().strip().startsWith("{")) {
            try {
                pi = MAPPER.readTree(pi.asText());
            } catch (Exception e) {
                pi = MAPPER.missingNode();
            }
        }
        List<String> banners = new ArrayList<>();
        if (pi.isObject()) {
            String b = pi.path("banner").path("url").asText("");
            if (!b.isEmpty()) banners.add(b);
            for (JsonNode x : pi.path("banners")) {
                String u = x.path("url").asText("");
                if (!u.isEmpty() && !banners.contains(u)) banners.add(u);
            }
        }
        String cover = data.path("cover").asText("");
        if (cover.startsWith("//")) cover = "https:" + cover;
        String banner = data.path("banner").asText("");
        if (banner.startsWith("//")) banner = "https:" + banner;
        if (banner.isEmpty() && !banners.isEmpty()) banner = banners.get(0);
        if (!banner.isEmpty() && images.isEmpty()) images.add(0, banner);
        for (int i = 1; i < banners.size(); i++) {
            String u = banners.get(i);
            if (u.startsWith("//")) u = "https:" + u;
            if (!u.isEmpty() && !images.contains(u) && !u.equals(banner)) images.add(u);
        }
        String venueImage = data.path("place_info").path("base_pic").asText("");
        if (venueImage.startsWith("//")) venueImage = "https:" + venueImage;

        String realName = "";
        JsonNode idBind = data.path("id_bind");
        if (idBind.isObject() && (idBind.path("open").asInt(0) == 1
                || !idBind.path("type").asText("").isEmpty())) {
            realName = "需实名购票";
        }
        if (realName.isEmpty() && data.path("buyer_info").path("type").asInt(0) == 1) {
            realName = "需实名购票";
        }
        String limit = "";
        for (JsonNode sk : data.path("screen_list")) {
            for (JsonNode tk : sk.path("ticket_list")) {
                int num = tk.path("static_limit").path("num").asInt(0);
                if (num > 0) {
                    limit = "单笔限购 " + num + " 张";
                    break;
                }
            }
            if (!limit.isEmpty()) break;
        }
        String merchant = data.path("merchant").path("name").asText(
                data.path("merchant").path("account_name").asText(""));
        for (JsonNode t : data.path("tags")) {
            String n = t.isObject() ? t.path("name").asText(t.path("title").asText("")) : t.asText("");
            if (!n.isEmpty()) ev.tags.add(n);
        }
        List<String> saleTags = new ArrayList<>();
        for (JsonNode t : data.path("sale_tags")) {
            String n = t.isObject() ? t.path("name").asText(t.path("title").asText(t.path("text").asText("")))
                    : t.asText("");
            if (!n.isEmpty()) saleTags.add(n);
        }
        for (JsonNode sk : data.path("screen_list")) {
            ShowSession s = new ShowSession();
            s.name = sk.path("name").asText("");
            s.startTime = biliTime(sk.path("start_time").asLong());
            for (JsonNode tk : sk.path("ticket_list")) {
                TicketTier tier = new TicketTier();
                tier.name = tk.path("desc").asText("");
                tier.price = biliPrice(tk.path("price"), null);
                JsonNode sale = tk.path("sale_flag");
                tier.status = sale.isObject() ? sale.path("display_name").asText("")
                        : (tk.path("clickable").asBoolean(false) ? "可购" : "不可购");
                tier.desc = trunc("id=" + tk.path("id").asText("")
                        + " saleStart=" + tk.path("sale_start").asText("")
                        + " saleEnd=" + tk.path("sale_end").asText("")
                        + " limit=" + (tk.path("static_limit").path("num").asText("").isEmpty()
                        ? "-" : tk.path("static_limit").path("num").asText("")), 200);
                s.tickets.add(tier);
            }
            ev.sessions.add(s);
        }
        ev.saleStart = biliTime(data.path("sale_begin").asLong(
                data.path("sale_start").asLong(data.path("sale_begin_time").asLong(0))));
        if (!limit.isEmpty()) ev.extra.put("限购", limit);
        if (!realName.isEmpty()) ev.extra.put("实名制", realName);
        if (!saleTags.isEmpty()) ev.extra.put("销售标签", trunc(String.join(" / ", saleTags), 200));
        String projectLabel = data.path("project_label").path("title").asText("");
        if (!projectLabel.isEmpty()) ev.extra.put("项目标签", projectLabel);
        if (!data.path("keywords").asText("").isEmpty()) {
            ev.extra.put("关键词", data.path("keywords").asText(""));
        }

        ev.city = data.path("city_name").asText("");
        ev.venue = data.path("venue_name").asText("");
        ev.address = data.path("venue_address").asText("");
        String start = biliTime(data.path("start_time").asLong());
        ev.showTime = data.path("end_time").asLong(0) > 0
                ? start + " ~ " + biliTime(data.path("end_time").asLong()) : start;
        ev.showDuration = data.path("duration").asText("");
        ev.priceRange = biliPrice(data.path("price_low"), data.path("price_high"));
        ev.status = data.path("sale_flag").asText("");
        ev.url = "https://show.bilibili.com/platform/detail.html?id=" + projectId;
        ev.cover = cover;
        ev.poster = banner;
        ev.venueImage = venueImage;
        ev.wantCount = data.path("wish_info").path("wish_count").asText(
                data.path("wish_info").path("wishCount").asText(""));
        ev.images.addAll(images);
        ev.merchant = merchant;
        return ev;
    }

    private static void addNotice(ShowEvent ev, String title, String content) {
        ObjectNode n = MAPPER.createObjectNode();
        n.put("标题", title);
        n.put("内容", trunc(content, 500));
        ev.noticeItems.add(n);
    }

    static String biliPrice(JsonNode low, JsonNode high) {
        long lo;
        try {
            lo = low == null ? 0 : low.asLong(0);
        } catch (Exception e) {
            return "";
        }
        if (lo <= 0) return "";
        String loS = fmtG(lo / 100.0);
        if (high == null || high.isNull() || high.isMissingNode() || high.asLong(-1) == lo) {
            return "¥" + loS;
        }
        return "¥" + loS + "-" + fmtG(high.asLong(0) / 100.0);
    }

    private static String fmtG(double v) {
        if (v == Math.rint(v)) return String.valueOf((long) v);
        return String.format(Locale.ROOT, "%.2f", v).replaceAll("0+$", "").replaceAll("\\.$", "");
    }

    static String biliTime(long ts) {
        if (ts <= 0) return "";
        return LocalDateTime.ofInstant(Instant.ofEpochSecond(ts), ZoneId.systemDefault()).format(BILI_TIME);
    }

    // ==================== URL 路由 ====================

    /** 识别票务链接 → {kind, id}；未识别返回 null。 */
    public static String[] detectTicketPlatform(String url) {
        String s = url == null ? "" : url.strip();
        Matcher m = Pattern.compile("damai\\.cn/shows/artist\\.html\\?artistId=(\\d+)").matcher(s);
        if (m.find()) return new String[]{"damai_artist", m.group(1)};
        m = Pattern.compile("damai\\.cn/damai/detail/item\\.html\\?itemId=(\\d+)").matcher(s);
        if (m.find()) return new String[]{"damai_item", m.group(1)};
        m = Pattern.compile("showstart\\.com/artist/(\\d+)").matcher(s);
        if (m.find()) return new String[]{"showstart_artist", m.group(1)};
        m = Pattern.compile("showstart\\.com/event/(\\d+)").matcher(s);
        if (m.find()) return new String[]{"showstart_event", m.group(1)};
        m = Pattern.compile("show\\.bilibili\\.com/platform/detail\\.html\\?id=(\\d+)").matcher(s);
        if (m.find()) return new String[]{"bili_project", m.group(1)};
        m = Pattern.compile("show\\.bilibili\\.com/platform/detail\\.html\\?project_id=(\\d+)").matcher(s);
        if (m.find()) return new String[]{"bili_project", m.group(1)};
        m = Pattern.compile("show\\.bilibili\\.com/platform/search\\.html\\?searchValue=([^&#]+)").matcher(s);
        if (m.find()) {
            return new String[]{"bili_search", URLDecoder.decode(m.group(1), StandardCharsets.UTF_8)};
        }
        if (s.contains("damai.cn") || s.contains("showstart.com") || s.contains("show.bilibili.com")) {
            return new String[]{"unknown_url", ""};
        }
        return null;
    }

    /** 统一入口：URL 或关键词 → 演出列表。 */
    public List<ShowEvent> parseTicketUrl(String url) throws IOException, InterruptedException {
        String[] kind = detectTicketPlatform(url);
        if (kind == null) throw new IOException("请输入票务链接或搜索关键词");
        switch (kind[0]) {
            case "damai_artist":
                @SuppressWarnings("unchecked")
                List<ShowEvent> events = (List<ShowEvent>) parseDamaiArtist(kind[1])[1];
                return events;
            case "damai_item":
                return List.of(parseDamaiItem(kind[1]));
            case "showstart_artist":
                return parseShowstartArtist(kind[1]);
            case "showstart_event":
                return List.of(parseShowstartEvent(kind[1]));
            case "bili_project":
                return List.of(parseBilibiliProject(kind[1]));
            case "bili_search":
                return searchBilibiliShow(kind[1]);
            default:
                throw new IOException("无法识别的票务链接（支持大麦艺人/演出、秀动艺人/事件、B站会员购搜索/项目）");
        }
    }

    // ==================== 跨平台关键词搜索 ====================

    public record SearchResult(List<ShowEvent> events, Map<String, String> diag) {}

    public SearchResult searchTicketsByKeyword(String keyword,
                                               String damaiArtistId, String showstartArtistId) {
        String kw = keyword == null ? "" : keyword.strip();
        List<ShowEvent> results = new ArrayList<>();
        Map<String, String> diag = new LinkedHashMap<>();
        if (kw.isEmpty()) {
            diag.put("_error", "请输入关键词");
            return new SearchResult(results, diag);
        }
        try {
            List<ShowEvent> biliEvents = searchBilibiliShow(kw);
            results.addAll(biliEvents);
            diag.put("bilibili", String.valueOf(biliEvents.size()));
        } catch (Exception e) {
            diag.put("bilibili", "❌ " + trunc(e.getMessage(), 80));
        }
        String did = damaiArtistId == null || damaiArtistId.isEmpty()
                ? DAMAI_ARTIST_ID_HINTS.getOrDefault(kw, "") : damaiArtistId;
        if (!did.isEmpty()) {
            try {
                @SuppressWarnings("unchecked")
                List<ShowEvent> events = (List<ShowEvent>) parseDamaiArtist(did)[1];
                results.addAll(events);
                diag.put("damai", String.valueOf(events.size()));
            } catch (Exception e) {
                diag.put("damai", "❌ " + trunc(e.getMessage(), 80));
            }
        } else {
            try {
                List<ShowEvent> events = damaiSearchKeyword(kw);
                results.addAll(events);
                diag.put("damai", events.isEmpty() ? "⚠️ 大麦无结果" : String.valueOf(events.size()));
            } catch (Exception e) {
                diag.put("damai", "❌ " + trunc(e.getMessage(), 80));
            }
        }
        String sid = showstartArtistId == null || showstartArtistId.isEmpty()
                ? SHOWSTART_ARTIST_ID_HINTS.getOrDefault(kw, "") : showstartArtistId;
        if (!sid.isEmpty()) {
            try {
                List<ShowEvent> events = parseShowstartArtist(sid);
                results.addAll(events);
                diag.put("showstart", String.valueOf(events.size()));
            } catch (Exception e) {
                diag.put("showstart", "❌ " + trunc(e.getMessage(), 80));
            }
        } else {
            try {
                List<ShowEvent> events = showstartSearchKeyword(kw);
                results.addAll(events);
                diag.put("showstart", events.isEmpty() ? "⚠️ 秀动无结果" : String.valueOf(events.size()));
            } catch (Exception e) {
                diag.put("showstart", "❌ " + trunc(e.getMessage(), 80));
            }
        }
        Set<String> seen = new LinkedHashSet<>();
        List<ShowEvent> unique = new ArrayList<>();
        for (ShowEvent ev : results) {
            String key = ev.platform + "|" + ev.eventId;
            if (!seen.add(key)) continue;
            unique.add(ev);
        }
        return new SearchResult(unique, diag);
    }

    /** 大麦游客关键词搜索（浏览器拦截 search.search 响应；HTTP 直调会被 RGV587 拦）。 */
    public List<ShowEvent> damaiSearchKeyword(String keyword) throws Exception {
        for (int attempt = 0; attempt < 2; attempt++) {
            DamaiSearchOne one = damaiSearchOnce(keyword);
            if (!one.events.isEmpty() || !one.blocked) return one.events;
            if (attempt == 0) Thread.sleep(5000);
        }
        return List.of();
    }

    private record DamaiSearchOne(List<ShowEvent> events, boolean blocked) {}

    private DamaiSearchOne damaiSearchOnce(String keyword) throws Exception {
        List<ShowEvent> hits = new ArrayList<>();
        boolean[] blocked = {false};
        int port;
        try (ServerSocket socket = new ServerSocket(0)) {
            port = socket.getLocalPort();
        }
        try (CdpClient browser = CdpClient.launch(port, 25)) {
            ObjectNode ua = MAPPER.createObjectNode();
            ua.put("userAgent", UA_MOBILE);
            ua.put("acceptLanguage", "zh-CN,zh;q=0.9");
            ua.put("platform", "iPhone");
            browser.call("Emulation.setUserAgentOverride", ua, 10);
            ObjectNode metrics = MAPPER.createObjectNode();
            metrics.put("width", 414);
            metrics.put("height", 896);
            metrics.put("deviceScaleFactor", 1);
            metrics.put("mobile", true);
            browser.call("Emulation.setDeviceMetricsOverride", metrics, 10);
            browser.call("Network.enable", null, 10);
            List<String> requestIds = new CopyOnWriteArrayList<>();
            CdpClient.EventListener listener = (method, params) -> {
                if (!"Network.responseReceived".equals(method)) return;
                String url = params.path("response").path("url").asText("");
                if (!url.contains("mtop.damai.wireless.search.search")) return;
                String decoded = URLDecoder.decode(url, StandardCharsets.UTF_8);
                if (!decoded.contains(keyword)) return;
                String rid = params.path("requestId").asText("");
                if (!rid.isEmpty()) requestIds.add(rid);
            };
            browser.addEventListener(listener);
            String searchUrl = "https://m.damai.cn/shows/search.html?keyword="
                    + URLEncoder.encode(keyword, StandardCharsets.UTF_8);
            browser.send("Page.navigate", nav(searchUrl));
            Thread.sleep(6000);
            try {
                browser.evalString("window.scrollTo(0,1000); String(window.scrollY)");
            } catch (Exception ignored) {
            }
            Thread.sleep(2000);
            for (String rid : requestIds) {
                try {
                    byte[] body = browser.responseBody(rid);
                    JsonNode d = MAPPER.readTree(new String(body, StandardCharsets.UTF_8));
                    String ret = d.path("ret").toString();
                    if (ret.contains("RGV587") || ret.contains("FAIL_SYS_USER_VALIDATE")) {
                        blocked[0] = true;
                        continue;
                    }
                    if (ret.contains("SUCCESS")) hits.addAll(damaiEventsFrom(d.path("data")));
                } catch (Exception ignored) {
                }
            }
        }
        return new DamaiSearchOne(hits, blocked[0]);
    }

    private static ObjectNode nav(String url) {
        ObjectNode nav = MAPPER.createObjectNode();
        nav.put("url", url);
        return nav;
    }

    private static List<ShowEvent> damaiEventsFrom(JsonNode data) {
        List<ShowEvent> events = new ArrayList<>();
        for (JsonNode pr : data.path("projectInfo")) {
            String pid = pr.path("id").asText("");
            if (pid.isEmpty()) continue;
            ShowEvent ev = new ShowEvent();
            ev.platform = "damai";
            ev.platformCn = "大麦";
            ev.eventId = pid;
            ev.title = pr.path("name").asText("");
            ev.artists = pr.path("actores").asText("").replace(";", " / ");
            ev.city = pr.path("cityName").asText(pr.path("venueCity").asText(""));
            ev.venue = pr.path("venueName").asText("");
            ev.showTime = pr.path("showTime").asText("");
            ev.priceRange = pr.path("priceStr").asText(pr.path("formattedPriceStr").asText(""));
            ev.status = pr.path("showStatus").path("desc").asText("");
            ev.category = pr.path("categoryName").asText("");
            ev.cover = pr.path("verticalPic").asText("");
            ev.url = "https://m.damai.cn/damai/detail/item.html?projectId=" + pid;
            events.add(ev);
        }
        return events;
    }

    /** 秀动游客关键词搜索（HTTP SSR）。 */
    public List<ShowEvent> showstartSearchKeyword(String keyword) throws IOException, InterruptedException {
        String kw = keyword == null ? "" : keyword.strip();
        if (kw.isEmpty()) return List.of();
        String html = showstartGet("/event/list?keyword=" + URLEncoder.encode(kw, StandardCharsets.UTF_8));
        Pattern card = Pattern.compile(
                "<a href=\"(/event/\\d+)\"[^>]*>.*?<div class=\"title\"[^>]*>(.*?)</div>"
                        + ".*?<div class=\"artist\"[^>]*>(.*?)</div>"
                        + ".*?<div class=\"price\"[^>]*>.*?<span[^>]*>(.*?)</span>"
                        + ".*?<div class=\"time\"[^>]*>(.*?)</div>"
                        + ".*?<div class=\"addr\"[^>]*>(.*?)</div>", Pattern.DOTALL);
        List<ShowEvent> events = new ArrayList<>();
        Matcher m = card.matcher(html);
        while (m.find()) {
            String url = m.group(1);
            String addr = stripTags(m.group(6));
            Matcher vm = Pattern.compile("\\[([^]]+)]\\s*(.*)").matcher(addr);
            String city = vm.matches() ? vm.group(1) : "";
            String venue = vm.matches() ? vm.group(2).strip() : addr;
            ShowEvent ev = baseShowstart(url.split("/")[url.split("/").length - 1], stripTags(m.group(2)));
            ev.artists = stripTags(m.group(3)).replace("艺人：", "");
            ev.city = city;
            ev.venue = venue;
            ev.showTime = stripTags(m.group(5)).replace("时间：", "");
            ev.priceRange = stripTags(m.group(4));
            ev.url = "https://www.showstart.com" + url;
            events.add(ev);
        }
        return events;
    }

    // ==================== 图片下载 ====================

    private static final String[][] REFERER_BY_HOST = {
            {"hdslb.com", "https://show.bilibili.com/"},
            {"damai.cn", "https://m.damai.cn/"},
            {"alicdn.com", "https://m.damai.cn/"},
            {"showstart.com", "https://www.showstart.com/"},
    };

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

    /** 下载一组图片（失败跳过），返回本地路径。 */
    public List<String> downloadImages(List<String> urls, Path folder, String prefix,
                                       Consumer<String> log) {
        List<String> out = new ArrayList<>();
        if (urls == null || urls.isEmpty()) return out;
        try {
            Files.createDirectories(folder);
        } catch (Exception ignored) {
        }
        int i = 0;
        for (String u : urls) {
            if (u == null || u.isEmpty()) continue;
            i++;
            String url = u;
            if (url.startsWith("//")) url = "https:" + url;
            if (url.contains("imageMogr2")) url = url.split("\\?")[0];
            String referer = "";
            for (String[] pair : REFERER_BY_HOST) {
                if (url.contains(pair[0])) {
                    referer = pair[1];
                    break;
                }
            }
            Path path = folder.resolve(prefix + String.format("%02d", i) + imgExt(url));
            try {
                byte[] data = downloadBytes(url, referer);
                if (data.length < 2000) throw new IOException("内容过小疑似防盗链占位图");
                Files.write(path, data);
                out.add(path.toString());
            } catch (Exception e) {
                if (log != null) log.accept("图片下载失败 " + tail(url, 40) + ": " + trunc(e.getMessage(), 40));
            }
        }
        return out;
    }

    private byte[] downloadBytes(String url, String referer) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url))
                .timeout(java.time.Duration.ofSeconds(30))
                .header("User-Agent", UA).GET();
        if (referer != null && !referer.isEmpty()) b.header("Referer", referer);
        return plain.jdkClient().send(b.build(), HttpResponse.BodyHandlers.ofByteArray()).body();
    }

    /** 下载演出图片：封面/海报/宣传图/座位图/嘉宾头像/场馆图。 */
    public void downloadEventImages(ShowEvent ev, Path baseDir, Consumer<String> log) {
        String ts = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"));
        Path base = baseDir.resolve(safeFolderName(ev.title, 40) + "_" + ev.eventId + "_" + ts);
        if (log != null) log.accept("下载图片到 " + base + " ...");
        ev.localCover.clear();
        ev.localCover.addAll(downloadImages(ev.cover.isEmpty() ? List.of() : List.of(ev.cover),
                base.resolve("cover"), "封面", log));
        ev.localPoster.clear();
        ev.localPoster.addAll(downloadImages(ev.poster.isEmpty() ? List.of() : List.of(ev.poster),
                base.resolve("poster"), "海报", log));
        ev.localImages.clear();
        ev.localImages.addAll(downloadImages(ev.images, base.resolve("images"), "宣传", log));
        ev.localSeat.clear();
        ev.localSeat.addAll(downloadImages(ev.seatImages, base.resolve("seat"), "座位", log));
        ev.localGuests.clear();
        Path gdir = base.resolve("guests");
        int n = 0;
        for (ObjectNode g : ev.guestsDetail) {
            String u = g.path("头像").asText("");
            if (u.isEmpty()) continue;
            n++;
            String url = u;
            if (url.startsWith("//")) url = "https:" + url;
            if (url.contains("imageMogr2")) url = url.split("\\?")[0];
            String safe = safeFolderName(g.path("名字").asText(""), 24);
            Path path = gdir.resolve(String.format("%02d_", n) + safe + imgExt(url));
            try {
                Files.createDirectories(gdir);
                Files.write(path, downloadBytes(url, ""));
                ev.localGuests.add(path.toString());
            } catch (Exception e) {
                if (log != null) log.accept("嘉宾头像失败 " + g.path("名字").asText("") + ": "
                        + trunc(e.getMessage(), 40));
            }
            String po = g.path("艺人海报").asText("");
            if (!po.isEmpty()) {
                if (po.startsWith("//")) po = "https:" + po;
                if (po.contains("imageMogr2")) po = po.split("\\?")[0];
                Path ppath = gdir.resolve(String.format("%02d_", n) + safe + "_海报" + imgExt(po));
                try {
                    Files.write(ppath, downloadBytes(po, ""));
                    ev.localGuests.add(ppath.toString());
                } catch (Exception e) {
                    if (log != null) log.accept("艺人海报失败 " + g.path("名字").asText("") + ": "
                            + trunc(e.getMessage(), 40));
                }
            }
        }
        ev.localVenue.clear();
        ev.localVenue.addAll(downloadImages(ev.venueImage.isEmpty() ? List.of() : List.of(ev.venueImage),
                base.resolve("venue"), "场馆", log));
        int total = ev.localCover.size() + ev.localPoster.size() + ev.localImages.size()
                + ev.localSeat.size() + ev.localGuests.size() + ev.localVenue.size();
        if (total > 0) ev.imageDir = base.toString();
        if (log != null) {
            log.accept("图片下载完成：共 " + total + " 张（封面 " + ev.localCover.size()
                    + " / 海报 " + ev.localPoster.size() + " / 宣传 " + ev.localImages.size()
                    + " / 座位 " + ev.localSeat.size() + " / 嘉宾 " + ev.localGuests.size()
                    + " / 场馆 " + ev.localVenue.size() + "）");
        }
    }

    // ==================== 工具 ====================

    static String stripTags(String html) {
        if (html == null || html.isEmpty()) return "";
        String noTags = html.replaceAll("<[^>]+>", "");
        String collapsed = noTags.replaceAll("\\s+", " ");
        return org.jsoup.parser.Parser.unescapeEntities(collapsed, false).strip();
    }

    private static List<String> findImages(String html) {
        List<String> out = new ArrayList<>();
        Matcher m = Pattern.compile("src=\"(https?://[^\"]+)\"").matcher(html == null ? "" : html);
        while (m.find()) {
            String u = m.group(1);
            if (!out.contains(u)) out.add(u);
        }
        return out;
    }

    private static String urlUnescape(String s) {
        return s.replace("\\u002F", "/").replace("\\/", "/").replace("\\\"", "\"");
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }

    private static String tail(String s, int n) {
        if (s == null) return "";
        return s.length() <= n ? s : s.substring(s.length() - n);
    }

    private static void put(ObjectNode node, String key, String value) {
        if (value != null && !value.isEmpty()) node.put(key, value);
    }

    private static void putList(ObjectNode node, String key, List<String> values) {
        if (values == null || values.isEmpty()) return;
        ArrayNode arr = node.putArray(key);
        values.forEach(arr::add);
    }
}
