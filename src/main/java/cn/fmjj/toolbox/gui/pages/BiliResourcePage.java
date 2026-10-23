package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.bili.BiliResource;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.image.ImageView;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 🎁 B站资源：空间信息 + 综合搜索（对应 Python 版 bili_resource 的两个 tab）。
 *
 * <p>已实现 2/8 个 tab（空间、综合搜索）；表情包×3、装扮×3 随 M6 后续。
 */
public class BiliResourcePage extends BasePage {

    private final BiliResource resource = new BiliResource();
    private final FileDownloader downloader = new FileDownloader();

    // 空间 tab
    private final TextField midField = new TextField();
    private final Label spaceInfo = new Label("输入 UID 加载");
    private final ImageView faceView = new ImageView();
    private final Label spaceStatus = new Label();

    // 搜索 tab
    private final TextField keywordField = new TextField();
    private final TableView<BiliResource.SearchItem> searchTable = new TableView<>();
    private final Label searchStatus = new Label();

    private Consumer<String> toast;

    @Override
    public String title() { return "🎁 B站资源"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        TabPane tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        // 顺序对齐原版 _FLAT：表情包搜索/装扮搜索/装扮表情包/直播表情/装扮ID详情/收藏集/空间/综合搜索
        tabs.getTabs().addAll(
                buildEmoteTab(),
                stubTab("🔍 装扮搜索", "关键词搜装扮/收藏集（白菜、UP主名等），无需登录",
                        "需移植 bili_garb.py（623 行）：装扮搜索接口"),
                stubTab("🆔 装扮表情包", "输入表情包/装扮 ID 下载，自动识别普通表情包 → 装扮表情 → 收藏集",
                        "需移植 bili_emoji.py：ID 解析与三态识别"),
                stubTab("📺 直播表情", "输入房间号加载直播间专属表情 / UP主大表情 / 通用表情",
                        "需移植 bili_emoji.py：直播间表情接口"),
                stubTab("👕 装扮ID详情", "输入装扮 item_id，加载套装全部资源（背景/粉丝牌/评论背景/表情/加载动画等）",
                        "需移植 bili_garb.py：装扮详情接口"),
                stubTab("🎁 收藏集", "输入活动 act_id，加载活动视觉 / 勋章 / 全部收藏集卡牌图与卡牌视频",
                        "需移植 bili_garb.py：收藏集接口"),
                buildSpaceTab(), buildSearchTab());

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("8 个 tab 与原版一致：表情包搜索 / 装扮搜索 / 装扮表情包 / 直播表情 / "
                + "装扮ID详情 / 收藏集 / 空间 / 综合搜索。当前可用：**空间**（免签名，头像/背景/头像框下载）"
                + "与**综合搜索**（WBI 签名）；其余 6 个 tab 待移植 bili_emoji / bili_garb。");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, tabs);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(tabs, Priority.ALWAYS);
        return root;
    }

    // ==================== 🗃 表情包搜索（已实现） ====================

    private final java.util.List<cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Package> emotePkgs =
            new java.util.ArrayList<>();
    private final javafx.scene.layout.FlowPane emoteGrid = new javafx.scene.layout.FlowPane(8, 8);
    private final Label emoteStatus = new Label("");
    private final TextField emoteSearchField = new TextField();
    private final ComboBox<String> emotePkgBox = new ComboBox<>();
    private final cn.fmjj.toolbox.engine.platform.bili.BiliEmote emoteApi =
            new cn.fmjj.toolbox.engine.platform.bili.BiliEmote();

    private Tab buildEmoteTab() {
        emoteSearchField.setPromptText("按表情名/包名搜索");
        emoteSearchField.setPrefWidth(220);
        emoteSearchField.setOnAction(e -> renderEmotes());
        emoteSearchField.textProperty().addListener((o, a, b) -> renderEmotes());
        emotePkgBox.setPrefWidth(240);
        emotePkgBox.setOnAction(e -> renderEmotes());
        Button load = new Button("🔄 加载表情包");
        load.setOnAction(e -> loadEmotes(load));
        Button dlAll = new Button("⬇ 下载当前列表");
        dlAll.setOnAction(e -> downloadAllEmotes());
        emoteStatus.setStyle("-fx-text-fill: #888;");

        HBox bar = new HBox(6, load, emotePkgBox, emoteSearchField, dlAll, emoteStatus);
        bar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(emoteStatus, Priority.ALWAYS);

        ScrollPane scroll = new ScrollPane(emoteGrid);
        scroll.setFitToWidth(true);
        VBox.setVgrow(scroll, Priority.ALWAYS);
        emoteGrid.setPadding(new Insets(8));

        VBox box = new VBox(6, bar, scroll);
        box.setPadding(new Insets(10));
        return new Tab("🗃 表情包搜索", box);
    }

    private void loadEmotes(Button btn) {
        btn.setDisable(true);
        emoteStatus.setText("加载中…");
        javafx.concurrent.Task<java.util.List<cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Package>> task =
                new javafx.concurrent.Task<>() {
                    @Override
                    protected java.util.List<cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Package> call()
                            throws Exception {
                        return emoteApi.fetchPackages(CookieStore.get("bilibili"));
                    }
                };
        task.setOnSucceeded(e -> {
            btn.setDisable(false);
            emotePkgs.clear();
            emotePkgs.addAll(task.getValue());
            var names = new java.util.ArrayList<String>();
            names.add("全部（" + cn.fmjj.toolbox.engine.platform.bili.BiliEmote.flatten(emotePkgs).size() + "）");
            for (var p : emotePkgs) names.add(p.name() + "（" + p.emotes().size() + "）");
            emotePkgBox.getItems().setAll(names);
            emotePkgBox.getSelectionModel().selectFirst();
            renderEmotes();
            emoteStatus.setText("已加载 " + emotePkgs.size() + " 个表情包");
        });
        task.setOnFailed(e -> {
            btn.setDisable(false);
            emoteStatus.setText("加载失败：" + task.getException().getMessage());
        });
        new Thread(task, "bili-emote").start();
    }

    private void renderEmotes() {
        emoteGrid.getChildren().clear();
        if (emotePkgs.isEmpty()) {
            Label hint = new Label("点「🔄 加载表情包」拉取（需 B站 登录 Cookie）");
            hint.setStyle("-fx-text-fill: #888;");
            emoteGrid.getChildren().add(hint);
            return;
        }
        int pkgIdx = emotePkgBox.getSelectionModel().getSelectedIndex();
        java.util.List<cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Emote> list;
        String kw = emoteSearchField.getText();
        if (kw != null && !kw.isBlank()) {
            list = cn.fmjj.toolbox.engine.platform.bili.BiliEmote.search(emotePkgs, kw);
        } else if (pkgIdx > 0 && pkgIdx <= emotePkgs.size()) {
            list = emotePkgs.get(pkgIdx - 1).emotes();
        } else {
            list = cn.fmjj.toolbox.engine.platform.bili.BiliEmote.flatten(emotePkgs);
        }
        int shown = 0;
        for (var em : list) {
            if (shown++ >= 300) break;   // 只渲染前 300 个，避免卡顿
            emoteGrid.getChildren().add(emoteCell(em));
        }
        emoteStatus.setText("显示 " + Math.min(list.size(), 300) + " / " + list.size() + " 个表情");
    }

    private Node emoteCell(cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Emote em) {
        Label name = new Label(em.text());
        name.setStyle("-fx-font-size: 10px;");
        name.setWrapText(true);
        name.setMaxWidth(80);
        javafx.scene.image.ImageView iv = new javafx.scene.image.ImageView();
        iv.setFitWidth(56);
        iv.setFitHeight(56);
        iv.setPreserveRatio(true);
        VBox cell = new VBox(2, iv, name);
        cell.setAlignment(Pos.CENTER);
        cell.setPrefWidth(88);
        cell.setStyle("-fx-border-color: #eee; -fx-border-radius: 4; -fx-padding: 4;");
        cell.setOnMouseClicked(e -> downloadEmote(em));
        javafx.scene.control.Tooltip.install(cell, new javafx.scene.control.Tooltip("点击下载：" + em.text()));
        // 异步加载缩略图
        new Thread(() -> {
            try {
                var img = new javafx.scene.image.Image(em.bestUrl(), true);
                javafx.application.Platform.runLater(() -> iv.setImage(img));
            } catch (Exception ignored) {
            }
        }, "emote-thumb").start();
        return cell;
    }

    private void downloadEmote(cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Emote em) {
        java.nio.file.Path dir = cn.fmjj.toolbox.core.settings.Settings.load()
                .effectiveExportDir().resolve("B站资源").resolve("表情包")
                .resolve(cn.fmjj.toolbox.engine.platform.bili.BiliEmote.safeName(em, 0)
                        .replaceAll("\\.[a-z]+$", ""));
        new Thread(() -> {
            var r = new cn.fmjj.toolbox.engine.download.FileDownloader().download(
                    java.util.List.of(em.bestUrl()),
                    dir.resolve(cn.fmjj.toolbox.engine.platform.bili.BiliEmote.safeName(em, 0)),
                    java.util.Map.of("Referer", "https://www.bilibili.com/"), p -> { }, () -> false);
            javafx.application.Platform.runLater(() -> emoteStatus.setText(
                    r.success() ? "已下载：" + em.text() : "下载失败：" + em.text()));
        }, "emote-dl").start();
    }

    private void downloadAllEmotes() {
        int pkgIdx = emotePkgBox.getSelectionModel().getSelectedIndex();
        java.util.List<cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Emote> list;
        String kw = emoteSearchField.getText();
        if (kw != null && !kw.isBlank()) {
            list = cn.fmjj.toolbox.engine.platform.bili.BiliEmote.search(emotePkgs, kw);
        } else if (pkgIdx > 0 && pkgIdx <= emotePkgs.size()) {
            list = emotePkgs.get(pkgIdx - 1).emotes();
        } else {
            list = cn.fmjj.toolbox.engine.platform.bili.BiliEmote.flatten(emotePkgs);
        }
        if (list.isEmpty()) { emoteStatus.setText("没有可下载的表情"); return; }
        emoteStatus.setText("开始下载 " + list.size() + " 个表情…");
        java.util.List<cn.fmjj.toolbox.engine.platform.bili.BiliEmote.Emote> batch = list;
        new Thread(() -> {
            int ok = 0, fail = 0;
            var dl = new cn.fmjj.toolbox.engine.download.FileDownloader();
            for (int i = 0; i < batch.size(); i++) {
                var em = batch.get(i);
                java.nio.file.Path dir = cn.fmjj.toolbox.core.settings.Settings.load()
                        .effectiveExportDir().resolve("B站资源").resolve("表情包")
                        .resolve(em.packageName().replaceAll("[\\\\/:*?\"<>|]", "_"));
                var r = dl.download(java.util.List.of(em.bestUrl()),
                        dir.resolve(cn.fmjj.toolbox.engine.platform.bili.BiliEmote.safeName(em, i)),
                        java.util.Map.of("Referer", "https://www.bilibili.com/"), p -> { }, () -> false);
                if (r.success()) ok++; else fail++;
                int n = i + 1;
                javafx.application.Platform.runLater(() ->
                        emoteStatus.setText("下载中… " + n + "/" + batch.size()));
            }
            int fok = ok, ffail = fail;
            javafx.application.Platform.runLater(() -> {
                emoteStatus.setText("下载完成：成功 " + fok + "，失败 " + ffail);
                toast("表情包下载完成：" + fok + " 个");
            });
        }, "emote-dl-all").start();
    }

    /** 未实现 tab 的占位：明确说明缺什么，不放假控件。 */
    private Tab stubTab(String name, String what, String needs) {
        Label title = new Label(what);
        title.setWrapText(true);
        title.setStyle("-fx-font-size: 13px;");
        Label need = new Label("⚠️ 未实现：" + needs);
        need.setWrapText(true);
        need.setStyle("-fx-text-fill: #c62828;");
        VBox box = new VBox(10, title, need);
        box.setPadding(new Insets(16));
        return new Tab(name, box);
    }

    // ==================== 空间 ====================

    private Tab buildSpaceTab() {
        midField.setPromptText("B站 UID，如 12345");
        midField.setPrefWidth(200);
        Button load = new Button("🔍 加载");
        load.setOnAction(e -> loadSpace(load));
        Button download = new Button("⬇ 下载头像/背景/头像框");
        download.setOnAction(e -> downloadSpace());

        faceView.setFitWidth(120);
        faceView.setFitHeight(120);
        faceView.setPreserveRatio(true);
        spaceInfo.setWrapText(true);
        spaceStatus.setWrapText(true);

        HBox top = new HBox(8, new Label("UID"), midField, load, download);
        top.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(spaceStatus, Priority.ALWAYS);

        HBox body = new HBox(14, faceView, spaceInfo);
        body.setAlignment(Pos.TOP_LEFT);
        HBox.setHgrow(spaceInfo, Priority.ALWAYS);

        VBox box = new VBox(12, top, body, spaceStatus);
        box.setPadding(new Insets(12));
        Tab tab = new Tab("👤 空间", box);
        return tab;
    }

    private void loadSpace(Button btn) {
        String mid = midField.getText() == null ? "" : midField.getText().trim();
        if (mid.isEmpty()) { spaceStatus.setText("请输入 UID"); return; }
        btn.setDisable(true);
        spaceStatus.setText("加载中…");
        javafx.concurrent.Task<BiliResource.SpaceCard> task = new javafx.concurrent.Task<>() {
            @Override
            protected BiliResource.SpaceCard call() throws Exception {
                return resource.spaceCard(mid, CookieStore.get("bilibili"));
            }
        };
        task.setOnSucceeded(e -> {
            btn.setDisable(false);
            var c = task.getValue();
            this.currentSpace = c;
            spaceInfo.setText("昵称：" + c.name() + "\nUID：" + c.mid() + "\n等级：Lv" + c.level()
                    + "\n粉丝：" + c.fansText() + "　关注：" + c.attention() + "　获赞：" + c.likes()
                    + "\n签名：" + (c.sign().isEmpty() ? "（无）" : c.sign()));
            spaceStatus.setText("已加载：" + c.name());
            if (!c.face().isEmpty()) {
                faceView.setImage(new javafx.scene.image.Image(
                        c.face().startsWith("//") ? "https:" + c.face() : c.face(), true));
            }
        });
        task.setOnFailed(e -> {
            btn.setDisable(false);
            spaceStatus.setText("加载失败：" + task.getException().getMessage());
        });
        new Thread(task, "bili-space").start();
    }

    private BiliResource.SpaceCard currentSpace;

    private void downloadSpace() {
        if (currentSpace == null) { spaceStatus.setText("请先加载空间"); return; }
        var files = BiliResource.spaceFiles(currentSpace);
        if (files.isEmpty()) { spaceStatus.setText("没有可下载的资源"); return; }
        Path dir = Settings.load().effectiveExportDir().resolve("B站资源")
                .resolve(BiliResource.stripHtml(currentSpace.name()));
        spaceStatus.setText("下载中…");
        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (Map.Entry<String, String> f : files) {
                    String name = f.getKey();
                    String url = f.getValue();
                    String ext = url.contains(".png") ? ".png" : url.contains(".gif") ? ".gif" : ".jpg";
                    var r = downloader.download(List.of(url), dir.resolve(name + ext),
                            Map.of("Referer", "https://www.bilibili.com/",
                                    "User-Agent", cn.fmjj.toolbox.core.bili.WbiSigner.UA),
                            p -> { }, () -> false);
                    if (r.success()) ok++; else fail++;
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            spaceStatus.setText("下载完成：成功 " + r[0] + "，失败 " + r[1] + "（" + dir + "）");
            if (r[0] > 0) toast("B站资源已下载：" + r[0] + " 个");
        });
        task.setOnFailed(e -> spaceStatus.setText("下载异常：" + task.getException().getMessage()));
        new Thread(task, "bili-space-dl").start();
    }

    // ==================== 综合搜索 ====================

    private Tab buildSearchTab() {
        keywordField.setPromptText("关键词（搜用户 + 视频）");
        keywordField.setOnAction(e -> doSearch());
        Button search = new Button("🔍 搜索");
        search.setOnAction(e -> doSearch());
        Button openLink = new Button("🔗 打开链接");
        openLink.setOnAction(e -> openSelected());

        HBox top = new HBox(8, new Label("关键词"), keywordField, search, openLink, searchStatus);
        top.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(keywordField, Priority.ALWAYS);
        HBox.setHgrow(searchStatus, Priority.ALWAYS);

        TableColumn<BiliResource.SearchItem, String> cType = new TableColumn<>("类型");
        cType.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().type()));
        cType.setPrefWidth(70);
        TableColumn<BiliResource.SearchItem, String> cTitle = new TableColumn<>("标题");
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().title()));
        cTitle.setPrefWidth(360);
        TableColumn<BiliResource.SearchItem, String> cAuthor = new TableColumn<>("作者");
        cAuthor.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().author()));
        cAuthor.setPrefWidth(180);
        TableColumn<BiliResource.SearchItem, String> cUrl = new TableColumn<>("链接");
        cUrl.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().url()));
        cUrl.setPrefWidth(320);
        searchTable.getColumns().addAll(List.of(cType, cTitle, cAuthor, cUrl));
        searchTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        searchTable.setOnMouseClicked(e -> {
            if (e.getClickCount() == 2) openSelected();
        });

        VBox box = new VBox(10, top, searchTable);
        box.setPadding(new Insets(12));
        VBox.setVgrow(searchTable, Priority.ALWAYS);
        return new Tab("🔍 综合搜索", box);
    }

    private void doSearch() {
        String kw = keywordField.getText() == null ? "" : keywordField.getText().trim();
        if (kw.isEmpty()) { searchStatus.setText("请输入关键词"); return; }
        searchStatus.setText("搜索中…");
        javafx.concurrent.Task<List<BiliResource.SearchItem>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<BiliResource.SearchItem> call() throws Exception {
                return resource.searchAll(kw, 1, CookieStore.get("bilibili"));
            }
        };
        task.setOnSucceeded(e -> {
            var items = task.getValue();
            searchTable.setItems(FXCollections.observableArrayList(items));
            searchStatus.setText("找到 " + items.size() + " 条（双击打开）");
        });
        task.setOnFailed(e -> searchStatus.setText("搜索失败：" + task.getException().getMessage()));
        new Thread(task, "bili-search").start();
    }

    private void openSelected() {
        var item = searchTable.getSelectionModel().getSelectedItem();
        if (item == null) { searchStatus.setText("请先选中一行"); return; }
        try {
            java.awt.Desktop.getDesktop().browse(java.net.URI.create(item.url()));
        } catch (Exception e) {
            searchStatus.setText("打开失败：" + item.url());
        }
    }
}
