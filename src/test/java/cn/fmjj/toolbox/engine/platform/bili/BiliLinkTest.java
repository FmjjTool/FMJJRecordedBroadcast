package cn.fmjj.toolbox.engine.platform.bili;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 链接解析测试。期望值由 Python 原版 bili.links.parse_link 生成：
 * video/BV1xx411c7mD · av12345 · space→('user',…) · live→('liveroom',…) · dynamic · None
 */
class BiliLinkTest {

    private static void assertLink(BiliLink.Type type, String id, String input) {
        BiliLink.LinkInfo info = BiliLink.parseText(input);
        assertEquals(type, info.type(), "type of: " + input);
        assertEquals(id, info.id(), "id of: " + input);
    }

    @Test
    @DisplayName("parseText 与 Python parse_link 一致（10 组向量）")
    void parseText() {
        assertLink(BiliLink.Type.VIDEO, "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD");
        assertLink(BiliLink.Type.VIDEO, "av12345", "https://www.bilibili.com/video/av12345?p=2");
        assertLink(BiliLink.Type.VIDEO, "BV1xx411c7mD", "https://bilibili.com/video/BV1xx411c7mD/?spm=1");
        assertLink(BiliLink.Type.VIDEO, "BV1ab411c7mD", "【录播】 https://www.bilibili.com/video/BV1ab411c7mD");
        assertLink(BiliLink.Type.USER, "123456", "https://space.bilibili.com/123456/video");
        assertLink(BiliLink.Type.LIVERoom, "7890", "https://live.bilibili.com/7890?x=1");
        assertLink(BiliLink.Type.DYNAMIC, "987654321", "https://t.bilibili.com/987654321");
        assertLink(BiliLink.Type.DYNAMIC, "55555", "https://www.bilibili.com/opus/55555");

        assertNull(BiliLink.parseText("随便一段文字没有链接"));
        assertNull(BiliLink.parseText(""));
        assertNull(BiliLink.parseText(null));
    }

    @Test
    @DisplayName("短链识别")
    void shortLink() {
        assertTrue(BiliLink.isShortLink("https://b23.tv/abcDEF"));
        assertTrue(BiliLink.isShortLink("https://bili2233.cn/xyz"));
        assertTrue(!BiliLink.isShortLink("https://www.bilibili.com/video/BV1xx411c7mD"));
    }

    @Test
    @DisplayName("分享文本前缀剥离后仍可识别")
    void shareText() {
        assertLink(BiliLink.Type.VIDEO, "BV1ab411c7mD",
                "【某标题 第2集】 https://www.bilibili.com/video/BV1ab411c7mD");
    }
}
