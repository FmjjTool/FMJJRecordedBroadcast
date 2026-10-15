package cn.fmjj.toolbox.engine.organize;

import cn.fmjj.toolbox.core.util.FileNameUtils;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 自动整理引擎：按文件名日期把文件归档进 YYYYMMDD 文件夹。
 * 移植自 Python 版 toolbox/organize_engine.py。
 */
public final class OrganizeEngine {

    /** 扫描到的文件。 */
    public record ScannedFile(String name, Path path, long size, String ext) {}

    /** 扫描结果：按日期分组的表 + 无日期文件名单。 */
    public record ScanResult(LinkedHashMap<String, List<ScannedFile>> groups, List<String> noDateFiles) {}

    /** 执行结果。 */
    public record OrganizeResult(int success, int fail, List<String> failedFiles) {}

    private OrganizeEngine() {}

    /** Python: scan_files —— 仅扫当前层，跳过目录/隐藏文件/.py/.json/.log。 */
    public static ScanResult scan(Path root) throws IOException {
        LinkedHashMap<String, List<ScannedFile>> groups = new LinkedHashMap<>();
        List<String> noDate = new ArrayList<>();
        if (!Files.isDirectory(root)) return new ScanResult(groups, noDate);

        List<Path> entries;
        try (var stream = Files.list(root)) {
            entries = stream.toList();
        }
        for (Path full : entries) {
            String name = full.getFileName().toString();
            if (!Files.isRegularFile(full)) continue;
            if (name.startsWith(".")) continue;
            if (name.endsWith(".py") || name.endsWith(".json") || name.endsWith(".log")) continue;

            // Python: ext = os.path.splitext(name)[1].lower()（最后一个点；前导点不算扩展名）
            int dot = name.lastIndexOf('.');
            String extPart = dot > 0 ? name.substring(dot).toLowerCase() : "";
            String date = FileNameUtils.extractDateFromFilename(name);
            if (date != null) {
                groups.computeIfAbsent(date, k -> new ArrayList<>()).add(
                        new ScannedFile(name, full, Files.size(full), extPart));
            } else {
                noDate.add(name);
            }
        }
        // Python: dict(sorted(groups.items())) —— 按日期键排序
        LinkedHashMap<String, List<ScannedFile>> sorted = new LinkedHashMap<>();
        new java.util.TreeMap<>(groups).forEach(sorted::put);
        return new ScanResult(sorted, noDate);
    }

    /** Python: execute_organize —— os.rename 语义；目标已存在则自动追加 _N。 */
    public static OrganizeResult execute(Path root, Map<String, List<ScannedFile>> groups) {
        int success = 0, fail = 0;
        List<String> failed = new ArrayList<>();

        for (Map.Entry<String, List<ScannedFile>> entry : groups.entrySet()) {
            Path folder = root.resolve(entry.getKey());
            try {
                Files.createDirectories(folder);
            } catch (IOException e) {
                fail += entry.getValue().size();
                entry.getValue().forEach(f -> failed.add(f.name()));
                continue;
            }
            for (ScannedFile f : entry.getValue()) {
                Path src = f.path();
                Path dst = folder.resolve(f.name());
                try {
                    if (Files.exists(dst)) {
                        String base = FileNameUtils.stripExtension(f.name());
                        String ext = base.equals(f.name()) ? "" : f.name().substring(base.length());
                        int counter = 1;
                        while (Files.exists(dst)) {
                            dst = folder.resolve(base + "_" + counter + ext);
                            counter++;
                        }
                    }
                    Files.move(src, dst, StandardCopyOption.REPLACE_EXISTING);
                    success++;
                } catch (IOException e) {
                    fail++;
                    failed.add(f.name());
                }
            }
        }
        return new OrganizeResult(success, fail, failed);
    }
}
