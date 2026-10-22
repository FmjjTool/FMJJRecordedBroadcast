package cn.fmjj.toolbox.engine.asset;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Stream;

/**
 * 资产扫描：统计工作目录下各日期文件夹的媒体数量与体积。
 * 对应 Python 版 gui_qt/pages/asset.py 的列表口径（图片/视频/音频/大小）。
 *
 * 纯本地、无网络；不读 json 原始数据（原版「原始数据」列依赖 asset_index 缓存，随 M6 后续）。
 */
public final class AssetScanner {

    public static final Set<String> IMAGE_EXTS = Set.of(
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".avif");
    public static final Set<String> VIDEO_EXTS = Set.of(
            ".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".rmvb", ".webm", ".m4v");
    public static final Set<String> AUDIO_EXTS = Set.of(
            ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".m4b", ".opus", ".amr");

    /** 单个文件夹统计。 */
    public record FolderStat(Path dir, String name, int images, int videos, int audios,
                             long bytes, long lastModifiedMillis) {
        public int totalFiles() { return images + videos + audios; }
    }

    /** 汇总。 */
    public record Totals(int folders, int images, int videos, int audios, long bytes) {}

    private AssetScanner() {}

    /** 扫描根目录：每个日期文件夹（YYYYMMDD）+ 根目录散落文件（名称记为「根目录」）。 */
    public static List<FolderStat> scan(Path root) {
        List<FolderStat> stats = new ArrayList<>();
        if (!Files.isDirectory(root)) return stats;

        List<Path> dirs = new ArrayList<>();
        List<Path> rootFiles = new ArrayList<>();
        try (Stream<Path> s = Files.list(root)) {
            for (Path p : s.toList()) {
                if (Files.isDirectory(p) && p.getFileName().toString().matches("^20\\d{6}$")) dirs.add(p);
                else if (Files.isRegularFile(p)) rootFiles.add(p);
            }
        } catch (IOException e) {
            return stats;
        }

        dirs.sort(Comparator.comparing(p -> p.getFileName().toString()));
        for (Path dir : dirs) {
            stats.add(statOf(dir, dir.getFileName().toString()));
        }
        if (!rootFiles.isEmpty()) {
            stats.add(statOf(rootFiles, "根目录"));
        }
        return stats;
    }

    private static FolderStat statOf(Path dir, String name) {
        int images = 0, videos = 0, audios = 0;
        long bytes = 0;
        long lastModified = 0;
        try (Stream<Path> s = Files.walk(dir)) {
            for (Path p : s.filter(Files::isRegularFile).toList()) {
                bytes += sizeOf(p);
                lastModified = Math.max(lastModified, mtimeOf(p));
                String ext = extOf(p);
                if (IMAGE_EXTS.contains(ext)) images++;
                else if (VIDEO_EXTS.contains(ext)) videos++;
                else if (AUDIO_EXTS.contains(ext)) audios++;
            }
        } catch (IOException ignored) {
        }
        return new FolderStat(dir, name, images, videos, audios, bytes, lastModified);
    }

    private static FolderStat statOf(List<Path> files, String name) {
        int images = 0, videos = 0, audios = 0;
        long bytes = 0, lastModified = 0;
        for (Path p : files) {
            bytes += sizeOf(p);
            lastModified = Math.max(lastModified, mtimeOf(p));
            String ext = extOf(p);
            if (IMAGE_EXTS.contains(ext)) images++;
            else if (VIDEO_EXTS.contains(ext)) videos++;
            else if (AUDIO_EXTS.contains(ext)) audios++;
        }
        Path base = files.isEmpty() ? Path.of(".") : files.get(0).getParent();
        return new FolderStat(base, name, images, videos, audios, bytes, lastModified);
    }

    /** 汇总全部行。 */
    public static Totals totals(List<FolderStat> stats) {
        int images = 0, videos = 0, audios = 0;
        long bytes = 0;
        for (FolderStat s : stats) {
            images += s.images();
            videos += s.videos();
            audios += s.audios();
            bytes += s.bytes();
        }
        return new Totals(stats.size(), images, videos, audios, bytes);
    }

    static String extOf(Path p) {
        String n = p.getFileName().toString();
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot).toLowerCase(Locale.ROOT) : "";
    }

    private static long sizeOf(Path p) {
        try {
            return Files.size(p);
        } catch (IOException e) {
            return 0;
        }
    }

    private static long mtimeOf(Path p) {
        try {
            return Files.getLastModifiedTime(p).toMillis();
        } catch (IOException e) {
            return 0;
        }
    }
}
