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
import javafx.scene.control.Label;
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
        tabs.getTabs().addAll(buildSpaceTab(), buildSearchTab());

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("空间：输入 UID 看资料并下载头像/背景/头像框（免签名）；"
                + "综合搜索：WBI 签名搜索用户与视频；表情包/装扮（6 个 tab）随 M6 后续");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, tabs);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(tabs, Priority.ALWAYS);
        return root;
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
