package cn.fmjj.toolbox.engine.accounts;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.bili.BiliAuth;
import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.browser.CdpClient;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.net.ServerSocket;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 账号时间线采集（B站 / 微博 / 抖音）。移植自 Python 版各平台 fetch_user_posts 主链路，
 * 数据模型对齐 platforms/base.py 的 VideoInfo。
 *
 * <p>快手/小红书时间线暂未移植（页面会明确提示），其余平台复用各自 HTTP API。
 */
public final class AccountFeed {

    private static final ObjectMapper MAPPER = JsonMapper.get();
    private static final DateTimeFormatter MINUTE = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");
    private static final String UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            + " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
    private static final String UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
            + " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

    /** 统一帖子模型（字段对齐 Python VideoInfo）。 */
    public static final class Post {
        public String platform = "";
        public String kind = "video";
        public String videoId = "";
        public String title = "";
        public String description = "";
        public String author = "";
        public String authorId = "";
        public String authorFace = "";
        public long playCount;
        public long likeCount;
        public long commentCount;
        public long shareCount;
        public long favoriteCount;
        public long danmakuCount;
        public String coverUrl = "";
        public String videoUrl = "";
        public String publishTime = "";
        public long publishTs;
        public final List<String> images = new ArrayList<>();

        public ObjectNode toDict() {
            ObjectNode d = MAPPER.createObjectNode();
            d.put("平台", platform);
            d.put("类型", "dynamic".equals(kind) ? "动态" : "image".equals(kind) ? "图文" : "视频");
            d.put("视频ID", videoId);
            d.put("标题", title);
            d.put("正文", description);
            d.put("作者", author);
            d.put("作者ID", authorId);
            d.put("作者头像", authorFace);
            d.put("weibo".equals(platform) ? "浏览量" : "播放量", playCount);
            d.put("点赞数", likeCount);
            d.put("评论数", commentCount);
            d.put("分享数", shareCount);
            d.put("收藏数", favoriteCount);
            d.put("弹幕数", danmakuCount);
            d.put("封面URL", coverUrl);
            d.put("链接", publicUrl());
            d.put("发布时间", publishTime);
            if (!images.isEmpty()) {
                ArrayNode arr = d.putArray("图片");
                images.forEach(arr::add);
            }
            if (publishTs > 0) d.put("发布时间戳", publishTs);
            return d;
        }

        public String publicUrl() {
            if ("douyin".equals(platform) && !videoId.isEmpty()) {
                return "image".equals(kind) ? "https://www.douyin.com/note/" + videoId
                        : "https://www.douyin.com/video/" + videoId;
            }
            return videoUrl;
        }
    }

    private final HttpClient http = new HttpClient();
    private final BiliAuth biliAuth = new BiliAuth(http);

    // ==================== 平台识别 ====================

    public static String detectPlatform(String url) {
        String s = url == null ? "" : url;
        if (s.contains("space.bilibili.com")) return "bilibili";
        if (s.contains("weibo.com") || s.contains("m.weibo.cn")) return "weibo";
        if (s.contains("douyin.com")) return "douyin";
        if (s.contains("kuaishou.com")) return "kuaishou";
        if (s.contains("xiaohongshu.com")) return "xiaohongshu";
        return "";
    }

    /** 采集账号最新内容；不支持时间线的平台抛出明确错误。 */
    public List<Post> fetchUserPosts(String url, int count, String kind, Consumer<String> log)
            throws Exception {
        String platform = detectPlatform(url);
        if (platform.isEmpty()) throw new IOException("无法识别账号链接");
        return switch (platform) {
            case "bilibili" -> fetchBilibili(url, count, kind == null ? "all" : kind, log);
            case "weibo" -> fetchWeibo(url, count, log);
            case "douyin" -> fetchDouyin(url, count, log);
            case "kuaishou" -> fetchKuaishou(url, count, log);
            case "xiaohongshu" -> fetchXiaohongshu(url, count, log);
            default -> throw new IOException("不支持的平台：" + platform);
        };
    }

    public static String platformCn(String platform) {
        return switch (platform) {
            case "bilibili" -> "B站";
            case "weibo" -> "微博";
            case "douyin" -> "抖音";
            case "kuaishou" -> "快手";
            case "xiaohongshu" -> "小红书";
            default -> platform;
        };
    }

    // ==================== B站 ====================

    private List<Post> fetchBilibili(String url, int count, String kind, Consumer<String> log)
            throws Exception {
        String mid = extractMid(url);
        if (mid.isEmpty()) throw new IOException("无法从链接提取 B站用户 ID");
        List<Post> videos = new ArrayList<>();
        if (!"dynamic".equals(kind)) {
            int n = "video".equals(kind) ? count : count / 2 + 1;
            videos = fetchBiliVideos(mid, n, log);
            if (log != null) log.accept("B站投稿视频 " + videos.size() + " 条");
        }
        List<Post> dynamics = new ArrayList<>();
        if (!"video".equals(kind)) {
            int n = "dynamic".equals(kind) ? count : count - videos.size() + 2;
            dynamics = fetchBiliDynamics(mid, Math.max(n, 1), log);
            if ("all".equals(kind)) {
                Set<String> bvids = new LinkedHashSet<>();
                for (Post p : videos) if (!"dynamic".equals(p.kind)) bvids.add(p.videoId);
                List<Post> keep = new ArrayList<>();
                for (Post p : dynamics) {
                    if (!p.videoId.isEmpty() && bvids.contains(p.videoId)) continue;
                    keep.add(p);
                }
                dynamics = keep;
            }
            if (log != null) log.accept("B站动态 " + dynamics.size() + " 条");
        }
        List<Post> all = new ArrayList<>();
        all.addAll(videos);
        all.addAll(dynamics);
        for (Post p : all) {
            if (p.publishTs <= 0) {
                Long ts = parsePublishTs(p.publishTime);
                p.publishTs = ts == null ? 0 : ts;
            }
        }
        all.sort((a, b) -> {
            if (a.publishTs != b.publishTs) return Long.compare(b.publishTs, a.publishTs);
            return b.publishTime.compareTo(a.publishTime);
        });
        Set<String> seen = new LinkedHashSet<>();
        List<Post> unique = new ArrayList<>();
        for (Post p : all) if (seen.add(p.videoId)) unique.add(p);
        return unique.subList(0, Math.min(count, unique.size()));
    }

    private static String extractMid(String url) {
        Matcher m = Pattern.compile("space\\.bilibili\\.com/(\\d+)").matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : "";
    }

    private List<Post> fetchBiliVideos(String mid, int count, Consumer<String> log) {
        String cookie = CookieStore.get("bilibili");
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", UA);
        headers.put("Referer", "https://space.bilibili.com/" + mid + "/video");
        if (!cookie.isEmpty()) headers.put("Cookie", cookie);
        int page = 1;
        int remaining = count;
        List<Post> out = new ArrayList<>();
        boolean useWbi = true;
        while (remaining > 0 && page <= 10) {
            int ps = Math.min(remaining, 30);
            Map<String, String> params = new LinkedHashMap<>();
            params.put("mid", mid);
            params.put("ps", String.valueOf(ps));
            params.put("pn", String.valueOf(page));
            params.put("order", "pubdate");
            params.put("tid", "0");
            params.put("keyword", "");
            JsonNode d = null;
            if (useWbi) {
                try {
                    WbiSigner.WbiKeys keys = biliAuth.wbiKeys();
                    String url = WbiSigner.signUrl("https://api.bilibili.com/x/space/wbi/arc/search",
                            params, keys);
                    d = HttpClient.parseJson(http.getText(url, headers));
                    if (d.path("code").asInt(0) != 0) useWbi = false;
                } catch (Exception e) {
                    useWbi = false;
                }
            }
            if (d == null || d.path("code").asInt(0) != 0) {
                try {
                    String url = "https://api.bilibili.com/x/space/arc/search?" + HttpClient.encodeForm(params);
                    d = HttpClient.parseJson(http.getText(url, headers));
                } catch (Exception e) {
                    if (log != null) log.accept("B站视频接口失败: " + trunc(e.getMessage(), 60));
                    break;
                }
            }
            if (d.path("code").asInt(-1) != 0) {
                if (log != null) log.accept("B站视频接口返回: " + d.path("message").asText(""));
                break;
            }
            JsonNode list = d.path("data").path("list").path("vlist");
            if (!list.isArray() || list.isEmpty()) break;
            int taken = 0;
            for (JsonNode v : list) {
                if (taken >= remaining) break;
                Post p = new Post();
                p.platform = "bilibili";
                p.kind = "video";
                p.videoId = v.path("bvid").asText("");
                p.title = stripHtml(v.path("title").asText(""));
                p.description = stripHtml(v.path("description").asText(""));
                p.author = v.path("author").asText("");
                p.authorId = v.path("mid").asText(mid);
                p.playCount = v.path("play").asLong(0);
                p.commentCount = v.path("comment").asLong(0);
                p.danmakuCount = v.path("video_review").asLong(0);
                p.favoriteCount = v.path("favorites").asLong(0);
                p.coverUrl = fixUrl(v.path("pic").asText(""));
                p.videoUrl = "https://www.bilibili.com/video/" + p.videoId;
                long created = v.path("created").asLong(0);
                if (created > 0) {
                    p.publishTs = created;
                    p.publishTime = LocalDateTime.ofInstant(Instant.ofEpochSecond(created),
                            ZoneId.systemDefault()).format(MINUTE);
                }
                out.add(p);
                taken++;
            }
            remaining -= taken;
            page++;
            if (taken == 0) break;
            sleep(300);
        }
        return out;
    }

    private List<Post> fetchBiliDynamics(String mid, int count, Consumer<String> log) {
        String cookie = CookieStore.get("bilibili");
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", UA);
        headers.put("Referer", "https://space.bilibili.com/" + mid + "/dynamic");
        if (!cookie.isEmpty()) headers.put("Cookie", cookie);
        List<Post> out = new ArrayList<>();
        String offset = "";
        int remaining = count;
        int page = 0;
        while (remaining > 0 && page < 10) {
            String url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid="
                    + mid + "&features=itemOpusStyle" + (offset.isEmpty() ? "" : "&offset=" + offset);
            try {
                JsonNode d = HttpClient.parseJson(http.getText(url, headers));
                if (d.path("code").asInt(-1) != 0) {
                    if (log != null) log.accept("B站动态接口返回: " + d.path("message").asText(""));
                    break;
                }
                JsonNode items = d.path("data").path("items");
                if (!items.isArray() || items.isEmpty()) break;
                int taken = 0;
                for (JsonNode it : items) {
                    if (taken >= remaining) break;
                    Post p = dynamicFromItem(it, mid);
                    if (p == null) continue;
                    out.add(p);
                    taken++;
                }
                remaining -= taken;
                offset = d.path("data").path("offset").asText("");
                if (!d.path("data").path("has_more").asBoolean(false) || offset.isEmpty() || taken == 0) break;
                page++;
                sleep(500);
            } catch (Exception e) {
                if (log != null) log.accept("B站动态接口失败: " + trunc(e.getMessage(), 60));
                break;
            }
        }
        return out;
    }

    private static Post dynamicFromItem(JsonNode it, String mid) {
        String id = it.path("id_str").asText("");
        if (id.isEmpty()) return null;
        JsonNode modules = it.path("modules");
        JsonNode author = modules.path("module_author");
        JsonNode dyn = modules.path("module_dynamic");
        String desc = dyn.path("desc").path("text").asText("");
        JsonNode major = dyn.path("major");
        JsonNode archive = major.path("archive");
        String bvid = archive.path("bvid").asText("");
        Post p = new Post();
        p.platform = "bilibili";
        p.kind = "dynamic";
        p.videoId = bvid.isEmpty() ? id : bvid;
        p.title = trunc(desc.isEmpty() ? archive.path("title").asText("") : desc, 120);
        p.description = desc;
        p.author = author.path("name").asText("");
        p.authorId = author.path("mid").asText(mid);
        p.authorFace = fixUrl(author.path("face").asText(""));
        long ts = author.path("pub_ts").asLong(0);
        if (ts > 0) {
            p.publishTs = ts;
            p.publishTime = LocalDateTime.ofInstant(Instant.ofEpochSecond(ts),
                    ZoneId.systemDefault()).format(MINUTE);
        } else {
            p.publishTime = author.path("pub_time").asText("");
        }
        JsonNode stat = archive.path("stat");
        p.playCount = stat.path("play").asLong(0);
        p.danmakuCount = stat.path("danmaku").asLong(0);
        p.commentCount = stat.path("comment").asLong(0);
        JsonNode mstat = modules.path("module_stat");
        p.likeCount = mstat.path("like").path("count").asLong(0);
        p.commentCount = Math.max(p.commentCount, mstat.path("comment").path("count").asLong(0));
        p.shareCount = mstat.path("forward").path("count").asLong(0);
        p.coverUrl = fixUrl(archive.path("cover").asText(""));
        if (p.coverUrl.isEmpty()) {
            JsonNode drawItems = major.path("draw").path("items");
            if (drawItems.isArray()) {
                for (JsonNode img : drawItems) {
                    String src = fixUrl(img.path("src").asText(""));
                    if (!src.isEmpty()) {
                        p.images.add(src);
                        if (p.coverUrl.isEmpty()) p.coverUrl = src;
                    }
                }
            }
        }
        JsonNode opusImages = major.path("opus").path("pics");
        if (opusImages.isArray()) {
            for (JsonNode img : opusImages) {
                String src = fixUrl(img.path("url").asText(img.path("src").asText("")));
                if (!src.isEmpty()) {
                    p.images.add(src);
                    if (p.coverUrl.isEmpty()) p.coverUrl = src;
                }
            }
        }
        p.videoUrl = bvid.isEmpty() ? "https://t.bilibili.com/" + id
                : "https://www.bilibili.com/video/" + bvid;
        if (p.title.isEmpty() && !archive.path("title").asText("").isEmpty()) {
            p.title = archive.path("title").asText("");
        }
        return p;
    }

    // ==================== 微博 ====================

    private static String extractWeiboUid(String url) {
        Matcher m = Pattern.compile("weibo\\.com/(?:u/)?(\\d{5,15})").matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : "";
    }

    private List<Post> fetchWeibo(String url, int count, Consumer<String> log) throws Exception {
        String uid = extractWeiboUid(url);
        if (uid.isEmpty()) throw new IOException("无法从链接提取微博用户 ID");
        String cookie = CookieStore.get("weibo");
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", UA_MOBILE);
        headers.put("Referer", "https://m.weibo.cn/u/" + uid);
        if (!cookie.isEmpty()) headers.put("Cookie", cookie);
        List<Post> out = new ArrayList<>();
        int page = 1;
        int remaining = count;
        while (remaining > 0 && page <= 50) {
            String api = "https://m.weibo.cn/api/container/getIndex?type=uid&value=" + uid
                    + "&containerid=107603" + uid + "&page=" + page;
            JsonNode d;
            try {
                d = HttpClient.parseJson(http.getText(api, headers));
            } catch (Exception e) {
                if (log != null) log.accept("微博接口失败: " + trunc(e.getMessage(), 60));
                break;
            }
            if (d.path("ok").asInt(0) != 1) {
                if (page == 1) {
                    throw new IOException(cookie.isEmpty()
                            ? "微博时间线需要登录 Cookie（Cookie 管理 → 微博 → 浏览器登录）"
                            : "微博接口返回异常（Cookie 可能已失效）");
                }
                break;
            }
            JsonNode cards = d.path("data").path("cards");
            if (!cards.isArray() || cards.isEmpty()) break;
            int taken = 0;
            for (JsonNode card : cards) {
                if (taken >= remaining) break;
                JsonNode mblog = card.path("mblog");
                if (mblog.isMissingNode() || mblog.isNull()) continue;
                Post p = weiboPost(mblog, uid);
                if (p == null) continue;
                out.add(p);
                taken++;
            }
            remaining -= taken;
            if (taken == 0) break;
            page++;
            sleep(500);
        }
        if (log != null) log.accept("微博获取到 " + out.size() + " 条");
        return out;
    }

    private static Post weiboPost(JsonNode mblog, String uid) {
        String id = mblog.path("id").asText(mblog.path("mid").asText(""));
        if (id.isEmpty()) return null;
        Post p = new Post();
        p.platform = "weibo";
        p.kind = "dynamic";
        p.videoId = id;
        String text = stripHtml(mblog.path("text").asText(""));
        p.description = text;
        p.title = trunc(text, 100);
        JsonNode user = mblog.path("user");
        p.author = user.path("screen_name").asText("");
        p.authorId = user.path("id").asText(uid);
        p.authorFace = fixUrl(user.path("profile_image_url").asText(""));
        p.likeCount = mblog.path("attitudes_count").asLong(0);
        p.commentCount = mblog.path("comments_count").asLong(0);
        p.shareCount = mblog.path("reposts_count").asLong(0);
        JsonNode pageInfo = mblog.path("page_info");
        p.playCount = pageInfo.path("play_count").asLong(0);
        p.coverUrl = fixUrl(pageInfo.path("page_pic").path("url").asText(""));
        for (JsonNode pic : mblog.path("pics")) {
            String u = pic.path("large").path("url").asText(pic.path("url").asText(""));
            if (!u.isEmpty()) p.images.add(fixUrl(u));
        }
        if (p.coverUrl.isEmpty() && !p.images.isEmpty()) p.coverUrl = p.images.get(0);
        String bid = mblog.path("bid").asText("");
        p.videoUrl = bid.isEmpty() ? "https://m.weibo.cn/detail/" + id
                : "https://weibo.com/" + uid + "/" + bid;
        p.publishTime = normalizeDateTime(mblog.path("created_at").asText(""));
        Long ts = parsePublishTs(p.publishTime);
        if (ts != null) p.publishTs = ts;
        return p;
    }

    /** 各种时间格式 → YYYY-MM-DD HH:mm（对应 Python platforms/base.py normalize_datetime）。 */
    static String normalizeDateTime(String raw) {
        if (raw == null) return "";
        String s = raw.strip();
        if (s.isEmpty()) return "";
        if (s.matches("\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}")) return s;
        if (s.matches("\\d{4}-\\d{2}-\\d{2}")) return s + " 00:00";
        Matcher weibo = Pattern.compile(
                "^[A-Za-z]{3} ([A-Za-z]{3}) (\\d{1,2}) (\\d{2}):(\\d{2}):(\\d{2}) [+-]\\d{4} (\\d{4})$")
                .matcher(s);
        if (weibo.find()) {
            Map<String, Integer> months = Map.ofEntries(
                    Map.entry("Jan", 1), Map.entry("Feb", 2), Map.entry("Mar", 3),
                    Map.entry("Apr", 4), Map.entry("May", 5), Map.entry("Jun", 6),
                    Map.entry("Jul", 7), Map.entry("Aug", 8), Map.entry("Sep", 9),
                    Map.entry("Oct", 10), Map.entry("Nov", 11), Map.entry("Dec", 12));
            Integer mo = months.get(weibo.group(1));
            if (mo != null) {
                try {
                    return LocalDateTime.of(Integer.parseInt(weibo.group(6)), mo,
                                    Integer.parseInt(weibo.group(2)),
                                    Integer.parseInt(weibo.group(3)),
                                    Integer.parseInt(weibo.group(4)),
                                    Integer.parseInt(weibo.group(5)))
                            .format(MINUTE);
                } catch (Exception ignored) {
                }
            }
        }
        String iso = s.replace('T', ' ');
        if (iso.matches("\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}.*")) {
            return iso.substring(0, 16);
        }
        Matcher dm = Pattern.compile("(\\d{4})[年/-](\\d{1,2})[月/-](\\d{1,2})").matcher(s);
        Matcher tm = Pattern.compile("(\\d{1,2}):(\\d{2})(?::(\\d{2}))?").matcher(s);
        if (dm.find()) {
            String date = String.format("%s-%02d-%02d", dm.group(1),
                    Integer.parseInt(dm.group(2)), Integer.parseInt(dm.group(3)));
            if (tm.find()) {
                return date + String.format(" %02d:%02d", Integer.parseInt(tm.group(1)),
                        Integer.parseInt(tm.group(2)));
            }
            return date + " 00:00";
        }
        return s;
    }

    // ==================== 抖音 ====================

    private static String extractSecUid(String url) {
        Matcher m = Pattern.compile("douyin\\.com/user/([A-Za-z0-9_-]+)").matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : "";
    }

    private List<Post> fetchDouyin(String url, int count, Consumer<String> log) throws Exception {
        String secUid = extractSecUid(url);
        if (secUid.isEmpty()) throw new IOException("无法从链接提取抖音用户 sec_uid");
        String cookie = CookieStore.get("douyin");
        Map<String, Long> byId = new LinkedHashMap<>();
        List<Post> collected = new ArrayList<>();
        List<String> requestIds = new CopyOnWriteArrayList<>();
        int port;
        try (ServerSocket s = new ServerSocket(0)) {
            port = s.getLocalPort();
        }
        try (CdpClient browser = CdpClient.launch(port, 25)) {
            var ua = MAPPER.createObjectNode();
            ua.put("userAgent", UA);
            ua.put("acceptLanguage", "zh-CN,zh;q=0.9");
            ua.put("platform", "Win32");
            browser.call("Emulation.setUserAgentOverride", ua, 10);
            var metrics = MAPPER.createObjectNode();
            metrics.put("width", 1400);
            metrics.put("height", 900);
            metrics.put("deviceScaleFactor", 1);
            metrics.put("mobile", false);
            browser.call("Emulation.setDeviceMetricsOverride", metrics, 10);
            browser.call("Network.enable", null, 10);
            injectCookies(browser, cookie, "https://www.douyin.com/");
            CdpClient.EventListener listener = (method, params) -> {
                if (!"Network.responseReceived".equals(method)) return;
                String u = params.path("response").path("url").asText("");
                if (!u.contains("aweme/v1/web/aweme/post/")) return;
                String rid = params.path("requestId").asText("");
                if (!rid.isEmpty()) requestIds.add(rid);
            };
            browser.addEventListener(listener);
            var nav = MAPPER.createObjectNode();
            nav.put("url", "https://www.douyin.com/user/" + secUid);
            browser.send("Page.navigate", nav);
            Thread.sleep(8000);
            for (int i = 0; i < 3; i++) {
                try {
                    browser.evalString("window.scrollTo(0, document.body.scrollHeight);"
                            + "String(document.body.scrollHeight)");
                } catch (Exception ignored) {
                }
                Thread.sleep(2500);
            }
            for (String rid : requestIds) {
                try {
                    byte[] body = browser.responseBody(rid);
                    JsonNode d = MAPPER.readTree(new String(body, StandardCharsets.UTF_8));
                    JsonNode list = d.path("aweme_list");
                    if (!list.isArray()) continue;
                    for (JsonNode aw : list) {
                        Post p = douyinPost(aw);
                        if (p != null && !byId.containsKey(p.videoId)) {
                            byId.put(p.videoId, p.publishTs);
                            collected.add(p);
                        }
                    }
                } catch (Exception ignored) {
                }
            }
        }
        List<Post> out = new ArrayList<>(collected);
        out.sort((a, b) -> Long.compare(b.publishTs, a.publishTs));
        if (out.isEmpty()) {
            throw new IOException("抖音时间线未获取到作品（可能需要登录 Cookie 或触发风控）");
        }
        if (log != null) log.accept("抖音获取到 " + Math.min(count, out.size()) + " 条");
        return out.subList(0, Math.min(count, out.size()));
    }

    private static Post douyinPost(JsonNode aw) {
        String id = aw.path("aweme_id").asText("");
        if (id.isEmpty()) return null;
        Post p = new Post();
        p.platform = "douyin";
        JsonNode images = aw.path("images");
        boolean imagePost = images.isArray() && !images.isEmpty();
        p.kind = imagePost ? "image" : "video";
        p.videoId = id;
        p.description = aw.path("desc").asText("");
        p.title = trunc(p.description, 120);
        JsonNode author = aw.path("author");
        p.author = author.path("nickname").asText("");
        p.authorId = author.path("sec_uid").asText("");
        p.authorFace = firstUrl(author.path("avatar_thumb"));
        JsonNode stat = aw.path("statistics");
        p.playCount = stat.path("play_count").asLong(0);
        p.likeCount = stat.path("digg_count").asLong(0);
        p.commentCount = stat.path("comment_count").asLong(0);
        p.shareCount = stat.path("share_count").asLong(0);
        p.favoriteCount = stat.path("collect_count").asLong(0);
        p.coverUrl = firstUrl(aw.path("video").path("cover"));
        if (imagePost) {
            for (JsonNode img : images) {
                String u = firstUrl(img);
                if (!u.isEmpty()) p.images.add(u);
            }
            if (p.coverUrl.isEmpty() && !p.images.isEmpty()) p.coverUrl = p.images.get(0);
        }
        long ts = aw.path("create_time").asLong(0);
        if (ts > 0) {
            p.publishTs = ts;
            p.publishTime = LocalDateTime.ofInstant(Instant.ofEpochSecond(ts),
                    ZoneId.systemDefault()).format(MINUTE);
        }
        p.videoUrl = imagePost ? "https://www.douyin.com/note/" + id
                : "https://www.douyin.com/video/" + id;
        return p;
    }

    private static String firstUrl(JsonNode node) {
        JsonNode list = node.path("url_list");
        if (list.isArray() && !list.isEmpty()) return fixUrl(list.get(0).asText(""));
        return fixUrl(node.path("url").asText(""));
    }

    private static void injectCookies(CdpClient browser, String cookie, String url) {
        if (cookie == null || cookie.isBlank()) return;
        for (String pair : cookie.split(";")) {
            int eq = pair.indexOf('=');
            if (eq <= 0) continue;
            var p = MAPPER.createObjectNode();
            p.put("name", pair.substring(0, eq).strip());
            p.put("value", pair.substring(eq + 1).strip());
            p.put("url", url);
            p.put("path", "/");
            try {
                browser.call("Network.setCookie", p, 10);
            } catch (Exception ignored) {
            }
        }
    }

    // ==================== 快手 ====================

    private static String extractKuaishouId(String url) {
        Matcher m = Pattern.compile("kuaishou\\.com/profile/([A-Za-z0-9_-]+)")
                .matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : "";
    }

    private List<Post> fetchKuaishou(String url, int count, Consumer<String> log) throws Exception {
        if (extractKuaishouId(url).isEmpty()) throw new IOException("无法从链接提取快手用户 ID");
        String cookie = CookieStore.get("kuaishou");
        Map<String, Post> byId = new LinkedHashMap<>();
        List<String> requestIds = new CopyOnWriteArrayList<>();
        boolean[] loginRequired = {false};
        boolean[] noMore = {false};
        int port;
        try (ServerSocket s = new ServerSocket(0)) {
            port = s.getLocalPort();
        }
        try (CdpClient browser = CdpClient.launch(port, 25)) {
            var ua = MAPPER.createObjectNode();
            ua.put("userAgent", UA);
            ua.put("acceptLanguage", "zh-CN,zh;q=0.9");
            ua.put("platform", "Win32");
            browser.call("Emulation.setUserAgentOverride", ua, 10);
            var metrics = MAPPER.createObjectNode();
            metrics.put("width", 1280);
            metrics.put("height", 900);
            metrics.put("deviceScaleFactor", 1);
            metrics.put("mobile", false);
            browser.call("Emulation.setDeviceMetricsOverride", metrics, 10);
            browser.call("Network.enable", null, 10);
            injectCookies(browser, cookie, "https://www.kuaishou.com/");
            CdpClient.EventListener listener = (method, params) -> {
                if (!"Network.responseReceived".equals(method)) return;
                String u = params.path("response").path("url").asText("");
                if (!u.contains("rest/v/profile/feed")) return;
                String rid = params.path("requestId").asText("");
                if (!rid.isEmpty()) requestIds.add(rid);
            };
            browser.addEventListener(listener);
            var nav = MAPPER.createObjectNode();
            nav.put("url", "https://www.kuaishou.com/profile/" + extractKuaishouId(url));
            browser.send("Page.navigate", nav);
            int processed = 0;
            int stalls = 0;
            int prev = -1;
            int maxRounds = count <= 60 ? 14 : 40;
            for (int round = 0; round < maxRounds; round++) {
                Thread.sleep(1500);
                for (; processed < requestIds.size(); processed++) {
                    try {
                        byte[] body = browser.responseBody(requestIds.get(processed));
                        JsonNode d = MAPPER.readTree(new String(body, StandardCharsets.UTF_8));
                        int resultCode = d.path("result").asInt(0);
                        if (resultCode == 109) {
                            loginRequired[0] = true;
                            continue;
                        }
                        if (resultCode != 1) continue;
                        for (JsonNode item : d.path("feeds")) {
                            Post p = kuaishouPost(item);
                            if (p != null && !byId.containsKey(p.videoId)) byId.put(p.videoId, p);
                        }
                        if ("no_more".equals(d.path("pcursor").asText(""))) noMore[0] = true;
                    } catch (Exception ignored) {
                    }
                }
                if (loginRequired[0]) {
                    throw new IOException("快手主页需要登录：请在「Cookie 管理」中登录快手后重试");
                }
                if (byId.size() >= count || noMore[0]) break;
                if (byId.size() == prev) {
                    if (++stalls >= 3) break;
                } else {
                    stalls = 0;
                }
                prev = byId.size();
                try {
                    browser.evalString("window.scrollBy(0,2500); String(window.scrollY)");
                } catch (Exception ignored) {
                }
            }
        }
        if (loginRequired[0]) {
            throw new IOException("快手主页需要登录：请在「Cookie 管理」中登录快手后重试");
        }
        List<Post> out = new ArrayList<>(byId.values());
        if (out.isEmpty()) {
            throw new IOException("快手主页未捕获到作品数据（Cookie 可能失效，请重新登录快手）");
        }
        out.sort((a, b) -> Long.compare(b.publishTs, a.publishTs));
        if (log != null) log.accept("快手获取到 " + Math.min(count, out.size()) + " 条");
        return out.subList(0, Math.min(count, out.size()));
    }

    private static Post kuaishouPost(JsonNode item) {
        JsonNode photo = item.path("photo");
        String id = photo.path("id").asText("");
        if (id.isEmpty()) return null;
        Post p = new Post();
        p.platform = "kuaishou";
        p.kind = "video";
        p.videoId = id;
        String caption = photo.path("caption").asText("").strip();
        p.description = caption;
        p.title = trunc(caption.split("\\R", 2)[0], 60);
        JsonNode author = item.path("author");
        p.author = author.path("name").asText("");
        p.authorId = author.path("id").asText("");
        p.authorFace = fixUrl(author.path("headerUrl").asText(""));
        p.playCount = toLong(photo.path("viewCount"));
        p.likeCount = toLong(photo.has("realLikeCount")
                ? photo.get("realLikeCount") : photo.get("likeCount"));
        p.coverUrl = fixUrl(photo.path("coverUrl").asText(
                photo.path("animatedCoverUrl").asText("")));
        long tsMs = photo.path("timestamp").asLong(0);
        if (tsMs > 0) {
            p.publishTs = tsMs / 1000;
            p.publishTime = LocalDateTime.ofInstant(Instant.ofEpochMilli(tsMs),
                    ZoneId.systemDefault()).format(MINUTE);
        }
        p.videoUrl = "https://www.kuaishou.com/short-video/" + id;
        return p;
    }

    // ==================== 小红书 ====================

    private static String extractXhsUserId(String url) {
        Matcher m = Pattern.compile("xiaohongshu\\.com/user/profile/([a-zA-Z0-9]+)")
                .matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : "";
    }

    private List<Post> fetchXiaohongshu(String url, int count, Consumer<String> log)
            throws Exception {
        if (extractXhsUserId(url).isEmpty()) throw new IOException("无法从链接提取小红书用户 ID");
        String cookie = CookieStore.get("xiaohongshu");
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", UA);
        headers.put("Referer", "https://www.xiaohongshu.com/");
        if (!cookie.isEmpty()) headers.put("Cookie", cookie);
        List<Post> posts = new ArrayList<>();
        try {
            String html = http.getText(url, headers);
            posts = xhsPostsFromHtml(html, count);
        } catch (Exception e) {
            if (log != null) log.accept("小红书主页请求失败: " + trunc(e.getMessage(), 60));
        }
        if (posts.isEmpty() && cookie.isEmpty()) {
            throw new IOException("小红书主页作品需要登录：请在「Cookie 管理」中登录小红书后重试");
        }
        if (posts.isEmpty()) {
            throw new IOException("小红书主页未解析到笔记（Cookie 可能失效，或页面结构变化）");
        }
        if (log != null) log.accept("小红书获取到 " + posts.size() + " 条");
        return posts;
    }

    static List<Post> xhsPostsFromHtml(String html, int count) {
        List<Post> out = new ArrayList<>();
        JsonNode data = balancedJson(html, "window\\.__INITIAL_STATE__");
        if (data == null) return out;
        JsonNode notes = data.path("user").path("notes");
        if (!notes.isArray()) return out;
        List<JsonNode> flat = new ArrayList<>();
        for (JsonNode wrapper : notes) {
            if (wrapper.isArray()) {
                for (JsonNode w : wrapper) if (w.isObject()) flat.add(w);
            } else if (wrapper.isObject()) {
                flat.add(wrapper);
            }
        }
        for (JsonNode wrapper : flat) {
            if (out.size() >= Math.max(1, count)) break;
            JsonNode card = wrapper.path("noteCard");
            if (!card.isObject() || card.isEmpty()) continue;
            String id = card.path("noteId").asText(wrapper.path("id").asText(""));
            if (id.isEmpty()) continue;
            JsonNode interact = card.path("interactInfo");
            JsonNode cover = card.path("cover");
            String coverUrl = fixUrl(cover.path("urlDefault").asText(
                    cover.path("urlPre").asText("")));
            String xsec = card.path("xsecToken").asText(wrapper.path("xsecToken").asText(""));
            String noteUrl = "https://www.xiaohongshu.com/discovery/item/" + id;
            if (!xsec.isEmpty()) noteUrl += "?xsec_token=" + xsec + "&xsec_source=pc_search";
            Post p = new Post();
            p.platform = "xiaohongshu";
            p.kind = "video";
            p.videoId = id;
            p.title = trunc(card.path("displayTitle").asText(""), 60);
            JsonNode user = card.path("user");
            p.author = user.path("nickname").asText("");
            p.authorId = user.path("userId").asText("");
            p.likeCount = parseCnCount(interact.path("likedCount").asText(""));
            p.coverUrl = coverUrl;
            p.videoUrl = noteUrl;
            long ms = card.path("time").asLong(0);
            if (ms > 0) {
                p.publishTs = ms / 1000;
                p.publishTime = LocalDateTime.ofInstant(Instant.ofEpochMilli(ms),
                        ZoneId.systemDefault()).format(MINUTE);
            }
            out.add(p);
        }
        return out;
    }

    /** 从 HTML 提取 marker = {...} 的完整 JSON（花括号配平，容忍 undefined/尾逗号/new Set）。 */
    static JsonNode balancedJson(String html, String markerRegex) {
        if (html == null) return null;
        Matcher m = Pattern.compile(markerRegex + "\\s*=\\s*").matcher(html);
        if (!m.find()) return null;
        int start = m.end();
        int depth = 0;
        boolean inStr = false;
        boolean esc = false;
        char quote = 0;
        int j = start;
        for (; j < html.length(); j++) {
            char c = html.charAt(j);
            if (esc) {
                esc = false;
            } else if (c == '\\') {
                esc = true;
            } else if (inStr) {
                if (c == quote) inStr = false;
            } else if (c == '"' || c == '\'') {
                inStr = true;
                quote = c;
            } else if (c == '{') {
                depth++;
            } else if (c == '}') {
                depth--;
                if (depth == 0) break;
            }
        }
        if (j >= html.length()) return null;
        String raw = html.substring(start, j + 1);
        String base = raw.replaceAll(",\\s*([}\\]])", "$1").replace("undefined", "null");
        Matcher setMap = Pattern.compile("new\\s+(?:Set|Map)\\(\\s*(\\[[^\\]]*])?\\s*\\)")
                .matcher(base);
        StringBuilder sb = new StringBuilder();
        while (setMap.find()) {
            setMap.appendReplacement(sb, setMap.group(1) == null ? "[]" : setMap.group(1));
        }
        setMap.appendTail(sb);
        for (String cand : List.of(raw, base, sb.toString())) {
            try {
                JsonNode n = MAPPER.readTree(cand);
                if (n != null && n.isObject()) return n;
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    static long parseCnCount(String s) {
        if (s == null || s.isBlank()) return 0;
        String t = s.strip();
        double mult = 1;
        if (t.contains("亿")) mult = 100_000_000;
        else if (t.contains("万")) mult = 10_000;
        Matcher m = Pattern.compile("(\\d+(?:\\.\\d+)?)").matcher(t);
        if (!m.find()) return 0;
        try {
            return (long) (Double.parseDouble(m.group(1)) * mult);
        } catch (Exception e) {
            return 0;
        }
    }

    private static long toLong(JsonNode n) {
        if (n == null || n.isNull()) return 0;
        try {
            return n.isNumber() ? n.asLong() : Long.parseLong(n.asText("0"));
        } catch (Exception e) {
            return 0;
        }
    }

    // ==================== 日期过滤 ====================

    /** 发布时间字符串 → epoch 秒；解析失败返回 null（对应条目保留不过滤）。 */
    public static Long parsePublishTs(String text) {
        String s = text == null ? "" : text.strip();
        if (s.isEmpty()) return null;
        Matcher m = Pattern.compile("(\\d{4})[-/.年](\\d{1,2})[-/.月](\\d{1,2})").matcher(s);
        if (m.find()) {
            try {
                int y = Integer.parseInt(m.group(1));
                int mo = Integer.parseInt(m.group(2));
                int d = Integer.parseInt(m.group(3));
                Matcher hm = Pattern.compile("(\\d{1,2}):(\\d{2})(?::(\\d{2}))?")
                        .matcher(s.substring(m.end()));
                LocalDateTime dt;
                if (hm.find()) {
                    dt = LocalDateTime.of(y, mo, d, Integer.parseInt(hm.group(1)),
                            Integer.parseInt(hm.group(2)),
                            hm.group(3) == null ? 0 : Integer.parseInt(hm.group(3)));
                } else {
                    dt = LocalDate.of(y, mo, d).atStartOfDay();
                }
                return dt.atZone(ZoneId.systemDefault()).toEpochSecond();
            } catch (Exception ignored) {
            }
        }
        LocalDateTime now = LocalDateTime.now();
        if (s.startsWith("刚刚") || s.startsWith("刚才") || s.startsWith("今天")) {
            return now.atZone(ZoneId.systemDefault()).toEpochSecond();
        }
        Matcher rm = Pattern.compile("(\\d+)\\s*(秒|分钟|分|小时|时|天|日|周|月)前").matcher(s);
        if (rm.find()) {
            long n = Long.parseLong(rm.group(1));
            long seconds = switch (rm.group(2)) {
                case "秒" -> n;
                case "分钟", "分" -> n * 60;
                case "小时", "时" -> n * 3600;
                case "天", "日" -> n * 86400;
                case "周" -> n * 7 * 86400;
                default -> n * 30 * 86400;
            };
            return now.atZone(ZoneId.systemDefault()).toEpochSecond() - seconds;
        }
        if (s.startsWith("昨天")) {
            return now.minusDays(1).atZone(ZoneId.systemDefault()).toEpochSecond();
        }
        if (s.startsWith("前天")) {
            return now.minusDays(2).atZone(ZoneId.systemDefault()).toEpochSecond();
        }
        return null;
    }

    /** 保留发布时间落在 [start,end] 的条目；时间解析失败的保留（宁多勿漏）。 */
    public static List<Post> filterByDate(List<Post> posts, String start, String end) {
        Long lo = dateBound(start, true);
        Long hi = dateBound(end, false);
        if (lo == null && hi == null) return posts;
        List<Post> keep = new ArrayList<>();
        for (Post p : posts) {
            Long ts = p.publishTs > 0 ? Long.valueOf(p.publishTs) : parsePublishTs(p.publishTime);
            if (ts == null) {
                keep.add(p);
                continue;
            }
            if (lo != null && ts < lo) continue;
            if (hi != null && ts > hi) continue;
            keep.add(p);
        }
        return keep;
    }

    private static Long dateBound(String text, boolean start) {
        if (text == null || text.isBlank()) return null;
        try {
            LocalDate d = LocalDate.parse(text.strip());
            LocalDateTime t = start ? d.atStartOfDay() : d.atTime(23, 59, 59);
            return t.atZone(ZoneId.systemDefault()).toEpochSecond();
        } catch (Exception e) {
            return null;
        }
    }

    // ==================== 工具 ====================

    private static String stripHtml(String html) {
        if (html == null || html.isEmpty()) return "";
        String withBreaks = html.replaceAll("(?i)<br\\s*/?>", "\n").replaceAll("<[^>]+>", "");
        String text = org.jsoup.parser.Parser.unescapeEntities(withBreaks, false);
        return text.replaceAll("[ \\t]+", " ").strip();
    }

    private static String fixUrl(String url) {
        if (url == null || url.isEmpty()) return "";
        return url.startsWith("//") ? "https:" + url : url.replace("&amp;", "&");
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
