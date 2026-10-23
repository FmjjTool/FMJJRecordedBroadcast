package cn.fmjj.toolbox.engine.tracker;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 追踪账号工具与卡片绘制测试。 */
class TrackerStoreTest {

    @Test
    @DisplayName("biliUid：从空间链接/纯 UID 提取")
    void biliUid() {
        assertEquals("12345", TrackerStore.biliUid("https://space.bilibili.com/12345"));
        assertEquals("12345", TrackerStore.biliUid("https://space.bilibili.com/12345?spm=1"));
        assertEquals("12345", TrackerStore.biliUid("12345"));
        assertEquals("", TrackerStore.biliUid("https://www.bilibili.com/video/BV1xx"));
        assertEquals("", TrackerStore.biliUid(null));
    }

    @Test
    @DisplayName("formatCount：万/亿/原值")
    void formatCount() {
        assertEquals("999", TrackerStore.formatCount(999));
        assertEquals("1.2万", TrackerStore.formatCount(12345));
        assertEquals("143.2万", TrackerStore.formatCount(1_432_000));
        assertEquals("1.50亿", TrackerStore.formatCount(150_000_000));
    }

    @Test
    @DisplayName("platformName：未知平台回退原值")
    void platformName() {
        assertEquals("B站", TrackerStore.platformName("bilibili"));
        assertEquals("小红书", TrackerStore.platformName("xiaohongshu"));
        assertEquals("weird", TrackerStore.platformName("weird"));
    }

    @Test
    @DisplayName("卡片绘制：尺寸随账号数增长，空列表也能画")
    void drawCard() {
        var a = new TrackerStore.Account("封茗囧菌", "bilibili",
                "https://space.bilibili.com/12345", "1", "歌手组", 1_432_000, 5_000_000, "2026-10-23 19:00");
        var one = cn.fmjj.toolbox.gui.pages.TrackerPage.drawCard(List.of(a));
        assertTrue(one.getWidth() >= 320);
        assertTrue(one.getHeight() >= 140);

        var four = cn.fmjj.toolbox.gui.pages.TrackerPage.drawCard(List.of(a, a, a, a));
        assertTrue(four.getHeight() > one.getHeight(), "4 个账号应占两行");

        var empty = cn.fmjj.toolbox.gui.pages.TrackerPage.drawCard(List.of());
        assertTrue(empty.getWidth() > 0 && empty.getHeight() > 0, "空列表也应能画");
    }
}
