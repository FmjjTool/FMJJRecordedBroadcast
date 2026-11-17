package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 酷狗音乐抓取（移植自 Python 版 music/scrapers/kugou.py 的搜索/详情/直链部分）。
 * 接口：mobilecdn 搜索/歌曲信息、m.kugou getSongInfo playInfo、krc 歌词。
 */
public final class KugouScraper {

    private static final ObjectMapper MAPPER = JsonMapper.get();
    private static final String SEARCH_URL = "http://mobilecdn.kugou.com/api/v3/search/song";
    private static final String PLAY_INFO_URL = "http://m.kugou.com/app/i/getSongInfo.php";
    private static final String LYRIC_URL = "http://m.kugou.com/app/i/krc.php";

    private final HttpClient http = new HttpClient();

    private Map<String, String> headers() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36");
        h.put("Referer", "http://m.kugou.com/");
        return h;
    }

    public List<MusicSong> searchSong(String keyword, int page, int limit) {
        List<MusicSong> out = new ArrayList<>();
        for (JsonNode item : searchItems(keyword, page, limit)) {
            MusicSong s = fromSearchItem(item);
            if (s != null) out.add(s);
        }
        return out;
    }

    /** 原始搜索结果条目（供 5sing 复用酷狗共享库）。 */
    public List<JsonNode> searchItems(String keyword, int page, int limit) {
        String url = SEARCH_URL + "?keyword=" + HttpClient.urlEncode(keyword)
                + "&page=" + page + "&pagesize=" + limit + "&showtype=1&format=json";
        List<JsonNode> out = new ArrayList<>();
        try {
            JsonNode d = http.getJson(url, headers());
            if (d == null || d.path("status").asInt(0) != 1) return out;
            for (JsonNode item : d.path("data").path("info")) out.add(item);
        } catch (Exception ignored) {
        }
        return out;
    }

    /** 单条搜索结果 → MusicSong（可离线测试）。 */
    public static MusicSong fromSearchItem(JsonNode item) {
        String hash = item.path("hash").asText("");
        if (hash.isEmpty()) return null;
        String name = item.path("songname").asText("")
                .replace("<em>", "").replace("</em>", "");
        int duration = item.path("duration").asInt(0);
        if (duration > 10000) duration /= 1000;
        String cover = item.path("imgUrl").asText(item.path("imgurl").asText(""))
                .replace("{size}", "480");
        String album = item.path("album_name").asText(item.path("remark").asText(""));
        return new MusicSong("kugou", hash, name,
                item.path("singername").asText(""), album, duration, cover,
                "https://www.kugou.com/song/#hash=" + hash);
    }

    /** 纯解析（可离线测试）。 */
    public static List<MusicSong> parseSearch(JsonNode d) {
        List<MusicSong> out = new ArrayList<>();
        if (d == null || d.path("status").asInt(0) != 1) return out;
        for (JsonNode item : d.path("data").path("info")) {
            MusicSong s = fromSearchItem(item);
            if (s != null) out.add(s);
        }
        return out;
    }

    /** 游客直链（免费歌 128k 全曲；VIP 歌明确报错）。 */
    public record PlayInfo(String url, String ext, String quality, String error) {}

    public PlayInfo playInfo(String hash) {
        String url = PLAY_INFO_URL + "?cmd=playInfo&hash=" + HttpClient.urlEncode(hash);
        try {
            JsonNode d = http.getJson(url, headers());
            String u = d.path("url").asText("");
            if (u.isEmpty()) {
                if (d.path("songName").isMissingNode() && d.path("singerName").isMissingNode()) {
                    return new PlayInfo("", "", "", "酷狗未找到该歌曲（可能已下架）");
                }
                int priv = d.path("privilege").asInt(d.path("128privilege").asInt(0));
                int payType = d.path("pay_type").asInt(0);
                if (priv == 10 || payType == 1 || payType == 3) {
                    return new PlayInfo("", "", "", "酷狗 VIP 付费歌曲，游客无法获取直链");
                }
                return new PlayInfo("", "", "", "酷狗未返回播放直链（歌曲可能已下架）");
            }
            String ext = d.path("extName").asText("mp3").toLowerCase(java.util.Locale.ROOT);
            if (!ext.startsWith(".")) ext = "." + ext;
            return new PlayInfo(u, ext, d.path("bitRate").asText("128") + "kbps", "");
        } catch (Exception e) {
            return new PlayInfo("", "", "", "酷狗直链请求失败：" + e.getMessage());
        }
    }

    /** 歌词（LRC/KRC 文本；游客可用）。 */
    public String getLyric(String hash) {
        String url = LYRIC_URL + "?hash=" + HttpClient.urlEncode(hash)
                + "&timelength=300000&cmd=100";
        try {
            String text = http.getText(url, headers()).strip();
            if (text.startsWith("\ufeff")) text = text.substring(1);
            if (text.startsWith("[")) return text;
            JsonNode d = MAPPER.readTree(text);
            String content = d.path("content").asText("");
            if (content.isEmpty()) content = d.path("lyrics").asText("");
            if (content.isEmpty()) return "";
            try {
                byte[] decoded = java.util.Base64.getDecoder().decode(content);
                String dz;
                try (var in = new java.util.zip.InflaterInputStream(
                        new java.io.ByteArrayInputStream(decoded))) {
                    dz = new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
                } catch (Exception e) {
                    dz = new String(decoded, java.nio.charset.StandardCharsets.UTF_8);
                }
                java.util.regex.Matcher m = java.util.regex.Pattern
                        .compile("\\[(\\d+),(\\d+)](.*?)(?=\\[\\d+,\\d+]|$)", java.util.regex.Pattern.DOTALL)
                        .matcher(dz);
                StringBuilder sb = new StringBuilder();
                while (m.find()) {
                    String line = m.group(3).strip();
                    if (!line.isEmpty()) sb.append(line).append('\n');
                }
                return sb.toString();
            } catch (Exception e) {
                return content;
            }
        } catch (Exception e) {
            return "";
        }
    }

    /** 从输入解析歌曲 hash（hash=xxx / mixsong/xxx / 32 位十六进制）。 */
    public static String extractSongId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("hash=([a-fA-F0-9]{32})").matcher(s);
        if (m.find()) return m.group(1);
        m = java.util.regex.Pattern.compile("mixsong/([a-fA-F0-9]{32})").matcher(s);
        if (m.find()) return m.group(1);
        return s.matches("[a-fA-F0-9]{32}") ? s : "";
    }

    /** 单曲详情（song/info，游客可用）。 */
    public MusicSong songDetail(String hash) {
        String url = "http://mobilecdn.kugou.com/api/v3/song/info?hash="
                + HttpClient.urlEncode(hash);
        try {
            JsonNode d = http.getJson(url, headers());
            if (d.path("status").asInt(0) != 1) return null;
            JsonNode info = d.path("data");
            String filename = info.path("filename").asText("");
            String songName = info.path("songname").asText("");
            String singer = info.path("singername").asText("");
            if (songName.isEmpty() && filename.contains(" - ")) {
                String[] parts = filename.split(" - ", 2);
                singer = singer.isEmpty() ? parts[0] : singer;
                songName = parts[1];
            }
            if (songName.isEmpty()) return null;
            JsonNode album = info.path("album");
            if (album.isArray() && !album.isEmpty()) album = album.get(0);
            String albumName = album.path("albumname").asText(album.path("name").asText(""));
            String audioId = album.path("album_audio_id").asText("");
            String cover = audioId.isEmpty() ? ""
                    : "http://imge.kugou.com/stdmusic/240/" + audioId + ".jpg";
            int duration = info.path("duration").asInt(0);
            if (duration > 10000) duration /= 1000;
            return new MusicSong("kugou", hash, songName, singer, albumName, duration, cover,
                    "https://www.kugou.com/song/#hash=" + hash);
        } catch (Exception e) {
            return null;
        }
    }

    /** 歌单曲目（special/info + special/song 分页）。 */
    public List<MusicSong> playlistSongs(String specialId) {
        List<MusicSong> out = new ArrayList<>();
        try {
            JsonNode infoResp = http.getJson(
                    "http://mobilecdn.kugou.com/api/v3/special/info?specialid="
                            + HttpClient.urlEncode(specialId) + "&format=json", headers());
            if (infoResp.path("status").asInt(0) != 1) return out;
            int total = infoResp.path("data").path("songcount").asInt(0);
            for (int page = 1; page <= 50; page++) {
                JsonNode d = http.getJson(
                        "http://mobilecdn.kugou.com/api/v3/special/song?specialid="
                                + HttpClient.urlEncode(specialId) + "&page=" + page
                                + "&pagesize=100", headers());
                if (d.path("status").asInt(0) != 1) break;
                JsonNode list = d.path("data").path("info");
                if (!list.isArray() || list.isEmpty()) break;
                for (JsonNode item : list) {
                    String hash = item.path("hash").asText("");
                    if (hash.isEmpty()) continue;
                    String filename = item.path("filename").asText("");
                    String singer = "";
                    String songName = filename;
                    if (filename.contains(" - ")) {
                        String[] parts = filename.split(" - ", 2);
                        singer = parts[0];
                        songName = parts[1];
                    }
                    int duration = item.path("duration").asInt(0);
                    if (duration > 10000) duration /= 1000;
                    String audioId = item.path("album_audio_id").asText("");
                    String cover = audioId.isEmpty() ? ""
                            : "http://imge.kugou.com/stdmusic/240/" + audioId + ".jpg";
                    out.add(new MusicSong("kugou", hash, songName, singer,
                            item.path("remark").asText(""), duration, cover,
                            "https://www.kugou.com/song/#hash=" + hash));
                }
                if (total > 0 && out.size() >= total) break;
                if (list.size() < 100) break;
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    public static String extractPlaylistId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("special/single/(\\d+)|specialid=(\\d+)|/plist/(\\d+)").matcher(s);
        if (m.find()) {
            for (int i = 1; i <= 3; i++) if (m.group(i) != null) return m.group(i);
        }
        return s.matches("\\d+") ? s : "";
    }
}
