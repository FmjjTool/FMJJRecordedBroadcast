package cn.fmjj.toolbox.engine.platform;

import cn.fmjj.toolbox.engine.platform.bili.BiliLink;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinLink;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 多平台链接路由：把任意文本（单条或批量多行）识别成 (平台, ID)。
 * 供「单个解析」与「批量导入」共用；正则与各平台 Python 实现保持一致。
 */
public final class LinkRouter {

    /** 快手。 */
    private static final Pattern KS_VIDEO_URL = Pattern.compile("kuaishou\\.com/f/([a-zA-Z0-9_-]+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern KS_VIDEO_ID = Pattern.compile("kuaishou\\.com/short-video/([a-zA-Z0-9]+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern KS_USER = Pattern.compile("kuaishou\\.com/profile/([a-zA-Z0-9]+)", Pattern.CASE_INSENSITIVE);

    /** 小红书。 */
    private static final Pattern XHS_NOTE = Pattern.compile(
            "xiaohongshu\\.com/(?:discovery/item|explore)/([a-f0-9]+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern XHS_USER = Pattern.compile(
            "xiaohongshu\\.com/user/profile/([a-f0-9]+)", Pattern.CASE_INSENSITIVE);

    /** 一条识别结果。platform 取值与 CookieStore.PLATFORM_NAMES 的键一致。 */
    public record Target(String platform, String id, String kind) {
        /** 当前是否有解析器（抖音/快手/小红书解析待 M5 后续刀）。 */
        public boolean parsable() {
            return "bilibili".equals(platform) || "weibo".equals(platform)
                    || "douyin".equals(platform) || "kuaishou".equals(platform);
        }
    }

    /** 批量识别结果。 */
    public record Outcome(List<Target> targets, List<String> unparsed) {}

    private LinkRouter() {}

    /** 单条文本识别；无法识别返回 null。 */
    public static Target route(String text) {
        String s = text == null ? "" : text.strip();
        if (s.isEmpty()) return null;

        BiliLink.LinkInfo bili = BiliLink.parseText(s);
        if (bili != null) {
            return new Target("bilibili", bili.id(), bili.type().key);
        }
        String weiboId = cn.fmjj.toolbox.engine.platform.weibo.WeiboParser.extractId(s);
        if (!weiboId.isEmpty()) return new Target("weibo", weiboId, "status");

        DouyinLink.LinkInfo dy = DouyinLink.parseText(s);
        if (dy != null) {
            return new Target("douyin", dy.id(), dy.shortLink() ? "short" : dy.type().name().toLowerCase());
        }

        Matcher m = KS_VIDEO_ID.matcher(s);
        if (m.find()) return new Target("kuaishou", m.group(1), "video");
        m = KS_VIDEO_URL.matcher(s);
        if (m.find()) return new Target("kuaishou", m.group(1), "video");
        m = KS_USER.matcher(s);
        if (m.find()) return new Target("kuaishou", m.group(1), "user");

        m = XHS_NOTE.matcher(s);
        if (m.find()) return new Target("xiaohongshu", m.group(1), "note");
        m = XHS_USER.matcher(s);
        if (m.find()) return new Target("xiaohongshu", m.group(1), "user");

        return null;
    }

    /**
     * 批量识别（Python 批量导入页语义）：逐行识别，空行跳过，
     * 识别失败的行原样收进 unparsed 供 UI 提示。
     */
    public static Outcome parseLines(String text) {
        List<Target> targets = new ArrayList<>();
        List<String> unparsed = new ArrayList<>();
        if (text == null) return new Outcome(targets, unparsed);
        for (String raw : text.split("\\R")) {
            String line = raw.strip();
            if (line.isEmpty()) continue;
            Target t = route(line);
            if (t == null) unparsed.add(line);
            else targets.add(t);
        }
        return new Outcome(targets, unparsed);
    }

    /** 平台中文名（用于表格展示）。 */
    public static String platformName(String platform) {
        return switch (platform) {
            case "bilibili" -> "B站";
            case "weibo" -> "微博";
            case "douyin" -> "抖音";
            case "kuaishou" -> "快手";
            case "xiaohongshu" -> "小红书";
            default -> platform;
        };
    }
}
