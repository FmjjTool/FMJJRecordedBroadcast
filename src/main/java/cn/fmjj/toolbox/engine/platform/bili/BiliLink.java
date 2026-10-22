package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.engine.net.HttpClient;

import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * B 站链接识别。移植自 Python 版 src/bili/links.py 的视频/空间/直播/动态子集
 * （装扮/收藏集/漫展等资源类链接属 M6「B站资源」页，暂不在此处理）。
 */
public final class BiliLink {

    private static final Pattern VIDEO = Pattern.compile(
            "https?://(?:www\\.)?bilibili\\.com/(?:video/)?((?:av|AV)\\d+|BV[0-9A-Za-z]+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern SHORT = Pattern.compile("https?://(b23\\.tv|bili2233\\.cn)/[\\w\\-]+", Pattern.CASE_INSENSITIVE);
    private static final Pattern SPACE = Pattern.compile("https?://space\\.bilibili\\.com/(\\d+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern LIVE = Pattern.compile("https?://live\\.bilibili\\.com/(\\d+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern DYNAMIC = Pattern.compile(
            "https?://(?:www\\.)?(?:t\\.bilibili\\.com|bilibili\\.com/opus)/(\\d+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern SHARE = Pattern.compile("^【.+】 ?http.+$", Pattern.DOTALL);

    /** 链接类型（key 与 Python parse_link 返回的元组首项一致）。 */
    public enum Type {
        VIDEO("video"), USER("user"), LIVERoom("liveroom"), DYNAMIC("dynamic");
        public final String key;
        Type(String key) { this.key = key; }
    }

    /** 解析结果：类型 + ID。 */
    public record LinkInfo(Type type, String id) {}

    private BiliLink() {}

    /** 纯文本解析（不做网络请求，便于测试）。 */
    public static LinkInfo parseText(String input) {
        String s = input == null ? "" : input.strip();
        if (s.isEmpty()) return null;
        if (SHARE.matcher(s).matches()) {
            s = s.replaceFirst("^【.+】 ?", "");
        }
        Matcher m = VIDEO.matcher(s);
        if (m.find()) return new LinkInfo(Type.VIDEO, m.group(1));
        m = SPACE.matcher(s);
        if (m.find()) return new LinkInfo(Type.USER, m.group(1));
        m = LIVE.matcher(s);
        if (m.find()) return new LinkInfo(Type.LIVERoom, m.group(1));
        m = DYNAMIC.matcher(s);
        if (m.find()) return new LinkInfo(Type.DYNAMIC, m.group(1));
        return null;
    }

    /** 是否短链（需要先跟随重定向）。 */
    public static boolean isShortLink(String input) {
        return input != null && SHORT.matcher(input.strip()).find();
    }

    /** 完整解析：短链先跟随重定向（Python: resolve_short）。 */
    public static LinkInfo parse(String input, HttpClient http) {
        String s = input == null ? "" : input.strip();
        if (s.isEmpty()) return null;
        LinkInfo direct = parseText(s);
        if (direct != null) return direct;
        if (!isShortLink(s)) return null;
        try {
            var resp = http.get(s, Map.of("User-Agent", HttpClient.UA_FIREFOX));
            String real = resp.uri().toString();
            LinkInfo resolved = parseText(real);
            if (resolved != null) return resolved;
        } catch (Exception ignored) {
            // 短链解析失败按无法识别处理（与 Python resolve_short 返回 "" 后原样匹配一致）
        }
        return null;
    }
}
