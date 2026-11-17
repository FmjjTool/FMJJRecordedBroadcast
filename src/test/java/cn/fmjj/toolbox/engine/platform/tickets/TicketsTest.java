package cn.fmjj.toolbox.engine.platform.tickets;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.time.ZoneId;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * 票务解析辅助函数跨语言一致性测试。
 * 期望值由 Python 原版 tickets.py 运行时生成。
 */
class TicketsTest {

    @Test
    void detectTicketPlatformMatchesPython() {
        assertArrayEquals(new String[]{"damai_artist", "33879518699"},
                Tickets.detectTicketPlatform("https://m.damai.cn/shows/artist.html?artistId=33879518699"));
        assertArrayEquals(new String[]{"damai_item", "123456"},
                Tickets.detectTicketPlatform("https://m.damai.cn/damai/detail/item.html?itemId=123456"));
        assertArrayEquals(new String[]{"showstart_artist", "8982311"},
                Tickets.detectTicketPlatform("https://www.showstart.com/artist/8982311"));
        assertArrayEquals(new String[]{"showstart_event", "306602"},
                Tickets.detectTicketPlatform("https://www.showstart.com/event/306602"));
        assertArrayEquals(new String[]{"bili_project", "98765"},
                Tickets.detectTicketPlatform("https://show.bilibili.com/platform/detail.html?id=98765"));
        assertArrayEquals(new String[]{"bili_project", "555"},
                Tickets.detectTicketPlatform("https://show.bilibili.com/platform/detail.html?project_id=555"));
        assertArrayEquals(new String[]{"bili_search", "封茗"},
                Tickets.detectTicketPlatform(
                        "https://show.bilibili.com/platform/search.html?searchValue=%E5%B0%81%E8%8C%97"));
        assertArrayEquals(new String[]{"unknown_url", ""},
                Tickets.detectTicketPlatform("https://m.damai.cn/foo"));
        assertNull(Tickets.detectTicketPlatform("封茗囧菌"));
    }

    @Test
    void biliPriceMatchesPython() {
        JsonNodeFactory f = JsonNodeFactory.instance;
        assertEquals("¥180", Tickets.biliPrice(f.numberNode(18000), null));
        assertEquals("¥180", Tickets.biliPrice(f.numberNode(18000), f.numberNode(18000)));
        assertEquals("¥180-280", Tickets.biliPrice(f.numberNode(18000), f.numberNode(28000)));
        assertEquals("", Tickets.biliPrice(f.numberNode(0), null));
        assertEquals("¥199", Tickets.biliPrice(f.numberNode(19900), null));
        assertEquals("", Tickets.biliPrice(f.textNode("x"), null));
    }

    @Test
    void biliTimeMatchesPython() {
        String expected = LocalDateTime.ofInstant(java.time.Instant.ofEpochSecond(1758714000),
                ZoneId.systemDefault()).format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"));
        assertEquals(expected, Tickets.biliTime(1758714000));
        assertEquals("", Tickets.biliTime(0));
    }

    @Test
    void folderAndExtMatchPython() {
        assertEquals("封茗囧菌 巡演苏州站", Tickets.safeFolderName("封茗囧菌 巡演/苏州:站", 40));
        assertEquals("abc", Tickets.safeFolderName("  .abc._ ", 40));
        assertEquals("未命名", Tickets.safeFolderName("", 40));
        assertEquals(".jpg", Tickets.imgExt("https://x/a.JPG?x=1"));
        assertEquals(".jpg", Tickets.imgExt("https://x/a.jpeg"));
        assertEquals(".webp", Tickets.imgExt("https://x/a.webp~tplv"));
        assertEquals(".jpg", Tickets.imgExt("https://x/a"));
    }

    @Test
    void stripTagsMatchesPython() {
        assertEquals("你好\u00a0世界2", Tickets.stripTags("<p>你好&nbsp;世界</p><br/>2"));
    }

    private static void assertArrayEquals(String[] expected, String[] actual) {
        org.junit.jupiter.api.Assertions.assertArrayEquals(expected, actual);
    }
}
