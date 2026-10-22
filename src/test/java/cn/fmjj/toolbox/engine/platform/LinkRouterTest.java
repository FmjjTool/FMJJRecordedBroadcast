package cn.fmjj.toolbox.engine.platform;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 多平台链接路由测试（正则对齐各平台 Python 实现）。 */
class LinkRouterTest {

    @Test
    @DisplayName("五平台识别")
    void routeAll() {
        assertEquals("bilibili", LinkRouter.route("https://www.bilibili.com/video/BV1xx411c7mD").platform());
        assertEquals("weibo", LinkRouter.route("https://m.weibo.cn/detail/5012345678901234").platform());
        assertEquals("douyin", LinkRouter.route("https://www.douyin.com/video/7412345678901234567").platform());

        var ks = LinkRouter.route("https://www.kuaishou.com/short-video/3xabcdefg?x=1");
        assertEquals("kuaishou", ks.platform());
        assertEquals("3xabcdefg", ks.id());
        assertEquals("video", ks.kind());

        var ks2 = LinkRouter.route("https://v.kuaishou.com/f/X-abc_123");
        assertEquals("kuaishou", ks2.platform());

        var xhs = LinkRouter.route("https://www.xiaohongshu.com/explore/65f0a1b2c3d4e5f6a7b8c9d0?xsec_token=1");
        assertEquals("xiaohongshu", xhs.platform());
        assertEquals("65f0a1b2c3d4e5f6a7b8c9d0", xhs.id());
        assertEquals("note", xhs.kind());

        assertNull(LinkRouter.route("随便一段文字"));
        assertNull(LinkRouter.route(""));
        assertNull(LinkRouter.route(null));
    }

    @Test
    @DisplayName("parsable：B站/微博/抖音/快手可解析，小红书待支持")
    void parsable() {
        assertTrue(LinkRouter.route("https://www.bilibili.com/video/BV1xx411c7mD").parsable());
        assertTrue(LinkRouter.route("https://m.weibo.cn/detail/5012345678901234").parsable());
        assertTrue(LinkRouter.route("https://www.douyin.com/video/7412345678901234567").parsable());
        assertTrue(LinkRouter.route("https://www.kuaishou.com/short-video/3xabc").parsable());
        assertFalse(LinkRouter.route("https://www.xiaohongshu.com/explore/65f0a1b2c3d4e5f6a7b8c9d0").parsable());
    }

    @Test
    @DisplayName("parseLines：多行混合，空行跳过，无法识别的行进 unparsed")
    void parseLines() {
        String text = """
                https://www.bilibili.com/video/BV1xx411c7mD

                【分享】https://m.weibo.cn/detail/5012345678901234
                这行不是链接
                https://www.kuaishou.com/short-video/3xabc
                """;
        LinkRouter.Outcome out = LinkRouter.parseLines(text);
        assertEquals(3, out.targets().size());
        assertEquals(List.of("bilibili", "weibo", "kuaishou"),
                out.targets().stream().map(LinkRouter.Target::platform).toList());
        assertEquals(List.of("这行不是链接"), out.unparsed());
    }

    @Test
    @DisplayName("platformName 中文名")
    void platformName() {
        assertEquals("B站", LinkRouter.platformName("bilibili"));
        assertEquals("小红书", LinkRouter.platformName("xiaohongshu"));
        assertEquals("unknown", LinkRouter.platformName("unknown"));
    }
}
