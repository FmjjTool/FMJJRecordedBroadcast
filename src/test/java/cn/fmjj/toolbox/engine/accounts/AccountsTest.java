package cn.fmjj.toolbox.engine.accounts;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * 账号管理/时间线辅助函数跨语言一致性测试。
 * 期望值由 Python 原版 accounts_manager.py / gui_qt/pages/info_accounts.py 运行时生成。
 */
class AccountsTest {

    @Test
    void accountLineRoundTripMatchesPython() {
        AccountStore.Account a = AccountStore.Account.fromLine(
                "11,22|游戏主播|抖音大号|https://www.douyin.com/xxx|备注|all");
        assertEquals("https://www.douyin.com/xxx", a.url);
        assertEquals("抖音大号", a.name);
        assertEquals(List.of("11", "22"), a.groupIds);
        assertEquals("游戏主播", a.groupName);
        assertEquals("备注", a.note);
        assertEquals("all", a.kind);
        assertEquals("11,22|游戏主播|抖音大号|https://www.douyin.com/xxx|备注|all", a.toLine());

        AccountStore.Account plain = AccountStore.Account.fromLine("https://example.com");
        assertEquals("https://example.com", plain.url);
        assertEquals("", plain.name);
        assertEquals("https://example.com", plain.toLine());

        AccountStore.Account named = AccountStore.Account.fromLine("账号名|https://example.com|备注");
        assertEquals("https://example.com", named.url);
        assertEquals("账号名", named.name);
        assertEquals("备注", named.note);
    }

    @Test
    void kindAndGroupParsingMatchesPython() {
        assertEquals("video", AccountStore.inferKindFromUrl("https://space.bilibili.com/123/video"));
        assertEquals("dynamic", AccountStore.inferKindFromUrl("https://space.bilibili.com/123/dynamic"));
        assertEquals("", AccountStore.inferKindFromUrl("https://space.bilibili.com/123"));
        assertEquals(List.of("11", "22", "33"), AccountStore.parseGroupIds("11,22，33,4,x"));
    }

    @Test
    void publishTimeParsingMatchesPython() {
        assertEquals(1790490659L, AccountFeed.parsePublishTs("2026-09-27 14:30:59"));
        assertEquals(1790438400L, AccountFeed.parsePublishTs("2026/9/27"));
        assertEquals(1790438400L, AccountFeed.parsePublishTs("2026年9月27日"));
        assertNull(AccountFeed.parsePublishTs("abc"));
        assertNull(AccountFeed.parsePublishTs(""));

        long now = System.currentTimeMillis() / 1000;
        Long threeHours = AccountFeed.parsePublishTs("3小时前");
        assertEquals(now - 3 * 3600, threeHours, 5);
        Long yesterday = AccountFeed.parsePublishTs("昨天");
        assertEquals(now - 86400, yesterday, 5);
    }

    @Test
    void platformDetection() {
        assertEquals("bilibili", AccountFeed.detectPlatform("https://space.bilibili.com/123"));
        assertEquals("weibo", AccountFeed.detectPlatform("https://weibo.com/u/123456"));
        assertEquals("douyin", AccountFeed.detectPlatform("https://www.douyin.com/user/MS4wLjAB"));
        assertEquals("kuaishou", AccountFeed.detectPlatform("https://www.kuaishou.com/profile/abc"));
        assertEquals("xiaohongshu", AccountFeed.detectPlatform("https://www.xiaohongshu.com/user/profile/abc"));
        assertEquals("", AccountFeed.detectPlatform("https://example.com"));
    }

    @Test
    void normalizeDateTimeMatchesPython() {
        assertEquals("2026-10-24 18:00",
                AccountFeed.normalizeDateTime("Sat Oct 24 18:00:00 +0800 2026"));
        assertEquals("2026-07-02 00:00", AccountFeed.normalizeDateTime("2026-07-02"));
        assertEquals("2026-07-02 00:00", AccountFeed.normalizeDateTime("2026年7月2日"));
        assertEquals("2026-07-02 00:00", AccountFeed.normalizeDateTime("2026-07-02T00:00:01+08:00"));
        assertEquals("abc", AccountFeed.normalizeDateTime("abc"));
    }

    @Test
    void xiaohongshuSsrParsingMatchesPython() {
        String html = "<script>window.__INITIAL_STATE__={\"user\":{\"notes\":[[{\"noteCard\":"
                + "{\"noteId\":\"abc\",\"displayTitle\":\"T\",\"interactInfo\":{\"likedCount\":\"1.2万\"},"
                + "\"cover\":{\"urlDefault\":\"//x/a.jpg\"},\"time\":1758714000000,"
                + "\"user\":{\"nickname\":\"N\",\"userId\":\"U\"},\"xsecToken\":\"xs\"}}]]},"
                + "\"flag\":undefined,\"set\":new Set([1,2]),}</script>";
        List<AccountFeed.Post> posts = AccountFeed.xhsPostsFromHtml(html, 10);
        assertEquals(1, posts.size());
        AccountFeed.Post p = posts.get(0);
        assertEquals("abc", p.videoId);
        assertEquals("T", p.title);
        assertEquals("N", p.author);
        assertEquals("U", p.authorId);
        assertEquals(12000, p.likeCount);
        assertEquals("https://x/a.jpg", p.coverUrl);
        assertEquals("2025-09-24 19:40", p.publishTime);
        assertEquals("https://www.xiaohongshu.com/discovery/item/abc"
                + "?xsec_token=xs&xsec_source=pc_search", p.videoUrl);

        assertEquals(12000, AccountFeed.parseCnCount("1.2万"));
        assertEquals(100000, AccountFeed.parseCnCount("10万+"));
        assertEquals(200000000, AccountFeed.parseCnCount("2亿"));
        assertEquals(0, AccountFeed.parseCnCount(""));
    }

    @Test
    void balancedJsonRejectsMissingMarker() {
        assertNull(AccountFeed.balancedJson("<html></html>", "window\\.__INITIAL_STATE__"));
    }

    @Test
    void dateFilterKeepsUnknownTimes() {
        AccountFeed.Post inside = new AccountFeed.Post();
        inside.videoId = "1";
        inside.publishTs = AccountFeed.parsePublishTs("2026-09-27 12:00");
        AccountFeed.Post outside = new AccountFeed.Post();
        outside.videoId = "2";
        outside.publishTs = AccountFeed.parsePublishTs("2026-09-28 12:00");
        AccountFeed.Post unknown = new AccountFeed.Post();
        unknown.videoId = "3";
        unknown.publishTs = 0;
        List<AccountFeed.Post> kept = AccountFeed.filterByDate(
                List.of(inside, outside, unknown), "2026-09-27", "2026-09-27");
        assertEquals(2, kept.size());
        assertEquals("1", kept.get(0).videoId);
        assertEquals("3", kept.get(1).videoId);
    }
}
