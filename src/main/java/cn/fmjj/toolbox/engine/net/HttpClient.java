package cn.fmjj.toolbox.engine.net;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.StringJoiner;

/**
 * 网络层封装（java.net.http）。
 * 对应 Python 版 httpx/requests Session 的常用姿势：统一 UA、JSON 解析、
 * 失败重试（指数退避）、form/查询串构造。
 *
 * 引擎层可用（零 GUI 依赖）；会话/限流策略在各页面按需组合。
 */
public final class HttpClient {

    /** 与 Python 版 wbi.py 的 UA 保持一致。 */
    public static final String UA_FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0";
    /** 扫码登录用的 Chrome UA（与 Python 版 cookie_manager 一致）。 */
    public static final String UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36";
    public static final String REFERER_BILI = "https://www.bilibili.com/";

    private static final ObjectMapper JSON = new ObjectMapper();

    private final java.net.http.HttpClient client;
    private final int maxRetries;
    private final long retryBaseMillis;

    public HttpClient() {
        this(2, 300);
    }

    public HttpClient(int maxRetries, long retryBaseMillis) {
        this.maxRetries = Math.max(0, maxRetries);
        this.retryBaseMillis = Math.max(0, retryBaseMillis);
        this.client = java.net.http.HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(java.net.http.HttpClient.Redirect.NORMAL)
                .build();
    }

    /** 暴露底层 JDK client（上传/流式等场景）。 */
    public java.net.http.HttpClient jdkClient() { return client; }

    // ==================== 请求执行 ====================

    /** 发送并拿到字符串响应；失败按重试策略重发。 */
    public HttpResponse<String> send(HttpRequest request) throws IOException, InterruptedException {
        return executeWithRetry(request, r -> client.send(r, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)));
    }

    /**
     * 可注入的重试执行器（测试用假 sender 驱动，不依赖真实网络）。
     * 规则：IO 异常 / 5xx 重试；重试次数耗尽后抛最后一次异常。
     */
    @FunctionalInterface
    public interface Sender {
        HttpResponse<String> send(HttpRequest request) throws IOException, InterruptedException;
    }

    public HttpResponse<String> executeWithRetry(HttpRequest request, Sender sender)
            throws IOException, InterruptedException {
        IOException last = null;
        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            if (attempt > 0 && retryBaseMillis > 0) {
                try {
                    Thread.sleep(retryBaseMillis * (1L << (attempt - 1)));
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new IOException("重试等待被中断", ie);
                }
            }
            try {
                HttpResponse<String> resp = sender.send(request);
                if (resp.statusCode() >= 500) {
                    last = new IOException("HTTP " + resp.statusCode());
                    if (attempt < maxRetries) continue;
                    throw last;
                }
                return resp;
            } catch (IOException e) {
                last = e;
            }
        }
        throw last != null ? last : new IOException("请求失败");
    }

    private HttpRequest.Builder base(String url, Map<String, String> headers) {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url));
        b.header("User-Agent", UA_FIREFOX);
        if (headers != null) headers.forEach(b::header);
        return b;
    }

    // ==================== 便捷方法 ====================

    /** 不跟随重定向的结果（用于拿 302 Location 直链，如酷我 antiserver）。 */
    public record RedirectResult(int status, String location, String body) {}

    /**
     * GET 但不跟随重定向，返回状态码 + Location。
     * 酷我等 CDN 的 302 目标证书较弱，直接读 Location 可避免 TLS 校验失败。
     */
    public RedirectResult getRedirect(String url, Map<String, String> headers) throws IOException, InterruptedException {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url))
                .timeout(Duration.ofSeconds(20))
                .GET();
        b.header("User-Agent", UA_FIREFOX);
        if (headers != null) headers.forEach(b::header);
        var noRedirect = java.net.http.HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(java.net.http.HttpClient.Redirect.NEVER)
                .build();
        var resp = noRedirect.send(b.build(), HttpResponse.BodyHandlers.ofString());
        return new RedirectResult(resp.statusCode(),
                resp.headers().firstValue("Location").orElse(""), resp.body());
    }

    public HttpResponse<String> get(String url, Map<String, String> headers)
            throws IOException, InterruptedException {
        return send(base(url, headers).GET().build());
    }

    public HttpResponse<String> postForm(String url, Map<String, String> form, Map<String, String> headers)
            throws IOException, InterruptedException {
        HttpRequest.BodyPublisher body = HttpRequest.BodyPublishers.ofString(
                encodeForm(form), StandardCharsets.UTF_8);
        return send(base(url, headers)
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(body).build());
    }

    public String getText(String url, Map<String, String> headers)
            throws IOException, InterruptedException {
        return get(url, headers).body();
    }

    /** GET 并解析 JSON（HTTP 错误/非 JSON 抛 IOException）。 */
    public JsonNode getJson(String url, Map<String, String> headers)
            throws IOException, InterruptedException {
        return parseJson(getText(url, headers));
    }

    public JsonNode postFormJson(String url, Map<String, String> form, Map<String, String> headers)
            throws IOException, InterruptedException {
        return parseJson(postForm(url, form, headers).body());
    }

    /** POST JSON 请求体（引擎层的创作者中心/票务/购物接口用）。 */
    public HttpResponse<String> postJson(String url, JsonNode body, Map<String, String> headers)
            throws IOException, InterruptedException {
        HttpRequest.BodyPublisher publisher = HttpRequest.BodyPublishers.ofString(
                JSON.writeValueAsString(body), StandardCharsets.UTF_8);
        return send(base(url, headers)
                .header("Content-Type", "application/json")
                .POST(publisher).build());
    }

    public static JsonNode parseJson(String body) throws IOException {
        try {
            return JSON.readTree(body);
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new IOException("响应不是合法 JSON：" + e.getOriginalMessage(), e);
        }
    }

    /** form 编码（application/x-www-form-urlencoded）。 */
    public static String encodeForm(Map<String, String> form) {
        StringJoiner sj = new StringJoiner("&");
        if (form != null) {
            form.forEach((k, v) -> sj.add(urlEncode(k) + "=" + urlEncode(v == null ? "" : v)));
        }
        return sj.toString();
    }

    public static String urlEncode(String s) {
        return URLEncoder.encode(s == null ? "" : s, StandardCharsets.UTF_8);
    }
}
