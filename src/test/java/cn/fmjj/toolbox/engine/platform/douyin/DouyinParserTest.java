package cn.fmjj.toolbox.engine.platform.douyin;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 抖音 SSR 解析测试（离线 fixture，不触网）。
 * fixture 结构取自 iesdouyin 分享页 `_ROUTER_DATA` 的真实形态。
 */
class DouyinParserTest {

    private static final String HTML = """
            <!DOCTYPE html><html><head><script>window._ROUTER_DATA = {"loaderData":{
              "video_layout":null,
              "video_(id)/page":{"ua":"Mozilla","renderInSSR":1,"lastPath":"7412345678901234567",
                "videoInfoRes":{"item_list":[{
                  "aweme_id":"7412345678901234567",
                  "desc":"测试视频标题\\n第二行描述",
                  "create_time":1730000000,
                  "author":{"nickname":"封茗囧菌","sec_uid":"MS4wLjABAAAAxyz"},
                  "video":{"duration":153000,
                    "cover":{"url_list":["https://p3.douyinpic.com/cover.jpg"]},
                    "play_addr":{"url_list":["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0300fg10000abc"]}},
                  "statistics":{"digg_count":12345,"comment_count":678,"share_count":90}
                }]}}
            }};</script></head><body></body></html>
            """;

    private static final String IMAGE_HTML = """
            <script>window._ROUTER_DATA = {"loaderData":{"video_(id)/page":{"videoInfoRes":{"item_list":[{
              "aweme_id":"7411111111111111111",
              "desc":"图集测试",
              "author":{"nickname":"作者A"},
              "video":{"duration":0,"cover":{"url_list":["https://p3.douyinpic.com/c.jpg"]},"play_addr":{"url_list":[]}},
              "images":[
                {"url_list":["https://p3.douyinpic.com/1.jpg"]},
                {"url_list":["https://p3.douyinpic.com/2.jpg"]}
              ],
              "statistics":{"digg_count":1,"comment_count":2,"share_count":3}
            }]}}}};</script>
            """;

    @Test
    @DisplayName("extractRouterData：平衡括号提取（含转义引号与嵌套）")
    void extractRouterData() {
        String json = DouyinParser.extractRouterData(HTML);
        assertTrue(json != null && json.startsWith("{") && json.endsWith("}"));
        assertTrue(json.contains("\"aweme_id\":\"7412345678901234567\""));
        assertNull(DouyinParser.extractRouterData("<html>no data</html>"));
        assertNull(DouyinParser.extractRouterData(null));

        // 字符串里的 } 不能提前结束扫描
        String tricky = "<script>window._ROUTER_DATA = {\"a\":\"x}y\"};</script>";
        assertEquals("{\"a\":\"x}y\"}", DouyinParser.extractRouterData(tricky));
    }

    @Test
    @DisplayName("视频：标题/作者/封面/去水印直链/时长/统计")
    void videoItem() throws Exception {
        var v = DouyinParser.fromRouterData(HTML);
        assertEquals("7412345678901234567", v.awemeId());
        assertEquals("测试视频标题", v.title());
        assertEquals("封茗囧菌", v.author());
        assertEquals("MS4wLjABAAAAxyz", v.authorId());
        assertEquals("https://p3.douyinpic.com/cover.jpg", v.cover());
        // playwm → play 去水印
        assertEquals("https://aweme.snssdk.com/aweme/v1/play/?video_id=v0300fg10000abc", v.videoUrl());
        assertEquals(153, v.durationSeconds());
        assertEquals(12345, v.diggCount());
        assertEquals(678, v.commentCount());
        assertEquals(90, v.shareCount());
        assertFalse(v.isImagePost());
        assertTrue(v.images().isEmpty());
    }

    @Test
    @DisplayName("图集：images 全量 + isImagePost")
    void imageItem() throws Exception {
        var v = DouyinParser.fromRouterData(IMAGE_HTML);
        assertEquals("7411111111111111111", v.awemeId());
        assertEquals(2, v.images().size());
        assertEquals("https://p3.douyinpic.com/1.jpg", v.images().get(0));
        assertTrue(v.isImagePost());
        assertEquals("图集（2 张）", DouyinParser.summary(v).get("类型"));
    }

    @Test
    @DisplayName("空 videoInfoRes：明确报错（含 filter_list 提示）")
    void emptyItemList() {
        String html = "<script>window._ROUTER_DATA = {\"loaderData\":{\"video_(id)/page\":"
                + "{\"videoInfoRes\":{\"item_list\":[],\"filter_list\":[{\"detail_msg\":\"作品已删除\"}]}}}};</script>";
        var e = assertThrows(IllegalStateException.class, () -> DouyinParser.fromRouterData(html));
        assertTrue(e.getMessage().contains("作品已删除"), e.getMessage());
    }

    @Test
    @DisplayName("空壳页面：给出「需要浏览器上下文」的明确诊断")
    void noRouterData() {
        String shell = "<html><body>" + "x".repeat(2000) + "</body></html>";
        var e = assertThrows(IllegalStateException.class, () -> DouyinParser.fromRouterData(shell));
        assertTrue(e.getMessage().contains("浏览器"), e.getMessage());

        var e2 = assertThrows(IllegalStateException.class,
                () -> DouyinParser.fromRouterData("<html></html>"));
        assertTrue(e2.getMessage().contains("链接无效"), e2.getMessage());
    }

    @Test
    @DisplayName("fromDom（浏览器渲染后 DOM）：标题/作者/作者ID/直链/封面")
    void fromDom() throws Exception {
        String html;
        try (var in = getClass().getResourceAsStream("/douyin/dom_sample.html")) {
            html = new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        }
        var v = DouyinParser.fromDom(html);
        assertEquals("7415554945589513513", v.awemeId());
        assertEquals("#封茗囧菌 #现场 祝大家中秋快乐！#桃花庵", v.title());
        assertEquals("封茗囧菌", v.author());
        assertEquals("MS4wLjABAAAA0Wk4gxp3AYFnqoqo-IBF6lbdLnrxgjy__DdhPBNBkws", v.authorId());
        // &amp; 需还原
        assertTrue(v.videoUrl().contains("&br=1118"), v.videoUrl());
        assertTrue(v.videoUrl().startsWith("https://v95-aw-web.douyinvod.com/"));
        assertTrue(v.cover().startsWith("https://p3-sign.douyinpic.com/"));
    }

    @Test
    @DisplayName("fromDom：无数据页面报错")
    void fromDomEmpty() {
        var e = assertThrows(IllegalStateException.class,
                () -> DouyinParser.fromDom("<html><body>空</body></html>"));
        assertTrue(e.getMessage().contains("没有作品数据"), e.getMessage());
    }

    @Test
    @DisplayName("firstTextNode：跳过空标签取第一个非空文本")
    void firstTextNode() {
        assertEquals("昵称", DouyinParser.firstTextNode("<div><span></span><b>昵称</b></div>"));
        assertEquals("", DouyinParser.firstTextNode("<div><span></span></div>"));
        assertEquals("A&B", DouyinParser.firstTextNode("<i>A&amp;B</i>"));
    }

    @Test
    @DisplayName("标题为空时用 aweme_id 兜底")
    void titleFallback() throws Exception {
        String html = "<script>window._ROUTER_DATA = {\"loaderData\":{\"video_(id)/page\":"
                + "{\"videoInfoRes\":{\"item_list\":[{\"aweme_id\":\"123\",\"desc\":\"\"}]}}}};</script>";
        assertEquals("抖音视频_123", DouyinParser.fromRouterData(html).title());
    }
}
