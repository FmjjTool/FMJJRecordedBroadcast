package cn.fmjj.toolbox.engine.platform.douyin;

import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 抖音指数格式化/解析跨语言一致性测试。
 * 期望值由 Python 原版 platforms/douyin_index.py 运行时生成。
 */
class DouyinIndexTest {

    @Test
    void fmtCountMatchesPython() {
        assertEquals("451.9万", DouyinIndex.fmtCount(4518751));
        assertEquals("3.5万", DouyinIndex.fmtCount(34594));
        assertEquals("387", DouyinIndex.fmtCount(387));
        assertEquals("1.00亿", DouyinIndex.fmtCount(100000000));
        assertEquals("0", DouyinIndex.fmtCount(0));
        assertEquals("-451.9万", DouyinIndex.fmtCount(-4518751));
        assertEquals("0", DouyinIndex.fmtCount(null));
        assertEquals("12", DouyinIndex.fmtCount("12"));
        assertEquals("9999", DouyinIndex.fmtCount(9999));
        assertEquals("1.0万", DouyinIndex.fmtCount(10000));
    }

    @Test
    void fmtPctMatchesPython() {
        assertEquals("+99.15%", DouyinIndex.fmtPct(0.9915));
        assertEquals("-5.00%", DouyinIndex.fmtPct(-0.05));
        assertEquals("0.00%", DouyinIndex.fmtPct(0));
        assertEquals("-", DouyinIndex.fmtPct(null));
        assertEquals("-", DouyinIndex.fmtPct("abc"));
        assertEquals("+100.00%", DouyinIndex.fmtPct(1.0));
        assertEquals("-0.04%", DouyinIndex.fmtPct(-0.0004));
    }

    @Test
    void parseDistMatchesPython() {
        com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
        JsonNode raw = mapper.getNodeFactory()
                .textNode("[{\"name\":\"male\",\"value\":0.5},{\"bad\":1},3]");
        List<JsonNode> items = DouyinIndex.parseDist(raw);
        assertEquals(2, items.size());
        assertEquals("male", items.get(0).path("name").asText());
        assertEquals(0.5, items.get(0).path("value").asDouble());

        assertTrue(DouyinIndex.parseDist(null).isEmpty());
        assertTrue(DouyinIndex.parseDist(mapper.getNodeFactory().textNode("not json")).isEmpty());
    }

    @Test
    void safeNameReplacesIllegalChars() {
        assertEquals("a_b_c", DouyinIndex.safeName("a/b:c"));
        assertEquals("未知达人", DouyinIndex.safeName("  "));
        assertEquals("封茗囧菌", DouyinIndex.safeName(" 封茗囧菌 "));
    }
}
