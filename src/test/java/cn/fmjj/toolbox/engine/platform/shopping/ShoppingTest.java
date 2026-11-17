package cn.fmjj.toolbox.engine.platform.shopping;

import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * 购物解析辅助函数跨语言一致性测试。
 * 期望值由 Python 原版 shopping.py 运行时生成。
 */
class ShoppingTest {

    @Test
    void detectPlatformMatchesPython() {
        assertArrayEquals(new String[]{"taobao_item", "123456"},
                Shopping.detectShoppingPlatform("https://item.taobao.com/item.htm?id=123456"));
        assertArrayEquals(new String[]{"taobao_item", "999"},
                Shopping.detectShoppingPlatform("https://detail.tmall.com/item.htm?id=999&spm=x"));
        assertArrayEquals(new String[]{"taobao_shop", "abc"},
                Shopping.detectShoppingPlatform("https://abc.taobao.com/shop/view_shop.htm?spm=x"));
        assertArrayEquals(new String[]{"taobao_shop_id", "12345"},
                Shopping.detectShoppingPlatform("https://shop12345.taobao.com"));
        assertArrayEquals(new String[]{"taobao_unknown", ""},
                Shopping.detectShoppingPlatform("https://www.taobao.com/foo"));
        assertNull(Shopping.detectShoppingPlatform("https://item.jd.com/1.html"));
    }

    @Test
    void fmtPriceMatchesPython() {
        assertEquals("¥25.00", Shopping.fmtPrice("¥25.00"));
        assertEquals("¥25", Shopping.fmtPrice("25"));
        assertEquals("", Shopping.fmtPrice("abc"));
        assertEquals("", Shopping.fmtPrice(""));
        assertEquals("", Shopping.fmtPrice(null));
        assertEquals("¥188.50", Shopping.fmtPrice("188.50元"));
    }

    @Test
    void fmtSalesMatchesPython() {
        assertEquals("已售 1万+", Shopping.fmtSales("1万+"));
        assertEquals("100+人付款", Shopping.fmtSales("100+人付款"));
        assertEquals("月销 64", Shopping.fmtSales("已售 64"));
        assertEquals("月销 12", Shopping.fmtSales("月销 12"));
        assertEquals("月销 12", Shopping.fmtSales("12"));
        assertEquals("", Shopping.fmtSales(""));
        assertEquals("", Shopping.fmtSales(null));
        assertEquals("已售 1.2万", Shopping.fmtSales("1.2万"));
    }

    @Test
    void folderAndExtMatchPython() {
        assertEquals("商品标题特价版", Shopping.safeFolderName("商品标题/特价:版", 40));
        assertEquals("abc", Shopping.safeFolderName("  .abc._ ", 40));
        assertEquals("未命名", Shopping.safeFolderName("", 40));
        assertEquals(".jpg", Shopping.imgExt("https://x/a.JPG?x=1"));
        assertEquals(".jpg", Shopping.imgExt("https://x/a.jpeg"));
        assertEquals(".webp", Shopping.imgExt("https://x/a.webp~tplv"));
        assertEquals(".jpg", Shopping.imgExt("https://x/a"));
    }

    @Test
    void extractIceSsrMatchesPython() {
        String html = "<script>!(function () {var a = window.__ICE_APP_CONTEXT__ || {};"
                + "var b = {\"loaderData\":{\"home\":{\"data\":{\"res\":{\"item\":{\"title\":\"T\"}}}}}};"
                + "for (var k in a) {b[k] = a[k]}window.__ICE_APP_CONTEXT__=b;})();</script>";
        ObjectNode ssr = Shopping.extractIceSsr(html);
        assertNotNull(ssr);
        assertEquals("T", Shopping.ssrRes(ssr).path("item").path("title").asText());
        assertNull(Shopping.extractIceSsr("<html>nothing</html>"));
    }
}
