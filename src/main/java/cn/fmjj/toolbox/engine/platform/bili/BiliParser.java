package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.engine.net.HttpClient;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * B 站视频解析：详情（view）+ 播放地址（playurl，DASH 全流）。
 * 移植自 Python 版 src/bilibili_download.py 的 get_cid / get_bilibili_play_info /
 * _pick_video_stream / _pick_audio_stream / _stream_urls。
 */
public final class BiliParser {

    private static final String VIEW_API = "https://api.bilibili.com/x/web-interface/view";
    private static final String PLAYURL_API = "https://api.bilibili.com/x/player/playurl";
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /** 画质描述兜底（优先用 API 的 accept_description）。 */
    public static final Map<Integer, String> QUALITY_DESC = new LinkedHashMap<>() {{
        put(127, "8K 超高清");
        put(126, "杜比视界");
        put(125, "HDR 真彩");
        put(120, "4K 超清");
        put(116, "1080P 60帧");
        put(112, "1080P+");
        put(80, "1080P");
        put(74, "720P 60帧");
        put(64, "720P");
        put(32, "480P");
        put(16, "360P");
        put(6, "240P");
    }};

    public record Quality(int qn, String desc) {}

    /** 一条 DASH 流。 */
    public record StreamInfo(int id, int codecid, long bandwidth, List<String> urls,
                             int width, int height, String codecs) {
        public String qualityText() {
            return QUALITY_DESC.getOrDefault(id, "画质" + id);
        }
    }

    public record PlayInfo(List<Quality> qualities, List<StreamInfo> video, List<StreamInfo> audio) {}

    public record VideoInfo(String bvid, long aid, long cid, String title, String owner,
                            String cover, long pubdate, int duration, String desc) {
        public String publishText() {
            if (pubdate <= 0) return "";
            return LocalDateTime.ofInstant(Instant.ofEpochSecond(pubdate), ZoneId.systemDefault()).format(TIME_FMT);
        }
    }

    private final HttpClient http;

    public BiliParser() { this(new HttpClient()); }

    public BiliParser(HttpClient http) { this.http = http; }

    /** Python: _headers —— Chrome UA + Referer/Origin（+ 可选 Cookie）。 */
    public static Map<String, String> headers(String bvid, String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", HttpClient.UA_CHROME);
        h.put("Referer", "https://www.bilibili.com/video/" + bvid);
        h.put("Origin", "https://www.bilibili.com");
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    /** Python: get_cid —— 支持 bvid / av 号，返回完整详情。 */
    public VideoInfo videoInfo(String idOrBvid, String cookie) throws Exception {
        String id = idOrBvid.startsWith("av") || idOrBvid.startsWith("AV")
                ? idOrBvid.substring(2) : idOrBvid;
        String url = VIEW_API + (idOrBvid.toLowerCase().startsWith("av")
                ? "?aid=" + HttpClient.urlEncode(id)
                : "?bvid=" + HttpClient.urlEncode(id));
        var d = http.getJson(url, headers(idOrBvid, cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException(d.path("message").asText("获取视频信息失败"));
        }
        var data = d.path("data");
        return new VideoInfo(
                data.path("bvid").asText(idOrBvid),
                data.path("aid").asLong(0),
                data.path("cid").asLong(0),
                data.path("title").asText(""),
                data.path("owner").path("name").asText(""),
                data.path("pic").asText(""),
                data.path("pubdate").asLong(0),
                data.path("duration").asInt(0),
                data.path("desc").asText(""));
    }

    /** Python: get_bilibili_play_info —— fnval=4048 取全流（DASH/杜比/Hi-Res/8K）。 */
    public PlayInfo playInfo(String bvid, long cid, String cookie) throws Exception {
        String url = PLAYURL_API + "?bvid=" + HttpClient.urlEncode(bvid)
                + "&cid=" + cid + "&qn=127&fnval=4048&fourk=1";
        var d = http.getJson(url, headers(bvid, cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException(d.path("message").asText("获取播放地址失败"));
        }
        var data = d.path("data");
        var aq = data.path("accept_quality");
        var ad = data.path("accept_description");
        List<Quality> qualities = new ArrayList<>();
        for (int i = 0; i < aq.size(); i++) {
            int qn = aq.get(i).asInt();
            String desc = i < ad.size() ? ad.get(i).asText("") : "";
            qualities.add(new Quality(qn, desc.isEmpty() ? QUALITY_DESC.getOrDefault(qn, "画质" + qn) : desc));
        }
        var dash = data.path("dash");
        List<StreamInfo> video = parseStreams(dash.path("video"));
        List<StreamInfo> audio = parseStreams(dash.path("audio"));
        return new PlayInfo(qualities, video, audio);
    }

    private static List<StreamInfo> parseStreams(com.fasterxml.jackson.databind.JsonNode array) {
        List<StreamInfo> out = new ArrayList<>();
        for (var node : array) {
            List<String> urls = new ArrayList<>();
            String base = node.path("baseUrl").asText(node.path("base_url").asText(""));
            if (!base.isEmpty()) urls.add(base);
            var backup = node.has("backupUrl") ? node.path("backupUrl") : node.path("backup_url");
            if (backup.isArray()) {
                for (var b : backup) {
                    String u = b.asText("");
                    if (!u.isEmpty() && !urls.contains(u)) urls.add(u);
                }
            }
            out.add(new StreamInfo(
                    node.path("id").asInt(0),
                    node.path("codecid").asInt(0),
                    node.path("bandwidth").asLong(0),
                    urls,
                    node.path("width").asInt(0),
                    node.path("height").asInt(0),
                    node.path("codecs").asText("")));
        }
        return out;
    }

    /** Python: _pick_video_stream —— 目标画质优先 AVC(codecid=7)，否则带宽最低。 */
    public static StreamInfo pickVideoStream(List<StreamInfo> streams, int targetQn) {
        if (streams == null || streams.isEmpty()) return null;
        List<StreamInfo> candidates = streams.stream().filter(s -> s.id() == targetQn).toList();
        if (candidates.isEmpty()) {
            candidates = streams.stream()
                    .sorted((a, b) -> Integer.compare(b.id(), a.id()))
                    .toList();
        }
        for (StreamInfo s : candidates) {
            if (s.codecid() == 7) return s;
        }
        return candidates.stream().min((a, b) -> Long.compare(a.bandwidth(), b.bandwidth())).orElse(null);
    }

    /** Python: _pick_audio_stream —— 带宽最高。 */
    public static StreamInfo pickAudioStream(List<StreamInfo> audio) {
        if (audio == null || audio.isEmpty()) return null;
        return audio.stream().max((a, b) -> Long.compare(a.bandwidth(), b.bandwidth())).orElse(null);
    }

    /** Python: sanitize_filename —— 替换非法字符 + 防路径遍历 + 压缩下划线 + 截断。 */
    public static String sanitizeFilename(String name, int maxLen, String fallback) {
        String s = name == null ? "" : name;
        s = s.replaceAll("[<>:\"/\\\\|?*\\n\\r\\t]", "_");
        s = s.replace("..", "_");
        s = s.replaceAll("[_\\s]+", "_").replaceAll("^[_ ]+|[_ ]+$", "");
        if (s.length() > maxLen) {
            s = s.substring(0, maxLen).replaceAll("[_ ]+$", "");
        }
        return s.isEmpty() ? fallback : s;
    }

    /** 输出文件名（Python: {date}_{author}_{title}.mp4）。 */
    public static String outputFileName(String publishTime, String author, String title, String bvid) {
        String date = publishTime == null || publishTime.isBlank() ? "" : publishTime.substring(0, Math.min(10, publishTime.length()));
        date = date.replaceAll("[^0-9]", "");
        if (date.isEmpty()) {
            date = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        }
        String authorSafe = sanitizeFilename(author == null || author.isBlank() ? "未知" : author, 80, "未知");
        String titleSafe = sanitizeFilename(title == null || title.isBlank() ? bvid : title, 80, "video");
        return date + "_" + authorSafe + "_" + titleSafe + ".mp4";
    }
}
