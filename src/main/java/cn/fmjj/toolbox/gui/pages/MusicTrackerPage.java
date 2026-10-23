package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.tracker.MusicTrackerStore;
import cn.fmjj.toolbox.engine.tracker.TrackerStore;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TextField;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

/**
 * 🎼 音乐账号追踪（对齐 Python 版 music_tracker.py 的骨架）。
 *
 * <p>顶部：`🎼 音乐账号追踪` + `🔄 刷新` + `📷 按组导出` + `○ 定时采集` + `⬇ 获取全部` + 平台下拉；
 * 双 Tab：功能页（卡片：昵称/平台/粉丝/涨跌/采集·导出·历史）/ ⚙️ 设置（搜索 + 添加账号 + 组号 + 列表）。
 *
 * <p>**采集接口全部未移植**（网易云/QQ/酷狗/酷我/5sing 的歌手主页接口），
 * 点击采集会明确提示；配置、卡片、涨跌对比、历史、按组导出等**纯本地能力已可用**。
 */
public class MusicTrackerPage extends BasePage {

    private final FlowPane cardGrid = new FlowPane(10, 10);
    private final ListView<String> accountList = new ListView<>();
    private final TextField nameField = new TextField();
    private final ComboBox<String> platformBox = new ComboBox<>();
    private final TextField urlField = new TextField();
    private final ComboBox<String> groupBox = new ComboBox<>();
    private final TextField groupNameField = new TextField();
    private final ComboBox<String> batchPlatform = new ComboBox<>();
    private final Label status = new Label("");
    private final Label timerDot = new Label("○");

    private final List<MusicTrackerStore.Account> accounts = new ArrayList<>();
    private javafx.animation.Timeline timer;

    private Consumer<String> toast;

    @Override
    public String title() { return "🎼 音乐账号追踪"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("🎼 音乐账号追踪");
        pageTitle.setStyle("-fx-font-size: 16px; -fx-font-weight: bold;");
        Button refresh = new Button("🔄 刷新");
        refresh.setOnAction(e -> {
            loadAccounts();
            renderCards();
        });
        Button exportGroup = new Button("📷 按组导出");
        exportGroup.setOnAction(e -> exportByGroup());
        timerDot.setStyle("-fx-text-fill: #888;");
        Button timerBtn = new Button("⏰ 定时采集");
        timerBtn.setTooltip(new javafx.scene.control.Tooltip("每 30 分钟自动采集一次"));
        timerBtn.setOnAction(e -> toggleTimer());
        Button fetchAll = new Button("⬇ 获取全部");
        fetchAll.setOnAction(e -> fetchAll());
        batchPlatform.getItems().add("全部平台");
        batchPlatform.getItems().addAll(MusicTrackerStore.PLATFORM_NAMES.values());
        batchPlatform.getSelectionModel().selectFirst();
        batchPlatform.setPrefWidth(92);

        HBox head = new HBox(6, pageTitle, refresh, exportGroup, timerDot, timerBtn,
                fetchAll, batchPlatform, spacer(), status);
        head.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        TabPane tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        tabs.getTabs().addAll(buildCardsTab(), buildSettingsTab());
        VBox.setVgrow(tabs, Priority.ALWAYS);

        VBox root = new VBox(6, head, tabs);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(8));
        VBox.setVgrow(tabs, Priority.ALWAYS);
        Platform.runLater(() -> {
            loadAccounts();
            renderCards();
        });
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    // ==================== 卡片 ====================

    private Tab buildCardsTab() {
        cardGrid.setPadding(new Insets(10));
        cardGrid.setPrefWrapLength(900);
        ScrollPane scroll = new ScrollPane(cardGrid);
        scroll.setFitToWidth(true);
        return new Tab("功能", scroll);
    }

    private void renderCards() {
        cardGrid.getChildren().clear();
        if (accounts.isEmpty()) {
            Label empty = new Label("（暂无音乐账号，请在「⚙️ 音乐账号追踪设置」里添加）");
            empty.setStyle("-fx-text-fill: #888;");
            cardGrid.getChildren().add(empty);
            return;
        }
        for (MusicTrackerStore.Account a : accounts) cardGrid.getChildren().add(card(a));
    }

    private Node card(MusicTrackerStore.Account a) {
        Label name = new Label("🎤 " + (a.name().isEmpty() ? "未命名" : a.name()));
        name.setStyle("-fx-font-size: 13px; -fx-font-weight: bold;");
        Label plat = new Label(MusicTrackerStore.PLATFORM_NAMES
                .getOrDefault(a.platform(), a.platform())
                + (a.groupName().isEmpty() ? "" : "　" + a.groupName()));
        plat.setStyle("-fx-font-size: 11px; -fx-text-fill: #6b7280;");

        long delta = a.lastFetch().isEmpty() ? 0 : MusicTrackerStore.delta(a.name(), a.fans());
        String deltaText = delta == 0 ? "" : (delta > 0 ? "  ↑" + TrackerStore.formatCount(delta)
                : "  ↓" + TrackerStore.formatCount(-delta));
        Label fans = new Label("粉丝 " + TrackerStore.formatCount(a.fans()) + deltaText);
        // 涨绿跌红（音乐平台粉丝数用绿涨红跌：与股市相反，这里按“数据变好=绿”习惯）
        fans.setStyle("-fx-font-size: 12px; -fx-text-fill: "
                + (delta > 0 ? "#2e7d32" : delta < 0 ? "#c62828" : "#1976d2") + ";");

        Label last = new Label(a.lastFetch().isEmpty() ? "未采集" : "采集于 " + a.lastFetch());
        last.setStyle("-fx-font-size: 10px; -fx-text-fill: #9ca3af;");

        Button fetch = new Button("⬇ 采集");
        fetch.setStyle("-fx-font-size: 10px;");
        fetch.setOnAction(e -> fetchOne(a));
        Button export = new Button("📷 导出");
        export.setStyle("-fx-font-size: 10px;");
        export.setOnAction(e -> exportOne(a));
        Button history = new Button("📜 历史");
        history.setStyle("-fx-font-size: 10px;");
        history.setOnAction(e -> showHistory(a));
        HBox btns = new HBox(4, fetch, export, history);

        VBox box = new VBox(4, name, plat, fans, last, btns);
        box.setPadding(new Insets(10));
        box.setPrefWidth(220);
        box.setStyle("-fx-background-color: white; -fx-border-color: #e0e4ea; "
                + "-fx-border-radius: 6; -fx-background-radius: 6;");
        return box;
    }

    // ==================== 设置 ====================

    private Tab buildSettingsTab() {
        Label searchTitle = new Label("搜索账号（按名称搜索歌手/用户；接口未移植，当前仅能手动填链接）");
        searchTitle.setWrapText(true);
        searchTitle.setStyle("-fx-font-weight: bold;");

        Label addTitle = new Label("添加追踪账号（账号名 + 链接地址）");
        addTitle.setStyle("-fx-font-weight: bold;");

        nameField.setPromptText("账号名");
        nameField.setPrefWidth(140);
        platformBox.getItems().addAll(MusicTrackerStore.PLATFORM_NAMES.values());
        platformBox.getSelectionModel().selectFirst();
        platformBox.setPrefWidth(100);
        HBox r1 = new HBox(6, new Label("账号名:"), nameField, new Label("平台:"), platformBox);
        r1.setAlignment(Pos.CENTER_LEFT);

        urlField.setPromptText("账号/歌手主页链接（可自动识别平台）");
        urlField.textProperty().addListener((o, a, b) -> {
            String p = MusicTrackerStore.guessPlatform(b);
            if (!p.isEmpty()) {
                platformBox.getSelectionModel().select(MusicTrackerStore.PLATFORM_NAMES.get(p));
            }
        });
        HBox r2 = new HBox(6, new Label("链接:"), urlField);
        r2.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(urlField, Priority.ALWAYS);

        groupBox.getItems().addAll("未分组", "1 组", "2 组", "3 组", "4 组", "5 组");
        groupBox.getSelectionModel().selectFirst();
        groupBox.setPrefWidth(120);
        groupNameField.setPromptText("组名（可空）");
        groupNameField.setPrefWidth(140);
        HBox r3 = new HBox(6, new Label("组号:"), groupBox, groupNameField);
        r3.setAlignment(Pos.CENTER_LEFT);

        Button add = new Button("＋ 添加账号");
        add.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        add.setOnAction(e -> addAccount());
        Button del = new Button("🗑 删除选中");
        del.setOnAction(e -> deleteSelected());
        HBox r4 = new HBox(6, add, del, spacer());
        r4.setAlignment(Pos.CENTER_LEFT);

        Label listTitle = new Label("已配置账号");
        listTitle.setStyle("-fx-font-weight: bold;");
        accountList.setPlaceholder(new Label("（暂无账号）"));
        VBox.setVgrow(accountList, Priority.ALWAYS);

        Label hint = new Label("⚠️ 网易云 / QQ音乐 / 酷狗 / 酷我 / 5sing 的歌手主页接口均未移植，"
                + "「采集」会明确提示。配置、卡片、涨跌对比、历史、按组导出等纯本地能力已可用。");
        hint.setWrapText(true);
        hint.setStyle("-fx-font-size: 11px; -fx-text-fill: #c62828;");

        VBox box = new VBox(8, searchTitle, addTitle, r1, r2, r3, r4, listTitle, accountList, hint);
        box.setPadding(new Insets(10));
        VBox.setVgrow(accountList, Priority.ALWAYS);
        return new Tab("⚙️ 音乐账号追踪设置", box);
    }

    // ==================== 数据 ====================

    private void loadAccounts() {
        accounts.clear();
        accounts.addAll(MusicTrackerStore.loadAccounts());
        List<String> names = new ArrayList<>();
        for (MusicTrackerStore.Account a : accounts) {
            names.add(a.name() + "　" + MusicTrackerStore.PLATFORM_NAMES
                    .getOrDefault(a.platform(), a.platform())
                    + (a.groupName().isEmpty() ? "" : "　" + a.groupName()));
        }
        accountList.setItems(FXCollections.observableArrayList(names));
        status.setText("共 " + accounts.size() + " 个音乐账号");
    }

    private void addAccount() {
        String name = nameField.getText() == null ? "" : nameField.getText().trim();
        String url = urlField.getText() == null ? "" : urlField.getText().trim();
        if (name.isEmpty()) { status.setText("请填写账号名"); return; }
        if (url.isEmpty()) { status.setText("请填写主页链接"); return; }
        String platCn = platformBox.getValue();
        String platform = MusicTrackerStore.PLATFORM_NAMES.entrySet().stream()
                .filter(e -> e.getValue().equals(platCn)).map(Map.Entry::getKey).findFirst().orElse("netease");
        String g = groupBox.getValue();
        accounts.add(new MusicTrackerStore.Account(name, platform, url,
                "未分组".equals(g) ? "" : g,
                groupNameField.getText() == null ? "" : groupNameField.getText().trim(),
                0, ""));
        MusicTrackerStore.saveAccounts(accounts);
        nameField.clear();
        urlField.clear();
        loadAccounts();
        renderCards();
        status.setText("已添加：" + name);
    }

    private void deleteSelected() {
        int idx = accountList.getSelectionModel().getSelectedIndex();
        if (idx < 0 || idx >= accounts.size()) { status.setText("请先选中账号"); return; }
        String name = accounts.remove(idx).name();
        MusicTrackerStore.saveAccounts(accounts);
        loadAccounts();
        renderCards();
        status.setText("已删除：" + name);
    }

    // ==================== 采集 / 历史 ====================

    private void fetchOne(MusicTrackerStore.Account a) {
        status.setText(MusicTrackerStore.PLATFORM_NAMES.getOrDefault(a.platform(), a.platform())
                + " 的歌手主页接口未移植，无法采集「" + a.name() + "」");
        toast("该平台采集接口未移植");
    }

    private void fetchAll() {
        if (accounts.isEmpty()) { status.setText("没有账号"); return; }
        String platCn = batchPlatform.getValue();
        long count = "全部平台".equals(platCn) ? accounts.size()
                : accounts.stream().filter(a -> MusicTrackerStore.PLATFORM_NAMES
                .getOrDefault(a.platform(), a.platform()).equals(platCn)).count();
        status.setText("待采集 " + count + " 个 —— 但音乐平台采集接口均未移植（仅本地记录可用）");
        toast("音乐平台采集接口未移植");
    }

    private void toggleTimer() {
        if (timer != null) {
            timer.stop();
            timer = null;
            timerDot.setText("○");
            timerDot.setStyle("-fx-text-fill: #888;");
            toast("定时采集已关闭");
            return;
        }
        timer = new javafx.animation.Timeline(
                new javafx.animation.KeyFrame(javafx.util.Duration.minutes(30), e -> fetchAll()));
        timer.setCycleCount(javafx.animation.Animation.INDEFINITE);
        timer.play();
        timerDot.setText("●");
        timerDot.setStyle("-fx-text-fill: #2e7d32;");
        toast("定时采集已开启（每 30 分钟）");
    }

    private void showHistory(MusicTrackerStore.Account a) {
        List<MusicTrackerStore.Snapshot> list = MusicTrackerStore.loadHistory()
                .getOrDefault(a.name(), List.of());
        StringBuilder sb = new StringBuilder("账号：" + a.name() + "\n\n");
        if (list.isEmpty()) {
            sb.append("（暂无历史快照；采集成功后会自动记录）");
        } else {
            for (var s : list) sb.append(s.time()).append("　粉丝 ")
                    .append(TrackerStore.formatCount(s.fans())).append("\n");
        }
        javafx.scene.control.Alert alert = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.INFORMATION);
        alert.setTitle("采集历史");
        alert.setHeaderText("📜 " + a.name() + " 的历史");
        javafx.scene.control.TextArea area = new javafx.scene.control.TextArea(sb.toString());
        area.setEditable(false);
        area.setPrefSize(420, 320);
        alert.getDialogPane().setContent(area);
        alert.showAndWait();
    }

    // ==================== 导出 ====================

    private void exportOne(MusicTrackerStore.Account a) {
        Path dir = Settings.load().effectiveExportDir().resolve("音乐账号追踪");
        try {
            Files.createDirectories(dir);
            Path target = dir.resolve(safe(a.name()) + ".png");
            javax.imageio.ImageIO.write(drawCard(List.of(a)), "png", target.toFile());
            status.setText("已导出：" + target);
            toast("卡片已导出");
        } catch (Exception e) {
            status.setText("导出失败：" + e.getMessage());
        }
    }

    private void exportByGroup() {
        if (accounts.isEmpty()) { status.setText("没有账号"); return; }
        Set<String> groups = new LinkedHashSet<>();
        for (MusicTrackerStore.Account a : accounts) {
            groups.add(a.groupName().isEmpty() ? "未分组" : a.groupName());
        }
        Path dir = Settings.load().effectiveExportDir().resolve("音乐账号追踪");
        int ok = 0;
        for (String g : groups) {
            List<MusicTrackerStore.Account> list = accounts.stream()
                    .filter(a -> (a.groupName().isEmpty() ? "未分组" : a.groupName()).equals(g)).toList();
            try {
                Files.createDirectories(dir);
                javax.imageio.ImageIO.write(drawCard(list), "png",
                        dir.resolve("分组_" + safe(g) + ".png").toFile());
                ok++;
            } catch (Exception ignored) {
            }
        }
        status.setText("已按组导出 " + ok + " 张 → " + dir);
        if (ok > 0) {
            toast("按组导出完成：" + ok + " 张");
            AudioEngine.openInExplorer(dir);
        }
    }

    private static String safe(String s) {
        return s.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    /** 卡片拼贴（3 列 320×140）。 */
    public static java.awt.image.BufferedImage drawCard(List<MusicTrackerStore.Account> list) {
        int perRow = 3, cw = 320, ch = 140, gap = 12, pad = 20;
        int rows = Math.max(1, (list.size() + perRow - 1) / perRow);
        int width = pad * 2 + perRow * cw + (perRow - 1) * gap;
        int height = pad * 2 + rows * ch + (rows - 1) * gap;
        var img = new java.awt.image.BufferedImage(width, height, java.awt.image.BufferedImage.TYPE_INT_RGB);
        var g = img.createGraphics();
        g.setRenderingHint(java.awt.RenderingHints.KEY_TEXT_ANTIALIASING,
                java.awt.RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        g.setColor(java.awt.Color.decode("#f2f4f7"));
        g.fillRect(0, 0, width, height);

        for (int i = 0; i < list.size(); i++) {
            var a = list.get(i);
            int x = pad + (i % perRow) * (cw + gap);
            int y = pad + (i / perRow) * (ch + gap);
            g.setColor(java.awt.Color.WHITE);
            g.fillRoundRect(x, y, cw, ch, 12, 12);
            g.setColor(java.awt.Color.decode("#e0e4ea"));
            g.drawRoundRect(x, y, cw, ch, 12, 12);

            g.setColor(java.awt.Color.decode("#1f2937"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, 17));
            g.drawString(a.name(), x + 16, y + 34);

            g.setColor(java.awt.Color.decode("#6b7280"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 12));
            g.drawString(MusicTrackerStore.PLATFORM_NAMES.getOrDefault(a.platform(), a.platform())
                    + (a.groupName().isEmpty() ? "" : " · " + a.groupName()), x + 16, y + 56);

            g.setColor(java.awt.Color.decode("#1976d2"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, 20));
            g.drawString("粉丝 " + TrackerStore.formatCount(a.fans()), x + 16, y + 92);

            g.setColor(java.awt.Color.decode("#9ca3af"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 10));
            g.drawString(a.lastFetch().isEmpty() ? "未采集" : "采集于 " + a.lastFetch(), x + 16, y + 118);
        }
        g.dispose();
        return img;
    }
}
