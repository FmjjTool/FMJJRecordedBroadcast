package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliEmote;
import cn.fmjj.toolbox.engine.platform.bili.BiliGarb;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.Tab;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.control.Tooltip;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * B站资源聚合页的「装扮表情包」「直播表情」两个 tab。
 * 移植自 Python 版 gui_qt/pages/bili_emoji.py 的「按 ID」与「直播间」。
 */
final class BiliResourceEmoteTabs {

    private record EmoteFile(String name, String dir, String url) {}

    private final ObjectMapper mapper = JsonMapper.get();
    private final BiliGarb garbApi = new BiliGarb();
    private final BiliEmote emoteApi = new BiliEmote();
    private final FileDownloader downloader = new FileDownloader();
    private final Consumer<String> toast;
    private final Consumer<String> showGarb;

    private final TextField emojiIdField = new TextField();
    private final CheckBox emojiSuitForce = new CheckBox("装扮表情包");
    private final ComboBox<String> emojiFmt = new ComboBox<>();
    private final Label emojiIdInfo = new Label(
            "自动识别：普通表情包 → 装扮表情 → 收藏集（命中收藏集会自动跳「收藏集」tab）");
    private final TableView<EmoteFile> emojiIdTable = new TableView<>();
    private final Label emojiIdProgress = new Label("");
    private final List<EmoteFile> emojiIdFiles = new ArrayList<>();

    private final TextField liveRoomField = new TextField();
    private final Label liveRoomInfo = new Label(
            "加载直播间专属表情 / UP主大表情 / 通用表情 + 主播头像/封面/网页+App背景图/关键帧（表情需登录）");
    private final TableView<EmoteFile> liveRoomTable = new TableView<>();
    private final Label liveRoomProgress = new Label("");
    private final List<EmoteFile> liveRoomFiles = new ArrayList<>();

    BiliResourceEmoteTabs(Consumer<String> toast, Consumer<String> showGarb) {
        this.toast = toast;
        this.showGarb = showGarb;
        buildIdTab();
        buildRoomTab();
    }

    // ==================== 🆔 装扮表情包 ====================

    Tab buildIdTab() {
        emojiIdField.setPromptText("表情包 ID / 装扮 item_id / 链接（收藏集链接自动跳转），多个 ID 用逗号分隔");
        emojiIdField.setPrefWidth(430);
        emojiIdField.setOnAction(e -> viewEmojiById());
        emojiSuitForce.setTooltip(new Tooltip("勾选后强制按装扮接口解析；不勾选时按 ID 长度自动判断"));
        emojiFmt.getItems().addAll("GIF（动图）", "WEBP", "PNG（静图）", "全部格式");
        emojiFmt.getSelectionModel().selectFirst();
        emojiFmt.setPrefWidth(120);
        Button view = new Button("查看");
        view.setStyle("-fx-background-color:#1976d2; -fx-text-fill:white;");
        view.setOnAction(e -> viewEmojiById());
        HBox bar = new HBox(6, new Label("表情包/装扮ID："), emojiIdField,
                emojiSuitForce, new Label("格式"), emojiFmt, view);
        bar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(emojiIdField, Priority.ALWAYS);

        emojiIdInfo.setStyle("-fx-text-fill:#888;");
        emojiIdInfo.setWrapText(true);
        emojiIdTable.setPlaceholder(new Label("输入 ID 后点「查看」"));
        TableColumn<EmoteFile, String> cName = new TableColumn<>("表情");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().name()));
        cName.setPrefWidth(300);
        TableColumn<EmoteFile, String> cInfo = new TableColumn<>("格式/目录");
        cInfo.setCellValueFactory(d -> new SimpleStringProperty(
                fileExt(d.getValue().url()).replace(".", "").toUpperCase(Locale.ROOT)
                        + " · " + d.getValue().dir()));
        cInfo.setPrefWidth(420);
        emojiIdTable.getColumns().addAll(List.of(cName, cInfo));

        Button dl = new Button("⬇ 全部下载");
        dl.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white;");
        dl.setOnAction(e -> downloadEmoteFiles(emojiIdFiles, "表情包", emojiIdProgress, dl));
        emojiIdProgress.setStyle("-fx-text-fill:#888;");
        HBox dlBar = new HBox(8, dl, emojiIdProgress);
        dlBar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(emojiIdProgress, Priority.ALWAYS);

        VBox box = new VBox(8, bar, emojiIdInfo, emojiIdTable, dlBar);
        box.setPadding(new Insets(10));
        VBox.setVgrow(emojiIdTable, Priority.ALWAYS);
        return new Tab("🆔 装扮表情包", box);
    }

    private void viewEmojiById() {
        String raw = emojiIdField.getText() == null ? "" : emojiIdField.getText().strip();
        if (raw.isEmpty()) return;
        Matcher act = Pattern.compile("(?:act_id|dlc_act_id)=(\\d+)").matcher(raw);
        if (act.find()) {
            emojiIdInfo.setText("识别到收藏集链接，正在跳「收藏集」tab……");
            showGarb.accept(act.group(1));
            return;
        }
        Matcher item = Pattern.compile("(?:item_id|suit_id)=(\\d+)").matcher(raw);
        if (item.find()) {
            emojiSuitForce.setSelected(true);
            raw = item.group(1);
        }
        if (raw.contains("http")) {
            emojiIdInfo.setText("未识别的链接（本 tab 支持表情包/装扮/收藏集链接）");
            return;
        }
        List<String> ids = new ArrayList<>();
        for (String s : raw.replace("，", ",").split(",")) {
            if (!s.strip().isEmpty()) ids.add(s.strip());
        }
        if (ids.isEmpty()) return;
        boolean force = emojiSuitForce.isSelected();
        String fmt = fmtKey(emojiFmt.getSelectionModel().getSelectedItem());
        emojiIdInfo.setText("加载中……");
        new Thread(() -> {
            String cookie = CookieStore.get("bilibili");
            List<EmoteFile> files = new ArrayList<>();
            List<String> hints = new ArrayList<>();
            String title = ids.get(0);
            String detectedAct = null;
            Set<String> seen = new LinkedHashSet<>();
            for (String id : ids) {
                boolean suitMode = force || id.length() > 8;
                if (suitMode) {
                    try {
                        JsonNode d = garbApi.suitDetail(id, cookie);
                        if (d == null || d.isMissingNode() || d.isEmpty()) {
                            hints.add(id + "：装扮详情为空（内容可能已下架）");
                            continue;
                        }
                        title = d.path("name").asText(id);
                        files.addAll(suitEmoteFiles(d, seen));
                    } catch (Exception e) {
                        hints.add(id + "：" + trunc(e.getMessage(), 80));
                    }
                } else {
                    try {
                        List<JsonNode> pkgs = emoteApi.emotePackages(List.of(id), cookie);
                        if (pkgs.isEmpty()) {
                            try {
                                garbApi.fetchAct(id, cookie);
                                detectedAct = id;
                                break;
                            } catch (Exception ignore) {
                            }
                            hints.add(id + "：普通表情包接口无数据（可尝试勾选「装扮表情包」）");
                            continue;
                        }
                        for (JsonNode p : pkgs) {
                            String pkgName = p.path("text").asText(p.path("pkg_name").asText(id));
                            title = pkgName;
                            String dir = sanitize(pkgName);
                            JsonNode arr = p.path("emote");
                            if (!arr.isArray()) continue;
                            for (JsonNode e : arr) {
                                String name = sanitize(e.path("text").asText("表情"));
                                for (String u : pickFormatUrls(e, fmt)) {
                                    addEmoteFile(files, seen, dir, name, u);
                                }
                            }
                        }
                    } catch (Exception e) {
                        hints.add(id + "：" + trunc(e.getMessage(), 80));
                    }
                }
            }
            final String fTitle = title;
            final List<String> fHints = hints;
            final String fAct = detectedAct;
            javafx.application.Platform.runLater(() -> {
                if (fAct != null) {
                    emojiIdInfo.setText("识别到收藏集活动（act_id=" + fAct + "），正在跳「收藏集」tab……");
                    showGarb.accept(fAct);
                    return;
                }
                emojiIdFiles.clear();
                emojiIdFiles.addAll(files);
                emojiIdTable.setItems(FXCollections.observableArrayList(files));
                emojiIdInfo.setText("「" + fTitle + "」共 " + files.size() + " 个表情文件"
                        + (fHints.isEmpty() ? "" : "  |  " + String.join("；", fHints)));
            });
        }, "bili-emoji-id").start();
    }

    /** 装扮详情 → 表情文件（item_emoji_list 优先，suit_items.emoji 兜底）。 */
    private List<EmoteFile> suitEmoteFiles(JsonNode d, Set<String> seen) {
        List<EmoteFile> files = new ArrayList<>();
        String title = d.path("name").asText("表情包");
        JsonNode items = d.path("suit_items");
        List<JsonNode> pkgs = new ArrayList<>();
        JsonNode ep = items.path("emoji_package");
        if (ep.isArray()) ep.forEach(pkgs::add);
        if (pkgs.isEmpty()) {
            var pseudo = mapper.createObjectNode();
            pseudo.put("name", title);
            pseudo.set("properties", d.path("properties"));
            pkgs.add(pseudo);
        }
        for (JsonNode pkg : pkgs) {
            String pkgName = pkg.path("name").asText(title);
            if (pkgName.isEmpty()) pkgName = title;
            String dir = sanitize(pkgName);
            List<JsonNode> emojis = new ArrayList<>();
            String raw = pkg.path("properties").path("item_emoji_list").asText("");
            if (!raw.isEmpty()) {
                try {
                    JsonNode arr = mapper.readTree(raw);
                    if (arr.isArray()) arr.forEach(emojis::add);
                } catch (Exception ignored) {
                }
            }
            if (emojis.isEmpty()) {
                JsonNode fallback = items.path("emoji");
                if (fallback.isArray()) fallback.forEach(emojis::add);
            }
            for (JsonNode e : emojis) {
                String name = sanitize(emojiKey(e.path("name").asText("表情")));
                JsonNode props = e.path("properties");
                for (String key : List.of("image_gif", "image_webp", "image")) {
                    String u = norm(props.path(key).asText(""));
                    if (!u.isEmpty()) addEmoteFile(files, seen, dir, name, u);
                }
            }
        }
        return files;
    }

    // ==================== 📺 直播表情 ====================

    Tab buildRoomTab() {
        liveRoomField.setPromptText("如 544820 或 https://live.bilibili.com/544820");
        liveRoomField.setPrefWidth(420);
        liveRoomField.setOnAction(e -> loadLiveRoom());
        Button load = new Button("加载");
        load.setStyle("-fx-background-color:#1976d2; -fx-text-fill:white;");
        load.setOnAction(e -> loadLiveRoom());
        HBox bar = new HBox(6, new Label("房间号："), liveRoomField, load);
        bar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(liveRoomField, Priority.ALWAYS);

        liveRoomInfo.setStyle("-fx-text-fill:#888;");
        liveRoomInfo.setWrapText(true);
        liveRoomTable.setPlaceholder(new Label("输入房间号后点「加载」"));
        TableColumn<EmoteFile, String> cName = new TableColumn<>("表情/图片");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().name()));
        cName.setPrefWidth(300);
        TableColumn<EmoteFile, String> cInfo = new TableColumn<>("格式/目录");
        cInfo.setCellValueFactory(d -> new SimpleStringProperty(
                fileExt(d.getValue().url()).replace(".", "").toUpperCase(Locale.ROOT)
                        + " · " + d.getValue().dir()));
        cInfo.setPrefWidth(420);
        liveRoomTable.getColumns().addAll(List.of(cName, cInfo));

        Button dl = new Button("⬇ 全部下载");
        dl.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white;");
        dl.setOnAction(e -> downloadEmoteFiles(liveRoomFiles, "表情包", liveRoomProgress, dl));
        liveRoomProgress.setStyle("-fx-text-fill:#888;");
        HBox dlBar = new HBox(8, dl, liveRoomProgress);
        dlBar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(liveRoomProgress, Priority.ALWAYS);

        VBox box = new VBox(8, bar, liveRoomInfo, liveRoomTable, dlBar);
        box.setPadding(new Insets(10));
        VBox.setVgrow(liveRoomTable, Priority.ALWAYS);
        return new Tab("📺 直播表情", box);
    }

    private void loadLiveRoom() {
        String raw = liveRoomField.getText() == null ? "" : liveRoomField.getText().strip();
        Matcher m = Pattern.compile("live\\.bilibili\\.com/(\\d+)").matcher(raw);
        String rid = m.find() ? m.group(1) : raw;
        if (!rid.matches("\\d+")) {
            liveRoomInfo.setText("请输入房间号或直播间链接");
            return;
        }
        liveRoomInfo.setText("加载中……");
        new Thread(() -> {
            try {
                BiliEmote.LiveAssets a = emoteApi.liveAssets(rid, CookieStore.get("bilibili"));
                List<EmoteFile> files = new ArrayList<>();
                Set<String> seen = new LinkedHashSet<>();
                String base = sanitize(a.anchor.isEmpty() ? "直播间" + rid : a.anchor + "直播间");
                addEmoteFile(files, seen, base, "主播头像", a.face);
                addEmoteFile(files, seen, base, "封面", a.cover);
                addEmoteFile(files, seen, base, "网页端背景图", a.background);
                addEmoteFile(files, seen, base, "App端背景图", a.appBackground);
                addEmoteFile(files, seen, base, "关键帧", a.keyframe);
                List<JsonNode> sorted = new ArrayList<>(a.packages);
                sorted.sort(java.util.Comparator.comparing(
                        p -> "通用表情".equals(p.path("pkg_name").asText("")) ? 1 : 0));
                for (JsonNode pkg : sorted) {
                    String pkgName = sanitize(pkg.path("pkg_name").asText("房间表情"));
                    for (JsonNode e : pkg.path("emoticons")) {
                        addEmoteFile(files, seen, base + "/" + pkgName,
                                sanitize(e.path("emoji").asText("表情")),
                                norm(e.path("url").asText("")));
                    }
                }
                List<String> notes = new ArrayList<>();
                if (a.packages.isEmpty()) {
                    notes.add("直播间表情为空（需登录 Cookie，或该房间无专属表情）");
                } else {
                    List<String> parts = new ArrayList<>();
                    for (JsonNode pkg : sorted) {
                        parts.add(pkg.path("pkg_name").asText("?") + "("
                                + pkg.path("emoticons").size() + ")");
                    }
                    notes.add("表情包：" + String.join("、", parts) + "（专属在前，通用在后）");
                }
                final List<String> fNotes = notes;
                javafx.application.Platform.runLater(() -> {
                    liveRoomFiles.clear();
                    liveRoomFiles.addAll(files);
                    liveRoomTable.setItems(FXCollections.observableArrayList(files));
                    liveRoomInfo.setText((a.title.isEmpty() ? "直播间 " + rid : a.title)
                            + " · 主播 " + (a.anchor.isEmpty() ? "-" : a.anchor)
                            + "｜共 " + files.size() + " 个文件"
                            + (fNotes.isEmpty() ? "" : "  |  " + String.join("；", fNotes)));
                });
            } catch (Exception e) {
                javafx.application.Platform.runLater(() ->
                        liveRoomInfo.setText("加载失败：" + trunc(e.getMessage(), 120)));
            }
        }, "bili-live-emote").start();
    }

    // ==================== 下载 / 工具 ====================

    private void downloadEmoteFiles(List<EmoteFile> files, String subDir,
                                    Label progress, Button button) {
        if (files.isEmpty()) {
            toast.accept("请先加载表情包");
            return;
        }
        List<EmoteFile> snapshot = new ArrayList<>(files);
        Path base = Settings.load().effectiveExportDir().resolve("B站资源").resolve(subDir);
        button.setDisable(true);
        progress.setText("下载中 0/" + snapshot.size());
        new Thread(() -> {
            int ok = 0;
            int fail = 0;
            for (int i = 0; i < snapshot.size(); i++) {
                EmoteFile f = snapshot.get(i);
                Path target = base.resolve(sanitize(f.dir())).resolve(
                        sanitize(f.name()) + fileExt(f.url()));
                var r = downloader.download(List.of(f.url()), target,
                        Map.of("Referer", "https://www.bilibili.com/",
                                "User-Agent", "Mozilla/5.0"), null, () -> false);
                if (r.success()) ok++;
                else fail++;
                int n = i + 1;
                int okN = ok;
                int failN = fail;
                javafx.application.Platform.runLater(() ->
                        progress.setText("下载中 " + n + "/" + snapshot.size()
                                + "（成功 " + okN + "，失败 " + failN + "）"));
            }
            int fok = ok;
            int ffail = fail;
            javafx.application.Platform.runLater(() -> {
                button.setDisable(false);
                progress.setText("完成 " + fok + "/" + snapshot.size()
                        + (ffail > 0 ? "（失败 " + ffail + "）" : ""));
                toast.accept("表情包下载完成：" + fok + " 个");
                try {
                    Files.createDirectories(base);
                    java.awt.Desktop.getDesktop().open(base.toFile());
                } catch (Exception ignored) {
                }
            });
        }, "bili-emote-dl").start();
    }

    private static List<String> pickFormatUrls(JsonNode e, String fmt) {
        String url = norm(e.path("url").asText(""));
        String gif = norm(e.path("gif_url").asText(""));
        String webp = norm(e.path("webp_url").asText(""));
        if ("all".equals(fmt)) {
            Set<String> out = new LinkedHashSet<>();
            for (String u : List.of(gif, webp, url)) if (!u.isEmpty()) out.add(u);
            return new ArrayList<>(out);
        }
        String chosen = switch (fmt) {
            case "gif" -> gif;
            case "webp" -> webp;
            default -> url;
        };
        if (chosen.isEmpty()) chosen = url;
        return chosen.isEmpty() ? List.of() : List.of(chosen);
    }

    private static void addEmoteFile(List<EmoteFile> files, Set<String> seen,
                                     String dir, String name, String url) {
        if (url == null || url.isEmpty()) return;
        if (!seen.add(url)) return;
        files.add(new EmoteFile(name, dir, url));
    }

    private static String fmtKey(String label) {
        if (label == null) return "gif";
        if (label.startsWith("WEBP")) return "webp";
        if (label.startsWith("PNG")) return "png";
        if (label.startsWith("全部")) return "all";
        return "gif";
    }

    private static String emojiKey(String raw) {
        String s = raw == null ? "" : raw.strip();
        int us = s.indexOf('_');
        if (us >= 0) {
            s = s.substring(us + 1);
            s = s.replaceAll("[\\])]+$", "");
        }
        return s;
    }

    private static String sanitize(String s) {
        String out = (s == null ? "" : s).replaceAll("[\\\\/:*?\"<>|\\r\\n\\t]", "")
                .strip().replaceAll("^[._\\s]+|[._\\s]+$", "");
        return out.isEmpty() ? "未命名" : trunc(out, 80);
    }

    private static String norm(String u) {
        if (u == null || u.isEmpty()) return "";
        return u.startsWith("//") ? "https:" + u : u.split("@")[0];
    }

    private static String fileExt(String url) {
        String u = (url == null ? "" : url).split("[?]")[0].toLowerCase(Locale.ROOT);
        for (String ext : List.of(".gif", ".webp", ".png", ".jpg", ".jpeg", ".mp4")) {
            if (u.endsWith(ext)) return ext;
        }
        return ".png";
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }
}
