package cn.fmjj.toolbox.engine.platform.douyin;

import cn.fmjj.toolbox.engine.net.HttpClient;

import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 抖音链接识别（作品 / 图文 / 主页 / 分享短链）。
 * 移植自 Python 版 platforms/douyin.py 的 VIDEO_ID / NOTE_ID / MODAL_ID / USER 正则。
 *
 * 注意：抖音详情接口受 Argus 风控保护，Python 版也走浏览器上下文（Playwright）；
 * Java 版的解析要等 M5 第三刀接入 playwright-java，本类只负责链接识别与短链展开。
 */
public final class DouyinLink {

    private static final Pattern VIDEO_ID = Pattern.compile("/video/(\\d+)");
    private static final Pattern NOTE_ID = Pattern.compile("/note/(\\d+)");
    private static final Pattern MODAL_ID = Pattern.compile("[?&]modal_id=(\\d+)");
    private static final Pattern USER = Pattern.compile("douyin\\.com/user/([a-zA-Z0-9_-]+)");
    private static final Pattern SHORT = Pattern.compile("https?://v\\.douyin\\.com/[\\w\\-]+", Pattern.CASE_INSENSITIVE);

    public enum Type { VIDEO, NOTE, USER, UNKNOWN }

    public record LinkInfo(Type type, String id, boolean shortLink) {}

    private DouyinLink() {}

    /** 纯文本识别（含分享文案：「复制打开抖音，看看… https://v.douyin.com/xxx/」）。 */
    public static LinkInfo parseText(String input) {
        String s = input == null ? "" : input.strip();
        if (s.isEmpty()) return null;
        if (SHORT.matcher(s).find()) return new LinkInfo(Type.UNKNOWN, "", true);

        Matcher m = VIDEO_ID.matcher(s);
        if (m.find()) return new LinkInfo(Type.VIDEO, m.group(1), false);
        m = NOTE_ID.matcher(s);
        if (m.find()) return new LinkInfo(Type.NOTE, m.group(1), false);
        m = MODAL_ID.matcher(s);
        if (m.find()) return new LinkInfo(Type.VIDEO, m.group(1), false);
        m = USER.matcher(s);
        if (m.find()) return new LinkInfo(Type.USER, m.group(1), false);
        return null;
    }

    /** 短链展开：跟随重定向后重新识别。 */
    public static LinkInfo resolve(String input, HttpClient http) {
        LinkInfo direct = parseText(input);
        if (direct == null || !direct.shortLink()) return direct;
        try {
            var resp = http.get(input.strip(), Map.of("User-Agent", HttpClient.UA_CHROME));
            return parseText(resp.uri().toString());
        } catch (Exception e) {
            return null;
        }
    }

    /** 主页/搜索弹窗链接规范化（Python: _normalize_url）。 */
    public static String normalizeUrl(String url) {
        if (url == null) return "";
        if (url.contains("/user/") || url.contains("/search/")) {
            Matcher m = MODAL_ID.matcher(url);
            if (m.find()) return "https://www.douyin.com/video/" + m.group(1);
        }
        return url;
    }
}
