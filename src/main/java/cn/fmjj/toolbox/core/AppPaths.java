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
 * <p><b>边界</b>：Java 版**只用自己的目录**，绝不读写 Python 版仓库的任何文件。
 * （曾经为了「登录态互通」去探测兄弟目录 {@code ../FMJJRecordedBroadcast/config}，
 * 结果单元测试里的 deleteIfExists 直接删掉了用户真实的 cookies.json —— 不可恢复。
 * 结论：跨仓库共享数据是红线，宁可让用户手动导入，也不能隐式串味。）
 *
 * <p>如需自定义位置，设环境变量 {@code FMJJ_HOME}；测试用系统属性 {@code fmjj.home}（优先级更高）。
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
     * 根目录选择（纯函数，便于测试）：显式指定 → 自身目录。
     * **不探测兄弟目录**（跨仓库共享数据是红线，见类注释）。
     */
    static Path pickRoot(Path self, String envHome) {
        if (envHome != null && !envHome.isBlank()) {
            Path p = Paths.get(envHome);
            if (Files.isDirectory(p)) return p.toAbsolutePath().normalize();
        }
        return self;
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
