package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.store.MediaStore;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;

import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * 🗂 资产管理（对齐 Python 版 asset.py 的三 Tab 结构）。
 *
 * <p>Tab 顺序与原版一致：**已获取资源 | 数据库浏览 | 本地资源**；
 * 页头右上角「💾 备份」。列表用卡片式（缩略图 + 两行文字），不是表格。
 */
public class AssetPage extends BasePage {

    private static final Set<String> IMAGE_EXTS = Set.of(".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp");
    private static final Set<String> VIDEO_EXTS = Set.of(".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".webm", ".m4v");
    private static final Set<String> AUDIO_EXTS = Set.of(".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma");

    private Consumer<String> toast;

    // ===== 已获取资源 =====
    private final ComboBox<String> platSel = new ComboBox<>();
    private final ComboBox<String> statusSel = new ComboBox<>();
    private final ComboBox<String> sortSel = new ComboBox<>();
    private final TextField searchField = new TextField();
    private final VBox collectedList = new VBox(4);
    private final Label collectedStatus = new Label("");
    private List<MediaStore.Row> collectedRows = new ArrayList<>();

    // ===== 数据库浏览 =====
    private final TableView<MediaStore.Row> dbTable = new TableView<>();
    private final Label dbStatus = new Label("");

    // ===== 本地资源 =====
    private final TextField dirField = new TextField();
    private final TextField limitField = new TextField("0");
    private final TextField batchField = new TextField("0");
    private final CheckBox incMode = new CheckBox("增量模式(写入索引)");
    private final VBox localList = new VBox(4);
    private final Label localStatus = new Label("");
    private List<Path> localFiles = new ArrayList<>();
    private int localShown = 0;

    @Override
    public String title() { return "🗂 资产管理"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label(title());
        pageTitle.setStyle("-fx-font-size: 16px; -fx-font-weight: bold;");
        Button backup = new Button("💾 备份");
        backup.setTooltip(new javafx.scene.control.Tooltip(
                "备份已获取数据与媒体文件为 zip 包（文件不覆盖）"));
        backup.setOnAction(e -> backupZip());
        HBox head = new HBox(8, pageTitle, spacer(), backup);
        head.setAlignment(Pos.CENTER_LEFT);
        head.setPadding(new Insets(8, 8, 0, 8));

        TabPane tabs = new TabPane();
        tabs.setTabClosingPolicy(TabPane.TabClosingPolicy.UNAVAILABLE);
        tabs.getTabs().addAll(buildCollectedTab(), buildDbTab(), buildLocalTab());
        tabs.getSelectionModel().selectedIndexProperty().addListener((o, a, b) -> {
            if (b.intValue() == 1) refreshDb();
        });

        VBox root = new VBox(0, head, tabs);
        root.getStyleClass().add("page-wrapper");
        VBox.setVgrow(tabs, Priority.ALWAYS);
        return root;
    }

    private static Node spacer() {
        HBox s = new HBox();
        HBox.setHgrow(s, Priority.ALWAYS);
        return s;
    }

    // ==================== Tab 1：已获取资源 ====================

    private Tab buildCollectedTab() {
        Label title = new Label("📦 已获取资源");
        title.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        platSel.getItems().add("全部");
        platSel.getItems().addAll(CookieStore.PLATFORM_NAMES.values());
        platSel.getSelectionModel().selectFirst();
        platSel.setPrefWidth(120);
        platSel.setOnAction(e -> refreshCollected());

        searchField.setPromptText("🔍 搜索标题/作者/ID...");
        searchField.textProperty().addListener((o, a, b) -> refreshCollected());
        HBox.setHgrow(searchField, Priority.ALWAYS);

        statusSel.getItems().addAll("全部状态", "未下载", "已下载", "下载不完整");
        statusSel.getSelectionModel().selectFirst();
        statusSel.setPrefWidth(110);
        statusSel.setOnAction(e -> refreshCollected());

        sortSel.getItems().addAll("时间倒序", "时间正序");
        sortSel.getSelectionModel().selectFirst();
        sortSel.setPrefWidth(90);
        sortSel.setOnAction(e -> refreshCollected());

        Button refresh = new Button("🔄");
        refresh.setOnAction(e -> refreshCollected());
        CheckBox selectAll = new CheckBox("全选");
        ComboBox<String> threadSel = new ComboBox<>(FXCollections.observableArrayList("1", "2", "4", "8", "16"));
        threadSel.getSelectionModel().select("4");
        threadSel.setPrefWidth(70);
        Button batchDl = new Button("📥 下载所选");
        batchDl.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        batchDl.setOnAction(e -> toast("批量下载队列随下一轮补齐（可先到「已获取」页逐条重试）"));

        HBox bar = new HBox(8, new Label("平台:"), platSel, searchField,
                statusSel, sortSel, refresh, selectAll, new Label("线程:"), threadSel, batchDl);
        bar.setAlignment(Pos.CENTER_LEFT);
        bar.setPadding(new Insets(0, 0, 6, 0));

        collectedStatus.setStyle("-fx-text-fill: #888;");
        ScrollPane scroll = new ScrollPane(collectedList);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        VBox.setVgrow(scroll, Priority.ALWAYS);

        VBox box = new VBox(6, title, bar, collectedStatus, scroll);
        box.setPadding(new Insets(10));
        Tab tab = new Tab("已获取资源", box);
        Platform.runLater(this::refreshCollected);
        return tab;
    }

    private void refreshCollected() {
        String platCn = platSel.getValue();
        String platform = null;
        if (platCn != null && !"全部".equals(platCn)) {
            for (var e : CookieStore.PLATFORM_NAMES.entrySet()) {
                if (e.getValue().equals(platCn)) { platform = e.getKey(); break; }
            }
        }
        final String plat = platform;
        javafx.concurrent.Task<List<MediaStore.Row>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<MediaStore.Row> call() {
                try (MediaStore store = new MediaStore()) {
                    return store.list(plat, 500);
                }
            }
        };
        task.setOnSucceeded(e -> {
            collectedRows = task.getValue();
            renderCollected();
        });
        task.setOnFailed(e -> collectedStatus.setText("读取失败：" + task.getException().getMessage()));
        new Thread(task, "asset-collected").start();
    }

    private void renderCollected() {
        String kw = searchField.getText() == null ? "" : searchField.getText().trim().toLowerCase(Locale.ROOT);
        String status = statusSel.getValue();
        boolean asc = "时间正序".equals(sortSel.getValue());

        List<MediaStore.Row> rows = new ArrayList<>(collectedRows);
        if (!kw.isEmpty()) {
            rows.removeIf(r -> !contains(r.title(), kw) && !contains(r.author(), kw)
                    && !contains(r.mediaId(), kw));
        }
        if ("已下载".equals(status)) rows.removeIf(r -> r.savedPath() == null || r.savedPath().isBlank());
        if ("未下载".equals(status)) rows.removeIf(r -> r.savedPath() != null && !r.savedPath().isBlank());
        if (asc) java.util.Collections.reverse(rows);

        collectedList.getChildren().clear();
        for (MediaStore.Row r : rows) {
            collectedList.getChildren().add(collectedCard(r));
        }
        collectedStatus.setText("共 " + rows.size() + " 条"
                + (rows.isEmpty() ? "（可在「单个解析」解析内容后自动入库）" : ""));
    }

    private static boolean contains(String s, String kw) {
        return s != null && s.toLowerCase(Locale.ROOT).contains(kw);
    }

    private Node collectedCard(MediaStore.Row r) {
        String platCn = CookieStore.PLATFORM_NAMES.getOrDefault(r.platform(), r.platform());
        Label icon = new Label("📦");
        icon.setMinSize(90, 60);
        icon.setMaxSize(90, 60);
        icon.setAlignment(Pos.CENTER);
        icon.setStyle("-fx-background-color: #eef1f5; -fx-background-radius: 4; -fx-font-size: 28px;");

        Label name = new Label((r.title() == null || r.title().isBlank() ? "(无标题)" : r.title()));
        name.setStyle("-fx-font-size: 12px; -fx-font-weight: bold;");
        String detail = platCn + "　" + (r.author() == null ? "-" : r.author())
                + "　" + (r.publishTime() == null || r.publishTime().isBlank() ? "-" : r.publishTime())
                + "　" + (r.savedPath() == null || r.savedPath().isBlank() ? "未下载" : "已下载");
        Label info = new Label(detail);
        info.setStyle("-fx-font-size: 11px; -fx-text-fill: #666;");
        info.setWrapText(true);

        VBox text = new VBox(2, name, info);
        HBox.setHgrow(text, Priority.ALWAYS);

        Button open = new Button("📂");
        open.setTooltip(new javafx.scene.control.Tooltip("打开所在文件夹"));
        open.setOnAction(e -> {
            Path p = r.savedPath() == null || r.savedPath().isBlank() ? null : Path.of(r.savedPath());
            if (p != null && Files.exists(p)) AudioEngine.openInExplorer(p);
            else toast("本地文件不存在");
        });

        HBox row = new HBox(8, icon, text, open);
        row.setAlignment(Pos.CENTER_LEFT);
        row.setPadding(new Insets(4, 6, 4, 6));
        row.setStyle("-fx-background-color: white; -fx-border-color: #eee; -fx-border-radius: 4;");
        return row;
    }

    // ==================== Tab 2：数据库浏览 ====================

    private Tab buildDbTab() {
        Label title = new Label("🗃 数据库浏览");
        title.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        TableColumn<MediaStore.Row, String> cPlat = new TableColumn<>("平台");
        cPlat.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(
                CookieStore.PLATFORM_NAMES.getOrDefault(d.getValue().platform(), d.getValue().platform())));
        cPlat.setPrefWidth(90);
        TableColumn<MediaStore.Row, String> cId = new TableColumn<>("ID");
        cId.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().mediaId()));
        cId.setPrefWidth(160);
        TableColumn<MediaStore.Row, String> cTitle = new TableColumn<>("标题");
        cTitle.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().title()));
        cTitle.setPrefWidth(360);
        TableColumn<MediaStore.Row, String> cAuthor = new TableColumn<>("作者");
        cAuthor.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().author()));
        cAuthor.setPrefWidth(140);
        TableColumn<MediaStore.Row, String> cTime = new TableColumn<>("发布");
        cTime.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().publishTime()));
        cTime.setPrefWidth(140);
        TableColumn<MediaStore.Row, String> cSaved = new TableColumn<>("本地路径");
        cSaved.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().savedPath()));
        cSaved.setPrefWidth(300);
        dbTable.getColumns().addAll(List.of(cPlat, cId, cTitle, cAuthor, cTime, cSaved));
        dbTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Button reload = new Button("🔄 刷新");
        reload.setOnAction(e -> refreshDb());
        dbStatus.setStyle("-fx-text-fill: #888;");
        HBox bar = new HBox(8, reload, dbStatus);
        bar.setAlignment(Pos.CENTER_LEFT);

        VBox.setVgrow(dbTable, Priority.ALWAYS);
        VBox box = new VBox(6, title, bar, dbTable);
        box.setPadding(new Insets(10));
        return new Tab("数据库浏览", box);
    }

    private void refreshDb() {
        javafx.concurrent.Task<List<MediaStore.Row>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<MediaStore.Row> call() {
                try (MediaStore store = new MediaStore()) {
                    return store.list(null, 2000);
                }
            }
        };
        task.setOnSucceeded(e -> {
            var rows = task.getValue();
            dbTable.setItems(FXCollections.observableArrayList(rows));
            dbStatus.setText("共 " + rows.size() + " 条");
        });
        task.setOnFailed(e -> dbStatus.setText("读取失败：" + task.getException().getMessage()));
        new Thread(task, "asset-db").start();
    }

    // ==================== Tab 3：本地资源 ====================

    private Tab buildLocalTab() {
        Label title = new Label("📁 本地资源");
        title.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        dirField.setPromptText("资源文件夹路径");
        Button pick = new Button("📂 选择");
        pick.setOnAction(e -> {
            DirectoryChooser c = new DirectoryChooser();
            c.setTitle("选择资源文件夹");
            File d = c.showDialog(dirField.getScene() == null ? null : dirField.getScene().getWindow());
            if (d != null) dirField.setText(d.getAbsolutePath());
        });
        HBox row1 = new HBox(8, dirField, pick);
        HBox.setHgrow(dirField, Priority.ALWAYS);

        limitField.setPrefWidth(50);
        batchField.setPrefWidth(40);
        Button scan = new Button("🔍 扫描");
        scan.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        scan.setOnAction(e -> scanLocal());
        HBox row2 = new HBox(6, new Label("上限:"), limitField, new Label("(0=全部)"),
                new Label("分批间隔(秒):"), batchField, incMode, spacer(), scan);
        row2.setAlignment(Pos.CENTER_LEFT);

        localStatus.setStyle("-fx-text-fill: #888;");
        ScrollPane scroll = new ScrollPane(localList);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        VBox.setVgrow(scroll, Priority.ALWAYS);

        VBox box = new VBox(6, title, row1, row2, localStatus, scroll);
        box.setPadding(new Insets(10));
        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return new Tab("本地资源", box);
    }

    private void scanLocal() {
        String dir = dirField.getText() == null ? "" : dirField.getText().trim();
        if (dir.isEmpty()) { localStatus.setText("请先选择文件夹"); return; }
        int limit;
        try {
            limit = Integer.parseInt(limitField.getText().trim());
        } catch (Exception e) {
            limit = 0;
        }
        final int max = limit;
        localStatus.setText("扫描中…");
        javafx.concurrent.Task<List<Path>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<Path> call() throws IOException {
                List<Path> out = new ArrayList<>();
                try (Stream<Path> s = Files.walk(Paths.get(dir))) {
                    for (Path p : s.filter(Files::isRegularFile).toList()) {
                        String ext = extOf(p);
                        if (IMAGE_EXTS.contains(ext) || VIDEO_EXTS.contains(ext) || AUDIO_EXTS.contains(ext)) {
                            out.add(p);
                            if (max > 0 && out.size() >= max) break;
                        }
                    }
                }
                out.sort(java.util.Comparator.comparing(Path::toString));
                return out;
            }
        };
        task.setOnSucceeded(e -> {
            localFiles = task.getValue();
            localShown = 0;
            localList.getChildren().clear();
            localStatus.setText("共 " + localFiles.size() + " 个媒体文件");
            showMoreLocal();
        });
        task.setOnFailed(e -> localStatus.setText("扫描失败：" + task.getException().getMessage()));
        new Thread(task, "asset-local-scan").start();
    }

    private void showMoreLocal() {
        int next = Math.min(localShown + 60, localFiles.size());
        for (int i = localShown; i < next; i++) {
            localList.getChildren().add(localCard(localFiles.get(i)));
        }
        localShown = next;
        if (localShown < localFiles.size()) {
            Button more = new Button("加载更多（" + (localFiles.size() - localShown) + " 个）");
            more.setOnAction(e -> {
                localList.getChildren().remove(more);
                showMoreLocal();
            });
            localList.getChildren().add(more);
        }
    }

    private Node localCard(Path p) {
        String ext = extOf(p);
        boolean isImage = IMAGE_EXTS.contains(ext);
        String kind = isImage ? "image" : VIDEO_EXTS.contains(ext) ? "video" : "audio";

        Label thumb = new Label(isImage ? "🖼" : "video".equals(kind) ? "🎬" : "🎵");
        thumb.setMinSize(90, 60);
        thumb.setMaxSize(90, 60);
        thumb.setAlignment(Pos.CENTER);
        thumb.setStyle("-fx-background-color: #eef1f5; -fx-background-radius: 4; -fx-font-size: 28px;");
        thumb.setTooltip(new javafx.scene.control.Tooltip(p.toString()));

        if (isImage) {
            new Thread(() -> {
                try {
                    var img = ImageTools.read(p);
                    var scaled = ImageTools.scale(img, 90, 60);
                    Platform.runLater(() -> thumb.setGraphic(new javafx.scene.image.ImageView(toFx(scaled, 90, 60))));
                } catch (Exception ignored) {
                }
            }, "asset-thumb").start();
        }

        long size;
        String mtime;
        try {
            size = Files.size(p);
            mtime = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
                    .format(java.time.LocalDateTime.ofInstant(
                            Files.getLastModifiedTime(p).toInstant(), java.time.ZoneId.systemDefault()));
        } catch (IOException e) {
            size = 0;
            mtime = "-";
        }
        Label info = new Label(p.getFileName() + "\n类型: " + kind + "　大小: "
                + AudioEngine.formatSize(size) + "　" + mtime);
        info.setStyle("-fx-font-size: 11px;");
        info.setWrapText(true);
        HBox.setHgrow(info, Priority.ALWAYS);

        Button open = new Button("📂");
        open.setOnAction(e -> AudioEngine.openInExplorer(p));

        HBox row = new HBox(8, thumb, info, open);
        row.setAlignment(Pos.CENTER_LEFT);
        row.setPadding(new Insets(4, 6, 4, 6));
        row.setStyle("-fx-background-color: white; -fx-border-color: #eee; -fx-border-radius: 4;");
        return row;
    }

    private static javafx.scene.image.Image toFx(java.awt.image.BufferedImage src, int w, int h) {
        var wi = new javafx.scene.image.WritableImage(w, h);
        var pw = wi.getPixelWriter();
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                pw.setArgb(x, y, src.getRGB(x, y));
            }
        }
        return wi;
    }

    private static String extOf(Path p) {
        String n = p.getFileName().toString().toLowerCase(Locale.ROOT);
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot) : "";
    }

    // ==================== 备份 ====================

    private void backupZip() {
        FileChooser c = new FileChooser();
        c.setTitle("导出资产备份");
        c.setInitialFileName("资产备份_" + java.time.LocalDate.now() + ".zip");
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("ZIP", "*.zip"));
        File target = c.showSaveDialog(dirField.getScene() == null ? null : dirField.getScene().getWindow());
        if (target == null) return;

        javafx.concurrent.Task<long[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected long[] call() throws IOException {
                long files = 0, bytes = 0;
                try (OutputStream fos = Files.newOutputStream(target.toPath());
                     ZipOutputStream zip = new ZipOutputStream(fos)) {
                    List<Path> roots = List.of(
                            Settings.load().effectiveExportDir(),
                            cn.fmjj.toolbox.core.AppPaths.configDir().resolve("collected.db"));
                    for (Path root : roots) {
                        if (!Files.exists(root)) continue;
                        if (Files.isRegularFile(root)) {
                            zip.putNextEntry(new ZipEntry(root.getFileName().toString()));
                            byte[] data = Files.readAllBytes(root);
                            zip.write(data);
                            zip.closeEntry();
                            files++;
                            bytes += data.length;
                            continue;
                        }
                        try (Stream<Path> s = Files.walk(root)) {
                            for (Path p : s.filter(Files::isRegularFile).toList()) {
                                String name = root.relativize(p).toString().replace('\\', '/');
                                zip.putNextEntry(new ZipEntry(root.getFileName() + "/" + name));
                                byte[] data = Files.readAllBytes(p);
                                zip.write(data);
                                zip.closeEntry();
                                files++;
                                bytes += data.length;
                            }
                        }
                    }
                }
                return new long[] {files, bytes};
            }
        };
        task.setOnSucceeded(e -> {
            long[] r = task.getValue();
            toast("备份完成：" + r[0] + " 个文件 / " + AudioEngine.formatSize(r[1]));
        });
        task.setOnFailed(e -> toast("备份失败：" + task.getException().getMessage()));
        new Thread(task, "asset-backup").start();
    }
}
