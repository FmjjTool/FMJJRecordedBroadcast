package cn.fmjj.toolbox.engine.platform.kuaishou;

import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 快手视频解析（网页版 SSR `__APOLLO_STATE__`）。
 *
 * <p>与 Python 版差异：原版用 Playwright 拦截 `/rest/v/profile/feed`（需指纹+签名）；
 * 实测网页版 short-video 页在**带完整请求头**（Accept / Accept-Language）时返回
 * 75KB SSR 页面，内含 `window.__APOLLO_STATE__` 的 GraphQL 缓存，纯 HTTP 可读。
 *
 * <p>结构搜索策略：Apollo 缓存的 key 形态（`Photo:xxx` / `VisionVideoDetailPhoto:xxx`）
 * 会随版本变化，所以用 DFS 找「含 caption 且有视频地址字段」的对象，不写死路径。
 */
public final class KuaishouParser {

    private static final String SHORT_VIDEO_URL = "https://www.kuaishou.com/short-video/%s";

    /** 解析结果。 */
    public record KuaishouVideo(String photoId, String caption, String author, String cover,
                                String videoUrl, int durationSeconds, String rawJson) {
        public String title() {
            String c = caption == null ? "" : caption.strip();
            if (c.isEmpty()) return "快手视频_" + photoId;
            int nl = c.indexOf('\n');
            return nl > 0 ? c.substring(0, nl) : c;
        }
    }

    private final HttpClient http;

    public KuaishouParser() { this(new HttpClient()); }

    public KuaishouParser(HttpClient http) { this.http = http; }

    private static Map<String, String> headers() {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", HttpClient.UA_CHROME);
        h.put("Referer", "https://www.kuaishou.com/");
        h.put("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
        h.put("Accept-Language", "zh-CN,zh;q=0.9");
        return h;
    }

    /** 抓页面并解析。 */
    public KuaishouVideo parse(String photoId) throws Exception {
        String html = http.getText(String.format(SHORT_VIDEO_URL, photoId), headers());
        if (html.length() < 2000) {
            // 未带完整 header 时快手返回 {"result":2,...}
            throw new IllegalStateException("快手返回了风控响应（页面过短），请稍后重试");
        }
        return fromHtml(html);
    }

    /** 纯解析：HTML → KuaishouVideo（可离线测试）。 */
    public static KuaishouVideo fromHtml(String html) throws Exception {
        String json = extractApolloState(html);
        if (json == null) throw new IllegalStateException("页面里没有 __APOLLO_STATE__（可能被风控或链接无效）");
        JsonNode root = HttpClient.parseJson(json);

        JsonNode photo = findPhoto(root);
        if (photo == null) {
            throw new IllegalStateException("未找到作品数据（可能已删除/私密，或需要登录）");
        }
        return fromPhoto(photo);
    }

    /** 从 Apollo 缓存里 DFS 找作品对象（含 caption 且含视频地址）。 */
    static JsonNode findPhoto(JsonNode node) {
        if (node == null) return null;
        if (node.isObject()) {
            if (node.has("caption") && (node.has("photoUrl") || node.has("manifest")
                    || node.has("playUrl") || node.has("srcNoMark"))) {
                return node;
            }
            var it = node.fields();
            while (it.hasNext()) {
                JsonNode found = findPhoto(it.next().getValue());
                if (found != null) return found;
            }
        } else if (node.isArray()) {
            for (JsonNode child : node) {
                JsonNode found = findPhoto(child);
                if (found != null) return found;
            }
        }
        return null;
    }

    /** 作品对象 → 统一结果（多 key 名兜底）。 */
    static KuaishouVideo fromPhoto(JsonNode p) {
        String photoId = firstText(p, "photoId", "id", "photo_id");
        String caption = firstText(p, "caption", "title", "desc");
        String author = firstText(p, "userName", "user_name");
        if (author.isEmpty()) {
            JsonNode user = p.path("user");
            author = firstText(user, "userName", "user_name", "name");
        }
        String cover = firstText(p, "coverUrl", "cover_url", "poster");
        if (cover.isEmpty()) {
            JsonNode covers = p.path("coverUrls");
            if (covers.isArray() && !covers.isEmpty()) cover = firstText(covers.get(0), "url");
        }
        String videoUrl = pickVideoUrl(p);
        int durationMs = p.path("duration").asInt(0);
        return new KuaishouVideo(photoId, caption, author, cover, videoUrl, durationMs / 1000, p.toString());
    }

    /** 视频直链：manifest.adaptationSet[].representation[].url 优先，其次 photoUrl/playUrl。 */
    static String pickVideoUrl(JsonNode p) {
        JsonNode manifest = p.path("manifest");
        if (manifest.isObject()) {
            JsonNode sets = manifest.path("adaptationSet");
            if (sets.isArray()) {
                List<String> urls = new ArrayList<>();
                for (JsonNode set : sets) {
                    for (JsonNode rep : set.path("representation")) {
                        String u = firstText(rep, "url", "backupUrl");
                        if (!u.isEmpty() && !urls.contains(u)) urls.add(u);
                    }
                }
                if (!urls.isEmpty()) {
                    // 优先非 H265（兼容性更好），否则取第一条
                    for (String u : urls) if (!u.contains("h265")) return u;
                    return urls.get(0);
                }
            }
        }
        for (String key : List.of("photoUrl", "playUrl", "srcNoMark", "src")) {
            String u = firstText(p, key);
            if (!u.isEmpty()) return u;
        }
        return "";
    }

    private static String firstText(JsonNode node, String... keys) {
        if (node == null || !node.isObject()) return "";
        for (String k : keys) {
            JsonNode v = node.get(k);
            if (v != null && v.isValueNode()) {
                String s = v.asText("");
                if (!s.isEmpty()) return s;
            }
        }
        return "";
    }

    /** 提取 `window.__APOLLO_STATE__ = {...}`（平衡括号扫描）。 */
    public static String extractApolloState(String html) {
        if (html == null) return null;
        int marker = html.indexOf("__APOLLO_STATE__");
        if (marker < 0) return null;
        int start = html.indexOf('{', marker);
        if (start < 0) return null;
        int depth = 0;
        boolean inString = false, escape = false;
        for (int i = start; i < html.length(); i++) {
            char c = html.charAt(i);
            if (inString) {
                if (escape) escape = false;
                else if (c == '\\') escape = true;
                else if (c == '"') inString = false;
                continue;
            }
            if (c == '"') inString = true;
            else if (c == '{') depth++;
            else if (c == '}') {
                depth--;
                if (depth == 0) return html.substring(start, i + 1);
            }
        }
        return null;
    }

    /** Python: extract_id —— short-video / f/ 短链 / 纯 ID。 */
    public static String extractId(String text) {
        String s = text == null ? "" : text.strip();
        if (s.isEmpty()) return "";
        var m = java.util.regex.Pattern.compile("short-video/([A-Za-z0-9_-]+)").matcher(s);
        if (m.find()) return m.group(1);
        m = java.util.regex.Pattern.compile("kuaishou\\.com/f/([A-Za-z0-9_-]+)").matcher(s);
        if (m.find()) return m.group(1);
        return s.matches("[A-Za-z0-9_-]{8,}") ? s : "";
    }
}
