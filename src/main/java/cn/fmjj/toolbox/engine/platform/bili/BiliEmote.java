package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * B站表情包（对应 Python 版 bili_emoji.py 的表情包搜索）。
 *
 * <p>接口：`x/emote/user/panel/web?business=reply`（需登录 Cookie；实测返回 11 个包、每包 20 个表情）。
 */
public final class BiliEmote {

    private static final String PANEL_API = "https://api.bilibili.com/x/emote/user/panel/web";

    /** 一个表情。 */
    public record Emote(String text, String url, String gifUrl, String packageName) {
        /** 优先 GIF（动图），没有则静态图。 */
        public String bestUrl() {
            return gifUrl == null || gifUrl.isEmpty() ? url : gifUrl;
        }
    }

    /** 一个表情包。 */
    public record Package(String name, List<Emote> emotes) {}

    private final HttpClient http = new HttpClient();

    private Map<String, String> headers(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", "https://www.bilibili.com/");
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    /** 拉全部表情包。 */
    public List<Package> fetchPackages(String cookie) throws Exception {
        JsonNode d = http.getJson(PANEL_API + "?business=reply", headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("获取表情包失败（需登录 Cookie）："
                    + d.path("message").asText("code=" + d.path("code").asInt()));
        }
        return parse(d.path("data"));
    }

    /** 纯解析（可离线测试）。 */
    public static List<Package> parse(JsonNode data) {
        List<Package> out = new ArrayList<>();
        JsonNode pkgs = data.path("packages");
        if (!pkgs.isArray()) return out;
        for (JsonNode p : pkgs) {
            String name = p.path("text").asText("");
            if (name.isEmpty()) name = p.path("pkg_name").asText("");
            if (name.isEmpty()) name = p.path("emote_type").asText("表情包");
            List<Emote> emotes = new ArrayList<>();
            JsonNode arr = p.path("emote");
            if (arr.isArray()) {
                for (JsonNode e : arr) {
                    emotes.add(new Emote(
                            e.path("text").asText(""),
                            norm(e.path("url").asText("")),
                            norm(e.path("gif_url").asText("")),
                            name));
                }
            }
            if (!emotes.isEmpty()) out.add(new Package(name, emotes));
        }
        return out;
    }

    private static String norm(String u) {
        if (u == null || u.isEmpty()) return "";
        return u.startsWith("//") ? "https:" + u : u;
    }

    /** 扁平化：所有表情（用于搜索）。 */
    public static List<Emote> flatten(List<Package> packages) {
        List<Emote> out = new ArrayList<>();
        for (Package p : packages) out.addAll(p.emotes());
        return out;
    }

    /** 按名字搜索（匹配表情名或包名，忽略大小写）。 */
    public static List<Emote> search(List<Package> packages, String keyword) {
        if (keyword == null || keyword.isBlank()) return flatten(packages);
        String kw = keyword.trim().toLowerCase(java.util.Locale.ROOT);
        List<Emote> out = new ArrayList<>();
        for (Package p : packages) {
            for (Emote e : p.emotes()) {
                if (e.text().toLowerCase(java.util.Locale.ROOT).contains(kw)
                        || e.packageName().toLowerCase(java.util.Locale.ROOT).contains(kw)) {
                    out.add(e);
                }
            }
        }
        return out;
    }

    /** 文件名安全化（表情名可能含 [ ] 等字符）。 */
    public static String safeName(Emote e, int index) {
        String base = e.text().replaceAll("[\\\\/:*?\"<>|\\[\\]]", "").strip();
        if (base.isEmpty()) base = "emote_" + index;
        String ext = e.bestUrl().toLowerCase(java.util.Locale.ROOT).contains(".gif") ? ".gif" : ".png";
        return base + ext;
    }
}
