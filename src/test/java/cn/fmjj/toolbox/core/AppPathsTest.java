package cn.fmjj.toolbox.core;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 根目录探测测试。
 * 关键行为：Java 版要与 Python 版共用同一份 cookies.json（登录态互通），
 * 否则用户在 Python 版扫码登录后 Java 版读不到（曾误判成「解密失败」）。
 */
class AppPathsTest {

    private static void touchCookies(Path root) throws IOException {
        Files.createDirectories(root.resolve("config"));
        Files.writeString(root.resolve("config").resolve("cookies.json"), "{}");
    }

    @Test
    @DisplayName("自身目录有 cookies.json → 用自身（绿色版独立运行）")
    void selfHasCookies(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("app"));
        touchCookies(self);
        assertEquals(self, AppPaths.pickRoot(self, null));
    }

    @Test
    @DisplayName("兄弟目录是 Python 版 → 用它（共享登录态）")
    void siblingPythonHome(@TempDir Path tmp) throws IOException {
        Path javaHome = Files.createDirectories(tmp.resolve("FMJJRecordedBroadcast_JAVA"));
        Path self = Files.createDirectories(javaHome.resolve("target").resolve("classes"));
        touchCookies(javaHome.resolveSibling("FMJJRecordedBroadcast"));
        assertEquals(tmp.resolve("FMJJRecordedBroadcast").toAbsolutePath().normalize(),
                AppPaths.pickRoot(self, null));
    }

    @Test
    @DisplayName("都没有 → 兜底自身")
    void fallbackSelf(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("solo").resolve("classes"));
        assertEquals(self, AppPaths.pickRoot(self, null));
    }

    @Test
    @DisplayName("FMJJ_HOME 优先")
    void envHomeWins(@TempDir Path tmp) throws IOException {
        Path self = Files.createDirectories(tmp.resolve("app"));
        touchCookies(self);
        Path envHome = Files.createDirectories(tmp.resolve("custom-home"));
        assertEquals(envHome.toAbsolutePath().normalize(), AppPaths.pickRoot(self, envHome.toString()));
    }

    @Test
    @DisplayName("空 config 目录不算（避免被 target/config 误导）")
    void emptyConfigIgnored(@TempDir Path tmp) throws IOException {
        Path javaHome = Files.createDirectories(tmp.resolve("FMJJRecordedBroadcast_JAVA"));
        Path self = Files.createDirectories(javaHome.resolve("target").resolve("classes"));
        Files.createDirectories(javaHome.resolve("target").resolve("config"));   // 空目录
        touchCookies(tmp.resolve("FMJJRecordedBroadcast"));
        assertEquals(tmp.resolve("FMJJRecordedBroadcast").toAbsolutePath().normalize(),
                AppPaths.pickRoot(self, null));
    }
}
