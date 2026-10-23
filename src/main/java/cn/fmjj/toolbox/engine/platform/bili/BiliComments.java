package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * B站评论区抓取（移植自 Python 版 toolbox/bili_comments.py）。
 *
 * <p>接口：`x/v2/reply`（**WBI 签名**），参数 type=1/oid=aid/pn/ps=20/sort=2（热评）。
 * 游客可翻前几页，深翻页需登录 Cookie（会返回空 replies）。
 */
public final class BiliComments {

    private static final String REPLY_API = "https://api.bilibili.com/x/v2/reply";
    private static final int PAGE_SIZE = 20;

    /** 一条评论。 */
    public record Comment(String user, String content, int likes, long ctime,
                          List<String> images, long rpid, String avatar) {
        public String timeText() {
            return java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
                    .format(java.time.LocalDateTime.ofInstant(
                            java.time.Instant.ofEpochSecond(ctime), java.time.ZoneId.systemDefault()));
        }
    }

    private final HttpClient http = new HttpClient();

    private Map<String, String> headers(String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", "https://www.bilibili.com/");
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    /** 取 WBI keys。 */
    private WbiSigner.WbiKeys fetchKeys(String cookie) throws Exception {
        JsonNode d = http.getJson("https://api.bilibili.com/x/web-interface/nav", headers(cookie));
        var wbi = d.path("data").path("wbi_img");
        String img = basename(wbi.path("img_url").asText(""));
        String sub = basename(wbi.path("sub_url").asText(""));
        if (img.isEmpty() || sub.isEmpty()) throw new IllegalStateException("未取到 WBI 密钥");
        return new WbiSigner.WbiKeys(img, sub, "");
    }

    private static String basename(String url) {
        String s = url;
        int q = s.indexOf('?');
        if (q >= 0) s = s.substring(0, q);
        int slash = s.lastIndexOf('/');
        if (slash >= 0) s = s.substring(slash + 1);
        int dot = s.lastIndexOf('.');
        return dot > 0 ? s.substring(0, dot) : s;
    }

    /** BV 号 → aid（走 view 接口）。 */
    public long aidOf(String idOrBvid, String cookie) throws Exception {
        if (idOrBvid.matches("\\d+")) return Long.parseLong(idOrBvid);
        JsonNode d = http.getJson("https://api.bilibili.com/x/web-interface/view?bvid="
                + HttpClient.urlEncode(idOrBvid), headers(cookie));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("无法解析视频：" + d.path("message").asText(""));
        }
        return d.path("data").path("aid").asLong(0);
    }

    /**
     * 翻页抓评论。
     *
     * @param idOrBvid BV 号或 aid
     * @param pages    最多翻几页（每页 20 条）
     */
    public List<Comment> fetch(String idOrBvid, int pages, String cookie,
                               java.util.function.Consumer<String> progress) throws Exception {
        long aid = aidOf(idOrBvid, cookie);
        var keys = fetchKeys(cookie);
        List<Comment> out = new ArrayList<>();
        for (int pn = 1; pn <= pages; pn++) {
            Map<String, String> params = new LinkedHashMap<>();
            params.put("type", "1");
            params.put("oid", String.valueOf(aid));
            params.put("pn", String.valueOf(pn));
            params.put("ps", String.valueOf(PAGE_SIZE));
            params.put("sort", "2");
            String url = WbiSigner.signUrl(REPLY_API, params, keys);
            JsonNode d = http.getJson(url, headers(cookie));
            int code = d.path("code").asInt(-1);
            if (code != 0) {
                String hint = code == -404 ? "（评论区不存在：视频已删除或未开启评论）"
                        : pn > 1 ? "（游客深翻页需登录 Cookie）" : "（" + d.path("message").asText() + "）";
                if (progress != null) progress.accept("第 " + pn + " 页失败" + hint);
                break;
            }
            JsonNode replies = d.path("data").path("replies");
            if (!replies.isArray() || replies.isEmpty()) break;
            for (JsonNode r : replies) out.add(fromReply(r));
            if (progress != null) progress.accept("已抓取 " + out.size() + " 条（第 " + pn + " 页）");
            // 没有更多页
            JsonNode page = d.path("data").path("page");
            int count = page.path("count").asInt(0);
            if ((long) pn * PAGE_SIZE >= count) break;
            Thread.sleep(300);   // 轻微限速，避免风控
        }
        return out;
    }

    /** reply 节点 → Comment（纯映射，可离线测试）。 */
    public static Comment fromReply(JsonNode r) {
        List<String> images = new ArrayList<>();
        JsonNode content = r.path("content");
        if (content.has("pictures")) {
            for (JsonNode pic : content.path("pictures")) {
                String u = pic.path("img_src").asText("");
                if (!u.isEmpty()) images.add(u.startsWith("//") ? "https:" + u : u);
            }
        }
        String avatar = r.path("member").path("avatar").asText("");
        if (avatar.startsWith("//")) avatar = "https:" + avatar;
        return new Comment(
                r.path("member").path("uname").asText(""),
                content.path("message").asText(""),
                r.path("like").asInt(0),
                r.path("ctime").asLong(0),
                images,
                r.path("rpid").asLong(0),
                avatar);
    }
}
