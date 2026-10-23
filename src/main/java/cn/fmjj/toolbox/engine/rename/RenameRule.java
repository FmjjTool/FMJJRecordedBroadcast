package cn.fmjj.toolbox.engine.rename;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 录播文件命名规则（移植自 Python 版 gui_qt/pages/rename.py 的 _build_filename）。
 *
 * <p>格式：`日期_时间_账户_类型1+类型2_有弹幕|无弹幕_平台_标题 + 后缀`
 * （空字段自动跳过；类型按用户点击顺序用 `+` 连接）。
 */
public final class RenameRule {

    /** 默认直播类型（对应 Python 版 config_manager.live_types）。 */
    public static final List<String> DEFAULT_LIVE_TYPES = List.of(
            "聊天", "游戏", "巡演", "签售", "线下", "生日", "调试", "跨年");

    /** 默认平台。 */
    public static final List<String> DEFAULT_PLATFORMS = List.of("B站", "抖音", "快手", "酷狗");

    private static final Pattern DATE_TIME = Pattern.compile(
            "(\\d{4}-?\\d{2}-?\\d{2})[ _T-]?(\\d{2}[-:]?\\d{2}[-:]?\\d{2})?");

    /** 命名要素。 */
    public record Parts(String date, String time, String account, List<String> types,
                        boolean withDanmaku, String platform, String title) {}

    private RenameRule() {}

    /** 拼装文件名（不含目录）。 */
    public static String build(Parts p, String suffix) {
        StringBuilder sb = new StringBuilder();
        append(sb, p.date());
        append(sb, p.time());
        append(sb, p.account());
        if (p.types() != null && !p.types().isEmpty()) append(sb, String.join("+", p.types()));
        append(sb, p.withDanmaku() ? "有弹幕" : "无弹幕");
        append(sb, p.platform());
        if (p.title() != null && !p.title().isBlank()) append(sb, cleanSpecial(p.title()));
        if (sb.length() == 0) return "";
        return sb + (suffix == null ? "" : suffix);
    }

    private static void append(StringBuilder sb, String part) {
        if (part == null) return;
        String t = part.strip();
        if (t.isEmpty()) return;
        if (sb.length() > 0) sb.append('_');
        sb.append(t);
    }

    /** 清理文件名非法字符（对应 Python clean_special_chars）。 */
    public static String cleanSpecial(String s) {
        return s == null ? "" : s.replaceAll("[\\\\/:*?\"<>|\\r\\n\\t]", "_").strip();
    }

    /** 从文件名/路径解析日期与时间（如 20261023_183000 或 2026-10-23 18-30-00）。 */
    public static String[] parseDateTime(String name) {
        if (name == null) return new String[] {"", ""};
        Matcher m = DATE_TIME.matcher(name);
        if (!m.find()) return new String[] {"", ""};
        String date = normalizeDate(m.group(1));
        String time = m.group(2) == null ? "" : normalizeTime(m.group(2));
        return new String[] {date, time};
    }

    static String normalizeDate(String d) {
        String digits = d.replaceAll("[^0-9]", "");
        if (digits.length() != 8) return d;
        return digits.substring(0, 4) + "-" + digits.substring(4, 6) + "-" + digits.substring(6, 8);
    }

    static String normalizeTime(String t) {
        String digits = t.replaceAll("[^0-9]", "");
        if (digits.length() != 6) return t;
        return digits.substring(0, 2) + "-" + digits.substring(2, 4) + "-" + digits.substring(4, 6);
    }

    /** 从文件名猜账户（取第一段非日期文本）。 */
    public static String guessAccount(String fileName) {
        if (fileName == null) return "";
        String stem = fileName;
        int dot = stem.lastIndexOf('.');
        if (dot > 0) stem = stem.substring(0, dot);
        for (String part : stem.split("[_\\-\\s]+")) {
            if (part.matches("\\d+")) continue;
            if (part.isBlank()) continue;
            return part;
        }
        return "";
    }
}
