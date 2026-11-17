package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TextField;
import javafx.scene.image.ImageView;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🚀 继续操作页：自定义软件/链接卡片 + 系统目录快捷入口。
 * 移植自 Python 版 gui_qt/pages/launcher.py（自定义条目、分页、跟随进程树）。
 */
public class LauncherPage extends BasePage {

    private static final int PAGE_SIZE = 12;
    private static final ObjectMapper MAPPER = JsonMapper.get();

    private static final class Item {
        String name = "";
        String path = "";
        String icon = "";
        boolean linked = true;

        boolean isLink() {
            String p = path.toLowerCase(java.util.Locale.ROOT);
            return p.startsWith("http://") || p.startsWith("https://");
        }
    }

    private final FlowPane grid = new FlowPane(10, 10);
    private final VBox configRows = new VBox(6);
    private final Label pageLabel = new Label("");
    private final Label status = new Label();
    private final Label linkedLabel = new Label("");
    private final Button prevBtn = new Button("‹ 上一页");
    private final Button nextBtn = new Button("下一页 ›");
    private final List<Item> items = new ArrayList<>();
    private final List<Process> linkedProcesses = new java.util.concurrent.CopyOnWriteArrayList<>();
    private TabPane tabs;
    private int page;

    private Consumer<String> toast;

    @Override
    public String title() { return "🚀 继续操作"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        items.clear();
        items.addAll(loadItems());
        tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        tabs.getTabs().addAll(new Tab("功能", buildPanel()), new Tab("设置", buildConfig()));
        refreshPanel();
        refreshConfigRows();
        refreshLinkedLabel();

        Label pageTitle = new Label(title());
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");
        Label hint = new Label("自定义软件/网页一键启动；勾选「跟随进程树」的程序会在退出软件时一并关闭");
        hint.setStyle("-fx-text-fill:#5a6072; -fx-font-size:11px;");
        VBox root = new VBox(8, pageTitle, hint, tabs);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(10));
        VBox.setVgrow(tabs, Priority.ALWAYS);
        return root;
    }

    // ==================== 功能 tab ====================

    private Node buildPanel() {
        Button stopAll = new Button("⏹ 停止已链接软件");
        stopAll.setStyle("-fx-background-color:#c62828; -fx-text-fill:white;");
        stopAll.setOnAction(e -> stopAllLinked());
        linkedLabel.setStyle("-fx-text-fill:#5a6072; -fx-font-size:11px;");
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox top = new HBox(8, stopAll, spacer, linkedLabel);
        top.setAlignment(Pos.CENTER_LEFT);

        prevBtn.setOnAction(e -> gotoPage(page - 1));
        nextBtn.setOnAction(e -> gotoPage(page + 1));
        pageLabel.setStyle("-fx-text-fill:#5a6072; -fx-font-size:12px;");
        Region s1 = new Region();
        Region s2 = new Region();
        HBox.setHgrow(s1, Priority.ALWAYS);
        HBox.setHgrow(s2, Priority.ALWAYS);
        HBox nav = new HBox(8, prevBtn, s1, pageLabel, s2, nextBtn);
        nav.setAlignment(Pos.CENTER);

        grid.setPadding(new Insets(4));
        VBox gridBox = new VBox(grid);
        VBox.setVgrow(gridBox, Priority.ALWAYS);

        Label dirsTitle = new Label("常用目录");
        dirsTitle.setStyle("-fx-font-weight:bold; -fx-text-fill:#5a6072;");
        HBox dirs = new HBox(8, dirButton("📁 工作目录", Settings.load().effectiveWorkDir()),
                dirButton("📦 导出目录", Settings.load().effectiveExportDir()),
                dirButton("⚙️ 配置目录", AppPaths.configDir()),
                dirButton("📝 日志目录", AppPaths.logsDir()));
        dirs.setAlignment(Pos.CENTER_LEFT);

        VBox box = new VBox(8, top, nav, gridBox, dirsTitle, dirs);
        box.setPadding(new Insets(10));
        return box;
    }

    private Button dirButton(String text, Path dir) {
        Button b = new Button(text);
        b.setOnAction(e -> {
            try {
                Files.createDirectories(dir);
            } catch (Exception ignored) {
            }
            AudioEngine.openInExplorer(dir);
        });
        return b;
    }

    private void gotoPage(int p) {
        int pages = Math.max(1, (items.size() + PAGE_SIZE - 1) / PAGE_SIZE);
        page = Math.max(0, Math.min(p, pages - 1));
        refreshPanel();
    }

    private void refreshPanel() {
        grid.getChildren().clear();
        int pages = Math.max(1, (items.size() + PAGE_SIZE - 1) / PAGE_SIZE);
        page = Math.max(0, Math.min(page, pages - 1));
        int start = page * PAGE_SIZE;
        int shown = Math.min(PAGE_SIZE, Math.max(0, items.size() - start));
        if (items.isEmpty()) {
            Label empty = new Label("还没有自定义条目，去「设置」页添加软件或链接");
            empty.setStyle("-fx-text-fill:#888; -fx-padding:20;");
            grid.getChildren().add(empty);
        }
        for (int i = 0; i < shown; i++) {
            Item item = items.get(start + i);
            grid.getChildren().add(card(item));
        }
        pageLabel.setText(items.isEmpty() ? "" : "第 " + (page + 1) + " / " + pages
                + " 页 · 共 " + items.size() + " 项");
        prevBtn.setDisable(page <= 0);
        nextBtn.setDisable(page >= pages - 1);
        boolean multi = pages > 1;
        prevBtn.setVisible(multi);
        prevBtn.setManaged(multi);
        nextBtn.setVisible(multi);
        nextBtn.setManaged(multi);
        pageLabel.setVisible(multi);
        pageLabel.setManaged(multi);
    }

    private Button card(Item item) {
        String name = item.name.isBlank() ? (item.isLink() ? "未命名链接" : "未命名软件") : item.name;
        ImageView icon = iconView(item.icon, item.isLink() ? "🔗" : "📦");
        if (icon == null) {
            Label emoji = new Label(item.isLink() ? "🔗" : "📦");
            emoji.setStyle("-fx-font-size: 34px;");
            VBox box = new VBox(6, emoji, new Label(name));
            box.setAlignment(Pos.CENTER);
            Button btn = new Button();
            btn.setGraphic(box);
            btn.setPrefSize(170, 110);
            btn.setOnAction(e -> launch(item));
            return btn;
        }
        VBox box = new VBox(6, icon, new Label(name));
        box.setAlignment(Pos.CENTER);
        Button btn = new Button();
        btn.setGraphic(box);
        btn.setPrefSize(170, 110);
        btn.setTooltip(new javafx.scene.control.Tooltip(item.path));
        btn.setOnAction(e -> launch(item));
        return btn;
    }

    private static ImageView iconView(String path, String fallback) {
        if (path == null || path.isBlank()) return null;
        try {
            Path p = Path.of(path);
            if (!Files.isRegularFile(p)) return null;
            ImageView v = new ImageView(new javafx.scene.image.Image(
                    p.toUri().toString(), false));
            if (v.getImage().isError()) return null;
            v.setFitWidth(56);
            v.setFitHeight(56);
            v.setPreserveRatio(true);
            return v;
        } catch (Exception e) {
            return null;
        }
    }

    private void launch(Item item) {
        if (item.path.isBlank()) {
            toast("该条目没有配置路径");
            return;
        }
        if (item.isLink()) {
            try {
                java.awt.Desktop.getDesktop().browse(java.net.URI.create(item.path));
            } catch (Exception e) {
                toast("打开链接失败：" + e.getMessage());
            }
            return;
        }
        Path p;
        try {
            p = Path.of(item.path);
        } catch (Exception e) {
            toast("路径无效：" + item.path);
            return;
        }
        if (!Files.isRegularFile(p)) {
            toast("文件不存在：" + item.path);
            return;
        }
        if (!item.linked) {
            try {
                java.awt.Desktop.getDesktop().open(p.toFile());
            } catch (Exception e) {
                toast("启动失败：" + e.getMessage());
            }
            return;
        }
        try {
            String lower = p.getFileName().toString().toLowerCase(java.util.Locale.ROOT);
            List<String> cmd = lower.endsWith(".bat") || lower.endsWith(".cmd")
                    ? List.of("cmd", "/c", p.toString())
                    : List.of(p.toString());
            Process process = new ProcessBuilder(cmd)
                    .directory(p.getParent() == null ? null : p.getParent().toFile())
                    .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                    .redirectError(ProcessBuilder.Redirect.DISCARD)
                    .start();
            linkedProcesses.add(process);
            process.onExit().thenRun(() -> {
                linkedProcesses.remove(process);
                javafx.application.Platform.runLater(this::refreshLinkedLabel);
            });
            refreshLinkedLabel();
            toast("已启动（跟随进程树）：" + p.getFileName());
        } catch (Exception e) {
            toast("启动失败：" + e.getMessage());
        }
    }

    private void stopAllLinked() {
        int n = linkedProcesses.size();
        for (Process p : new ArrayList<>(linkedProcesses)) {
            try {
                p.descendants().forEach(ProcessHandle::destroyForcibly);
                p.destroyForcibly();
            } catch (Exception ignored) {
            }
        }
        linkedProcesses.clear();
        refreshLinkedLabel();
        toast("已停止 " + n + " 个跟随进程");
    }

    private void refreshLinkedLabel() {
        linkedProcesses.removeIf(p -> !p.isAlive());
        linkedLabel.setText("跟随进程：" + linkedProcesses.size() + " 个");
    }

    @Override
    public void shutdown() {
        stopAllLinked();
    }

    // ==================== 设置 tab ====================

    private Node buildConfig() {
        Button addApp = new Button("➕ 添加软件");
        addApp.setOnAction(e -> addItem(false));
        Button addLink = new Button("🔗 添加链接");
        addLink.setStyle("-fx-background-color:#1a5c2a; -fx-text-fill:white;");
        addLink.setOnAction(e -> addItem(true));
        Button save = new Button("💾 保存并刷新面板");
        save.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white;");
        save.setOnAction(e -> {
            saveItems();
            refreshPanel();
            toast("已保存 " + items.size() + " 个条目");
        });
        HBox bar = new HBox(8, addApp, addLink, save);
        bar.setAlignment(Pos.CENTER_LEFT);

        ScrollPane scroll = new ScrollPane(configRows);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        VBox box = new VBox(8, bar, scroll);
        box.setPadding(new Insets(10));
        VBox.setVgrow(scroll, Priority.ALWAYS);
        return box;
    }

    private void addItem(boolean link) {
        Item item = new Item();
        item.name = link ? "新链接" : "新软件";
        item.path = link ? "https://" : "";
        item.linked = !link;
        items.add(item);
        refreshConfigRows();
        if (!link) browseItem(item);
    }

    private void refreshConfigRows() {
        configRows.getChildren().clear();
        for (int i = 0; i < items.size(); i++) {
            final int idx = i;
            Item item = items.get(i);
            TextField name = new TextField(item.name);
            name.setPrefWidth(130);
            name.textProperty().addListener((o, a, b) -> item.name = b);
            TextField path = new TextField(item.path);
            path.setPromptText(item.isLink() ? "http/https 链接" : "程序路径");
            path.textProperty().addListener((o, a, b) -> item.path = b);
            HBox.setHgrow(path, Priority.ALWAYS);
            TextField icon = new TextField(item.icon);
            icon.setPromptText("图标文件（可空）");
            icon.setPrefWidth(170);
            icon.textProperty().addListener((o, a, b) -> item.icon = b);
            Button browse = new Button("📁");
            browse.setOnAction(e -> {
                FileChooser chooser = new FileChooser();
                chooser.setTitle("选择程序文件");
                File f = chooser.showOpenDialog(configRows.getScene() == null
                        ? null : configRows.getScene().getWindow());
                if (f != null) {
                    path.setText(f.getAbsolutePath());
                    item.path = f.getAbsolutePath();
                }
            });
            Button pickIcon = new Button("🖼");
            pickIcon.setOnAction(e -> {
                FileChooser chooser = new FileChooser();
                chooser.setTitle("选择图标图片");
                chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter(
                        "图片", "*.png", "*.jpg", "*.jpeg", "*.ico"));
                File f = chooser.showOpenDialog(configRows.getScene() == null
                        ? null : configRows.getScene().getWindow());
                if (f != null) {
                    icon.setText(f.getAbsolutePath());
                    item.icon = f.getAbsolutePath();
                }
            });
            CheckBox linked = new CheckBox("📌 跟随进程树");
            linked.setSelected(item.linked);
            linked.setDisable(item.isLink());
            linked.setOnAction(e -> item.linked = linked.isSelected());
            Button up = new Button("⬆");
            up.setOnAction(e -> moveItem(idx, -1));
            Button down = new Button("⬇");
            down.setOnAction(e -> moveItem(idx, 1));
            Button del = new Button("✕");
            del.setStyle("-fx-text-fill:#c62828;");
            del.setOnAction(e -> {
                items.remove(item);
                refreshConfigRows();
            });
            HBox row = new HBox(6, new Label("⋮⋮"), name, icon, pickIcon, path, browse,
                    linked, up, down, del);
            row.setAlignment(Pos.CENTER_LEFT);
            row.setPadding(new Insets(4, 6, 4, 6));
            row.setStyle("-fx-background-color:#eef1f6; -fx-background-radius:6;");
            configRows.getChildren().add(row);
        }
        if (items.isEmpty()) {
            configRows.getChildren().add(new Label("暂无条目，点上方「添加软件 / 添加链接」。"));
        }
    }

    private void moveItem(int idx, int delta) {
        int j = idx + delta;
        if (idx < 0 || idx >= items.size() || j < 0 || j >= items.size()) return;
        Item tmp = items.get(idx);
        items.set(idx, items.get(j));
        items.set(j, tmp);
        refreshConfigRows();
    }

    private void browseItem(Item item) {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择程序文件");
        File f = chooser.showOpenDialog(configRows.getScene() == null
                ? null : configRows.getScene().getWindow());
        if (f != null) {
            item.path = f.getAbsolutePath();
            if (item.name.isBlank() || "新软件".equals(item.name)) {
                item.name = f.getName();
            }
            refreshConfigRows();
        }
    }

    // ==================== 配置 ====================

    private static Path configFile() {
        return AppPaths.configDir().resolve("launcher.json");
    }

    private static List<Item> loadItems() {
        List<Item> out = new ArrayList<>();
        Path f = configFile();
        if (!Files.isRegularFile(f)) return out;
        try {
            JsonNode root = MAPPER.readTree(f.toFile());
            JsonNode arr = root.isArray() ? root : root.path("items");
            for (JsonNode n : arr) {
                Item item = new Item();
                item.name = n.path("name").asText("");
                item.path = n.path("path").asText("");
                item.icon = n.path("icon").asText("");
                item.linked = n.path("linked").asBoolean(true);
                if (!item.path.isBlank()) out.add(item);
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    private void saveItems() {
        try {
            ObjectNode root = MAPPER.createObjectNode();
            ArrayNode arr = root.putArray("items");
            for (Item item : items) {
                ObjectNode n = arr.addObject();
                n.put("name", item.name);
                n.put("path", item.path);
                n.put("icon", item.icon);
                n.put("linked", item.linked);
            }
            Files.createDirectories(configFile().getParent());
            Files.writeString(configFile(), MAPPER.writerWithDefaultPrettyPrinter()
                    .writeValueAsString(root), java.nio.charset.StandardCharsets.UTF_8);
        } catch (Exception e) {
            toast("保存失败：" + e.getMessage());
        }
    }
}
