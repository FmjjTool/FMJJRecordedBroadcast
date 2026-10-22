package cn.fmjj.toolbox.core.bili;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * BiliAuth 纯逻辑测试。网络交互（nav/spi/ticket/qr）不做单测，靠冒烟与手动验证；
 * 这里锁定可从 Python 原版取到向量的部分。
 */
class BiliAuthTest {

    @Test
    @DisplayName("GenWebTicket hexsign 与 Python hmac 一致")
    void hexsignMatchesPython() {
        // Python: hmac.new(b"XgwSnGZ1p", b"ts1700000000", sha256).hexdigest()
        assertEquals("bb79f0d980ffbb51597aa1a3e8b55603025cc1322ac766f4c1a98852e6182514",
                BiliAuth.hmacSha256Hex("XgwSnGZ1p", "ts1700000000"));
        assertEquals("988b4c8d4995d0747bbc4fc15e4c17b576cb5660e3e4e3cf9a6335da086a2c0c",
                BiliAuth.hmacSha256Hex("XgwSnGZ1p", "ts1792045783"));
    }

    @Test
    @DisplayName("stripKey：wbi_img URL → key")
    void stripKey() {
        assertEquals("7cd084941338484aae1ad9425b74077e",
                BiliAuth.stripKey("https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b74077e.png"));
        assertEquals("abc", BiliAuth.stripKey("x/abc.png"));
        assertEquals("", BiliAuth.stripKey(""));
    }

    @Test
    @DisplayName("extractWWebid：__RENDER_DATA__ URL 编码 JSON → access_id")
    void extractWWebid() {
        // 由 Python urllib.parse.quote(json.dumps({...}), safe='') 生成
        String html = "<html><script id=\"__RENDER_DATA__\" type=\"application/json\">"
                + "%7B%22access_id%22%3A%20%22w_webid_abc123%22%2C%20%22other%22%3A%201%7D"
                + "</script></html>";
        assertEquals("w_webid_abc123", BiliAuth.extractWWebid(html));
        assertEquals("", BiliAuth.extractWWebid("<html>无渲染数据</html>"));
        assertEquals("", BiliAuth.extractWWebid(""));
        // access_id 缺失时返回空串而不抛
        assertEquals("", BiliAuth.extractWWebid(
                "<script id=\"__RENDER_DATA__\" type=\"application/json\">%7B%22x%22%3A1%7D</script>"));
    }

    @Test
    @DisplayName("formatCookieHeader：过滤空值、分号拼接（Python 语义）")
    void formatCookieHeader() {
        Map<String, String> cookies = new LinkedHashMap<>();
        cookies.put("SESSDATA", "abc");
        cookies.put("bili_jct", "def");
        cookies.put("empty", "");
        cookies.put("null", null);
        assertEquals("SESSDATA=abc; bili_jct=def", BiliAuth.formatCookieHeader(cookies));
        assertEquals("", BiliAuth.formatCookieHeader(Map.of()));
    }

    @Test
    @DisplayName("wbiKeys/buvid 缓存结构：实例化即可用（不发请求）")
    void instantiation() {
        BiliAuth auth = new BiliAuth();
        assertEquals("", BiliAuth.extractWWebid("x"));
        assertTrue(auth != null);
    }
}
