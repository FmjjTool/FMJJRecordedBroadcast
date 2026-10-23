package cn.fmjj.toolbox.engine.diagnostics;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.media.Ffmpeg;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinBrowser;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 环境自检（对应 Python 版 check_deps）。
 * 打包分发前/用户遇到功能异常时，一次性看清依赖是否齐全。
 */
public final class EnvironmentCheck {

    public enum Status { OK, WARN, FAIL }

    public record Item(String name, Status status, String detail) {
        public String icon() {
            return switch (status) {
                case OK -> "✅";
                case WARN -> "⚠️";
                case FAIL -> "❌";
            };
        }
    }

    private EnvironmentCheck() {}

    /** 全部检查（按重要性排序）。 */
    public static List<Item> run() {
        List<Item> items = new ArrayList<>();
        items.add(checkJava());
        items.add(checkFfmpeg());
        items.add(checkFfprobe());
        items.add(checkBrowser());
        items.add(checkDirs());
        items.add(checkDisk());
        items.add(checkCookies());
        return items;
    }

    public static Item checkJava() {
        String v = System.getProperty("java.version", "?");
        int major = parseMajor(v);
        Status s = major >= 21 ? Status.OK : Status.WARN;
        return new Item("Java 运行时", s, v + (major >= 21 ? "（满足 ≥21）" : "（建议 ≥21）"));
    }

    static int parseMajor(String v) {
        try {
            String head = v.split("\\.")[0];
            return Integer.parseInt(head.replaceAll("[^0-9]", ""));
        } catch (Exception e) {
            return 0;
        }
    }

    public static Item checkFfmpeg() {
        if (!Ffmpeg.available()) {
            return new Item("FFmpeg", Status.FAIL,
                    "未找到。视频转换/音频提取/录播会不可用；可在设置里指定路径或装到 PATH");
        }
        String ver = Ffmpeg.version();
        return new Item("FFmpeg", Status.OK, ver.isEmpty() ? Ffmpeg.ffmpegExe() : ver);
    }

    public static Item checkFfprobe() {
        String ff = Ffmpeg.ffmpegExe();
        if (ff == null || ff.isEmpty()) return new Item("FFprobe", Status.WARN, "FFmpeg 未就绪，跳过");
        Path probe = Path.of(ff).resolveSibling("ffprobe.exe");
        if (Files.isRegularFile(probe)) return new Item("FFprobe", Status.OK, probe.toString());
        return new Item("FFprobe", Status.WARN, "同目录未找到 ffprobe（部分探测功能受限）");
    }

    public static Item checkBrowser() {
        String b = DouyinBrowser.findBrowser();
        if (b == null || b.isEmpty()) {
            return new Item("浏览器（抖音解析）", Status.WARN,
                    "未找到 Edge/Chrome；抖音解析需要浏览器渲染，其他平台不受影响");
        }
        return new Item("浏览器（抖音解析）", Status.OK, b);
    }

    public static Item checkDirs() {
        try {
            Path root = AppPaths.appRoot();
            Path cfg = AppPaths.configDir();
            Path dl = AppPaths.downloadDir();
            Files.createDirectories(cfg);
            Files.createDirectories(dl);
            return new Item("数据目录", Status.OK,
                    root + "（config / download / logs 可写）");
        } catch (Exception e) {
            return new Item("数据目录", Status.FAIL, "不可写：" + e.getMessage());
        }
    }

    public static Item checkDisk() {
        try {
            var store = Files.getFileStore(AppPaths.appRoot());
            long usable = store.getUsableSpace();
            long total = store.getTotalSpace();
            long gb = 1024L * 1024 * 1024;
            Status s = usable < 2 * gb ? Status.WARN : Status.OK;
            return new Item("磁盘空间", s,
                    String.format("可用 %.1f GB / 共 %.1f GB", usable / (double) gb, total / (double) gb));
        } catch (Exception e) {
            return new Item("磁盘空间", Status.WARN, "无法读取：" + e.getMessage());
        }
    }

    public static Item checkCookies() {
        Path f = AppPaths.configDir().resolve("cookies.json");
        if (!Files.isRegularFile(f)) {
            return new Item("登录态", Status.WARN, "还没有 cookies.json（可在 Cookie 管理里扫码登录）");
        }
        try {
            Map<String, String> all = CookieStore.loadCookies();
            long valid = all.values().stream().filter(v -> v != null && !v.isEmpty()).count();
            Status s = valid == 0 ? Status.WARN : Status.OK;
            return new Item("登录态", s, valid + " 个平台已登录（共 " + all.size() + " 条记录）");
        } catch (Exception e) {
            return new Item("登录态", Status.WARN, "读取失败：" + e.getMessage());
        }
    }

    /** 汇总：有一项 FAIL 即不通过。 */
    public static boolean allOk(List<Item> items) {
        return items.stream().noneMatch(i -> i.status() == Status.FAIL);
    }
}
