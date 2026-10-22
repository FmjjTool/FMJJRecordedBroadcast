package cn.fmjj.toolbox.engine.platform.kuaishou;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 快手解析测试（离线 fixture）。fixture 按 Apollo 缓存真实形态构造：
 * `defaultClient` 下挂 `Photo:{id}` 对象。
 */
class KuaishouParserTest {

    private static final String HTML = """
            <html><head><title>快手</title></head><body>
            <script>window.__APOLLO_STATE__={"defaultClient":{"ROOT_QUERY":{"kconf({\\"key\\":\\"x\\"})":{"type":"json"}},
            "Photo:3xabc123def":{"photoId":"3xabc123def","caption":"测试标题\\n第二行","duration":15300,
              "userName":"快手作者","coverUrl":"https://p1.a.yximgs.com/cover.jpg",
              "manifest":{"adaptationSet":[
                {"representation":[{"url":"https://v2.kwaicdn.com/h265/video.mp4"},{"url":"https://v2.kwaicdn.com/avc/video.mp4"}]}
              ]}}}};</script></body></html>
            """;

    @Test
    @DisplayName("extractApolloState：平衡括号提取")
    void extractApollo() {
        String json = KuaishouParser.extractApolloState(HTML);
        assertNotNull(json);
        assertTrue(json.startsWith("{") && json.endsWith("}"));
        assertNull(KuaishouParser.extractApolloState("<html></html>"));
        assertNull(KuaishouParser.extractApolloState(null));
    }

    @Test
    @DisplayName("fromHtml：标题/作者/封面/直链（优先非 H265）/时长")
    void fromHtml() throws Exception {
        var v = KuaishouParser.fromHtml(HTML);
        assertEquals("3xabc123def", v.photoId());
        assertEquals("测试标题", v.title());
        assertEquals("快手作者", v.author());
        assertEquals("https://p1.a.yximgs.com/cover.jpg", v.cover());
        // h265 排在前面，但要优先 avc
        assertEquals("https://v2.kwaicdn.com/avc/video.mp4", v.videoUrl());
        assertEquals(15, v.durationSeconds());
    }

    @Test
    @DisplayName("findPhoto：无作品对象时报错")
    void noPhoto() {
        String html = "<script>window.__APOLLO_STATE__={\"defaultClient\":{\"ROOT_QUERY\":{}}};</script>";
        var e = assertThrows(IllegalStateException.class, () -> KuaishouParser.fromHtml(html));
        assertTrue(e.getMessage().contains("未找到作品数据"), e.getMessage());
    }

    @Test
    @DisplayName("fromPhoto：photoUrl 兜底 / 缺字段")
    void fallback() {
        var p = new com.fasterxml.jackson.databind.ObjectMapper();
        var node = p.createObjectNode()
                .put("photoId", "abc")
                .put("caption", "")
                .put("photoUrl", "https://x/video.mp4");
        var v = KuaishouParser.fromPhoto(node);
        assertEquals("快手视频_abc", v.title());
        assertEquals("https://x/video.mp4", v.videoUrl());
        assertEquals("", v.author());
    }

    @Test
    @DisplayName("extractId：short-video / f 短链 / 纯 ID / 非法")
    void extractId() {
        assertEquals("3xabc123def", KuaishouParser.extractId("https://www.kuaishou.com/short-video/3xabc123def?x=1"));
        assertEquals("X-abc_123", KuaishouParser.extractId("https://v.kuaishou.com/f/X-abc_123"));
        assertEquals("3xabc123def", KuaishouParser.extractId("3xabc123def"));
        assertEquals("", KuaishouParser.extractId("随便一段文字"));
        assertEquals("", KuaishouParser.extractId(""));
    }
}
