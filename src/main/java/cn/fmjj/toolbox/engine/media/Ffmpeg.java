package cn.fmjj.toolbox.engine.media;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.settings.Settings;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * ffmpeg / ffprobe 定位与子进程执行。
 * 移植自 Python 版 toolbox/utils.py 的 find_ffmpeg_exe / classify_ffmpeg_error /
 * preserve_timestamps，以及各引擎里的 subprocess 调用姿势。
 *
 * 定位顺序（与原版一致）：系统 PATH → 全局设置的自定义位置 → 内置 bin/ → 裸 "ffmpeg"。
 */
public final class Ffmpeg {

    private static volatile String cachedFfmpeg;
    private static volatile String cachedFfprobe;

    private Ffmpeg() {}

    /** 子进程执行结果。 */
    public record RunResult(int exitCode, String stderr, boolean timedOut) {
        public boolean ok() { return !timedOut && exitCode == 0; }
    }

    // ==================== 定位 ====================

    /** Python: find_ffmpeg_exe。 */
    public static String ffmpegExe() {
        if (cachedFfmpeg != null) return cachedFfmpeg;
        String exe = findOnPath(isWindows() ? "ffmpeg.exe" : "ffmpeg");
        if (exe == null) exe = customCandidate("ffmpeg");
        if (exe == null) {
            Path bundled = AppPaths.appRoot().resolve("bin").resolve(isWindows() ? "ffmpeg.exe" : "ffmpeg");
            if (Files.isRegularFile(bundled)) exe = bundled.toString();
        }
        if (exe == null) exe = "ffmpeg";
        cachedFfmpeg = exe;
        return exe;
    }

    /** Python: find_ffprobe_exe（自定义位置同目录 → ffmpeg 同目录）。 */
    public static String ffprobeExe() {
        if (cachedFfprobe != null) return cachedFfprobe;
        String name = isWindows() ? "ffprobe.exe" : "ffprobe";
        String exe = findOnPath(name);
        if (exe == null) exe = customCandidate("ffprobe");
        if (exe == null) {
            Path bundled = AppPaths.appRoot().resolve("bin").resolve(name);
            if (Files.isRegularFile(bundled)) exe = bundled.toString();
        }
        if (exe == null) {
            String ff = ffmpegExe();
            if (!"ffmpeg".equals(ff)) {
                Path sibling = Path.of(ff).getParent();
                if (sibling != null && Files.isRegularFile(sibling.resolve(name))) {
                    exe = sibling.resolve(name).toString();
                }
            }
        }
        if (exe == null) exe = isWindows() ? "ffprobe.exe" : "ffprobe";
        cachedFfprobe = exe;
        return exe;
    }

    /** 清缓存（改设置后调用）。 */
    public static void resetCache() {
        cachedFfmpeg = null;
        cachedFfprobe = null;
    }

    /** 是否找到可用的 ffmpeg（供 UI 提示）。 */
    public static boolean available() {
        String exe = ffmpegExe();
        return !"ffmpeg".equals(exe) && Files.isRegularFile(Path.of(exe));
    }

    private static String customCandidate(String base) {
        String configured = Settings.load().ffmpegPath();
        if (configured == null || configured.isBlank()) return null;
        Path p = Path.of(configured);
        if (Files.isDirectory(p)) {
            Path candidate = p.resolve(isWindows() ? base + ".exe" : base);
            return Files.isRegularFile(candidate) ? candidate.toString() : null;
        }
        if (Files.isRegularFile(p)) {
            if (base.equals("ffmpeg")) return p.toString();
            Path sibling = p.getParent() == null ? null : p.getParent().resolve(isWindows() ? base + ".exe" : base);
            return sibling != null && Files.isRegularFile(sibling) ? sibling.toString() : null;
        }
        return null;
    }

    private static String findOnPath(String name) {
        String path = System.getenv("PATH");
        if (path == null) return null;
        for (String dir : path.split(java.io.File.pathSeparator)) {
            if (dir.isBlank()) continue;
            try {
                Path candidate = Path.of(dir).resolve(name);
                if (Files.isRegularFile(candidate)) return candidate.toString();
            } catch (Exception ignored) {
                // 非法 PATH 段跳过
            }
        }
        return null;
    }

    static boolean isWindows() {
        return System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT).contains("win");
    }

    // ==================== 执行 ====================

    /**
     * 执行 ffmpeg 命令（Python: subprocess.Popen + communicate(timeout)）。
     * 超时会强杀子进程，返回 timedOut=true。
     */
    public static RunResult run(List<String> command, int timeoutSeconds) {
        try {
            ProcessBuilder pb = new ProcessBuilder(command);
            pb.redirectErrorStream(true);
            Process p = pb.start();
            byte[] output;
            boolean finished = p.waitFor(timeoutSeconds, TimeUnit.SECONDS);
            if (!finished) {
                p.destroyForcibly();
                p.waitFor(5, TimeUnit.SECONDS);
                return new RunResult(-1, "", true);
            }
            output = p.getInputStream().readAllBytes();
            String text = new String(output, StandardCharsets.UTF_8);
            return new RunResult(p.exitValue(), text, false);
        } catch (IOException e) {
            // ffmpeg 不存在时 ProcessBuilder 抛 IOException
            return new RunResult(-2, "ffmpeg 未找到: " + e.getMessage(), false);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new RunResult(-3, "执行被中断", true);
        }
    }

    /** Python: classify_ffmpeg_error —— 返回 {错误类型, 详情}。 */
    public static String[] classifyError(String stderr) {
        String err = stderr == null ? "" : stderr.toLowerCase(java.util.Locale.ROOT);
        if (stderr == null || stderr.isBlank()) {
            return new String[] {"未知错误", "ffmpeg 没有输出错误信息，可能是被强制终止"};
        }
        if (err.contains("invalid argument") && err.contains("muxer")) {
            return new String[] {"输出格式错误", "ffmpeg 无法识别输出格式"};
        }
        if (err.contains("no such file or directory")) {
            return new String[] {"文件不存在", "输入或输出路径找不到文件"};
        }
        if (err.contains("permission denied")) {
            return new String[] {"权限不足", "无法写入输出文件，可能被占用"};
        }
        if (err.contains("invalid data found when processing input")) {
            return new String[] {"输入文件损坏", "视频文件数据损坏，可能录制不完整"};
        }
        if (err.contains("could not find codec parameters")) {
            return new String[] {"无法识别编码", "ffmpeg 无法识别视频编码格式"};
        }
        if (err.contains("stream map") && err.contains("matches no streams")) {
            return new String[] {"缺少音视频流", "文件中没有找到视频或音频流"};
        }
        if (err.contains("aac_adtstoasc") || err.contains("bitstream filter")) {
            return new String[] {"音频格式不兼容", "音频比特流过滤器失败"};
        }
        if (err.contains("non-monotonous dts") || err.contains("invalid dts")) {
            return new String[] {"时间戳异常", "视频时间戳错乱，常见于直播录屏"};
        }
        String[] lines = stderr.split("\\R");
        List<String> tail = new ArrayList<>();
        for (int i = lines.length - 1; i >= 0 && tail.size() < 3; i--) {
            if (!lines[i].isBlank()) tail.add(0, "      " + lines[i].strip());
        }
        return new String[] {"转换失败", String.join("\n", tail)};
    }

    /** Python: preserve_timestamps —— 复制修改/访问时间（创建时间平台受限）。 */
    public static boolean preserveTimestamps(Path src, Path dst) {
        try {
            var attrs = Files.readAttributes(src, java.nio.file.attribute.BasicFileAttributes.class);
            Files.setLastModifiedTime(dst, attrs.lastModifiedTime());
            try {
                Files.setAttribute(dst, "basic:lastAccessTime", attrs.lastAccessTime());
            } catch (Exception ignored) {
                // 平台不支持写访问时间
            }
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** 便捷：FileTime 转换。 */
    public static FileTime now() { return FileTime.fromMillis(System.currentTimeMillis()); }
}
