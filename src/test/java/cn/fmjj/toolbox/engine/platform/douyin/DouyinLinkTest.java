package cn.fmjj.toolbox.engine.platform.douyin;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 抖音链接识别测试（正则与 Python douyin.py 一致）。 */
class DouyinLinkTest {

    @Test
    @DisplayName("作品 / 图文 / 弹窗 / 主页")
    void parse() {
        var v = DouyinLink.parseText("https://www.douyin.com/video/7412345678901234567");
        assertEquals(DouyinLink.Type.VIDEO, v.type());
        assertEquals("7412345678901234567", v.id());

        var n = DouyinLink.parseText("https://www.douyin.com/note/7412345678901234567");
        assertEquals(DouyinLink.Type.NOTE, n.type());

        var modal = DouyinLink.parseText("https://www.douyin.com/search/%E5%BD%95%E6%92%AD?modal_id=7412345678901234567");
        assertEquals(DouyinLink.Type.VIDEO, modal.type());
        assertEquals("7412345678901234567", modal.id());

        var user = DouyinLink.parseText("https://www.douyin.com/user/MS4wLjABAAAAxyz");
        assertEquals(DouyinLink.Type.USER, user.type());
        assertEquals("MS4wLjABAAAAxyz", user.id());
    }

    @Test
    @DisplayName("分享短链标记 + 分享文案")
    void shortLink() {
        var s = DouyinLink.parseText("7.43 复制打开抖音，看看【封茗囧菌】的作品 https://v.douyin.com/iAbCdEf/ 好听的歌");
        assertTrue(s.shortLink());
        assertEquals(DouyinLink.Type.UNKNOWN, s.type());

        assertNull(DouyinLink.parseText("普通文字"));
        assertNull(DouyinLink.parseText(""));
    }

    @Test
    @DisplayName("normalizeUrl：搜索弹窗链接规范化为 /video/{id}")
    void normalize() {
        assertEquals("https://www.douyin.com/video/7412345678901234567",
                DouyinLink.normalizeUrl("https://www.douyin.com/search/x?modal_id=7412345678901234567"));
        assertEquals("https://www.douyin.com/video/7412345678901234567",
                DouyinLink.normalizeUrl("https://www.douyin.com/user/abc/search/kw?modal_id=7412345678901234567"));
        // 非弹窗链接原样返回
        assertEquals("https://www.douyin.com/video/123", DouyinLink.normalizeUrl("https://www.douyin.com/video/123"));
    }
}
