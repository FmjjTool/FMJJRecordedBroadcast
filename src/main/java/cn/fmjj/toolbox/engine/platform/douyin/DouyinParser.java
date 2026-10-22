package cn.fmjj.toolbox.engine.platform.douyin;

import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 抖音视频解析（分享页 SSR 数据路线）。
 *
 * <p>与 Python 版的差异：原版走 Playwright 浏览器上下文（因为 `aweme/detail` 被 Argus 拦）。
 * 实测发现 **分享页 `iesdouyin.com/share/video/{id}/` 的 `_ROUTER_DATA` 里带完整 SSR 数据**，
 * 纯 HTTP 即可解析，不需要浏览器 —— 这条路线比原版更轻。
 *
 * <p>已知限制：需要真实 aweme_id（短链要先用 {@link DouyinLink} 展开）；
 * 部分账号/视频可能返回空 `videoInfoRes`（风控），此时明确报错。
 */
public final class DouyinParser {

    private static final String SHARE_URL = "https://www.iesdouyin.com/share/video/%s/";

    /** 解析结果。 */
    public record DouyinVideo(String awemeId, String desc, String author, String authorId,
                              String cover, String videoUrl, List<String> images,
                              int durationSeconds, long createTime, int diggCount,
                              int commentCount, int shareCount, String rawJson) {

        public boolean isImagePost() { return !images.isEmpty() && videoUrl.isEmpty(); }

        public String title() {
            String d = desc == null ? "" : desc.strip();
            if (d.isEmpty()) return "抖音视频_" + awemeId;
            int nl = d.indexOf('\n');
            return nl > 0 ? d.substring(0, nl) : d;
        }
    }

    private final HttpClient http;

    public DouyinParser() { this(new HttpClient()); }

    public DouyinParser(HttpClient http) { this.http = http; }

    /** 抓分享页并解析：先试 HTTP（有 SSR 就用），否则用系统浏览器渲染。 */
    public DouyinVideo parse(String awemeId) throws Exception {
        String url = String.format(SHARE_URL, awemeId);
        String httpError = null;
        try {
            String html = http.getText(url, Map.of(
                    "User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                            + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                    "Referer", "https://www.douyin.com/"));
            return fromRouterData(html);
        } catch (Exception e) {
            httpError = e.getMessage();
        }
        // 降级：系统浏览器渲染（DOM 里有 <source src> 直链与 aweme_id）
        if (!DouyinBrowser.available()) {
            throw new IllegalStateException("抖音解析需要浏览器渲染，但未找到 Edge/Chrome。"
                    + "（HTTP 直连失败原因：" + httpError + "）");
        }
        String dom = DouyinBrowser.fetchDom("https://www.douyin.com/video/" + awemeId, 45);
        return fromDom(dom);
    }

    // ==================== DOM 解析（浏览器渲染后） ====================

    private static final java.util.regex.Pattern P_AWEME_ID =
            java.util.regex.Pattern.compile("data-e2e-aweme-id=\"(\\d+)\"");
    private static final java.util.regex.Pattern P_TITLE =
            java.util.regex.Pattern.compile("<title>(.*?)</title>", java.util.regex.Pattern.DOTALL);
    private static final java.util.regex.Pattern P_AUTHOR =
            java.util.regex.Pattern.compile("data-e2e=\"user-info\"[^>]*>([\\s\\S]{0,3000})");
    private static final java.util.regex.Pattern P_SOURCE =
            java.util.regex.Pattern.compile("<source[^>]*src=\"([^\"]+)\"");
    private static final java.util.regex.Pattern P_USER_LINK =
            java.util.regex.Pattern.compile("//www\\.douyin\\.com/user/([A-Za-z0-9_-]{10,})");
    private static final java.util.regex.Pattern P_IMG =
            java.util.regex.Pattern.compile("<img[^>]*src=\"(https://[^\"]*douyinpic\\.com/[^\"]+)\"");

    /** 解析浏览器渲染后的 DOM（可离线测试）。 */
    public static DouyinVideo fromDom(String html) {
        if (html == null || html.isBlank()) throw new IllegalStateException("页面为空");

        String awemeId = "";
        var mId = P_AWEME_ID.matcher(html);
        if (mId.find()) awemeId = mId.group(1);

        String title = "";
        var mTitle = P_TITLE.matcher(html);
        if (mTitle.find()) {
            title = unescape(mTitle.group(1)).strip();
            if (title.endsWith(" - 抖音")) title = title.substring(0, title.length() - 4).strip();
        }

        // 作者：user-info 区块里第一个"非空文本节点"（昵称），后面依次是"认证徽章/音乐人/粉丝…"
        String author = "";
        var mAuthor = P_AUTHOR.matcher(html);
        if (mAuthor.find()) author = firstTextNode(mAuthor.group(1));
        String authorId = "";
        var mUid = P_USER_LINK.matcher(html);
        if (mUid.find()) authorId = mUid.group(1);

        // 视频直链：多条 CDN 镜像，第一条为主
        List<String> sources = new ArrayList<>();
        var mSrc = P_SOURCE.matcher(html);
        while (mSrc.find()) {
            String u = unescape(mSrc.group(1));
            if (u.startsWith("http") && !sources.contains(u)) sources.add(u);
        }
        String videoUrl = sources.isEmpty() ? "" : sources.get(0);

        String cover = "";
        var mImg = P_IMG.matcher(html);
        if (mImg.find()) cover = unescape(mImg.group(1));

        if (awemeId.isEmpty() && title.isEmpty() && videoUrl.isEmpty()) {
            throw new IllegalStateException("页面里没有作品数据（可能已删除/私密，或未加载完成）");
        }
        String desc = title.isEmpty() ? "" : title;
        return new DouyinVideo(awemeId, desc, author, authorId, cover, videoUrl,
                List.of(), 0, 0, 0, 0, 0, "");
    }

    private static String unescape(String s) {
        return s.replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'")
                .replace("&lt;", "<").replace("&gt;", ">");
    }

    /**
     * 取 HTML 片段里第一个非空文本节点。
     * 不能用「去标签 + split 空格」：长 style 属性会让 `&lt;[^&gt;]+&gt;` 匹配不完整，残留 `&lt;div`。
     */
    static String firstTextNode(String block) {
        if (block == null) return "";
        int i = 0;
        while (i < block.length()) {
            int lt = block.indexOf('<', i);
            if (lt < 0) break;
            int gt = block.indexOf('>', lt);
            if (gt < 0) break;
            int nextLt = block.indexOf('<', gt + 1);
            if (nextLt < 0) break;
            String text = unescape(block.substring(gt + 1, nextLt)).strip();
            if (!text.isEmpty()) return text;
            i = nextLt;
        }
        return "";
    }

    /** 纯解析：HTML → DouyinVideo（可离线测试）。 */
    public static DouyinVideo fromRouterData(String html) throws Exception {
        String json = extractRouterData(html);
        if (json == null) {
            // 实测：真实视频的分享页 / PC 页都是 72KB 左右空壳（JS 渲染 + 风控），不含任何数据
            boolean shellPage = html != null && html.length() > 1000;
            throw new IllegalStateException(shellPage
                    ? "抖音对纯 HTTP 请求返回空壳页面（JS 渲染 + 风控），需要浏览器上下文才能解析。"
                      + "当前版本暂不支持抖音解析，请用原 Python 版处理，或等待浏览器方案（路线图 M5-浏览器）"
                    : "页面内容异常（可能链接无效）");
        }
        JsonNode root = HttpClient.parseJson(json);
        JsonNode page = root.path("loaderData").path("video_(id)/page");
        JsonNode res = page.path("videoInfoRes");
        if (!res.has("item_list") || res.path("item_list").isEmpty()) {
            String filter = res.path("filter_list").toString();
            throw new IllegalStateException("视频信息为空（作品可能已删除/私密，或该请求未获得 SSR 数据）"
                    + (filter.isEmpty() || "[]".equals(filter) ? "" : "：" + filter));
        }
        return fromItem(res.path("item_list").get(0));
    }

    /** 字段映射（可离线测试）。 */
    public static DouyinVideo fromItem(JsonNode item) {
        String awemeId = item.path("aweme_id").asText("");
        String desc = item.path("desc").asText("");
        var author = item.path("author");
        String nickname = author.path("nickname").asText("");
        String authorId = author.path("sec_uid").asText(author.path("uid").asText(""));

        var video = item.path("video");
        String cover = firstUrl(video.path("cover"));
        String playUrl = firstUrl(video.path("play_addr"));
        // 分享页给的是带水印地址（playwm），换成 play 去水印
        if (playUrl.contains("playwm")) playUrl = playUrl.replace("playwm", "play");
        int durationMs = video.path("duration").asInt(0);

        List<String> images = new ArrayList<>();
        var imageList = item.path("images");
        if (imageList.isArray()) {
            for (JsonNode img : imageList) {
                String u = firstUrl(img);
                if (!u.isEmpty()) images.add(u);
            }
        }

        var stats = item.path("statistics");
        return new DouyinVideo(awemeId, desc, nickname, authorId, cover, playUrl, images,
                durationMs / 1000, item.path("create_time").asLong(0),
                stats.path("digg_count").asInt(0),
                stats.path("comment_count").asInt(0),
                stats.path("share_count").asInt(0),
                item.toString());
    }

    private static String firstUrl(JsonNode node) {
        var list = node.path("url_list");
        if (list.isArray()) {
            for (JsonNode u : list) {
                String s = u.asText("");
                if (!s.isEmpty()) return s;
            }
        }
        return node.path("url").asText("");
    }

    /**
     * 从 HTML 提取 `_ROUTER_DATA = {...}` 的 JSON（平衡括号扫描，字符串/转义安全）。
     * 不用正则 `\{.*\}` 是因为 JSON 里可能含 `</script>` 之外的 `}`。
     */
    public static String extractRouterData(String html) {
        if (html == null) return null;
        int marker = html.indexOf("_ROUTER_DATA");
        if (marker < 0) return null;
        int start = html.indexOf('{', marker);
        if (start < 0) return null;
        int depth = 0;
        boolean inString = false;
        boolean escape = false;
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

    /** 供 UI 展示的元信息行。 */
    public static Map<String, String> summary(DouyinVideo v) {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("作者", v.author());
        m.put("时长", v.durationSeconds() > 0
                ? String.format("%02d:%02d", v.durationSeconds() / 60, v.durationSeconds() % 60) : "—");
        m.put("点赞", String.valueOf(v.diggCount()));
        m.put("评论", String.valueOf(v.commentCount()));
        m.put("类型", v.isImagePost() ? "图集（" + v.images().size() + " 张）" : "视频");
        return m;
    }
}
