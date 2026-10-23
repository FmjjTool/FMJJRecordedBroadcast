package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.bili.BiliResource;
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
import java.util.Set;
import java.util.function.Consumer;

/**
 * 📈 网媒账号追踪（对齐 Python 版 tracker.py 的骨架）。
 *
 * <p>顶部：`📈 网媒账号追踪` + `🔄 刷新` + `📷 按组导出` + `⏰ 定时采集` + `⬇ 获取全部` + 平台下拉；
 * 两个 Tab：**追踪账号**（卡片网格）/ **⚙️ 追踪账号设置**（添加账号 + 分组管理 + 已配置列表）。
 *
 * <p>采集：**B站可用**（`x/web-interface/card` 取粉丝/获赞）；其他平台接口未移植，明确提示。
 * 导出：按组生成 PNG 卡片（Graphics2D 绘制）。
 */
public class TrackerPage extends BasePage {

    private static final String[][] PLATFORMS = {
            {"bilibili", "B站"}, {"douyin", "抖音"}, {"weibo", "微博"},
            {"kuaishou", "快手"}, {"xiaohongshu", "小红书"},
    };

    private final FlowPane cardGrid = new FlowPane(10, 10);
    private final ListView<String> accountList = new ListView<>();
    private final TextField nameField = new TextField();
    private final ComboBox<String> platformBox = new ComboBox<>();
    private final TextField urlField = new TextField();
    private final TextField groupIdField = new TextField();
    private final TextField groupNameField = new TextField();
    private final ComboBox<String> batchPlatform = new ComboBox<>();
    private final Label status = new Label("");
    private final Label timerDot = new Label("○");

    private final List<TrackerStore.Account> accounts = new ArrayList<>();
    private final BiliResource biliResource = new BiliResource();
    private javafx.animation.Timeline timer;

    private Consumer<String> toast;

    @Override
    public String title() { return "📈 账号追踪"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("📈 网媒账号追踪");
        pageTitle.setStyle("-fx-font-size: 16px; -fx-font-weight: bold;");
        Button refresh = new Button("🔄 刷新");
        refresh.setOnAction(e -> {
            loadAccounts();
            renderCards();
        });
        Button exportGroup = new Button("📷 按组导出");
        exportGroup.setTooltip(new javafx.scene.control.Tooltip("按分组导出账号卡片 PNG"));
        exportGroup.setOnAction(e -> exportByGroup());
        timerDot.setStyle("-fx-text-fill: #888;");
        Button timerBtn = new Button("⏰ 定时采集");
        timerBtn.setTooltip(new javafx.scene.control.Tooltip("每 30 分钟自动采集一次（仅 B站可用）"));
        timerBtn.setOnAction(e -> toggleTimer());
        Button fetchAll = new Button("⬇ 获取全部");
        fetchAll.setOnAction(e -> fetchAll());
        batchPlatform.getItems().add("全部平台");
        for (String[] p : PLATFORMS) batchPlatform.getItems().add(p[1]);
        batchPlatform.getSelectionModel().selectFirst();
        batchPlatform.setPrefWidth(92);

        HBox head = new HBox(6, pageTitle, refresh, exportGroup, timerDot, timerBtn,
                fetchAll, batchPlatform, spacer(), status);
        head.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        TabPane tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        tabs.getTabs().addAll(buildAccountsTab(), buildSettingsTab());
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

    // ==================== Tab1：账号卡片 ====================

    private Tab buildAccountsTab() {
        cardGrid.setPadding(new Insets(10));
        cardGrid.setPrefWrapLength(900);
        ScrollPane scroll = new ScrollPane(cardGrid);
        scroll.setFitToWidth(true);
        return new Tab("追踪账号", scroll);
    }

    private void renderCards() {
        cardGrid.getChildren().clear();
        if (accounts.isEmpty()) {
            Label empty = new Label("（暂无账号，请在「⚙️ 追踪账号设置」里添加）");
            empty.setStyle("-fx-text-fill: #888;");
            cardGrid.getChildren().add(empty);
            return;
        }
        for (TrackerStore.Account a : accounts) {
            cardGrid.getChildren().add(card(a));
        }
    }

    private Node card(TrackerStore.Account a) {
        Label name = new Label("👤 " + (a.name().isEmpty() ? "未命名" : a.name()));
        name.setStyle("-fx-font-size: 13px; -fx-font-weight: bold;");
        Label plat = new Label(TrackerStore.platformName(a.platform())
                + (a.groupName().isEmpty() ? "" : "　" + a.groupName()));
        plat.setStyle("-fx-font-size: 11px; -fx-text-fill: #6b7280;");
        Label fans = new Label("粉丝 " + TrackerStore.formatCount(a.fans())
                + "　获赞 " + TrackerStore.formatCount(a.likes()));
        fans.setStyle("-fx-font-size: 12px; -fx-text-fill: #1976d2;");
        Label last = new Label(a.lastFetch().isEmpty() ? "未采集" : "采集于 " + a.lastFetch());
        last.setStyle("-fx-font-size: 10px; -fx-text-fill: #9ca3af;");

        Button fetch = new Button("⬇ 采集");
        fetch.setStyle("-fx-font-size: 10px;");
        fetch.setOnAction(e -> fetchOne(a));
        Button export = new Button("📷 导出");
        export.setStyle("-fx-font-size: 10px;");
        export.setOnAction(e -> exportOne(a));
        HBox btns = new HBox(4, fetch, export);

        VBox box = new VBox(4, name, plat, fans, last, btns);
        box.setPadding(new Insets(10));
        box.setPrefWidth(210);
        box.setStyle("-fx-background-color: white; -fx-border-color: #e0e4ea; "
                + "-fx-border-radius: 6; -fx-background-radius: 6;");
        return box;
    }

    // ==================== Tab2：设置 ====================

    private Tab buildSettingsTab() {
        Label addTitle = new Label("添加追踪账号（账号名 + 链接地址）");
        addTitle.setStyle("-fx-font-weight: bold;");

        nameField.setPromptText("账号名");
        nameField.setPrefWidth(140);
        platformBox.getItems().addAll(FXCollections.observableArrayList(
                java.util.Arrays.stream(PLATFORMS).map(p -> p[1]).toList()));
        platformBox.getSelectionModel().selectFirst();
        platformBox.setPrefWidth(100);
        HBox r1 = new HBox(6, new Label("账号名:"), nameField, new Label("平台:"), platformBox);
        r1.setAlignment(Pos.CENTER_LEFT);

        urlField.setPromptText("链接地址（B站可用 https://space.bilibili.com/12345 或纯 UID）");
        groupIdField.setPromptText("组编号");
        groupIdField.setPrefWidth(110);
        groupNameField.setPromptText("组名");
        groupNameField.setPrefWidth(140);
        HBox r2 = new HBox(6, new Label("链接:"), urlField,
                new Label("分组:"), groupIdField, groupNameField);
        r2.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(urlField, Priority.ALWAYS);

        Button add = new Button("＋ 添加账号");
        add.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        add.setOnAction(e -> addAccount());
        Button del = new Button("🗑 删除选中");
        del.setOnAction(e -> deleteSelected());
        HBox r3 = new HBox(6, add, del, spacer());
        r3.setAlignment(Pos.CENTER_LEFT);

        Label listTitle = new Label("已配置账号");
        listTitle.setStyle("-fx-font-weight: bold;");
        accountList.setPlaceholder(new Label("（暂无账号）"));
        VBox.setVgrow(accountList, Priority.ALWAYS);

        Label hint = new Label("采集：B站可用（card 接口取粉丝/获赞）；抖音/微博/快手/小红书接口未移植，"
                + "采集会明确提示。配置存 config/tracker_accounts.json。");
        hint.setWrapText(true);
        hint.setStyle("-fx-font-size: 11px; -fx-text-fill: #8a93a5;");

        VBox box = new VBox(8, addTitle, r1, r2, r3, listTitle, accountList, hint);
        box.setPadding(new Insets(10));
        VBox.setVgrow(accountList, Priority.ALWAYS);
        return new Tab("⚙️ 追踪账号设置", box);
    }

    // ==================== 数据 ====================

    private void loadAccounts() {
        accounts.clear();
        accounts.addAll(TrackerStore.load());
        List<String> names = new ArrayList<>();
        for (TrackerStore.Account a : accounts) {
            names.add(a.name() + "　" + TrackerStore.platformName(a.platform())
                    + (a.groupName().isEmpty() ? "" : "　" + a.groupName()));
        }
        accountList.setItems(FXCollections.observableArrayList(names));
        status.setText("共 " + accounts.size() + " 个账号");
    }

    private void addAccount() {
        String name = nameField.getText() == null ? "" : nameField.getText().trim();
        String url = urlField.getText() == null ? "" : urlField.getText().trim();
        if (name.isEmpty()) { status.setText("请填写账号名"); return; }
        if (url.isEmpty()) { status.setText("请填写链接地址"); return; }
        int idx = platformBox.getSelectionModel().getSelectedIndex();
        String platform = idx >= 0 ? PLATFORMS[idx][0] : "bilibili";
        accounts.add(new TrackerStore.Account(name, platform, url,
                groupIdField.getText() == null ? "" : groupIdField.getText().trim(),
                groupNameField.getText() == null ? "" : groupNameField.getText().trim(),
                0, 0, ""));
        TrackerStore.save(accounts);
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
        TrackerStore.save(accounts);
        loadAccounts();
        renderCards();
        status.setText("已删除：" + name);
    }

    // ==================== 采集 ====================

    private void fetchOne(TrackerStore.Account a) {
        if (!"bilibili".equals(a.platform())) {
            status.setText(TrackerStore.platformName(a.platform()) + " 采集接口未移植（当前仅 B站）");
            return;
        }
        String uid = TrackerStore.biliUid(a.url());
        if (uid.isEmpty()) {
            status.setText("无法从链接解析 UID：" + a.url());
            return;
        }
        status.setText("采集 " + a.name() + " …");
        javafx.concurrent.Task<BiliResource.SpaceCard> task = new javafx.concurrent.Task<>() {
            @Override
            protected BiliResource.SpaceCard call() throws Exception {
                return biliResource.spaceCard(uid, CookieStore.get("bilibili"));
            }
        };
        task.setOnSucceeded(e -> {
            var c = task.getValue();
            String now = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"));
            for (int i = 0; i < accounts.size(); i++) {
                if (accounts.get(i).name().equals(a.name())) {
                    accounts.set(i, accounts.get(i).withStats(c.fans(), c.likes(), now));
                    break;
                }
            }
            TrackerStore.save(accounts);
            loadAccounts();
            renderCards();
            status.setText("✅ " + a.name() + "：" + TrackerStore.formatCount(c.fans()) + " 粉丝");
        });
        task.setOnFailed(e -> status.setText("采集失败：" + task.getException().getMessage()));
        new Thread(task, "tracker-fetch").start();
    }

    private void fetchAll() {
        String plat = batchPlatform.getValue();
        String key = null;
        if (plat != null && !"全部平台".equals(plat)) {
            for (String[] p : PLATFORMS) if (p[1].equals(plat)) key = p[0];
        }
        final String filter = key;
        List<TrackerStore.Account> targets = accounts.stream()
                .filter(a -> filter == null || filter.equals(a.platform())).toList();
        if (targets.isEmpty()) { status.setText("没有可采集的账号"); return; }
        int biliCount = (int) targets.stream().filter(a -> "bilibili".equals(a.platform())).count();
        status.setText("开始采集 " + targets.size() + " 个（B站 " + biliCount + " 个可采）");
        for (TrackerStore.Account a : targets) fetchOne(a);
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

    // ==================== 导出 ====================

    private void exportOne(TrackerStore.Account a) {
        Path dir = Settings.load().effectiveExportDir().resolve("账号追踪");
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
        for (TrackerStore.Account a : accounts) {
            groups.add(a.groupName().isEmpty() ? "未分组" : a.groupName());
        }
        Path dir = Settings.load().effectiveExportDir().resolve("账号追踪");
        int ok = 0;
        for (String g : groups) {
            List<TrackerStore.Account> list = accounts.stream()
                    .filter(a -> (a.groupName().isEmpty() ? "未分组" : a.groupName()).equals(g)).toList();
            try {
                Files.createDirectories(dir);
                Path target = dir.resolve("分组_" + safe(g) + ".png");
                javax.imageio.ImageIO.write(drawCard(list), "png", target.toFile());
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

    /** 绘制卡片拼贴图（每行 3 张，卡片 320×140）。 */
    public static java.awt.image.BufferedImage drawCard(List<TrackerStore.Account> list) {
        int perRow = 3;
        int cw = 320, ch = 140, gap = 12, pad = 20;
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
            g.drawString(TrackerStore.platformName(a.platform())
                    + (a.groupName().isEmpty() ? "" : " · " + a.groupName()), x + 16, y + 56);

            g.setColor(java.awt.Color.decode("#1976d2"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, 20));
            g.drawString("粉丝 " + TrackerStore.formatCount(a.fans()), x + 16, y + 90);
            g.setColor(java.awt.Color.decode("#6b7280"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 12));
            g.drawString("获赞 " + TrackerStore.formatCount(a.likes()), x + 180, y + 90);

            g.setColor(java.awt.Color.decode("#9ca3af"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 10));
            g.drawString(a.lastFetch().isEmpty() ? "未采集" : "采集于 " + a.lastFetch(), x + 16, y + 118);
        }
        g.dispose();
        return img;
    }
}
