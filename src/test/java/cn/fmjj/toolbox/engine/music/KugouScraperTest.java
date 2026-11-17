package cn.fmjj.toolbox.engine.music;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 酷狗搜索解析一致性测试（字段映射对齐 Python music/scrapers/kugou.py）。
 */
class KugouScraperTest {

    @Test
    void parseSearchMatchesPythonMapping() throws Exception {
        JsonNode d = new ObjectMapper().readTree("""
                {"status":1,"data":{"info":[{
                  "hash":"3caece70f7df3de374c87851d9656a6d",
                  "songname":"<em>霜雪千年</em>",
                  "singername":"双笙 (陈元汐)、封茗囧菌",
                  "album_name":"霜雪千年",
                  "duration":246,
                  "imgUrl":"http://imge.kugou.com/{size}/x.jpg"
                }]}}
                """);
        List<MusicSong> songs = KugouScraper.parseSearch(d);
        assertEquals(1, songs.size());
        MusicSong s = songs.get(0);
        assertEquals("kugou", s.platform());
        assertEquals("3caece70f7df3de374c87851d9656a6d", s.songId());
        assertEquals("霜雪千年", s.songName());
        assertEquals("双笙 (陈元汐)、封茗囧菌", s.singer());
        assertEquals("霜雪千年", s.albumName());
        assertEquals(246, s.durationSeconds());
        assertEquals("http://imge.kugou.com/480/x.jpg", s.coverUrl());
    }

    @Test
    void extractSongIdMatchesPython() {
        String hash = "3caece70f7df3de374c87851d9656a6d";
        assertEquals(hash, KugouScraper.extractSongId(
                "https://www.kugou.com/song/#hash=" + hash));
        assertEquals(hash, KugouScraper.extractSongId(hash));
        assertEquals("", KugouScraper.extractSongId("not-a-hash"));
    }
}
