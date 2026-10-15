package cn.fmjj.toolbox.engine.subtitle;

import cn.fmjj.toolbox.engine.subtitle.SubtitleEngine.ConvertResult;
import cn.fmjj.toolbox.engine.subtitle.SubtitleEngine.Cue;
import cn.fmjj.toolbox.engine.subtitle.SubtitleEngine.OutputFile;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 字幕引擎跨语言一致性测试。
 * 期望值全部由 Python 原版 {@code src/toolbox/subtitle_engine.py} 生成。
 */
class SubtitleEngineTest {

    private final SubtitleEngine engine = new SubtitleEngine();

    private static final String SRT =
            "1\n00:00:01,000 --> 00:00:03,500\n第一行字幕\n\n2\n00:00:04,000 --> 00:00:06,250\n第二行\n第二行续\n";

    private static final String BCC = """
            {"font_size":0.5,"font_color":"#FFFFFF","body":[{"from":1.0,"to":3.5,"content":" BCC第一行 "},{"from":4.0,"to":6.25,"content":""}]}""";

    @Test
    @DisplayName("detect_format: srt / bcc")
    void detectFormat() {
        assertEquals("srt", engine.detectFormat("a.srt", SRT.getBytes(StandardCharsets.UTF_8)));
        assertEquals("bcc", engine.detectFormat("a.json", BCC.getBytes(StandardCharsets.UTF_8)));
        assertEquals("json", engine.detectFormat("a.json", "{}".getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    @DisplayName("parse_srt 与 Python 一致")
    void parseSrt() {
        List<Cue> cues = engine.parseSrt(SRT.getBytes(StandardCharsets.UTF_8));
        assertEquals(2, cues.size());
        assertEquals(new Cue(1.0, 3.5, "第一行字幕"), cues.get(0));
        assertEquals(new Cue(4.0, 6.25, "第二行\n第二行续"), cues.get(1));
    }

    @Test
    @DisplayName("generate: text / lrc / srt / timed_text / ass 全部对齐 Python 输出")
    void generateAll() {
        List<Cue> cues = engine.parseSrt(SRT.getBytes(StandardCharsets.UTF_8));
        assertArrayEquals("第一行字幕\n第二行\n第二行续".getBytes(StandardCharsets.UTF_8), engine.generateText(cues));
        assertArrayEquals("[00:01.00]第一行字幕\n[00:04.00]第二行\n第二行续".getBytes(StandardCharsets.UTF_8),
                engine.generateLrc(cues));
        assertArrayEquals(SRT.getBytes(StandardCharsets.UTF_8), engine.generateSrt(cues));
        assertArrayEquals("[00:01.00 --> 00:03.50] 第一行字幕\n[00:04.00 --> 00:06.25] 第二行\n第二行续"
                .getBytes(StandardCharsets.UTF_8), engine.generateTimedText(cues));
        String ass = new String(engine.generateAss(cues), StandardCharsets.UTF_8);
        assertTrue(ass.startsWith("[Script Info]\nTitle: Converted Subtitle\n"), ass);
        assertTrue(ass.contains("Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,第一行字幕"), ass);
        assertTrue(ass.endsWith("第二行\n第二行续"), ass);
    }

    @Test
    @DisplayName("parse_bcc 与 Python 一致（空 content 跳过）")
    void parseBcc() {
        List<Cue> cues = engine.parseBcc(BCC.getBytes(StandardCharsets.UTF_8));
        assertEquals(List.of(new Cue(1.0, 3.5, "BCC第一行")), cues);
        assertArrayEquals("[00:01.00]BCC第一行".getBytes(StandardCharsets.UTF_8), engine.generateLrc(cues));
    }

    @Test
    @DisplayName("parse_text 每行 2 秒间隔")
    void parseText() {
        List<Cue> cues = engine.parseText("甲\n乙丙\n".getBytes(StandardCharsets.UTF_8));
        assertEquals(List.of(new Cue(0.0, 2.0, "甲"), new Cue(2.0, 4.0, "乙丙")), cues);
    }

    @Test
    @DisplayName("时间格式化：half-even 舍入复刻 Python（61.125 -> 01:01.12，不是 .13）")
    void timeFormatting() {
        assertEquals("01:01.12", engine.secondsToTimeStr(61.125, "lrc"));
        assertEquals("01:02:05,500", engine.secondsToTimeStr(3725.5, "srt"));
        assertEquals("0:00:01.50", engine.secondsToTimeStr(1.5, "ass"));
        assertEquals("01:01.12", engine.secondsToTimeStr(61.125, "timed_text"));
        assertEquals(1.0, engine.timeStrToSeconds("00:00:01,000", "srt"));
        assertEquals(3.5, engine.timeStrToSeconds("00:00:03,500", "srt"));
        assertEquals(0.0, engine.timeStrToSeconds("junk", "srt"));
    }

    @Test
    @DisplayName("convert_file 全流程：命名规则（Python 实测：无空格后缀直接拼接）")
    void convertFile() {
        ConvertResult r = engine.convertFile("a.srt", SRT.getBytes(StandardCharsets.UTF_8),
                List.of("text", "timed_text", "lrc", "ass", "srt"), "original");
        assertTrue(r.success(), r.message());
        assertEquals(5, r.outputFiles().size());
        // Python 实测输出名（suffix 含空格才会插下划线，而后缀从不含空格）：
        // ['a纯文字.txt', 'a带时间轴.txt', 'a.lrc', 'a.ass', 'a.srt']
        assertEquals("a纯文字.txt", r.outputFiles().get(0).filename());
        assertEquals("a带时间轴.txt", r.outputFiles().get(1).filename());
        assertEquals("a.lrc", r.outputFiles().get(2).filename());
        assertEquals("a.ass", r.outputFiles().get(3).filename());
        assertEquals("a.srt", r.outputFiles().get(4).filename());
    }

    @Test
    @DisplayName("convert_file：date 模式加时间后缀；不支持的格式报错")
    void convertFileEdgeCases() {
        ConvertResult r = engine.convertFile("a.srt", SRT.getBytes(StandardCharsets.UTF_8),
                List.of("lrc"), "date");
        assertTrue(r.success());
        assertTrue(r.outputFiles().get(0).filename().matches("a_\\d{8}_\\d{6}\\.lrc"),
                r.outputFiles().get(0).filename());

        ConvertResult bad = engine.convertFile("a.mp4", SRT.getBytes(StandardCharsets.UTF_8),
                List.of("lrc"), "original");
        assertFalse(bad.success());
        assertTrue(bad.message().startsWith("不支持的输入格式:"), bad.message());

        ConvertResult empty = engine.convertFile("a.json", "{\"body\":[]}".getBytes(StandardCharsets.UTF_8),
                List.of("lrc"), "original");
        assertFalse(empty.success());
        assertEquals("未解析到字幕内容", empty.message());

        for (OutputFile f : r.outputFiles()) assertTrue(f.content().length > 0);
    }
}
