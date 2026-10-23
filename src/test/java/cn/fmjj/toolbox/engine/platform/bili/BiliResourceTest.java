package cn.fmjj.toolbox.engine.platform.bili;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** B站资源解析测试（离线 fixture）。 */
class BiliResourceTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    @DisplayName("card：昵称/签名/粉丝/等级/头像/背景/头像框")
    void card() throws Exception {
        String json = """
                {"card":{"mid":"12345","name":"封茗囧菌","sign":"签名文案","fans":100000,
                  "attention":12,"likes":500000,"face":"https://i0.hdslb.com/face.jpg",
                  "pendant":{"image":"https://i0.hdslb.com/pendant.png"},
                  "level_info":{"current_level":6}},
                 "space":{"l_img":"https://i0.hdslb.com/top.jpg"}}
                """;
        var c = BiliResource.fromCard(JSON.readTree(json));
        assertEquals("12345", c.mid());
        assertEquals("封茗囧菌", c.name());
        assertEquals("签名文案", c.sign());
        assertEquals(100000, c.fans());
        assertEquals("10.0万", c.fansText());
        assertEquals(6, c.level());
        assertEquals("https://i0.hdslb.com/top.jpg", c.topPhoto());

        var files = BiliResource.spaceFiles(c);
        assertEquals(3, files.size());
        assertEquals("头像", files.get(0).getKey());
    }

    @Test
    @DisplayName("parseSearch：视频 + 用户混排，去 em 标签、补 https")
    void parseSearch() throws Exception {
        String json = """
                {"result":[
                  {"result_type":"video","data":[
                    {"bvid":"BV1xx411c7mD","title":"<em class=\\"keyword\\">封茗囧菌</em> 翻唱",
                     "author":"UP主A","pic":"//i0.hdslb.com/pic.jpg","description":"简介"}
                  ]},
                  {"result_type":"bili_user","data":[
                    {"mid":12345,"title":"<em>封茗囧菌</em>","upic":"//i0.hdslb.com/face.jpg","usign":"签名"}
                  ]},
                  {"result_type":"media_bangumi","data":[{"title":"忽略"}]}
                ]}
                """;
        var items = BiliResource.parseSearch(JSON.readTree(json));
        assertEquals(2, items.size(), "只保留 video / bili_user");

        var v = items.get(0);
        assertEquals("视频", v.type());
        assertEquals("封茗囧菌 翻唱", v.title());
        assertEquals("UP主A", v.author());
        assertEquals("https://www.bilibili.com/video/BV1xx411c7mD", v.url());
        assertEquals("https://i0.hdslb.com/pic.jpg", v.cover());

        var u = items.get(1);
        assertEquals("用户", u.type());
        assertEquals("UID 12345", u.author());
        assertEquals("https://space.bilibili.com/12345", u.url());
    }

    @Test
    @DisplayName("basename：从 WBI img_url 取文件名去扩展名")
    void basename() {
        assertEquals("7cd084941338484aae1ad9425b84077c",
                BiliResource.basename("https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png"));
        assertEquals("abc", BiliResource.basename("https://x/y/abc.png?x=1"));
        assertEquals("abc", BiliResource.basename("abc"));
    }

    @Test
    @DisplayName("normUrl / stripHtml")
    void helpers() {
        assertEquals("https://i0.hdslb.com/a.jpg", BiliResource.normUrl("//i0.hdslb.com/a.jpg"));
        assertEquals("", BiliResource.normUrl(""));
        assertEquals("封茗囧菌 翻唱", BiliResource.stripHtml("<em>封茗囧菌</em> 翻唱"));
        assertEquals("A&B", BiliResource.stripHtml("A&amp;B"));
    }

    @Test
    @DisplayName("空数据：不抛异常，返回空列表")
    void empty() throws Exception {
        assertTrue(BiliResource.parseSearch(JSON.readTree("{}")).isEmpty());
        assertTrue(BiliResource.parseSearch(JSON.readTree("{\"result\":[]}")).isEmpty());
        assertFalse(BiliResource.fromCard(JSON.readTree("{}")).name().length() > 0);
    }
}
