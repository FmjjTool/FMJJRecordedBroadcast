package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.bili.BiliDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliParser;
import cn.fmjj.toolbox.engine.store.MediaStore;
import javafx.application.Platform;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 📦 已获取（对齐 Python 版 info_results.py 的四栏结构）。
 *
 * <p>顶部：标题(18px) + 伸展 + `📤 导出所有勾选`(绿) + `📥 下载所有勾选`(绿) +
 * `🗑 删除所有勾选`(红) + `🗑 全部清空`(红) + `画质:`(110) + `线程:`(64)；
 * 第二行：`导出格式:` JSON / XLSX / MD；主体：**四栏**（📕 抖音 | 📺 B站 | 📝 微博 | 📦 其他），
 * 每栏白底圆角，栏头「平台名 + 计数」，按钮行「全选/反选/清除 + 📤 导出 + 📥 下载」，下面是卡片列表。
 */
public class InfoResultsPage extends BasePage {

    private static final String[][] COLUMNS = {
            {"douyin", "📕 抖音"}, {"bilibili", "📺 B站"}, {"weibo", "📝 微博"}, {"other", "📦 其他"}
    };

    /** 一条记录 + 勾选状态。 */
    private static final class Item {
        final MediaStore.Row row;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);
        Item(MediaStore.Row row) { this.row = row; }
    }

    private final Map<String, VBox> columnLists = new LinkedHashMap<>();
    private final Map<String, Label> columnCounts = new LinkedHashMap<>();
    private final Map<String, List<Item>> columnItems = new LinkedHashMap<>();
    private final ComboBox<String> qualityBox = new ComboBox<>(FXCollections.observableArrayList(
            "最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"));
    private final ComboBox<String> threadsBox = new ComboBox<>(FXCollections.observableArrayList(
            "1", "2", "4", "8", "16"));
    private final CheckBox fmtJson = new CheckBox("JSON");
    private final CheckBox fmtXlsx = new CheckBox("XLSX");
    private final CheckBox fmtMd = new CheckBox("MD");
    private final Label status = new Label("就绪");

    private final BiliParser biliParser = new BiliParser();
    private final BiliDownloader biliDownloader = new BiliDownloader();

    private Consumer<String> toast;

    @Override
    public String title() { return "📦 已获取"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("📦 已获取（按平台分栏，按发布日期倒序）");
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        Button exportAll = greenBtn("📤 导出所有勾选");
        exportAll.setTooltip(new javafx.scene.control.Tooltip("导出四栏内全部勾选条目"));
        exportAll.setOnAction(e -> exportChecked(null));
        Button dlAll = greenBtn("📥 下载所有勾选");
        dlAll.setTooltip(new javafx.scene.control.Tooltip("下载四栏内全部勾选条目"));
        dlAll.setOnAction(e -> downloadChecked(null));
        Button delSel = redBtn("🗑 删除所有勾选");
        delSel.setTooltip(new javafx.scene.control.Tooltip("从已获取列表移除勾选条目（不影响本地文件）"));
        delSel.setOnAction(e -> deleteChecked());
        Button clearAll = redBtn("🗑 全部清空");
        clearAll.setOnAction(e -> clearAll());

        qualityBox.getSelectionModel().selectFirst();
        qualityBox.setPrefWidth(110);
        threadsBox.getSelectionModel().select("4");
        threadsBox.setPrefWidth(64);

        HBox head = new HBox(8, pageTitle, spacer(), exportAll, dlAll, delSel, clearAll,
                new Label("画质:"), qualityBox, new Label("线程:"), threadsBox);
        head.setAlignment(Pos.CENTER_LEFT);

        fmtJson.setSelected(true);
        fmtXlsx.setSelected(true);
        fmtMd.setSelected(true);
        fmtXlsx.setTooltip(new javafx.scene.control.Tooltip("XLSX 待补（当前导出 CSV 兼容格式）"));
        HBox fmtRow = new HBox(6, new Label("导出格式:"), fmtJson, fmtXlsx, fmtMd, spacer(), status);
        fmtRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        HBox cols = new HBox(8);
        for (String[] col : COLUMNS) {
            cols.getChildren().add(buildColumn(col[0], col[1]));
        }
        VBox.setVgrow(cols, Priority.ALWAYS);

        VBox root = new VBox(6, head, fmtRow, cols);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(10));
        Platform.runLater(this::refresh);
        return root;
    }

    private static Button greenBtn(String text) {
        Button b = new Button(text);
        b.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        return b;
    }

    private static Button redBtn(String text) {
        Button b = new Button(text);
        b.setStyle("-fx-background-color: #c62828; -fx-text-fill: white;");
        return b;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private Node buildColumn(String key, String name) {
        VBox col = new VBox(4);
        col.setStyle("-fx-background-color: white; -fx-background-radius: 8; "
                + "-fx-border-color: #eee; -fx-border-radius: 8;");
        col.setPadding(new Insets(8));
        HBox.setHgrow(col, Priority.ALWAYS);
        col.setPrefWidth(300);

        Label nameLabel = new Label(name);
        Label count = new Label("0/0");
        count.setStyle("-fx-text-fill: #1976d2;");
        HBox colHead = new HBox(6, nameLabel, spacer(), count);
        colHead.setAlignment(Pos.CENTER_LEFT);

        Button selAll = tinyBtn("全选", () -> selectColumn(key, "all"));
        Button selInv = tinyBtn("反选", () -> selectColumn(key, "invert"));
        Button selNone = tinyBtn("清除", () -> selectColumn(key, "none"));
        Button export = tinyBtn("📤 导出", () -> exportChecked(key));
        Button dl = tinyBtn("📥 下载", () -> downloadChecked(key));
        HBox btns = new HBox(2, selAll, selInv, selNone, export, dl);
        btns.setAlignment(Pos.CENTER_LEFT);

        VBox list = new VBox(4);
        ScrollPane scroll = new ScrollPane(list);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        scroll.setStyle("-fx-background-color: transparent;");
        VBox.setVgrow(scroll, Priority.ALWAYS);

        col.getChildren().addAll(colHead, btns, scroll);
        columnLists.put(key, list);
        columnCounts.put(key, count);
        return col;
    }

    private static Button tinyBtn(String text, Runnable action) {
        Button b = new Button(text);
        int w = text.length() > 2 ? 52 : 40;
        b.setMinSize(w, 22);
        b.setMaxSize(w, 22);
        b.setStyle("-fx-font-size: 10px;");
        b.setOnAction(e -> action.run());
        return b;
    }

    // ==================== 数据 ====================

    private void refresh() {
        javafx.concurrent.Task<List<MediaStore.Row>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<MediaStore.Row> call() {
                try (MediaStore store = new MediaStore()) {
                    return store.list(null, 2000);
                }
            }
        };
        task.setOnSucceeded(e -> render(task.getValue()));
        task.setOnFailed(e -> status.setText("读取失败：" + task.getException().getMessage()));
        new Thread(task, "results-refresh").start();
    }

    private void render(List<MediaStore.Row> rows) {
        for (VBox list : columnLists.values()) list.getChildren().clear();
        for (List<Item> items : columnItems.values()) items.clear();

        for (MediaStore.Row row : rows) {
            String key = columnLists.containsKey(row.platform()) ? row.platform() : "other";
            Item item = new Item(row);
            item.checked.addListener((o, a, b) -> updateCounts());
            columnItems.computeIfAbsent(key, k -> new ArrayList<>()).add(item);
            columnLists.get(key).getChildren().add(card(item));
        }
        updateCounts();
        status.setText("共 " + rows.size() + " 条");
    }

    private Node card(Item item) {
        CheckBox cb = new CheckBox();
        cb.selectedProperty().bindBidirectional(item.checked);

        boolean isVideo = item.row.savedPath() != null && item.row.savedPath().endsWith(".mp4");
        String title = item.row.title() == null || item.row.title().isBlank()
                ? "(无标题)" : item.row.title();
        Label name = new Label((isVideo ? "[视频] " : "") + title);
        name.setStyle("-fx-font-size: 11px;");
        name.setWrapText(true);
        HBox.setHgrow(name, Priority.ALWAYS);

        String detail = (item.row.author() == null ? "-" : item.row.author())
                + "　" + (item.row.publishTime() == null || item.row.publishTime().isBlank()
                ? "-" : item.row.publishTime())
                + "　" + (item.row.savedPath() == null || item.row.savedPath().isBlank() ? "未下载" : "已下载");
        Label meta = new Label(detail);
        meta.setStyle("-fx-font-size: 10px; -fx-text-fill: #888;");
        meta.setWrapText(true);

        VBox text = new VBox(1, name, meta);
        HBox.setHgrow(text, Priority.ALWAYS);
        HBox row = new HBox(4, cb, text);
        row.setAlignment(Pos.TOP_LEFT);
        row.setPadding(new Insets(3));
        row.setStyle("-fx-border-color: #f0f0f0; -fx-border-radius: 4;");
        row.setOnMouseClicked(e -> {
            if (e.getClickCount() == 2) {
                String p = item.row.savedPath();
                if (p != null && !p.isBlank() && Files.exists(Path.of(p))) {
                    AudioEngine.openInExplorer(Path.of(p));
                } else {
                    toast("本地文件不存在（未下载）");
                }
            }
        });
        return row;
    }

    private void updateCounts() {
        int total = 0;
        for (String[] col : COLUMNS) {
            List<Item> items = columnItems.getOrDefault(col[0], List.of());
            long checked = items.stream().filter(i -> i.checked.get()).count();
            Label c = columnCounts.get(col[0]);
            if (c != null) c.setText(checked + "/" + items.size());
            total += items.size();
        }
        if (total == 0) status.setText("暂无已获取数据（解析内容后自动入库）");
    }

    private void selectColumn(String key, String mode) {
        for (Item i : columnItems.getOrDefault(key, List.of())) {
            i.checked.set(switch (mode) {
                case "all" -> true;
                case "none" -> false;
                default -> !i.checked.get();
            });
        }
        updateCounts();
    }

    private List<Item> checkedItems(String key) {
        List<Item> out = new ArrayList<>();
        if (key == null) {
            for (List<Item> items : columnItems.values()) {
                for (Item i : items) if (i.checked.get()) out.add(i);
            }
        } else {
            for (Item i : columnItems.getOrDefault(key, List.of())) if (i.checked.get()) out.add(i);
        }
        return out;
    }

    // ==================== 导出 ====================

    private void exportChecked(String key) {
        List<Item> items = checkedItems(key);
        if (items.isEmpty()) { status.setText("没有勾选条目"); return; }
        if (!fmtJson.isSelected() && !fmtMd.isSelected() && !fmtXlsx.isSelected()) {
            status.setText("请至少勾选一种导出格式");
            return;
        }
        Path outDir = Settings.load().effectiveExportDir().resolve("已获取导出");
        try {
            Files.createDirectories(outDir);
            String stamp = java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss")
                    .format(java.time.LocalDateTime.now());
            List<String> saved = new ArrayList<>();
            if (fmtJson.isSelected()) {
                Path p = outDir.resolve("已获取_" + stamp + ".json");
                Files.writeString(p, toJson(items), StandardCharsets.UTF_8);
                saved.add(p.getFileName().toString());
            }
            if (fmtMd.isSelected()) {
                Path p = outDir.resolve("已获取_" + stamp + ".md");
                Files.writeString(p, toMarkdown(items), StandardCharsets.UTF_8);
                saved.add(p.getFileName().toString());
            }
            if (fmtXlsx.isSelected()) {
                Path p = outDir.resolve("已获取_" + stamp + ".csv");
                Files.writeString(p, toCsv(items), StandardCharsets.UTF_8);
                saved.add(p.getFileName().toString() + "（CSV，XLSX 待补）");
            }
            status.setText("已导出：" + String.join(" / ", saved));
            toast("导出完成：" + items.size() + " 条");
        } catch (Exception e) {
            status.setText("导出失败：" + e.getMessage());
        }
    }

    private static String esc(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }

    private static String toJson(List<Item> items) {
        StringBuilder sb = new StringBuilder("[\n");
        for (int i = 0; i < items.size(); i++) {
            var r = items.get(i).row;
            sb.append("  {\"platform\":\"").append(esc(r.platform()))
              .append("\",\"mediaId\":\"").append(esc(r.mediaId()))
              .append("\",\"title\":\"").append(esc(r.title()))
              .append("\",\"author\":\"").append(esc(r.author()))
              .append("\",\"publishTime\":\"").append(esc(r.publishTime()))
              .append("\",\"duration\":").append(r.duration())
              .append(",\"savedPath\":\"").append(esc(r.savedPath()))
              .append("\"}").append(i == items.size() - 1 ? "" : ",").append("\n");
        }
        return sb.append("]\n").toString();
    }

    private static String toMarkdown(List<Item> items) {
        StringBuilder sb = new StringBuilder("| 平台 | 标题 | 作者 | 发布 | 本地 |\n|---|---|---|---|---|\n");
        for (Item it : items) {
            var r = it.row;
            sb.append("| ").append(CookieStore.PLATFORM_NAMES.getOrDefault(r.platform(), r.platform()))
              .append(" | ").append(nz(r.title()))
              .append(" | ").append(nz(r.author()))
              .append(" | ").append(nz(r.publishTime()))
              .append(" | ").append(nz(r.savedPath()).isBlank() ? "未下载" : "已下载")
              .append(" |\n");
        }
        return sb.toString();
    }

    private static String toCsv(List<Item> items) {
        StringBuilder sb = new StringBuilder("平台,标题,作者,发布,时长(秒),本地路径\n");
        for (Item it : items) {
            var r = it.row;
            sb.append('"').append(CookieStore.PLATFORM_NAMES.getOrDefault(r.platform(), r.platform()))
              .append("\",\"").append(nz(r.title()).replace("\"", "\"\""))
              .append("\",\"").append(nz(r.author()).replace("\"", "\"\""))
              .append("\",\"").append(nz(r.publishTime()))
              .append("\",").append(r.duration())
              .append(",\"").append(nz(r.savedPath()).replace("\"", "\"\""))
              .append("\"\n");
        }
        return "\uFEFF" + sb;
    }

    private static String nz(String s) { return s == null ? "" : s; }

    // ==================== 下载 / 删除 ====================

    private void downloadChecked(String key) {
        List<Item> items = checkedItems(key);
        if (items.isEmpty()) { status.setText("没有勾选条目"); return; }
        int threads = 4;
        try {
            threads = Integer.parseInt(threadsBox.getValue());
        } catch (Exception ignored) {
        }
        final int th = threads;
        Path outRoot = Settings.load().effectiveExportDir();
        String cookie = CookieStore.get("bilibili");
        status.setText("下载中…（" + items.size() + " 条）");

        javafx.concurrent.Task<int[]> job = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0, skip = 0;
                for (Item it : items) {
                    var r = it.row;
                    if (!"bilibili".equals(r.platform())) {
                        skip++;
                        continue;
                    }
                    try {
                        var info = biliParser.videoInfo(r.mediaId(), cookie);
                        var res = biliDownloader.download(info.bvid(), 0, cookie, outRoot,
                                msg -> {}, p -> { }, () -> false, th);
                        if (res.success()) {
                            ok++;
                            try (MediaStore store = new MediaStore()) {
                                store.upsert(cn.fmjj.toolbox.engine.platform.MediaItem.of("bilibili",
                                        info.bvid(), info.title(), info.owner(), info.cover(),
                                        info.publishText(), info.duration(), info.desc(), List.of(), "{}"),
                                        res.path().toString());
                            }
                        } else {
                            fail++;
                        }
                    } catch (Exception e) {
                        fail++;
                    }
                }
                return new int[] {ok, fail, skip};
            }
        };
        job.setOnSucceeded(e -> {
            int[] r = job.getValue();
            status.setText("下载完成：成功 " + r[0] + "，失败 " + r[1] + "，跳过 " + r[2] + "（非 B站暂不支持重下）");
            toast("下载完成：" + r[0] + " 成功");
            refresh();
        });
        job.setOnFailed(e -> status.setText("下载异常：" + job.getException().getMessage()));
        new Thread(job, "results-download").start();
    }

    private void deleteChecked() {
        List<Item> items = checkedItems(null);
        if (items.isEmpty()) { status.setText("没有勾选条目"); return; }
        try (MediaStore store = new MediaStore()) {
            int n = 0;
            for (Item it : items) {
                if (store.delete(it.row.id())) n++;
            }
            status.setText("已移除 " + n + " 条（本地文件保留）");
        } catch (Exception e) {
            status.setText("删除失败：" + e.getMessage());
        }
        refresh();
    }

    private void clearAll() {
        javafx.scene.control.Alert alert = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.CONFIRMATION,
                "确认清空全部已获取记录？（不影响已下载的本地文件）",
                javafx.scene.control.ButtonType.OK, javafx.scene.control.ButtonType.CANCEL);
        alert.setHeaderText(null);
        alert.showAndWait().ifPresent(bt -> {
            if (bt != javafx.scene.control.ButtonType.OK) return;
            try (MediaStore store = new MediaStore()) {
                for (MediaStore.Row r : store.list(null, 100000)) store.delete(r.id());
            } catch (Exception ignored) {
            }
            status.setText("已全部清空");
            refresh();
        });
    }
}
