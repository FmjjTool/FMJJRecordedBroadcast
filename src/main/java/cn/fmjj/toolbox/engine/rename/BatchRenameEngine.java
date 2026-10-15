package cn.fmjj.toolbox.engine.rename;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

/**
 * 批量重命名引擎（zRenamer 风格：八类规则叠加）。
 * 逐行移植自 Python 版 gui_qt/pages/rename_batch.py 的规则部分
 * （split_name / join_name / apply_rules 及预览冲突逻辑）。
 */
public final class BatchRenameEngine {

    private static final Pattern SPECIAL_RE = Pattern.compile("[!-/:-@\\[-`{-~\\s]");
    private static final Pattern CHINESE_RE = Pattern.compile("[\\u4e00-\\u9fff]");

    // ---------- 配置 ----------

    public record WholeCfg(String caseMode, String num, String convert, String extCase, String newExt) {}
    public record ReplaceCfg(String find, String to, boolean all, boolean regex, boolean caseSensitive) {}
    public record AddCfg(String prefix, String suffix, String posText, int posIdx, boolean fromRight,
                         String kw, boolean kwAfter, String kwText) {}
    public record DelCfg(List<String> types, int pos, int cnt, boolean fromRight,
                         String kw, boolean dirRight, int kwCnt, boolean inclKw) {}
    public record NumCfg(boolean on, int start, int step, int width, String mode) {}
    public record OptionsCfg(boolean ignoreExt, boolean conflict) {}

    public record RenameConfig(WholeCfg whole, ReplaceCfg replace, AddCfg add, DelCfg del,
                               NumCfg num, String template, OptionsCfg options) {
        public static RenameConfig empty() {
            return new RenameConfig(
                    new WholeCfg("", "", "", "", ""),
                    new ReplaceCfg("", "", true, false, false),
                    new AddCfg("", "", "", 0, false, "", true, ""),
                    new DelCfg(List.of(), 0, 0, false, "", true, 0, false),
                    new NumCfg(false, 1, 1, 1, "suffix"),
                    "",
                    new OptionsCfg(true, true));
        }
    }

    private BatchRenameEngine() {}

    // ---------- 拆分 / 合并 ----------

    /** Python: split_name(filename, ignore_ext)。 */
    public static String[] splitName(String filename, boolean ignoreExt) {
        if (!ignoreExt) return new String[] {filename, ""};
        int dot = filename.lastIndexOf('.');
        if (dot <= 0) return new String[] {filename, ""};
        return new String[] {filename.substring(0, dot), filename.substring(dot + 1)};
    }

    /** Python: join_name(stem, ext)。 */
    public static String joinName(String stem, String ext) {
        return ext.isEmpty() ? stem : stem + "." + ext;
    }

    // ---------- 规则应用 ----------

    /** Python: apply_rules，返回 [新主名, 新扩展名]。 */
    public static String[] applyRules(String stem, String ext, RenameConfig cfg, int index, LocalDateTime now) {
        // ---------- 整体 ----------
        WholeCfg w = cfg.whole();
        if ("upper".equals(w.caseMode())) stem = stem.toUpperCase();
        else if ("lower".equals(w.caseMode())) stem = stem.toLowerCase();
        else if ("capital".equals(w.caseMode())) stem = stem.isEmpty() ? stem : stem.substring(0, 1).toUpperCase() + stem.substring(1);

        if ("random".equals(w.num())) {
            stem = stem + "_" + java.util.concurrent.ThreadLocalRandom.current().nextInt(1000, 10000);
        }
        if ("full2half".equals(w.convert())) {
            StringBuilder sb = new StringBuilder(stem.length());
            for (int i = 0; i < stem.length(); i++) {
                char c = stem.charAt(i);
                sb.append(c >= 0xFF01 && c <= 0xFF5E ? (char) (c - 0xFEE0) : c);
            }
            stem = sb.toString();
        } else if ("half2full".equals(w.convert())) {
            StringBuilder sb = new StringBuilder(stem.length());
            for (int i = 0; i < stem.length(); i++) {
                char c = stem.charAt(i);
                sb.append(c >= 33 && c <= 126 ? (char) (c + 0xFEE0) : c);
            }
            stem = sb.toString();
        }
        if ("upper".equals(w.extCase()) && !ext.isEmpty()) ext = ext.toUpperCase();
        else if ("lower".equals(w.extCase()) && !ext.isEmpty()) ext = ext.toLowerCase();
        else if ("delete".equals(w.extCase())) ext = "";
        if (!w.newExt().isEmpty()) {
            // Python: new_ext.lstrip(".")（去掉所有前导点）
            int i = 0;
            while (i < w.newExt().length() && w.newExt().charAt(i) == '.') i++;
            ext = w.newExt().substring(i);
        }

        // ---------- 替换 ----------
        ReplaceCfg r = cfg.replace();
        if (!r.find().isEmpty()) {
            boolean all = r.all();
            if (r.regex()) {
                try {
                    // Python str 正则默认 Unicode 语义：\d 匹配全角数字等 Nd 类字符
                    Pattern p = Pattern.compile(r.find(), Pattern.UNICODE_CHARACTER_CLASS);
                    Matcher m = p.matcher(stem);
                    stem = all ? m.replaceAll(r.to()) : m.replaceFirst(r.to());
                } catch (PatternSyntaxException ignored) {
                    // Python: re.error → 原样保留
                }
            } else if (!r.caseSensitive()) {
                // Python: re.escape(find) + IGNORECASE；替换串按字面量处理（quoteReplacement 等价）
                Matcher m = Pattern.compile(Pattern.quote(r.find()), Pattern.CASE_INSENSITIVE).matcher(stem);
                stem = all ? m.replaceAll(Matcher.quoteReplacement(r.to())) : m.replaceFirst(Matcher.quoteReplacement(r.to()));
            } else {
                stem = all ? stem.replace(r.find(), r.to()) : stem.replaceFirst(Pattern.quote(r.find()), Matcher.quoteReplacement(r.to()));
            }
        }

        // ---------- 添加 ----------
        AddCfg a = cfg.add();
        stem = a.prefix() + stem + a.suffix();
        if (!a.posText().isEmpty()) {
            int idx = a.fromRight() ? Math.max(stem.length() - a.posIdx(), 0) : Math.min(a.posIdx(), stem.length());
            stem = stem.substring(0, idx) + a.posText() + stem.substring(idx);
        }
        if (!a.kw().isEmpty()) {
            int found = stem.indexOf(a.kw());
            if (found >= 0) {
                int cut = found + a.kw().length();
                if (!a.kwAfter()) cut = found;
                stem = stem.substring(0, cut) + a.kwText() + stem.substring(cut);
            }
        }

        // ---------- 删除 ----------
        DelCfg d = cfg.del();
        if (!d.types().isEmpty()) {
            StringBuilder sb = new StringBuilder(stem.length());
            for (int i = 0; i < stem.length(); i++) {
                char c = stem.charAt(i);
                if (keepChar(c, d.types())) sb.append(c);
            }
            stem = sb.toString();
        }
        if (d.cnt() > 0) {
            if (d.fromRight()) {
                int start = Math.max(stem.length() - d.pos() - d.cnt(), 0);
                int from = Math.max(stem.length() - d.pos(), 0);
                stem = stem.substring(0, start) + stem.substring(Math.min(from, stem.length()));
            } else {
                stem = stem.substring(0, Math.min(d.pos(), stem.length()))
                        + stem.substring(Math.min(d.pos() + d.cnt(), stem.length()));
            }
        }
        if (!d.kw().isEmpty()) {
            int found = stem.indexOf(d.kw());
            if (found >= 0) {
                boolean right = d.dirRight();
                int n = d.kwCnt();
                boolean include = d.inclKw();
                if (right) {
                    int start = found + d.kw().length();
                    if (include) start = found;
                    n = d.kwCnt() + (include ? d.kw().length() : 0);
                    stem = stem.substring(0, start) + stem.substring(Math.min(start + n, stem.length()));
                } else {
                    int end = found + d.kw().length();
                    if (include) end = found;
                    int start = Math.max(end - d.kwCnt(), 0);
                    stem = stem.substring(0, start) + stem.substring(end);
                }
            }
        }

        // ---------- 序号 ----------
        NumCfg n = cfg.num();
        if (n.on()) {
            int no = n.start() + index * n.step();
            String numStr = String.format("%0" + n.width() + "d", no);
            stem = "suffix".equals(n.mode()) ? stem + numStr : numStr + "_" + stem;
        }

        // ---------- 模板 ----------
        String t = cfg.template();
        if (!t.isEmpty()) {
            String original = t;
            t = t.replace("{名}", stem).replace("{name}", stem)
                 .replace("{扩展}", ext).replace("{ext}", ext)
                 .replace("{序号}", String.format("%03d", index + 1))
                 .replace("{num}", String.format("%03d", index + 1))
                 .replace("{日期}", now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd")))
                 .replace("{date}", now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd")))
                 .replace("{时间}", now.format(DateTimeFormatter.ofPattern("HHmmss")))
                 .replace("{年}", now.format(DateTimeFormatter.ofPattern("yyyy")))
                 .replace("{月}", now.format(DateTimeFormatter.ofPattern("MM")))
                 .replace("{日}", now.format(DateTimeFormatter.ofPattern("dd")));
            if (original.contains("{ext}") || original.contains("{扩展}")) ext = "";
            stem = t;
        }
        return new String[] {stem, ext};
    }

    private static boolean keepChar(char c, List<String> types) {
        if (types.contains("space") && Character.isWhitespace(c)) return false;
        if (types.contains("digit") && Character.isDigit(c)) return false;
        if (types.contains("lower") && Character.isLowerCase(c) && c < 128) return false;
        if (types.contains("upper") && Character.isUpperCase(c) && c < 128) return false;
        if (types.contains("chinese") && CHINESE_RE.matcher(String.valueOf(c)).find()) return false;
        if (types.contains("special") && SPECIAL_RE.matcher(String.valueOf(c)).find()) return false;
        return true;
    }

    // ---------- 预览冲突（Python _refresh） ----------

    /**
     * 与 Python _refresh 一致：空/./.. 主名回退；冲突时在扩展名前插 _N。
     *
     * <p>Python 的跨项撞名判定按「列表索引身份」（{@code o is not item}），
     * 不是按字符串——两个同名文件会互相触发冲突，双双加序号。
     * 另：规则把不同原名改成同名的情况 Python 不去重（used 表只在检测到冲突时
     * 才登记），移植保留该行为（bug 级一致，见路线图已知怪癖）。
     *
     * @param originalBasenames 全部文件名（按索引对齐）
     * @param selfIndex         当前项在列表中的索引
     */
    public static String resolveNewName(String originalName, RenameConfig cfg, int index,
                                        LocalDateTime now, boolean conflictOn,
                                        Map<String, Integer> used, List<String> originalBasenames,
                                        int selfIndex) {
        String[] parts = splitName(originalName, cfg.options().ignoreExt());
        String stem = parts[0], ext = parts[1];
        String[] applied = applyRules(stem, ext, cfg, index, now);
        String newStem = applied[0];
        String newExt = applied[1];
        if (newStem.isEmpty() || ".".equals(newStem) || "..".equals(newStem)) {
            newStem = !stem.isEmpty() ? stem : "未命名";
        }
        String newName = joinName(newStem, newExt);
        if (conflictOn) {
            String key = newName.toLowerCase();
            boolean hitOther = false;
            for (int i = 0; i < originalBasenames.size(); i++) {
                if (i == selfIndex) continue;
                if (originalBasenames.get(i).equals(newName)) { hitOther = true; break; }
            }
            if (used.containsKey(key) || hitOther) {
                int count = used.getOrDefault(key, 0) + 1;
                used.put(key, count);
                int dot = newName.lastIndexOf('.');
                String base = dot > 0 ? newName.substring(0, dot) : newName;
                String e = dot > 0 ? newName.substring(dot) : "";
                newName = base + "_" + count + e;
            }
        }
        return newName;
    }
}
