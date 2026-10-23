package cn.fmjj.toolbox.engine.platform.wechat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 公众号解析/导出测试（离线 fixture，结构取自真实文章页）。 */
class WechatArticleParserTest {

    private static final String HTML = """
            <html><head>
            <meta property="og:title" content="OG 标题">
            <meta name="author" content="作者甲">
            <script>var ct = "1730000000";</script>
            </head><body>
            <h1 id="activity-name">  测试文章标题  </h1>
            <a id="js_name">封茗囧菌</a>
            <em id="publish_time">2026-10-23 10:00</em>
            <div id="js_content">
              <p>第一段<strong>加粗</strong>文字</p>
              <p><img data-src="https://mmbiz.qpic.cn/a.jpg?wx_fmt=jpeg" src="data:image/gif;base64,R0lGOD"></p>
              <p>第二段<br>换行后</p>
              <img data-src="//mmbiz.qpic.cn/b.png">
            </div>
            </body></html>
            """;

    @Test
    @DisplayName("extractUrls：从混合文本提取 mp.weixin 链接并去重")
    void extractUrls() {
        String text = """
                看这篇 https://mp.weixin.qq.com/s/abc123 很好
                还有 https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1&sn=x
                重复 https://mp.weixin.qq.com/s/abc123
                其他 https://www.bilibili.com/video/BV1xx
                """;
        List<String> urls = WechatArticleParser.extractUrls(text);
        assertEquals(2, urls.size());
        assertEquals("https://mp.weixin.qq.com/s/abc123", urls.get(0));
        assertTrue(urls.get(1).contains("__biz="));
        assertTrue(WechatArticleParser.extractUrls("没有链接").isEmpty());
        assertTrue(WechatArticleParser.extractUrls(null).isEmpty());
    }

    @Test
    @DisplayName("parse：标题/公众号/作者/时间/正文/图片（data-src 优先）")
    void parse() {
        var art = WechatArticleParser.parse(HTML, "https://mp.weixin.qq.com/s/abc123");
        assertEquals("测试文章标题", art.title());
        assertEquals("封茗囧菌", art.account());
        assertEquals("作者甲", art.author());
        assertEquals("2026-10-23 10:00", art.publishTime());
        assertEquals(2, art.images().size());
        assertEquals("https://mmbiz.qpic.cn/a.jpg?wx_fmt=jpeg", art.images().get(0));
        assertEquals("https://mmbiz.qpic.cn/b.png", art.images().get(1), "// 开头应补 https");
        assertTrue(art.contentHtml().contains("第一段"));
        // data-src 已补到 src
        assertTrue(art.contentHtml().contains("src=\"https://mmbiz.qpic.cn/a.jpg"));
    }

    @Test
    @DisplayName("parse：var ct 兜底时间")
    void publishTimeFallback() {
        String html = "<html><body><h1 id=\"activity-name\">T</h1>"
                + "<script>var ct = \"1730000000\";</script>"
                + "<div id=\"js_content\">x</div></body></html>";
        var art = WechatArticleParser.parse(html, "u");
        assertTrue(art.publishTime().startsWith("2024-10-"), art.publishTime());
    }

    @Test
    @DisplayName("parse：错误页 / 风控 / 无正文都给出可读消息")
    void errors() {
        String err = "<html><body><div class=\"weui-msg__title\">该内容已被发布者删除</div></body></html>";
        var e1 = assertThrows(IllegalStateException.class, () -> WechatArticleParser.parse(err, "u"));
        assertTrue(e1.getMessage().contains("删除"), e1.getMessage());

        String risk = "<html><body>操作频繁，请稍后再试</body></html>";
        var e2 = assertThrows(IllegalStateException.class, () -> WechatArticleParser.parse(risk, "u"));
        assertTrue(e2.getMessage().contains("风控"), e2.getMessage());

        String noContent = "<html><body><h1 id=\"activity-name\">T</h1></body></html>";
        var e3 = assertThrows(IllegalStateException.class, () -> WechatArticleParser.parse(noContent, "u"));
        assertTrue(e3.getMessage().contains("js_content"), e3.getMessage());
    }

    @Test
    @DisplayName("stem：公众号 - 标题，去非法字符")
    void stem() {
        var art = new WechatArticleParser.Article("u", "标题/带:非法*字符", "公众号", "", "", "", List.of());
        assertEquals("公众号 - 标题_带_非法_字符", art.stem());
        var empty = new WechatArticleParser.Article("u", "", "", "", "", "", List.of());
        assertEquals("未命名文章", empty.stem());
    }

    @Test
    @DisplayName("htmlToMarkdown：标题/段落/图片/加粗")
    void markdown() {
        String html = "<h2>小标题</h2><p>正文<strong>粗</strong></p><p><img src=\"a.jpg\"></p>";
        String md = WechatExporter.htmlToMarkdown(html, Map.of());
        assertTrue(md.contains("## 小标题"), md);
        assertTrue(md.contains("**粗**"), md);
        assertTrue(md.contains("![](a.jpg)"), md);
    }

    @Test
    @DisplayName("导出：HTML 与 Markdown 落盘，图片路径被替换")
    void export(@TempDir Path tmp) throws Exception {
        var art = WechatArticleParser.parse(HTML, "https://mp.weixin.qq.com/s/abc123");
        Map<String, String> local = Map.of("https://mmbiz.qpic.cn/a.jpg?wx_fmt=jpeg", "img/1.jpg");

        Path html = WechatExporter.exportHtml(art, tmp, local);
        assertTrue(Files.isRegularFile(html));
        String content = Files.readString(html);
        assertTrue(content.contains("img/1.jpg"), "图片应替换为本地路径");
        assertTrue(content.contains("测试文章标题"));

        Path md = WechatExporter.exportMarkdown(art, tmp, local);
        assertTrue(Files.isRegularFile(md));
        assertTrue(Files.readString(md).contains("# 测试文章标题"));
    }
}
