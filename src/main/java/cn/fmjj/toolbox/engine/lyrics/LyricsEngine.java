package cn.fmjj.toolbox.engine.lyrics;

import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.DataFormatException;
import java.util.zip.Deflater;
import java.util.zip.Inflater;

/**
 * 歌词转换引擎。
 *
 * 逐行移植自 Python 版 {@code src/toolbox/lyrics_engine.py}：
 * 解析 LRC / KRC(酷狗，含解密) / KSC(酷我) / QRC(QQ音乐) / TRC·ELRC(增强LRC) / 纯文本；
 * 生成 TXT / LRC / SRT / ASS / KSC(逐字) / KRCD。
 *
 * 移植约定：
 * <ul>
 *   <li>Python 的 words 元组第三项恒为 ""（无调用方读取），Java 侧用 {@link Word} 只保留 startMs/durMs；</li>
 *   <li>Python 的 to_ass 用 _fmt_lrc_time 输出 ASS 时间（"00:00.00" 而非 "0:00:00.00"）——
 *       与原版保持 bug 级一致，待与作者确认后再修，不在移植期顺手改；</li>
 *   <li>两位小数格式化统一用 BigDecimal 精确值 + HALF_EVEN，复刻 Python format 行为。</li>
 * </ul>
 */
public final class LyricsEngine {

    /** 一行歌词。 */
    public record LyricLine(double start, double end, String text, List<Word> words) {}

    /** 逐字时间（毫秒）。 */
    public record Word(int startMs, int durMs) {}

    /** 单文件转换结果。 */
    public record ConvertResult(boolean success, String message, List<Output> outputFiles) {
        public record Output(String format, String filename, byte[] content) {}
        public static ConvertResult fail(String message) {
            return new ConvertResult(false, message, List.of());
        }
    }

    /** 酷狗 KRC 固定异或密钥（16 字节循环）。 */
    private static final int[] KRC_KEY = {64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105};

    /** 片头/片尾署名（转换输出时自动在开头和结尾各加一条）。 */
    public static final String CREDIT_LINE = "-歌词文件由封茗囧菌数据汇总库收集-";

    private static final byte[] KRC_MAGIC = "krc1".getBytes(StandardCharsets.US_ASCII);

    // 元数据标签跳过正则（与 Python 各解析函数内联的正则一一对应）
    private static final Pattern META_PARSE_TAGGED = Pattern.compile("^\\[(ti|ar|al|by|offset|id|hash|sign|total|re|ve|length|language):");
    private static final Pattern META_KRC = Pattern.compile("^\\[(ti|ar|al|by|offset|id|hash|sign|total|re|ve|language):");
    private static final Pattern META_KSC_OR_PLAIN = Pattern.compile("^\\[(ti|ar|al|by|offset|id|hash|sign|total):");

    // ==================== KRC 加解密 ====================

    /** 明文歌词 → 酷狗 KRC：UTF-8 → zlib 压缩 → 循环 XOR → 加 4 字节头。 */
    public static byte[] encodeKrc(String text) {
        byte[] src = text.getBytes(StandardCharsets.UTF_8);
        Deflater deflater = new Deflater(); // 默认 level=-1 → 6，与 Python zlib.compress 默认一致
        deflater.setInput(src);
        deflater.finish();
        ByteArrayOutputStream bos = new ByteArrayOutputStream(Math.max(64, src.length / 2));
        byte[] buf = new byte[8192];
        while (!deflater.finished()) {
            int n = deflater.deflate(buf);
            bos.write(buf, 0, n);
        }
        deflater.end();
        byte[] compressed = bos.toByteArray();

        byte[] out = new byte[4 + compressed.length];
        System.arraycopy(KRC_MAGIC, 0, out, 0, 4);
        for (int i = 0; i < compressed.length; i++) {
            out[4 + i] = (byte) (compressed[i] ^ KRC_KEY[i % 16]);
        }
        return out;
    }

    /** 酷狗 KRC 解密：跳过 4 字节头 → 循环 XOR → zlib 解压（失败则直接解码）。 */
    public static String decodeKrc(byte[] content) {
        byte[] data = content.length > 4 ? Arrays.copyOfRange(content, 4, content.length) : content;
        byte[] raw = new byte[data.length];
        for (int i = 0; i < data.length; i++) {
            raw[i] = (byte) (data[i] ^ KRC_KEY[i % 16]);
        }
        try {
            Inflater inflater = new Inflater();
            inflater.setInput(raw);
            ByteArrayOutputStream bos = new ByteArrayOutputStream(Math.max(64, raw.length * 4));
            byte[] buf = new byte[8192];
            while (!inflater.finished()) {
                int n = inflater.inflate(buf);
                if (n == 0 && (inflater.needsInput() || inflater.needsDictionary())) {
                    // Python zlib.decompress 对截断流抛 error → 走兜底解码
                    throw new DataFormatException("truncated zlib stream");
                }
                bos.write(buf, 0, n);
            }
            inflater.end();
            return new String(bos.toByteArray(), StandardCharsets.UTF_8);
        } catch (DataFormatException e) {
            try {
                return new String(raw, StandardCharsets.UTF_8);
            } catch (Exception e2) {
                return new String(raw, Charset.forName("GBK"));
            }
        }
    }

    /** KRC（酷狗加密）→ KRCD（明文）。 */
    public String krcToKrcd(byte[] content) { return decodeKrc(content); }

    /** KRCD（明文）→ KRC（酷狗加密）。 */
    public byte[] krcdToKrc(String text) { return encodeKrc(text); }

    // ==================== 时间格式化 ====================

    private static double round2HalfEven(double v) {
        return new BigDecimal(v).setScale(2, RoundingMode.HALF_EVEN).doubleValue();
    }

    private static String fmtLrcTime(double seconds) {
        seconds = Math.max(0.0, seconds);
        int m = (int) seconds / 60;
        double s = seconds - m * 60;
        return String.format("%02d:%05.2f", m, round2HalfEven(s));
    }

    private static String fmtSrtTime(double seconds) {
        seconds = Math.max(0.0, seconds);
        int h = (int) seconds / 3600;
        int m = ((int) seconds % 3600) / 60;
        int s = (int) seconds % 60;
        int ms = new BigDecimal((seconds - (int) seconds) * 1000)
                .setScale(0, RoundingMode.HALF_EVEN).intValue();
        if (ms >= 1000) ms = 999;
        return String.format("%02d:%02d:%02d,%03d", h, m, s, ms);
    }

    // ==================== 解析 ====================

    @FunctionalInterface
    private interface LineCb {
        /** 返回 null / 单行 / 多行。 */
        Object handle(String raw, Pattern timeRe);
    }

    /** Python: _parse_tagged —— 通用逐行解析框架。 */
    private List<LyricLine> parseTagged(String text, Pattern timeRe, LineCb cb) {
        List<LyricLine> lines = new ArrayList<>();
        for (String raw : text.split("\n")) {
            raw = raw.strip();
            if (raw.isEmpty()) continue;
            if (META_PARSE_TAGGED.matcher(raw).find()) continue;
            Object got = cb.handle(raw, timeRe);
            if (got == null) continue;
            if (got instanceof List<?> list) {
                for (Object o : list) lines.add((LyricLine) o);
            } else {
                lines.add((LyricLine) got);
            }
        }
        lines.sort(Comparator.comparingDouble(LyricLine::start)); // List.sort 稳定，与 Python 一致
        return lines;
    }

    private static String text(byte[] content) {
        return new String(content, StandardCharsets.UTF_8);
    }

    public List<LyricLine> parseLrc(byte[] content) {
        Pattern timeRe = Pattern.compile("\\[(\\d{1,2}):(\\d{1,2})(?:[.:](\\d{1,3}))?\\]");
        return parseTagged(text(content), timeRe, (raw, tre) -> {
            // 注意：Matcher 复用同一对象迭代，find() 之后 group() 状态会失效，
            // 必须在循环内把值取出暂存（对应 Python finditer 的独立 Match 对象）。
            List<int[]> stamps = new ArrayList<>();
            List<String> fracs = new ArrayList<>();
            int lastEnd = -1;
            Matcher m = tre.matcher(raw);
            while (m.find()) {
                stamps.add(new int[] {Integer.parseInt(m.group(1)), Integer.parseInt(m.group(2))});
                fracs.add(m.group(3));
                lastEnd = m.end();
            }
            if (stamps.isEmpty()) return null;
            String lyric = raw.substring(lastEnd).strip();
            if (lyric.isEmpty()) return null;
            List<LyricLine> out = new ArrayList<>(stamps.size());
            for (int i = 0; i < stamps.size(); i++) {
                String frac = fracs.get(i);
                double t;
                if (frac == null) t = stamps.get(i)[0] * 60 + stamps.get(i)[1];
                else if (frac.length() == 3) t = stamps.get(i)[0] * 60 + stamps.get(i)[1] + Integer.parseInt(frac) / 1000.0;
                else t = stamps.get(i)[0] * 60 + stamps.get(i)[1] + Integer.parseInt(pad3(frac)) / 1000.0;
                out.add(new LyricLine(t, t + 5.0, lyric, List.of()));
            }
            return out;
        });
    }

    public List<LyricLine> parseKrc(byte[] content) {
        String t = decodeKrc(content);
        Pattern lineRe = Pattern.compile("^\\[(\\d+),(\\d+)(?:,\\d+)?\\](.*)$");
        Pattern wordRe = Pattern.compile("<(\\d+),(\\d+)(?:,\\d+)?>");
        List<LyricLine> lines = new ArrayList<>();
        for (String raw : t.split("\n")) {
            raw = raw.strip();
            if (raw.isEmpty()) continue;
            if (META_KRC.matcher(raw).find()) continue;
            Matcher m = lineRe.matcher(raw);
            if (!m.find()) continue;
            int startMs = Integer.parseInt(m.group(1));
            int durMs = Integer.parseInt(m.group(2));
            String body = m.group(3).strip();
            if (body.isEmpty()) continue;
            List<Word> words = new ArrayList<>();
            Matcher wm = wordRe.matcher(body);
            while (wm.find()) words.add(new Word(Integer.parseInt(wm.group(1)), Integer.parseInt(wm.group(2))));
            String plain = wordRe.matcher(body).replaceAll("").strip();
            lines.add(new LyricLine(startMs / 1000.0, (startMs + durMs) / 1000.0, plain, words));
        }
        lines.sort(Comparator.comparingDouble(LyricLine::start));
        return lines;
    }

    public List<LyricLine> parseKsc(byte[] content) {
        String t = text(content);
        Pattern lineRe = Pattern.compile("\\[(\\d{1,2}):(\\d{1,2})(?:[.:](\\d{1,3}))?\\](.*)$");
        Pattern wordRe = Pattern.compile("<(\\d+),(\\d+)(?:,\\d+)?>");
        List<LyricLine> lines = new ArrayList<>();
        for (String raw : t.split("\n")) {
            raw = raw.strip();
            if (raw.isEmpty()) continue;
            if (raw.startsWith("karaoke.") || META_KSC_OR_PLAIN.matcher(raw).find()) continue;
            Matcher m = lineRe.matcher(raw);
            if (!m.find()) continue;
            int mm = Integer.parseInt(m.group(1));
            int ss = Integer.parseInt(m.group(2));
            String frac = m.group(3);
            double sec;
            if (frac == null) sec = mm * 60 + ss;
            else if (frac.length() == 3) sec = mm * 60 + ss + Integer.parseInt(frac) / 1000.0;
            else sec = mm * 60 + ss + Integer.parseInt(pad3(frac)) / 1000.0;
            String body = m.group(4);
            String plain = wordRe.matcher(body).replaceAll("").strip();
            if (plain.isEmpty()) continue;
            List<Word> words = new ArrayList<>();
            Matcher wm = wordRe.matcher(body);
            while (wm.find()) words.add(new Word(Integer.parseInt(wm.group(1)), Integer.parseInt(wm.group(2))));
            double end;
            if (!words.isEmpty()) {
                Word last = words.get(words.size() - 1);
                end = sec + (last.startMs() + last.durMs()) / 1000.0;
            } else {
                end = sec + 5.0;
            }
            lines.add(new LyricLine(sec, end, plain, words));
        }
        lines.sort(Comparator.comparingDouble(LyricLine::start));
        return lines;
    }

    public List<LyricLine> parseQrc(byte[] content) {
        String t = text(content);
        Pattern lineRe = Pattern.compile("^\\[(\\d+),(\\d+)\\](.*)$");
        Pattern wordRe = Pattern.compile("\\((\\d+),(\\d+)\\)");
        List<LyricLine> lines = new ArrayList<>();
        for (String raw : t.split("\n")) {
            raw = raw.strip();
            if (raw.isEmpty()) continue;
            if (META_KSC_OR_PLAIN.matcher(raw).find()) continue;
            Matcher m = lineRe.matcher(raw);
            if (!m.find()) continue;
            int startMs = Integer.parseInt(m.group(1));
            int durMs = Integer.parseInt(m.group(2));
            String body = m.group(3);
            String plain = wordRe.matcher(body).replaceAll("").strip();
            if (plain.isEmpty()) continue;
            List<Word> words = new ArrayList<>();
            Matcher wm = wordRe.matcher(body);
            while (wm.find()) words.add(new Word(Integer.parseInt(wm.group(1)), Integer.parseInt(wm.group(2))));
            lines.add(new LyricLine(startMs / 1000.0, (startMs + durMs) / 1000.0, plain, words));
        }
        lines.sort(Comparator.comparingDouble(LyricLine::start));
        return lines;
    }

    public List<LyricLine> parseTrc(byte[] content) {
        String t = text(content);
        Pattern lineRe = Pattern.compile("^\\[(\\d{1,2}):(\\d{1,2})(?:[.:](\\d{1,3}))?\\](.*)$");
        Pattern wordRe = Pattern.compile("<(\\d{1,2}):(\\d{1,2})(?:[.:](\\d{1,3}))?>");
        List<LyricLine> lines = new ArrayList<>();
        for (String raw : t.split("\n")) {
            raw = raw.strip();
            if (raw.isEmpty()) continue;
            if (META_KSC_OR_PLAIN.matcher(raw).find()) continue;
            Matcher m = lineRe.matcher(raw);
            if (!m.find()) continue;
            int mm = Integer.parseInt(m.group(1));
            int ss = Integer.parseInt(m.group(2));
            String frac = m.group(3);
            double sec;
            if (frac == null) sec = mm * 60 + ss;
            else if (frac.length() == 3) sec = mm * 60 + ss + Integer.parseInt(frac) / 1000.0;
            else sec = mm * 60 + ss + Integer.parseInt(pad3(frac)) / 1000.0;
            String body = m.group(4);
            String plain = wordRe.matcher(body).replaceAll("").strip();
            if (plain.isEmpty()) continue;
            List<Word> words = new ArrayList<>();
            Matcher wm = wordRe.matcher(body);
            while (wm.find()) {
                int wmm = Integer.parseInt(wm.group(1));
                int wss = Integer.parseInt(wm.group(2));
                String wfrac = wm.group(3);
                double wsec;
                if (wfrac == null) wsec = wmm * 60 + wss;
                else if (wfrac.length() == 3) wsec = wmm * 60 + wss + Integer.parseInt(wfrac) / 1000.0;
                else wsec = wmm * 60 + wss + Integer.parseInt(pad3(wfrac == null ? "0" : wfrac)) / 1000.0;
                words.add(new Word((int) (wsec * 1000), 0));
            }
            lines.add(new LyricLine(sec, sec + 5.0, plain, words));
        }
        lines.sort(Comparator.comparingDouble(LyricLine::start));
        return lines;
    }

    public List<LyricLine> parsePlain(byte[] content) {
        List<LyricLine> lines = new ArrayList<>();
        for (String raw : text(content).split("\n")) {
            raw = raw.strip();
            if (!raw.isEmpty()) lines.add(new LyricLine(0.0, 0.0, raw, List.of()));
        }
        return lines;
    }

    /** Python: parse —— 按扩展名路由。 */
    public List<LyricLine> parse(String filename, byte[] content) {
        String ext = filename.contains(".") ? filename.substring(filename.lastIndexOf('.') + 1).toLowerCase(Locale.ROOT) : "";
        return switch (ext) {
            case "krc" -> parseKrc(content);
            case "ksc" -> parseKsc(content);
            case "qrc" -> parseQrc(content);
            case "trc", "elrc" -> parseTrc(content);
            case "lrc" -> parseLrc(content);
            default -> {
                List<LyricLine> r;
                try {
                    r = parseLrc(content);
                } catch (RuntimeException e) {
                    yield parsePlain(content);
                }
                yield r.isEmpty() ? parsePlain(content) : r;
            }
        };
    }

    private static String pad3(String frac) {
        StringBuilder sb = new StringBuilder(frac == null ? "" : frac);
        while (sb.length() < 3) sb.append('0');
        return sb.toString();
    }

    // ==================== 生成 ====================

    public byte[] toText(List<LyricLine> lines) {
        String body = String.join("\n", lines.stream().filter(l -> !l.text().isEmpty()).map(LyricLine::text).toList());
        return (CREDIT_LINE + "\n" + body + "\n" + CREDIT_LINE).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] toLrc(List<LyricLine> lines) {
        List<String> out = new ArrayList<>();
        out.add("[00:00:00]" + CREDIT_LINE);
        for (LyricLine ln : lines) {
            if (!ln.text().isEmpty()) out.add("[" + fmtLrcTime(ln.start()) + "]" + ln.text());
        }
        out.add("[00:00:00]" + CREDIT_LINE);
        return String.join("\n", out).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] toSrt(List<LyricLine> lines) {
        List<LyricLine> body = lines.stream().filter(l -> !l.text().isEmpty()).toList();
        List<String> out = new ArrayList<>();
        out.add("1");
        out.add(fmtSrtTime(0.0) + " --> " + fmtSrtTime(0.0));
        out.add(CREDIT_LINE);
        out.add("");
        for (int i = 0; i < body.size(); i++) {
            LyricLine ln = body.get(i);
            double start = ln.start();
            // Python: end = ln.get("end") or (start + 5.0) —— 0.0 也走默认值
            double end = ln.end() != 0.0 ? ln.end() : start + 5.0;
            out.add(String.valueOf(i + 2));
            out.add(fmtSrtTime(start) + " --> " + fmtSrtTime(end));
            out.add(ln.text());
            out.add("");
        }
        out.add(String.valueOf(body.size() + 2));
        out.add(fmtSrtTime(0.0) + " --> " + fmtSrtTime(0.0));
        out.add(CREDIT_LINE);
        out.add("");
        return String.join("\n", out).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] toAss(List<LyricLine> lines) {
        String header = """
                [Script Info]
                Title: Converted Lyric
                ScriptType: v4.00+
                WrapStyle: 0
                ScaledBorderAndShadow: yes
                [V4+ Styles]
                Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
                Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,0
                [Events]
                Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
                """;
        List<String> out = new ArrayList<>(List.of(header));
        out.add("Dialogue: 0," + fmtLrcTime(0.0) + "," + fmtLrcTime(0.0) + ",Default,,0,0,0,," + CREDIT_LINE);
        for (LyricLine ln : lines) {
            if (ln.text().isEmpty()) continue;
            // Python: end = ln.get("end") or (start + 5.0) —— 0.0 也走默认值
            double end = ln.end() != 0.0 ? ln.end() : ln.start() + 5.0;
            out.add("Dialogue: 0," + fmtLrcTime(ln.start()) + "," + fmtLrcTime(end) + ",Default,,0,0,0,,"
                    + ln.text().replace("\n", " "));
        }
        out.add("Dialogue: 0," + fmtLrcTime(0.0) + "," + fmtLrcTime(0.0) + ",Default,,0,0,0,," + CREDIT_LINE);
        return String.join("\n", out).getBytes(StandardCharsets.UTF_8);
    }

    public byte[] toKsc(List<LyricLine> lines) {
        List<String> out = new ArrayList<>();
        out.add("[00:00:00]" + CREDIT_LINE);
        for (LyricLine ln : lines) {
            if (ln.text().isEmpty()) continue;
            List<Word> words = ln.words();
            String body;
            if (!words.isEmpty()) {
                List<String> segs = splitText(ln.text(), words.size());
                int n = Math.min(words.size(), segs.size()); // Python zip 截断到较短
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < n; i++) {
                    sb.append(segs.get(i)).append('<').append(words.get(i).durMs())
                      .append(',').append(words.get(i).startMs()).append(",0>");
                }
                body = sb.toString();
            } else {
                // Python: ln.get('end', start+5) —— 键都存在（含 plain 的 0.0），原样取 end
                long durMs = (long) ((ln.end() - ln.start()) * 1000);
                body = ln.text() + "<" + durMs + ",0,0>";
            }
            out.add("[" + fmtLrcTime(ln.start()) + "]" + body);
        }
        out.add("[00:00:00]" + CREDIT_LINE);
        return String.join("\n", out).getBytes(StandardCharsets.UTF_8);
    }

    private static List<String> splitText(String text, int n) {
        if (n <= 0) return List.of(text);
        List<String> segs = new ArrayList<>(n);
        if (n >= text.length()) {
            for (int i = 0; i < text.length(); i++) segs.add(String.valueOf(text.charAt(i)));
            while (segs.size() < n) segs.add("");
            return segs;
        }
        double per = (double) text.length() / n;
        for (int i = 0; i < n; i++) {
            segs.add(text.substring((int) (i * per), (int) ((i + 1) * per)));
        }
        return segs;
    }

    // ==================== 单文件转换 ====================

    private record FmtSpec(String suffix, Generator gen) {}

    @FunctionalInterface
    private interface Generator { byte[] apply(List<LyricLine> lines); }

    private final Map<String, FmtSpec> gen = new LinkedHashMap<>() {{
        put("txt", new FmtSpec("_歌词.txt", LyricsEngine.this::toText));
        put("lrc", new FmtSpec(".lrc", LyricsEngine.this::toLrc));
        put("srt", new FmtSpec(".srt", LyricsEngine.this::toSrt));
        put("ass", new FmtSpec(".ass", LyricsEngine.this::toAss));
        put("ksc", new FmtSpec(".ksc", LyricsEngine.this::toKsc));
    }};

    public ConvertResult convert(String filename, byte[] content, String outputFormat) {
        try {
            List<LyricLine> lines = parse(filename, content);
            if (lines.isEmpty()) return ConvertResult.fail("未解析到歌词内容");
            FmtSpec spec = gen.get(outputFormat);
            if (spec == null) return ConvertResult.fail("不支持的输出格式: " + outputFormat);
            String base = filename.contains(".") ? filename.substring(0, filename.lastIndexOf('.')) : filename;
            String outName = base + spec.suffix();
            return new ConvertResult(true, "ok",
                    List.of(new ConvertResult.Output(outputFormat, outName, spec.gen().apply(lines))));
        } catch (Exception e) {
            return ConvertResult.fail("转换失败: " + e.getMessage());
        }
    }
}
