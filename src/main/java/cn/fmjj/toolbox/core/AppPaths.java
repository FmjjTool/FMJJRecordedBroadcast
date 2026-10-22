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
 * appRoot 解析优先级：
 * <ol>
 *   <li>环境变量 {@code FMJJ_HOME}（显式指定）</li>
 *   <li>自身目录（jar 所在目录）下已有 config/ → 独立运行</li>
 *   <li>兄弟目录 {@code ../FMJJRecordedBroadcast}（Python 版）→ **共享登录态与数据**</li>
 *   <li>兜底：自身目录</li>
 * </ol>
 * 第 3 条是为了「Java 版与原版共用同一份 cookies.json」，否则用户在 Python 版扫码登录后
 * Java 版读不到（曾经踩过：CookieStore.get 返回空，误判成解密失败）。
 */
public final class AppPaths {

    private static Path cachedRoot;

    private AppPaths() {}

    /**
     * 应用根目录。
     *
     * <p><b>测试必须隔离</b>：系统属性 {@code fmjj.home} 优先于环境变量，
     * surefire 里固定指向 {@code target/test-home}。否则单元测试会操作真实
     * {@code config/cookies.json}（曾发生：CookieStoreTest 的 cleanup() 删掉了用户登录态）。
     */
    public static synchronized Path appRoot() {
        if (cachedRoot != null) return cachedRoot;
        String prop = System.getProperty("fmjj.home");
        String home = prop != null && !prop.isBlank() ? prop : System.getenv("FMJJ_HOME");
        cachedRoot = pickRoot(selfDir(), home);
        return cachedRoot;
    }

    /** 测试用：重置缓存（配合 fmjj.home 切换）。 */
    static synchronized void resetForTesting() {
        cachedRoot = null;
    }

    /**
     * 根目录选择（纯函数，便于测试）：显式 env → 自身/上级有 cookies.json →
     * 兄弟目录 FMJJRecordedBroadcast 有 cookies.json → 自身。
     */
    static Path pickRoot(Path self, String envHome) {
        if (envHome != null && !envHome.isBlank()) {
            Path p = Paths.get(envHome);
            if (Files.isDirectory(p)) return p.toAbsolutePath().normalize();
        }
        Path probe = self;
        for (int i = 0; i < 5 && probe != null; i++) {
            if (hasCookieFile(probe)) return probe;
            Path sibling = probe.resolveSibling("FMJJRecordedBroadcast");
            if (hasCookieFile(sibling)) return sibling.toAbsolutePath().normalize();
            probe = probe.getParent();
        }
        return self;
    }

    private static boolean hasCookieFile(Path dir) {
        return dir != null && Files.isRegularFile(dir.resolve("config").resolve("cookies.json"));
    }

    /** jar/classes 所在目录。 */
    private static Path selfDir() {
        try {
            Path codeSource = Paths.get(AppPaths.class.getProtectionDomain()
                    .getCodeSource().getLocation().toURI());
            Path dir = Files.isDirectory(codeSource) ? codeSource : codeSource.getParent();
            return dir == null ? Paths.get("").toAbsolutePath() : dir.toAbsolutePath().normalize();
        } catch (Exception e) {
            return Paths.get("").toAbsolutePath();
        }
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
