package cn.fmjj.toolbox.engine.subtitle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 字幕转换引擎。
 *
 * 逐行移植自 Python 版 {@code src/toolbox/subtitle_engine.py}（402060 字段名、
 * 舍入方式、输出文案全部保持一致）。
 *
 * 已知的微小行为差异（有意的健壮化，见 JAVA_PORT_PLAN.md）：
 * <ul>
 *   <li>Python 的 utf-8 errors="ignore" 会丢弃非法字节，Java 的 UTF-8 解码替换为 U+FFFD，
 *       对合法字幕文件（含 BOM，Jackson 可处理）输出一致；</li>
 *   <li>Python 的 f-string 两位小数舍入为 round-half-even（精确二进制值），
 *       本类用 BigDecimal 精确值 + HALF_EVEN 复刻，避免 Java String.format 的 HALF_UP 偏差。</li>
 * </ul>
 */
public final class SubtitleEngine {

    /** 一条字幕：起/止秒 + 文本。 */
    public record Cue(double start, double end, String text) {}

    /** 一个待落盘输出文件。 */
    public record OutputFile(String format, String filename, byte[] content) {}

    /** 单文件转换结果。 */
    public record ConvertResult(boolean success, String message, List<OutputFile> outputFiles) {
        public static ConvertResult fail(String message) {
            return new ConvertResult(false, message, List.of());
        }
    }

    private static final Pattern SRT_TIME = Pattern.compile("(\\d{2}):(\\d{2}):(\\d{2}),(\\d{3})");
    private static final Pattern ASS_TIME = Pattern.compile("(\\d+):(\\d{2}):(\\d{2})\\.(\\d{2})");
    private static final Pattern ASS_TAG = Pattern.compile("\\{[^}]*\\}");
    private static final DateTimeFormatter DATE_SUFFIX = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss");

    private static final ObjectMapper MAPPER = new ObjectMapper();

    // 输出格式 -> (后缀, 生成器)，与 Python format_map 一一对应
    private record FmtSpec(String suffix, Generator gen) {}

    @FunctionalInterface
    private interface Generator { byte[] apply(List<Cue> cues); }

    private final Map<String, FmtSpec> formatMap = new LinkedHashMap<>() {{
        put("text", new FmtSpec("纯文字.txt", SubtitleEngine.this::generateText));
        put("timed_text", new FmtSpec("带时间轴.txt", SubtitleEngine.this::generateTimedText));
        put("lrc", new FmtSpec(".lrc", SubtitleEngine.this::generateLrc));
        put("ass", new FmtSpec(".ass", SubtitleEngine.this::generateAss));
        put("srt", new FmtSpec(".srt", SubtitleEngine.this::generateSrt));
    }};

    private final Pattern srtTimePattern = SRT_TIME;
    private final Pattern assTimePattern = ASS_TIME;

    // ==================== 时间格式化 ====================

    /** 复刻 Python format(x, ".2f")：精确二进制值 + round-half-even。 */
    private static double round2HalfEven(double v) {
        return new BigDecimal(v).setScale(2, RoundingMode.HALF_EVEN).doubleValue();
    }

    /** Python: seconds_to_time_str。format_type: lrc / srt / ass / timed_text。 */
    public String secondsToTimeStr(double seconds, String formatType) {
        if (seconds < 0) seconds = 0;
        int totalSeconds = (int) seconds;
        int hours = totalSeconds / 3600;
        int minutes = (totalSeconds % 3600) / 60;
        int secs = totalSeconds % 60;
        int msPart = (int) ((seconds - totalSeconds) * 100);

        return switch (formatType == null ? "lrc" : formatType) {
            case "srt" -> {
                int msFull = (int) ((seconds - totalSeconds) * 1000);
                yield String.format("%02d:%02d:%02d,%03d", hours, minutes, secs, msFull);
            }
            case "ass" -> String.format("%d:%02d:%02d.%02d", hours, minutes, secs, msPart);
            // lrc / timed_text / 其它
            default -> String.format("%02d:%05.2f", minutes, round2HalfEven(secs + msPart / 100.0));
        };
    }

    /** Python: time_str_to_seconds。format_type: srt / ass / lrc。 */
    public double timeStrToSeconds(String timeStr, String formatType) {
        String s = timeStr == null ? "" : timeStr.strip();
        if ("srt".equals(formatType)) {
            Matcher m = srtTimePattern.matcher(s);
            if (m.lookingAt()) {
                long h = Long.parseLong(m.group(1)), mm = Long.parseLong(m.group(2)),
                        sec = Long.parseLong(m.group(3)), ms = Long.parseLong(m.group(4));
                return h * 3600 + mm * 60 + sec + ms / 1000.0;
            }
        } else if ("ass".equals(formatType)) {
            Matcher m = assTimePattern.matcher(s);
            if (m.lookingAt()) {
                long h = Long.parseLong(m.group(1)), mm = Long.parseLong(m.group(2)),
                        sec = Long.parseLong(m.group(3)), cs = Long.parseLong(m.group(4));
                return h * 3600 + mm * 60 + sec + cs / 100.0;
            }
        } else if ("lrc".equals(formatType)) {
            String[] parts = s.split(":");
            if (parts.length == 2) {
                return Integer.parseInt(parts[0].strip()) * 60 + Double.parseDouble(parts[1].strip());
            }
        }
        return 0.0;
    }

    // ==================== 解析 ====================

    /** Python: parse_bcc。JSON 结构 { body: [ {from, to, content}, ... ] }。 */
    public List<Cue> parseBcc(byte[] content) {
        try {
            JsonNode root = MAPPER.readTree(content);
            JsonNode body = root.path("body");
            if (!body.isArray()) throw new IllegalArgumentException("BCC格式错误，缺少body数组");
            List<Cue> out = new ArrayList<>();
            for (JsonNode item : body) {
                double from = item.path("from").asDouble(0.0);
                double to = item.path("to").asDouble(0.0);
                String text = item.path("content").asText("").strip();
                if (!text.isEmpty()) out.add(new Cue(from, to, text));
            }
            return out;
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalArgumentException("解析BCC失败: " + e.getMessage(), e);
        }
    }

    /** Python: parse_ass。 */
    public List<Cue> parseAss(byte[] content) {
        try {
            List<Cue> out = new ArrayList<>();
            boolean inDialogue = false;
            for (String line : decodeLines(content)) {
                line = line.strip();
                if (line.startsWith("[Events]")) { inDialogue = true; continue; }
                if (!inDialogue || !line.startsWith("Dialogue:")) continue;
                String[] parts = line.split(",", 10);
                if (parts.length < 10) continue;
                double start = timeStrToSeconds(parts[1], "ass");
                double end = timeStrToSeconds(parts[2], "ass");
                String text = ASS_TAG.matcher(parts[9].strip()).replaceAll("");
                if (!text.isEmpty()) out.add(new Cue(start, end, text));
            }
            return out;
        } catch (Exception e) {
            throw new IllegalArgumentException("解析ASS失败: " + e.getMessage(), e);
        }
    }

    /** Python: parse_srt。 */
    public List<Cue> parseSrt(byte[] content) {
        try {
            List<Cue> subs = new ArrayList<>();
            double[] cur = {0.0, 0.0};
            StringBuilder text = new StringBuilder();
            boolean inText = false;
            for (String line : decodeLines(content)) {
                line = line.strip();
                if (line.isEmpty()) {
                    if (!text.isEmpty()) {
                        subs.add(new Cue(cur[0], cur[1], text.toString().strip()));
                        cur[0] = 0.0; cur[1] = 0.0; text.setLength(0);
                    }
                    inText = false;
                    continue;
                }
                if (line.contains("-->") && !inText) {
                    String[] tp = line.split("-->");
                    if (tp.length == 2) {
                        cur[0] = timeStrToSeconds(tp[0], "srt");
                        cur[1] = timeStrToSeconds(tp[1], "srt");
                    }
                    continue;
                }
                if (!isDigits(line) && !line.contains("-->")) {
                    text.append(line).append('\n');
                    inText = true;
                }
            }
            if (!text.isEmpty()) subs.add(new Cue(cur[0], cur[1], text.toString().strip()));
            return subs.stream().filter(c -> !c.text().isEmpty()).toList();
        } catch (Exception e) {
            throw new IllegalArgumentException("解析SRT失败: " + e.getMessage(), e);
        }
    }

    /** Python: parse_text（每行 2 秒间隔）。 */
    public List<Cue> parseText(byte[] content) {
        try {
            List<Cue> out = new ArrayList<>();
            double start = 0.0;
            for (String line : decodeLines(content)) {
                line = line.strip();
                if (!line.isEmpty()) {
                    out.add(new Cue(start, start + 2.0, line));
                    start += 2.0;
                }
            }
            return out;
        } catch (Exception e) {
            throw new IllegalArgumentException("解析TXT失败: " + e.getMessage(), e);
        }
    }

    /** Python: detect_format。 */
    public String detectFormat(String filename, byte[] content) {
        String ext = filename.contains(".") ? filename.substring(filename.lastIndexOf('.') + 1).toLowerCase(Locale.ROOT) : "";
        if ("json".equals(ext) && content != null) {
            try {
                JsonNode root = MAPPER.readTree(content);
                if (root.path("body").isArray()) return "bcc";
            } catch (Exception ignored) {
                // 非合法 JSON，走 python 一致的分支
            }
            return "json";
        }
        return ext;
    }

    private static boolean isDigits(String s) {
        if (s.isEmpty()) return false;
        for (int i = 0; i < s.length(); i++) if (!Character.isDigit(s.charAt(i))) return false;
        return true;
    }

    private static String[] decodeLines(byte[] content) {
        return new String(content, StandardCharsets.UTF_8).split("\n", -1);
    }

    // ==================== 生成 ====================

    public byte[] generateText(List<Cue> cues) {
        return String.join("\n", cues.stream().map(Cue::text).toList()).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] generateTimedText(List<Cue> cues) {
        List<String> lines = new ArrayList<>();
        for (Cue c : cues) {
            lines.add(String.format("[%s --> %s] %s",
                    secondsToTimeStr(c.start(), "timed_text"),
                    secondsToTimeStr(c.end(), "timed_text"), c.text()));
        }
        return String.join("\n", lines).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] generateLrc(List<Cue> cues) {
        List<String> lines = new ArrayList<>();
        for (Cue c : cues) {
            lines.add("[" + secondsToTimeStr(c.start(), "lrc") + "]" + c.text());
        }
        return String.join("\n", lines).getBytes(StandardCharsets.UTF_8);
    }

    private static final String ASS_HEADER = """
            [Script Info]
            Title: Converted Subtitle
            ScriptType: v4.00+
            WrapStyle: 0
            ScaledBorderAndShadow: yes
            [V4+ Styles]
            Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
            Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,0
            [Events]
            Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            """;

    public byte[] generateAss(List<Cue> cues) {
        List<String> lines = new ArrayList<>(List.of(ASS_HEADER));
        for (Cue c : cues) {
            lines.add(String.format("Dialogue: 0,%s,%s,Default,,0,0,0,,%s",
                    secondsToTimeStr(c.start(), "ass"),
                    secondsToTimeStr(c.end(), "ass"), c.text()));
        }
        return String.join("\n", lines).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] generateSrt(List<Cue> cues) {
        List<String> lines = new ArrayList<>();
        for (int idx = 0; idx < cues.size(); idx++) {
            Cue c = cues.get(idx);
            lines.add(String.valueOf(idx + 1));
            lines.add(String.format("%s --> %s",
                    secondsToTimeStr(c.start(), "srt"), secondsToTimeStr(c.end(), "srt")));
            lines.add(c.text());
            lines.add("");
        }
        return String.join("\n", lines).getBytes(StandardCharsets.UTF_8);
    }

    // ==================== 单文件转换 ====================

    /** Python: convert_file。outputFormats ∈ [text, timed_text, lrc, ass, srt]；nameMode: original / date。 */
    public ConvertResult convertFile(String filename, byte[] content,
                                     List<String> outputFormats, String nameMode) {
        try {
            String inputFormat = detectFormat(filename, content);
            List<Cue> cues = switch (inputFormat) {
                case "bcc", "json" -> parseBcc(content);
                case "ass" -> parseAss(content);
                case "srt" -> parseSrt(content);
                case "txt" -> parseText(content);
                default -> null;
            };
            if (cues == null) return ConvertResult.fail("不支持的输入格式: " + inputFormat);
            if (cues.isEmpty()) return ConvertResult.fail("未解析到字幕内容");

            String baseName = filename.contains(".")
                    ? filename.substring(0, filename.lastIndexOf('.')) : filename;
            if ("date".equals(nameMode)) {
                baseName = baseName + "_" + LocalDateTime.now().format(DATE_SUFFIX);
            }

            List<OutputFile> files = new ArrayList<>();
            for (String fmt : outputFormats) {
                FmtSpec spec = formatMap.get(fmt);
                if (spec == null) continue;
                String outName = spec.suffix().contains(" ")
                        ? baseName + "_" + spec.suffix()
                        : baseName + spec.suffix();
                files.add(new OutputFile(fmt, outName, spec.gen().apply(cues)));
            }
            return new ConvertResult(true, "成功转换为 " + files.size() + " 种格式", files);
        } catch (Exception e) {
            return ConvertResult.fail("转换失败: " + e.getMessage());
        }
    }
}
