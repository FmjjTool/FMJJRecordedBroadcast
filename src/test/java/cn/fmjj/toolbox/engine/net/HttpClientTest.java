package cn.fmjj.toolbox.engine.net;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * HttpClient 测试：重试/异常路径用注入的假 sender 驱动（不绑端口——
 * 部分受限环境不允许监听 socket）；纯函数部分直接断言。
 */
class HttpClientTest {

    /** 假响应。 */
    private static HttpResponse<String> resp(int status, String body) {
        return new HttpResponse<>() {
            @Override public int statusCode() { return status; }
            @Override public HttpRequest request() { return null; }
            @Override public java.util.Optional<HttpResponse<String>> previousResponse() { return java.util.Optional.empty(); }
            @Override public java.net.http.HttpHeaders headers() { return java.net.http.HttpHeaders.of(Map.of(), (a, b) -> true); }
            @Override public String body() { return body; }
            @Override public java.util.Optional<javax.net.ssl.SSLSession> sslSession() { return java.util.Optional.empty(); }
            @Override public java.net.URI uri() { return java.net.URI.create("http://127.0.0.1/"); }
            @Override public java.net.http.HttpClient.Version version() { return java.net.http.HttpClient.Version.HTTP_1_1; }
        };
    }

    private static HttpRequest req() {
        return HttpRequest.newBuilder(java.net.URI.create("http://127.0.0.1/")).GET().build();
    }

    @Test
    @DisplayName("5xx 按策略重试后成功（500 → 500 → 200）")
    void retryThenSuccess() throws Exception {
        AtomicInteger hits = new AtomicInteger();
        HttpClient client = new HttpClient(3, 1);
        HttpResponse<String> r = client.executeWithRetry(req(), request -> {
            int n = hits.incrementAndGet();
            return resp(n < 3 ? 500 : 200, "{\"code\":0}");
        });
        assertEquals(200, r.statusCode());
        assertEquals(3, hits.get());
        assertEquals(0, HttpClient.parseJson(r.body()).path("code").asInt());
    }

    @Test
    @DisplayName("持续 5xx 耗尽重试后抛 IOException")
    void retryExhausted() {
        AtomicInteger hits = new AtomicInteger();
        HttpClient client = new HttpClient(1, 1);
        assertThrows(IOException.class, () -> client.executeWithRetry(req(), request -> {
            hits.incrementAndGet();
            return resp(500, "{}");
        }));
        assertEquals(2, hits.get()); // 首发 + 1 次重试
    }

    @Test
    @DisplayName("IO 异常同样重试；耗尽后抛最后一次异常")
    void ioExceptionRetried() {
        AtomicInteger hits = new AtomicInteger();
        HttpClient client = new HttpClient(2, 1);
        IOException e = assertThrows(IOException.class, () -> client.executeWithRetry(req(), request -> {
            hits.incrementAndGet();
            throw new IOException("connection refused");
        }));
        assertTrue(e.getMessage().contains("connection refused"));
        assertEquals(3, hits.get()); // 1 + 2 次重试
    }

    @Test
    @DisplayName("4xx 不重试，直接返回")
    void clientErrorNoRetry() throws Exception {
        AtomicInteger hits = new AtomicInteger();
        HttpResponse<String> r = new HttpClient(3, 1).executeWithRetry(req(), request -> {
            hits.incrementAndGet();
            return resp(404, "not found");
        });
        assertEquals(404, r.statusCode());
        assertEquals(1, hits.get());
    }

    @Test
    @DisplayName("parseJson：合法/非法 JSON")
    void parseJson() throws Exception {
        assertEquals("值", HttpClient.parseJson("{\"k\":\"值\"}").path("k").asText());
        IOException e = assertThrows(IOException.class, () -> HttpClient.parseJson("<html>登录页</html>"));
        assertTrue(e.getMessage().contains("不是合法 JSON"), e.getMessage());
    }

    @Test
    @DisplayName("encodeForm / urlEncode（application/x-www-form-urlencoded）")
    void encodeForm() {
        java.util.Map<String, String> form = new java.util.LinkedHashMap<>();
        form.put("qrcode_key", "a+b");
        form.put("中文", "值");
        assertEquals("qrcode_key=a%2Bb&%E4%B8%AD%E6%96%87=%E5%80%BC", HttpClient.encodeForm(form));

        java.util.Map<String, String> form2 = new java.util.LinkedHashMap<>();
        form2.put("a", "1");
        form2.put("b", " ");
        // Python urllib.parse.urlencode 语义：空格编码为 +（quote_plus）
        assertEquals("a=1&b=+", HttpClient.encodeForm(form2));
        assertEquals("", HttpClient.encodeForm(null));
        assertEquals("a+b", HttpClient.urlEncode("a b"));
    }
}
