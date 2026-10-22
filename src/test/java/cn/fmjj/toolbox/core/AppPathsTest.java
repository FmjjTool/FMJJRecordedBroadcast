package cn.fmjj.toolbox.core;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 根目录选择测试。
 *
 * <p><b>红线</b>：Java 版只用自己的目录，**绝不探测兄弟目录**（Python 版仓库）。
 * 曾经为了让两边共享登录态去探测 `../FMJJRecordedBroadcast/config`，
 * 结果单元测试的 `deleteIfExists(configDir()/cookies.json)` 删掉了用户真实登录态。
 */
class AppPathsTest {

    private static void touchCookies(Path root) throws IOException {
        Files.createDirectories(root.resolve("config"));
        Files.writeString(root.resolve("config").resolve("cookies.json"), "{}");
    }

    @Test
    @DisplayName("默认：用自身目录")
    void selfByDefault(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("app"));
        touchCookies(self);
        assertEquals(self, AppPaths.pickRoot(self, null));
    }

    @Test
    @DisplayName("红线：兄弟目录是 Python 版也不碰")
    void neverTouchSiblingPythonHome(@TempDir Path tmp) throws IOException {
        Path javaHome = Files.createDirectories(tmp.resolve("FMJJRecordedBroadcast_JAVA"));
        Path self = Files.createDirectories(javaHome.resolve("target").resolve("classes"));
        touchCookies(tmp.resolve("FMJJRecordedBroadcast"));   // 兄弟目录（Python 版）
        assertEquals(self, AppPaths.pickRoot(self, null));
    }

    @Test
    @DisplayName("红线：上级目录有 config 也不碰")
    void neverWalkUp(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("target").resolve("classes"));
        touchCookies(tmp);
        assertEquals(self, AppPaths.pickRoot(self, null));
    }

    @Test
    @DisplayName("FMJJ_HOME 显式指定才生效（用户主动选择共享）")
    void envHomeWins(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("app"));
        Path envHome = Files.createDirectories(tmp.resolve("custom-home"));
        assertEquals(envHome.toAbsolutePath().normalize(), AppPaths.pickRoot(self, envHome.toString()));
    }

    @Test
    @DisplayName("FMJJ_HOME 指向不存在的目录 → 忽略，用自身")
    void envHomeInvalid(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("app"));
        assertEquals(self, AppPaths.pickRoot(self, tmp.resolve("nope").toString()));
    }
}
