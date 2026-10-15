package cn.fmjj.toolbox.engine.filetime;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 文件时间修改引擎：批量改创建/修改/访问时间。
 * 移植自 Python 版 gui_qt/pages/file_time.py 的 parse_time_from_name / 偏移解析 / set_file_times。
 *
 * 已知 parity 差距（见 JAVA_PORT_PLAN.md 难点清单）：
 * Python 用 win32file/ctypes 设置 Windows 创建时间；Java NIO 在多数 JDK/平台上
 * 不支持写 creationTime——本引擎对 ctime 尽力而为（尝试 basic:creationTime，失败记入
 * {@link ApplyResult#ctimeUnsupported()}），不改 fdat/mtime 的正常路径。
 */
public final class FileTimeEngine {

    private static final Pattern NAME_TIME = Pattern.compile(
            "(20\\d{2})[-_.年/](\\d{1,2})[-_.月/](\\d{1,2})日?"
            + "(?:[-_ T](\\d{1,2})[-_:T时](\\d{1,2})(?:[-_:分](\\d{1,2}))?)?");
    private static final Pattern NAME_TIME_COMPACT = Pattern.compile(
            "(20\\d{2})(\\d{2})(\\d{2})[-_ ]?(\\d{2})?(\\d{2})?(\\d{2})?");
    private static final Pattern OFFSET = Pattern.compile("^([+-])(\\d+(?:\\.\\d+)?)([smhd])$");

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /** 修改方式。 */
    public enum Mode { SET, OFFSET, FROM_NAME }

    private FileTimeEngine() {}

    /** Python: parse_time_from_name —— 返回本地时间（无时间默认 12:00:00），解析不了返回 null。 */
    public static LocalDateTime parseTimeFromName(String name) {
        Matcher m = NAME_TIME.matcher(name);
        int y, mo, d, hh = 12, mi = 0, ss = 0;
        if (m.find()) {
            y = Integer.parseInt(m.group(1));
            mo = Integer.parseInt(m.group(2));
            d = Integer.parseInt(m.group(3));
            hh = m.group(4) != null ? Integer.parseInt(m.group(4)) : 12;
            mi = m.group(5) != null ? Integer.parseInt(m.group(5)) : 0;
            ss = m.group(6) != null ? Integer.parseInt(m.group(6)) : 0;
        } else {
            m = NAME_TIME_COMPACT.matcher(name);
            if (!m.find()) return null;
            y = Integer.parseInt(m.group(1));
            mo = Integer.parseInt(m.group(2));
            d = Integer.parseInt(m.group(3));
            hh = m.group(4) != null ? Integer.parseInt(m.group(4)) : 12;
            mi = m.group(5) != null ? Integer.parseInt(m.group(5)) : 0;
            ss = m.group(6) != null ? Integer.parseInt(m.group(6)) : 0;
        }
        try {
            return LocalDateTime.of(y, mo, d, hh, mi, ss);
        } catch (java.time.DateTimeException e) {
            return null;
        }
    }

    /** LocalDateTime → epoch 秒（系统时区，Python datetime.timestamp() 同语义）。 */
    public static double toEpoch(LocalDateTime dt) {
        return dt.atZone(ZoneId.systemDefault()).toEpochSecond();
    }

    /** 格式化 epoch 秒，失败返回 "?"。 */
    public static String formatEpoch(double seconds) {
        try {
            return LocalDateTime.ofInstant(
                    Instant.ofEpochMilli(Math.round(seconds * 1000)), ZoneId.systemDefault()).format(FMT);
        } catch (RuntimeException e) {
            return "?";
        }
    }

    /**
     * 解析偏移量文本（如 -210m / +2d / +0h / +1.5h）。
     * Python 消息：偏移格式错误（如 -3h30m 不支持混合，请用 -210m）
     */
    public static double parseOffsetSeconds(String text) {
        String txt = text == null ? "" : text.strip().toLowerCase();
        Matcher m = OFFSET.matcher(txt);
        if (!m.matches()) {
            throw new IllegalArgumentException("偏移格式错误（如 -3h30m 不支持混合，请用 -210m）");
        }
        int sign = "+".equals(m.group(1)) ? 1 : -1;
        double unit = switch (m.group(3)) {
            case "s" -> 1;
            case "m" -> 60;
            case "h" -> 3600;
            default -> 86400;
        };
        return sign * Double.parseDouble(m.group(2)) * unit;
    }

    /** 单文件目标时间；返回 null 表示 OK，非 null 为错误文本。 */
    public static String computeTarget(Path file, Mode mode, LocalDateTime specified, String offsetText) {
        try {
            switch (mode) {
                case SET -> {
                    if (specified == null) return "未指定时间";
                }
                case OFFSET -> parseOffsetSeconds(offsetText); // 只验格式
                case FROM_NAME -> {
                    if (parseTimeFromName(file.getFileName().toString()) == null) return "文件名无时间";
                }
            }
            return null;
        } catch (IllegalArgumentException e) {
            return e.getMessage();
        }
    }

    /** 应用后的状态。 */
    public record ApplyResult(boolean ok, String error, boolean ctimeUnsupported) {
        public static ApplyResult success() { return new ApplyResult(true, "", false); }
        public static ApplyResult successNoCtime() { return new ApplyResult(true, "", true); }
        public static ApplyResult fail(String error) { return new ApplyResult(false, error, false); }
    }

    /**
     * 按模式计算并写入 (atime, mtime, ctime)。
     * Python: 指定时间 → 三个同值；偏移 → 各当前值 + delta；文件名 → 提取值。
     */
    public static ApplyResult apply(Path file, Mode mode, LocalDateTime specified, String offsetText) {
        double atime, mtime;
        Double ctime = null;
        try {
            var attrs = Files.readAttributes(file, java.nio.file.attribute.BasicFileAttributes.class);
            atime = attrs.lastAccessTime().toMillis() / 1000.0;
            mtime = attrs.lastModifiedTime().toMillis() / 1000.0;
            switch (mode) {
                case SET -> {
                    double ts = toEpoch(specified);
                    atime = ts; mtime = ts; ctime = ts;
                }
                case OFFSET -> {
                    double delta = parseOffsetSeconds(offsetText);
                    atime += delta; mtime += delta;
                    // 创建时间：尽力读取当前值后平移（写支持见 setFileTimes）
                    ctime = attrs.creationTime().toMillis() / 1000.0 + delta;
                }
                case FROM_NAME -> {
                    LocalDateTime got = parseTimeFromName(file.getFileName().toString());
                    if (got == null) return ApplyResult.fail("文件名无时间");
                    double ts = toEpoch(got);
                    atime = ts; mtime = ts; ctime = ts;
                }
            }
        } catch (IOException e) {
            return ApplyResult.fail(String.valueOf(e.getMessage()));
        } catch (IllegalArgumentException e) {
            return ApplyResult.fail(e.getMessage());
        }
        return setFileTimes(file, atime, mtime, ctime);
    }

    /**
     * Python: set_file_times —— os.utime + Windows 创建时间。
     * Java：lastModifiedTime 必写；lastAccessTime 与 creationTime 尽力而为。
     */
    public static ApplyResult setFileTimes(Path path, double atime, double mtime, Double ctime) {
        try {
            Files.setLastModifiedTime(path, FileTime.fromMillis(Math.round(mtime * 1000)));
            try {
                Files.setAttribute(path, "basic:lastAccessTime", FileTime.fromMillis(Math.round(atime * 1000)));
            } catch (IOException | UnsupportedOperationException ignored) {
                // 平台不支持写访问时间
            }
            boolean ctimeUnsupported = false;
            if (ctime != null && System.getProperty("os.name", "").toLowerCase().contains("win")) {
                try {
                    Files.setAttribute(path, "basic:creationTime", FileTime.fromMillis(Math.round(ctime * 1000)));
                } catch (IOException | UnsupportedOperationException e) {
                    ctimeUnsupported = true;
                }
            }
            return ctimeUnsupported ? ApplyResult.successNoCtime() : ApplyResult.success();
        } catch (IOException e) {
            return ApplyResult.fail(String.valueOf(e.getMessage()));
        }
    }
}
