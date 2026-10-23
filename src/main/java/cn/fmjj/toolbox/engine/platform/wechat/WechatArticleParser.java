package cn.fmjj.toolbox.engine.platform.wechat;

import cn.fmjj.toolbox.engine.net.HttpClient;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 公众号文章解析（移植自 Python 版 platforms/wechat_article.py）。
 *
 * <p>纯 HTML 解析：标题 {@code #activity-name}、公众号 {@code #js_name}、
 * 作者 {@code meta[name=author]}、时间 {@code #publish_time} 或 {@code var ct}、
 * 正文 {@code #js_content}；图片取 {@code img[data-src]}（懒加载）优先。
 */
public final class WechatArticleParser {

    private static final Pattern URL_RE = Pattern.compile("https?://mp\\.weixin\\.qq\\.com/\\S+", Pattern.CASE_INSENSITIVE);
    private static final Pattern CT_RE = Pattern.compile("var\\s+ct\\s*=\\s*\"?(\\d{10})\"?");

    /** 文章。 */
    public record Article(String url, String title, String account, String author,
                          String publishTime, String contentHtml, List<String> images) {
        /** 文件名主干：公众号 - 标题（去非法字符）。 */
        public String stem() {
            String base = (account == null || account.isBlank() ? "" : account + " - ")
                    + (title == null || title.isBlank() ? "未命名文章" : title);
            String safe = base.replaceAll("[\\\\/:*?\"<>|\\n\\r\\t]", "_").strip();
            if (safe.length() > 120) safe = safe.substring(0, 120);
            return safe.isEmpty() ? "wechat_article" : safe;
        }
    }

    private final HttpClient http;

    public WechatArticleParser() { this(new HttpClient()); }

    public WechatArticleParser(HttpClient http) { this.http = http; }

    /** 从任意文本提取公众号链接（去重保序）。 */
    public static List<String> extractUrls(String text) {
        Set<String> out = new LinkedHashSet<>();
        if (text == null) return List.of();
        Matcher m = URL_RE.matcher(text);
        while (m.find()) out.add(m.group().strip());
        return new ArrayList<>(out);
    }

    /** 抓取并解析；失败抛异常（消息可直接给用户看）。 */
    public Article fetch(String url) throws Exception {
        String html = http.getText(url, java.util.Map.of(
                "User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                        + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                "Accept-Language", "zh-CN,zh;q=0.9"));
        return parse(html, url);
    }

    /** 纯解析（可离线测试）。 */
    public static Article parse(String html, String url) {
        if (html == null || html.isBlank()) throw new IllegalArgumentException("页面为空");
        if (html.contains("操作频繁") || html.contains("环境异常")) {
            throw new IllegalStateException("微信风控拦截（操作频繁/环境异常），请稍后再试或降低批量速度");
        }
        Document doc = Jsoup.parse(html);

        Element errTitle = doc.selectFirst("div.weui-msg__title");
        if (errTitle != null && doc.selectFirst("#js_content") == null) {
            String msg = errTitle.text().strip();
            throw new IllegalStateException("无法解析：" + (msg.isEmpty() ? "页面异常（可能已删除或需验证）" : msg));
        }

        String title = text(doc.selectFirst("#activity-name"));
        if (title.isEmpty()) {
            Element og = doc.selectFirst("meta[property=og:title]");
            title = og != null ? og.attr("content").strip() : "";
        }
        if (title.isEmpty()) title = "未命名文章";

        String account = text(doc.selectFirst("#js_name"));
        Element authorMeta = doc.selectFirst("meta[name=author]");
        String author = authorMeta != null ? authorMeta.attr("content").strip() : "";

        String publishTime = text(doc.selectFirst("#publish_time"));
        if (publishTime.isEmpty()) {
            Matcher m = CT_RE.matcher(html);
            if (m.find()) {
                publishTime = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
                        .format(java.time.LocalDateTime.ofInstant(
                                java.time.Instant.ofEpochSecond(Long.parseLong(m.group(1))),
                                java.time.ZoneId.of("Asia/Shanghai")));
            }
        }

        Element content = doc.selectFirst("#js_content");
        if (content == null) throw new IllegalStateException("未找到正文（js_content），链接可能不是图文消息");

        List<String> images = new ArrayList<>();
        Elements imgs = content.select("img");
        for (Element img : imgs) {
            String src = img.attr("data-src");
            if (src.isEmpty()) src = img.attr("src");
            src = normalizeImageUrl(src);   // // 开头补 https:
            if (src.startsWith("http") && !images.contains(src)) images.add(src);
        }

        // 正文里把懒加载的 data-src 补成 src，导出的 HTML 才能直接显示本地图
        for (Element img : imgs) {
            String src = img.attr("data-src");
            if (!src.isEmpty()) img.attr("src", normalizeImageUrl(src));
        }

        return new Article(url, title, account, author, publishTime, content.html(), images);
    }

    private static String text(Element e) {
        return e == null ? "" : e.text().strip();
    }

    /** 公众号图片 URL 补协议 + 去 webp 参数（部分图需 wx_fmt 才能取原图）。 */
    public static String normalizeImageUrl(String url) {
        String u = url;
        if (u.startsWith("//")) u = "https:" + u;
        return u;
    }

    /** 建议的文件扩展名（按 URL 猜，取不到给 .jpg）。 */
    public static String imageExt(String url) {
        String u = url.toLowerCase(Locale.ROOT);
        int q = u.indexOf('?');
        if (q >= 0) u = u.substring(0, q);
        if (u.endsWith(".png")) return ".png";
        if (u.endsWith(".gif")) return ".gif";
        if (u.endsWith(".webp")) return ".webp";
        return ".jpg";
    }
}
