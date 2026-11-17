package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.net.HttpClient;
import cn.fmjj.toolbox.engine.platform.shopping.Shopping;
import cn.fmjj.toolbox.engine.platform.shopping.TaobaoXlsx;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import javafx.application.Platform;
import javafx.beans.property.ReadOnlyStringWrapper;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.SplitPane;
import javafx.scene.control.TableCell;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableRow;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.control.Tooltip;
import javafx.scene.control.cell.CheckBoxTableCell;
import javafx.scene.image.Image;
import javafx.scene.image.ImageView;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import java.util.function.Function;

/**
 * 购物解析页。移植自 Python 版 gui_qt/pages/shopping_page.py：
 * 淘宝店铺商品列表 + 商品详情（主图/SKU/参数/图文详情）+ 图片下载 + Excel 导出。
 */
public class ShoppingPage extends BasePage {

    private static final String DEFAULT_SHOP_URL =
            "https://f03rr9e3mrh0fdmsfil8w1sdcgp77kl.taobao.com/shop/view_shop.htm";

    private static final class ProductRow {
        final Shopping.ShopProduct pr;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);

        ProductRow(Shopping.ShopProduct pr) {
            this.pr = pr;
        }
    }

    private final Shopping engine = new Shopping();
    private final HttpClient http = new HttpClient();
    private final ObjectMapper mapper = new ObjectMapper();

    private final TextField input = new TextField();
    private final Button parseBtn = new Button("🔍 解析");
    private final Button detailBtn = new Button("🛍 获取选中商品详情");
    private final Button exportBtn = new Button("💾 导出商品 JSON");
    private final Label checkCount = new Label("已勾选 0 件");
    private final TableView<ProductRow> shopTable = new TableView<>();
    private final TableView<List<String>> skuTable = new TableView<>();
    private final TextArea detailText = new TextArea();
    private final ImageView preview = new ImageView();
    private final Label previewHint = new Label("选中商品后显示主图预览");
    private final Button xlsxBtn = new Button("📊 导出单商品 Excel");
    private final Button linkBtn = new Button("🌐 打开商品页面");
    private final Label statusLabel = new Label("");

    private Shopping.ShopInfo shop;
    private List<Shopping.ShopProduct> products = new ArrayList<>();
    private Shopping.TaobaoItemDetail detail;
    private final Map<String, Shopping.TaobaoItemDetail> detailCache =
            java.util.Collections.synchronizedMap(new LinkedHashMap<>());
    private List<String> skuImages = new ArrayList<>();
    private boolean busy;
    private int previewSeq;

    public ShoppingPage() {
        input.setPromptText("留空直接回车 = 默认店铺 · 或输入 https://xxx.taobao.com/shop/view_shop.htm / "
                + "https://item.taobao.com/item.htm?id=123456");
        input.setOnAction(e -> parse());
        parseBtn.setStyle("-fx-background-color:#2e7d32; -fx-text-fill:white; -fx-background-radius:4;");
        parseBtn.setOnAction(e -> parse());
        detailBtn.setDisable(true);
        detailBtn.setTooltip(new Tooltip("勾选了商品→逐件拉取主图/选购参数/参数信息/图文详情"
                + "（图片自动下载、Excel 连带导出到商品目录）；未勾选→只拉当前选中行"));
        detailBtn.setOnAction(e -> fetchDetails());
        exportBtn.setDisable(true);
        exportBtn.setTooltip(new Tooltip("导出勾选的商品为全量 JSON（主图/规格参数/选购参数/"
                + "参数信息/图文详情/本地图片路径）；未拉详情的自动逐件拉取并下载图片"));
        exportBtn.setOnAction(e -> exportJson());
        xlsxBtn.setDisable(true);
        xlsxBtn.setOnAction(e -> exportXlsx());
        linkBtn.setDisable(true);
        linkBtn.setOnAction(e -> openLink());
    }

    @Override
    public String title() {
        return "🛒 购物解析";
    }

    // ==================== 构建 ====================

    @Override
    protected Node build() {
        Label title = new Label(title());
        title.setStyle("-fx-font-size:18px; -fx-font-weight:bold;");
        Button help = new Button("❓ 购物解析 · 功能介绍");
        help.setOnAction(e -> showHelp());
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox titleRow = new HBox(8, title, spacer, help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        HBox.setHgrow(input, Priority.ALWAYS);
        HBox row = new HBox(8, input, parseBtn, detailBtn, exportBtn);
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

        buildShopTable();
        buildSkuTable();
        StackPane left = new StackPane(shopTable, skuTable);
        skuTable.setVisible(false);
        skuTable.setManaged(false);

        detailText.setEditable(false);
        detailText.setWrapText(false);
        detailText.setPromptText("选中左侧商品查看简介；点「获取选中商品详情」拉主图/选购参数/"
                + "参数信息/图文详情");
        preview.setPreserveRatio(true);
        preview.setFitWidth(320);
        preview.setFitHeight(220);
        previewHint.setStyle("-fx-text-fill:#888;");
        StackPane previewBox = new StackPane(preview, previewHint);
        previewBox.setMinHeight(180);
        previewBox.setStyle("-fx-background-color:#f5f6f8; -fx-background-radius:6;");
        HBox rightBtns = new HBox(8, xlsxBtn, linkBtn);
        VBox right = new VBox(4, new Label("商品详情"), detailText, previewBox, rightBtns);
        VBox.setVgrow(detailText, Priority.ALWAYS);

        SplitPane split = new SplitPane(left, right);
        split.setDividerPositions(0.6);
        VBox.setVgrow(split, Priority.ALWAYS);

        statusLabel.setStyle("-fx-text-fill:#888;");
        statusLabel.setWrapText(true);

        VBox root = new VBox(8, titleRow, row, selRow, split, statusLabel);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        return root;
    }

    private void buildShopTable() {
        shopTable.setPlaceholder(new Label("暂无商品，先解析店铺或商品链接"));
        shopTable.setEditable(true);
        shopTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        shopTable.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> onShopSelect());

        TableColumn<ProductRow, Boolean> check = new TableColumn<>("选");
        check.setPrefWidth(40);
        check.setEditable(true);
        check.setSortable(false);
        check.setCellValueFactory(d -> d.getValue().checked);
        check.setCellFactory(CheckBoxTableCell.forTableColumn(check));
        shopTable.getColumns().add(check);

        addShopColumn("商品标题", 360, r -> r.pr.title.isEmpty() ? "-" : r.pr.title,
                Comparator.comparing((String s) -> s.toLowerCase()));
        addShopColumn("价格", 90, r -> r.pr.price, Comparator.comparingDouble(ShoppingPage::firstNumber));
        addShopColumn("销量", 100, r -> r.pr.sales, Comparator.comparingDouble(ShoppingPage::salesNumber));
        addShopColumn("优惠券", 80, r -> r.pr.coupon.isEmpty() ? "" : "-¥" + r.pr.coupon,
                Comparator.comparingDouble(ShoppingPage::firstNumber));
        addShopColumn("商品ID", 130, r -> r.pr.itemId, Comparator.naturalOrder());
        addShopColumn("链接", 60, r -> "打开", Comparator.naturalOrder());

        shopTable.setRowFactory(tv -> {
            TableRow<ProductRow> row = new TableRow<>();
            row.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !row.isEmpty()) browse(row.getItem().pr.url);
            });
            return row;
        });
    }

    private void addShopColumn(String name, double width, Function<ProductRow, String> value,
                               Comparator<String> comparator) {
        TableColumn<ProductRow, String> col = new TableColumn<>(name);
        col.setPrefWidth(width);
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
        shopTable.getColumns().add(col);
    }

    private void buildSkuTable() {
        skuTable.setPlaceholder(new Label("暂无规格数据"));
        skuTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        String[] headers = {"SKU 选项", "价格", "库存"};
        double[] widths = {360, 100, 80};
        for (int i = 0; i < headers.length; i++) {
            final int idx = i;
            TableColumn<List<String>, String> col = new TableColumn<>(headers[i]);
            col.setPrefWidth(widths[i]);
            col.setSortable(false);
            col.setCellValueFactory(d -> new ReadOnlyStringWrapper(
                    d.getValue() != null && idx < d.getValue().size() ? d.getValue().get(idx) : ""));
            skuTable.getColumns().add(col);
        }
        skuTable.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> onSkuSelect());
    }

    private void showHelp() {
        Alert alert = new Alert(Alert.AlertType.INFORMATION);
        alert.setTitle("购物解析 · 功能介绍");
        alert.setHeaderText("购物解析");
        alert.setContentText("""
                · 解析店铺：粘贴淘宝店铺链接（店铺信息游客可获取，商品列表需淘宝 Cookie）；
                  留空回车用默认店铺
                · 解析商品：粘贴 item.taobao.com/item.htm?id=xxx 链接（详情需淘宝 Cookie）
                · 表格：勾选商品（全选/反选/清除）后点「获取选中商品详情」，
                  逐件拉取主图/选购参数/参数信息/图文详情——图片自动下载到
                  <导出目录>/淘宝商品/{标题}_{商品ID}_{时间}/，Excel 连带导出到同目录
                · 💾 导出商品 JSON：输出勾选商品的全量数据；未拉过详情的自动逐件拉取
                · 单件失败原地重试不中断整批；详情缓存复用，重复导出不重复请求
                · 🍪 淘宝 Cookie 由侧栏「Cookie 管理」维护
                """);
        alert.getDialogPane().setPrefWidth(620);
        alert.showAndWait();
    }

    // ==================== 解析 ====================

    private void parse() {
        if (busy) return;
        String text = input.getText() == null ? "" : input.getText().strip();
        if (text.isEmpty()) {
            input.setText(DEFAULT_SHOP_URL);
            text = DEFAULT_SHOP_URL;
        }
        String url = text;
        String cookie = CookieStore.get("taobao");
        String[] kind = Shopping.detectShoppingPlatform(url);
        if (kind != null && "taobao_item".equals(kind[0])) {
            parseItem(url, cookie);
            return;
        }
        parseShop(url, cookie);
    }

    private void parseShop(String url, String cookie) {
        setBusy(true, "⏳ 解析中...");
        Thread t = new Thread(() -> {
            try {
                Shopping.ShopResult result = engine.parseShoppingUrl(url, cookie, 60, this::log);
                Platform.runLater(() -> applyShop(result));
            } catch (Exception e) {
                Platform.runLater(() -> {
                    setBusy(false);
                    showParseError(e);
                });
            }
        }, "shopping-shop");
        t.setDaemon(true);
        t.start();
    }

    private void applyShop(Shopping.ShopResult result) {
        setBusy(false);
        shop = result.shop();
        products = result.products();
        detail = null;
        detailCache.clear();
        skuTable.setVisible(false);
        skuTable.setManaged(false);
        shopTable.setVisible(true);
        shopTable.setManaged(true);
        detailText.clear();
        clearPreview("选中商品后显示主图预览");
        xlsxBtn.setDisable(true);
        linkBtn.setDisable(true);
        fillShopTable();
        if (products.isEmpty()) {
            statusLabel.setText("✅ 店铺信息获取成功（未配置淘宝 Cookie，或店铺暂无在售商品）");
        } else {
            statusLabel.setText("✅ " + (shop.shopName.isEmpty() ? "店铺" : shop.shopName)
                    + "：获取到 " + products.size() + " 件商品；勾选后「导出商品 JSON」全量导出，"
                    + "或选中一行拉详情");
        }
        exportBtn.setDisable(products.isEmpty());
    }

    private void parseItem(String url, String cookie) {
        if (cookie == null || cookie.isBlank()) {
            statusLabel.setText("❌ 淘宝商品详情需要登录：请在 Cookie 管理中添加淘宝 Cookie");
            toast("淘宝商品详情需要登录 Cookie");
            return;
        }
        setBusy(true, "⏳ 解析商品详情中...");
        Thread t = new Thread(() -> {
            try {
                Shopping.TaobaoItemDetail d = engine.parseTaobaoItemUrl(url, cookie, this::log);
                Path base = Settings.load().effectiveExportDir().resolve("淘宝商品");
                engine.downloadItemImages(d, base, this::log);
                Path xlsx = Shopping.defaultItemXlsxPath(d, base);
                TaobaoXlsx.export(d, xlsx.toString());
                Platform.runLater(() -> applyItem(d));
            } catch (Exception e) {
                Platform.runLater(() -> {
                    setBusy(false);
                    showParseError(e);
                });
            }
        }, "shopping-item");
        t.setDaemon(true);
        t.start();
    }

    private void applyItem(Shopping.TaobaoItemDetail d) {
        setBusy(false);
        detail = d;
        shop = null;
        products = new ArrayList<>();
        detailCache.put(d.itemId, d);
        shopTable.setVisible(false);
        shopTable.setManaged(false);
        skuTable.setVisible(true);
        skuTable.setManaged(true);
        fillSkuTable(d);
        showDetail(d);
        String imgs = "主图 " + (d.localMain.isEmpty() ? d.mainImages.size() : d.localMain.size())
                + " / 规格 " + d.localSku.size()
                + " / 详情 " + (d.localDesc.isEmpty() ? d.descImages.size() : d.localDesc.size());
        statusLabel.setText("✅ " + trunc(d.title, 40) + "｜图片已下载（" + imgs
                + "），Excel 已连带导出到商品目录");
        exportBtn.setDisable(true);
    }

    private void showParseError(Exception e) {
        String msg = e.getMessage() == null ? "" : e.getMessage();
        if (msg.contains("SESSION_EXPIRED") || msg.contains("登录页") || msg.contains("RGV587")
                || msg.contains("风控") || msg.contains("Cookie")) {
            statusLabel.setText("❌ " + trunc(msg, 300) + "  → 修复：左下角 ⚙️ 全局设置 → Cookie 管理 →"
                    + " 淘宝 →「浏览器」重新登录");
        } else {
            statusLabel.setText("❌ " + trunc(msg, 300));
        }
        toast("解析失败，详见页面底部状态栏");
    }

    // ==================== 表格 ====================

    private void fillShopTable() {
        List<ProductRow> rows = new ArrayList<>();
        for (Shopping.ShopProduct pr : products) {
            ProductRow r = new ProductRow(pr);
            r.checked.addListener((o, a, b) -> updateCheckCount());
            rows.add(r);
        }
        shopTable.setItems(FXCollections.observableArrayList(rows));
        shopTable.getSortOrder().clear();
        updateCheckCount();
        detailBtn.setDisable(true);
    }

    private void fillSkuTable(Shopping.TaobaoItemDetail d) {
        List<String> imageUrls = new ArrayList<>();
        for (ObjectNode grp : d.skuProps) {
            for (JsonNode v : grp.path("values")) imageUrls.add(v.path("image").asText(""));
        }
        skuImages = new ArrayList<>();
        for (int i = 0; i < d.skus.size(); i++) {
            String img = i < imageUrls.size() ? imageUrls.get(i) : "";
            if (img.isEmpty() && !d.mainImages.isEmpty()) img = d.mainImages.get(0);
            skuImages.add(img);
        }
        List<List<String>> rows = new ArrayList<>();
        for (Shopping.ItemSku s : d.skus) {
            rows.add(List.of(s.names.isEmpty() ? "-" : s.names, s.price, s.quantity));
        }
        skuTable.setItems(FXCollections.observableArrayList(rows));
    }

    private void updateCheckCount() {
        int n = 0;
        for (ProductRow r : shopTable.getItems()) if (r.checked.get()) n++;
        checkCount.setText("已勾选 " + n + " 件");
    }

    private void setAllChecks(boolean value) {
        for (ProductRow r : shopTable.getItems()) r.checked.set(value);
        updateCheckCount();
    }

    private void invertChecks() {
        for (ProductRow r : shopTable.getItems()) r.checked.set(!r.checked.get());
        updateCheckCount();
    }

    private List<ProductRow> checkedRows() {
        List<ProductRow> out = new ArrayList<>();
        for (ProductRow r : shopTable.getItems()) if (r.checked.get()) out.add(r);
        return out;
    }

    // ==================== 选中 / 详情 ====================

    private void onShopSelect() {
        ProductRow row = shopTable.getSelectionModel().getSelectedItem();
        detailBtn.setDisable(row == null || detail != null);
        if (row == null) {
            linkBtn.setDisable(true);
            return;
        }
        Shopping.TaobaoItemDetail cached = detailCache.get(row.pr.itemId);
        if (cached != null) {
            detailText.setText(formatDetail(cached));
            xlsxBtn.setDisable(false);
            previewFromDetail(cached);
        } else {
            detailText.setText(formatSummary(row.pr));
            xlsxBtn.setDisable(true);
            previewUrl(row.pr.pic, false);
        }
        linkBtn.setDisable(row.pr.url.isEmpty());
    }

    private void onSkuSelect() {
        if (detail == null) return;
        int idx = skuTable.getSelectionModel().getSelectedIndex();
        if (idx >= 0 && idx < skuImages.size()) previewUrl(skuImages.get(idx), false);
        linkBtn.setDisable(detail.url.isEmpty());
    }

    private void fetchDetails() {
        if (busy || detail != null || products.isEmpty()) return;
        List<ProductRow> rows = checkedRows();
        if (rows.isEmpty()) {
            ProductRow sel = shopTable.getSelectionModel().getSelectedItem();
            if (sel != null) rows = List.of(sel);
        }
        if (rows.isEmpty()) return;
        List<Shopping.ShopProduct> todo = new ArrayList<>();
        for (ProductRow r : rows) if (!detailCache.containsKey(r.pr.itemId)) todo.add(r.pr);
        if (todo.isEmpty()) {
            showCached(rows);
            return;
        }
        int total = todo.size();
        setBusy(true, "⏳ 获取 " + total + " 件商品详情（件间隔 3 秒防风控）...");
        List<ProductRow> rowsFinal = rows;
        Thread t = new Thread(() -> {
            String cookie = CookieStore.get("taobao");
            Path base = Settings.load().effectiveExportDir().resolve("淘宝商品");
            List<Shopping.TaobaoItemDetail> done = new ArrayList<>();
            List<String> failed = new ArrayList<>();
            for (int i = 0; i < todo.size(); i++) {
                Shopping.ShopProduct pr = todo.get(i);
                log("详情进度 " + (i + 1) + "/" + total + "：" + trunc(pr.title, 26));
                if (i > 0) sleep(3000);
                for (int attempt = 1; attempt <= 2; attempt++) {
                    try {
                        Shopping.TaobaoItemDetail d = engine.fetchItemDetail(pr.itemId, cookie, this::log);
                        engine.downloadItemImages(d, base, this::log);
                        Path xlsx = Shopping.defaultItemXlsxPath(d, base);
                        TaobaoXlsx.export(d, xlsx.toString());
                        log("  Excel 已导出：" + xlsx.getFileName());
                        done.add(d);
                        break;
                    } catch (Exception e) {
                        if (attempt == 1) {
                            log("  " + trunc(pr.title, 16) + " 失败，10 秒后重试...");
                            sleep(10000);
                        } else {
                            failed.add(pr.title + "：" + trunc(e.getMessage(), 60));
                        }
                    }
                }
            }
            Platform.runLater(() -> {
                setBusy(false);
                for (Shopping.TaobaoItemDetail d : done) detailCache.put(d.itemId, d);
                showCached(rowsFinal);
                String tip = failed.isEmpty() ? "" : "，" + failed.size() + " 件失败";
                statusLabel.setText("✅ 已获取 " + done.size() + " 件详情并连带导出 Excel" + tip
                        + "（<导出目录>/淘宝商品/，每件一个目录）");
            });
        }, "shopping-detail");
        t.setDaemon(true);
        t.start();
    }

    private void showCached(List<ProductRow> rows) {
        ProductRow sel = shopTable.getSelectionModel().getSelectedItem();
        Shopping.TaobaoItemDetail d = sel == null ? null : detailCache.get(sel.pr.itemId);
        if (d == null) {
            for (ProductRow r : rows) {
                d = detailCache.get(r.pr.itemId);
                if (d != null) break;
            }
        }
        if (d != null) showDetail(d);
    }

    private void showDetail(Shopping.TaobaoItemDetail d) {
        detailText.setText(formatDetail(d));
        xlsxBtn.setDisable(false);
        linkBtn.setDisable(d.url.isEmpty());
        previewFromDetail(d);
    }

    private void previewFromDetail(Shopping.TaobaoItemDetail d) {
        String url = !d.localMain.isEmpty() ? d.localMain.get(0)
                : (d.mainImages.isEmpty() ? "" : d.mainImages.get(0));
        previewUrl(url, !d.localMain.isEmpty());
    }

    private void openLink() {
        String url = detail != null ? detail.url : "";
        if (url.isEmpty()) {
            ProductRow row = shopTable.getSelectionModel().getSelectedItem();
            if (row != null) url = row.pr.url;
        }
        if (!url.isEmpty()) browse(url);
    }

    private void exportXlsx() {
        Shopping.TaobaoItemDetail d = detail;
        if (d == null) {
            ProductRow row = shopTable.getSelectionModel().getSelectedItem();
            if (row != null) d = detailCache.get(row.pr.itemId);
        }
        if (d == null) {
            toast("请先点「获取选中商品详情」拉取商品详情，再导出 Excel");
            return;
        }
        try {
            Path base = Settings.load().effectiveExportDir().resolve("淘宝商品");
            Path path = Shopping.defaultItemXlsxPath(d, base);
            TaobaoXlsx.export(d, path.toString());
            statusLabel.setText("✅ 已导出单商品 Excel：" + path);
        } catch (Exception e) {
            toast("导出失败: " + trunc(e.getMessage(), 80));
        }
    }

    // ==================== 导出 JSON ====================

    private void exportJson() {
        if (busy || products.isEmpty()) return;
        List<ProductRow> checked = checkedRows();
        if (checked.isEmpty()) {
            toast("请先勾选要导出的商品（表格第一列复选框），或点「☑ 全选」");
            return;
        }
        FileChooser chooser = new FileChooser();
        chooser.setTitle("导出商品 JSON（全量）");
        chooser.setInitialFileName("淘宝商品_已选" + checked.size() + "件.json");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("JSON", "*.json"));
        File target = chooser.showSaveDialog(input.getScene() == null ? null : input.getScene().getWindow());
        if (target == null) return;
        int total = checked.size();
        setBusy(true, "⏳ 导出 " + total + " 件商品...");
        Path path = target.toPath();
        Thread t = new Thread(() -> {
            String cookie = CookieStore.get("taobao");
            Path base = Settings.load().effectiveExportDir().resolve("淘宝商品");
            List<ObjectNode> items = new ArrayList<>();
            List<String> failed = new ArrayList<>();
            int seq = 0;
            for (ProductRow row : checked) {
                seq++;
                Shopping.ShopProduct pr = row.pr;
                log("导出进度 " + seq + "/" + total + "：" + trunc(pr.title, 26));
                if (seq > 1) sleep(3000);
                Shopping.TaobaoItemDetail d = detailCache.get(pr.itemId);
                if (d == null) {
                    for (int attempt = 1; attempt <= 2; attempt++) {
                        try {
                            d = engine.fetchItemDetail(pr.itemId, cookie, this::log);
                            engine.downloadItemImages(d, base, this::log);
                            Path xlsx = Shopping.defaultItemXlsxPath(d, base);
                            TaobaoXlsx.export(d, xlsx.toString());
                            detailCache.put(pr.itemId, d);
                            break;
                        } catch (Exception e) {
                            d = null;
                            if (attempt == 1) {
                                log("  " + trunc(pr.title, 16) + " 失败，10 秒后重试...");
                                sleep(10000);
                            } else {
                                failed.add(trunc(pr.title, 20) + "：" + trunc(e.getMessage(), 60));
                            }
                        }
                    }
                }
                if (d != null) {
                    items.add(d.toDict());
                } else {
                    ObjectNode rowJson = pr.toDict();
                    rowJson.put("详情获取失败", "");
                    items.add(rowJson);
                }
            }
            ObjectNode data = mapper.createObjectNode();
            data.set("店铺", shop == null ? mapper.createObjectNode() : shop.toDict());
            data.put("商品数", items.size());
            var arr = data.putArray("商品");
            items.forEach(arr::add);
            if (!failed.isEmpty()) {
                var warn = data.putArray("导出告警");
                failed.forEach(warn::add);
            }
            try {
                Files.writeString(path, mapper.writerWithDefaultPrettyPrinter()
                        .writeValueAsString(data), StandardCharsets.UTF_8);
            } catch (Exception e) {
                Platform.runLater(() -> {
                    setBusy(false);
                    toast("导出失败: " + trunc(e.getMessage(), 80));
                });
                return;
            }
            Platform.runLater(() -> {
                setBusy(false);
                String tip = failed.isEmpty() ? "" : "，" + failed.size() + " 件详情获取失败";
                statusLabel.setText("✅ 已导出 " + items.size() + " 件商品全量 JSON" + tip + "到 " + path);
            });
        }, "shopping-export");
        t.setDaemon(true);
        t.start();
    }

    // ==================== 详情文本 ====================

    private String formatSummary(Shopping.ShopProduct pr) {
        StringBuilder sb = new StringBuilder();
        sb.append("标题：").append(pr.title.isEmpty() ? "-" : pr.title).append('\n');
        if (!pr.price.isEmpty()) sb.append("价格：").append(pr.price).append('\n');
        if (!pr.reservePrice.isEmpty() && !pr.reservePrice.equals(pr.price)) {
            sb.append("原价：").append(pr.reservePrice).append('\n');
        }
        if (!pr.sales.isEmpty()) sb.append("销量：").append(pr.sales).append('\n');
        if (!pr.coupon.isEmpty()) sb.append("优惠券：减 ¥").append(pr.coupon).append('\n');
        sb.append("商品ID：").append(pr.itemId).append("\n\n");
        sb.append("点「🛍 获取选中商品详情」拉取\n");
        sb.append("主图 / 选购参数 / 参数信息 / 图文详情（图片自动下载）\n");
        return sb.toString();
    }

    private String formatDetail(Shopping.TaobaoItemDetail d) {
        StringBuilder sb = new StringBuilder();
        sb.append("标题：").append(d.title.isEmpty() ? "-" : d.title).append('\n');
        if (!d.price.isEmpty()) sb.append("价格：").append(d.price).append('\n');
        if (!d.sales.isEmpty()) sb.append("销量：").append(d.sales).append('\n');
        if (!d.shopName.isEmpty()) sb.append("店铺：").append(d.shopName).append('\n');
        if (!d.delivery.isEmpty()) sb.append("物流：").append(d.delivery).append('\n');
        if (!d.shopScores.isEmpty()) sb.append("店铺评分：").append(d.shopScores).append('\n');
        if (!d.skuProps.isEmpty()) {
            sb.append("\n═══ 规格参数 ═══\n");
            for (ObjectNode grp : d.skuProps) {
                List<String> vals = new ArrayList<>();
                for (JsonNode v : grp.path("values")) vals.add(v.path("name").asText(""));
                sb.append("  ").append(grp.path("name").asText("规格")).append("：")
                        .append(String.join(" / ", vals)).append('\n');
            }
        }
        if (!d.skus.isEmpty()) {
            sb.append("\n═══ 选购参数（").append(d.skus.size()).append(" 档）═══\n");
            for (Shopping.ItemSku s : d.skus) {
                List<String> parts = new ArrayList<>();
                parts.add(s.names.isEmpty() ? "-" : s.names);
                if (!s.price.isEmpty()) parts.add(s.price);
                if (!s.quantity.isEmpty()) parts.add("库存 " + s.quantity);
                sb.append("  · ").append(String.join(" | ", parts)).append('\n');
            }
        }
        if (!d.params.isEmpty()) {
            sb.append("\n═══ 参数信息（").append(d.params.size()).append(" 项）═══\n");
            for (String[] p : d.params) sb.append("  ").append(p[0]).append("：").append(p[1]).append('\n');
        }
        if (!d.mainImages.isEmpty()) {
            sb.append("\n═══ 主图（").append(d.mainImages.size()).append(" 张）═══\n");
            for (int i = 0; i < Math.min(8, d.mainImages.size()); i++) {
                sb.append("  [").append(i + 1).append("] ").append(d.mainImages.get(i)).append('\n');
            }
        }
        if (!d.descImages.isEmpty()) {
            sb.append("\n═══ 图文详情（").append(d.descImages.size()).append(" 张长图）═══\n");
            for (int i = 0; i < Math.min(10, d.descImages.size()); i++) {
                sb.append("  [").append(i + 1).append("] ").append(d.descImages.get(i)).append('\n');
            }
        }
        sb.append("\n已下载图片：主图 ").append(d.localMain.size())
                .append(" / 规格 ").append(d.localSku.size())
                .append(" / 详情 ").append(d.localDesc.size()).append('\n');
        sb.append("链接：").append(d.url).append('\n');
        return sb.toString();
    }

    // ==================== 图片预览 ====================

    private void previewUrl(String url, boolean local) {
        int seq = ++previewSeq;
        if (url == null || url.isEmpty()) {
            clearPreview("无图片");
            return;
        }
        if (local) {
            try {
                Image img = new Image(Path.of(url).toUri().toString(), false);
                if (img.isError()) {
                    clearPreview("图片加载失败");
                } else {
                    showImage(img);
                }
            } catch (Exception e) {
                clearPreview("图片加载失败");
            }
            return;
        }
        clearPreview("图片加载中...");
        Thread t = new Thread(() -> {
            try {
                HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                        .header("User-Agent", "Mozilla/5.0")
                        .header("Referer", "https://www.taobao.com/")
                        .GET().build();
                byte[] data = http.jdkClient().send(req,
                        HttpResponse.BodyHandlers.ofByteArray()).body();
                Platform.runLater(() -> {
                    if (seq != previewSeq) return;
                    Image img = new Image(new ByteArrayInputStream(data));
                    if (img.isError()) clearPreview("图片解析失败");
                    else showImage(img);
                });
            } catch (Exception e) {
                Platform.runLater(() -> {
                    if (seq == previewSeq) clearPreview("图片加载失败");
                });
            }
        }, "shopping-preview");
        t.setDaemon(true);
        t.start();
    }

    private void showImage(Image img) {
        preview.setImage(img);
        previewHint.setText("");
    }

    private void clearPreview(String hint) {
        preview.setImage(null);
        previewHint.setText(hint);
    }

    // ==================== 工具 ====================

    private void setBusy(boolean value) {
        setBusy(value, null);
    }

    private void setBusy(boolean value, String text) {
        busy = value;
        parseBtn.setDisable(value);
        detailBtn.setDisable(value || detail != null
                || shopTable.getSelectionModel().getSelectedItem() == null);
        exportBtn.setDisable(value || products.isEmpty());
        xlsxBtn.setDisable(value || (detail == null && detailCache.isEmpty()));
        if (text != null && !text.isEmpty()) statusLabel.setText(text);
    }

    private void log(String msg) {
        Platform.runLater(() -> statusLabel.setText("⏳ " + msg));
    }

    private void toast(String msg) {
        if (toastSink != null) toastSink.accept(msg);
        else statusLabel.setText(msg);
    }

    private Consumer<String> toastSink;

    @Override
    public void bindToast(Consumer<String> sink) {
        this.toastSink = sink;
    }

    private static void browse(String url) {
        try {
            java.awt.Desktop.getDesktop().browse(URI.create(url));
        } catch (Exception ignored) {
        }
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
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\d+(?:\\.\\d+)?")
                .matcher(s.replace("万", "").replace("+", ""));
        return m.find() ? Double.parseDouble(m.group()) : 0;
    }

    private static double salesNumber(String s) {
        if (s == null) return 0;
        double n = firstNumber(s);
        return s.contains("万") ? n * 10000 : n;
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }
}
