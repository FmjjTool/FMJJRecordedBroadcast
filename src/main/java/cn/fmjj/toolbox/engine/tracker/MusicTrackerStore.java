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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 音乐账号追踪的配置与历史快照（对应 Python 版 music_tracker 的 xlsx + 长期缓存）。
 *
 * <p>配置：`config/music_tracker_accounts.json`；
 * 历史：`config/music_tracker_history.json`（每个账号一条时间序列，用于涨跌对比）。
 */
public final class MusicTrackerStore {

    /** 音乐账号。 */
    public record Account(String name, String platform, String url, String groupId, String groupName,
                          long fans, String lastFetch) {
        public Account withStats(long fans, String lastFetch) {
            return new Account(name, platform, url, groupId, groupName, fans, lastFetch);
        }
    }

    /** 一次历史快照。 */
    public record Snapshot(String time, long fans) {}

    public static final Map<String, String> PLATFORM_NAMES = new LinkedHashMap<>() {{
        put("netease", "网易云");
        put("qq", "QQ音乐");
        put("kugou", "酷狗");
        put("kuwo", "酷我");
        put("5sing", "5sing");
    }};

    private static final ObjectMapper JSON = new ObjectMapper();

    private MusicTrackerStore() {}

    public static Path accountsFile() {
        return AppPaths.configDir().resolve("music_tracker_accounts.json");
    }

    public static Path historyFile() {
        return AppPaths.configDir().resolve("music_tracker_history.json");
    }

    public static List<Account> loadAccounts() {
        List<Account> out = new ArrayList<>();
        Path f = accountsFile();
        if (!Files.isRegularFile(f)) return out;
        try {
            JsonNode arr = JSON.readTree(Files.readString(f, StandardCharsets.UTF_8));
            if (arr.isArray()) {
                for (JsonNode n : arr) {
                    out.add(new Account(
                            n.path("name").asText(""),
                            n.path("platform").asText(""),
                            n.path("url").asText(""),
                            n.path("groupId").asText(""),
                            n.path("groupName").asText(""),
                            n.path("fans").asLong(0),
                            n.path("lastFetch").asText("")));
                }
            }
        } catch (Exception e) {
            Log.warn("读取音乐追踪账号失败：" + e.getMessage());
        }
        return out;
    }

    public static void saveAccounts(List<Account> accounts) {
        try {
            Files.createDirectories(accountsFile().getParent());
            ArrayNode arr = JSON.createArrayNode();
            for (Account a : accounts) {
                ObjectNode n = arr.addObject();
                n.put("name", a.name());
                n.put("platform", a.platform());
                n.put("url", a.url());
                n.put("groupId", a.groupId());
                n.put("groupName", a.groupName());
                n.put("fans", a.fans());
                n.put("lastFetch", a.lastFetch());
            }
            Files.writeString(accountsFile(), JSON.writerWithDefaultPrettyPrinter().writeValueAsString(arr),
                    StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.warn("保存音乐追踪账号失败：" + e.getMessage());
        }
    }

    /** 读取全部历史：账号名 → 快照列表。 */
    public static Map<String, List<Snapshot>> loadHistory() {
        Map<String, List<Snapshot>> out = new LinkedHashMap<>();
        Path f = historyFile();
        if (!Files.isRegularFile(f)) return out;
        try {
            JsonNode root = JSON.readTree(Files.readString(f, StandardCharsets.UTF_8));
            var it = root.fields();
            while (it.hasNext()) {
                var e = it.next();
                List<Snapshot> list = new ArrayList<>();
                if (e.getValue().isArray()) {
                    for (JsonNode s : e.getValue()) {
                        list.add(new Snapshot(s.path("time").asText(""), s.path("fans").asLong(0)));
                    }
                }
                out.put(e.getKey(), list);
            }
        } catch (Exception e) {
            Log.warn("读取音乐追踪历史失败：" + e.getMessage());
        }
        return out;
    }

    /** 追加一条快照（同名同时间覆盖，最多保留 200 条）。 */
    public static void appendHistory(String accountName, String time, long fans) {
        Map<String, List<Snapshot>> history = loadHistory();
        List<Snapshot> list = history.computeIfAbsent(accountName, k -> new ArrayList<>());
        list.removeIf(s -> s.time().equals(time));
        list.add(new Snapshot(time, fans));
        if (list.size() > 200) list = new ArrayList<>(list.subList(list.size() - 200, list.size()));
        history.put(accountName, list);
        try {
            Files.createDirectories(historyFile().getParent());
            ObjectNode root = JSON.createObjectNode();
            for (var e : history.entrySet()) {
                ArrayNode arr = root.putArray(e.getKey());
                for (Snapshot s : e.getValue()) {
                    ObjectNode n = arr.addObject();
                    n.put("time", s.time());
                    n.put("fans", s.fans());
                }
            }
            Files.writeString(historyFile(), JSON.writerWithDefaultPrettyPrinter().writeValueAsString(root),
                    StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.warn("保存音乐追踪历史失败：" + e.getMessage());
        }
    }

    /** 涨跌：与上一条快照比较，返回差值（无历史返回 0）。 */
    public static long delta(String accountName, long currentFans) {
        List<Snapshot> list = loadHistory().get(accountName);
        if (list == null || list.isEmpty()) return 0;
        long prev = list.get(list.size() - 1).fans();
        return currentFans - prev;
    }

    /** 从主页链接猜平台。 */
    public static String guessPlatform(String url) {
        if (url == null) return "";
        String u = url.toLowerCase();
        if (u.contains("music.163.com")) return "netease";
        if (u.contains("y.qq.com")) return "qq";
        // 5sing 的域名是 5sing.kugou.com，必须先判，否则会被 kugou 抢先命中
        if (u.contains("5sing.kugou.com")) return "5sing";
        if (u.contains("kugou.com")) return "kugou";
        if (u.contains("kuwo.cn")) return "kuwo";
        return "";
    }
}
