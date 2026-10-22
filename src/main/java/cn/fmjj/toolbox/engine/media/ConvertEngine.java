package cn.fmjj.toolbox.engine.media;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * 视频转换引擎（ffmpeg）：无损封装优先 → 可选转码兜底 → 失败重试 → 缓存。
 * 逐行移植自 Python 版 toolbox/convert_engine.py。
 */
public final class ConvertEngine {

    /** 转换配置（对应 Python 的 convert config dict 子集）。 */
    public record ConvertConfig(String outputExtension, String outputDir, boolean deleteSourceAfterSuccess,
                                int retryCount, int retryDelaySeconds, boolean enableEncodeFallback,
                                String encodeVideoCodec, int encodeCrf, String encodePreset,
                                boolean encodeAudioCopy, boolean preserveMetadata, boolean preserveTimestamps,
                                List<String> inputExtensions) {

        public static ConvertConfig defaults() {
            return new ConvertConfig(".mp4", "", false, 2, 1, false,
                    "libx264", 18, "fast", true, true, true,
                    List.of(".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".rmvb"));
        }
    }

    /** 单文件结果（mode: lossless / encode / skip / failed / interrupted）。 */
    public record ConvertResult(Path src, Path dst, Path rel, boolean success, String mode,
                                String errorType, String errorDetail, boolean interrupted) {}

    private ConvertEngine() {}

    // ==================== 缓存 ====================

    /** 缓存项。 */
    public record CacheEntry(long size, double mtime, boolean success, String output, double timestamp) {}

    /** Python: is_convert_cached —— 大小一致且 mtime 差 <1 秒才算命中。 */
    public static boolean isCached(Map<String, CacheEntry> cache, Path src) {
        String key = src.toAbsolutePath().normalize().toString();
        CacheEntry entry = cache.get(key);
        if (entry == null || !entry.success()) return false;
        try {
            long size = Files.size(src);
            double mtime = Files.getLastModifiedTime(src).toMillis() / 1000.0;
            return entry.size() == size && Math.abs(entry.mtime() - mtime) < 1;
        } catch (IOException e) {
            return false;
        }
    }

    /** Python: update_convert_cache。 */
    public static void updateCache(Map<String, CacheEntry> cache, Path src, boolean success, Path output) {
        String key = src.toAbsolutePath().normalize().toString();
        try {
            cache.put(key, new CacheEntry(Files.size(src),
                    Files.getLastModifiedTime(src).toMillis() / 1000.0, success,
                    output == null ? "" : output.toAbsolutePath().normalize().toString(),
                    System.currentTimeMillis() / 1000.0));
        } catch (IOException ignored) {
            // 源文件不可读时不写缓存（与 Python 的 try/except 一致）
        }
    }

    // ==================== 命令构造（纯函数，便于断言） ====================

    /** Python: lossless_mux 的 argv。 */
    public static List<String> losslessMuxCommand(Path src, Path dst, ConvertConfig cfg) {
        List<String> cmd = new ArrayList<>(List.of(Ffmpeg.ffmpegExe(), "-i", src.toString()));
        cmd.addAll(List.of("-map", "0:v:0", "-map", "0:a:0"));
        if (cfg.preserveMetadata()) {
            cmd.addAll(List.of("-map_metadata", "0", "-map_chapters", "0"));
        }
        cmd.addAll(List.of("-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart", "-f", "mp4", "-y", "-hide_banner", dst.toString()));
        return cmd;
    }

    /** Python: encode_video 的 argv。 */
    public static List<String> encodeCommand(Path src, Path dst, ConvertConfig cfg) {
        List<String> cmd = new ArrayList<>(List.of(Ffmpeg.ffmpegExe(), "-i", src.toString()));
        cmd.addAll(List.of("-map", "0:v:0", "-map", "0:a:0"));
        if (cfg.preserveMetadata()) {
            cmd.addAll(List.of("-map_metadata", "0", "-map_chapters", "0"));
        }
        cmd.addAll(List.of("-c:v", cfg.encodeVideoCodec(), "-crf", String.valueOf(cfg.encodeCrf()),
                "-preset", cfg.encodePreset()));
        if (cfg.encodeAudioCopy()) cmd.addAll(List.of("-c:a", "copy"));
        else cmd.addAll(List.of("-c:a", "aac", "-b:a", "192k"));
        cmd.addAll(List.of("-movflags", "+faststart", "-y", "-hide_banner", dst.toString()));
        return cmd;
    }

    /** Python: get_output_path —— 有 output_dir 时保留相对目录结构。 */
    public static Path getOutputPath(Path src, Path root, ConvertConfig cfg) {
        String ext = cfg.outputExtension() == null || cfg.outputExtension().isBlank() ? ".mp4" : cfg.outputExtension();
        if (cfg.outputDir() != null && !cfg.outputDir().isBlank()) {
            Path rel = root.relativize(src);
            Path dst = Path.of(cfg.outputDir()).resolve(withSuffix(rel, ext));
            try {
                Files.createDirectories(dst.getParent());
            } catch (IOException ignored) {
            }
            return dst;
        }
        return withSuffix(src, ext);
    }

    static Path withSuffix(Path p, String ext) {
        String name = p.getFileName().toString();
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        Path parent = p.getParent();
        String newName = base + ext;
        return parent == null ? Path.of(newName) : parent.resolve(newName);
    }

    // ==================== 转换执行 ====================

    /** Python: convert_one_file。 */
    public static ConvertResult convertOne(Path src, Path root, ConvertConfig cfg,
                                           Consumer<String> progress, BooleanSupplier shouldStop) {
        Path dst = getOutputPath(src, root, cfg);
        Path tmp = dst.getParent().resolve(stemOf(dst) + ".tmp.mp4");
        Path rel = root.relativize(src);

        Consumer<String> log = progress == null ? s -> {} : progress;

        if (Files.exists(dst)) {
            log.accept("📂 " + rel + "\n✅ 已存在，跳过");
            return new ConvertResult(src, dst, rel, true, "skip", "", "", false);
        }
        try {
            Files.deleteIfExists(tmp);
        } catch (IOException ignored) {
        }
        log.accept("📂 " + rel);

        int maxAttempts = cfg.retryCount() + 1;
        String errType = "", errDetail = "";
        boolean interrupted = false;

        for (int attempt = 0; attempt < maxAttempts; attempt++) {
            if (shouldStop != null && shouldStop.getAsBoolean()) {
                interrupted = true;
                break;
            }
            if (attempt > 0) {
                log.accept("   🔄 第 " + attempt + " 次重试...");
                try {
                    Thread.sleep(cfg.retryDelaySeconds() * 1000L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    interrupted = true;
                    break;
                }
            }
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException ignored) {
            }

            var lossless = Ffmpeg.run(losslessMuxCommand(src, tmp, cfg), 600);
            if (shouldStop != null && shouldStop.getAsBoolean()) {
                interrupted = true;
                break;
            }
            if (lossless.ok() && fileNonEmpty(tmp)) {
                if (finish(tmp, dst, src, cfg, "lossless", log)) {
                    return new ConvertResult(src, dst, rel, true, "lossless", "", "", false);
                }
                continue;
            }
            if (lossless.timedOut()) {
                errType = "超时";
                errDetail = "转换超时（超过 10 分钟）";
            } else {
                String[] classified = Ffmpeg.classifyError(lossless.stderr());
                errType = classified[0];
                errDetail = classified[1];
            }

            if (cfg.enableEncodeFallback() && (shouldStop == null || !shouldStop.getAsBoolean())) {
                log.accept("   ⚠️ 无损失败，转码中...");
                try {
                    Files.deleteIfExists(tmp);
                } catch (IOException ignored) {
                }
                var encoded = Ffmpeg.run(encodeCommand(src, tmp, cfg), 3600);
                if (shouldStop != null && shouldStop.getAsBoolean()) {
                    interrupted = true;
                    break;
                }
                if (encoded.ok() && fileNonEmpty(tmp)) {
                    if (finish(tmp, dst, src, cfg, "encode", log)) {
                        return new ConvertResult(src, dst, rel, true, "encode", "", "", false);
                    }
                    continue;
                }
                if (encoded.timedOut()) {
                    errType = "超时";
                    errDetail = "转码超时（超过 1 小时）";
                } else {
                    errType = "转码失败";
                    errDetail = tail(encoded.stderr(), 500);
                }
            }
        }

        try {
            Files.deleteIfExists(tmp);
        } catch (IOException ignored) {
        }
        if (interrupted) {
            log.accept("⏸️ 已中止");
            return new ConvertResult(src, dst, rel, false, "interrupted", errType, errDetail, true);
        }
        log.accept("❌ 失败 [" + errType + "]");
        return new ConvertResult(src, dst, rel, false, "failed", errType, errDetail, false);
    }

    /** 临时文件就位 → 目标：改名 + 保留时间戳 + 可选删源。 */
    private static boolean finish(Path tmp, Path dst, Path src, ConvertConfig cfg, String mode,
                                  Consumer<String> log) {
        try {
            Files.move(tmp, dst, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException e) {
            return false;
        }
        if (cfg.preserveTimestamps()) Ffmpeg.preserveTimestamps(src, dst);
        if (cfg.deleteSourceAfterSuccess()) {
            try {
                Files.deleteIfExists(src);
            } catch (IOException ignored) {
            }
        }
        log.accept("lossless".equals(mode) ? "✅ 无损完成" : "✅ 转码完成");
        return true;
    }

    private static boolean fileNonEmpty(Path p) {
        try {
            return Files.isRegularFile(p) && Files.size(p) > 0;
        } catch (IOException e) {
            return false;
        }
    }

    private static String stemOf(Path p) {
        String name = p.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }

    private static String tail(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(s.length() - max) : s;
    }

    /** Python: find_videos —— 按 input_extensions 递归查找。 */
    public static List<Path> findVideos(Path root, ConvertConfig cfg) {
        List<String> exts = cfg.inputExtensions().stream()
                .map(e -> e.toLowerCase(Locale.ROOT)).toList();
        List<Path> out = new ArrayList<>();
        try (var stream = Files.walk(root)) {
            stream.filter(Files::isRegularFile).forEach(p -> {
                String name = p.getFileName().toString();
                int dot = name.lastIndexOf('.');
                if (dot > 0 && exts.contains(name.substring(dot).toLowerCase(Locale.ROOT))) out.add(p);
            });
        } catch (IOException ignored) {
        }
        out.sort(null);
        return out;
    }
}
