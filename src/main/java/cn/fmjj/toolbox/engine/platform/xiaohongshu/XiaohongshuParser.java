package cn.fmjj.toolbox.engine.platform.xiaohongshu;

import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 小红书笔记解析（探索页 SSR 路线）。
 *
 * <p><b>能力边界（实测结论）</b>：
 * <ul>
 *   <li>✅ 带 Cookie 请求 {@code /explore} → {@code __INITIAL_STATE__.feed.feeds}
 *       有推荐流笔记（id / 标题 / 作者 / 类型 / 时长 / 封面 / xsecToken）</li>
 *   <li>✅ 登录态：{@code user.loggedIn} + 昵称</li>
 *   <li>❌ 笔记详情页（{@code /explore/{id}?xsec_token=}）SSR **不含笔记数据**，
 *       媒体（视频直链/图片原图）只能走 {@code edith.xiaohongshu.com/api/sns/web/v1/feed}，
 *       该接口需要 **x-s 签名**（前端 JS 生成）</li>
 *   <li>❌ headless 浏览器直接访问会被「安全限制」页拦截</li>
 * </ul>
 * 因此当前只做「浏览 + 跳转」，媒体下载需补 x-s 签名（见路线图 3.18）。
 */
public final class XiaohongshuParser {

    private static final String EXPLORE_URL = "https://www.xiaohongshu.com/explore";

    /** 笔记摘要。 */
    public record Note(String id, String title, String author, String type, int durationSeconds,
                       String cover, String xsecToken) {
        /** 带 xsec_token 的网页链接（可直接在浏览器打开）。 */
        public String webUrl() {
            return "https://www.xiaohongshu.com/explore/" + id
                    + (xsecToken == null || xsecToken.isEmpty() ? "" : "?xsec_token=" + xsecToken
                    + "&xsec_source=pc_feed");
        }

        public String durationText() {
            if (durationSeconds <= 0) return "—";
            return String.format("%02d:%02d", durationSeconds / 60, durationSeconds % 60);
        }
    }

    /** 登录态。 */
    public record LoginState(boolean loggedIn, String nickname) {}

    private final HttpClient http;

    public XiaohongshuParser() { this(new HttpClient()); }

    public XiaohongshuParser(HttpClient http) { this.http = http; }

    private static Map<String, String> headers(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", HttpClient.UA_CHROME);
        h.put("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
        h.put("Accept-Language", "zh-CN,zh;q=0.9");
        h.put("Referer", "https://www.xiaohongshu.com/");
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    /** 抓探索页 HTML（带 Cookie 才有 feeds）。 */
    public String fetchExplore(String cookie) throws Exception {
        return http.getText(EXPLORE_URL, headers(cookie));
    }

    /** 推荐流笔记列表。 */
    public List<Note> exploreFeed(String cookie) throws Exception {
        return parseExplore(fetchExplore(cookie)).notes();
    }

    /** 探索页解析结果（笔记 + 登录态）。 */
    public record ExploreResult(List<Note> notes, LoginState login) {}

    public static ExploreResult parseExplore(String html) throws Exception {
        JsonNode root = extractState(html);
        List<Note> notes = new ArrayList<>();
        JsonNode feeds = root.path("feed").path("feeds");
        if (feeds.isArray()) {
            for (JsonNode f : feeds) notes.add(fromFeedItem(f));
        }
        JsonNode user = root.path("user");
        var login = new LoginState(user.path("loggedIn").asBoolean(false),
                user.path("userInfo").path("nickname").asText(""));
        return new ExploreResult(notes, login);
    }

    /** feed 单项 → Note（纯映射，可离线测试）。 */
    static Note fromFeedItem(JsonNode f) {
        JsonNode card = f.path("noteCard");
        String type = card.path("type").asText("");
        int duration = card.path("video").path("capa").path("duration").asInt(0);
        String cover = firstNonEmpty(
                card.path("cover").path("urlDefault").asText(""),
                card.path("cover").path("url").asText(""),
                card.path("cover").path("urlPre").asText(""));
        return new Note(
                f.path("id").asText(""),
                card.path("displayTitle").asText(""),
                card.path("user").path("nickname").asText(""),
                type,
                duration,
                cover,
                f.path("xsecToken").asText(""));
    }

    private static String firstNonEmpty(String... values) {
        for (String v : values) if (v != null && !v.isEmpty()) return v;
        return "";
    }

    /**
     * 提取 {@code __INITIAL_STATE__ = {...}}。
     *
     * <p>注意：小红的 SSR 里含 JS 的 {@code undefined}，Jackson 不接受，需替换成 null。
     */
    public static JsonNode extractState(String html) throws Exception {
        if (html == null) throw new IllegalStateException("页面为空");
        int marker = html.indexOf("__INITIAL_STATE__");
        if (marker < 0) {
            throw new IllegalStateException(html.contains("安全限制")
                    ? "被小红书安全校验拦截（headless/无登录态会被拒）"
                    : "页面里没有 __INITIAL_STATE__");
        }
        int start = html.indexOf('{', marker);
        if (start < 0) throw new IllegalStateException("状态数据格式异常");
        String json = balancedJson(html, start);
        if (json == null) throw new IllegalStateException("状态数据截断");
        json = json.replace("undefined", "null");
        return new ObjectMapper().readTree(json);
    }

    /** 平衡括号扫描（字符串/转义安全）。 */
    static String balancedJson(String text, int start) {
        int depth = 0;
        boolean inString = false, escape = false;
        for (int i = start; i < text.length(); i++) {
            char c = text.charAt(i);
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
                if (depth == 0) return text.substring(start, i + 1);
            }
        }
        return null;
    }

    /** 从链接里取 noteId（explore/{id} / discovery/item/{id} / 纯 id）。 */
    public static String extractNoteId(String text) {
        String s = text == null ? "" : text.strip();
        if (s.isEmpty()) return "";
        var m = java.util.regex.Pattern.compile("(?:explore|discovery/item|note)/([0-9a-f]{24})").matcher(s);
        if (m.find()) return m.group(1);
        m = java.util.regex.Pattern.compile("([0-9a-f]{24})").matcher(s);
        return m.find() ? m.group(1) : "";
    }

    /** 从链接里取 xsec_token。 */
    public static String extractXsecToken(String text) {
        String s = text == null ? "" : text;
        var m = java.util.regex.Pattern.compile("xsec_token=([A-Za-z0-9_\\-=]+)").matcher(s);
        return m.find() ? m.group(1) : "";
    }

    /** 给 UI 用的空节点（占位）。 */
    static ObjectNode emptyNode() {
        return new ObjectMapper().createObjectNode();
    }
}
