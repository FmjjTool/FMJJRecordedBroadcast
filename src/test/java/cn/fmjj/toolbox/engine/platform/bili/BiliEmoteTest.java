package cn.fmjj.toolbox.engine.platform.bili;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** B站表情包解析测试（离线 fixture，结构取自真实接口）。 */
class BiliEmoteTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private static final String DATA = """
            {"packages":[
              {"text":"装扮收藏集-同人","emote":[
                {"text":"[doge]","url":"//i0.hdslb.com/bfs/garb/a.png","gif_url":"//i0.hdslb.com/bfs/garb/a.gif"},
                {"text":"[笑]","url":"//i0.hdslb.com/bfs/garb/b.png","gif_url":""}
              ]},
              {"text":"小黄脸","emote":[
                {"text":"[微笑]","url":"//i0.hdslb.com/bfs/emote/c.png","gif_url":""}
              ]},
              {"text":"空包","emote":[]}
            ]}
            """;

    @Test
    @DisplayName("parse：跳过空包，补 https，GIF 优先")
    void parse() throws Exception {
        var pkgs = BiliEmote.parse(JSON.readTree(DATA));
        assertEquals(2, pkgs.size(), "空包应被跳过");
        assertEquals("装扮收藏集-同人", pkgs.get(0).name());
        assertEquals(2, pkgs.get(0).emotes().size());

        var doge = pkgs.get(0).emotes().get(0);
        assertEquals("[doge]", doge.text());
        assertEquals("https://i0.hdslb.com/bfs/garb/a.png", doge.url());
        assertEquals("https://i0.hdslb.com/bfs/garb/a.gif", doge.gifUrl());
        assertEquals("https://i0.hdslb.com/bfs/garb/a.gif", doge.bestUrl(), "有 GIF 时优先 GIF");

        var smile = pkgs.get(0).emotes().get(1);
        assertEquals("https://i0.hdslb.com/bfs/garb/b.png", smile.bestUrl(), "无 GIF 时用静态图");
    }

    @Test
    @DisplayName("flatten / search：扁平化与关键词过滤")
    void flattenAndSearch() throws Exception {
        var pkgs = BiliEmote.parse(JSON.readTree(DATA));
        assertEquals(3, BiliEmote.flatten(pkgs).size());

        assertEquals(1, BiliEmote.search(pkgs, "doge").size());
        assertEquals(2, BiliEmote.search(pkgs, "收藏集").size(), "按包名匹配");
        assertEquals(3, BiliEmote.search(pkgs, "").size(), "空关键词返回全部");
        assertTrue(BiliEmote.search(pkgs, "不存在").isEmpty());
    }

    @Test
    @DisplayName("safeName：去 [ ] 等非法字符，按 URL 判扩展名")
    void safeName() throws Exception {
        var pkgs = BiliEmote.parse(JSON.readTree(DATA));
        var doge = pkgs.get(0).emotes().get(0);
        assertEquals("doge.gif", BiliEmote.safeName(doge, 0));

        var smile = pkgs.get(0).emotes().get(1);
        assertEquals("笑.png", BiliEmote.safeName(smile, 1));
    }
}
