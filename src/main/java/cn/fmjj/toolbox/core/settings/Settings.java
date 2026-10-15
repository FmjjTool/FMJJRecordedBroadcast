package cn.fmjj.toolbox.core.settings;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.JsonMapper;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * 应用设置：config/settings.json 持久化。
 * 对应 Python 版 config_manager.py 的配置中枢（M2 只移植全局设置页相关键，
 * 其余 60+ 键随各页面里程碑补齐）。
 */
public final class Settings {

    private static final ObjectMapper MAPPER = JsonMapper.get();

    private String workDir = "";
    private String exportDir = "";
    private String theme = "light";                 // light / dark
    private boolean themeScheduleEnabled = false;
    private boolean themeFollowSystem = false;
    private String themeLightTime = "07:00";
    private String themeDarkTime = "19:00";
    private String ffmpegPath = "";
    private boolean workDirLocked = false;
    private boolean exportDirLocked = false;

    Settings() {}

    public static Settings load() {
        Settings s = new Settings();
        Path file = AppPaths.settingsFile();
        if (Files.isRegularFile(file)) {
            try {
                Settings disk = MAPPER.readValue(file.toFile(), Settings.class);
                if (disk != null) s = disk;
            } catch (Exception e) {
                // 配置损坏时回退默认值（与 Python _read_json 的容错一致）
            }
        }
        return s;
    }

    public void save() {
        try {
            MAPPER.writerWithDefaultPrettyPrinter().writeValue(AppPaths.settingsFile().toFile(), this);
        } catch (Exception ignored) {
            // 只读场景由调用方以 toast 提示
        }
    }

    // ---------- 工作目录 / 导出目录 ----------

    /** 与 Python get_app_root/download 约定一致：默认 <appRoot>/download。 */
    public Path effectiveWorkDir() {
        return resolveDir(workDir, AppPaths.appRoot().resolve("download"));
    }

    public Path effectiveExportDir() {
        return resolveDir(exportDir, AppPaths.appRoot().resolve("download"));
    }

    private static Path resolveDir(String configured, Path fallback) {
        if (configured != null && !configured.isBlank()) {
            Path p = Path.of(configured);
            try {
                Files.createDirectories(p);
                return p;
            } catch (Exception ignored) {
                // 配置的目录不可用 → 回退
            }
        }
        try {
            Files.createDirectories(fallback);
        } catch (Exception ignored) {
        }
        return fallback;
    }

    // ---------- getters / setters ----------

    public String workDir() { return workDir; }
    public void setWorkDir(String workDir) { this.workDir = workDir == null ? "" : workDir; }

    public String exportDir() { return exportDir; }
    public void setExportDir(String exportDir) { this.exportDir = exportDir == null ? "" : exportDir; }

    public String theme() { return theme; }
    public void setTheme(String theme) { this.theme = theme; }

    public boolean themeScheduleEnabled() { return themeScheduleEnabled; }
    public void setThemeScheduleEnabled(boolean v) { this.themeScheduleEnabled = v; }

    public boolean themeFollowSystem() { return themeFollowSystem; }
    public void setThemeFollowSystem(boolean v) { this.themeFollowSystem = v; }

    public String themeLightTime() { return themeLightTime; }
    public void setThemeLightTime(String v) { this.themeLightTime = v; }

    public String themeDarkTime() { return themeDarkTime; }
    public void setThemeDarkTime(String v) { this.themeDarkTime = v; }

    public String ffmpegPath() { return ffmpegPath; }
    public void setFfmpegPath(String ffmpegPath) { this.ffmpegPath = ffmpegPath == null ? "" : ffmpegPath; }

    public boolean workDirLocked() { return workDirLocked; }
    public void setWorkDirLocked(boolean v) { this.workDirLocked = v; }

    public boolean exportDirLocked() { return exportDirLocked; }
    public void setExportDirLocked(boolean v) { this.exportDirLocked = v; }
}
