package cn.fmjj.toolbox.engine.auth;

import cn.fmjj.toolbox.core.AppPaths;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Cookie 仓库测试：读写往返、明文兼容、TTL、总开关（写 target/config，用完清理）。 */
class CookieStoreTest {

    private Path cookieFile() { return AppPaths.configDir().resolve("cookies.json"); }

    private void cleanup() throws IOException {
        Files.deleteIfExists(cookieFile());
    }

    @Test
    @DisplayName("set → get 往返（加密落盘、自动解密）")
    void setGetRoundTrip() throws IOException {
        cleanup();
        try {
            CookieStore.set("bilibili", "SESSDATA=abc123; bili_jct=xyz");
            assertEquals("SESSDATA=abc123; bili_jct=xyz", CookieStore.get("bilibili"));

            // 落盘内容必须是 enc: 前缀（不落明文）
            String disk = Files.readString(cookieFile());
            assertTrue(disk.contains("enc:"), disk.substring(0, Math.min(80, disk.length())));
            assertFalse(disk.contains("SESSDATA=abc123"), "cookies.json 不应包含明文 Cookie");

            // 覆盖写
            CookieStore.set("bilibili", "SESSDATA=new");
            assertEquals("SESSDATA=new", CookieStore.get("bilibili"));
        } finally {
            cleanup();
        }
    }

    @Test
    @DisplayName("clear 移除平台；总开关关闭后 get 返回空")
    void clearAndSwitch() throws IOException {
        cleanup();
        try {
            CookieStore.set("qq", "uin=100");
            CookieStore.setEnabled(false);
            assertEquals("", CookieStore.get("qq"));
            assertFalse(CookieStore.isEnabled());

            CookieStore.setEnabled(true);
            assertEquals("uin=100", CookieStore.get("qq"));

            CookieStore.clear("qq");
            assertEquals("", CookieStore.get("qq"));
        } finally {
            CookieStore.setEnabled(true);
            cleanup();
        }
    }

    @Test
    @DisplayName("旧格式明文（无 enc: 前缀）原样返回——向后兼容")
    void legacyPlaintext() throws IOException {
        cleanup();
        try {
            Files.writeString(cookieFile(), """
                    {
                      "cookies": { "douyin": "sessionid=legacy-plain" },
                      "cookie_enabled": true
                    }
                    """);
            assertEquals("sessionid=legacy-plain", CookieStore.get("douyin"));
        } finally {
            cleanup();
        }
    }

    @Test
    @DisplayName("weibo TTL 30 天：旧时间视为过期")
    void weiboTtl() throws IOException {
        cleanup();
        try {
            CookieStore.set("weibo", "SUB=token");
            assertFalse(CookieStore.expired("weibo"));
            assertEquals("SUB=token", CookieStore.get("weibo"));

            // 手改保存时间为 40 天前 → get 返回空
            java.time.format.DateTimeFormatter fmt =
                    java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
            String old = java.time.LocalDateTime.now().minusDays(40).format(fmt);
            String disk = Files.readString(cookieFile())
                    .replaceAll("\"cookie_times\"\\s*:\\s*\\{[^}]*\\}",
                            "\"cookie_times\": { \"weibo\": \"" + old + "\" }");
            Files.writeString(cookieFile(), disk);
            assertEquals("", CookieStore.get("weibo"));
            assertTrue(CookieStore.expired("weibo"));

            // 非 TTL 平台永不过期
            CookieStore.set("bilibili", "SESSDATA=x");
            assertFalse(CookieStore.expired("bilibili"));
        } finally {
            cleanup();
        }
    }

    @Test
    @DisplayName("平台元数据：12 平台中文名 + 10 个浏览器登录配置")
    void metadata() {
        assertEquals(12, CookieStore.PLATFORM_NAMES.size());
        assertEquals("B站", CookieStore.displayName("bilibili"));
        assertEquals(10, CookieStore.BROWSER_LOGIN.size());
        assertEquals("https://www.bilibili.com/", CookieStore.BROWSER_LOGIN.get("bilibili").url());
        assertEquals("SESSDATA", CookieStore.BROWSER_LOGIN.get("bilibili").markers()[0]);
    }
}
