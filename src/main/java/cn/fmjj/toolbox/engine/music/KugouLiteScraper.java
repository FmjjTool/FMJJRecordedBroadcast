package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.math.BigInteger;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.random.RandomGenerator;

/**
 * 酷狗概念版（kugou lite）客户端。
 *
 * <p>移植自 Python 版 {@code music/scrapers/kugou_lite.py}：
 * 三套签名（Android lite / Web / v5 key）、扫码登录、领 VIP、搜索、直链。
 * 会话（token/userid）存 {@code config/kugou_lite_session.json}，不走 CookieStore。
 *
 * <p><b>边界</b>：搜索与直链必须登录（游客报 error_code=152）；
 * 概念版 VIP 可下载 128k/320k/FLAC（每日广告上报可领 +3 小时）。
 */
public final class KugouLiteScraper {

    private static final String SALT_ANDROID_LITE = "LnT6xpN3khm36zse0QzvmgTZ3waWdRSA";
    private static final String SALT_WEB = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt";
    private static final String SALT_SIGNKEY_LITE = "185672dd44712f60bb1736df5a377e82";
    private static final int APPID = 3116;
    private static final int CLIENTVER = 11440;
    private static final int SRCAPPID = 2919;
    private static final String UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi";

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final HttpClient http = new HttpClient();
    private final Map<String, String> cookies = new LinkedHashMap<>();

    private String guid = UUID.randomUUID().toString();
    private String mid;
    private String token = "";
    private long userId;
    private String nickname = "";
    private final String dfid = "-";

    public KugouLiteScraper() {
        applySession();
        if (mid == null || mid.isBlank()) mid = calcMid(guid);
    }

    /** 指定会话（导入/测试用；不读写本机会话文件）。 */
    public KugouLiteScraper(Session session) {
        if (session != null && session.loggedIn()) {
            if (!session.guid().isBlank()) guid = session.guid();
            mid = session.mid().isBlank() ? calcMid(guid) : session.mid();
            token = session.token();
            userId = session.userId();
            nickname = session.nickname();
        } else {
            applySession();
        }
        if (mid == null || mid.isBlank()) mid = calcMid(guid);
    }

    // ==================== 会话 ====================

    /** 已存会话。 */
    public record Session(String guid, String mid, String token, long userId, String nickname) {
        public boolean loggedIn() { return token != null && !token.isBlank(); }
    }

    public static Path sessionFile() {
        return AppPaths.configDir().resolve("kugou_lite_session.json");
    }

    public static synchronized Session loadSession() {
        return readSession(sessionFile());
    }

    /** 从任意文件读会话（导入/测试用）。 */
    public static Session readSession(Path f) {
        if (f == null || !Files.isRegularFile(f)) return new Session("", "", "", 0, "");
        try {
            JsonNode d = JSON.readTree(f.toFile());
            return new Session(d.path("guid").asText(""), d.path("mid").asText(""),
                    d.path("token").asText(""), d.path("userid").asLong(0),
                    d.path("nickname").asText(""));
        } catch (Exception e) {
            return new Session("", "", "", 0, "");
        }
    }

    /** 会话文件时间（Cookie 管理里显示）。 */
    public static String sessionTime() {
        Path f = sessionFile();
        if (!Files.isRegularFile(f)) return "";
        try {
            return LocalDateTime.ofInstant(Files.getLastModifiedTime(f).toInstant(),
                    java.time.ZoneId.systemDefault()).format(TIME_FMT);
        } catch (Exception e) {
            return "";
        }
    }

    public static synchronized void clearSession() {
        try {
            Files.deleteIfExists(sessionFile());
        } catch (Exception ignored) {
        }
    }

    private void applySession() {
        Session s = loadSession();
        if (s.loggedIn()) {
            if (!s.guid().isBlank()) guid = s.guid();
            mid = s.mid().isBlank() ? calcMid(guid) : s.mid();
            token = s.token();
            userId = s.userId();
            nickname = s.nickname();
        }
    }

    private void saveSession() {
        try {
            ObjectNode n = JSON.createObjectNode();
            n.put("guid", guid);
            n.put("mid", mid);
            n.put("token", token);
            n.put("userid", userId);
            n.put("nickname", nickname);
            Files.createDirectories(sessionFile().getParent());
            JSON.writerWithDefaultPrettyPrinter().writeValue(sessionFile().toFile(), n);
        } catch (Exception ignored) {
        }
    }

    public boolean isLoggedIn() { return !token.isBlank(); }

    public String nickname() { return nickname; }

    public long userId() { return userId; }

    // ==================== 签名 ====================

    /** Android(lite)：盐 + 排序参数串 + 请求体 + 盐。 */
    static String signAndroid(Map<String, String> params, String body) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : sorted(params)) sb.append(e.getKey()).append('=').append(e.getValue());
        return md5(SALT_ANDROID_LITE + sb + (body == null ? "" : body) + SALT_ANDROID_LITE);
    }

    /** Web：盐 + 排序参数串 + 盐。 */
    static String signWeb(Map<String, String> params) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : sorted(params)) sb.append(e.getKey()).append('=').append(e.getValue());
        return md5(SALT_WEB + sb + SALT_WEB);
    }

    static String signKey(String hash, String mid, long userId) {
        return md5(hash + SALT_SIGNKEY_LITE + APPID + mid + (userId == 0 ? 0 : userId));
    }

    static String calcMid(String guid) {
        return new BigInteger(md5(guid), 16).toString();
    }

    private static List<Map.Entry<String, String>> sorted(Map<String, String> params) {
        List<Map.Entry<String, String>> list = new ArrayList<>(params.entrySet());
        list.sort(Map.Entry.comparingByKey());
        return list;
    }

    private static String md5(String s) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] digest = md.digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(32);
            for (byte b : digest) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException("MD5 不可用", e);
        }
    }

    // ==================== 请求 ====================

    /** 底层请求；encrypt = "web" 或 "android"。 */
    private JsonNode request(String method, String url, Map<String, Object> extra, String encrypt,
                             String xRouter, String body, String overrideDfid, boolean noToken)
            throws Exception {
        Map<String, String> params = new LinkedHashMap<>();
        String realDfid = overrideDfid == null ? dfid : overrideDfid;
        long clienttime = System.currentTimeMillis() / 1000;
        params.put("dfid", realDfid);
        params.put("mid", mid);
        params.put("uuid", "-");
        params.put("appid", String.valueOf(APPID));
        params.put("clientver", String.valueOf(CLIENTVER));
        params.put("clienttime", String.valueOf(clienttime));
        if (!token.isBlank() && !noToken) {
            params.put("token", token);
            if (userId > 0) params.put("userid", String.valueOf(userId));
        }
        if (extra != null) {
            for (Map.Entry<String, Object> e : extra.entrySet()) {
                params.put(e.getKey(), String.valueOf(e.getValue()));
            }
        }
        params.put("signature", "web".equals(encrypt)
                ? signWeb(params) : signAndroid(params, body));

        StringBuilder qs = new StringBuilder();
        for (Map.Entry<String, String> e : params.entrySet()) {
            if (qs.length() > 0) qs.append('&');
            qs.append(HttpClient.urlEncode(e.getKey())).append('=')
                    .append(HttpClient.urlEncode(e.getValue()));
        }
        String full = url + (url.contains("?") ? "&" : "?") + qs;

        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", UA);
        headers.put("kg-rc", "1");
        headers.put("kg-thash", "5d816a0");
        headers.put("kg-rec", "1");
        headers.put("kg-rf", "B9EDA08A64250DEFFBCADDEE00F8F25F");
        headers.put("dfid", realDfid);
        headers.put("mid", mid);
        headers.put("clienttime", String.valueOf(clienttime));
        if (xRouter != null) headers.put("x-router", xRouter);
        if (!cookies.isEmpty()) {
            StringBuilder c = new StringBuilder();
            for (Map.Entry<String, String> e : cookies.entrySet()) {
                if (c.length() > 0) c.append("; ");
                c.append(e.getKey()).append('=').append(e.getValue());
            }
            headers.put("Cookie", c.toString());
        }

        HttpResponse<String> resp;
        if ("POST".equalsIgnoreCase(method)) {
            headers.put("Content-Type", "application/json");
            ObjectNode node = (ObjectNode) JSON.readTree(body);
            resp = http.postJson(full, node, headers);
        } else {
            resp = http.get(full, headers);
        }
        absorbCookies(resp);
        try {
            return JSON.readTree(resp.body());
        } catch (Exception e) {
            ObjectNode n = JSON.createObjectNode();
            n.put("_raw", resp.body() == null ? "" : resp.body().substring(0, Math.min(500, resp.body().length())));
            n.put("_status", resp.statusCode());
            return n;
        }
    }

    private void absorbCookies(HttpResponse<String> resp) {
        for (String header : resp.headers().allValues("Set-Cookie")) {
            int semi = header.indexOf(';');
            String kv = semi >= 0 ? header.substring(0, semi) : header;
            int eq = kv.indexOf('=');
            if (eq > 0) cookies.put(kv.substring(0, eq).strip(), kv.substring(eq + 1).strip());
        }
    }

    // ==================== 扫码登录 ====================

    /** 二维码：key + 官方 PNG（可能为空）+ 本地渲染内容。 */
    public record QrCode(String key, byte[] image, String content) {}

    public QrCode qrCreate() throws Exception {
        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("appid", 1001);
        extra.put("type", 1);
        extra.put("plat", 4);
        extra.put("qrcode_txt", "https://h5.kugou.com/apps/loginQRCode/html/index.html?appid=" + APPID + "&");
        extra.put("srcappid", SRCAPPID);
        JsonNode d = request("GET", "https://login-user.kugou.com/v2/qrcode",
                extra, "web", null, null, "-", true);
        JsonNode data = d.path("data");
        String key = data.path("qrcode").asText(data.path("key").asText(""));
        if (key.isBlank()) {
            throw new IllegalStateException("生成二维码失败：" + abbreviate(d));
        }
        byte[] png = null;
        String img = data.path("qrcode_img").asText("");
        if (img.startsWith("data:image") && img.contains(",")) {
            try {
                png = Base64.getDecoder().decode(img.substring(img.indexOf(',') + 1));
            } catch (Exception ignored) {
            }
        }
        String content = "https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode=" + key;
        return new QrCode(key, png, content);
    }

    /** 扫码状态：1=等扫码 2=待确认 4=成功 0=过期。 */
    public record Poll(int status, String message, boolean loggedIn) {}

    public Poll qrPoll(String qrcodeKey) throws Exception {
        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("plat", 4);
        extra.put("appid", APPID);
        extra.put("srcappid", SRCAPPID);
        extra.put("qrcode", qrcodeKey);
        String dev = cookies.get("KUGOU_API_DEV");
        if (dev != null && !dev.isBlank()) extra.put("dev", dev);

        JsonNode d = request("GET", "https://login-user.kugou.com/v2/get_userinfo_qrcode",
                extra, "web", null, null, "-", true);
        JsonNode data = d.path("data");
        int st = data.hasNonNull("status") ? data.get("status").asInt(1) : 1;
        if (st == 4) {
            token = data.path("token").asText("");
            userId = data.path("userid").asLong(0);
            JsonNode ui = data.has("userinfo") ? data.path("userinfo") : data.path("user_info");
            nickname = firstNonEmpty(data.path("nickname").asText(""),
                    data.path("NickName").asText(""),
                    ui.path("nickname").asText(""), ui.path("NickName").asText(""),
                    ui.path("user_name").asText(""), nickname);
            if (!token.isBlank()) {
                saveSession();
                return new Poll(4, "登录成功", true);
            }
            return new Poll(4, "已确认但未返回 token", false);
        }
        if (st == 0) return new Poll(0, "二维码已过期，请重新登录", false);
        if (st == 2) return new Poll(2, "已扫码，请在手机上确认", false);
        return new Poll(st, "等待扫码", false);
    }

    // ==================== 会话校验 / 领 VIP ====================

    public record Verify(boolean ok, String message) {}

    public Verify verifySession() {
        if (token.isBlank()) return new Verify(false, "未登录（无会话文件）");
        try {
            Map<String, Object> extra = new LinkedHashMap<>();
            extra.put("keyword", "a");
            extra.put("page", 1);
            extra.put("pagesize", 1);
            extra.put("platform", "AndroidFilter");
            extra.put("albumhide", 0);
            extra.put("iscorrection", 1);
            extra.put("nocollect", 0);
            JsonNode d = request("GET", "https://gateway.kugou.com/v3/search/song",
                    extra, "android", "complexsearch.kugou.com", null, null, false);
            if (d.path("status").asInt(0) == 1) return new Verify(true, "会话有效");
            int err = d.path("error_code").asInt(0);
            if (err == 152) return new Verify(false, "token 已失效（被识别为游客），请重新扫码登录");
            return new Verify(false, "验证失败：status=" + d.path("status").asText("")
                    + " error_code=" + err);
        } catch (Exception e) {
            return new Verify(false, "验证请求失败：" + e.getMessage());
        }
    }

    public record Claim(boolean ok, String message) {}

    public Claim claimVip() {
        if (token.isBlank()) return new Claim(false, "请先扫码登录酷狗概念版");
        try {
            long now = System.currentTimeMillis();
            String body = "{\"ad_id\":12307537187,\"play_end\":" + now
                    + ",\"play_start\":" + (now - 30000) + "}";
            JsonNode d = request("POST", "https://gateway.kugou.com/youth/v1/ad/play_report",
                    Map.of(), "android", null, body, null, false);
            int st = d.path("status").asInt(0);
            int err = d.path("error_code").asInt(0);
            if (st == 1) return new Claim(true, "领取成功（VIP +3 小时）");
            if (err == 20026) return new Claim(true, "今日领取次数已达上限（每日 8 次）");
            return new Claim(false, "领取未确认（status=" + d.path("status").asText("")
                    + " error_code=" + err + "），需在手机 App 验证");
        } catch (Exception e) {
            return new Claim(false, "领取请求失败：" + e.getMessage());
        }
    }

    // ==================== 搜索 / 直链 ====================

    /** v3 搜索结果原始项（FileHash/FileName/PayType…）。 */
    public List<JsonNode> searchItems(String keyword, int page, int pageSize) throws Exception {
        if (token.isBlank()) throw new IllegalStateException("酷狗概念版未登录（需先在 Cookie 管理扫码登录）");
        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("keyword", keyword);
        extra.put("page", page);
        extra.put("pagesize", pageSize);
        extra.put("platform", "AndroidFilter");
        extra.put("albumhide", 0);
        extra.put("iscorrection", 1);
        extra.put("nocollect", 0);
        JsonNode d = request("GET", "https://gateway.kugou.com/v3/search/song",
                extra, "android", "complexsearch.kugou.com", null, null, false);
        if (d.path("status").asInt(0) != 1) {
            throw new IllegalStateException("搜索失败：status=" + d.path("status").asText("")
                    + " error_code=" + d.path("error_code").asText(""));
        }
        List<JsonNode> out = new ArrayList<>();
        JsonNode lists = d.path("data").path("lists");
        if (lists.isArray()) for (JsonNode it : lists) out.add(it);
        return out;
    }

    public List<MusicSong> searchSong(String keyword, int page, int limit) {
        try {
            List<MusicSong> songs = new ArrayList<>();
            for (JsonNode it : searchItems(keyword, page, limit)) {
                MusicSong s = fromSearchItem(it);
                if (s != null) songs.add(s);
            }
            return songs.size() > limit ? songs.subList(0, limit) : songs;
        } catch (IllegalStateException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalStateException("酷狗概念版搜索失败：" + e.getMessage(), e);
        }
    }

    /** 搜索结果映射（纯函数，可离线测试）。 */
    public static MusicSong fromSearchItem(JsonNode it) {
        String h = firstNonEmpty(it.path("FileHash").asText(""), it.path("Hash").asText(""),
                it.path("hash").asText("")).toLowerCase(Locale.ROOT);
        if (h.isBlank()) return null;
        String name = firstNonEmpty(it.path("FileName").asText(""), it.path("SongName").asText(""));
        String singer = firstNonEmpty(it.path("SingerName").asText(""), it.path("singername").asText(""));
        if (singer.isBlank() && name.contains(" - ")) {
            int idx = name.indexOf(" - ");
            singer = name.substring(0, idx).strip();
            name = name.substring(idx + 3).strip();
        }
        int duration = it.path("Duration").asInt(it.path("duration").asInt(0));
        String cover = it.path("Image").asText("").replace("{size}", "480");
        return new MusicSong("kugou_lite", h, name, singer,
                it.path("AlbumName").asText(""), duration, cover,
                "https://www.kugou.com/song/#hash=" + h);
    }

    public record PlayInfo(String url, String ext, String quality, String error) {}

    /** quality: 128 / 320 / flac。 */
    public PlayInfo playInfo(String hash, String quality) {
        if (token.isBlank()) return new PlayInfo("", "", "", "酷狗概念版未登录（需先在 Cookie 管理扫码登录）");
        try {
            String h = hash == null ? "" : hash.toLowerCase(Locale.ROOT);
            Map<String, Object> extra = new LinkedHashMap<>();
            extra.put("album_id", 0);
            extra.put("area_code", 1);
            extra.put("hash", h);
            extra.put("ssa_flag", "is_fromtrack");
            extra.put("version", 11430);
            extra.put("page_id", 967177915);
            extra.put("quality", quality);
            extra.put("album_audio_id", 0);
            extra.put("behavior", "play");
            extra.put("pid", 411);
            extra.put("cmd", 26);
            extra.put("pidversion", 3001);
            extra.put("IsFreePart", 0);
            extra.put("ppage_id", "356753938,823673182,967485191");
            extra.put("cdnBackup", 1);
            extra.put("module", "");
            extra.put("key", signKey(h, mid, userId));
            String randomDfid = randomDfid();

            JsonNode d = request("GET", "https://gateway.kugou.com/v5/url",
                    extra, "android", "trackercdn.kugou.com", null, randomDfid, false);
            String url = "";
            JsonNode u = d.path("url");
            if (u.isArray() && u.size() > 0) url = u.get(0).asText("");
            else if (u.isTextual()) url = u.asText("");
            if (url.isBlank()) {
                JsonNode backup = d.path("backupUrl");
                if (backup.isArray() && backup.size() > 0) url = backup.get(0).asText("");
            }
            if (d.path("status").asInt(0) == 1 && !url.isBlank()) {
                String ext = d.path("extName").asText("mp3").toLowerCase(Locale.ROOT);
                if (!ext.startsWith(".")) ext = "." + ext;
                String tag = "flac".equals(quality) ? "FLAC"
                        : "320".equals(quality) ? "320kbps" : "128kbps";
                return new PlayInfo(url, ext, tag, "");
            }
            int err = d.path("error_code").asInt(0);
            String msg = "未拿到播放链接（status=" + d.path("status").asText("")
                    + " error=" + err + "）";
            if (err == 20020 || err == 20021 || err == 30004) {
                msg += "：会员歌曲，请先在 Cookie 管理领取概念版 VIP 后重试";
            }
            return new PlayInfo("", "", "", msg);
        } catch (Exception e) {
            return new PlayInfo("", "", "", "酷狗概念版直链请求失败：" + e.getMessage());
        }
    }

    /** 详情：hash 与主版酷狗同库，复用游客接口。 */
    public MusicSong songDetail(String hash) {
        try {
            return new KugouScraper().songDetail(hash);
        } catch (Exception e) {
            return new MusicSong("kugou_lite", hash, hash, "", "", 0, "",
                    "https://www.kugou.com/song/#hash=" + hash);
        }
    }

    public static String extractSongId(String text) {
        return KugouScraper.extractSongId(text);
    }

    // ==================== 工具 ====================

    private static String randomDfid() {
        String chars = "abcdefghijklmnopqrstuvwxyz0123456789";
        RandomGenerator r = RandomGenerator.getDefault();
        StringBuilder sb = new StringBuilder(24);
        for (int i = 0; i < 24; i++) sb.append(chars.charAt(r.nextInt(chars.length())));
        return sb.toString();
    }

    private static String firstNonEmpty(String... values) {
        for (String v : values) if (v != null && !v.isEmpty()) return v;
        return "";
    }

    private static String abbreviate(JsonNode d) {
        String s = d.toString();
        return s.length() <= 200 ? s : s.substring(0, 200);
    }
}
