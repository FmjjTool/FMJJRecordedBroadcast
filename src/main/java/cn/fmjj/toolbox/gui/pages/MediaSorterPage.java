package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.MediaDedup;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.Window;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * 🧹 媒体筛选：按 MD5 找重复文件（对应 Python 版 image_sorter）。
 * 只做「找出 + 删除多余副本（进回收站）」，不自动删。
 */
public class MediaSorterPage extends BasePage {

    private static final class Row {
        final String md5;
        final long size;
        final List<Path> files;
        Row(MediaDedup.DuplicateGroup g) {
            this.md5 = g.md5();
            this.size = g.size();
            this.files = g.files();
        }
        String filesText() {
            return files.stream().map(p -> p.getFileName().toString())
                    .reduce((a, b) -> a + " | " + b).orElse("");
        }
    }

    private final TextField dirField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final Label status = new Label("就绪");

    private Consumer<String> toast;

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    public String title() { return "🧹 媒体筛选"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    @Override
    protected Node build() {
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描重复");
        scan.setOnAction(e -> scan(scan));
        Button remove = new Button("🗑 删除多余副本");
        remove.setOnAction(e -> removeDuplicates());

        HBox top = new HBox(8, new Label("目录"), dirField, browse, scan, remove, status);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(dirField, Priority.ALWAYS);
        HBox.setHgrow(status, Priority.ALWAYS);

        TableColumn<Row, String> cFiles = new TableColumn<>("重复文件（保留第一个）");
        cFiles.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().filesText()));
        cFiles.setPrefWidth(520);
        TableColumn<Row, String> cCount = new TableColumn<>("份数");
        cCount.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().files.size())));
        cCount.setPrefWidth(60);
        TableColumn<Row, String> cSize = new TableColumn<>("单份大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(AudioEngine.formatSize(d.getValue().size)));
        cSize.setPrefWidth(110);
        TableColumn<Row, String> cMd5 = new TableColumn<>("MD5");
        cMd5.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().md5));
        cMd5.setPrefWidth(300);
        table.getColumns().addAll(List.of(cFiles, cCount, cSize, cMd5));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("先按文件大小预分组，再算 MD5 精确比对；删除时每组保留第一个，其余进回收站");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Window owner() { return table.getScene() == null ? null : table.getScene().getWindow(); }

    private void chooseDir() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择要查重的目录");
        File d = c.showDialog(owner());
        if (d != null) {
            dirField.setText(d.getAbsolutePath());
            scan(null);
        }
    }

    private void scan(Button btn) {
        String dir = dirField.getText().trim();
        if (dir.isEmpty()) { status.setText("请先选择目录"); return; }
        if (btn != null) btn.setDisable(true);
        status.setText("扫描中…（大目录较慢）");
        javafx.concurrent.Task<MediaDedup.Result> task = new javafx.concurrent.Task<>() {
            @Override
            protected MediaDedup.Result call() throws Exception {
                return MediaDedup.scan(Paths.get(dir), java.util.Set.of());
            }
        };
        task.setOnSucceeded(e -> {
            if (btn != null) btn.setDisable(false);
            var r = task.getValue();
            List<Row> rows = new ArrayList<>();
            for (var g : r.groups()) rows.add(new Row(g));
            table.setItems(FXCollections.observableArrayList(rows));
            status.setText(String.format("扫描 %d 个文件，%d 组重复，可省 %s",
                    r.scanned(), r.groups().size(), AudioEngine.formatSize(r.wastedBytes())));
        });
        task.setOnFailed(e -> {
            if (btn != null) btn.setDisable(false);
            status.setText("扫描失败：" + task.getException().getMessage());
        });
        new Thread(task, "dedup-scan").start();
    }

    private void removeDuplicates() {
        if (table.getItems().isEmpty()) { status.setText("没有重复项"); return; }
        List<Path> toDelete = new ArrayList<>();
        for (Row row : table.getItems()) {
            for (int i = 1; i < row.files.size(); i++) toDelete.add(row.files.get(i));
        }
        if (toDelete.isEmpty()) return;
        var r = AudioEngine.deleteFilesSafe(toDelete);
        status.setText("已删除 " + r.ok() + " 个（进回收站），失败 " + r.failed().size());
        if (r.ok() > 0) toast("已清理 " + r.ok() + " 个重复文件");
        scan(null);
    }
}
