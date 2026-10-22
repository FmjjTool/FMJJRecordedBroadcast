package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.asset.AssetScanner;
import cn.fmjj.toolbox.engine.asset.AssetScanner.FolderStat;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.input.MouseButton;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.Window;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.function.Consumer;

/**
 * 🗂 资产管理页：按日期文件夹统计媒体资产（图片/视频/音频/体积）。
 * 对应 Python 版 gui_qt/pages/asset.py 的统计口径（原版的「原始数据」列依赖
 * asset_index 缓存，随 M6 后续补）。
 */
public class AssetPage extends BasePage {

    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");

    private final TextField dirField = new TextField();
    private final TableView<FolderStat> table = new TableView<>();
    private final Label summary = new Label("就绪");

    private Consumer<String> toast;

    @Override
    public String title() { return "🗂 资产管理"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());
        Button open = new Button("📁 打开选中");
        open.setOnAction(e -> openSelected());
        Button export = new Button("📤 导出 CSV");
        export.setOnAction(e -> exportCsv());

        HBox top = new HBox(8, new Label("目录"), dirField, browse, scan, open, export);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(dirField, Priority.ALWAYS);

        TableColumn<FolderStat, String> cName = new TableColumn<>("文件夹");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().name()));
        cName.setPrefWidth(140);
        TableColumn<FolderStat, String> cImages = new TableColumn<>("图片");
        cImages.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().images())));
        cImages.setPrefWidth(70);
        TableColumn<FolderStat, String> cVideos = new TableColumn<>("视频");
        cVideos.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().videos())));
        cVideos.setPrefWidth(70);
        TableColumn<FolderStat, String> cAudios = new TableColumn<>("音频");
        cAudios.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().audios())));
        cAudios.setPrefWidth(70);
        TableColumn<FolderStat, String> cFiles = new TableColumn<>("媒体总数");
        cFiles.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().totalFiles())));
        cFiles.setPrefWidth(90);
        TableColumn<FolderStat, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(AudioEngine.formatSize(d.getValue().bytes())));
        cSize.setPrefWidth(110);
        TableColumn<FolderStat, String> cTime = new TableColumn<>("最后修改");
        cTime.setCellValueFactory(d -> new SimpleStringProperty(formatTime(d.getValue().lastModifiedMillis())));
        cTime.setPrefWidth(150);
        table.getColumns().addAll(List.of(cName, cImages, cVideos, cAudios, cFiles, cSize, cTime));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.setOnMouseClicked(e -> {
            if (e.getButton() == MouseButton.PRIMARY && e.getClickCount() == 2) openSelected();
        });

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("扫描工作目录下的日期文件夹（YYYYMMDD），统计图片/视频/音频数量与体积；双击行打开文件夹");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table, summary);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private static String formatTime(long millis) {
        if (millis <= 0) return "—";
        return LocalDateTime.ofInstant(Instant.ofEpochMilli(millis), ZoneId.systemDefault()).format(TIME);
    }

    private void chooseDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择要统计的目录");
        File d = chooser.showDialog(owner());
        if (d != null) {
            dirField.setText(d.getAbsolutePath());
            scan();
        }
    }

    private void scan() {
        String dir = dirField.getText().trim();
        if (dir.isEmpty()) {
            summary.setText("请先选择目录");
            return;
        }
        long start = System.currentTimeMillis();
        List<FolderStat> stats = AssetScanner.scan(Paths.get(dir));
        table.getItems().setAll(stats);
        var t = AssetScanner.totals(stats);
        summary.setText(String.format("共 %d 个文件夹 · 图片 %d · 视频 %d · 音频 %d · 总计 %s（耗时 %d ms）",
                t.folders(), t.images(), t.videos(), t.audios(),
                AudioEngine.formatSize(t.bytes()), System.currentTimeMillis() - start));
    }

    private void openSelected() {
        FolderStat row = table.getSelectionModel().getSelectedItem();
        if (row == null) {
            summary.setText("请先选中一行");
            return;
        }
        AudioEngine.openInExplorer(row.dir());
        toast("已打开：" + row.dir());
    }

    private void exportCsv() {
        if (table.getItems().isEmpty()) {
            summary.setText("没有可导出的数据");
            return;
        }
        javafx.stage.FileChooser chooser = new javafx.stage.FileChooser();
        chooser.setTitle("导出资产统计");
        chooser.setInitialFileName("资产统计.csv");
        chooser.getExtensionFilters().add(new javafx.stage.FileChooser.ExtensionFilter("CSV", "*.csv"));
        File file = chooser.showSaveDialog(owner());
        if (file == null) return;
        try {
            StringBuilder sb = new StringBuilder("文件夹,图片,视频,音频,媒体总数,大小(字节),最后修改\n");
            for (FolderStat s : table.getItems()) {
                sb.append('"').append(s.name()).append("\",")
                        .append(s.images()).append(',')
                        .append(s.videos()).append(',')
                        .append(s.audios()).append(',')
                        .append(s.totalFiles()).append(',')
                        .append(s.bytes()).append(',')
                        .append(formatTime(s.lastModifiedMillis())).append('\n');
            }
            byte[] bom = {(byte) 0xEF, (byte) 0xBB, (byte) 0xBF};
            byte[] body = sb.toString().getBytes(StandardCharsets.UTF_8);
            byte[] all = new byte[bom.length + body.length];
            System.arraycopy(bom, 0, all, 0, bom.length);
            System.arraycopy(body, 0, all, bom.length, body.length);
            Files.write(file.toPath(), all);
            summary.setText("已导出：" + file.getName());
        } catch (Exception e) {
            summary.setText("导出失败：" + e.getMessage());
        }
    }
}
