package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * B站表情包（对应 Python 版 bili_emoji.py 的表情包搜索 / 装扮表情 / 直播间表情）。
 *
 * <p>接口：
 * <ul>
 *   <li>全部表情：{@code x/emote/user/panel/web?business=reply}（需登录）</li>
 *   <li>表情包详情：{@code x/emote/package?ids=&business=reply}</li>
 *   <li>直播间表情：{@code xlive/web-ucenter/v2/emoticon/GetEmoticons}（需登录）</li>
 * </ul>
 */
public final class BiliEmote {

    private static final String PANEL_API = "https://api.bilibili.com/x/emote/user/panel/web";
    private static final String PACKAGE_API = "https://api.bilibili.com/x/emote/package";
    private static final String ROOM_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info";
    private static final String MASTER_INFO_API = "https://api.live.bilibili.com/live_user/v1/Master/info";
    private static final String APP_ROOM_API =
            "https://api.live.bilibili.com/xlive/app-room/v1/index/getInfoByRoom";
    private static final String ROOM_EMOTE_API =
            "https://api.live.bilibili.com/xlive/web-ucenter/v2/emoticon/GetEmoticons";

    /** 一个表情。 */
    public record Emote(String text, String url, String gifUrl, String packageName) {
        /** 优先 GIF（动图），没有则静态图。 */
        public String bestUrl() {
            return gifUrl == null || gifUrl.isEmpty() ? url : gifUrl;
        }
    }

    /** 一个表情包。 */
    public record Package(String name, List<Emote> emotes) {}

    /** 直播间资源（房间信息 + 专属/通用表情）。 */
    public static final class LiveAssets {
        public String roomId = "";
        public String realRoomId = "";
        public String title = "";
        public String anchor = "";
        public String face = "";
        public String cover = "";
        public String background = "";
        public String appBackground = "";
        public String keyframe = "";
        public final List<JsonNode> packages = new ArrayList<>();
    }

    private final HttpClient http = new HttpClient();

    private Map<String, String> headers(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", "https://www.bilibili.com/");
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    private Map<String, String> liveHeaders(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", "https://live.bilibili.com/");
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

    /** 按表情包 ID 列表拉详情（packages 数组，含 url/gif_url/webp_url）。 */
    public List<JsonNode> emotePackages(List<String> ids, String cookie) throws Exception {
        if (ids == null || ids.isEmpty()) return List.of();
        String url = PACKAGE_API + "?ids=" + HttpClient.urlEncode(String.join(",", ids))
                + "&business=reply";
        JsonNode d = http.getJson(url, headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("表情包查询失败：" + d.path("message").asText()
                    + "（code=" + d.path("code").asInt() + "）");
        }
        List<JsonNode> out = new ArrayList<>();
        JsonNode pkgs = d.path("data").path("packages");
        if (pkgs.isArray()) pkgs.forEach(out::add);
        return out;
    }

    /** 直播间：房间信息 + 主播头像 + 背景/关键帧 + 直播间表情（表情需登录）。 */
    public LiveAssets liveAssets(String roomId, String cookie) throws Exception {
        LiveAssets out = new LiveAssets();
        out.roomId = roomId;
        JsonNode room = http.getJson(ROOM_INFO_API + "?room_id=" + HttpClient.urlEncode(roomId),
                liveHeaders(cookie));
        if (room.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("房间不存在或已关停：" + room.path("message").asText());
        }
        JsonNode data = room.path("data");
        out.realRoomId = data.path("room_id").asText(roomId);
        out.title = data.path("title").asText("");
        out.cover = norm(data.path("user_cover").asText(""));
        out.keyframe = norm(data.path("keyframe").asText(""));
        out.background = norm(data.path("background").asText(""));
        String uid = data.path("uid").asText("");
        if (!uid.isEmpty()) {
            try {
                JsonNode master = http.getJson(MASTER_INFO_API + "?uid="
                        + HttpClient.urlEncode(uid), liveHeaders(cookie));
                JsonNode info = master.path("data").path("info");
                out.anchor = info.path("uname").asText("");
                out.face = norm(info.path("face").asText(""));
            } catch (Exception ignored) {
            }
        }
        // App 端详情（App 签名）：拿 app 背景图与更完整的头像/昵称
        try {
            Map<String, String> params = new LinkedHashMap<>();
            params.put("room_id", out.realRoomId);
            params.put("platform", "android");
            params.put("device", "android");
            Map<String, String> signed = WbiSigner.signApp(params);
            StringBuilder qs = new StringBuilder();
            for (Map.Entry<String, String> e : signed.entrySet()) {
                if (qs.length() > 0) qs.append('&');
                qs.append(e.getKey()).append('=').append(
                        URLEncoder.encode(e.getValue() == null ? "" : e.getValue(),
                                StandardCharsets.UTF_8));
            }
            JsonNode app = http.getJson(APP_ROOM_API + "?" + qs, liveHeaders(cookie));
            if (app.path("code").asInt(-1) == 0) {
                JsonNode d = app.path("data");
                JsonNode ri = d.path("room_info");
                JsonNode ai = d.path("anchor_info").path("base_info");
                if (out.appBackground.isEmpty()) {
                    out.appBackground = norm(ri.path("app_background").asText(""));
                }
                if (out.anchor.isEmpty()) out.anchor = ai.path("uname").asText("");
                if (out.face.isEmpty()) out.face = norm(ai.path("face").asText(""));
            }
        } catch (Exception ignored) {
        }
        if (cookie != null && !cookie.isBlank()) {
            try {
                JsonNode em = http.getJson(ROOM_EMOTE_API + "?platform=pc&room_id="
                        + HttpClient.urlEncode(out.realRoomId), liveHeaders(cookie));
                if (em.path("code").asInt(-1) == 0) {
                    JsonNode arr = em.path("data").path("data");
                    if (arr.isArray()) arr.forEach(out.packages::add);
                }
            } catch (Exception ignored) {
            }
        }
        return out;
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
