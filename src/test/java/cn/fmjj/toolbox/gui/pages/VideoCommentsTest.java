package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.platform.bili.BiliComments;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 评论区解析与折行纯逻辑测试。 */
class VideoCommentsTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    @DisplayName("fromReply：昵称/内容/点赞/时间/图片（// 补 https）")
    void fromReply() throws Exception {
        String json = """
                {"rpid":123,"like":45,"ctime":1730000000,
                 "member":{"uname":"用户A","avatar":"//i0.hdslb.com/face.jpg"},
                 "content":{"message":"这是一条评论","pictures":[
                    {"img_src":"//i0.hdslb.com/a.jpg"},{"img_src":"https://i0.hdslb.com/b.png"}]}}
                """;
        var c = BiliComments.fromReply(JSON.readTree(json));
        assertEquals("用户A", c.user());
        assertEquals("这是一条评论", c.content());
        assertEquals(45, c.likes());
        assertEquals(2, c.images().size());
        assertEquals("https://i0.hdslb.com/a.jpg", c.images().get(0));
        assertEquals("https://i0.hdslb.com/b.png", c.images().get(1));
        assertTrue(c.avatar().startsWith("https://"));
        assertTrue(c.timeText().matches("\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}"));
    }

    @Test
    @DisplayName("fromReply：无图/缺字段不崩")
    void fromReplyEmpty() throws Exception {
        var c = BiliComments.fromReply(JSON.readTree("{\"member\":{},\"content\":{}}"));
        assertEquals("", c.user());
        assertEquals("", c.content());
        assertEquals(0, c.likes());
        assertTrue(c.images().isEmpty());
    }

    @Test
    @DisplayName("wrap：中文按 1 字宽、英文按 0.55 估算折行")
    void wrap() {
        List<String> cn = VideoCommentsPage.wrap("一二三四五六", 3);
        assertEquals(2, cn.size());
        assertEquals("一二三", cn.get(0));

        List<String> en = VideoCommentsPage.wrap("abcdefghij", 5);
        assertTrue(en.size() >= 2, "英文应折成多行");
        assertTrue(en.get(0).length() >= 8, "英文一行能放更多字符：" + en.get(0));

        assertEquals(1, VideoCommentsPage.wrap("", 10).size());
        assertEquals(1, VideoCommentsPage.wrap(null, 10).size());
    }
}
