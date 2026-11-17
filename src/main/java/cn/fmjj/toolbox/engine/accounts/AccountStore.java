package cn.fmjj.toolbox.engine.accounts;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.JsonMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 账号与分组配置。移植自 Python 版 accounts_manager.py：
 * config/accounts.txt（每行一个账号）+ config/groups.json（组号 → 组名）。
 */
public final class AccountStore {

    private static final ObjectMapper MAPPER = JsonMapper.get();
    private static final Pattern GROUP_ID = Pattern.compile("^(\\d)\\1$");

    public static final class Account {
        public String url = "";
        public String name = "";
        public final List<String> groupIds = new ArrayList<>();
        public String groupName = "";
        public String note = "";
        public String kind = "";

        public String groupId() {
            return groupIds.isEmpty() ? "" : groupIds.get(0);
        }

        public String toLine() {
            String line;
            if (!groupIds.isEmpty()) {
                line = String.join(",", groupIds) + "|" + groupName + "|" + name + "|" + url;
            } else if (!name.isEmpty()) {
                line = name + "|" + url;
            } else {
                line = url;
            }
            if (!note.isEmpty()) line += "|" + note;
            if (kind.equals("video") || kind.equals("dynamic") || kind.equals("all")) line += "|" + kind;
            return line;
        }

        public static Account fromLine(String line) {
            String s = line == null ? "" : line.strip();
            String kind = "";
            String[] parts = s.split("\\|", -1);
            if (parts.length >= 2 && List.of("video", "dynamic", "all").contains(parts[parts.length - 1])) {
                kind = parts[parts.length - 1];
                String[] cut = new String[parts.length - 1];
                System.arraycopy(parts, 0, cut, 0, cut.length);
                parts = cut;
            }
            Account a = new Account();
            a.kind = kind;
            if (parts.length >= 4 && parts[0].matches("[\\d,]+")) {
                for (String g : parts[0].split(",")) if (!g.isEmpty()) a.groupIds.add(g);
                a.groupName = parts[1];
                String name = parts[2];
                String url = parts[3];
                if (url.isEmpty() || (!url.startsWith("http") && name.startsWith("http"))) {
                    String tmp = name;
                    name = url;
                    url = tmp;
                }
                a.name = name;
                a.url = url;
                a.note = parts.length > 4 ? parts[4] : "";
                return a;
            }
            if ((parts.length == 2 || parts.length == 3) && !parts[0].startsWith("http")) {
                a.name = parts[0];
                a.url = parts[1];
                a.note = parts.length > 2 ? parts[2] : "";
                return a;
            }
            a.url = parts.length > 0 ? parts[0] : s;
            return a;
        }
    }

    private AccountStore() {}

    private static Path accountsFile() {
        return AppPaths.configDir().resolve("accounts.txt");
    }

    private static Path groupsFile() {
        return AppPaths.configDir().resolve("groups.json");
    }

    public static synchronized List<Account> loadAccounts() {
        List<Account> out = new ArrayList<>();
        Path f = accountsFile();
        if (!Files.isRegularFile(f)) return out;
        try {
            for (String line : Files.readAllLines(f, StandardCharsets.UTF_8)) {
                String s = line.strip();
                if (!s.isEmpty() && !s.startsWith("#")) out.add(Account.fromLine(s));
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    public static synchronized void saveAccounts(List<Account> accounts) {
        try {
            StringBuilder sb = new StringBuilder();
            for (Account a : accounts) sb.append(a.toLine()).append('\n');
            Files.createDirectories(accountsFile().getParent());
            Files.writeString(accountsFile(), sb.toString(), StandardCharsets.UTF_8);
        } catch (Exception ignored) {
        }
    }

    static List<String> parseGroupIds(String raw) {
        List<String> out = new ArrayList<>();
        if (raw == null || raw.isEmpty()) return out;
        for (String g : raw.replace("，", ",").split(",")) {
            String s = g.strip();
            if (!s.isEmpty() && GROUP_ID.matcher(s).matches()) out.add(s);
        }
        return out;
    }

    public static String inferKindFromUrl(String url) {
        if (url != null && url.contains("space.bilibili.com")) {
            String s = url.strip();
            if (s.endsWith("/dynamic")) return "dynamic";
            if (s.endsWith("/video")) return "video";
        }
        return "";
    }

    /** 添加账号；URL 已存在返回 false。 */
    public static synchronized boolean addAccount(String url, String name, String groupIds,
                                                  String groupName, String note, String kind) {
        String u = url == null ? "" : url.strip();
        String n = name == null ? "" : name.replace("|", " ").strip();
        if (u.isEmpty()) return false;
        if (!u.startsWith("http") && n.startsWith("http")) {
            String tmp = n;
            n = u;
            u = tmp;
        }
        List<Account> accounts = loadAccounts();
        for (Account a : accounts) if (a.url.equals(u)) return false;
        Account a = new Account();
        a.url = u;
        a.name = n;
        a.groupIds.addAll(parseGroupIds(groupIds));
        a.groupName = groupName == null ? "" : groupName.replace("|", " ").strip();
        a.note = note == null ? "" : note.replace("|", " ").strip();
        a.kind = kind == null || kind.isEmpty() ? inferKindFromUrl(u) : kind;
        accounts.add(a);
        saveAccounts(accounts);
        return true;
    }

    public static synchronized boolean updateAccount(String url, String name,
                                                     String groupIds, String groupName,
                                                     String note, String kind, String newUrl) {
        List<Account> accounts = loadAccounts();
        for (Account a : accounts) {
            if (!a.url.equals(url)) continue;
            String nu = newUrl == null ? "" : newUrl.strip();
            if (!nu.isEmpty() && !nu.equals(url)) {
                for (Account other : accounts) if (other.url.equals(nu)) return false;
                a.url = nu;
                if (kind == null || kind.isEmpty()) a.kind = inferKindFromUrl(nu);
            }
            if (name != null) a.name = name.replace("|", " ").strip();
            if (note != null) a.note = note.replace("|", " ").strip();
            if (kind != null) {
                a.kind = kind;
                if (kind.isEmpty() && !nu.isEmpty()) a.kind = inferKindFromUrl(nu);
            }
            a.groupIds.clear();
            a.groupIds.addAll(parseGroupIds(groupIds));
            a.groupName = groupName == null ? "" : groupName.replace("|", " ").strip();
            saveAccounts(accounts);
            return true;
        }
        return false;
    }

    public static synchronized boolean removeAccount(String url) {
        List<Account> accounts = loadAccounts();
        boolean removed = accounts.removeIf(a -> a.url.equals(url));
        if (removed) saveAccounts(accounts);
        return removed;
    }

    public static synchronized boolean moveAccount(String url, int delta) {
        List<Account> accounts = loadAccounts();
        int idx = -1;
        for (int i = 0; i < accounts.size(); i++) if (accounts.get(i).url.equals(url)) idx = i;
        if (idx < 0) return false;
        int j = idx + (delta > 0 ? 1 : -1);
        if (j < 0 || j >= accounts.size()) return false;
        Account tmp = accounts.get(idx);
        accounts.set(idx, accounts.get(j));
        accounts.set(j, tmp);
        saveAccounts(accounts);
        return true;
    }

    // ==================== 分组 ====================

    public static synchronized Map<String, String> getGroups() {
        Map<String, String> groups = new LinkedHashMap<>();
        Path f = groupsFile();
        if (Files.isRegularFile(f)) {
            try {
                JsonNode n = MAPPER.readTree(f.toFile());
                n.fields().forEachRemaining(e -> {
                    if (!e.getKey().isEmpty()) groups.put(e.getKey(), e.getValue().asText(""));
                });
            } catch (Exception ignored) {
            }
        }
        for (Account a : loadAccounts()) {
            for (String g : a.groupIds) {
                if (!g.isEmpty() && !groups.containsKey(g)) groups.put(g, a.groupName);
            }
        }
        return groups;
    }

    public static synchronized boolean saveGroup(String groupId, String groupName) {
        String id = groupId == null ? "" : groupId.strip();
        String name = groupName == null ? "" : groupName.strip();
        if (id.isEmpty() || name.isEmpty()) return false;
        Map<String, String> groups = getGroups();
        groups.put(id, name);
        return writeGroups(groups);
    }

    private static boolean writeGroups(Map<String, String> groups) {
        try {
            ObjectNode n = MAPPER.createObjectNode();
            groups.forEach(n::put);
            Files.createDirectories(groupsFile().getParent());
            Files.writeString(groupsFile(), MAPPER.writerWithDefaultPrettyPrinter()
                    .writeValueAsString(n), StandardCharsets.UTF_8);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    public static synchronized void deleteGroup(String groupId) {
        Map<String, String> groups = getGroups();
        if (groups.remove(groupId) != null) writeGroups(groups);
        List<Account> accounts = loadAccounts();
        boolean changed = false;
        for (Account a : accounts) {
            if (a.groupIds.remove(groupId)) {
                if (a.groupIds.isEmpty()) a.groupName = "";
                changed = true;
            }
        }
        if (changed) saveAccounts(accounts);
    }

    public static synchronized boolean moveGroup(String groupId, int delta) {
        Map<String, String> groups = getGroups();
        List<String> keys = new ArrayList<>(groups.keySet());
        int idx = keys.indexOf(groupId);
        if (idx < 0) return false;
        int j = idx + (delta > 0 ? 1 : -1);
        if (j < 0 || j >= keys.size()) return false;
        String tmp = keys.get(idx);
        keys.set(idx, keys.get(j));
        keys.set(j, tmp);
        Map<String, String> reordered = new LinkedHashMap<>();
        for (String k : keys) reordered.put(k, groups.get(k));
        return writeGroups(reordered);
    }

    public static List<Account> accountsByGroup(String groupId) {
        List<Account> out = new ArrayList<>();
        for (Account a : loadAccounts()) if (a.groupIds.contains(groupId)) out.add(a);
        return out;
    }
}
