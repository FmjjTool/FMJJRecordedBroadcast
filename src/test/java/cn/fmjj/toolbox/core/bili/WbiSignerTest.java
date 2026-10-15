package cn.fmjj.toolbox.core.bili;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * WBI 签名跨语言一致性测试。
 *
 * 期望值全部由 Python 原版 {@code src/bili/wbi.py}（ts 固定为 1700000000）生成：
 * <pre>
 *   mixin:   761db123af3ea09b4b42d37a704a41b8
 *   query:   dm_img_list=%5B%5D&foo=bar%20x&mid=123456&w_webid=abc123_webid&wts=1700000000&w_rid=a972e4f0b7b8085543a76f361968af53
 *   app_sign: 151ac8860784d468d87d868638fc529b
 * </pre>
 */
class WbiSignerTest {

    private static final String IMG = "7cd084941338484aae1ad9425b74077e";
    private static final String SUB = "3d9bd81a8b7b3976a2f6cbf5d1a7c6ab";
    private static final WbiSigner.WbiKeys KEYS = new WbiSigner.WbiKeys(IMG, SUB, "abc123_webid");

    @Test
    @DisplayName("mixinKey 与 Python 一致")
    void mixinKeyMatchesPython() {
        assertEquals("761db123af3ea09b4b42d37a704a41b8", WbiSigner.mixinKey(IMG, SUB));
    }

    @Test
    @DisplayName("WBI 查询串与 Python 一致（含 !'()* 剔除与空值编码）")
    void buildQueryMatchesPython() {
        Map<String, String> params = Map.of(
                "mid", "123456",
                "foo", "b!a'r( )*x",
                "dm_img_list", "[]");
        String query = WbiSigner.buildQuery(params, KEYS, 1700000000L);
        assertEquals("dm_img_list=%5B%5D&foo=bar%20x&mid=123456&w_webid=abc123_webid"
                        + "&wts=1700000000&w_rid=a972e4f0b7b8085543a76f361968af53",
                query);
    }

    @Test
    @DisplayName("signApp 与 Python 一致")
    void signAppMatchesPython() {
        Map<String, String> out = WbiSigner.signApp(Map.of(
                "access_key", "AK_TEST",
                "appkey_extra", "z1"));
        assertEquals("151ac8860784d468d87d868638fc529b", out.get("sign"));
        assertEquals("1700000000", out.get("ts"));
        assertEquals("8160400", out.get("build"));
    }

    @Test
    @DisplayName("percentEncode 与 Python urllib.parse.quote(safe='') 一致")
    void percentEncodeMatchesPythonQuote() {
        // Python: quote("a b!c'd(e)f*g~", safe='') == 'a%20b%21c%27d%28e%29f%2Ag~'（~ 不编码，十六进制大写）
        assertEquals("a%20b%21c%27d%28e%29f%2Ag~", WbiSigner.percentEncode("a b!c'd(e)f*g~"));
        assertEquals("%E4%B8%AD%E6%96%87", WbiSigner.percentEncode("中文"));
        assertEquals("a-b.c_d~e", WbiSigner.percentEncode("a-b.c_d~e"));
        assertEquals("~", WbiSigner.percentEncode("~"));
    }

    @Test
    @DisplayName("signUrl 在带 ? 的 URL 上用 & 拼接")
    void signUrlSeparator() {
        String url = WbiSigner.signUrl("https://api.bilibili.com/x/web-interface/nav?foo=1",
                Map.of(), KEYS);
        assertEquals(true, url.startsWith("https://api.bilibili.com/x/web-interface/nav?foo=1&"));
        String url2 = WbiSigner.signUrl("https://api.bilibili.com/x/web-interface/nav",
                Map.of(), KEYS);
        assertEquals(true, url2.startsWith("https://api.bilibili.com/x/web-interface/nav?"));
    }
}
