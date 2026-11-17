package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 网易云音乐（移植自 Python 版 music/scrapers/netease.py 的搜索/歌词/直链部分）。
 *
 * <p>搜索用 `/api/search/get` 表单接口；歌词 `/api/song/lyric`；
 * 直链走公开的 `song/media/outer/url` 302（免费歌可用，VIP 歌会失败）。
 */
public final class NeteaseScraper {

    private static final String BASE = "https://music.163.com";
    private final HttpClient http = new HttpClient();

    private Map<String, String> headers() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36");
        h.put("Referer", BASE + "/search/");
        h.put("X-Requested-With", "XMLHttpRequest");
        String cookie = CookieStore.get("netease");
        if (cookie != null && !cookie.isBlank()) h.put("Cookie", cookie);
        return h;
    }

    public List<MusicSong> searchSong(String keyword, int page, int limit) {
        Map<String, String> form = new LinkedHashMap<>();
        form.put("s", keyword);
        form.put("type", "1");
        form.put("offset", String.valueOf((Math.max(1, page) - 1) * limit));
        form.put("limit", String.valueOf(limit));
        try {
            JsonNode d = http.postFormJson(BASE + "/api/search/get", form, headers());
            return parseSearch(d);
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 纯解析（可离线测试）。 */
    public static List<MusicSong> parseSearch(JsonNode d) {
        List<MusicSong> out = new ArrayList<>();
        if (d == null || d.path("code").asInt(0) != 200) return out;
        for (JsonNode item : d.path("result").path("songs")) {
            MusicSong s = fromSongItem(item);
            if (s != null) out.add(s);
        }
        return out;
    }

    static MusicSong fromSongItem(JsonNode item) {
        String id = item.path("id").asText("");
        if (id.isEmpty()) return null;
        JsonNode album = item.has("al") ? item.path("al") : item.path("album");
        JsonNode artists = item.has("ar") ? item.path("ar") : item.path("artists");
        List<String> names = new ArrayList<>();
        if (artists.isArray()) {
            for (JsonNode a : artists) {
                String n = a.path("name").asText("");
                if (!n.isEmpty()) names.add(n);
            }
        }
        long durationMs = item.has("dt") ? item.path("dt").asLong(0)
                : item.path("duration").asLong(0);
        return new MusicSong("netease", id, item.path("name").asText(""),
                String.join(" / ", names), album.path("name").asText(""),
                (int) (durationMs / 1000), album.path("picUrl").asText(""),
                BASE + "/song?id=" + id);
    }

    /** 单曲详情（song/detail）。 */
    public MusicSong songDetail(String songId) {
        String url = BASE + "/api/song/detail?ids=" + HttpClient.urlEncode("[" + songId + "]");
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(0) != 200) return null;
            JsonNode songs = d.path("songs");
            return songs.isArray() && !songs.isEmpty() ? fromSongItem(songs.get(0)) : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** 专辑曲目（api/album/{id}）。 */
    public List<MusicSong> albumSongs(String albumId) {
        String url = BASE + "/api/album/" + HttpClient.urlEncode(albumId);
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(0) != 200) return List.of();
            JsonNode album = d.path("album");
            String albumName = album.path("name").asText("");
            String cover = album.path("picUrl").asText("");
            List<MusicSong> out = new ArrayList<>();
            for (JsonNode item : album.path("songs")) {
                MusicSong s = fromSongItem(item);
                if (s == null) continue;
                out.add(new MusicSong("netease", s.songId(), s.songName(), s.singer(),
                        s.albumName().isEmpty() ? albumName : s.albumName(),
                        s.durationSeconds(), s.coverUrl().isEmpty() ? cover : s.coverUrl(),
                        s.pageUrl()));
            }
            return out;
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 歌单曲目（api/playlist/detail）。 */
    public List<MusicSong> playlistSongs(String playlistId) {
        String url = BASE + "/api/playlist/detail?id=" + HttpClient.urlEncode(playlistId);
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(0) != 200) return List.of();
            JsonNode playlist = d.path("result").path("playlist");
            if (!playlist.isObject()) playlist = d.path("result");
            if (!playlist.path("tracks").isArray()) playlist = d.path("playlist");
            List<MusicSong> out = new ArrayList<>();
            for (JsonNode item : playlist.path("tracks")) {
                MusicSong s = fromSongItem(item);
                if (s != null) out.add(s);
            }
            return out;
        } catch (Exception e) {
            return List.of();
        }
    }

    public static String extractSongId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("song\\?id=(\\d+)|/song/(\\d+)").matcher(s);
        if (m.find()) return m.group(1) == null ? m.group(2) : m.group(1);
        return s.matches("\\d{3,}") ? s : "";
    }

    public static String extractAlbumId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("album\\?id=(\\d+)|/album/(\\d+)").matcher(s);
        if (m.find()) return m.group(1) == null ? m.group(2) : m.group(1);
        return s.matches("\\d{3,}") ? s : "";
    }

    public static String extractPlaylistId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("playlist\\?id=(\\d+)|playlist/(\\d+)|toplist\\?id=(\\d+)").matcher(s);
        if (m.find()) {
            for (int i = 1; i <= 3; i++) if (m.group(i) != null) return m.group(i);
        }
        return s.matches("\\d{3,}") ? s : "";
    }

    public String getLyric(String songId) {
        String url = BASE + "/api/song/lyric?id=" + HttpClient.urlEncode(songId)
                + "&lv=-1&kv=-1&tv=-1";
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(0) != 200) return "";
            return d.path("lrc").path("lyric").asText("");
        } catch (Exception e) {
            return "";
        }
    }

    /** 播放直链：公开 outer 接口通常 302 到 CDN；VIP/版权歌返回空。 */
    public record PlayInfo(String url, String error) {}

    public PlayInfo playInfo(String songId) {
        String outer = BASE + "/song/media/outer/url?id=" + HttpClient.urlEncode(songId) + ".mp3";
        try {
            var r = http.getRedirect(outer, Map.of(
                    "User-Agent", headers().get("User-Agent"),
                    "Referer", BASE + "/"));
            if (r.status() >= 300 && r.status() < 400 && !r.location().isEmpty()) {
                return new PlayInfo(r.location(), "");
            }
            if (r.status() == 200) return new PlayInfo(outer, "");
            return new PlayInfo("", "网易云未返回播放直链（VIP/版权限制，HTTP " + r.status() + "）");
        } catch (Exception e) {
            return new PlayInfo("", "网易云直链请求失败：" + e.getMessage());
        }
    }
}
