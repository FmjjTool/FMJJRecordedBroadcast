package cn.fmjj.toolbox.engine.tracker;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 音乐账号追踪存储测试（surefire 已用 fmjj.home=target/test-home 隔离）。 */
class MusicTrackerStoreTest {

    @Test
    @DisplayName("guessPlatform：从主页链接识别平台")
    void guessPlatform() {
        assertEquals("netease", MusicTrackerStore.guessPlatform("https://music.163.com/#/user/home?id=1"));
        assertEquals("qq", MusicTrackerStore.guessPlatform("https://y.qq.com/n/ryqq/singer/abc"));
        assertEquals("kugou", MusicTrackerStore.guessPlatform("https://www.kugou.com/singer/123.html"));
        assertEquals("kuwo", MusicTrackerStore.guessPlatform("https://www.kuwo.cn/singer_detail/1"));
        assertEquals("5sing", MusicTrackerStore.guessPlatform("http://5sing.kugou.com/123/default.html"));
        assertEquals("", MusicTrackerStore.guessPlatform("https://example.com/x"));
        assertEquals("", MusicTrackerStore.guessPlatform(null));
    }

    @Test
    @DisplayName("历史快照：追加、去重、涨跌计算")
    void historyAndDelta() {
        String name = "测试账号_" + System.nanoTime();
        MusicTrackerStore.appendHistory(name, "2026-10-23 10:00", 1000);
        MusicTrackerStore.appendHistory(name, "2026-10-23 12:00", 1500);
        var list = MusicTrackerStore.loadHistory().get(name);
        assertEquals(2, list.size());
        assertEquals(1500, list.get(1).fans());

        // 同名同时间覆盖
        MusicTrackerStore.appendHistory(name, "2026-10-23 12:00", 1600);
        list = MusicTrackerStore.loadHistory().get(name);
        assertEquals(2, list.size());
        assertEquals(1600, list.get(1).fans());

        // 涨跌：与最后一条比较
        assertEquals(400, MusicTrackerStore.delta(name, 2000));
        assertEquals(-600, MusicTrackerStore.delta(name, 1000));
        assertEquals(0, MusicTrackerStore.delta("不存在的账号" + System.nanoTime(), 100));
    }

    @Test
    @DisplayName("账号配置：保存后可读回")
    void accountsRoundTrip() {
        var acc = new MusicTrackerStore.Account("歌手A", "netease",
                "https://music.163.com/#/user/home?id=1", "1 组", "主力", 12345, "2026-10-23 10:00");
        MusicTrackerStore.saveAccounts(List.of(acc));
        var loaded = MusicTrackerStore.loadAccounts();
        assertTrue(loaded.stream().anyMatch(a -> a.name().equals("歌手A")), "应能读回刚保存的账号");
    }

    @Test
    @DisplayName("卡片绘制：尺寸随账号数增长")
    void drawCard() {
        var a = new MusicTrackerStore.Account("歌手A", "netease", "u", "1", "组", 12345, "2026-10-23 10:00");
        var one = cn.fmjj.toolbox.gui.pages.MusicTrackerPage.drawCard(List.of(a));
        assertTrue(one.getWidth() >= 320 && one.getHeight() >= 140);
        var four = cn.fmjj.toolbox.gui.pages.MusicTrackerPage.drawCard(List.of(a, a, a, a));
        assertTrue(four.getHeight() > one.getHeight());
    }
}
