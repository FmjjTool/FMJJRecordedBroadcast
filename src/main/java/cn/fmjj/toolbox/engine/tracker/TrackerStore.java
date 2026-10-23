package cn.fmjj.toolbox.engine.tracker;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.Log;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * 追踪账号配置存储（对应 Python 版 tracker 页的账号配置，落 config/tracker_accounts.json）。
 *
 * <p>一条记录：账号名 / 平台 / 链接 / 分组 / 最近采集到的粉丝数与获赞数 / 采集时间。
 */
public final class TrackerStore {

    /** 一个追踪账号。 */
    public record Account(String name, String platform, String url, String groupId, String groupName,
                          long fans, long likes, String lastFetch) {
        public Account withStats(long fans, long likes, String lastFetch) {
            return new Account(name, platform, url, groupId, groupName, fans, likes, lastFetch);
        }
    }

    private static final ObjectMapper JSON = new ObjectMapper();

    private TrackerStore() {}

    public static Path file() {
        return AppPaths.configDir().resolve("tracker_accounts.json");
    }

    public static List<Account> load() {
        List<Account> out = new ArrayList<>();
        Path f = file();
        if (!Files.isRegularFile(f)) return out;
        try {
            JsonNode root = JSON.readTree(Files.readString(f, StandardCharsets.UTF_8));
            JsonNode arr = root.isArray() ? root : root.path("accounts");
            if (arr.isArray()) {
                for (JsonNode n : arr) {
                    out.add(new Account(
                            n.path("name").asText(""),
                            n.path("platform").asText(""),
                            n.path("url").asText(""),
                            n.path("groupId").asText(""),
                            n.path("groupName").asText(""),
                            n.path("fans").asLong(0),
                            n.path("likes").asLong(0),
                            n.path("lastFetch").asText("")));
                }
            }
        } catch (Exception e) {
            Log.warn("读取追踪账号失败：" + e.getMessage());
        }
        return out;
    }

    public static void save(List<Account> accounts) {
        try {
            Files.createDirectories(file().getParent());
            ArrayNode arr = JSON.createArrayNode();
            for (Account a : accounts) {
                ObjectNode n = arr.addObject();
                n.put("name", a.name());
                n.put("platform", a.platform());
                n.put("url", a.url());
                n.put("groupId", a.groupId());
                n.put("groupName", a.groupName());
                n.put("fans", a.fans());
                n.put("likes", a.likes());
                n.put("lastFetch", a.lastFetch());
            }
            Files.writeString(file(), JSON.writerWithDefaultPrettyPrinter().writeValueAsString(arr),
                    StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.warn("保存追踪账号失败：" + e.getMessage());
        }
    }

    /** 平台中文名。 */
    public static String platformName(String key) {
        return switch (key) {
            case "bilibili" -> "B站";
            case "douyin" -> "抖音";
            case "weibo" -> "微博";
            case "kuaishou" -> "快手";
            case "xiaohongshu" -> "小红书";
            default -> key;
        };
    }

    /** 从链接里取 B站 UID。 */
    public static String biliUid(String url) {
        if (url == null) return "";
        var m = java.util.regex.Pattern.compile("space\\.bilibili\\.com/(\\d+)").matcher(url);
        if (m.find()) return m.group(1);
        var m2 = java.util.regex.Pattern.compile("^(\\d+)$").matcher(url.trim());
        return m2.find() ? m2.group(1) : "";
    }

    /** 粉丝数格式化（万/亿）。 */
    public static String formatCount(long n) {
        if (n >= 100_000_000) return String.format("%.2f亿", n / 100_000_000.0);
        if (n >= 10_000) return String.format("%.1f万", n / 10_000.0);
        return String.valueOf(n);
    }
}
