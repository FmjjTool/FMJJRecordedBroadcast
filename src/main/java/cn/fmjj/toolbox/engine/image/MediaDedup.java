package cn.fmjj.toolbox.engine.image;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;

/**
 * 媒体筛选：按内容 MD5 找重复文件（对应 Python 版 image_sorter）。
 *
 * <p>两阶段：先按文件大小分组（大小唯一的一定不重复，省掉哈希开销），
 * 再对同尺寸文件算 MD5。
 */
public final class MediaDedup {

    /** 重复组：同一 MD5 的多个文件。 */
    public record DuplicateGroup(String md5, long size, List<Path> files) {}

    /** 扫描结果。 */
    public record Result(int scanned, List<DuplicateGroup> groups) {
        public int duplicateCount() {
            return groups.stream().mapToInt(g -> g.files().size() - 1).sum();
        }

        public long wastedBytes() {
            return groups.stream().mapToLong(g -> g.size() * (g.files().size() - 1)).sum();
        }
    }

    private MediaDedup() {}

    /**
     * 扫描目录找重复。
     *
     * @param exts 扩展名过滤（含点，小写）；空集合 = 全部文件
     */
    public static Result scan(Path root, Set<String> exts) throws IOException {
        if (!Files.isDirectory(root)) return new Result(0, List.of());

        Map<Long, List<Path>> bySize = new HashMap<>();
        int scanned = 0;
        try (Stream<Path> s = Files.walk(root)) {
            for (Path p : s.filter(Files::isRegularFile).toList()) {
                if (!exts.isEmpty() && !exts.contains(extOf(p))) continue;
                scanned++;
                bySize.computeIfAbsent(sizeOf(p), k -> new ArrayList<>()).add(p);
            }
        }

        List<DuplicateGroup> groups = new ArrayList<>();
        for (var entry : bySize.entrySet()) {
            if (entry.getValue().size() < 2) continue;
            Map<String, List<Path>> byHash = new HashMap<>();
            for (Path p : entry.getValue()) {
                String md5 = md5(p);
                if (md5.isEmpty()) continue;
                byHash.computeIfAbsent(md5, k -> new ArrayList<>()).add(p);
            }
            for (var h : byHash.entrySet()) {
                if (h.getValue().size() > 1) {
                    h.getValue().sort(java.util.Comparator.comparing(Path::toString));
                    groups.add(new DuplicateGroup(h.getKey(), entry.getKey(), h.getValue()));
                }
            }
        }
        groups.sort((a, b) -> Long.compare(b.size() * (b.files().size() - 1),
                a.size() * (a.files().size() - 1)));
        return new Result(scanned, groups);
    }

    /** 文件 MD5（十六进制小写）；失败返回空串。 */
    public static String md5(Path file) {
        try (InputStream in = Files.newInputStream(file)) {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
            StringBuilder sb = new StringBuilder();
            for (byte b : md.digest()) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** 字符串的 MD5（测试用）。 */
    public static String md5OfText(String text) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            StringBuilder sb = new StringBuilder();
            for (byte b : md.digest(text.getBytes(java.nio.charset.StandardCharsets.UTF_8))) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private static long sizeOf(Path p) {
        try {
            return Files.size(p);
        } catch (IOException e) {
            return -1;
        }
    }

    private static String extOf(Path p) {
        String n = p.getFileName().toString().toLowerCase(Locale.ROOT);
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot) : "";
    }
}
