package cn.fmjj.toolbox.core;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * 应用路径定位：与 Python 版（FengMingToolbox）目录约定保持一致。
 *
 * <pre>
 *   &lt;appRoot&gt;/
 *     config/    配置与登录态（gitignore，不入库）
 *     download/  采集/导出数据（gitignore，不入库）
 *     logs/      运行日志
 * </pre>
 *
 * appRoot 解析规则：运行时取当前 jar 所在目录，IDE/开发模式取当前工作目录。
 */
public final class AppPaths {

    private static Path cachedRoot;

    private AppPaths() {}

    /** 应用根目录（jar 所在目录或开发模式下的 user.dir）。 */
    public static synchronized Path appRoot() {
        if (cachedRoot == null) {
            try {
                Path jarDir = Paths.get(AppPaths.class.getProtectionDomain()
                        .getCodeSource().getLocation().toURI()).getParent();
                // classes/ 目录（IDE 未打包时）向上退一级不可靠，统一策略见下
                cachedRoot = jarDir == null ? Paths.get("").toAbsolutePath() : jarDir;
            } catch (Exception e) {
                cachedRoot = Paths.get("").toAbsolutePath();
            }
        }
        return cachedRoot;
    }

    public static Path configDir() { return ensure(appRoot().resolve("config")); }

    public static Path downloadDir() { return ensure(appRoot().resolve("download")); }

    public static Path logsDir() { return ensure(appRoot().resolve("logs")); }

    /** 启动时一次性确保三件套目录存在。 */
    public static void ensureDirs() {
        configDir();
        downloadDir();
        logsDir();
    }

    public static Path settingsFile() { return configDir().resolve("settings.json"); }

    public static Path cookieDbFile() { return configDir().resolve("cookies.db"); }

    public static Path collectedDbFile() { return configDir().resolve("collected.db"); }

    private static Path ensure(Path dir) {
        try {
            if (!Files.isDirectory(dir)) {
                Files.createDirectories(dir);
            }
        } catch (IOException ignored) {
            // 只读文件系统等场景：调用方按路径不可用处理
        }
        return dir;
    }
}
