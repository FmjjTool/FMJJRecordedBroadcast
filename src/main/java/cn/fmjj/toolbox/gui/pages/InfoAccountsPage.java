package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.accounts.AccountFeed;
import cn.fmjj.toolbox.engine.accounts.AccountStore;
import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.application.Platform;
import javafx.beans.property.ReadOnlyStringWrapper;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.ProgressBar;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TableCell;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.Tooltip;
import javafx.scene.control.cell.CheckBoxTableCell;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.util.Duration;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 账号获取页。移植自 Python 版 gui_qt/pages/info_accounts.py：
 * 账号/分组管理、批量抓取账号最新内容、日期段过滤、增量入库、定时获取。
 *
 * <p>时间线平台：B站 / 微博 / 抖音 / 快手 / 小红书（快手/小红书主页需要登录 Cookie）。
 */
public class InfoAccountsPage extends BasePage {

    private static final DateTimeFormatter LOG_TS = DateTimeFormatter.ofPattern("HH:mm:ss");

    private static final class AccountRow {
        final AccountStore.Account acc;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);

        AccountRow(AccountStore.Account acc) {
            this.acc = acc;
        }
    }

    private record AccountResult(AccountStore.Account acc, List<AccountFeed.Post> posts,
                                 String error, boolean cancelled) {}

    private final AccountFeed feed = new AccountFeed();

    private final ComboBox<String> modeBox = new ComboBox<>();
    private final ComboBox<String> groupBox = new ComboBox<>();
    private final TextField countField = new TextField("20");
    private final CheckBox incrementalBox = new CheckBox("增量模式");
    private final TextField dateStart = new TextField();
    private final TextField dateEnd = new TextField();
    private final ComboBox<String> timerBox = new ComboBox<>();
    private final Button fetchBtn = new Button("🚀 获取最新");
    private final Button cancelBtn = new Button("✕ 取消");
    private final ToggleButton pauseBtn = new ToggleButton("⏸ 暂停");
    private final ProgressBar progress = new ProgressBar(0);
    private final Label progressLabel = new Label("");
    private final Label statusLabel = new Label("");
    private final TableView<AccountRow> accountTable = new TableView<>();
    private final TextArea logArea = new TextArea();

    private final TextField addUrl = new TextField();
    private final TextField addName = new TextField();
    private final TextField addGroups = new TextField();
    private final ComboBox<String> addKind = new ComboBox<>();
    private final TableView<AccountStore.Account> settingsTable = new TableView<>();
    private final TextField editUrl = new TextField();
    private final TextField editName = new TextField();
    private final TextField editGroups = new TextField();
    private final ComboBox<String> editKind = new ComboBox<>();
    private final TextField groupIdField = new TextField();
    private final TextField groupNameField = new TextField();
    private final ListView<String> groupList = new ListView<>();

    private volatile boolean busy;
    private volatile boolean cancelling;
    private volatile boolean paused;
    private Timeline autoTimer;
    private Consumer<String> toastSink;

    @Override
    public String title() {
        return "👥 账号获取";
    }

    @Override
    public void bindToast(Consumer<String> sink) {
        this.toastSink = sink;
    }

    @Override
    public void shutdown() {
        cancelling = true;
        if (autoTimer != null) {
            autoTimer.stop();
            autoTimer = null;
        }
    }

    private void toast(String msg) {
        if (toastSink != null) toastSink.accept(msg);
        else statusLabel.setText(msg);
    }

    // ==================== 构建 ====================

    @Override
    protected Node build() {
        TabPane tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        tabs.getTabs().addAll(new Tab("功能", buildFetchTab()), new Tab("设置", buildSettingsTab()));
        VBox root = new VBox(tabs);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(8));
        VBox.setVgrow(tabs, Priority.ALWAYS);
        return root;
    }

    private Node buildFetchTab() {
        Label title = new Label(title());
        title.setStyle("-fx-font-size:18px; -fx-font-weight:bold;");
        Button help = new Button("❓ 账号获取 · 功能介绍");
        help.setOnAction(e -> showHelp());
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox titleRow = new HBox(8, title, spacer, help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        modeBox.getItems().addAll("全部账号", "选中账号", "按组采集");
        modeBox.getSelectionModel().selectFirst();
        modeBox.setOnAction(e -> refreshGroupBox());
        groupBox.setPrefWidth(130);
        groupBox.setDisable(true);
        modeBox.valueProperty().addListener((o, a, b) -> groupBox.setDisable(!"按组采集".equals(b)));
        countField.setPrefWidth(60);
        countField.setTooltip(new Tooltip("每个账号抓取条数；0 = 不限制（翻到底）"));
        incrementalBox.setSelected(true);
        incrementalBox.setTooltip(new Tooltip("增量模式：追加到 <导出目录>/已获取数据/账号增量/<平台>.jsonl，"
                + "按作品 ID 去重；关闭则每个作品导出独立 JSON 文件"));
        dateStart.setPromptText("开始日期 yyyy-MM-dd");
        dateEnd.setPromptText("结束日期 yyyy-MM-dd");
        dateStart.setPrefWidth(130);
        dateEnd.setPrefWidth(130);

        HBox row1 = new HBox(8, new Label("采集模式"), modeBox, groupBox,
                new Label("条数"), countField, incrementalBox,
                new Label("日期段"), dateStart, new Label("~"), dateEnd);
        row1.setAlignment(Pos.CENTER_LEFT);

        fetchBtn.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white; -fx-background-radius:4;");
        fetchBtn.setOnAction(e -> fetch());
        cancelBtn.setDisable(true);
        cancelBtn.setOnAction(e -> {
            cancelling = true;
            log("⊘ 收到取消请求：未开始的账号将跳过，在飞账号自然收尾");
        });
        pauseBtn.setDisable(true);
        pauseBtn.setOnAction(e -> {
            paused = pauseBtn.isSelected();
            pauseBtn.setText(paused ? "▶ 继续" : "⏸ 暂停");
            log(paused ? "⏸ 已暂停（账号边界生效）" : "▶ 已继续");
        });
        timerBox.getItems().addAll("不定时", "30分钟", "1小时", "2小时");
        timerBox.getSelectionModel().selectFirst();
        timerBox.setOnAction(e -> applyTimer());
        HBox row2 = new HBox(8, fetchBtn, cancelBtn, pauseBtn,
                new Label("定时获取"), timerBox, progress, progressLabel);
        row2.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(progress, Priority.ALWAYS);
        progress.setMaxWidth(Double.MAX_VALUE);

        buildAccountTable();
        VBox.setVgrow(accountTable, Priority.ALWAYS);
        accountTable.setPrefHeight(260);

        logArea.setEditable(false);
        logArea.setPrefHeight(190);
        logArea.setPromptText("采集日志");
        statusLabel.setStyle("-fx-text-fill:#888;");

        VBox box = new VBox(8, titleRow, row1, row2, accountTable,
                new Label("采集日志"), logArea, statusLabel);
        box.setPadding(new Insets(12));
        reloadAccounts();
        return box;
    }

    private void buildAccountTable() {
        accountTable.setPlaceholder(new Label("暂无账号，先到「设置」页添加"));
        accountTable.setEditable(true);
        accountTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        TableColumn<AccountRow, Boolean> check = new TableColumn<>("选");
        check.setPrefWidth(40);
        check.setEditable(true);
        check.setSortable(false);
        check.setCellValueFactory(d -> d.getValue().checked);
        check.setCellFactory(CheckBoxTableCell.forTableColumn(check));
        accountTable.getColumns().add(check);
        addTextColumn(accountTable, "账号名", 140, r -> r.acc.name.isEmpty() ? "-" : r.acc.name);
        addTextColumn(accountTable, "平台", 90, r -> platformName(r.acc.url));
        addTextColumn(accountTable, "分组", 100, r -> String.join(",", r.acc.groupIds));
        addTextColumn(accountTable, "采集类型", 80, r -> kindName(r.acc.kind));
        addTextColumn(accountTable, "链接", 360, r -> r.acc.url);
    }

    private Node buildSettingsTab() {
        Label title = new Label("账号与分组设置");
        title.setStyle("-fx-font-size:16px; -fx-font-weight:bold;");

        addUrl.setPromptText("主页链接（B站空间 / 微博主页 / 抖音 sec_uid 主页）");
        addName.setPromptText("账号名（可选）");
        addGroups.setPromptText("分组（如 11,22，可空）");
        addKind.getItems().addAll("自动", "只视频", "只动态", "全部");
        addKind.getSelectionModel().selectFirst();
        Button addBtn = new Button("➕ 添加账号");
        addBtn.setOnAction(e -> addAccount());
        HBox addRow = new HBox(8, addUrl, addName, addGroups, addKind, addBtn);
        addRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(addUrl, Priority.ALWAYS);

        buildSettingsTable();

        editUrl.setPromptText("主页链接");
        editName.setPromptText("账号名");
        editGroups.setPromptText("分组（11,22）");
        editKind.getItems().addAll("自动", "只视频", "只动态", "全部");
        editKind.getSelectionModel().selectFirst();
        Button saveBtn = new Button("💾 保存修改");
        saveBtn.setOnAction(e -> saveAccountEdit());
        Button delBtn = new Button("🗑 删除");
        delBtn.setOnAction(e -> deleteAccount());
        Button upBtn = new Button("↑ 上移");
        upBtn.setOnAction(e -> moveAccount(-1));
        Button downBtn = new Button("↓ 下移");
        downBtn.setOnAction(e -> moveAccount(1));
        HBox editRow = new HBox(8, editName, editUrl, editGroups, editKind,
                saveBtn, delBtn, upBtn, downBtn);
        editRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(editUrl, Priority.ALWAYS);

        groupIdField.setPromptText("组号（11/22/33…）");
        groupIdField.setPrefWidth(140);
        groupNameField.setPromptText("组名");
        Button groupAdd = new Button("➕ 添加分组");
        groupAdd.setOnAction(e -> addGroup());
        Button groupDel = new Button("🗑 删除");
        groupDel.setOnAction(e -> deleteGroup());
        Button groupUp = new Button("↑");
        groupUp.setOnAction(e -> moveGroup(-1));
        Button groupDown = new Button("↓");
        groupDown.setOnAction(e -> moveGroup(1));
        HBox groupEdit = new HBox(8, groupIdField, groupNameField, groupAdd, groupDel, groupUp, groupDown);
        groupEdit.setAlignment(Pos.CENTER_LEFT);
        groupList.setPrefHeight(160);

        VBox box = new VBox(8, title,
                new Label("添加账号"), addRow,
                new Label("已配置账号（选中后编辑）"), settingsTable,
                new Label("编辑选中账号"), editRow,
                new Label("分组管理"), groupEdit, groupList);
        box.setPadding(new Insets(12));
        reloadSettings();
        return box;
    }

    private void buildSettingsTable() {
        settingsTable.setPlaceholder(new Label("暂无账号"));
        settingsTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        settingsTable.setPrefHeight(220);
        addTextColumn(settingsTable, "账号名", 140, a -> a.name.isEmpty() ? "-" : a.name);
        addTextColumn(settingsTable, "平台", 90, a -> platformName(a.url));
        addTextColumn(settingsTable, "分组", 100, a -> String.join(",", a.groupIds));
        addTextColumn(settingsTable, "类型", 80, a -> kindName(a.kind));
        addTextColumn(settingsTable, "链接", 380, a -> a.url);
        settingsTable.getSelectionModel().selectedItemProperty().addListener((o, old, a) -> {
            if (a == null) return;
            editUrl.setText(a.url);
            editName.setText(a.name);
            editGroups.setText(String.join(",", a.groupIds));
            editKind.getSelectionModel().select(kindLabelIndex(a.kind));
        });
    }

    private <T> void addTextColumn(TableView<T> table, String name, double width,
                                   java.util.function.Function<T, String> value) {
        TableColumn<T, String> col = new TableColumn<>(name);
        col.setPrefWidth(width);
        col.setCellValueFactory(d -> new ReadOnlyStringWrapper(value.apply(d.getValue())));
        col.setCellFactory(c -> new TableCell<>() {
            @Override
            protected void updateItem(String item, boolean empty) {
                super.updateItem(item, empty);
                setText(empty ? null : item);
                setTooltip(empty || item == null ? null : new Tooltip(item));
            }
        });
        table.getColumns().add(col);
    }

    private void showHelp() {
        Alert alert = new Alert(Alert.AlertType.INFORMATION);
        alert.setTitle("账号获取 · 功能介绍");
        alert.setHeaderText("账号获取");
        alert.setContentText("""
                · 添加账号：主页 URL + 账号名 + 组号（可逗号分隔绑多组）
                · 获取最新：勾选账号后批量抓取，可暂停 / 取消；抓取结果自动保存到
                  <导出目录>/已获取数据/（增量模式写入 账号增量/<平台>.jsonl，按作品 ID 去重；
                  非增量模式每个作品一个 JSON 文件）
                · 采集模式：全部账号 / 选中账号 / 按组采集；条数 0 = 不限制
                · 日期段过滤：只保留发布时间在范围内的条目（时间解析失败的条目保留）
                · 定时获取：按 30 分钟 / 1 小时 / 2 小时间隔自动执行「全部/选中/按组」采集
                · 时间线平台：B站（投稿+动态）/ 微博 / 抖音 / 快手 / 小红书
                · 抖音请粘贴含 sec_uid 的主页链接（分享主页得到），`/user/抖音号` 不可用
                · 快手、小红书主页需要登录 Cookie；B站/微博游客优先
                """);
        alert.getDialogPane().setPrefWidth(640);
        alert.showAndWait();
    }

    // ==================== 账号列表 / 分组 ====================

    private void reloadAccounts() {
        List<AccountRow> rows = new ArrayList<>();
        for (AccountStore.Account a : AccountStore.loadAccounts()) rows.add(new AccountRow(a));
        accountTable.setItems(FXCollections.observableArrayList(rows));
        refreshGroupBox();
    }

    private void reloadSettings() {
        settingsTable.setItems(FXCollections.observableArrayList(AccountStore.loadAccounts()));
        Map<String, String> groups = AccountStore.getGroups();
        List<String> items = new ArrayList<>();
        groups.forEach((id, name) -> items.add(id + ":" + name));
        groupList.setItems(FXCollections.observableArrayList(items));
        refreshGroupBox();
    }

    private void refreshGroupBox() {
        Map<String, String> groups = AccountStore.getGroups();
        String selected = groupBox.getSelectionModel().getSelectedItem();
        List<String> items = new ArrayList<>();
        groups.forEach((id, name) -> items.add(id + ":" + name));
        groupBox.setItems(FXCollections.observableArrayList(items));
        if (selected != null && items.contains(selected)) groupBox.getSelectionModel().select(selected);
        else if (!items.isEmpty()) groupBox.getSelectionModel().selectFirst();
    }

    private void addAccount() {
        String kind = kindValue(addKind.getSelectionModel().getSelectedItem());
        if (!AccountStore.addAccount(addUrl.getText(), addName.getText(), addGroups.getText(),
                "", "", kind)) {
            toast("添加失败：链接为空或已存在");
            return;
        }
        addUrl.clear();
        addName.clear();
        addGroups.clear();
        reloadAccounts();
        reloadSettings();
        log("✅ 已添加账号");
    }

    private void saveAccountEdit() {
        AccountStore.Account selected = settingsTable.getSelectionModel().getSelectedItem();
        if (selected == null) {
            toast("请先在列表中选择要编辑的账号");
            return;
        }
        String kind = kindValue(editKind.getSelectionModel().getSelectedItem());
        if (!AccountStore.updateAccount(selected.url, editName.getText(), editGroups.getText(),
                "", "", kind, editUrl.getText())) {
            toast("保存失败：新链接与已有账号重复");
            return;
        }
        reloadAccounts();
        reloadSettings();
        log("✅ 账号已更新");
    }

    private void deleteAccount() {
        AccountStore.Account selected = settingsTable.getSelectionModel().getSelectedItem();
        if (selected == null) {
            toast("请先选择要删除的账号");
            return;
        }
        if (AccountStore.removeAccount(selected.url)) {
            reloadAccounts();
            reloadSettings();
            log("🗑 已删除账号");
        }
    }

    private void moveAccount(int delta) {
        AccountStore.Account selected = settingsTable.getSelectionModel().getSelectedItem();
        if (selected == null) return;
        if (AccountStore.moveAccount(selected.url, delta)) {
            reloadAccounts();
            reloadSettings();
        }
    }

    private void addGroup() {
        if (!AccountStore.saveGroup(groupIdField.getText(), groupNameField.getText())) {
            toast("分组添加失败：组号/组名为空");
            return;
        }
        groupIdField.clear();
        groupNameField.clear();
        reloadSettings();
    }

    private void deleteGroup() {
        String selected = groupList.getSelectionModel().getSelectedItem();
        if (selected == null) return;
        String gid = selected.split(":", 2)[0];
        AccountStore.deleteGroup(gid);
        reloadSettings();
        reloadAccounts();
    }

    private void moveGroup(int delta) {
        String selected = groupList.getSelectionModel().getSelectedItem();
        if (selected == null) return;
        AccountStore.moveGroup(selected.split(":", 2)[0], delta);
        reloadSettings();
    }

    // ==================== 采集 ====================

    private void fetch() {
        if (busy) return;
        List<AccountStore.Account> accounts = resolveAccounts();
        if (accounts.isEmpty()) {
            toast("没有可采集的账号");
            return;
        }
        int count;
        try {
            count = Integer.parseInt(countField.getText().strip());
        } catch (Exception e) {
            count = 20;
        }
        final int limit = count <= 0 ? 1_000_000 : count;
        final boolean incremental = incrementalBox.isSelected();
        final String start = dateStart.getText().strip();
        final String end = dateEnd.getText().strip();
        cancelling = false;
        paused = false;
        pauseBtn.setSelected(false);
        pauseBtn.setText("⏸ 暂停");
        setBusy(true, "⏳ 准备采集 " + accounts.size() + " 个账号...");
        progress.setProgress(0);
        progressLabel.setText("0/" + accounts.size());
        log("═══ 开始采集 " + accounts.size() + " 个账号（条数 "
                + (limit >= 1_000_000 ? "无上限" : limit) + "，"
                + (incremental ? "增量模式" : "独立文件模式") + "）═══");
        Thread worker = new Thread(() -> runFetch(accounts, limit, incremental, start, end),
                "accounts-fetch");
        worker.setDaemon(true);
        worker.start();
    }

    private List<AccountStore.Account> resolveAccounts() {
        String mode = modeBox.getSelectionModel().getSelectedItem();
        List<AccountStore.Account> accounts;
        if ("选中账号".equals(mode)) {
            accounts = new ArrayList<>();
            for (AccountRow r : accountTable.getItems()) {
                if (r.checked.get()) accounts.add(r.acc);
            }
        } else if ("按组采集".equals(mode)) {
            String g = groupBox.getSelectionModel().getSelectedItem();
            if (g == null) return List.of();
            accounts = AccountStore.accountsByGroup(g.split(":", 2)[0]);
        } else {
            accounts = AccountStore.loadAccounts();
        }
        accounts.removeIf(a -> a.url == null || a.url.isBlank());
        return accounts;
    }

    private void runFetch(List<AccountStore.Account> accounts, int limit, boolean incremental,
                          String start, String end) {
        int total = accounts.size();
        ExecutorService pool = Executors.newFixedThreadPool(Math.min(4, Math.max(1, total)));
        CompletionService<AccountResult> cs = new ExecutorCompletionService<>(pool);
        for (AccountStore.Account a : accounts) {
            cs.submit(() -> {
                if (cancelling) return new AccountResult(a, List.of(), "", true);
                waitWhilePaused();
                if (cancelling) return new AccountResult(a, List.of(), "", true);
                String platform = AccountFeed.detectPlatform(a.url);
                String name = a.name.isEmpty() ? trunc(a.url, 36) : a.name;
                try {
                    long started = System.currentTimeMillis();
                    List<AccountFeed.Post> posts = feed.fetchUserPosts(
                            a.url, limit, a.kind, msg -> log("    [" + trunc(name, 16) + "] " + msg));
                    List<AccountFeed.Post> filtered = AccountFeed.filterByDate(posts, start, end);
                    if (filtered.size() != posts.size()) {
                        log("    [" + trunc(name, 16) + "] 🗓 日期段过滤: "
                                + posts.size() + " → " + filtered.size() + " 条");
                    }
                    log("    [" + trunc(name, 16) + "] ✅ " + platformName(platform)
                            + " 获取到 " + filtered.size() + " 条（"
                            + (System.currentTimeMillis() - started) + "ms）");
                    return new AccountResult(a, filtered, "", false);
                } catch (Exception e) {
                    return new AccountResult(a, List.of(), e.getMessage() == null
                            ? e.toString() : e.getMessage(), false);
                }
            });
        }
        pool.shutdown();
        Map<String, Set<String>> seenCache = new HashMap<>();
        int done = 0;
        int totalPosts = 0;
        int failed = 0;
        for (int i = 0; i < total; i++) {
            AccountResult r;
            try {
                r = cs.take().get();
            } catch (Exception e) {
                continue;
            }
            done++;
            String name = r.acc().name.isEmpty() ? trunc(r.acc().url, 20) : r.acc().name;
            if (r.cancelled()) {
                log("  ⊘ 已取消，跳过：" + name);
            } else if (!r.error().isEmpty()) {
                failed++;
                log("  ❌ " + name + " 失败：" + trunc(r.error(), 120));
            } else {
                int saved = savePosts(r.acc(), r.posts(), incremental, seenCache);
                totalPosts += r.posts().size();
                if (!incremental) log("  💾 " + name + " 已导出 " + saved + " 个文件");
            }
            int d = done;
            Platform.runLater(() -> {
                progress.setProgress(d / (double) Math.max(total, 1));
                progressLabel.setText(d + "/" + total);
            });
        }
        int finalTotal = totalPosts;
        int finalFailed = failed;
        Platform.runLater(() -> {
            setBusy(false, "");
            String summary = "✅ 采集完成：共 " + finalTotal + " 条内容，失败 "
                    + finalFailed + " 个账号";
            statusLabel.setText(summary);
            log("═══ " + summary + " ═══");
        });
    }

    private void waitWhilePaused() {
        while (paused && !cancelling) {
            sleep(200);
        }
    }

    private int savePosts(AccountStore.Account acc, List<AccountFeed.Post> posts,
                          boolean incremental, Map<String, Set<String>> seenCache) {
        if (posts.isEmpty()) return 0;
        Path root = Settings.load().effectiveExportDir().resolve("已获取数据");
        int saved = 0;
        if (incremental) {
            Path dir = root.resolve("账号增量");
            try {
                Files.createDirectories(dir);
            } catch (Exception ignored) {
            }
            Map<String, List<AccountFeed.Post>> byPlatform = new LinkedHashMap<>();
            for (AccountFeed.Post p : posts) {
                byPlatform.computeIfAbsent(p.platform.isEmpty() ? "unknown" : p.platform,
                        k -> new ArrayList<>()).add(p);
            }
            for (Map.Entry<String, List<AccountFeed.Post>> e : byPlatform.entrySet()) {
                Path file = dir.resolve(e.getKey() + ".jsonl");
                Set<String> seen = seenCache.computeIfAbsent(e.getKey(), k -> loadSeenIds(file));
                StringBuilder sb = new StringBuilder();
                for (AccountFeed.Post p : e.getValue()) {
                    if (p.videoId == null || p.videoId.isEmpty()) continue;
                    if (!seen.add(p.videoId)) continue;
                    try {
                        sb.append(new com.fasterxml.jackson.databind.ObjectMapper()
                                .writeValueAsString(p.toDict())).append('\n');
                        saved++;
                    } catch (Exception ignored) {
                    }
                }
                if (sb.length() > 0) {
                    try {
                        Files.writeString(file, sb.toString(), StandardCharsets.UTF_8,
                                StandardOpenOption.CREATE, StandardOpenOption.APPEND);
                    } catch (Exception ex) {
                        log("  ⚠️ 写入增量文件失败：" + trunc(ex.getMessage(), 60));
                    }
                }
            }
            log("  💾 " + (acc.name.isEmpty() ? trunc(acc.url, 20) : acc.name)
                    + " 增量入库 " + saved + " 条");
        } else {
            for (AccountFeed.Post p : posts) {
                if (p.videoId == null || p.videoId.isEmpty()) continue;
                Path dir = root.resolve("账号内容").resolve(
                        p.platform.isEmpty() ? "unknown" : p.platform);
                try {
                    Files.createDirectories(dir);
                    Path file = dir.resolve(p.videoId + ".json");
                    if (Files.exists(file)) continue;
                    Files.writeString(file, new com.fasterxml.jackson.databind.ObjectMapper()
                            .writerWithDefaultPrettyPrinter().writeValueAsString(p.toDict()),
                            StandardCharsets.UTF_8);
                    saved++;
                } catch (Exception ignored) {
                }
            }
        }
        return saved;
    }

    private Set<String> loadSeenIds(Path file) {
        Set<String> seen = new LinkedHashSet<>();
        if (!Files.isRegularFile(file)) return seen;
        try {
            var mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                if (line.isBlank()) continue;
                try {
                    String id = mapper.readTree(line).path("视频ID").asText("");
                    if (!id.isEmpty()) seen.add(id);
                } catch (Exception ignored) {
                }
            }
        } catch (Exception ignored) {
        }
        return seen;
    }

    // ==================== 定时 ====================

    private void applyTimer() {
        if (autoTimer != null) {
            autoTimer.stop();
            autoTimer = null;
        }
        String v = timerBox.getSelectionModel().getSelectedItem();
        if (v == null || "不定时".equals(v)) return;
        double minutes = switch (v) {
            case "30分钟" -> 30;
            case "1小时" -> 60;
            default -> 120;
        };
        autoTimer = new Timeline(new KeyFrame(Duration.minutes(minutes), e -> {
            if (!busy) fetch();
        }));
        autoTimer.setCycleCount(Timeline.INDEFINITE);
        autoTimer.play();
        log("⏱ 已启用定时获取：每 " + v);
    }

    // ==================== 状态 ====================

    private void setBusy(boolean value, String text) {
        busy = value;
        fetchBtn.setDisable(value);
        cancelBtn.setDisable(!value);
        pauseBtn.setDisable(!value);
        if (text != null && !text.isEmpty()) statusLabel.setText(text);
    }

    private void log(String line) {
        String msg = LocalDateTime.now().format(LOG_TS) + "  " + line;
        Platform.runLater(() -> {
            logArea.appendText(msg + "\n");
            logArea.setScrollTop(Double.MAX_VALUE);
        });
    }

    private static String platformName(String url) {
        String p = AccountFeed.detectPlatform(url);
        return p.isEmpty() ? "未知" : AccountFeed.platformCn(p);
    }

    private static String kindName(String kind) {
        return switch (kind == null ? "" : kind) {
            case "video" -> "只视频";
            case "dynamic" -> "只动态";
            case "all" -> "全部";
            default -> "自动";
        };
    }

    private static int kindLabelIndex(String kind) {
        return switch (kind == null ? "" : kind) {
            case "video" -> 1;
            case "dynamic" -> 2;
            case "all" -> 3;
            default -> 0;
        };
    }

    private static String kindValue(String label) {
        if (label == null) return "";
        return switch (label) {
            case "只视频" -> "video";
            case "只动态" -> "dynamic";
            case "全部" -> "all";
            default -> "";
        };
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }
}
