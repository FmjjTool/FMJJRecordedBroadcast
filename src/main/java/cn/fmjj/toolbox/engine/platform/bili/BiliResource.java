package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * B站资源：空间信息 + 综合搜索（对应 Python 版 bili_info.py / api.py 的两个接口）。
 *
 * <ul>
 *   <li>空间卡片：{@code x/web-interface/card}（免签名）</li>
 *   <li>综合搜索：{@code x/web-interface/wbi/search/all/v2}（需 WBI 签名）</li>
 * </ul>
 */
public final class BiliResource {

    private static final String CARD_API = "https://api.bilibili.com/x/web-interface/card";
    private static final String SEARCH_API = "https://api.bilibili.com/x/web-interface/wbi/search/all/v2";
    private static final String NAV_API = "https://api.bilibili.com/x/web-interface/nav";

    /** 空间信息。 */
    public record SpaceCard(String mid, String name, String sign, int fans, int attention,
                            int likes, String face, String topPhoto, String pendantUrl, int level) {
        public String fansText() {
            return fans >= 10000 ? String.format("%.1f万", fans / 10000.0) : String.valueOf(fans);
        }
    }

    /** 搜索结果项。 */
    public record SearchItem(String type, String title, String author, String url,
                             String cover, String desc) {}

    private final HttpClient http;

    public BiliResource() { this(new HttpClient()); }

    public BiliResource(HttpClient http) { this.http = http; }

    private static Map<String, String> headers(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", WbiSigner.REFERER_BILI);
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    // ==================== 空间 ====================

    public SpaceCard spaceCard(String mid, String cookie) throws Exception {
        String url = CARD_API + "?mid=" + HttpClient.urlEncode(mid) + "&photo=true";
        JsonNode d = http.getJson(url, headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException(d.path("message").asText("获取空间信息失败"));
        }
        return fromCard(d.path("data"));
    }

    /** card 响应 → SpaceCard（纯映射，可离线测试）。 */
    public static SpaceCard fromCard(JsonNode data) {
        JsonNode card = data.path("card");
        JsonNode space = data.path("space");
        return new SpaceCard(
                card.path("mid").asText(""),
                card.path("name").asText(""),
                card.path("sign").asText(""),
                card.path("fans").asInt(data.path("follower").asInt(0)),
                card.path("attention").asInt(0),
                card.path("likes").asInt(0),
                card.path("face").asText(""),
                space.path("l_img").asText(""),
                card.path("pendant").path("image").asText(""),
                card.path("level_info").path("current_level").asInt(0));
    }

    /** 空间可下载资源清单：(名称, URL)。 */
    public static List<Map.Entry<String, String>> spaceFiles(SpaceCard c) {
        List<Map.Entry<String, String>> out = new ArrayList<>();
        add(out, "头像", c.face());
        add(out, "空间背景图", c.topPhoto());
        add(out, "头像框", c.pendantUrl());
        return out;
    }

    private static void add(List<Map.Entry<String, String>> out, String name, String url) {
        if (url != null && !url.isEmpty()) out.add(Map.entry(name, url));
    }

    // ==================== 综合搜索（WBI） ====================

    /** 取 WBI keys（nav 接口，未登录也能拿到 img_url/sub_url）。 */
    public WbiSigner.WbiKeys fetchKeys(String cookie) throws Exception {
        JsonNode d = http.getJson(NAV_API, headers(cookie));
        JsonNode wbi = d.path("data").path("wbi_img");
        String img = wbi.path("img_url").asText("");
        String sub = wbi.path("sub_url").asText("");
        if (img.isEmpty() || sub.isEmpty()) throw new IllegalStateException("未取到 WBI 密钥（nav 接口异常）");
        return new WbiSigner.WbiKeys(basename(img), basename(sub), d.path("data").path("wbi_img").asText(""));
    }

    /** 从 URL 取文件名去扩展名（img_url 末尾那段）。 */
    static String basename(String url) {
        String s = url;
        int q = s.indexOf('?');
        if (q >= 0) s = s.substring(0, q);
        int slash = s.lastIndexOf('/');
        if (slash >= 0) s = s.substring(slash + 1);
        int dot = s.lastIndexOf('.');
        return dot > 0 ? s.substring(0, dot) : s;
    }

    /** 综合搜索（用户 + 视频）。 */
    public List<SearchItem> searchAll(String keyword, int page, String cookie) throws Exception {
        var keys = fetchKeys(cookie);
        Map<String, String> params = new LinkedHashMap<>();
        params.put("keyword", keyword);
        params.put("page", String.valueOf(page));
        String signed = WbiSigner.signUrl(SEARCH_API, params, keys);
        JsonNode d = http.getJson(signed, headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException(d.path("message").asText("搜索失败"));
        }
        return parseSearch(d.path("data"));
    }

    /** 搜索结果 → 扁平列表（纯映射，可离线测试）。 */
    public static List<SearchItem> parseSearch(JsonNode data) {
        List<SearchItem> out = new ArrayList<>();
        JsonNode groups = data.path("result");
        if (!groups.isArray()) return out;
        for (JsonNode group : groups) {
            String type = group.path("result_type").asText("");
            JsonNode arr = group.path("data");
            if (!arr.isArray()) continue;
            for (JsonNode it : arr) {
                String title = stripHtml(it.path("title").asText(""));
                switch (type) {
                    case "video" -> out.add(new SearchItem("视频", title,
                            stripHtml(it.path("author").asText("")),
                            "https://www.bilibili.com/video/" + it.path("bvid").asText(""),
                            normUrl(it.path("pic").asText("")),
                            stripHtml(it.path("description").asText(""))));
                    case "bili_user" -> out.add(new SearchItem("用户", title,
                            "UID " + it.path("mid").asText(""),
                            "https://space.bilibili.com/" + it.path("mid").asText(""),
                            normUrl(it.path("upic").asText("")),
                            stripHtml(it.path("usign").asText(""))));
                    default -> { }
                }
            }
        }
        return out;
    }

    /** B站图片地址常以 // 开头，补 https。 */
    static String normUrl(String u) {
        if (u == null || u.isEmpty()) return "";
        return u.startsWith("//") ? "https:" + u : u;
    }

    /** 去掉搜索结果里的 &lt;em&gt; 高亮标签。 */
    public static String stripHtml(String s) {
        return s == null ? "" : s.replaceAll("<[^>]+>", "").replace("&amp;", "&").strip();
    }
}
