package cn.fmjj.toolbox.engine.media;

import java.awt.Desktop;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * 音频提取引擎。
 * 移植自 Python 版 toolbox/audio_engine.py：无损提取音频（-acodec copy）、
 * 按日期分组扫描、安全删除（回收站）。
 */
public final class AudioEngine {

    public static final List<String> AUDIO_EXTS = List.of(
            ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".m4b", ".opus", ".amr");

    /** 与 Python utils.VIDEO_EXTS 一致。 */
    public static final List<String> VIDEO_EXTS = List.of(
            ".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".rmvb", ".webm", ".ogg");

    private static final Pattern DATE_FOLDER = Pattern.compile("^20\\d{6}$");

    private AudioEngine() {}

    /** 提取命令（纯函数，便于断言）。 */
    public static List<String> extractCommand(Path src, Path dst) {
        return List.of(Ffmpeg.ffmpegExe(), "-i", src.toString(),
                "-vn", "-acodec", "copy", "-y", "-hide_banner", dst.toString());
    }

    /** 提取结果。 */
    public record ExtractResult(boolean success, String error) {}

    /** Python: extract_audio_one —— 300 秒超时；成功要求目标文件非空。 */
    public static ExtractResult extractAudioOne(Path src, Path dst) {
        var r = Ffmpeg.run(extractCommand(src, dst), 300);
        if (r.ok()) {
            try {
                if (Files.isRegularFile(dst) && Files.size(dst) > 0) return new ExtractResult(true, "");
            } catch (IOException ignored) {
            }
        }
        if (r.timedOut()) return new ExtractResult(false, "提取超时");
        String stderr = r.stderr() == null ? "" : r.stderr();
        return new ExtractResult(false, stderr.length() > 300 ? stderr.substring(stderr.length() - 300) : stderr);
    }

    /** Python: find_audio_videos —— 递归找视频（用于批量提取）。 */
    public static List<Path> findAudioVideos(Path root) {
        List<Path> out = new ArrayList<>();
        try (var stream = Files.walk(root)) {
            stream.filter(Files::isRegularFile).forEach(p -> {
                String ext = extOf(p);
                if (VIDEO_EXTS.contains(ext)) out.add(p);
            });
        } catch (IOException ignored) {
        }
        out.sort(null);
        return out;
    }

    /**
     * Python: scan_audio_by_date —— 根目录音频归 "根目录"，YYYYMMDD 子文件夹按日期分组，
     * 日期升序。
     */
    public static LinkedHashMap<String, List<Path>> scanAudioByDate(Path root) {
        LinkedHashMap<String, List<Path>> groups = new LinkedHashMap<>();
        if (!Files.isDirectory(root)) return groups;

        List<Path> rootAudios = new ArrayList<>();
        List<Path> dirs = new ArrayList<>();
        try (var s = Files.list(root)) {
            for (Path p : s.toList()) {
                if (Files.isRegularFile(p) && AUDIO_EXTS.contains(extOf(p))) rootAudios.add(p);
                else if (Files.isDirectory(p) && DATE_FOLDER.matcher(p.getFileName().toString()).matches()) dirs.add(p);
            }
        } catch (IOException e) {
            return groups;
        }
        rootAudios.sort(null);
        if (!rootAudios.isEmpty()) groups.put("根目录", rootAudios);

        dirs.sort(null);
        for (Path d : dirs) {
            List<Path> audios = new ArrayList<>();
            try (var s = Files.walk(d)) {
                s.filter(Files::isRegularFile).forEach(p -> {
                    if (AUDIO_EXTS.contains(extOf(p))) audios.add(p);
                });
            } catch (IOException ignored) {
            }
            audios.sort(null);
            if (!audios.isEmpty()) groups.put(d.getFileName().toString(), audios);
        }
        return groups;
    }

    /** 删除结果。 */
    public record DeleteResult(int ok, List<String> failed) {}

    /**
     * Python: delete_files_safe —— 优先回收站（Java: Desktop.moveToTrash），
     * 不可用时退化为永久删除；以「原路径是否还存在」作为最终判定。
     */
    public static DeleteResult deleteFilesSafe(List<Path> paths) {
        int ok = 0;
        List<String> failed = new ArrayList<>();
        Desktop desktop = Desktop.isDesktopSupported() ? Desktop.getDesktop() : null;
        boolean canTrash = desktop != null && desktop.isSupported(Desktop.Action.MOVE_TO_TRASH);

        for (Path p : paths) {
            try {
                if (canTrash) {
                    if (!desktop.moveToTrash(p.toFile())) throw new IOException("移入回收站失败");
                } else {
                    Files.delete(p);
                }
                ok++;
            } catch (Exception e) {
                if (!Files.exists(p)) ok++;      // 误报：文件实际已删除
                else failed.add(p.getFileName() + ": " + e.getMessage());
            }
        }
        return new DeleteResult(ok, failed);
    }

    static String extOf(Path p) {
        String name = p.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(dot).toLowerCase(Locale.ROOT) : "";
    }

    /** 是否为视频（供页面筛选）。 */
    public static boolean isVideo(Path p) {
        return VIDEO_EXTS.contains(extOf(p));
    }

    /** 目标音频文件路径（同名 .m4a，与 Python 页面逻辑一致）。 */
    public static Path audioTarget(Path video) {
        String name = video.getFileName().toString();
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        Path parent = video.getParent();
        String target = base + ".m4a";
        return parent == null ? Path.of(target) : parent.resolve(target);
    }

    /** 文件大小格式化（页面展示用）。 */
    public static String formatSize(long bytes) {
        if (bytes >= 1024L * 1024 * 1024) return String.format("%.2f GB", bytes / 1024.0 / 1024 / 1024);
        if (bytes >= 1024L * 1024) return String.format("%.1f MB", bytes / 1024.0 / 1024);
        if (bytes >= 1024) return String.format("%.0f KB", bytes / 1024.0);
        return bytes + " B";
    }

    /** 供页面：文件可读大小（失败返回 0）。 */
    public static long sizeOf(Path p) {
        try {
            return Files.size(p);
        } catch (IOException e) {
            return 0;
        }
    }

    /** 供页面：Desktop 是否支持回收站。 */
    public static boolean trashSupported() {
        Desktop d = Desktop.isDesktopSupported() ? Desktop.getDesktop() : null;
        return d != null && d.isSupported(Desktop.Action.MOVE_TO_TRASH);
    }

    /** 打开文件夹（页面「打开目录」按钮）。 */
    public static void openInExplorer(Path dir) {
        try {
            if (Desktop.isDesktopSupported()) Desktop.getDesktop().open(new File(dir.toString()));
        } catch (Exception ignored) {
        }
    }
}
