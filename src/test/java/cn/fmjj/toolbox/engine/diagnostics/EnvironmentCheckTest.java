package cn.fmjj.toolbox.engine.diagnostics;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 环境自检测试：各项都要能返回结果（不因缺依赖抛异常）。 */
class EnvironmentCheckTest {

    @Test
    @DisplayName("run：7 项齐全，每项都有名称/状态/说明")
    void runAll() {
        List<EnvironmentCheck.Item> items = EnvironmentCheck.run();
        assertEquals(7, items.size());
        for (var it : items) {
            assertNotNull(it.name());
            assertNotNull(it.status());
            assertNotNull(it.detail());
            assertFalse(it.name().isBlank());
        }
    }

    @Test
    @DisplayName("Java 版本：当前环境应满足 ≥21")
    void java() {
        var it = EnvironmentCheck.checkJava();
        assertEquals(EnvironmentCheck.Status.OK, it.status(), it.detail());
        assertTrue(it.detail().contains(System.getProperty("java.version")));
    }

    @Test
    @DisplayName("parseMajor：27.0.1→27、1.8.0→1、非法→0")
    void parseMajor() {
        assertEquals(27, EnvironmentCheck.parseMajor("27.0.1"));
        assertEquals(21, EnvironmentCheck.parseMajor("21"));
        assertEquals(1, EnvironmentCheck.parseMajor("1.8.0_412"));
        assertEquals(0, EnvironmentCheck.parseMajor("abc"));
        assertEquals(0, EnvironmentCheck.parseMajor(""));
    }

    @Test
    @DisplayName("FFmpeg：本机有则 OK 且带版本，没有则 FAIL 且有指引")
    void ffmpeg() {
        var it = EnvironmentCheck.checkFfmpeg();
        if (it.status() == EnvironmentCheck.Status.OK) {
            assertTrue(it.detail().contains("ffmpeg") || it.detail().contains("FFmpeg"), it.detail());
        } else {
            assertEquals(EnvironmentCheck.Status.FAIL, it.status());
            assertTrue(it.detail().contains("设置") || it.detail().contains("PATH"), it.detail());
        }
    }

    @Test
    @DisplayName("数据目录与磁盘：都能给出结果")
    void dirsAndDisk() {
        assertEquals(EnvironmentCheck.Status.OK, EnvironmentCheck.checkDirs().status());
        var disk = EnvironmentCheck.checkDisk();
        assertNotNull(disk.detail());
        assertTrue(disk.status() == EnvironmentCheck.Status.OK
                || disk.status() == EnvironmentCheck.Status.WARN);
    }

    @Test
    @DisplayName("登录态：无 cookies.json 时 WARN 而不是异常")
    void cookies() {
        var it = EnvironmentCheck.checkCookies();
        assertNotNull(it.status());
        assertNotNull(it.detail());
    }

    @Test
    @DisplayName("allOk：有 FAIL 即不通过")
    void allOk() {
        assertTrue(EnvironmentCheck.allOk(List.of(
                new EnvironmentCheck.Item("a", EnvironmentCheck.Status.OK, ""),
                new EnvironmentCheck.Item("b", EnvironmentCheck.Status.WARN, ""))));
        assertFalse(EnvironmentCheck.allOk(List.of(
                new EnvironmentCheck.Item("a", EnvironmentCheck.Status.FAIL, ""))));
    }

    @Test
    @DisplayName("状态图标")
    void icons() {
        assertEquals("✅", new EnvironmentCheck.Item("x", EnvironmentCheck.Status.OK, "").icon());
        assertEquals("⚠️", new EnvironmentCheck.Item("x", EnvironmentCheck.Status.WARN, "").icon());
        assertEquals("❌", new EnvironmentCheck.Item("x", EnvironmentCheck.Status.FAIL, "").icon());
    }
}
