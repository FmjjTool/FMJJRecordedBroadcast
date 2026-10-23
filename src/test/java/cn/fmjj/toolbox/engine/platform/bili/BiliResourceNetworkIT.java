package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** B站资源真实网络冒烟（默认跳过）：FMJJ_NET_TEST=1 mvn test -Dtest=BiliResourceNetworkIT */
class BiliResourceNetworkIT {

    @Test
    @DisplayName("空间 card + WBI 综合搜索")
    void live() throws Exception {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")), "需要 FMJJ_NET_TEST=1");
        String cookie = CookieStore.get("bilibili");
        System.out.println("  cookieLen=" + cookie.length());

        var res = new BiliResource();
        var card = res.spaceCard("2", cookie);
        System.out.println("  card name=" + card.name() + " fans=" + card.fansText()
                + " level=" + card.level() + " face=" + card.face().substring(0, Math.min(50, card.face().length())));
        assertFalse(card.name().isEmpty(), "昵称为空");

        var keys = res.fetchKeys(cookie);
        System.out.println("  wbi imgKey=" + keys.imgKey() + " subKey=" + keys.subKey());

        var items = res.searchAll("封茗囧菌", 1, cookie);
        System.out.println("  search count=" + items.size());
        items.stream().limit(5).forEach(i -> System.out.println("   - " + i.type() + " | " + i.title() + " | " + i.url()));
        assertTrue(items.size() > 0, "搜索结果为空");
    }
}
