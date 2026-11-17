package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * QQ 音乐（移植自 Python 版 music/scrapers/qq_music.py 的搜索/歌词/直链尝试）。
 *
 * <p>搜索/歌词接口游客可用；直链接口近年收紧，免费歌偶发可用，VIP 歌会失败并明确提示。
 */
public final class QQMusicScraper {

    private static final String SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp";
    private static final String LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg";

    private final HttpClient http = new HttpClient();

    private Map<String, String> headers() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36");
        h.put("Referer", "https://y.qq.com/");
        String cookie = CookieStore.get("qq");
        if (cookie != null && !cookie.isBlank()) h.put("Cookie", cookie);
        return h;
    }

    public List<MusicSong> searchSong(String keyword, int page, int limit) {
        String url = SEARCH_URL + "?w=" + HttpClient.urlEncode(keyword)
                + "&format=json&p=" + page + "&n=" + limit + "&type=0";
        try {
            JsonNode d = http.getJson(url, headers());
            return parseSearch(d);
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 纯解析（可离线测试）。 */
    public static List<MusicSong> parseSearch(JsonNode d) {
        List<MusicSong> out = new ArrayList<>();
        if (d == null) return out;
        for (JsonNode item : d.path("data").path("song").path("list")) {
            MusicSong s = fromItem(item);
            if (s != null) out.add(s);
        }
        return out;
    }

    static MusicSong fromItem(JsonNode item) {
        String mid = item.path("songmid").asText(item.path("mid").asText(""));
        if (mid.isEmpty()) return null;
        List<String> singers = new ArrayList<>();
        for (JsonNode s : item.path("singer")) {
            String n = s.path("name").asText("");
            if (!n.isEmpty()) singers.add(n);
        }
        JsonNode album = item.path("album");
        String albumMid = item.path("albummid").asText("");
        if (albumMid.isEmpty()) albumMid = album.isObject()
                ? album.path("mid").asText("") : album.asText("");
        String albumName = item.path("albumname").asText("");
        if (albumName.isEmpty() && album.isObject()) albumName = album.path("name").asText("");
        String songName = item.path("songname").asText("");
        if (songName.isEmpty()) songName = item.path("name").asText(item.path("title").asText(""));
        String cover = albumMid.isEmpty() ? ""
                : "https://y.gtimg.cn/music/photo_new/T002R800x800M000" + albumMid + ".jpg";
        return new MusicSong("qq", mid, songName,
                String.join(" / ", singers), albumName,
                item.path("interval").asInt(0), cover,
                "https://y.qq.com/n/ryqq/songDetail/" + mid);
    }

    /** 单曲详情（fcg_play_single_song）。 */
    public MusicSong songDetail(String songMid) {
        String url = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?songmid="
                + HttpClient.urlEncode(songMid) + "&format=json";
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(-1) != 0) return null;
            JsonNode data = d.path("data");
            JsonNode item = data.isArray() && !data.isEmpty() ? data.get(0) : data;
            return fromItem(item);
        } catch (Exception e) {
            return null;
        }
    }

    /** 专辑曲目（fcg_v8_album_detail_cp）。 */
    public List<MusicSong> albumSongs(String albumMid) {
        String url = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_detail_cp.fcg?albummid="
                + HttpClient.urlEncode(albumMid) + "&format=json";
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(-1) != 0) return List.of();
            JsonNode data = d.path("data");
            JsonNode list = data.path("getSongInfo");
            if (!list.isArray() || list.isEmpty()) list = data.path("list");
            List<MusicSong> out = new ArrayList<>();
            for (JsonNode item : list) {
                MusicSong s = fromItem(item);
                if (s != null) out.add(s);
            }
            return out;
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 歌单曲目（qzone fcg_ucc_getcdinfo_byids_cp）。 */
    public List<MusicSong> playlistSongs(String disstid) {
        String url = "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
                + "?type=1&json=1&utf8=1&onlysong=0&disstid=" + HttpClient.urlEncode(disstid)
                + "&format=json&g_tk=5381&loginUin=0&hostUin=0&inCharset=utf8"
                + "&outCharset=utf-8&notice=0&platform=yqq&needNewCode=0";
        try {
            JsonNode d = http.getJson(url, headers());
            JsonNode cdlist = d.path("cdlist");
            if (!cdlist.isArray() || cdlist.isEmpty()) return List.of();
            List<MusicSong> out = new ArrayList<>();
            for (JsonNode item : cdlist.get(0).path("songlist")) {
                MusicSong s = fromItem(item);
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
                .compile("songDetail/([A-Za-z0-9]+)|songmid=([A-Za-z0-9]+)").matcher(s);
        if (m.find()) return m.group(1) == null ? m.group(2) : m.group(1);
        return s.matches("[A-Za-z0-9]{8,}") ? s : "";
    }

    public static String extractAlbumId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("albumDetail/([A-Za-z0-9]+)|albummid=([A-Za-z0-9]+)").matcher(s);
        if (m.find()) return m.group(1) == null ? m.group(2) : m.group(1);
        return s.matches("[A-Za-z0-9]{8,}") ? s : "";
    }

    public static String extractPlaylistId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("playlist/(\\d+)|disstid=(\\d+)").matcher(s);
        if (m.find()) return m.group(1) == null ? m.group(2) : m.group(1);
        return s.matches("\\d+") ? s : "";
    }

    public String getLyric(String songId) {
        String url = LYRIC_URL + "?songmid=" + HttpClient.urlEncode(songId)
                + "&format=json&nobase64=1";
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("code").asInt(-1) != 0) return "";
            String lyric = d.path("lyric").asText("");
            return lyric.replace("\\n", "\n");
        } catch (Exception e) {
            return "";
        }
    }

    /** 播放直链（pyq 分享接口；仅接受跳转，其他情况明确报错）。 */
    public record PlayInfo(String url, String ext, String error) {}

    public PlayInfo playInfo(String songMid) {
        String url = "http://c6.y.qq.com/rsc/fcgi-bin/fcg_pyq_play.fcg?songid=&songmid="
                + HttpClient.urlEncode(songMid)
                + "&songtype=1&fromtag=50&uin=0&code=5F4F8";
        try {
            var r = http.getRedirect(url, headers());
            if (r.status() >= 300 && r.status() < 400 && !r.location().isEmpty()) {
                String loc = r.location();
                String lower = loc.toLowerCase(java.util.Locale.ROOT);
                String ext = lower.contains(".flac") ? ".flac"
                        : lower.contains(".m4a") ? ".m4a"
                        : lower.contains(".ogg") ? ".ogg" : ".mp3";
                return new PlayInfo(loc, ext, "");
            }
            if (r.status() == 200) {
                return new PlayInfo("", "", "QQ音乐未返回直链（接口已收紧，需 VIP/登录态），当前仅支持搜索");
            }
            return new PlayInfo("", "", "QQ音乐直链接口已收紧（需 VIP/登录态，HTTP "
                    + r.status() + "），当前仅支持搜索");
        } catch (Exception e) {
            return new PlayInfo("", "", "QQ音乐直链请求失败："
                    + (e.getMessage() == null ? e.toString() : e.getMessage()));
        }
    }
}
