package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 酷我音乐搜索/直链/歌词。
 * 移植自 Python 版 music/scrapers/kuwo.py：双搜策略 + _match_any 过滤 + antiserver 直链。
 *
 * 直链说明：antiserver 返回 302，Location 即 mp3 地址。**不跟随重定向**读取 Location，
 * 可避开目标 CDN 的弱证书问题（Python 侧是 urllib 报 CERTIFICATE_VERIFY_FAILED）。
 */
public final class KuwoScraper {

    private static final String SEARCH_URL = "https://www.kuwo.cn/search/searchMusicBykeyWord";
    private static final String SEARCH_PARAMS = "vipver=1&client=kt&ft=music&cluster=0&strategy=2012"
            + "&encoding=utf8&rformat=json&mobi=1&issubtitle=1&show_copyright_off=1";
    private static final String LYRIC_URL = "http://m.kuwo.cn/newh5/singles/songinfoandlrc";
    private static final String ANTISERVER = "http://antiserver.kuwo.cn/anti.s";

    private static final Pattern RID_IN_TEXT = Pattern.compile(
            "play_detail/(\\d+)|yinyue/(\\d+)|[?&]rid=(\\d+)");

    private final HttpClient http;

    public KuwoScraper() { this(new HttpClient()); }

    public KuwoScraper(HttpClient http) { this.http = http; }

    private static Map<String, String> headers() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", HttpClient.UA_CHROME);
        h.put("Referer", "https://www.kuwo.cn/");
        h.put("Accept", "application/json, text/plain, */*");
        return h;
    }

    // ==================== 搜索 ====================

    /** Python: search_song —— 双搜 + 过滤去重。 */
    public List<MusicSong> searchSong(String keyword, int page, int limit) {
        List<MusicSong> songs = new ArrayList<>();
        List<String> seen = new ArrayList<>();

        for (MusicSong s : doSearch(keyword, page, limit * 2)) {
            if (!matchAny(keyword, s.songName(), s.singer())) continue;
            if (!seen.contains(s.songId())) {
                seen.add(s.songId());
                songs.add(s);
            }
        }

        String[] parts = keyword.strip().split("\\s+");
        if (parts.length >= 2 && songs.size() < limit) {
            String singerKw = "";
            for (String p : parts) if (p.length() > singerKw.length()) singerKw = p;
            for (MusicSong s : doSearch(singerKw, 1, limit * 3)) {
                if (seen.contains(s.songId())) continue;
                if (matchAny(keyword, s.songName(), s.singer())) {
                    seen.add(s.songId());
                    songs.add(s);
                }
            }
        }
        return songs.size() > limit ? songs.subList(0, limit) : songs;
    }

    /** Python: _do_search —— 单次搜索。 */
    public List<MusicSong> doSearch(String keyword, int page, int limit) {
        String url = SEARCH_URL + "?" + SEARCH_PARAMS
                + "&pn=" + ((page - 1) * limit) + "&rn=" + limit
                + "&all=" + HttpClient.urlEncode(keyword);
        List<MusicSong> songs = new ArrayList<>();
        try {
            JsonNode data = http.getJson(url, headers());
            JsonNode list = data.path("abslist");
            for (JsonNode item : list) {
                songs.add(fromSearchItem(item));
            }
        } catch (Exception e) {
            return songs;
        }
        return songs;
    }

    /** abslist 单项 → MusicSong（纯映射，可离线测试）。 */
    public static MusicSong fromSearchItem(JsonNode item) {
        String rid = item.path("MUSICRID").asText("").replace("MUSIC_", "");
        String songName = item.path("SONGNAME").asText(item.path("NAME").asText(""))
                .replace("&nbsp;", " ").replace("&", "&");
        String singer = item.path("ARTIST").asText(item.path("SINGER").asText("")).replace("&", "&");
        String picShort = item.path("web_albumpic_short").asText("").replaceFirst("^\\d+/", "");
        String cover = item.path("PICPATH").asText(item.path("pic").asText(""));
        if (cover.isEmpty() && !picShort.isEmpty()) {
            cover = "https://img1.kuwo.cn/star/albumcover/700/" + picShort;
        }
        int duration = item.path("DURATION").asInt(0);
        return new MusicSong("kuwo", rid, songName, singer,
                item.path("ALBUM").asText(""), duration, cover,
                "http://www.kuwo.cn/play_detail/" + rid);
    }

    /** Python: _match_any —— 最长片段必须命中；其余片段「至少命中一个」即通过。 */
    public static boolean matchAny(String keyword, String songName, String singer) {
        String[] parts = keyword.strip().split("\\s+");
        if (parts.length <= 1) return true;
        String sn = songName == null ? "" : songName.toLowerCase(Locale.ROOT);
        String si = singer == null ? "" : singer.toLowerCase(Locale.ROOT);
        String longest = "";
        for (String p : parts) if (p.length() > longest.length()) longest = p;
        longest = longest.toLowerCase(Locale.ROOT);
        if (longest.length() >= 2 && !si.contains(longest) && !sn.contains(longest)) return false;
        for (String p : parts) {
            String lp = p.toLowerCase(Locale.ROOT);
            if (lp.length() >= 2 && (sn.contains(lp) || si.contains(lp))) return true;
        }
        return false;
    }

    // ==================== 直链 / 歌词 ====================

    /** Python: get_play_url —— antiserver 302 Location 即 mp3 直链。 */
    public String getPlayUrl(String songId, String bitrate) {
        String br = bitrate == null || bitrate.isBlank() ? "128kmp3" : bitrate;
        String url = ANTISERVER + "?format=mp3&rid=MUSIC_" + songId
                + "&response=res&type=convert_url&br=" + HttpClient.urlEncode(br);
        try {
            var r = http.getRedirect(url, Map.of("Referer", "https://www.kuwo.cn/"));
            if (r.status() >= 300 && r.status() < 400 && !r.location().isBlank()) {
                return r.location();
            }
            // 少数情况下直接返回纯文本 URL
            String body = r.body() == null ? "" : r.body().strip();
            if (body.startsWith("http")) return body;
        } catch (Exception ignored) {
        }
        return "";
    }

    /** 歌词（LRC 文本）；取不到返回空串。注意歌词接口要 m.kuwo.cn 的 Referer。 */
    public String getLyric(String songId) {
        try {
            Map<String, String> h = new LinkedHashMap<>();
            h.put("User-Agent", HttpClient.UA_CHROME);
            h.put("Referer", "https://m.kuwo.cn/");
            h.put("Accept", "application/json, text/plain, */*");
            JsonNode d = http.getJson(LYRIC_URL + "?musicId=" + HttpClient.urlEncode(songId), h);
            JsonNode lrcList = d.path("data").path("lrclist");
            StringBuilder sb = new StringBuilder();
            for (JsonNode line : lrcList) {
                String time = line.path("time").asText("");
                String text = line.path("lineLyric").asText("");
                if (time.isEmpty()) continue;
                try {
                    double sec = Double.parseDouble(time);
                    int min = (int) (sec / 60);
                    double rest = sec - min * 60;
                    sb.append(String.format("[%02d:%05.2f]%s%n", min, rest, text));
                } catch (NumberFormatException ignored) {
                }
            }
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** Python: extract_song_id —— play_detail/xxx、yinyue/xxx、rid=xxx 或纯数字。 */
    public static String extractSongId(String text) {
        String s = text == null ? "" : text.strip();
        if (s.isEmpty()) return "";
        Matcher m = RID_IN_TEXT.matcher(s);
        if (m.find()) {
            for (int i = 1; i <= m.groupCount(); i++) {
                if (m.group(i) != null) return m.group(i);
            }
        }
        return s.matches("\\d+") ? s : "";
    }

    // ==================== 单曲 / 歌单 / 专辑解析 ====================

    /** 单曲详情（songinfoandlrc 同时带歌词，这里只取元信息）。 */
    public MusicSong songDetail(String rid) {
        try {
            Map<String, String> h = new LinkedHashMap<>();
            h.put("User-Agent", HttpClient.UA_CHROME);
            h.put("Referer", "https://m.kuwo.cn/");
            h.put("Accept", "application/json, text/plain, */*");
            JsonNode d = http.getJson(LYRIC_URL + "?musicId=" + HttpClient.urlEncode(rid)
                    + "&httpsStatus=1", h);
            JsonNode wrapper = d.path("data");
            JsonNode info = wrapper.path("songinfo");
            if (!info.isObject()) info = wrapper.isObject() ? wrapper : null;
            if (info == null) return null;
            String name = info.path("songName").asText("");
            if (name.isEmpty()) return null;
            String cover = info.path("pic").asText(info.path("htsPic").asText(""));
            if (!cover.isEmpty()) cover = cover.replaceFirst("/(\\d+)/", "/700/");
            int duration = info.path("duration").asInt(0);
            if (duration > 10000) duration /= 1000;
            return new MusicSong("kuwo", rid, name, info.path("artist").asText(""),
                    info.path("album").asText(""), duration, cover,
                    "https://www.kuwo.cn/play_detail/" + rid);
        } catch (Exception e) {
            return null;
        }
    }

    /** 歌单曲目（nplserver pl.svc，游客可用，分页）。 */
    public List<MusicSong> playlistSongs(String playlistId) {
        List<MusicSong> out = new ArrayList<>();
        int total = 0;
        for (int pn = 0; pn <= 50; pn++) {
            String url = "http://nplserver.kuwo.cn/pl.svc?op=getlistinfo&pid="
                    + HttpClient.urlEncode(playlistId) + "&pn=" + pn
                    + "&rn=100&encode=utf8&keyset=pl2012&identity=kuwo&pcmp4=1"
                    + "&vipver=MUSIC_9.0.5.0_W1&newver=1";
            try {
                JsonNode d = http.getJson(url, headers());
                if (pn == 0) total = d.path("total").asInt(0);
                JsonNode list = d.path("musiclist");
                if (!list.isArray() || list.isEmpty()) break;
                for (JsonNode item : list) {
                    String id = item.path("id").asText("");
                    if (id.isEmpty()) continue;
                    String cover = item.path("albumpic").asText(item.path("musicPic").asText(""));
                    if (!cover.isEmpty()) cover = cover.replaceFirst("/(\\d+)/", "/700/");
                    int duration = item.path("duration").asInt(0);
                    if (duration > 10000) duration /= 1000;
                    out.add(new MusicSong("kuwo", id,
                            item.path("name").asText(item.path("displaysongname").asText("")),
                            item.path("artist").asText(item.path("displayartistname").asText("")),
                            item.path("album").asText(item.path("displayalbumname").asText("")),
                            duration, cover, "https://www.kuwo.cn/play_detail/" + id));
                }
                if (total > 0 && out.size() >= total) break;
                if (list.size() < 100) break;
            } catch (Exception e) {
                break;
            }
        }
        return out;
    }

    /** 专辑曲目（www albumInfo；接口偶发反爬为空时返回空列表）。 */
    public List<MusicSong> albumSongs(String albumId) {
        String url = "http://www.kuwo.cn/api/www/album/albumInfo?albumid="
                + HttpClient.urlEncode(albumId) + "&pn=1&rn=100&httpsStatus=1";
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", HttpClient.UA_CHROME);
        h.put("Referer", "https://www.kuwo.cn/");
        h.put("Accept", "application/json, text/plain, */*");
        h.put("csrf", "");
        h.put("X-Requested-With", "XMLHttpRequest");
        try {
            JsonNode d = http.getJson(url, h);
            if (d.path("code").asInt(-1) != 200) return List.of();
            JsonNode info = d.path("data");
            String albumName = info.path("name").asText(info.path("album").asText(""));
            String cover = info.path("pic").asText(info.path("htsPic").asText(""));
            List<MusicSong> out = new ArrayList<>();
            for (JsonNode item : info.path("musicList")) {
                String rid = item.path("rid").asText("");
                if (rid.isEmpty()) continue;
                int duration = item.path("duration").asInt(0);
                if (duration > 10000) duration /= 1000;
                out.add(new MusicSong("kuwo", rid, item.path("name").asText(""),
                        item.path("artist").asText(""), albumName, duration, cover,
                        "https://www.kuwo.cn/play_detail/" + rid));
            }
            return out;
        } catch (Exception e) {
            return List.of();
        }
    }

    public static String extractPlaylistId(String text) {
        String s = text == null ? "" : text.strip();
        Matcher m = Pattern.compile("playlist_detail/(\\d+)|[?&]pid=(\\d+)|playlist/(\\d+)")
                .matcher(s);
        if (m.find()) {
            for (int i = 1; i <= 3; i++) if (m.group(i) != null) return m.group(i);
        }
        return s.matches("\\d+") ? s : "";
    }

    public static String extractAlbumId(String text) {
        String s = text == null ? "" : text.strip();
        Matcher m = Pattern.compile("album_detail/(\\d+)|[?&]albumid=(\\d+)|[?&]albumId=(\\d+)")
                .matcher(s);
        if (m.find()) {
            for (int i = 1; i <= 3; i++) if (m.group(i) != null) return m.group(i);
        }
        return s.matches("\\d+") ? s : "";
    }
}
