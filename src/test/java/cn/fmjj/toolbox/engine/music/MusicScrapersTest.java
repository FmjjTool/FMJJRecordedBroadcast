package cn.fmjj.toolbox.engine.music;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 网易云 / QQ 音乐搜索解析测试（字段映射对齐 Python 版 scrapers）。
 */
class MusicScrapersTest {

    @Test
    void neteaseParseSearch() throws Exception {
        JsonNode d = new ObjectMapper().readTree("""
                {"code":200,"result":{"songs":[{
                  "id":35345243,
                  "name":"霜雪千年",
                  "ar":[{"name":"双笙"},{"name":"封茗囧菌"}],
                  "al":{"name":"霜雪千年","picUrl":"https://p1.music.126.net/x.jpg"},
                  "dt":246000
                }]}}
                """);
        List<MusicSong> songs = NeteaseScraper.parseSearch(d);
        assertEquals(1, songs.size());
        MusicSong s = songs.get(0);
        assertEquals("netease", s.platform());
        assertEquals("35345243", s.songId());
        assertEquals("霜雪千年", s.songName());
        assertEquals("双笙 / 封茗囧菌", s.singer());
        assertEquals("霜雪千年", s.albumName());
        assertEquals(246, s.durationSeconds());
        assertEquals("https://p1.music.126.net/x.jpg", s.coverUrl());
        assertEquals("https://music.163.com/song?id=35345243", s.pageUrl());
    }

    @Test
    void qqParseSearch() throws Exception {
        JsonNode d = new ObjectMapper().readTree("""
                {"data":{"song":{"list":[{
                  "songmid":"004Pf1Nt4BLyfN",
                  "songname":"芳华慢",
                  "albumname":"芳华慢",
                  "albummid":"003abc",
                  "singer":[{"name":"封茗囧菌"}],
                  "interval":209
                }]}}}
                """);
        List<MusicSong> songs = QQMusicScraper.parseSearch(d);
        assertEquals(1, songs.size());
        MusicSong s = songs.get(0);
        assertEquals("qq", s.platform());
        assertEquals("004Pf1Nt4BLyfN", s.songId());
        assertEquals("芳华慢", s.songName());
        assertEquals("封茗囧菌", s.singer());
        assertEquals(209, s.durationSeconds());
        assertEquals("https://y.gtimg.cn/music/photo_new/T002R800x800M000003abc.jpg",
                s.coverUrl());
    }

    @Test
    void errorResponsesReturnEmpty() throws Exception {
        ObjectMapper m = new ObjectMapper();
        assertTrue(NeteaseScraper.parseSearch(m.readTree("{\"code\":400}")).isEmpty());
        assertTrue(QQMusicScraper.parseSearch(m.readTree("{}")).isEmpty());
    }

    @Test
    void idExtractionMatchesPython() {
        assertEquals("35345243", NeteaseScraper.extractSongId(
                "https://music.163.com/song?id=35345243"));
        assertEquals("166449770", NeteaseScraper.extractAlbumId(
                "https://music.163.com/album?id=166449770"));
        assertEquals("3778678", NeteaseScraper.extractPlaylistId(
                "https://music.163.com/playlist?id=3778678"));
        assertEquals("004Pf1Nt4BLyfN", QQMusicScraper.extractSongId(
                "https://y.qq.com/n/ryqq/songDetail/004Pf1Nt4BLyfN"));
        assertEquals("003w6fkW1TUFJo", QQMusicScraper.extractAlbumId(
                "https://y.qq.com/n/ryqq/albumDetail/003w6fkW1TUFJo"));
        assertEquals("7011264340", QQMusicScraper.extractPlaylistId(
                "https://y.qq.com/n/ryqq/playlist/7011264340"));
        assertEquals("yc/2646511", FiveSingScraper.extractSongId(
                "http://5sing.kugou.com/m/detail/yc-2646511-1.html"));
        assertEquals("https://5sing.kugou.com/yunjingchu/dj/6109fb71da767f5a61bd9c43.html",
                FiveSingScraper.extractPlaylistId(
                        "http://5sing.kugou.com/yunjingchu/dj/6109fb71da767f5a61bd9c43.html"));
    }
}
