package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.platform.tickets.Tickets;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import javafx.application.Platform;
import javafx.beans.property.ReadOnlyStringWrapper;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.collections.ListChangeListener;
import javafx.collections.ObservableList;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.SplitPane;
import javafx.scene.control.TableCell;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.control.Tooltip;
import javafx.scene.control.cell.CheckBoxTableCell;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.function.Function;

/**
 * 票务解析页。移植自 Python 版 gui_qt/pages/tickets_page.py：
 * 大麦 / 秀动 / B站会员购 三平台演出检索、详情拉取、图片下载与 JSON 导出。
 */
public class TicketsPage extends BasePage {

    private static final String DEFAULT_KEYWORD = "封茗囧菌";

    private static final class EventRow {
        final Tickets.ShowEvent ev;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);

        EventRow(Tickets.ShowEvent ev) {
            this.ev = ev;
        }
    }

    private final Tickets engine = new Tickets();
    private final ObjectMapper mapper = JsonMapper.get();

    private final TextField input = new TextField();
    private final Button parseBtn = new Button("🔍 解析");
    private final Button detailBtn = new Button("🎫 获取选中演出详情");
    private final Button exportBtn = new Button("💾 导出列表");
    private final Label checkCount = new Label("已勾选 0 场");
    private final TableView<EventRow> table = new TableView<>();
    private final TextArea detailText = new TextArea();
    private final Button linkBtn = new Button("🌐 打开演出页面");
    private final Label statusLabel = new Label("");

    private final List<Tickets.ShowEvent> events = new ArrayList<>();
    private final List<Tickets.ShowEvent> eventsOriginal = new ArrayList<>();
    private final Map<String, ObjectNode> detailCache = new LinkedHashMap<>();
    private boolean busy;
    private boolean filling;

    public TicketsPage() {
        input.setPromptText("留空直接回车 = 搜索「封茗囧菌」· "
                + "或输入 damai/showstart/bilibili 链接或任意关键词");
        input.setOnAction(e -> parse());
        parseBtn.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white; -fx-background-radius:4;");
        parseBtn.setOnAction(e -> parse());
        detailBtn.setDisable(true);
        detailBtn.setTooltip(new Tooltip("勾选了演出→逐场拉取详情+下载图片（封面/海报/宣传图/"
                + "座位图/嘉宾头像/场馆图，存 <导出目录>/票务演出/）；未勾选→仅当前选中行"));
        detailBtn.setOnAction(e -> fetchDetails());
        exportBtn.setDisable(true);
        exportBtn.setTooltip(new Tooltip("导出勾选的演出为全量 JSON（场次/票档/须知/嘉宾明细/"
                + "图片本地路径）；未拉详情的自动逐场拉取并下载图片"));
        exportBtn.setOnAction(e -> exportJson());
    }

    @Override
    public String title() {
        return "🎫 票务解析";
    }

    // ==================== 构建 ====================

    @Override
    protected Node build() {
        Label title = new Label(title());
        title.setStyle("-fx-font-size:18px; -fx-font-weight:bold;");
        Button help = new Button("❓ 票务解析 · 功能介绍");
        help.setOnAction(e -> showHelp());
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox titleRow = new HBox(8, title, spacer, help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        HBox.setHgrow(input, Priority.ALWAYS);
        Button detail = detailBtn;
        Button export = exportBtn;
        HBox row = new HBox(8, input, parseBtn, detail, export);
        row.setAlignment(Pos.CENTER_LEFT);

        Button selAll = new Button("☑ 全选");
        selAll.setOnAction(e -> setAllChecks(true));
        Button invert = new Button("⇄ 反选");
        invert.setOnAction(e -> invertChecks());
        Button clear = new Button("✖ 清除");
        clear.setOnAction(e -> setAllChecks(false));
        checkCount.setStyle("-fx-text-fill:#888;");
        HBox selRow = new HBox(8, selAll, invert, clear, checkCount);
        selRow.setAlignment(Pos.CENTER_LEFT);

        buildTable();
        detailText.setEditable(false);
        detailText.setWrapText(false);
        detailText.setPromptText("选中左侧演出查看详情；点「获取选中演出详情」拉场次/票档");
        linkBtn.setDisable(true);
        linkBtn.setOnAction(e -> openLink());
        VBox right = new VBox(4, new Label("演出详情"), detailText, linkBtn);
        VBox.setVgrow(detailText, Priority.ALWAYS);

        SplitPane split = new SplitPane(table, right);
        split.setDividerPositions(0.6);
        VBox.setVgrow(split, Priority.ALWAYS);

        statusLabel.setStyle("-fx-text-fill:#888;");
        statusLabel.setWrapText(true);

        VBox root = new VBox(8, titleRow, row, selRow, split, statusLabel);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        return root;
    }

    private void buildTable() {
        table.setPlaceholder(new Label("暂无演出，先粘贴链接或输入关键词解析"));
        table.setEditable(true);
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        table.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> onSelect());

        TableColumn<EventRow, Boolean> check = new TableColumn<>("选");
        check.setPrefWidth(40);
        check.setEditable(true);
        check.setSortable(false);
        check.setCellValueFactory(d -> d.getValue().checked);
        check.setCellFactory(CheckBoxTableCell.forTableColumn(check));
        table.getColumns().add(check);

        addColumn("标题", 320, r -> r.ev.title.isEmpty() ? "-" : r.ev.title,
                Comparator.comparing((String s) -> s.toLowerCase(Locale.ROOT)));
        addColumn("平台", 110, r -> r.ev.platformCn.isEmpty() ? r.ev.platform : r.ev.platformCn,
                Comparator.comparing((String s) -> s.toLowerCase(Locale.ROOT)));
        addColumn("城市", 90, r -> r.ev.city,
                Comparator.comparing((String s) -> s.toLowerCase(Locale.ROOT)));
        addColumn("时间", 150, r -> r.ev.showTime, Comparator.naturalOrder());
        addColumn("票价", 100, r -> r.ev.priceRange,
                Comparator.comparingDouble(TicketsPage::firstNumber));
        addColumn("状态", 90, r -> r.ev.status,
                Comparator.comparing((String s) -> s.toLowerCase(Locale.ROOT)));

        table.setRowFactory(tv -> {
            javafx.scene.control.TableRow<EventRow> row = new javafx.scene.control.TableRow<>();
            row.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !row.isEmpty()) openEvent(row.getItem().ev);
            });
            return row;
        });
        table.getSortOrder().addListener((ListChangeListener<TableColumn<EventRow, ?>>) c -> {
            if (table.getSortOrder().isEmpty() && !events.isEmpty()) {
                reorderRows(eventsOriginal);
            }
        });
    }

    private void addColumn(String name, double width, Function<EventRow, String> value,
                           Comparator<String> comparator) {
        TableColumn<EventRow, String> col = new TableColumn<>(name);
        col.setPrefWidth(width);
        col.setSortable(true);
        col.setCellValueFactory(d -> new ReadOnlyStringWrapper(value.apply(d.getValue())));
        col.setComparator(comparator);
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

    private void reorderRows(List<Tickets.ShowEvent> order) {
        filling = true;
        List<EventRow> rows = new ArrayList<>();
        for (Tickets.ShowEvent ev : order) rows.add(new EventRow(ev));
        // 勾选状态按演出 key 迁移
        Map<String, Boolean> checked = checkedKeys();
        rows.forEach(r -> r.checked.set(checked.getOrDefault(key(r.ev), false)));
        rows.forEach(r -> r.checked.addListener((o, a, b) -> updateCheckCount()));
        table.setItems(FXCollections.observableArrayList(rows));
        filling = false;
        updateCheckCount();
    }

    private void showHelp() {
        Alert alert = new Alert(Alert.AlertType.INFORMATION);
        alert.setTitle("票务解析 · 功能介绍");
        alert.setHeaderText("票务解析");
        alert.setContentText("""
                · 搜索：粘贴平台链接（大麦艺人/演出、秀动艺人/事件、B站会员购搜索/项目），
                  或直接输入艺人名/演出名/场馆关键词——留空回车默认搜「封茗囧菌」，
                  三平台同时搜索（大麦走真实浏览器约需 20 秒）
                · 表格：勾选（全选/反选/清除）后点「获取选中演出详情」逐场拉取，
                  并下载图片（封面/海报/宣传图/座位图/嘉宾头像/场馆图，存 <导出目录>/票务演出/）
                · 💾 导出列表：输出全量 JSON（未拉详情的自动逐场拉取+下载图片）
                · 单件失败自动原地重试，不中断整批；导出时按勾选逐件处理并显示进度
                · 采集链路游客优先，降低风控风险
                """);
        alert.getDialogPane().setPrefWidth(620);
        alert.showAndWait();
    }

    // ==================== 解析 ====================

    private void parse() {
        if (busy) return;
        String text = input.getText() == null ? "" : input.getText().strip();
        if (text.isEmpty()) {
            input.setText(DEFAULT_KEYWORD);
            text = DEFAULT_KEYWORD;
        }
        String query = text;
        setBusy(true, "⏳ 解析中...");
        Thread t = new Thread(() -> {
            try {
                List<Tickets.ShowEvent> list;
                Map<String, String> diag = new LinkedHashMap<>();
                String[] kind = Tickets.detectTicketPlatform(query);
                if (kind != null) {
                    list = engine.parseTicketUrl(query);
                } else if (looksLikeUrl(query)) {
                    throw new IllegalArgumentException("无法识别的票务链接（支持大麦艺人/演出、"
                            + "秀动艺人/事件、B站会员购搜索/项目）");
                } else {
                    Tickets.SearchResult result = engine.searchTicketsByKeyword(query, "", "");
                    list = result.events();
                    diag = result.diag();
                }
                List<Tickets.ShowEvent> finalList = list;
                Map<String, String> finalDiag = diag;
                Platform.runLater(() -> applyParse(finalList, finalDiag));
            } catch (Exception e) {
                Platform.runLater(() -> {
                    setBusy(false);
                    statusLabel.setText("");
                    toast("解析失败: " + trunc(e.getMessage(), 80));
                });
            }
        }, "tickets-parse");
        t.setDaemon(true);
        t.start();
    }

    private static boolean looksLikeUrl(String s) {
        if (s.contains("://") || s.contains("/")) return true;
        String head = s.length() > 5 ? s.substring(0, 5) : s;
        return head.chars().anyMatch(Character::isDigit);
    }

    private void applyParse(List<Tickets.ShowEvent> list, Map<String, String> diag) {
        setBusy(false);
        events.clear();
        events.addAll(list);
        eventsOriginal.clear();
        eventsOriginal.addAll(list);
        detailCache.clear();
        detailText.clear();
        linkBtn.setDisable(true);
        fillTable();
        StringBuilder tip = new StringBuilder();
        if (diag != null && !diag.isEmpty()) {
            List<String> nice = new ArrayList<>();
            for (Map.Entry<String, String> e : diag.entrySet()) {
                if (e.getValue() == null || e.getValue().isEmpty()) continue;
                if (e.getValue().chars().allMatch(Character::isDigit)) {
                    if (!"0".equals(e.getValue())) nice.add(e.getKey() + "=" + e.getValue());
                } else {
                    nice.add(e.getKey() + ": " + e.getValue());
                }
            }
            if (!nice.isEmpty()) tip.append("｜").append(String.join(" ", nice));
        }
        statusLabel.setText("✅ 解析到 " + events.size() + " 场演出" + tip);
        exportBtn.setDisable(events.isEmpty());
    }

    private void fillTable() {
        filling = true;
        List<EventRow> rows = new ArrayList<>();
        for (Tickets.ShowEvent ev : events) {
            EventRow r = new EventRow(ev);
            r.checked.addListener((o, a, b) -> updateCheckCount());
            rows.add(r);
        }
        table.setItems(FXCollections.observableArrayList(rows));
        table.getSortOrder().clear();
        filling = false;
        updateCheckCount();
        detailBtn.setDisable(true);
        exportBtn.setDisable(events.isEmpty());
    }

    // ==================== 勾选 ====================

    private void updateCheckCount() {
        if (filling) return;
        int n = 0;
        for (EventRow r : table.getItems()) if (r.checked.get()) n++;
        checkCount.setText("已勾选 " + n + " 场");
    }

    private void setAllChecks(boolean value) {
        filling = true;
        for (EventRow r : table.getItems()) r.checked.set(value);
        filling = false;
        updateCheckCount();
    }

    private void invertChecks() {
        filling = true;
        for (EventRow r : table.getItems()) r.checked.set(!r.checked.get());
        filling = false;
        updateCheckCount();
    }

    private List<EventRow> checkedRows() {
        List<EventRow> out = new ArrayList<>();
        for (EventRow r : table.getItems()) if (r.checked.get()) out.add(r);
        return out;
    }

    private Map<String, Boolean> checkedKeys() {
        Map<String, Boolean> out = new HashMap<>();
        for (EventRow r : table.getItems()) {
            if (r.checked.get()) out.put(key(r.ev), true);
        }
        return out;
    }

    private static String key(Tickets.ShowEvent ev) {
        return ev.platform + ":" + ev.eventId;
    }

    // ==================== 选中 / 详情 ====================

    private void onSelect() {
        EventRow row = table.getSelectionModel().getSelectedItem();
        detailBtn.setDisable(row == null);
        if (row == null) {
            linkBtn.setDisable(true);
            return;
        }
        ObjectNode cached = detailCache.get(key(row.ev));
        detailText.setText(formatDetail(row.ev, cached));
        linkBtn.setDisable(row.ev.url.isEmpty()
                && (cached == null || cached.path("链接").asText("").isEmpty()));
    }

    private void fetchDetails() {
        if (busy || events.isEmpty()) return;
        List<EventRow> rows = checkedRows();
        if (rows.isEmpty()) {
            EventRow sel = table.getSelectionModel().getSelectedItem();
            if (sel != null) rows = List.of(sel);
        }
        if (rows.isEmpty()) return;
        final List<EventRow> rowsFinal = rows;
        List<Tickets.ShowEvent> todo = new ArrayList<>();
        for (EventRow r : rowsFinal) {
            if (!detailCache.containsKey(key(r.ev))) todo.add(r.ev);
        }
        if (todo.isEmpty()) {
            showCached(rowsFinal);
            return;
        }
        int total = todo.size();
        setBusy(true, "⏳ 获取 " + total + " 场演出详情（含图片下载）...");
        Thread t = new Thread(() -> {
            List<Tickets.ShowEvent> done = new ArrayList<>();
            List<String> failed = new ArrayList<>();
            Path base = Settings.load().effectiveExportDir().resolve("票务演出");
            for (int i = 0; i < todo.size(); i++) {
                Tickets.ShowEvent ev = todo.get(i);
                log("详情进度 " + (i + 1) + "/" + total + "：" + trunc(ev.title, 26));
                if (i > 0) sleep(1500);
                for (int attempt = 1; attempt <= 2; attempt++) {
                    try {
                        Tickets.ShowEvent full = parseOne(ev);
                        engine.downloadEventImages(full, base, this::log);
                        done.add(full);
                        break;
                    } catch (Exception e) {
                        if (attempt == 1) {
                            log("  " + trunc(ev.title, 16) + " 失败，5 秒后重试...");
                            sleep(5000);
                        } else {
                            failed.add(ev.title + "：" + trunc(e.getMessage(), 60));
                        }
                    }
                }
            }
            Platform.runLater(() -> {
                setBusy(false);
                for (Tickets.ShowEvent ev : done) detailCache.put(key(ev), ev.toDict());
                showCached(rowsFinal);
                String tip = failed.isEmpty() ? "" : "，" + failed.size() + " 场失败";
                statusLabel.setText("✅ 已获取 " + done.size() + " 场详情并下载图片" + tip
                        + "（<导出目录>/票务演出/，每场一个目录）");
            });
        }, "tickets-detail");
        t.setDaemon(true);
        t.start();
    }

    private Tickets.ShowEvent parseOne(Tickets.ShowEvent ev) throws Exception {
        return switch (ev.platform) {
            case "damai" -> engine.parseDamaiItem(ev.eventId);
            case "showstart" -> engine.parseShowstartEvent(ev.eventId);
            case "bilibili_show" -> engine.parseBilibiliProject(ev.eventId);
            default -> throw new IllegalStateException("平台 " + ev.platformCn + " 暂不支持详情获取");
        };
    }

    private void showCached(List<EventRow> rows) {
        EventRow show = table.getSelectionModel().getSelectedItem();
        if (show == null && !rows.isEmpty()) show = rows.get(0);
        if (show == null) return;
        ObjectNode detail = detailCache.get(key(show.ev));
        if (detail != null) {
            detailText.setText(formatDetail(show.ev, detail));
            linkBtn.setDisable(false);
        }
    }

    private void openLink() {
        EventRow row = table.getSelectionModel().getSelectedItem();
        if (row == null) return;
        ObjectNode detail = detailCache.get(key(row.ev));
        String url = detail != null ? detail.path("链接").asText("") : "";
        if (url.isEmpty()) url = row.ev.url;
        if (!url.isEmpty()) browse(url);
    }

    private void openEvent(Tickets.ShowEvent ev) {
        if (ev != null && !ev.url.isEmpty()) browse(ev.url);
    }

    private static void browse(String url) {
        try {
            java.awt.Desktop.getDesktop().browse(java.net.URI.create(url));
        } catch (Exception ignored) {
        }
    }

    // ==================== 导出 ====================

    private void exportJson() {
        if (busy || events.isEmpty()) return;
        List<EventRow> checked = checkedRows();
        if (checked.isEmpty()) {
            toast("请先勾选要导出的演出（表格第一列复选框），或点「☑ 全选」");
            return;
        }
        FileChooser chooser = new FileChooser();
        chooser.setTitle("导出演出 JSON（全量）");
        chooser.setInitialFileName("票务演出_已选" + checked.size() + "场.json");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("JSON", "*.json"));
        File target = chooser.showSaveDialog(input.getScene() == null
                ? null : input.getScene().getWindow());
        if (target == null) return;
        int total = checked.size();
        setBusy(true, "⏳ 导出 " + total + " 场演出...");
        Path path = target.toPath();
        Thread t = new Thread(() -> {
            Path base = Settings.load().effectiveExportDir().resolve("票务演出");
            ArrayNode out = mapper.createArrayNode();
            List<String> failed = new ArrayList<>();
            int seq = 0;
            for (EventRow row : checked) {
                seq++;
                Tickets.ShowEvent ev = row.ev;
                log("导出进度 " + seq + "/" + total + "：" + trunc(ev.title, 26));
                if (seq > 1) sleep(1500);
                ObjectNode d = ev.toDict();
                ObjectNode detail = detailCache.get(key(ev));
                if (detail == null) {
                    for (int attempt = 1; attempt <= 2; attempt++) {
                        try {
                            Tickets.ShowEvent full = parseOne(ev);
                            engine.downloadEventImages(full, base, this::log);
                            detail = full.toDict();
                            detailCache.put(key(full), detail);
                            break;
                        } catch (Exception e) {
                            detail = null;
                            if (attempt == 1) {
                                log("  " + trunc(ev.title, 16) + " 失败，5 秒后重试...");
                                sleep(5000);
                            } else {
                                failed.add(trunc(ev.title, 20) + "：" + trunc(e.getMessage(), 60));
                            }
                        }
                    }
                }
                if (detail != null) {
                    final ObjectNode det = detail;
                    det.fields().forEachRemaining(e -> {
                        if (!"获取时间".equals(e.getKey()) && !isEmptyNode(e.getValue())) {
                            d.set(e.getKey(), e.getValue());
                        }
                    });
                }
                out.add(d);
            }
            ObjectNode result = mapper.createObjectNode();
            if (failed.isEmpty()) {
                // Python 无告警时导出的是数组本身
                try {
                    Files.writeString(path, mapper.writerWithDefaultPrettyPrinter()
                            .writeValueAsString(out), StandardCharsets.UTF_8);
                } catch (Exception e) {
                    Platform.runLater(() -> {
                        setBusy(false);
                        toast("导出失败: " + trunc(e.getMessage(), 80));
                    });
                    return;
                }
            } else {
                result.set("演出", out);
                ArrayNode warn = result.putArray("导出告警");
                failed.forEach(warn::add);
                try {
                    Files.writeString(path, mapper.writerWithDefaultPrettyPrinter()
                            .writeValueAsString(result), StandardCharsets.UTF_8);
                } catch (Exception e) {
                    Platform.runLater(() -> {
                        setBusy(false);
                        toast("导出失败: " + trunc(e.getMessage(), 80));
                    });
                    return;
                }
            }
            int count = out.size();
            Platform.runLater(() -> {
                setBusy(false);
                String tip = failed.isEmpty() ? "" : "，" + failed.size() + " 场详情获取失败";
                statusLabel.setText("✅ 已导出 " + count + " 场演出全量 JSON" + tip + "到 " + path);
            });
        }, "tickets-export");
        t.setDaemon(true);
        t.start();
    }

    // ==================== 详情格式化 ====================

    private static final List<String> DETAIL_ORDER = List.of(
            "平台", "标题", "副标题", "艺人", "城市", "场馆", "地址", "经纬度",
            "时间", "演出时长", "开售时间", "票价", "状态", "想看", "想看人数",
            "分类", "限购", "实名制", "主办方", "联系方式", "退票政策",
            "销售标签", "项目标签", "关键词");

    private String formatDetail(Tickets.ShowEvent ev, ObjectNode detail) {
        ObjectNode d = detail != null ? detail : ev.toDict();
        StringBuilder sb = new StringBuilder();
        for (String k : DETAIL_ORDER) {
            String v = d.path(k).asText("");
            if (!v.isEmpty()) sb.append(k).append("：").append(v).append('\n');
        }
        for (String k : List.of("海报", "场馆图", "本地图片目录")) {
            String v = d.path(k).asText("");
            if (!v.isEmpty()) sb.append(k).append("：").append(v).append('\n');
        }
        String desc = d.path("演出介绍").asText("");
        if (!desc.isEmpty()) {
            sb.append("\n═══ 演出介绍 ═══\n");
            sb.append(trunc(desc, 1500));
            if (desc.length() > 1500) sb.append("…");
            sb.append('\n');
        }
        appendUrlList(sb, d, "宣传图", 10);
        appendUrlList(sb, d, "座位图", 5);
        JsonNode notices = d.path("购票须知");
        if (notices.isArray() && !notices.isEmpty()) {
            sb.append("\n═══ 购票须知（").append(notices.size()).append(" 条）═══\n");
            for (JsonNode n : notices) {
                if (n.isObject()) {
                    sb.append("【").append(n.path("标题").asText("")).append("】\n");
                    sb.append(trunc(n.path("内容").asText(""), 300)).append("\n\n");
                } else {
                    sb.append("• ").append(n.asText("")).append('\n');
                }
            }
        }
        JsonNode faqs = d.path("常见问题");
        if (faqs.isArray() && !faqs.isEmpty()) {
            sb.append("\n═══ 常见问题（").append(faqs.size()).append(" 条）═══\n");
            int i = 0;
            for (JsonNode q : faqs) {
                if (++i > 20) break;
                sb.append("  Q").append(i).append(". ").append(q.asText("")).append('\n');
            }
        }
        JsonNode tags = d.path("标签");
        if (tags.isArray() && !tags.isEmpty()) {
            List<String> list = new ArrayList<>();
            for (JsonNode t : tags) list.add(t.asText(""));
            sb.append("\n═══ 标签 ═══\n");
            sb.append(trunc(String.join(" / ", list), 300)).append('\n');
        }
        JsonNode guests = d.path("嘉宾明细");
        if (guests.isArray() && !guests.isEmpty()) {
            sb.append("\n═══ 嘉宾（").append(guests.size()).append(" 位）═══\n");
            int i = 0;
            for (JsonNode g : guests) {
                if (++i > 30) break;
                StringBuilder line = new StringBuilder(g.path("名字").asText(""));
                String tag = g.path("标签").asText("");
                if (!tag.isEmpty()) line.append(" / ").append(tag);
                String intro = g.path("简介").asText("");
                if (!intro.isEmpty()) {
                    line.append("：").append(trunc(intro, 60));
                    if (intro.length() > 60) line.append("…");
                }
                sb.append("  · ").append(line).append('\n');
            }
            if (guests.size() > 30) sb.append("  （还有 ").append(guests.size() - 30).append(" 位…）\n");
        }
        JsonNode sessions = d.path("场次明细");
        if (sessions.isArray() && !sessions.isEmpty()) {
            sb.append("\n═══ 场次与票档 ═══\n");
            int i = 0;
            for (JsonNode s : sessions) {
                i++;
                String name = s.path("场次").asText("");
                if (name.isEmpty()) name = "场次" + i;
                sb.append("【").append(name).append("】")
                        .append(s.path("开始时间").asText("")).append('\n');
                JsonNode tiers = s.path("票档");
                if (!tiers.isArray() || tiers.isEmpty()) {
                    sb.append("  （无票档数据）\n");
                    continue;
                }
                for (JsonNode t : tiers) {
                    List<String> parts = new ArrayList<>();
                    addIf(parts, t.path("名称").asText(""));
                    addIf(parts, t.path("价格").asText(""));
                    addIf(parts, t.path("状态").asText(""));
                    addIf(parts, t.path("说明").asText(""));
                    sb.append("  · ").append(String.join(" | ", parts)).append('\n');
                }
            }
        }
        List<String[]> extras = new ArrayList<>();
        Set<String> known = new java.util.HashSet<>(DETAIL_ORDER);
        known.addAll(List.of("场次明细", "演出介绍", "演出介绍HTML", "宣传图", "座位图",
                "购票须知", "常见问题", "标签", "嘉宾明细", "海报", "场馆图", "本地图片目录",
                "演出ID", "链接", "封面", "获取时间"));
        d.fields().forEachRemaining(e -> {
            if (known.contains(e.getKey()) || isEmptyNode(e.getValue())) return;
            extras.add(new String[]{e.getKey(), e.getValue().isValueNode()
                    ? e.getValue().asText("") : e.getValue().toString()});
        });
        if (!extras.isEmpty()) {
            sb.append("\n═══ 其他信息 ═══\n");
            for (String[] e : extras) {
                sb.append(e[0]).append("：").append(trunc(e[1], 200)).append('\n');
            }
        }
        String link = d.path("链接").asText("");
        if (!link.isEmpty()) sb.append("\n链接：").append(link).append('\n');
        return sb.toString();
    }

    private static void appendUrlList(StringBuilder sb, ObjectNode d, String key, int max) {
        JsonNode arr = d.path(key);
        if (!arr.isArray() || arr.isEmpty()) return;
        sb.append("\n═══ ").append(key).append("（").append(arr.size()).append(" 张）═══\n");
        int i = 0;
        for (JsonNode u : arr) {
            if (++i > max) break;
            sb.append("  [").append(i).append("] ").append(u.asText("")).append('\n');
        }
    }

    private static void addIf(List<String> parts, String value) {
        if (value != null && !value.isEmpty()) parts.add(value);
    }

    private static boolean isEmptyNode(JsonNode n) {
        if (n == null || n.isNull() || n.isMissingNode()) return true;
        if (n.isTextual()) return n.asText("").isEmpty();
        if (n.isArray() || n.isObject()) return n.isEmpty();
        return false;
    }

    // ==================== 工具 ====================

    private void setBusy(boolean value) {
        setBusy(value, null);
    }

    private void setBusy(boolean value, String text) {
        busy = value;
        parseBtn.setDisable(value);
        detailBtn.setDisable(value || table.getSelectionModel().getSelectedItem() == null);
        exportBtn.setDisable(value || events.isEmpty());
        if (text != null && !text.isEmpty()) statusLabel.setText(text);
    }

    private void log(String msg) {
        Platform.runLater(() -> statusLabel.setText("⏳ " + msg));
    }

    private void toast(String msg) {
        // BasePage 的 toast 由 MainWindow 注入时再显示；这里通过状态栏兜底
        if (toastSink != null) toastSink.accept(msg);
        else statusLabel.setText(msg);
    }

    private Consumer<String> toastSink;

    @Override
    public void bindToast(Consumer<String> sink) {
        this.toastSink = sink;
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static double firstNumber(String s) {
        if (s == null) return 0;
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\d+(?:\\.\\d+)?").matcher(s);
        return m.find() ? Double.parseDouble(m.group()) : 0;
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }
}
