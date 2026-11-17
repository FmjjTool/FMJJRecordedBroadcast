package cn.fmjj.toolbox.engine.music;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 酷狗概念版签名/字段映射测试。
 * 期望值由 Python 版 music/scrapers/kugou_lite.py 的同名函数算出。
 */
class KugouLiteScraperTest {

    @Test
    void androidSignatureMatchesPython() {
        Map<String, String> p = new LinkedHashMap<>();
        p.put("dfid", "-");
        p.put("mid", "123");
        p.put("uuid", "-");
        p.put("appid", "3116");
        p.put("clientver", "11440");
        p.put("clienttime", "1700000000");
        p.put("hash", "abc");
        assertEquals("d4cd6ab370e5a2efd09691c5ca35d79f", KugouLiteScraper.signAndroid(p, ""));
    }

    @Test
    void androidSignatureIncludesBody() {
        Map<String, String> p = new LinkedHashMap<>();
        p.put("dfid", "-");
        p.put("mid", "123");
        p.put("uuid", "-");
        p.put("appid", "3116");
        p.put("clientver", "11440");
        p.put("clienttime", "1700000000");
        String body = "{\"ad_id\":12307537187,\"play_end\":1700000030000,\"play_start\":1700000000000}";
        assertEquals("a6d590ab8fc51b63365d36c675e60280", KugouLiteScraper.signAndroid(p, body));
    }

    @Test
    void webSignatureMatchesPython() {
        Map<String, String> p = new LinkedHashMap<>();
        p.put("dfid", "-");
        p.put("mid", "123");
        p.put("uuid", "-");
        p.put("appid", "1001");
        p.put("clientver", "11440");
        p.put("clienttime", "1700000000");
        p.put("type", "1");
        p.put("plat", "4");
        p.put("qrcode_txt", "https://h5.kugou.com/apps/loginQRCode/html/index.html?appid=3116&");
        p.put("srcappid", "2919");
        assertEquals("03eb33614930a2305fe85d1e4c8f7c52", KugouLiteScraper.signWeb(p));
    }

    @Test
    void keyAndMidMatchPython() {
        assertEquals("c5ea1578355cf483c621e477e29a7328", KugouLiteScraper.signKey("abc", "123", 456));
        assertEquals("39942508068327174915303286770651046302", KugouLiteScraper.calcMid("guid"));
    }

    @Test
    void searchItemMappingMatchesPython() throws Exception {
        JsonNode it = new ObjectMapper().readTree("""
                {"FileHash":"3CAECE70F7DF3DE374C87851D9656A6D","FileName":"双笙 - 霜雪千年",
                 "Duration":246,"AlbumName":"霜雪千年","Image":"http://imge.kugou.com/{size}/x.jpg",
                 "PayType":3}
                """);
        MusicSong s = KugouLiteScraper.fromSearchItem(it);
        assertEquals("kugou_lite", s.platform());
        assertEquals("3caece70f7df3de374c87851d9656a6d", s.songId());
        assertEquals("双笙", s.singer());
        assertEquals("霜雪千年", s.songName());
        assertEquals("霜雪千年", s.albumName());
        assertEquals(246, s.durationSeconds());
        assertEquals("http://imge.kugou.com/480/x.jpg", s.coverUrl());
        assertTrue(s.pageUrl().contains("hash=3caece70"));
        assertNull(KugouLiteScraper.fromSearchItem(new ObjectMapper().readTree("{\"FileName\":\"x\"}")));
    }

    @Test
    void extractSongIdMatchesKugou() {
        String hash = "3caece70f7df3de374c87851d9656a6d";
        assertEquals(hash, KugouLiteScraper.extractSongId("https://www.kugou.com/song/#hash=" + hash));
        assertEquals(hash, KugouLiteScraper.extractSongId(hash));
        assertEquals("", KugouLiteScraper.extractSongId("not-a-hash"));
    }

    @Test
    void qualityMapping() {
        assertEquals("128", MusicDownloader.liteQuality("128kmp3"));
        assertEquals("320", MusicDownloader.liteQuality("320kmp3"));
        assertEquals("flac", MusicDownloader.liteQuality("flac"));
        assertEquals("128", MusicDownloader.liteQuality(null));
    }
}
