package cn.fmjj.toolbox.core.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner.WbiKeys;
import cn.fmjj.toolbox.engine.net.HttpClient;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * B 站 Web 鉴权网络层：WBI 密钥获取、buvid、bili_ticket、Cookie 头拼接、
 * 扫码登录（Web 二维码）。
 * 移植自 Python 版 src/bili/wbi.py 的网络部分 + cookie_manager.py 的扫码流程。
 */
public final class BiliAuth {

    private static final Pattern RENDER_DATA = Pattern.compile(
            "<script id=\"__RENDER_DATA__\" type=\"application/json\">([^<]+)</script>");

    private volatile CachedKeys wbiCache; // 60 分钟
    private volatile Map<String, String> buvidCache;
    private volatile TicketCache ticketCache;

    private final HttpClient http;

    public BiliAuth() { this(new HttpClient()); }

    public BiliAuth(HttpClient http) { this.http = http; }

    private record CachedKeys(WbiKeys keys, long ts) {}
    private record TicketCache(String ticket, long expiresAt) {}

    // ==================== WBI ====================

    /** Python: _get_wbi_keys —— nav 取 img/sub key，空间页取 w_webid，缓存 60 分钟。 */
    public WbiKeys wbiKeys() throws Exception {
        if (wbiCache != null && Instant.now().getEpochSecond() - wbiCache.ts() < 3600) {
            return wbiCache.keys();
        }
        synchronized (this) {
            if (wbiCache != null && Instant.now().getEpochSecond() - wbiCache.ts() < 3600) {
                return wbiCache.keys();
            }
            String imgKey = null, subKey = null;
            var nav = http.getJson("https://api.bilibili.com/x/web-interface/nav", Map.of(
                    "User-Agent", WbiSigner.UA, "Referer", WbiSigner.REFERER_BILI));
            var wbiImg = nav.path("data").path("wbi_img");
            imgKey = stripKey(wbiImg.path("img_url").asText(""));
            subKey = stripKey(wbiImg.path("sub_url").asText(""));

            String wWebid = "";
            try {
                String html = http.getText("https://space.bilibili.com/1",
                        Map.of("User-Agent", WbiSigner.UA));
                wWebid = extractWWebid(html);
            } catch (Exception ignored) {
                // 空间页失败不影响签名可用性（与 Python try/except 一致）
            }
            WbiKeys keys = new WbiKeys(imgKey, subKey, wWebid);
            wbiCache = new CachedKeys(keys, Instant.now().getEpochSecond());
            return keys;
        }
    }

    /** Python: refresh_wbi。 */
    public void refreshWbi() { wbiCache = null; }

    /** 从 wbi_img URL 取 key（.../xxx.png → xxx）。 */
    static String stripKey(String url) {
        int slash = url.lastIndexOf('/');
        String name = slash >= 0 ? url.substring(slash + 1) : url;
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }

    /** Python: __RENDER_DATA__ → access_id。解码逻辑对应 urllib.parse.unquote。 */
    public static String extractWWebid(String html) {
        Matcher m = RENDER_DATA.matcher(html);
        if (!m.find()) return "";
        try {
            String json = java.net.URLDecoder.decode(m.group(1), StandardCharsets.UTF_8);
            var node = HttpClient.parseJson(json);
            String accessId = node.path("access_id").asText("");
            return accessId == null ? "" : accessId;
        } catch (Exception e) {
            return "";
        }
    }

    // ==================== 设备指纹 / 票据 ====================

    /** Python: get_buvid —— 失败兜底 "1"。 */
    public Map<String, String> buvid() throws Exception {
        if (buvidCache != null) return buvidCache;
        synchronized (this) {
            if (buvidCache != null) return buvidCache;
            String buvid3 = "1", buvid4 = "1";
            try {
                var d = http.getJson("https://api.bilibili.com/x/frontend/finger/spi", Map.of(
                        "User-Agent", WbiSigner.UA, "Referer", WbiSigner.REFERER_BILI));
                var data = d.path("data");
                buvid3 = data.path("b_3").asText("1");
                buvid4 = data.path("b_4").asText("1");
            } catch (Exception ignored) {
            }
            Map<String, String> out = new HashMap<>();
            out.put("buvid3", buvid3);
            out.put("buvid4", buvid4);
            out.put("b_nut", String.valueOf(Instant.now().getEpochSecond()));
            buvidCache = out;
            return out;
        }
    }

    /** Python: get_bili_ticket —— GenWebTicket，HMAC-SHA256 签名，约 3 天有效。 */
    public String biliTicket() throws Exception {
        if (ticketCache != null && ticketCache.expiresAt() > Instant.now().getEpochSecond() + 60) {
            return ticketCache.ticket();
        }
        synchronized (this) {
            if (ticketCache != null && ticketCache.expiresAt() > Instant.now().getEpochSecond() + 60) {
                return ticketCache.ticket();
            }
            try {
                Map<String, String> buvid = buvid();
                long ts = Instant.now().getEpochSecond();
                String hexsign = hmacSha256Hex("XgwSnGZ1p", "ts" + ts);
                String url = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
                        + "?key_id=ec02&hexsign=" + hexsign
                        + "&context%5Bts%5D=" + ts + "&csrf=";
                var d = http.getJson(url, Map.of(
                        "User-Agent", WbiSigner.UA, "Referer", WbiSigner.REFERER_BILI,
                        "Cookie", "buvid3=" + buvid.get("buvid3") + "; buvid4=" + buvid.get("buvid4")));
                var data = d.path("data");
                String ticket = data.path("ticket").asText("");
                long expiresAt = data.path("created_at").asLong(0) + data.path("ttl").asLong(0);
                if (!ticket.isEmpty()) {
                    ticketCache = new TicketCache(ticket, expiresAt);
                }
                return ticket;
            } catch (Exception e) {
                return "";
            }
        }
    }

    /** Python: build_cookie_header。 */
    public String buildCookieHeader(String loginCookie) throws Exception {
        if (loginCookie != null && !loginCookie.isEmpty()) {
            String ticket = biliTicket();
            return !ticket.isEmpty() ? loginCookie + "; bili_ticket=" + ticket : loginCookie;
        }
        Map<String, String> buvid = buvid();
        String ticket = biliTicket();
        StringBuilder sb = new StringBuilder();
        sb.append("buvid3=").append(buvid.get("buvid3"))
          .append("; buvid4=").append(buvid.get("buvid4"))
          .append("; b_nut=").append(buvid.get("b_nut"));
        if (!ticket.isEmpty()) sb.append("; bili_ticket=").append(ticket);
        return sb.toString();
    }

    // ==================== 扫码登录（Web 二维码） ====================

    /** Python cookie_manager._bilibili_qr。返回 (登录 URL, qrcode_key)。 */
    public String[] qrGenerate() throws Exception {
        var d = http.getJson("https://passport.bilibili.com/x/passport-login/web/qrcode/generate", Map.of());
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("生成二维码失败");
        }
        return new String[] {d.path("data").path("url").asText(), d.path("data").path("qrcode_key").asText()};
    }

    /** 轮询结果（含 Set-Cookie 收集，供登录成功后保存）。 */
    public record QrPollResult(QrCode code, String message, Map<String, String> cookies, String redirectUrl) {}

    /** Python: POLL 循环体的码值映射。 */
    public enum QrCode {
        SUCCESS("登录成功"),
        WAITING("⏳ 等待扫码..."),
        SCANNED("✅ 扫码成功，请在手机上确认"),
        EXPIRED("⚠️ 二维码已失效，请重新登录"),
        FAILED("登录失败");

        public final String message;
        QrCode(String message) { this.message = message; }
    }

    /**
     * Python: POLL 一次 + cookie 收集。
     * 原版用 requests.Session 自动收 cookie；这里手动解析 Set-Cookie，
     * 并在 SESSDATA 缺失时补一次跳转（对应 Python 的 `if "SESSDATA" not in cookies` 分支）。
     */
    public QrPollResult qrPoll(String key) throws Exception {
        var resp = http.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key="
                        + HttpClient.urlEncode(key), Map.of());
        var pd = HttpClient.parseJson(resp.body()).path("data");
        int code = pd.path("code").asInt(-1);
        Map<String, String> cookies = collectSetCookies(resp);
        String redirectUrl = pd.path("url").asText("");

        QrCode status;
        String message;
        if (code == 0) {
            status = QrCode.SUCCESS;
            message = QrCode.SUCCESS.message;
            if (!cookies.containsKey("SESSDATA") && !redirectUrl.isEmpty()) {
                // Python: 再 GET 一次登录跳转地址补齐 cookie
                try {
                    var r2 = http.get(redirectUrl, Map.of());
                    cookies.putAll(collectSetCookies(r2));
                } catch (Exception ignored) {
                }
            }
        } else if (code == 86101) {
            status = QrCode.WAITING;
            message = QrCode.WAITING.message;
        } else if (code == 86090) {
            status = QrCode.SCANNED;
            message = QrCode.SCANNED.message;
        } else if (code == 86038) {
            status = QrCode.EXPIRED;
            message = QrCode.EXPIRED.message;
        } else {
            status = QrCode.FAILED;
            message = "登录失败，错误码 " + code;
        }
        return new QrPollResult(status, message, cookies, redirectUrl);
    }

    /** 从响应头收集 Set-Cookie（name=value，取第一个分号前）。 */
    static Map<String, String> collectSetCookies(java.net.http.HttpResponse<String> resp) {
        Map<String, String> out = new java.util.LinkedHashMap<>();
        for (String header : resp.headers().allValues("Set-Cookie")) {
            int eq = header.indexOf('=');
            if (eq <= 0) continue;
            String name = header.substring(0, eq).trim();
            String value = header.substring(eq + 1);
            int semi = value.indexOf(';');
            if (semi >= 0) value = value.substring(0, semi);
            if (!name.isEmpty()) out.put(name, value.trim());
        }
        return out;
    }

    /** Python: cookie_str = "; ".join(k=v)。供扫码成功后从响应 Set-Cookie 组装。 */
    public static String formatCookieHeader(Map<String, String> cookies) {
        StringBuilder sb = new StringBuilder();
        cookies.forEach((k, v) -> {
            if (v == null || v.isEmpty()) return;
            if (sb.length() > 0) sb.append("; ");
            sb.append(k).append('=').append(v);
        });
        return sb.toString();
    }

    // ==================== 工具 ====================

    static String hmacSha256Hex(String secret, String data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] digest = mac.doFinal(data.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(64);
            for (byte b : digest) sb.append(String.format("%02x", b, Locale.ROOT));
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException("HMAC-SHA256 不可用", e);
        }
    }
}
