package cn.fmjj.toolbox.engine.auth;

import cn.fmjj.toolbox.core.AppPaths;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Cookie 仓库（12 平台统一存储）。
 * 逐行移植自 Python 版 src/auth.py：cookies.json 结构、enc: 前缀加密、
 * TTL、总开关全部一致；Java 版与原版共享同一份文件，登录态互通。
 *
 * 平台登录配置（URL / 登录特征 Cookie / 提示）移植自 cookie_manager.py。
 */
public final class CookieStore {

    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final ObjectMapper JSON = new ObjectMapper();

    /** Python: COOKIE_TTL_DAYS（0 = 永不过期）。 */
    public static final Map<String, Integer> TTL_DAYS = Map.of("weibo", 30);

    /** Python: PLATFORM_NAMES。 */
    public static final Map<String, String> PLATFORM_NAMES = new LinkedHashMap<>() {{
        put("bilibili", "B站");
        put("douyin", "抖音");
        put("weibo", "微博");
        put("netease", "网易云");
        put("qq", "QQ音乐");
        put("kugou", "酷狗");
        put("kugou_lite", "酷狗概念版");
        put("kuwo", "酷我");
        put("5sing", "5sing");
        put("xiaohongshu", "小红书");
        put("kuaishou", "快手");
        put("taobao", "淘宝");
    }};

    /** 浏览器登录配置：(url, 标题, 登录特征 cookie, 操作提示) —— 移植自 cookie_manager.py configs。 */
    public record LoginConfig(String url, String title, String[] markers, String hint) {}

    public static final Map<String, LoginConfig> BROWSER_LOGIN = new LinkedHashMap<>() {{
        put("weibo", new LoginConfig("https://m.weibo.cn/", "微博登录（软件内）", new String[]{"SUB"}, ""));
        put("douyin", new LoginConfig("https://www.douyin.com/", "抖音登录（软件内）", new String[]{"sessionid"}, ""));
        put("bilibili", new LoginConfig("https://www.bilibili.com/", "B站登录（软件内）", new String[]{"SESSDATA"}, ""));
        put("qq", new LoginConfig("https://y.qq.com/", "QQ音乐登录（软件内）",
                new String[]{"uin", "qm_keyst"}, "点击右上角「登录」→ 扫码或账号登录"));
        put("kugou", new LoginConfig("https://www.kugou.com/", "酷狗登录（软件内）",
                new String[]{"KugouID", "userid", "token"}, "点击右上角「登录」→ 扫码或账号登录"));
        put("kuwo", new LoginConfig("https://www.kuwo.cn/", "酷我登录（软件内）",
                new String[]{"userid"}, "点击顶部「登录」→ 手机号验证码登录"));
        put("5sing", new LoginConfig("https://5sing.kugou.com/login", "5sing登录（软件内）",
                new String[]{"userid"}, "账号密码登录（支持酷狗账号）"));
        put("xiaohongshu", new LoginConfig("https://www.xiaohongshu.com/", "小红书登录（软件内）",
                new String[]{"web_session"}, "点击「登录」→ 扫码或短信验证"));
        put("kuaishou", new LoginConfig("https://www.kuaishou.com/account/login", "快手登录（软件内）",
                new String[]{"kuaishou.server.web_st", "kuaishou.server.webday7_st"},
                "扫码 / 手机号验证码登录"));
        put("taobao", new LoginConfig("https://www.taobao.com/", "淘宝登录（软件内）",
                new String[]{"cookie2", "unb", "sg"}, "点击顶部「登录」→ 扫码或手机号登录"));
    }};

    private static Path file() { return AppPaths.configDir().resolve("cookies.json"); }

    private CookieStore() {}

    // ==================== 内部 IO ====================

    private static JsonNode loadRaw() {
        Path f = file();
        if (!Files.isRegularFile(f)) return JSON.createObjectNode();
        try {
            return JSON.readTree(f.toFile());
        } catch (Exception e) {
            return JSON.createObjectNode();
        }
    }

    private static synchronized void saveRaw(ObjectNode raw) {
        Path f = file();
        try {
            // 写前备份：任何写入都把旧文件留一份 cookies.json.bak
            // （曾经出过事故：单元测试直接删了真实 cookies.json，无备份无法恢复）
            if (Files.isRegularFile(f)) {
                Files.copy(f, f.resolveSibling("cookies.json.bak"),
                        java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            }
            JSON.writerWithDefaultPrettyPrinter().writeValue(f.toFile(), raw);
        } catch (IOException ignored) {
            // 只读场景由调用方处理
        }
    }

    private static ObjectNode ensureObject(JsonNode node) {
        return node != null && node.isObject() ? (ObjectNode) node : JSON.createObjectNode();
    }

    private static byte[] key() { return FernetCompat.deriveKey(FernetCompat.machineGuid()); }

    // ==================== 对外 API（移植 auth.py） ====================

    /** Python: load_cookies() —— 自动解密；旧格式明文原样返回。 */
    public static synchronized Map<String, String> loadCookies() {
        JsonNode raw = loadRaw();
        JsonNode cookies = raw.path("cookies");
        Map<String, String> out = new LinkedHashMap<>();
        if (cookies.isObject()) {
            cookies.fields().forEachRemaining(e -> {
                if (e.getValue().isTextual()) out.put(e.getKey(), decrypt(e.getValue().asText()));
            });
        }
        return out;
    }

    /** Python: _load_cookie_times()。 */
    public static synchronized Map<String, String> loadTimes() {
        JsonNode raw = loadRaw();
        Map<String, String> times = new LinkedHashMap<>();
        JsonNode t = raw.path("cookie_times");
        if (t.isObject()) {
            t.fields().forEachRemaining(e -> {
                if (e.getValue().isTextual()) times.put(e.getKey(), e.getValue().asText());
            });
        }
        // 兼容旧格式：从 updated_at 推断
        if (times.isEmpty() && raw.has("updated_at") && raw.path("cookies").isObject()) {
            String global = raw.path("updated_at").asText();
            raw.path("cookies").fieldNames().forEachRemaining(p -> times.put(p, global));
        }
        return times;
    }

    /** Python: is_cookie_enabled()（默认开启）。 */
    public static synchronized boolean isEnabled() {
        return loadRaw().path("cookie_enabled").asBoolean(true);
    }

    public static synchronized void setEnabled(boolean enabled) {
        ObjectNode raw = ensureObject(loadRaw());
        raw.put("cookie_enabled", enabled);
        saveRaw(raw);
    }

    /** Python: get_cookie() —— 总开关关闭或 TTL 过期返回空串。 */
    public static synchronized String get(String platform) {
        if (!isEnabled()) return "";
        String cookie = loadCookies().getOrDefault(platform, "");
        if (cookie.isEmpty()) return "";

        int ttl = TTL_DAYS.getOrDefault(platform, 0);
        if (ttl <= 0) return cookie;

        String savedAt = loadTimes().getOrDefault(platform, "");
        if (savedAt.isEmpty()) return cookie;
        try {
            LocalDateTime dt = LocalDateTime.parse(savedAt, TIME_FMT);
            if (LocalDateTime.now().minusDays(ttl).isAfter(dt)) return "";
        } catch (Exception ignored) {
            // 解析不了先放行（与 Python 的 except ValueError: pass 一致）
        }
        return cookie;
    }

    /** Python: set_cookie() —— 加密保存 + 记录时间。 */
    public static synchronized void set(String platform, String cookieStr) {
        Map<String, String> all = loadCookies();
        all.put(platform, cookieStr == null ? "" : cookieStr);
        Map<String, String> times = loadTimes();
        times.put(platform, LocalDateTime.now().format(TIME_FMT));

        ObjectNode raw = ensureObject(loadRaw());
        ObjectNode enc = raw.putObject("cookies");
        all.forEach((k, v) -> enc.put(k, encrypt(v)));
        ObjectNode t = raw.putObject("cookie_times");
        times.forEach(t::put);
        raw.put("updated_at", LocalDateTime.now().format(TIME_FMT));
        saveRaw(raw);
    }

    /** Python: clear_cookie()。 */
    public static synchronized void clear(String platform) {
        Map<String, String> all = loadCookies();
        all.remove(platform);
        Map<String, String> times = loadTimes();
        times.remove(platform);

        ObjectNode raw = ensureObject(loadRaw());
        ObjectNode enc = raw.putObject("cookies");
        all.forEach((k, v) -> enc.put(k, encrypt(v)));
        ObjectNode t = raw.putObject("cookie_times");
        times.forEach(t::put);
        raw.put("updated_at", LocalDateTime.now().format(TIME_FMT));
        saveRaw(raw);
    }

    // ==================== enc: 编解码 ====================

    /** Python: _encrypt_cookie —— 空串空处理；失败降级返回原文（不丢数据）。 */
    public static String encrypt(String plain) {
        if (plain == null || plain.isEmpty()) return "";
        try {
            return "enc:" + FernetCompat.encrypt(plain, key());
        } catch (Exception e) {
            return plain;
        }
    }

    /** Python: _decrypt_cookie —— 无 enc: 前缀原样返回；解密失败返回空串。 */
    public static String decrypt(String stored) {
        if (stored == null || stored.isEmpty()) return "";
        if (!stored.startsWith("enc:")) return stored;
        try {
            return FernetCompat.decrypt(stored.substring(4), key());
        } catch (Exception e) {
            return "";
        }
    }

    /** 供 UI 展示：平台的中文名。 */
    public static String displayName(String platform) {
        return PLATFORM_NAMES.getOrDefault(platform, platform);
    }

    /** 平台是否过期（供 UI 标红）；未记录时间视为不过期。 */
    public static synchronized boolean expired(String platform) {
        int ttl = TTL_DAYS.getOrDefault(platform, 0);
        if (ttl <= 0) return false;
        String savedAt = loadTimes().getOrDefault(platform, "");
        if (savedAt.isEmpty()) return false;
        try {
            return LocalDateTime.now().minusDays(ttl).isAfter(LocalDateTime.parse(savedAt, TIME_FMT));
        } catch (Exception e) {
            return false;
        }
    }
}
