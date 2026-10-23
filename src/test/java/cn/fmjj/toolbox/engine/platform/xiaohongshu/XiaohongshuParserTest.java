package cn.fmjj.toolbox.engine.platform.xiaohongshu;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 小红书探索页解析测试（离线 fixture，结构取自真实页面）。
 */
class XiaohongshuParserTest {

    private static final String HTML = """
            <html><head><title>小红书</title></head><body>
            <script>window.__INITIAL_STATE__={"user":{"loggedIn":true,"userInfo":{"nickname":"小红薯6777CCC4"}},
            "feed":{"feeds":[
              {"id":"6ad9ec56000000002403c56c","xsecToken":"AB1SVKUvMOmX3PcinETM",
               "noteCard":{"type":"video","displayTitle":"以她的力,50岁还想要自然","user":{"nickname":"Snowytrend2425"},
                 "cover":{"urlDefault":"https://sns-img.xhscdn.com/cover1.jpg","url":""},
                 "video":{"capa":{"duration":12}}}},
              {"id":"6ad9ec56000000002403c56d","xsecToken":"AB2xxx",
               "noteCard":{"type":"normal","displayTitle":"图文笔记","user":{"nickname":"作者B"},
                 "cover":{"urlDefault":"","url":"https://sns-img.xhscdn.com/cover2.jpg"},
                 "video":undefined}}
            ]}};</script></body></html>
            """;

    @Test
    @DisplayName("parseExplore：笔记列表 + 登录态（含 undefined 容错）")
    void parseExplore() throws Exception {
        var r = XiaohongshuParser.parseExplore(HTML);
        assertEquals(2, r.notes().size());
        assertTrue(r.login().loggedIn());
        assertEquals("小红薯6777CCC4", r.login().nickname());

        var n0 = r.notes().get(0);
        assertEquals("6ad9ec56000000002403c56c", n0.id());
        assertEquals("以她的力,50岁还想要自然", n0.title());
        assertEquals("Snowytrend2425", n0.author());
        assertEquals("video", n0.type());
        assertEquals(12, n0.durationSeconds());
        assertEquals("00:12", n0.durationText());
        assertEquals("https://sns-img.xhscdn.com/cover1.jpg", n0.cover());
        assertTrue(n0.webUrl().contains("xsec_token=AB1SVKUvMOmX3PcinETM"));

        // cover.urlDefault 为空时用 url 兜底
        assertEquals("https://sns-img.xhscdn.com/cover2.jpg", r.notes().get(1).cover());
    }

    @Test
    @DisplayName("extractState：undefined → null 可被 Jackson 解析")
    void undefinedTolerance() throws Exception {
        String html = "<script>window.__INITIAL_STATE__={\"a\":undefined,\"b\":1};</script>";
        var node = XiaohongshuParser.extractState(html);
        assertTrue(node.path("a").isNull());
        assertEquals(1, node.path("b").asInt());
    }

    @Test
    @DisplayName("extractState：安全限制页给出明确提示")
    void securityPage() {
        String html = "<html><head><title>安全限制</title></head><body>请完成验证</body></html>";
        var e = assertThrows(IllegalStateException.class, () -> XiaohongshuParser.extractState(html));
        assertTrue(e.getMessage().contains("安全校验"), e.getMessage());
    }

    @Test
    @DisplayName("balancedJson：字符串里的 } 不提前结束")
    void balanced() {
        String text = "x={\"a\":\"}}\"};";
        assertEquals("{\"a\":\"}}\"}", XiaohongshuParser.balancedJson(text, 2));
    }

    @Test
    @DisplayName("extractNoteId / extractXsecToken：三种链接形态")
    void extractIds() {
        String url = "https://www.xiaohongshu.com/explore/6ad9ec56000000002403c56c?xsec_token=AB1SVKUvMOmX3PcinETM&xsec_source=pc_feed";
        assertEquals("6ad9ec56000000002403c56c", XiaohongshuParser.extractNoteId(url));
        assertEquals("AB1SVKUvMOmX3PcinETM", XiaohongshuParser.extractXsecToken(url));
        assertEquals("6ad9ec56000000002403c56c",
                XiaohongshuParser.extractNoteId("https://www.xiaohongshu.com/discovery/item/6ad9ec56000000002403c56c"));
        assertEquals("", XiaohongshuParser.extractNoteId("随便一段文字"));
        assertEquals("", XiaohongshuParser.extractXsecToken("没有 token"));
    }

    @Test
    @DisplayName("空 feeds：不抛异常")
    void emptyFeed() throws Exception {
        var r = XiaohongshuParser.parseExplore("<script>window.__INITIAL_STATE__={\"feed\":{}};</script>");
        assertTrue(r.notes().isEmpty());
        assertFalse(r.login().loggedIn());
    }
}
