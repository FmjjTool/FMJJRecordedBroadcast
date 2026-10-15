package cn.fmjj.toolbox.core.util;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 文件名工具：从文件名提取日期（YYYYMMDD）。
 * 移植自 Python 版 toolbox/utils.py 的 extract_date_from_filename / _validate_date。
 */
public final class FileNameUtils {

    private static final Pattern ORGANIZE_DATE_PATTERN = Pattern.compile("(\\d{4})(\\d{2})(\\d{2})");
    /** 带分隔符的日期：2026-06-08 / 2026_06_08 / 2026.06.08（月/日 1-2 位）。 */
    private static final Pattern ORGANIZE_DATE_SEP_PATTERN =
            Pattern.compile("(\\d{4})[-_./](\\d{1,2})[-_./](\\d{1,2})");

    private FileNameUtils() {}

    /** 去掉扩展名（os.path.splitext 语义：最后一个点）。 */
    public static String stripExtension(String filename) {
        int dot = filename.lastIndexOf('.');
        if (dot <= 0) return filename;
        return filename.substring(0, dot);
    }

    /** 扩展名（不含点，小写前的原样；空串表示无扩展名）。 */
    public static String extensionOf(String filename) {
        int dot = filename.lastIndexOf('.');
        if (dot <= 0 || dot == filename.length() - 1) return "";
        return filename.substring(dot + 1);
    }

    /**
     * 校验日期并返回 YYYYMMDD；非法返回 null。
     * 月末边界处理与 Python 一致：2月30日这类直接判 None（last_day=28，d=30 超限）。
     */
    public static String validateDate(int y, int mo, int d) {
        if (!(2000 <= y && y <= 2099 && 1 <= mo && mo <= 12 && 1 <= d && d <= 31)) return null;
        try {
            LocalDate.of(y, mo, d);
            return String.format("%04d%02d%02d", y, mo, d);
        } catch (java.time.DateTimeException e) {
            // 计算当月实际天数（如 2 月），d 超过则 None（与 Python 分支等价）
            int lastDay = (mo == 12
                    ? LocalDate.of(y + 1, 1, 1)
                    : LocalDate.of(y, mo + 1, 1)).minusDays(1).getDayOfMonth();
            if (d <= lastDay) return String.format("%04d%02d%02d", y, mo, d);
            return null;
        }
    }

    /**
     * Python: extract_date_from_filename。
     * 先扫描带分隔符日期（跳过范围不合法的），再找连续 8 位数字。
     */
    public static String extractDateFromFilename(String filename) {
        String name = stripExtension(filename == null ? "" : filename);

        Matcher m = ORGANIZE_DATE_SEP_PATTERN.matcher(name);
        while (m.find()) {
            int y = Integer.parseInt(m.group(1));
            int mo = Integer.parseInt(m.group(2));
            int d = Integer.parseInt(m.group(3));
            if (!(2000 <= y && y <= 2099 && 1 <= mo && mo <= 12 && 1 <= d && d <= 31)) continue;
            String result = validateDate(y, mo, d);
            if (result != null) return result;
        }

        m = ORGANIZE_DATE_PATTERN.matcher(name);
        if (m.find()) {
            try {
                return validateDate(Integer.parseInt(m.group(1)),
                        Integer.parseInt(m.group(2)), Integer.parseInt(m.group(3)));
            } catch (NumberFormatException ignored) {
                // \d{4} 不可能失败，保持与 Python 相同的 try 结构
            }
        }
        return null;
    }

    private static final DateTimeFormatter COMPACT = DateTimeFormatter.ofPattern("yyyyMMdd");

    /** YYYYMMDD 字符串转 LocalDate。 */
    public static LocalDate parseCompact(String yyyymmdd) {
        return LocalDate.parse(yyyymmdd, COMPACT);
    }
}
