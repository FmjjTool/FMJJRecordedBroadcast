package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.platform.douyin.DouyinIndex;
import com.fasterxml.jackson.databind.JsonNode;
import javafx.application.Platform;
import javafx.beans.property.ReadOnlyStringWrapper;
import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.ButtonBar;
import javafx.scene.control.ButtonType;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Dialog;
import javafx.scene.control.Label;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TableCell;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableRow;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.control.Tooltip;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 抖音指数页。移植自 Python 版 gui_qt/pages/douyin_index.py：
 * 搜索达人 → 全量抓取（作者/作品/粉丝分析）→ 本地 JSON 快照。
 */
public class DouyinIndexPage extends BasePage {

    private record CoreRow(String label, String seg, String prefix) {}
    private record AvgRow(String label, String seg, String prefix) {}
    private record Window(String suffix, String label, String pctLabel) {}
    private record RangeItem(int days, String label) {
        @Override public String toString() { return label; }
    }

    private static final List<CoreRow> CORE_ROWS = List.of(
            new CoreRow("新增点赞量", "like_info", "new_like_count_by_"),
            new CoreRow("净增粉丝量", "fans_info", "new_fans_count_by_"),
            new CoreRow("新增分享量", "share_info", "new_share_count_by_"),
            new CoreRow("新增评论量", "comment_info", "new_comment_count_by_"),
            new CoreRow("新增作品量", "item_info", "new_item_count_by_"));

    private static final List<Window> WINDOWS = List.of(
            new Window("day", "昨日", "日环比"),
            new Window("week", "近7天", "环比前7天"),
            new Window("half_month", "近15天", "环比前15天"),
            new Window("month", "近30天", "环比前30天"));

    private static final List<AvgRow> AVG_ROWS = List.of(
            new AvgRow("稿均点赞量", "like_info", "like_average_count_by_"),
            new AvgRow("稿均涨粉量", "fans_info", "fans_average_count_by_"),
            new AvgRow("稿均分享量", "share_info", "share_average_count_by_"),
            new AvgRow("稿均评论量", "comment_info", "comment_average_count_by_"));

    private static final List<String> FANS_ORDER = List.of(
            "省份分布", "城市级别", "城市分布", "年龄分布", "性别分布",
            "手机品牌", "手机价格", "兴趣标签");

    private final DouyinIndex engine = new DouyinIndex();
    private Consumer<String> toast;

    private final TextField input = new TextField();
    private final Button searchBtn = new Button("🔍 搜索");
    private final Button fetchBtn = new Button("📊 获取全部数据并保存");
    private final ComboBox<RangeItem> rangeCombo = new ComboBox<>();
    private final Label accountLabel = new Label("当前账号：未选择（先搜索并确认达人）");
    private final Label statusLabel = new Label("输入达人名称或抖音号 → 🔍 搜索 → 确认 → 获取全部数据并保存");
    private final Label trendLabel = new Label("每日趋势");

    private final TableView<List<String>> coreTable;
    private final TableView<List<String>> avgTable;
    private final TableView<List<String>> trendTable;
    private final TableView<List<String>> videoTable;
    private final TableView<List<String>> fansTable;
    private final TableView<List<String>> similarTable;

    private String uid = "";
    private String name = "";
    private JsonNode data;
    private boolean busy;
    private List<JsonNode> videoRows = List.of();
    private List<JsonNode> similarRows = List.of();

    public DouyinIndexPage() {
        coreTable = newTable(
                new String[]{"指标", "昨日", "日环比", "近7天", "环比前7天",
                        "近15天", "环比前15天", "近30天", "环比前30天"},
                new double[]{100, 90, 90, 90, 95, 90, 95, 90, 95});
        avgTable = newTable(
                new String[]{"指标", "近7天稿均", "环比前7天", "近30天稿均", "环比前30天"},
                new double[]{100, 100, 95, 100, 100});
        trendTable = newTable(
                new String[]{"日期", "新增点赞", "净增粉丝", "新增分享", "新增评论", "新增作品"},
                new double[]{90, 95, 95, 95, 95, 95});
        videoTable = newTable(
                new String[]{"发布日期", "内容", "点赞量", "评论量", "分享量", "收藏量", "视频指数"},
                new double[]{88, 380, 85, 85, 85, 85, 85});
        fansTable = newTable(
                new String[]{"维度", "项目", "占比", "TGI"},
                new double[]{80, 180, 100, 80});
        similarTable = newTable(
                new String[]{"排名", "达人", "抖音号", "粉丝", "总获赞", "作品", "标签"},
                new double[]{50, 170, 130, 90, 90, 70, 170});
    }

    @Override
    public String title() {
        return "📈 抖音指数";
    }

    @Override
    public void bindToast(Consumer<String> sink) {
        this.toast = sink;
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    // ==================== 构建 ====================

    @Override
    protected Node build() {
        Label title = new Label(title());
        title.setStyle("-fx-font-size:18px; -fx-font-weight:bold;");
        Button help = new Button("❓ 抖音指数 · 功能介绍");
        help.setOnAction(e -> showHelp());
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox titleRow = new HBox(8, title, spacer, help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        VBox topCard = new VBox(6);
        topCard.getStyleClass().add("card");
        topCard.setPadding(new Insets(10, 12, 10, 12));

        input.setPromptText("输入达人名称或抖音号（如：封茗囧菌 / mandisasa）");
        input.setOnAction(e -> doSearch());
        HBox.setHgrow(input, Priority.ALWAYS);
        searchBtn.setOnAction(e -> doSearch());
        rangeCombo.getItems().addAll(
                new RangeItem(30, "近30天"), new RangeItem(15, "近15天"), new RangeItem(7, "近7天"));
        rangeCombo.getSelectionModel().selectFirst();
        rangeCombo.setPrefWidth(110);
        rangeCombo.setTooltip(new Tooltip("达人视频列表与视频指数的统计时间范围"
                + "（核心指标各时间窗一次全部返回）"));
        fetchBtn.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white; -fx-background-radius:4;");
        fetchBtn.setOnAction(e -> doFetch());
        Button openBtn = new Button("📂 打开保存目录");
        openBtn.setTooltip(new Tooltip("打开 <导出目录>/抖音指数/（每次抓取的完整数据都保存在这里）"));
        openBtn.setOnAction(e -> openDir());
        HBox row = new HBox(8, input, searchBtn, new Label("统计范围"), rangeCombo, fetchBtn, openBtn);
        row.setAlignment(Pos.CENTER_LEFT);

        accountLabel.setWrapText(true);
        topCard.getChildren().addAll(row, accountLabel);

        videoTable.setRowFactory(tv -> {
            TableRow<List<String>> r = new TableRow<>();
            r.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !r.isEmpty()) openVideo(r.getIndex());
            });
            return r;
        });
        similarTable.setRowFactory(tv -> {
            TableRow<List<String>> r = new TableRow<>();
            r.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !r.isEmpty()) openSimilar(r.getIndex());
            });
            return r;
        });

        TabPane tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        tabs.getTabs().addAll(
                new Tab("📊 核心指标", coreTab()),
                new Tab("📅 每日趋势", trendTab()),
                new Tab("🎬 达人视频", videoTab()),
                new Tab("👥 粉丝分析", fansTab()),
                new Tab("🔗 相似达人", similarTab()));
        VBox.setVgrow(tabs, Priority.ALWAYS);

        statusLabel.setStyle("-fx-text-fill:#666;");

        VBox root = new VBox(6, titleRow, topCard, tabs, statusLabel);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(8));
        return root;
    }

    private Node coreTab() {
        Label coreTitle = section("核心指标（作者分析）");
        Label avgTitle = section("稿均指标（作品分析 · 每条作品的平均数据）");
        VBox box = new VBox(6, coreTitle, coreTable, avgTitle, avgTable);
        VBox.setVgrow(coreTable, Priority.ALWAYS);
        VBox.setVgrow(avgTable, Priority.ALWAYS);
        box.setPadding(new Insets(6, 4, 4, 4));
        return box;
    }

    private Node trendTab() {
        VBox box = new VBox(6, trendLabel, trendTable);
        VBox.setVgrow(trendTable, Priority.ALWAYS);
        box.setPadding(new Insets(6, 4, 4, 4));
        return box;
    }

    private Node videoTab() {
        VBox box = new VBox(6, section("达人视频（双击行打开视频页）"), videoTable);
        VBox.setVgrow(videoTable, Priority.ALWAYS);
        box.setPadding(new Insets(6, 4, 4, 4));
        return box;
    }

    private Node fansTab() {
        VBox box = new VBox(6, section("粉丝画像（占比 + TGI 指数，TGI>100 表示偏好度高于大盘）"), fansTable);
        VBox.setVgrow(fansTable, Priority.ALWAYS);
        box.setPadding(new Insets(6, 4, 4, 4));
        return box;
    }

    private Node similarTab() {
        VBox box = new VBox(6, section("相似达人（双击行直接抓取该达人）"), similarTable);
        VBox.setVgrow(similarTable, Priority.ALWAYS);
        box.setPadding(new Insets(6, 4, 4, 4));
        return box;
    }

    private static Label section(String text) {
        Label lb = new Label(text);
        lb.setStyle("-fx-font-weight:bold;");
        return lb;
    }

    private static TableView<List<String>> newTable(String[] headers, double[] widths) {
        TableView<List<String>> table = new TableView<>();
        table.setPlaceholder(new Label("暂无数据"));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        for (int i = 0; i < headers.length; i++) {
            final int idx = i;
            TableColumn<List<String>, String> col = new TableColumn<>(headers[i]);
            col.setSortable(false);
            col.setPrefWidth(i < widths.length ? widths[i] : 90);
            col.setCellValueFactory(d -> new ReadOnlyStringWrapper(
                    d.getValue() != null && idx < d.getValue().size() ? d.getValue().get(idx) : ""));
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
        return table;
    }

    private void showHelp() {
        Alert alert = new Alert(Alert.AlertType.INFORMATION);
        alert.setTitle("抖音指数 · 功能介绍");
        alert.setHeaderText("抖音指数");
        alert.setContentText("""
                · 需要抖音 Cookie（创作者中心接口需登录态）：「Cookie 管理」→ 抖音 → 浏览器登录
                · 搜索：输入达人名称或抖音号 → 🔍 搜索 → 候选列表中双击或确认选择（两步确认）
                · 核心指标：新增点赞/净增粉丝/新增分享/新增评论/新增作品，
                  昨日·近7天·近15天·近30天 四档 + 各档环比 + 涨粉里程碑
                · 稿均指标：稿均点赞/涨粉/分享/评论（近7天、近30天 + 环比）
                · 每日趋势：接口返回范围内每天的五个指标序列
                · 达人视频：发布日期/内容/点赞/评论/分享/收藏/视频指数（双击行打开视频页）；
                  「统计范围」下拉控制视频列表与视频指数的时间范围
                · 粉丝分析：省份/城市级别/城市/年龄/性别/手机品牌/手机价格/兴趣标签，
                  全部带占比 + TGI 偏好指数（TGI>100 表示该人群偏好度高于大盘）
                · 相似达人：同赛道达人榜（双击行直接抓取该达人）
                · 保存位置：<导出目录>/抖音指数/<达人名>/抖音指数_时间戳.json
                  （另存 latest.json 为最新一份快照）
                """);
        alert.getDialogPane().setPrefWidth(620);
        alert.showAndWait();
    }

    // ==================== 搜索（两步确认） ====================

    private void doSearch() {
        String kw = input.getText() == null ? "" : input.getText().strip();
        if (kw.isEmpty()) {
            statusLabel.setText("❌ 请输入达人名称或抖音号");
            return;
        }
        setBusy(true, "⏳ 搜索「" + kw + "」...");
        Thread t = new Thread(() -> {
            try {
                List<JsonNode> users = engine.searchDaren(kw, 20);
                Platform.runLater(() -> {
                    setBusy(false);
                    if (users.isEmpty()) {
                        statusLabel.setText("❌ 未找到「" + kw + "」相关的达人");
                        return;
                    }
                    pickDaren(users, kw);
                });
            } catch (Exception e) {
                Platform.runLater(() -> {
                    setBusy(false);
                    statusLabel.setText("❌ 搜索失败：" + e.getMessage());
                });
            }
        }, "douyin-index-search");
        t.setDaemon(true);
        t.start();
    }

    private void pickDaren(List<JsonNode> users, String kw) {
        Dialog<Integer> dlg = new Dialog<>();
        dlg.setTitle("选择达人");
        dlg.setResizable(true);
        dlg.getDialogPane().setPrefWidth(760);
        dlg.getDialogPane().setPrefHeight(460);
        ButtonType okType = new ButtonType("确认", ButtonBar.ButtonData.OK_DONE);
        dlg.getDialogPane().getButtonTypes().addAll(ButtonType.CANCEL, okType);

        TableView<List<String>> table = newTable(
                new String[]{"达人", "抖音号", "粉丝", "总获赞", "作品", "标签"},
                new double[]{180, 140, 100, 100, 70, 180});
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (JsonNode u : users) {
            rows.add(List.of(
                    text(u, "user_name"),
                    text(u, "aweme_id").isEmpty() ? "-" : text(u, "aweme_id"),
                    DouyinIndex.fmtCount(raw(u, "follow_count")),
                    DouyinIndex.fmtCount(raw(u, "like_count")),
                    text(u, "item_count"),
                    text(u, "first_tag_name") + "/" + text(u, "second_tag_name")));
        }
        table.setItems(rows);
        table.setRowFactory(tv -> {
            TableRow<List<String>> r = new TableRow<>();
            r.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !r.isEmpty()) {
                    table.getSelectionModel().select(r.getIndex());
                    dlg.setResult(r.getIndex());
                    dlg.close();
                }
            });
            return r;
        });

        VBox box = new VBox(8,
                new Label("找到 " + users.size() + " 个达人，双击或选择后点「确认」："), table);
        VBox.setVgrow(table, Priority.ALWAYS);
        box.setPadding(new Insets(8));
        dlg.getDialogPane().setContent(box);
        dlg.setResultConverter(bt -> bt == okType ? table.getSelectionModel().getSelectedIndex() : null);

        Integer picked = dlg.showAndWait().orElse(-1);
        if (picked == null || picked < 0 || picked >= users.size()) return;
        JsonNode u = users.get(picked);
        uid = text(u, "user_id");
        name = text(u, "user_name");
        accountLabel.setText("当前账号：" + name
                + "（抖音号: " + (text(u, "aweme_id").isEmpty() ? "-" : text(u, "aweme_id")) + "）"
                + " · 粉丝 " + DouyinIndex.fmtCount(raw(u, "follow_count"))
                + " · 总获赞 " + DouyinIndex.fmtCount(raw(u, "like_count"))
                + " · 作品 " + text(u, "item_count"));
        statusLabel.setText("✅ 已选择「" + name + "」→ 点「📊 获取全部数据并保存」");
        fetchBtn.requestFocus();
    }

    // ==================== 抓取 ====================

    private void doFetch() {
        if (uid.isEmpty()) {
            statusLabel.setText("❌ 请先搜索并确认达人");
            return;
        }
        if (busy) return;
        RangeItem item = rangeCombo.getSelectionModel().getSelectedItem();
        int days = item == null ? 30 : item.days();
        String targetUid = uid;
        String targetName = name;
        setBusy(true, "⏳ 正在抓取「" + targetName + "」的抖音指数...");
        Thread t = new Thread(() -> {
            try {
                JsonNode result = engine.fetchDarenAll(targetUid, days,
                        msg -> Platform.runLater(() -> statusLabel.setText("⏳ " + msg)));
                Path path = DouyinIndex.saveSnapshot(result);
                Platform.runLater(() -> {
                    setBusy(false);
                    data = result;
                    render(result);
                    statusLabel.setText("✅ 「" + targetName + "」数据已保存：" + path);
                    toast("抖音指数已保存：" + path.getFileName());
                });
            } catch (Exception e) {
                Platform.runLater(() -> {
                    setBusy(false);
                    statusLabel.setText("❌ 抓取失败：" + e.getMessage());
                    toast("抖音指数抓取失败：" + e.getMessage());
                });
            }
        }, "douyin-index-fetch");
        t.setDaemon(true);
        t.start();
    }

    private void setBusy(boolean value) {
        setBusy(value, null);
    }

    private void setBusy(boolean value, String text) {
        busy = value;
        searchBtn.setDisable(value);
        fetchBtn.setDisable(value);
        if (text != null && !text.isEmpty()) statusLabel.setText(text);
    }

    // ==================== 渲染 ====================

    private void render(JsonNode root) {
        JsonNode core = root.path("core_metrics");
        renderCore(core);
        renderTrend(core);
        renderAvg(root.path("item_metrics"));
        renderVideos(root.path("videos"));
        renderFans(root.path("fans_profile"));
        renderSimilar(root.path("similar_users"));

        JsonNode author = root.path("author");
        String intro = text(author, "user_introduction").replace("\n", " · ");
        if (intro.length() > 120) intro = intro.substring(0, 120) + "...";
        JsonNode ms = author.path("fans_milestone");
        List<String> milestones = new ArrayList<>();
        addMilestone(milestones, ms, 1, "first_1w_fans_date");
        addMilestone(milestones, ms, 10, "first_10w_fans_date");
        addMilestone(milestones, ms, 30, "first_30w_fans_date");
        addMilestone(milestones, ms, 100, "first_100w_fans_date");
        String msText = String.join(" · ", milestones);

        String displayName = text(author, "user_name");
        if (displayName.isEmpty()) displayName = name;
        String aweme = text(author, "aweme_id");
        StringBuilder sb = new StringBuilder();
        sb.append("当前账号：").append(displayName)
                .append("（抖音号: ").append(aweme.isEmpty() ? "-" : aweme).append("）")
                .append(" · ").append(text(author, "first_tag_name"))
                .append("/").append(text(author, "second_tag_name"))
                .append(" · 粉丝 ").append(DouyinIndex.fmtCount(raw(author, "fans_count")))
                .append(" · 总获赞 ").append(DouyinIndex.fmtCount(raw(author, "like_count")))
                .append(" · 作品 ").append(text(author, "item_count"));
        if (!msText.isEmpty()) sb.append("\n涨粉里程碑：").append(msText);
        if (!intro.isEmpty()) sb.append("\n简介：").append(intro);
        accountLabel.setText(sb.toString());
    }

    private static void addMilestone(List<String> out, JsonNode ms, int wan, String key) {
        String day = text(ms, key);
        if (!day.isEmpty()) out.add("首" + wan + "万粉 " + fmtDay(day));
    }

    private void renderCore(JsonNode core) {
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (CoreRow row : CORE_ROWS) {
            JsonNode info = core.path(row.seg());
            List<String> line = new ArrayList<>();
            line.add(row.label());
            for (Window w : WINDOWS) {
                JsonNode v = info.get(row.prefix() + w.suffix());
                line.add(present(v) ? DouyinIndex.fmtCount(raw(v)) : "-");
                JsonNode ex = info.get("exchange_by_" + w.suffix());
                line.add(present(ex) ? DouyinIndex.fmtPct(raw(ex)) : "-");
            }
            rows.add(line);
        }
        coreTable.setItems(rows);
    }

    private void renderAvg(JsonNode itemMetrics) {
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (AvgRow row : AVG_ROWS) {
            JsonNode arr = itemMetrics.path(row.seg());
            JsonNode info = arr.isArray() && !arr.isEmpty() ? arr.get(0) : null;
            List<String> line = new ArrayList<>();
            line.add(row.label());
            JsonNode wk = info == null ? null : info.get(row.prefix() + "week");
            JsonNode mo = info == null ? null : info.get(row.prefix() + "month");
            line.add(present(wk) ? String.format(Locale.ROOT, "%.2f", wk.asDouble()) : "-");
            line.add(info == null ? "-" : DouyinIndex.fmtPct(raw(info.get("exchange_by_week"))));
            line.add(present(mo) ? String.format(Locale.ROOT, "%.2f", mo.asDouble()) : "-");
            line.add(info == null ? "-" : DouyinIndex.fmtPct(raw(info.get("exchange_by_month"))));
            rows.add(line);
        }
        avgTable.setItems(rows);
    }

    private void renderTrend(JsonNode core) {
        String[] keys = {"likelistday", "fanslistday", "sharelistday", "commentlistday", "itemlistday"};
        Map<String, Map<String, JsonNode>> byDate = new HashMap<>();
        for (String key : keys) {
            JsonNode arr = core.path(key);
            if (!arr.isArray()) continue;
            for (JsonNode it : arr) {
                String d = text(it, "date");
                if (d.length() != 8) continue;
                byDate.computeIfAbsent(fmtDay(d), k -> new HashMap<>()).put(key, it.get("count"));
            }
        }
        List<String> dates = new ArrayList<>(byDate.keySet());
        dates.sort(Comparator.reverseOrder());
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (String d : dates) {
            Map<String, JsonNode> row = byDate.get(d);
            List<String> line = new ArrayList<>();
            line.add(d);
            for (String key : keys) {
                JsonNode v = row.get(key);
                line.add(present(v) ? DouyinIndex.fmtCount(raw(v)) : "-");
            }
            rows.add(line);
        }
        trendLabel.setText("每日趋势 · 共 " + dates.size() + " 天（接口返回的最大范围）");
        trendTable.setItems(rows);
    }

    private void renderVideos(JsonNode videosNode) {
        List<JsonNode> videos = new ArrayList<>();
        if (videosNode.isArray()) videosNode.forEach(videos::add);
        videos.sort(Comparator.comparing((JsonNode v) -> text(v, "create_time")).reversed());
        videoRows = videos;
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (JsonNode v : videos) {
            String d = text(v, "create_time");
            rows.add(List.of(
                    d.length() == 8 ? fmtDay(d) : "-",
                    text(v, "video_text"),
                    DouyinIndex.fmtCount(raw(v, "like_cnt")),
                    DouyinIndex.fmtCount(raw(v, "coment_cnt")),
                    DouyinIndex.fmtCount(raw(v, "share_cnt")),
                    DouyinIndex.fmtCount(raw(v, "follow_cnt")),
                    text(v, "video_index").isEmpty() ? "-" : text(v, "video_index")));
        }
        videoTable.setItems(rows);
    }

    private void renderFans(JsonNode fans) {
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (String label : FANS_ORDER) {
            JsonNode items = fans.path(label);
            if (!items.isArray() || items.isEmpty()) continue;
            Map<String, JsonNode> tgi = new LinkedHashMap<>();
            JsonNode tgiArr = fans.path(label + "TGI");
            if (tgiArr.isArray()) {
                for (JsonNode x : tgiArr) tgi.put(text(x, "name"), x.get("value"));
            }
            for (JsonNode x : items) {
                JsonNode v = x.get("value");
                String pct = present(v) && v.isNumber()
                        ? String.format(Locale.ROOT, "%.2f%%", v.asDouble() * 100) : "-";
                JsonNode t = tgi.get(text(x, "name"));
                String tgiText = present(t) && t.isNumber()
                        ? String.format(Locale.ROOT, "%.2f", t.asDouble()) : "-";
                rows.add(List.of(label, text(x, "name"), pct, tgiText));
            }
        }
        fansTable.setItems(rows);
    }

    private void renderSimilar(JsonNode similarNode) {
        List<JsonNode> users = new ArrayList<>();
        if (similarNode.isArray()) similarNode.forEach(users::add);
        similarRows = users;
        ObservableList<List<String>> rows = FXCollections.observableArrayList();
        for (JsonNode u : users) {
            rows.add(List.of(
                    text(u, "rank"),
                    text(u, "user_name"),
                    text(u, "aweme_id").isEmpty() ? "-" : text(u, "aweme_id"),
                    DouyinIndex.fmtCount(raw(u, "follow_count")),
                    DouyinIndex.fmtCount(raw(u, "like_count")),
                    text(u, "item_count"),
                    text(u, "first_tag_name") + "/" + text(u, "second_tag_name")));
        }
        similarTable.setItems(rows);
    }

    // ==================== 交互 ====================

    private void openVideo(int row) {
        if (data == null || row < 0 || row >= videoRows.size()) return;
        String vid = text(videoRows.get(row), "item_id");
        if (vid.isEmpty()) return;
        try {
            java.awt.Desktop.getDesktop().browse(
                    java.net.URI.create("https://www.douyin.com/video/" + vid));
        } catch (Exception e) {
            toast("打开视频页失败：" + e.getMessage());
        }
    }

    private void openSimilar(int row) {
        if (data == null || busy || row < 0 || row >= similarRows.size()) return;
        JsonNode u = similarRows.get(row);
        uid = text(u, "user_id");
        name = text(u, "user_name");
        input.setText(name);
        statusLabel.setText("✅ 已切换到「" + name + "」→ 正在抓取...");
        doFetch();
    }

    private void openDir() {
        try {
            Path dir = DouyinIndex.indexDir();
            Files.createDirectories(dir);
            java.awt.Desktop.getDesktop().open(dir.toFile());
        } catch (Exception e) {
            toast("保存目录尚未创建（先完成一次抓取）");
        }
    }

    // ==================== 工具 ====================

    private static String text(JsonNode node, String field) {
        if (node == null) return "";
        JsonNode v = node.path(field);
        return v.isMissingNode() || v.isNull() ? "" : v.asText("");
    }

    private static JsonNode raw(JsonNode node, String field) {
        return node == null ? null : node.get(field);
    }

    /** JsonNode → 数字/字符串原始值（交给 fmtCount/fmtPct）。 */
    private static Object raw(JsonNode v) {
        if (v == null || v.isNull() || v.isMissingNode()) return null;
        return v.isNumber() ? v.numberValue() : v.asText("");
    }

    private static boolean present(JsonNode v) {
        return v != null && !v.isNull() && !v.isMissingNode() && !v.asText("").isEmpty();
    }

    private static String fmtDay(String v) {
        return v != null && v.length() == 8
                ? v.substring(0, 4) + "-" + v.substring(4, 6) + "-" + v.substring(6) : v;
    }
}
