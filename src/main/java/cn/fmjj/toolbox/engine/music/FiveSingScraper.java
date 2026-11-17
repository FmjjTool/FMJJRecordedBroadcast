package cn.fmjj.toolbox.engine.music;

import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 5sing 搜索/解析（移植自 Python music/scrapers/fivesing.py）。
 * 5sing 主站受 CloudFlare 保护，搜索改用酷狗共享库；歌曲页/歌单页走 5sing HTML 解析。
 */
public final class FiveSingScraper {

    private final KugouScraper kugou = new KugouScraper();
    private final HttpClient http = new HttpClient();

    private java.util.Map<String, String> headers() {
        return java.util.Map.of(
                "User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Referer", "https://5sing.kugou.com/");
    }

    public List<MusicSong> searchSong(String keyword, int page, int limit) {
        List<MusicSong> out = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (JsonNode item : kugou.searchItems(keyword, page, Math.max(limit * 3, limit))) {
            String hash = item.path("hash").asText("");
            if (hash.isEmpty() || !seen.add(hash)) continue;
            String name = item.path("songname").asText("")
                    .replace("<em>", "").replace("</em>", "");
            String album = item.path("album_name").asText("");
            int accompany = item.path("Accompany").asInt(0);
            int isOriginal = item.path("isoriginal").asInt(0);
            String otherName = item.path("othername_original").asText("");
            boolean yc = isOriginal == 1
                    || (accompany == 0 && !album.contains("翻唱")
                    && !otherName.contains("DJ") && !name.contains("伴奏"));
            boolean bz = accompany == 1 || name.contains("伴奏") || album.contains("伴奏");
            boolean fc = isOriginal != 1 && accompany != 1
                    && (album.contains("翻唱") || album.toLowerCase(java.util.Locale.ROOT).contains("cover")
                    || (!name.contains("伴奏") && !otherName.contains("DJ")));
            if (!(yc || bz || fc)) continue;
            int duration = item.path("duration").asInt(0);
            if (duration > 10000) duration /= 1000;
            String cover = item.path("imgUrl").asText(item.path("imgurl").asText(""))
                    .replace("{size}", "480");
            out.add(new MusicSong("5sing", hash, name,
                    item.path("singername").asText(""), album, duration, cover,
                    "https://5sing.kugou.com/m/detail/" + hash + ".html"));
        }
        return out;
    }

    /** 单曲：yc/123 形式解析歌曲页；酷狗 hash 走共享库详情。 */
    public MusicSong songDetail(String songId) {
        if (!songId.contains("/")) {
            MusicSong k = kugou.songDetail(songId);
            if (k == null) return null;
            return new MusicSong("5sing", songId, k.songName(), k.singer(), k.albumName(),
                    k.durationSeconds(), k.coverUrl(),
                    "https://5sing.kugou.com/m/detail/" + songId + ".html");
        }
        try {
            String html = http.getText("https://5sing.kugou.com/m/detail/"
                    + songId.replace("/", "-") + "-1.html", headers());
            if (html.contains("meta http-equiv=\"refresh\"")
                    && firstGroup(html, "<title>([^<]+)</title>").contains("5sing音乐")) {
                html = http.getText("https://5sing.kugou.com/" + songId + ".html", headers());
            }
            String title = firstGroup(html, "<title>([^<]+)</title>");
            String songName = "";
            String singer = "";
            if (!title.isEmpty() && !title.contains("5sing音乐")) {
                String[] parts = title.split(" - ");
                if (parts.length > 0) songName = parts[0].strip();
                if (parts.length > 1) singer = parts[1].strip();
            }
            if (songName.isEmpty()) return null;
            String cover = firstGroup(html, "og:image\"\\s+content=\"([^\"]+)\"");
            return new MusicSong("5sing", songId, songName, singer, "", 0, cover,
                    "https://5sing.kugou.com/" + songId + ".html");
        } catch (Exception e) {
            return null;
        }
    }

    /** 歌单页 HTML → 曲目（yc/fc/bz + 数字）。 */
    public List<MusicSong> playlistSongs(String text) {
        String url = normalizePlaylistUrl(text);
        if (url.isEmpty()) return List.of();
        try {
            String html = http.getText(url, headers());
            List<MusicSong> out = new ArrayList<>();
            Set<String> seen = new LinkedHashSet<>();
            java.util.regex.Matcher m = java.util.regex.Pattern.compile(
                    "href=\"(?:https?://5sing\\.kugou\\.com)?/?((?:yc|fc|bz)/\\d+)\\.html\"[^>]*>\\s*([^<]{1,60}?)\\s*</a>")
                    .matcher(html);
            while (m.find()) {
                String id = m.group(1);
                String name = m.group(2).strip();
                if (name.isEmpty() || !seen.add(id)) continue;
                out.add(new MusicSong("5sing", id, name, "", "", 0, "",
                        "https://5sing.kugou.com/" + id + ".html"));
            }
            return out;
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 下载直链：hash → 酷狗；yc/123 → 5sing getSongUrl（优先 hq 192k）。 */
    public record PlayInfo(String url, String ext, String error) {}

    public PlayInfo playInfo(String songId) {
        if (!songId.contains("/")) {
            KugouScraper.PlayInfo k = kugou.playInfo(songId);
            String ext = k.ext().isEmpty() ? ".mp3" : k.ext();
            return new PlayInfo(k.url(), ext, k.error());
        }
        String[] parts = songId.split("/", 2);
        String url = "http://mobileapi.5sing.kugou.com/song/getSongUrl?songtype="
                + HttpClient.urlEncode(parts[0]) + "&songid=" + HttpClient.urlEncode(parts[1])
                + "&version=6.6.72";
        try {
            JsonNode d = http.getJson(url, headers());
            JsonNode data = d.path("data");
            String hq = data.path("hqurl").asText("");
            String lq = data.path("lqurl").asText("");
            String chosen = !hq.isEmpty() ? hq : lq;
            if (chosen.isEmpty()) return new PlayInfo("", "", "5sing 未返回播放直链");
            String ext = (chosen.equals(hq) ? data.path("hqext").asText("")
                    : data.path("lqext").asText("")).toLowerCase(java.util.Locale.ROOT);
            if (ext.isEmpty()) ext = "mp3";
            if (!ext.startsWith(".")) ext = "." + ext;
            return new PlayInfo(chosen, ext, "");
        } catch (Exception e) {
            return new PlayInfo("", "", "5sing 直链请求失败：" + e.getMessage());
        }
    }

    public static String extractSongId(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("/(yc|fc|bz)/(\\d+)\\.html|/m/(yc|fc|bz)/(\\d+)").matcher(s);
        if (m.find()) {
            if (m.group(1) != null) return m.group(1) + "/" + m.group(2);
            return m.group(3) + "/" + m.group(4);
        }
        m = java.util.regex.Pattern.compile("/(?:m/detail/)?(yc|fc|bz)-(\\d+)(?:-\\d+)?\\.html")
                .matcher(s);
        if (m.find()) return m.group(1) + "/" + m.group(2);
        if (s.matches("(yc|fc|bz)/\\d+")) return s;
        if (s.matches("(yc|fc|bz)-\\d+(-\\d+)?")) {
            String[] p = s.split("-");
            return p[0] + "/" + p[1];
        }
        return s.matches("[a-fA-F0-9]{32}") ? s : "";
    }

    public static String extractPlaylistId(String text) {
        return normalizePlaylistUrl(text);
    }

    static String normalizePlaylistUrl(String text) {
        String s = text == null ? "" : text.strip();
        java.util.regex.Matcher m = java.util.regex.Pattern.compile(
                "(?:https?://5sing\\.kugou\\.com)?/?([A-Za-z0-9_-]+)/dj/([0-9a-zA-Z_]+)\\.html")
                .matcher(s);
        return m.find() ? "https://5sing.kugou.com/" + m.group(1) + "/dj/"
                + m.group(2) + ".html" : "";
    }

    private static String firstGroup(String text, String regex) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile(regex).matcher(text);
        return m.find() ? m.group(1).strip() : "";
    }
}
