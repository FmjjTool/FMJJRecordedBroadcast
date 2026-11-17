package cn.fmjj.toolbox.engine.platform.weibo;

import cn.fmjj.toolbox.engine.net.HttpClient;
import cn.fmjj.toolbox.engine.platform.MediaItem;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 微博解析（m.weibo.cn JSON API）。
 * 移植自 Python 版 platforms/weibo.py：extract_id / _extract_fid / _clean_rich /
 * _parse_play_count / _build_info（视频 + 图片 + 长文本）。
 *
 * 已知差距：Python 还有「移动页 HTML 解析」和「视频播放器页 fid → component API」
 * 两级兜底，Java 版当前只走 statuses/show 主路径（失败时明确报错），后续补。
 */
public final class WeiboParser {

    private static final Pattern MID = Pattern.compile("weibo\\.com/(\\d+)/([a-zA-Z0-9]+)");
    private static final Pattern CN = Pattern.compile("weibo\\.cn/([a-zA-Z0-9]+)");
    private static final Pattern DETAIL = Pattern.compile("m\\.weibo\\.cn/detail/([a-zA-Z0-9]+)");
    private static final Pattern STATUS = Pattern.compile("m\\.weibo\\.cn/status/([a-zA-Z0-9]+)");
    private static final Pattern FID = Pattern.compile(
            "(?:video\\.weibo\\.com/show\\?fid=|h5\\.video\\.weibo\\.com/show/|weibo\\.com/tv/show/)(\\d+:\\d+)");

    private static final String STATUS_API = "https://m.weibo.cn/api/statuses/show";
    private static final String LONGTEXT_API = "https://m.weibo.cn/statuses/extend";

    private static final Map<String, String> HEADERS = Map.of(
            "User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept", "application/json, text/plain, */*",
            "Accept-Language", "zh-CN,zh;q=0.9",
            "Referer", "https://m.weibo.cn/",
            "X-Requested-With", "XMLHttpRequest");

    private final HttpClient http;

    public WeiboParser() { this(new HttpClient()); }

    public WeiboParser(HttpClient http) { this.http = http; }

    /** 搜索命中（视频/图文微博）。 */
    public record SearchHit(String title, String author, String url, String cover, String desc) {}

    /** 关键词搜索（m.weibo.cn container getIndex，游客/Cookie 均可尝试）。 */
    public List<SearchHit> search(String keyword, int page, String cookie) {
        List<SearchHit> out = new ArrayList<>();
        try {
            String container = "100103type=1&q=" + keyword;
            String url = "https://m.weibo.cn/api/container/getIndex?containerid="
                    + HttpClient.urlEncode(container) + "&page_type=searchall&page=" + page;
            Map<String, String> headers = new LinkedHashMap<>(HEADERS);
            if (cookie != null && !cookie.isEmpty()) headers.put("Cookie", cookie);
            JsonNode d = http.getJson(url, headers);
            if (d.path("ok").asInt(0) != 1) return out;
            for (JsonNode card : d.path("data").path("cards")) {
                JsonNode mblog = card.path("mblog");
                if (!mblog.isObject()) continue;
                String text = cleanRich(mblog.path("text").asText(""));
                String title = text.replaceAll("\\s+", " ").strip();
                if (title.length() > 80) title = title.substring(0, 80);
                JsonNode user = mblog.path("user");
                String uid = user.path("id").asText("");
                String bid = mblog.path("bid").asText("");
                String id = mblog.path("id").asText(mblog.path("mid").asText(""));
                String pageUrl = bid.isEmpty() ? "https://m.weibo.cn/detail/" + id
                        : "https://weibo.com/" + uid + "/" + bid;
                String cover = mblog.path("page_info").path("page_pic").path("url").asText("");
                if (cover.isEmpty() && mblog.path("pics").isArray() && !mblog.path("pics").isEmpty()) {
                    cover = mblog.path("pics").get(0).path("url").asText("");
                }
                out.add(new SearchHit(title, user.path("screen_name").asText(""),
                        pageUrl, cover, text));
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    // ==================== 链接 / 文本工具 ====================

    /** Python: extract_id —— detail → status → weibo.cn → weibo.com/{uid}/{bid}。 */
    public static String extractId(String url) {
        String s = url == null ? "" : url;
        for (Pattern p : List.of(DETAIL, STATUS, CN)) {
            Matcher m = p.matcher(s);
            if (m.find()) return m.group(1);
        }
        Matcher m = MID.matcher(s);
        if (m.find()) return m.group(2);
        return "";
    }

    /** Python: _extract_fid —— 视频播放器页 fid（1034:xxx）。 */
    public static String extractFid(String url) {
        Matcher m = FID.matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : "";
    }

    /** Python: _clean_rich —— <br> 保留换行，其余标签去除。 */
    public static String cleanRich(String text) {
        if (text == null || text.isEmpty()) return "";
        String t = text.replaceAll("(?i)<br\\s*/?\\s*>", "\n");
        return t.replaceAll("<[^>]+>", "").strip();
    }

    /** Python: _parse_play_count —— "5万次播放"/"1.2亿次播放"/"999+" → int。 */
    public static int parsePlayCount(JsonNode v) {
        if (v == null || v.isNull()) return 0;
        if (v.isNumber()) return v.asInt();
        String s = v.asText("").strip();
        if (s.isEmpty()) return 0;
        Matcher m = Pattern.compile("([\\d.]+)\\s*亿").matcher(s);
        if (m.find()) return (int) (Double.parseDouble(m.group(1)) * 1e8);
        m = Pattern.compile("([\\d.]+)\\s*万").matcher(s);
        if (m.find()) return (int) (Double.parseDouble(m.group(1)) * 1e4);
        m = Pattern.compile("(\\d+)").matcher(s);
        return m.find() ? Integer.parseInt(m.group(1)) : 0;
    }

    private static String ensureScheme(String url) {
        if (url != null && url.startsWith("//")) return "https:" + url;
        return url == null ? "" : url;
    }

    // ==================== 解析 ====================

    /** 完整解析：URL/ID → MediaItem（statuses/show + 长文本展开）。 */
    public MediaItem parse(String urlOrId, String cookie) throws Exception {
        String id = urlOrId.matches("^\\d+$") ? urlOrId : extractId(urlOrId);
        if (id.isEmpty()) throw new IllegalArgumentException("无法识别的微博链接");
        Map<String, String> headers = new LinkedHashMap<>(HEADERS);
        if (cookie != null && !cookie.isEmpty()) headers.put("Cookie", cookie);

        JsonNode status = http.getJson(STATUS_API + "?id=" + HttpClient.urlEncode(id), headers);
        if (!status.has("id") || !status.has("user")) {
            throw new IllegalStateException("微博接口未返回有效数据（可能需要登录 Cookie）");
        }
        // 长微博：接口未带 longText 时现场展开
        if (status.path("isLongText").asBoolean(false) && !status.has("longText")) {
            try {
                JsonNode ext = http.getJson(LONGTEXT_API + "?id=" + HttpClient.urlEncode(id), headers);
                String content = ext.path("data").path("longTextContent").asText("");
                if (!content.isEmpty()) {
                    ((com.fasterxml.jackson.databind.node.ObjectNode) status)
                            .putObject("longText").put("longTextContent", content);
                }
            } catch (Exception ignored) {
                // 展开失败保留截断文本
            }
        }
        return fromStatus(status, id);
    }

    /** 纯映射：status JSON → MediaItem（可离线测试）。 */
    public static MediaItem fromStatus(JsonNode status, String id) {
        String rawText = status.path("text").asText(status.path("raw_text").asText(""));
        String clean = cleanRich(rawText);
        if (status.path("isLongText").asBoolean(false)) {
            String lt = status.path("longText").path("longTextContent").asText("");
            if (!lt.isEmpty()) clean = cleanRich(lt);
        }
        String title = status.path("status_title").asText("");
        if (title.isEmpty()) {
            title = clean.contains("\n") ? clean.substring(0, clean.indexOf('\n')).strip() : clean;
        }

        var user = status.path("user");
        String author = user.path("screen_name").asText(user.path("name").asText(""));
        String created = status.path("created_at").asText("");

        List<MediaItem.Stream> streams = new ArrayList<>();
        String cover = "";
        var pageInfo = status.path("page_info");
        if (pageInfo.isObject()) {
            var mi = pageInfo.path("media_info");
            String videoUrl = firstNonEmpty(
                    mi.path("stream_url_hd").asText(""),
                    mi.path("stream_url").asText(""),
                    mi.path("mp4_720p_mp4").asText(""),
                    mi.path("mp4_hd_mp4").asText(""),
                    mi.path("mp4_sd_mp4").asText(""),
                    mi.path("mp4_url").asText(""),
                    mi.path("h5_url").asText(""));
            cover = ensureScheme(mi.path("pic_url").asText(pageInfo.path("page_pic").path("url").asText("")));
            if (videoUrl.isEmpty()) {
                var urls = pageInfo.path("urls");
                if (urls.isObject()) {
                    videoUrl = firstNonEmpty(
                            urls.path("mp4_720p_mp4").asText(""),
                            urls.path("mp4_hd_mp4").asText(""),
                            urls.path("mp4_sd_mp4").asText(""),
                            urls.path("mp4_ld_mp4").asText(""));
                }
            }
            if (!videoUrl.isEmpty()) {
                streams.add(new MediaItem.Stream("video", "原画", ensureScheme(videoUrl), List.of(), 0));
            }
        }

        var pics = status.path("pics");
        if (pics.isArray()) {
            for (var p : pics) {
                String url = ensureScheme(p.path("large").path("url").asText(
                        p.path("url").asText("")));
                if (!url.isEmpty()) {
                    streams.add(new MediaItem.Stream("image", "原图", url, List.of(), 0));
                }
            }
        }

        String publishTime = created;
        return MediaItem.of("weibo", id, title, author, cover, publishTime, 0, clean, streams,
                status.toString());
    }

    private static String firstNonEmpty(String... values) {
        for (String v : values) if (v != null && !v.isEmpty()) return v;
        return "";
    }
}
