package cn.fmjj.toolbox.engine.lyrics;

import cn.fmjj.toolbox.engine.lyrics.LyricsEngine.ConvertResult;
import cn.fmjj.toolbox.engine.lyrics.LyricsEngine.LyricLine;
import cn.fmjj.toolbox.engine.lyrics.LyricsEngine.Word;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 歌词引擎跨语言一致性测试。
 * 期望值全部由 Python 原版 {@code src/toolbox/lyrics_engine.py} 生成。
 */
class LyricsEngineTest {

    private final LyricsEngine engine = new LyricsEngine();

    private static final String LRC = """
            [ti:标题]
            [ar:歌手]
            [00:01.50]第一句
            [00:02.25][01:03.5]第二句
            [00:00.00.500]毫秒句
            普通文字行
            """;

    /** Python: encode_krc(KRC_TEXT) 的字节流（hex）。 */
    private static final String KRC_HEX =
            "6b72633138dbea416a0244976034239c016418d6187834b41ef5a08711c5e8f7d0fa85acba197a4715f5b4c97aec358912c2a6e23df907bacd6f496847c01149cf2517734f";

    private static final String KRC_TEXT =
            "[1000,2000]测试(0,500)歌(0,500)词<0,500,0>\n[4000,1500]第二行<0,500,0>\n";

    private static byte[] hexToBytes(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    @Test
    @DisplayName("parse_lrc 与 Python 一致（元数据跳过、多时间戳同行、非法行跳过）")
    void parseLrc() {
        List<LyricLine> lines = engine.parseLrc(LRC.getBytes(StandardCharsets.UTF_8));
        assertEquals(3, lines.size());
        assertEquals(new LyricLine(1.5, 6.5, "第一句", List.of()), lines.get(0));
        assertEquals(new LyricLine(2.25, 7.25, "第二句", List.of()), lines.get(1));
        assertEquals(new LyricLine(63.5, 68.5, "第二句", List.of()), lines.get(2));
    }

    @Test
    @DisplayName("to_lrc / to_txt / to_srt 与 Python 逐字节一致（含署名行）")
    void generators() {
        List<LyricLine> lines = engine.parseLrc(LRC.getBytes(StandardCharsets.UTF_8));
        assertArrayEquals(("[00:00:00]-歌词文件由封茗囧菌数据汇总库收集-\n"
                + "[00:01.50]第一句\n[00:02.25]第二句\n[01:03.50]第二句\n"
                + "[00:00:00]-歌词文件由封茗囧菌数据汇总库收集-").getBytes(StandardCharsets.UTF_8),
                engine.toLrc(lines));
        assertArrayEquals(("-歌词文件由封茗囧菌数据汇总库收集-\n"
                + "第一句\n第二句\n第二句\n"
                + "-歌词文件由封茗囧菌数据汇总库收集-").getBytes(StandardCharsets.UTF_8),
                engine.toText(lines));
        assertArrayEquals(("1\n00:00:00,000 --> 00:00:00,000\n-歌词文件由封茗囧菌数据汇总库收集-\n\n"
                + "2\n00:00:01,500 --> 00:00:06,500\n第一句\n\n"
                + "3\n00:00:02,250 --> 00:00:07,250\n第二句\n\n"
                + "4\n00:01:03,500 --> 00:01:08,500\n第二句\n\n"
                + "5\n00:00:00,000 --> 00:00:00,000\n-歌词文件由封茗囧菌数据汇总库收集-\n")
                .getBytes(StandardCharsets.UTF_8), engine.toSrt(lines));
    }

    @Test
    @DisplayName("KRC：解密 Python 生成的密文 + 自编码往返一致")
    void krc() {
        assertEquals(KRC_TEXT, engine.decodeKrc(hexToBytes(KRC_HEX)));
        // 编码侧不要求与 Python 字节级一致（zlib 压缩器实现差异），
        // 跨语言兼容性由上一行的「Java 解 Python 密文」保证；
        // 自产 KRC 只需 Java 自身可解往返。
        assertEquals(KRC_TEXT, engine.decodeKrc(engine.encodeKrc(KRC_TEXT)));
        // 长度 >4 一律跳过前 4 字节，非 zlib 数据走兜底直解（Python 对 b"plain" 返回 "."）
        assertEquals(".", engine.decodeKrc("plain".getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    @DisplayName("parse_krc：逐字时间拆解与纯文本提取")
    void parseKrc() {
        List<LyricLine> lines = engine.parseKrc(hexToBytes(KRC_HEX));
        assertEquals(2, lines.size());
        assertEquals(new LyricLine(1.0, 3.0, "测试(0,500)歌(0,500)词", List.of(new Word(0, 500))), lines.get(0));
        assertEquals(new LyricLine(4.0, 5.5, "第二行", List.of(new Word(0, 500))), lines.get(1));
    }

    @Test
    @DisplayName("parse_ksc / parse_qrc / parse_plain 与 Python 一致")
    void parseOthers() {
        List<LyricLine> ksc = engine.parseKsc("[00:01.50]<0,500,0>歌<500,500,0>词\n".getBytes(StandardCharsets.UTF_8));
        assertEquals(List.of(new LyricLine(1.5, 2.5, "歌词", List.of(new Word(0, 500), new Word(500, 500)))), ksc);

        List<LyricLine> qrc = engine.parseQrc("[1000,2000]QQ(0,500)音(0,500)乐(0,500)\n".getBytes(StandardCharsets.UTF_8));
        assertEquals(List.of(new LyricLine(1.0, 3.0, "QQ音乐",
                List.of(new Word(0, 500), new Word(0, 500), new Word(0, 500)))), qrc);

        List<LyricLine> plain = engine.parsePlain("行一\n行二\n".getBytes(StandardCharsets.UTF_8));
        assertEquals(List.of(new LyricLine(0.0, 0.0, "行一", List.of()),
                new LyricLine(0.0, 0.0, "行二", List.of())), plain);
    }

    @Test
    @DisplayName("to_ksc：无逐字时整句一字 + 署名行（与 Python 输出一致）")
    void toKsc() {
        byte[] out = engine.toKsc(engine.parseLrc("[00:01.00]一二三\n".getBytes(StandardCharsets.UTF_8)));
        assertArrayEquals(("[00:00:00]-歌词文件由封茗囧菌数据汇总库收集-\n"
                + "[00:01.00]一二三<5000,0,0>\n"
                + "[00:00:00]-歌词文件由封茗囧菌数据汇总库收集-").getBytes(StandardCharsets.UTF_8), out);
    }

    @Test
    @DisplayName("convert：按扩展名路由 + 输出命名 _歌词.txt")
    void convert() {
        ConvertResult r = engine.convert("song.lrc", LRC.getBytes(StandardCharsets.UTF_8), "txt");
        assertTrue(r.success(), r.message());
        assertEquals("song_歌词.txt", r.outputFiles().get(0).filename());
        String txt = new String(r.outputFiles().get(0).content(), StandardCharsets.UTF_8);
        assertTrue(txt.startsWith("-歌词文件由封茗囧菌数据汇总库收集-\n第一句"), txt);
        assertTrue(txt.endsWith("第二句\n-歌词文件由封茗囧菌数据汇总库收集-"), txt);

        ConvertResult q = engine.convert("song.qrc",
                "[1000,2000]QQ音乐(0,500)(500,500)(1000,500)(1500,500)\n".getBytes(StandardCharsets.UTF_8), "lrc");
        assertTrue(q.success());
        assertEquals("song.lrc", q.outputFiles().get(0).filename());

        assertFalse(engine.convert("song.lrc", LRC.getBytes(StandardCharsets.UTF_8), "ogg").success());
    }

    @Test
    @DisplayName("fmtLrcTime half-even：61.125 -> 01:01.12")
    void timeFormatting() {
        byte[] out = engine.toLrc(List.of(new LyricLine(61.125, 66.125, "x", List.of())));
        assertTrue(new String(out, StandardCharsets.UTF_8).contains("[01:01.12]x"),
                new String(out, StandardCharsets.UTF_8));
    }
}
