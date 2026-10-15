package cn.fmjj.toolbox.engine.rename;

import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.AddCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.DelCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.NumCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.OptionsCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.RenameConfig;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.ReplaceCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.WholeCfg;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 批量重命名规则一致性测试。
 * 期望值全部由 Python 原版 apply_rules（固定 now=2026-10-15 13:30:45）生成。
 */
class BatchRenameEngineTest {

    private static final LocalDateTime NOW = LocalDateTime.of(2026, 10, 15, 13, 30, 45);

    private static RenameConfig cfg(WholeCfg w, ReplaceCfg r, AddCfg a, DelCfg d, NumCfg n, String t) {
        return new RenameConfig(w, r, a, d, n, t, new OptionsCfg(true, true));
    }

    private static WholeCfg whole(String caseMode, String num, String convert, String extCase, String newExt) {
        return new WholeCfg(caseMode, num, convert, extCase, newExt);
    }

    private static ReplaceCfg replace(String find, String to, boolean all, boolean regex, boolean cs) {
        return new ReplaceCfg(find, to, all, regex, cs);
    }

    private static AddCfg add(String prefix, String suffix, String posText, int posIdx, boolean fromRight,
                              String kw, boolean kwAfter, String kwText) {
        return new AddCfg(prefix, suffix, posText, posIdx, fromRight, kw, kwAfter, kwText);
    }

    private static DelCfg del(List<String> types, int pos, int cnt, boolean fromRight,
                              String kw, boolean dirRight, int kwCnt, boolean inclKw) {
        return new DelCfg(types, pos, cnt, fromRight, kw, dirRight, kwCnt, inclKw);
    }

    private static NumCfg num(boolean on, int start, int step, int width, String mode) {
        return new NumCfg(on, start, step, width, mode);
    }

    private static final AddCfg NO_ADD = add("", "", "", 0, false, "", true, "");
    private static final DelCfg NO_DEL = del(List.of(), 0, 0, false, "", true, 0, false);
    private static final NumCfg NO_NUM = num(false, 1, 1, 1, "suffix");
    private static final ReplaceCfg NO_REPLACE = replace("", "", true, false, false);

    @Test
    @DisplayName("整体：大小写 / 全半角 / 扩展名操作")
    void wholeRules() {
        assertArrayEquals(new String[] {"HELLO WORLD", "flv"},
                BatchRenameEngine.applyRules("hello world", "flv",
                        cfg(whole("upper", "", "", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
        assertArrayEquals(new String[] {"Hello world", "flv"},
                BatchRenameEngine.applyRules("hello world", "flv",
                        cfg(whole("capital", "", "", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 1, NOW));
        // 全角→半角（Python: ＡＢＣ１２３ -> ABC123）
        assertArrayEquals(new String[] {"ABC123", "txt"},
                BatchRenameEngine.applyRules("ＡＢＣ１２３", "txt",
                        cfg(whole("", "", "full2half", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
        // 半角→全角（Python: LOVE录播 2026 -> ＬＯＶＥ录播 ２０２６；2026-06-08 录播 -> ２０２６－０６－０８ 录播）
        assertArrayEquals(new String[] {"ＬＯＶＥ录播 ２０２６", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4",
                        cfg(whole("", "", "half2full", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
        assertArrayEquals(new String[] {"ｈｅｌｌｏ ｗｏｒｌｄ", "flv"},
                BatchRenameEngine.applyRules("hello world", "flv",
                        cfg(whole("", "", "half2full", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
        assertArrayEquals(new String[] {"２０２６－０６－０８ 录播", "mp4"},
                BatchRenameEngine.applyRules("2026-06-08 录播", "mp4",
                        cfg(whole("", "", "half2full", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
        // 扩展名：lower + 新扩展名（lstrip('.')）；delete 清空
        assertArrayEquals(new String[] {"LOVE录播 2026", "MP4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4",
                        cfg(whole("", "", "", "lower", ".MP4"), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
        assertArrayEquals(new String[] {"abc", ""},
                BatchRenameEngine.applyRules("abc", "FLV",
                        cfg(whole("", "", "", "delete", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM, ""), 0, NOW));
    }

    @Test
    @DisplayName("替换：文本忽略大小写 / 区分大小写首个 / 正则")
    void replaceRules() {
        RenameConfig ci = cfg(whole("", "", "", "", ""),
                replace("LOVE", "爱", true, false, false), NO_ADD, NO_DEL, NO_NUM, "");
        assertArrayEquals(new String[] {"爱录播 2026", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", ci, 0, NOW));

        RenameConfig firstCs = cfg(whole("", "", "", "", ""),
                replace("a", "X", false, false, true), NO_ADD, NO_DEL, NO_NUM, "");
        assertArrayEquals(new String[] {"Xbc", "FLV"},
                BatchRenameEngine.applyRules("abc", "FLV", firstCs, 0, NOW));

        RenameConfig regex = cfg(whole("", "", "", "", ""),
                replace("\\d+", "#", true, true, false), NO_ADD, NO_DEL, NO_NUM, "");
        assertArrayEquals(new String[] {"LOVE录播 #", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", regex, 0, NOW));
        assertArrayEquals(new String[] {"ＡＢＣ#", "txt"},
                BatchRenameEngine.applyRules("ＡＢＣ１２３", "txt", regex, 0, NOW));
        assertArrayEquals(new String[] {"#-#-# 录播", "mp4"},
                BatchRenameEngine.applyRules("2026-06-08 录播", "mp4", regex, 0, NOW));

        // 非法正则：原样保留（Python re.error → pass）
        RenameConfig badRegex = cfg(whole("", "", "", "", ""),
                replace("[", "x", true, true, false), NO_ADD, NO_DEL, NO_NUM, "");
        assertArrayEquals(new String[] {"abc[", "FLV"},
                BatchRenameEngine.applyRules("abc[", "FLV", badRegex, 0, NOW));
    }

    @Test
    @DisplayName("添加：前后缀 + 指定位置 + 关键词后（顺序敏感）")
    void addRules() {
        RenameConfig ops = cfg(whole("", "", "", "", ""), NO_REPLACE,
                add("【囧菌】", "_end", "MID", 3, false, "", true, ""), NO_DEL, NO_NUM, "");
        // Python: 先前后缀再按位置插入 → 【囧菌MID】LOVE录播 2026_end
        assertArrayEquals(new String[] {"【囧菌MID】LOVE录播 2026_end", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", ops, 0, NOW));

        RenameConfig kw = cfg(whole("", "", "", "", ""), NO_REPLACE,
                add("", "", "", 0, false, "录播", true, "_中"), NO_DEL, NO_NUM, "");
        assertArrayEquals(new String[] {"LOVE录播_中 2026", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", kw, 2, NOW));
        assertArrayEquals(new String[] {"封茗囧菌录播_中合集", "mp4"},
                BatchRenameEngine.applyRules("封茗囧菌录播合集", "mp4", kw, 2, NOW));
    }

    @Test
    @DisplayName("删除：字符类型 / 位置 / 关键词")
    void delRules() {
        RenameConfig types = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD,
                del(List.of("space", "digit", "chinese"), 0, 0, false, "", true, 0, false), NO_NUM, "");
        assertArrayEquals(new String[] {"LOVE", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", types, 0, NOW));
        assertArrayEquals(new String[] {"helloworld", "flv"},
                BatchRenameEngine.applyRules("hello world", "flv", types, 0, NOW));
        assertArrayEquals(new String[] {"ＡＢＣ", "txt"},
                BatchRenameEngine.applyRules("ＡＢＣ１２３", "txt", types, 0, NOW));
        assertArrayEquals(new String[] {"", "mp4"},
                BatchRenameEngine.applyRules("封茗囧菌录播合集", "mp4", types, 0, NOW));
        assertArrayEquals(new String[] {"--", "mp4"},
                BatchRenameEngine.applyRules("2026-06-08 录播", "mp4", types, 0, NOW));

        RenameConfig pos = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD,
                del(List.of(), 2, 3, false, "", true, 0, false), NO_NUM, "");
        assertArrayEquals(new String[] {"LO播 2026", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", pos, 0, NOW));
        assertArrayEquals(new String[] {"he world", "flv"},
                BatchRenameEngine.applyRules("hello world", "flv", pos, 0, NOW));
        assertArrayEquals(new String[] {"ＡＢ３", "txt"},
                BatchRenameEngine.applyRules("ＡＢＣ１２３", "txt", pos, 0, NOW));
        assertArrayEquals(new String[] {"2006-08 录播", "mp4"},
                BatchRenameEngine.applyRules("2026-06-08 录播", "mp4", pos, 0, NOW));

        RenameConfig kw = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD,
                del(List.of(), 0, 0, false, "录播", true, 2, false), NO_NUM, "");
        assertArrayEquals(new String[] {"LOVE录播026", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", kw, 0, NOW));
        assertArrayEquals(new String[] {"封茗囧菌录播", "mp4"},
                BatchRenameEngine.applyRules("封茗囧菌录播合集", "mp4", kw, 0, NOW));
    }

    @Test
    @DisplayName("序号：后缀/前缀 + 起始/步长/位宽")
    void numRules() {
        RenameConfig suffix = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD, NO_DEL,
                num(true, 5, 3, 4, "suffix"), "");
        assertArrayEquals(new String[] {"LOVE录播 20260011", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", suffix, 2, NOW));

        RenameConfig prefix = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD, NO_DEL,
                num(true, 1, 1, 2, "prefix"), "");
        assertArrayEquals(new String[] {"08_LOVE录播 2026", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", prefix, 7, NOW));
    }

    @Test
    @DisplayName("模板：{名}{序号}{日期}{时间}（now 固定）")
    void templateRules() {
        RenameConfig t = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM,
                "录播_{序号}_{日期}_{时间}_{名}");
        assertArrayEquals(new String[] {"录播_005_2026-10-15_133045_LOVE录播 2026", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", t, 4, NOW));

        // 模板含 {ext} 时扩展名被消费（ext 置空）
        RenameConfig tExt = cfg(whole("", "", "", "", ""), NO_REPLACE, NO_ADD, NO_DEL, NO_NUM,
                "{名}-{序号}-{ext}");
        assertArrayEquals(new String[] {"abc-001-mp4", ""},
                BatchRenameEngine.applyRules("abc", "mp4", tExt, 0, NOW));
    }

    @Test
    @DisplayName("组合规则：lower + 删空格 + 序号（叠加顺序）")
    void comboRules() {
        RenameConfig combo = cfg(whole("lower", "", "", "", ""), NO_REPLACE, NO_ADD,
                del(List.of("space"), 0, 0, false, "", true, 0, false),
                num(true, 100, 10, 3, "suffix"), "");
        assertArrayEquals(new String[] {"love录播2026110", "mp4"},
                BatchRenameEngine.applyRules("LOVE录播 2026", "mp4", combo, 1, NOW));
        assertArrayEquals(new String[] {"2026-06-08录播110", "mp4"},
                BatchRenameEngine.applyRules("2026-06-08 录播", "mp4", combo, 1, NOW));
    }

    @Test
    @DisplayName("预览冲突：同名双加序号 / 规则撞名不去重（Python _refresh 原样语义）")
    void conflictResolution() {
        // Python 真值：两个同名文件互相触发冲突 → a_1 / a_2；b 不撞
        RenameConfig noop = RenameConfig.empty();
        List<String> originals = List.of("a.mp4", "a.mp4", "b.mp4");
        Map<String, Integer> used = new LinkedHashMap<>();
        List<String> results = new ArrayList<>();
        for (int i = 0; i < originals.size(); i++) {
            results.add(BatchRenameEngine.resolveNewName(
                    originals.get(i), noop, i, NOW, true, used, originals, i));
        }
        assertEquals(List.of("a_1.mp4", "a_2.mp4", "b.mp4"), results);

        // Python 真值：规则把 x1/x2 都改成 x.mp4 —— 原版不去重（used 表只在撞已登记名时生效）
        Map<String, Integer> used2 = new LinkedHashMap<>();
        List<String> originals2 = List.of("x1.mp4", "x2.mp4");
        List<String> results2 = new ArrayList<>();
        RenameConfig same = cfg(whole("", "", "", "", ""),
                replace("\\d", "", true, true, false), NO_ADD, NO_DEL, NO_NUM, "");
        for (int i = 0; i < originals2.size(); i++) {
            results2.add(BatchRenameEngine.resolveNewName(
                    originals2.get(i), same, i, NOW, true, used2, originals2, i));
        }
        assertEquals(List.of("x.mp4", "x.mp4"), results2);

        // 三个同名 → a_1 / a_2 / a_3
        Map<String, Integer> used4 = new LinkedHashMap<>();
        List<String> originals4 = List.of("a.mp4", "a.mp4", "a.mp4");
        List<String> results4 = new ArrayList<>();
        for (int i = 0; i < originals4.size(); i++) {
            results4.add(BatchRenameEngine.resolveNewName(
                    originals4.get(i), noop, i, NOW, true, used4, originals4, i));
        }
        assertEquals(List.of("a_1.mp4", "a_2.mp4", "a_3.mp4"), results4);

        // 主名为空回退：删除所有字符后回退到原主名（Python: new_stem = stem or "未命名"）
        Map<String, Integer> used3 = new LinkedHashMap<>();
        String r = BatchRenameEngine.resolveNewName("abc", cfg(whole("", "", "", "", ""), NO_REPLACE,
                        NO_ADD, del(List.of("lower"), 0, 0, false, "", true, 0, false), NO_NUM, ""),
                0, NOW, true, used3, List.of("abc"), 0);
        assertEquals("abc", r);
    }

    @Test
    @DisplayName("split/join 与 ignore_ext=false")
    void splitJoin() {
        assertArrayEquals(new String[] {"a", "b"}, BatchRenameEngine.splitName("a.b", true));
        assertArrayEquals(new String[] {"a.b", ""}, BatchRenameEngine.splitName("a.b", false));
        assertArrayEquals(new String[] {"noext", ""}, BatchRenameEngine.splitName("noext", true));
        assertEquals("a.b", BatchRenameEngine.joinName("a", "b"));
        assertEquals("a", BatchRenameEngine.joinName("a", ""));
    }
}
