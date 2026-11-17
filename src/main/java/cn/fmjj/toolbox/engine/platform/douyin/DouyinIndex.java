package cn.fmjj.toolbox.engine.platform.douyin;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 抖音指数（创作者中心 · 达人详情）API 层。
 * 移植自 Python 版 platforms/douyin_index.py。
 *
 * <p>接口 creator.douyin.com/api/v2/daren/*：POST JSON，仅需登录 Cookie + referer +
 * appsource 头（无需 a_bogus/msToken/csrf）。游客态一律 401。
 */
public final class DouyinIndex {

    public static final String UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
    public static final String REFERER = "https://creator.douyin.com/creator-micro/creator-count/"
            + "arithmetic-index/daren/detail";

    private static final String BASE = "https://creator.douyin.com";
    private static final ObjectMapper MAPPER = JsonMapper.get();
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final DateTimeFormatter FILE_TS = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss");
    private static final DateTimeFormatter FETCHED_AT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /** 粉丝画像键 → 中文标签（*_Tgi 后缀由解析层统一追加 TGI）。 */
    public static final Map<String, String> FANS_KEYS = new LinkedHashMap<>();

    static {
        FANS_KEYS.put("Province", "省份分布");
        FANS_KEYS.put("City", "城市分布");
        FANS_KEYS.put("CityLabel", "城市级别");
        FANS_KEYS.put("Age", "年龄分布");
        FANS_KEYS.put("Gender", "性别分布");
        FANS_KEYS.put("DeviceBrand", "手机品牌");
        FANS_KEYS.put("DevicePrice", "手机价格");
        FANS_KEYS.put("FirstTag", "兴趣标签");
        FANS_KEYS.put("SecondTag", "兴趣二级标签");
    }

    private final HttpClient http = new HttpClient();
    private final String cookie;

    public DouyinIndex() {
        this.cookie = CookieStore.get("douyin");
    }

    public boolean hasCookie() {
        return cookie != null && !cookie.isBlank();
    }

    private Map<String, String> headers() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA_PC);
        h.put("Accept", "application/json, text/plain, */*");
        h.put("appsource", "PC");
        h.put("Referer", REFERER);
        if (cookie != null && !cookie.isBlank()) h.put("Cookie", cookie);
        return h;
    }

    private JsonNode post(String path, ObjectNode payload) throws IOException, InterruptedException {
        if (!hasCookie()) {
            throw new IOException("未配置抖音 Cookie：请到「Cookie 管理」→ 抖音 → 浏览器登录"
                    + "（抖音指数接口需要登录态）");
        }
        HttpResponse<String> resp = http.postJson(BASE + path, payload, headers());
        if (resp.statusCode() == 401) {
            throw new IOException("抖音 Cookie 已失效（401 未登录）：请到「Cookie 管理」重新登录抖音");
        }
        if (resp.statusCode() / 100 != 2) {
            throw new IOException("HTTP " + resp.statusCode() + "（" + path + "）");
        }
        JsonNode d;
        try {
            d = HttpClient.parseJson(resp.body());
        } catch (IOException e) {
            throw new IOException("接口返回非 JSON（" + path + "，HTTP " + resp.statusCode() + "）", e);
        }
        if (d.path("status").asInt(-1) != 0) {
            String msg = d.path("msg").asText("");
            throw new IOException("接口返回错误：" + (msg.isEmpty() ? d.path("status").asText("") : msg)
                    + "（" + path + "）");
        }
        return d.path("data");
    }

    private static ObjectNode obj() {
        return MAPPER.createObjectNode();
    }

    // ==================== 单接口 ====================

    /** 按名称/抖音号搜索达人 → 候选列表（含指数系统 uid）。 */
    public List<JsonNode> searchDaren(String keyword, int total)
            throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("keyword", keyword);
        p.put("total", String.valueOf(total));
        JsonNode data = post("/api/v2/daren/get_sug_great_user_list", p);
        return list(data.path("userlist"));
    }

    public ObjectNode authorInfo(String uid) throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("user_id", uid);
        JsonNode d = post("/api/v2/daren/get_author_info", p);
        return d.isObject() ? (ObjectNode) d : obj();
    }

    /** 核心指标：四档新增点赞/净增粉丝/分享/评论/作品 + 环比 + 每日序列。 */
    public ObjectNode coreMetrics(String uid) throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("user_id", uid);
        JsonNode d = post("/api/v2/daren/get_great_user_mile_Info", p);
        return d.isObject() ? (ObjectNode) d : obj();
    }

    /** 稿均指标：近7天/近30天 + 环比。 */
    public ObjectNode itemMetrics(String uid) throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("user_id", uid);
        JsonNode d = post("/api/v2/daren/get_great_item_mile_Info", p);
        return d.isObject() ? (ObjectNode) d : obj();
    }

    public List<JsonNode> topVideos(String uid, String startDate, String endDate)
            throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("user_id", uid);
        p.put("start_date", startDate);
        p.put("end_date", endDate);
        JsonNode data = post("/api/v2/daren/get_great_user_top_video", p);
        return list(data.path("like_list"));
    }

    /** 单视频的视频指数数值（无指数数据返回空串）。 */
    public String itemIndex(String itemId, String startDate, String endDate)
            throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("itemId", itemId == null ? "" : itemId);
        p.put("startDate", startDate);
        p.put("endDate", endDate);
        JsonNode data = post("/api/v2/daren/item_index_exist", p);
        return "succeed".equals(data.path("item_statue").asText(""))
                ? data.path("item_index").asText("") : "";
    }

    /** 粉丝画像：各维度分布占比 + TGI（接口各键为二次 JSON 字符串）。 */
    public Map<String, List<JsonNode>> fansProfile(String uid)
            throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("user_id", uid);
        JsonNode data = post("/api/v2/daren/get_great_user_fans_info", p);
        Map<String, List<JsonNode>> out = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : FANS_KEYS.entrySet()) {
            String key = e.getKey();
            String label = e.getValue();
            List<JsonNode> items = parseDist(data.path(key));
            if ("Gender".equals(key)) cnGender(items);
            if (!items.isEmpty()) out.put(label, items);
            List<JsonNode> tgi = parseDist(data.path(key + "_Tgi"));
            if ("Gender".equals(key)) cnGender(tgi);
            if (!tgi.isEmpty()) out.put(label + "TGI", tgi);
        }
        return out;
    }

    public List<JsonNode> similarUsers(String uid) throws IOException, InterruptedException {
        ObjectNode p = obj();
        p.put("user_id", uid);
        JsonNode data = post("/api/v2/daren/get_similar_users", p);
        return list(data.path("similar_user_list"));
    }

    /** 粉丝画像字段为 JSON 字符串，解析失败返回空列表。 */
    static List<JsonNode> parseDist(JsonNode raw) {
        List<JsonNode> out = new ArrayList<>();
        if (raw == null || raw.isMissingNode() || raw.isNull()) return out;
        JsonNode arr = raw;
        if (raw.isTextual()) {
            try {
                arr = MAPPER.readTree(raw.asText(""));
            } catch (Exception e) {
                return out;
            }
        }
        if (arr == null || !arr.isArray()) return out;
        for (JsonNode x : arr) {
            if (x.isObject()) out.add(x);
        }
        return out;
    }

    private static void cnGender(List<JsonNode> items) {
        for (JsonNode it : items) {
            if (!(it instanceof ObjectNode o)) continue;
            String name = o.path("name").asText("");
            if ("male".equals(name)) o.put("name", "男性");
            else if ("female".equals(name)) o.put("name", "女性");
        }
    }

    private static List<JsonNode> list(JsonNode node) {
        List<JsonNode> out = new ArrayList<>();
        if (node != null && node.isArray()) {
            for (JsonNode x : node) out.add(x);
        }
        return out;
    }

    // ==================== 全量抓取 ====================

    /** 视频统计时间范围：[今天-days+1, 今天]，格式 YYYYMMDD。 */
    public static String[] dateRange(int days) {
        LocalDate end = LocalDate.now();
        LocalDate start = end.minusDays(Math.max(0, days - 1));
        return new String[]{start.format(DAY), end.format(DAY)};
    }

    /** 抓取一个达人的全部抖音指数数据（作者 + 作品 + 粉丝）。 */
    public ObjectNode fetchDarenAll(String uid, int days, Consumer<String> progress)
            throws IOException, InterruptedException {
        Consumer<String> say = progress == null ? msg -> { } : progress;
        String[] range = dateRange(days);
        String startDate = range[0];
        String endDate = range[1];

        say.accept("获取达人信息...");
        ObjectNode author = authorInfo(uid);
        sleep();
        say.accept("获取核心指标（昨日/近7天/近15天/近30天）...");
        ObjectNode core = coreMetrics(uid);
        sleep();
        say.accept("获取稿均指标...");
        ObjectNode itemMetrics = itemMetrics(uid);
        sleep();
        say.accept("获取达人视频列表...");
        List<JsonNode> videos = topVideos(uid, startDate, endDate);
        for (int i = 0; i < videos.size(); i++) {
            sleep();
            say.accept("获取视频指数 " + (i + 1) + "/" + videos.size() + "...");
            JsonNode v = videos.get(i);
            if (v instanceof ObjectNode o) {
                o.put("video_index", itemIndex(o.path("item_id").asText(""), startDate, endDate));
            }
        }
        sleep();
        say.accept("获取粉丝画像（地域/年龄/性别/手机/兴趣）...");
        Map<String, List<JsonNode>> fans = fansProfile(uid);
        sleep();
        say.accept("获取相似达人...");
        List<JsonNode> similar = similarUsers(uid);

        ObjectNode out = obj();
        ObjectNode meta = out.putObject("meta");
        meta.put("platform", "douyin_index");
        meta.put("uid", uid);
        meta.put("fetched_at", LocalDateTime.now().format(FETCHED_AT));
        meta.put("days", days);
        meta.put("start_date", startDate);
        meta.put("end_date", endDate);
        out.set("author", author);
        out.set("core_metrics", core);
        out.set("item_metrics", itemMetrics);
        ArrayNode va = out.putArray("videos");
        videos.forEach(va::add);
        ObjectNode fa = out.putObject("fans_profile");
        for (Map.Entry<String, List<JsonNode>> e : fans.entrySet()) {
            ArrayNode arr = fa.putArray(e.getKey());
            e.getValue().forEach(arr::add);
        }
        ArrayNode sa = out.putArray("similar_users");
        similar.forEach(sa::add);
        return out;
    }

    private static void sleep() {
        try {
            Thread.sleep(400);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    // ==================== 本地保存 ====================

    static String safeName(String name) {
        String s = name == null ? "" : name.trim().replaceAll("[\\\\/:*?\"<>|]", "_");
        return s.isEmpty() ? "未知达人" : s;
    }

    /** 数据保存根目录：<导出目录>/抖音指数/（自动创建）。 */
    public static Path indexDir() {
        Path dir = Settings.load().effectiveExportDir().resolve("抖音指数");
        try {
            Files.createDirectories(dir);
        } catch (Exception ignored) {
        }
        return dir;
    }

    /** 全量快照：抖音指数/<达人名>/抖音指数_时间戳.json（+ latest.json）。 */
    public static Path saveSnapshot(JsonNode data) throws IOException {
        String name = safeName(data.path("author").path("user_name").asText(""));
        Path dir = indexDir().resolve(name);
        Files.createDirectories(dir);
        String ts = LocalDateTime.now().format(FILE_TS);
        Path path = dir.resolve("抖音指数_" + ts + ".json");
        String text = MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(data);
        Files.writeString(path, text, StandardCharsets.UTF_8);
        Files.writeString(dir.resolve("latest.json"), text, StandardCharsets.UTF_8);
        return path;
    }

    // ==================== 展示格式化 ====================

    /** 计数 → 中文紧凑格式：4518751 → 451.9万；34594 → 3.5万；387 → 387。 */
    public static String fmtCount(Object v) {
        double d;
        try {
            d = Double.parseDouble(String.valueOf(v == null ? "0" : v).trim());
        } catch (Exception e) {
            return String.valueOf(v == null ? 0 : v);
        }
        boolean neg = d < 0;
        double n = Math.abs(d);
        String sign = neg ? "-" : "";
        if (n >= 100_000_000) return sign + String.format(Locale.ROOT, "%.2f亿", n / 100_000_000);
        if (n >= 10_000) return sign + String.format(Locale.ROOT, "%.1f万", n / 10_000);
        return sign + String.valueOf((long) n);
    }

    /** 环比小数（0.9915）→ 百分比文本（+99.15%）。 */
    public static String fmtPct(Object v) {
        double x;
        try {
            x = Double.parseDouble(String.valueOf(v).trim());
        } catch (Exception e) {
            return "-";
        }
        return (x > 0 ? "+" : "") + String.format(Locale.ROOT, "%.2f%%", x * 100);
    }
}
