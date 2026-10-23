package cn.fmjj.toolbox.engine.rename;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 录播命名规则测试。 */
class RenameRuleTest {

    @Test
    @DisplayName("build：日期_时间_账户_类型_弹幕_平台_标题 + 后缀")
    void buildFull() {
        var p = new RenameRule.Parts("2026-10-23", "18-30-00", "封茗囧菌",
                List.of("聊天", "游戏"), true, "B站", "生日会");
        assertEquals("2026-10-23_18-30-00_封茗囧菌_聊天+游戏_有弹幕_B站_生日会.mp4",
                RenameRule.build(p, ".mp4"));
    }

    @Test
    @DisplayName("build：空字段跳过（无标题/无平台/无类型）")
    void buildPartial() {
        var p = new RenameRule.Parts("2026-10-23", "", "A", List.of(), false, "", "");
        assertEquals("2026-10-23_A_无弹幕.mp4", RenameRule.build(p, ".mp4"));
    }

    @Test
    @DisplayName("build：类型顺序按传入顺序（用户点击顺序）")
    void typeOrder() {
        var p = new RenameRule.Parts("", "", "A", List.of("游戏", "聊天"), true, "", "");
        assertEquals("A_游戏+聊天_有弹幕.flv", RenameRule.build(p, ".flv"));
    }

    @Test
    @DisplayName("build：标题里的非法字符被替换")
    void cleanTitle() {
        var p = new RenameRule.Parts("", "", "A", List.of(), true, "", "a/b:c*d?");
        assertEquals("A_有弹幕_a_b_c_d_.mp4", RenameRule.build(p, ".mp4"));
    }

    @Test
    @DisplayName("parseDateTime：紧凑与分隔两种格式")
    void parseDateTime() {
        assertEquals("2026-10-23", RenameRule.parseDateTime("20261023_183000_a.mp4")[0]);
        assertEquals("18-30-00", RenameRule.parseDateTime("20261023_183000_a.mp4")[1]);
        assertEquals("2026-10-23", RenameRule.parseDateTime("2026-10-23 18-30-00.mp4")[0]);
        assertEquals("18-30-00", RenameRule.parseDateTime("2026-10-23 18-30-00.mp4")[1]);
        assertEquals("", RenameRule.parseDateTime("没有日期.mp4")[0]);
        assertEquals("", RenameRule.parseDateTime(null)[0]);
    }

    @Test
    @DisplayName("guessAccount：跳过纯数字段")
    void guessAccount() {
        assertEquals("封茗囧菌", RenameRule.guessAccount("20261023_封茗囧菌_歌回.mp4"));
        assertEquals("abc", RenameRule.guessAccount("abc_123.mp4"));
        assertEquals("", RenameRule.guessAccount("123_456.mp4"));
        assertTrue(RenameRule.DEFAULT_LIVE_TYPES.contains("聊天"));
    }
}
