package cn.fmjj.toolbox.core.settings;

import cn.fmjj.toolbox.core.AppPaths;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Settings 持久化测试（读写同一配置文件，覆盖默认值兼容路径）。 */
class SettingsTest {

    @Test
    @DisplayName("save → load 往返一致；缺省键有默认值")
    void roundTrip() throws IOException {
        Path cfg = AppPaths.settingsFile();
        Settings s = Settings.load();
        s.setWorkDir("D:/tmp/work");
        s.setExportDir("D:/tmp/export");
        s.setTheme("dark");
        s.setThemeFollowSystem(true);
        s.setThemeLightTime("06:30");
        s.setFfmpegPath("D:/ffmpeg/bin");
        s.setExportDirLocked(true);
        s.save();

        Settings loaded = Settings.load();
        assertEquals("D:/tmp/work", loaded.workDir());
        assertEquals("D:/tmp/export", loaded.exportDir());
        assertEquals("dark", loaded.theme());
        assertTrue(loaded.themeFollowSystem());
        assertEquals("06:30", loaded.themeLightTime());
        assertEquals("19:00", loaded.themeDarkTime());   // 未设置的键保持默认
        assertEquals("D:/ffmpeg/bin", loaded.ffmpegPath());
        assertTrue(loaded.exportDirLocked());
        assertTrue(loaded.effectiveWorkDir().toString().contains("tmp"));

        Files.deleteIfExists(cfg);
    }

    @Test
    @DisplayName("effectiveWorkDir：空配置回退 appRoot/download")
    void fallbackDirs() {
        Settings s = new Settings();
        Path p = s.effectiveWorkDir();
        assertTrue(p.toString().replace('\\', '/').endsWith("download"), p.toString());
        // appRoot 可能是自身 target（首次运行）或兄弟目录的 Python 版（共享登录态），
        // 只校验目录真实存在，不绑定具体位置
        assertTrue(java.nio.file.Files.isDirectory(p), p.toString());
    }
}
