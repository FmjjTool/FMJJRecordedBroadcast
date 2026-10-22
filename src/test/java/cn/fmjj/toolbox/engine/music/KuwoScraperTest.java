package cn.fmjj.toolbox.engine.music;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 酷我抓取器纯逻辑测试。
 * extractSongId / matchAny 的期望值来自 Python 原版运行时；
 * fromSearchItem 用真实接口返回结构的离线 fixture。
 */
class KuwoScraperTest {

    @Test
    @DisplayName("extract_song_id：四种链接形态 + 纯数字 + 非法")
    void extractSongId() {
        assertEquals("82239887", KuwoScraper.extractSongId("http://www.kuwo.cn/play_detail/82239887"));
        assertEquals("12345", KuwoScraper.extractSongId("https://www.kuwo.cn/yinyue/12345"));
        assertEquals("999", KuwoScraper.extractSongId("x?rid=999"));
        assertEquals("82239887", KuwoScraper.extractSongId("82239887"));
        assertEquals("", KuwoScraper.extractSongId("abc"));
        assertEquals("", KuwoScraper.extractSongId(""));
        assertEquals("", KuwoScraper.extractSongId(null));
    }

    @Test
    @DisplayName("match_any 与 Python 一致（最长片段必须命中 + 其余至少命中一个）")
    void matchAny() {
        assertTrue(KuwoScraper.matchAny("起风了", "起风了", "周深"));
        assertTrue(KuwoScraper.matchAny("起风了 周深", "起风了", "周深"));
        // 最长片段"起风了"命中歌名 → 通过（"周深"不必命中）
        assertTrue(KuwoScraper.matchAny("起风了 周深", "起风了", "买辣椒也用券"));
        assertTrue(KuwoScraper.matchAny("周深 大鱼", "大鱼", "周深"));
        assertFalse(KuwoScraper.matchAny("abc def", "xyz", "qqq"));
    }

    @Test
    @DisplayName("fromSearchItem：abslist 结构 → MusicSong（MUSIC_ 前缀剥离 / 封面升级 / 时长）")
    void fromSearchItem() throws Exception {
        String json = """
                {
                  "MUSICRID": "MUSIC_82239887",
                  "SONGNAME": "起风了",
                  "ARTIST": "买辣椒也用券",
                  "ALBUM": "起风了",
                  "ALBUMID": "123456",
                  "DURATION": "325",
                  "web_albumpic_short": "120/abc.jpg",
                  "PICPATH": ""
                }""";
        var item = new ObjectMapper().readTree(json);
        MusicSong song = KuwoScraper.fromSearchItem(item);
        assertEquals("kuwo", song.platform());
        assertEquals("82239887", song.songId());
        assertEquals("起风了", song.songName());
        assertEquals("买辣椒也用券", song.singer());
        assertEquals("起风了", song.albumName());
        assertEquals(325, song.durationSeconds());
        assertEquals("05:25", song.durationText());
        assertEquals("https://img1.kuwo.cn/star/albumcover/700/abc.jpg", song.coverUrl());
        assertEquals("http://www.kuwo.cn/play_detail/82239887", song.pageUrl());
        assertEquals("买辣椒也用券 - 起风了", song.fileNameBase());
    }

    @Test
    @DisplayName("fromSearchItem：&nbsp; 还原；&amp; 保持原样（原版 replace('&','&') 是 no-op，bug 级一致）")
    void fromSearchItemEdge() throws Exception {
        String json = """
                {"MUSICRID": "MUSIC_1", "NAME": "A&nbsp;B", "SINGER": "X&amp;Y"}""";
        MusicSong song = KuwoScraper.fromSearchItem(new ObjectMapper().readTree(json));
        assertEquals("A B", song.songName());
        assertEquals("X&amp;Y", song.singer());
        assertEquals(0, song.durationSeconds());
        assertEquals("—", song.durationText());
    }
}
