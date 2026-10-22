package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.TableView;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.Window;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🚚 文件迁移页：把日期文件夹（YYYYMMDD）批量迁移到目标盘/目录。
 * 移植自 Python 版 gui_qt/pages/migrate.py（核心链路；原版支持多选勾选，这里用多选行 + 全选/反选）。
 */
public class MigratePage extends BasePage {

    private record Row(Path dir, String name, int files, long bytes) {}

    private final TextField srcField = new TextField();
    private final TextField dstField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final ComboBox<String> mode = new ComboBox<>(
            javafx.collections.FXCollections.observableArrayList("移动", "复制"));
    private final TextArea log = new TextArea();
    private final Label status = new Label("就绪");

    private Consumer<String> toast;

    @Override
    public String title() { return "🚚 文件迁移"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        mode.getSelectionModel().select(0);
        log.setEditable(false);
        log.setPrefRowCount(6);

        Button browseSrc = new Button("📂 浏览");
        browseSrc.setOnAction(e -> chooseDir(srcField, "选择源目录"));
        Button browseDst = new Button("📂 浏览");
        browseDst.setOnAction(e -> chooseDir(dstField, "选择目标目录"));
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());

        HBox srcRow = new HBox(8, new Label("源目录"), srcField, browseSrc, scan);
        srcRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(srcField, Priority.ALWAYS);
        HBox dstRow = new HBox(8, new Label("目标目录"), dstField, browseDst, new Label("方式"), mode);
        dstRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(dstField, Priority.ALWAYS);

        TableColumn<Row, String> cName = new TableColumn<>("日期文件夹");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().name()));
        TableColumn<Row, String> cFiles = new TableColumn<>("文件数");
        cFiles.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().files())));
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(AudioEngine.formatSize(d.getValue().bytes())));
        table.getColumns().addAll(List.of(cName, cFiles, cSize));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.getSelectionModel().setSelectionMode(javafx.scene.control.SelectionMode.MULTIPLE);

        Button selectAll = new Button("全选");
        selectAll.setOnAction(e -> table.getSelectionModel().selectAll());
        Button invert = new Button("反选");
        invert.setOnAction(e -> {
            var sel = new ArrayList<>(table.getSelectionModel().getSelectedItems());
            table.getSelectionModel().clearSelection();
            for (Row r : table.getItems()) if (!sel.contains(r)) table.getSelectionModel().select(r);
        });
        Button migrate = new Button("🚀 开始迁移");
        migrate.setDefaultButton(true);
        migrate.setOnAction(e -> migrate());
        HBox actionRow = new HBox(10, selectAll, invert, migrate, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("扫描源目录下的 YYYYMMDD 日期文件夹；选中后迁移（目标已存在则逐文件合并、同名加 _N）");
        hint.getStyleClass().add("stub-desc");

        VBox root = new VBox(10, pageTitle, hint, srcRow, dstRow, table, actionRow, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        srcField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void chooseDir(TextField target, String title) {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle(title);
        File d = chooser.showDialog(owner());
        if (d != null) target.setText(d.getAbsolutePath());
    }

    private void scan() {
        String dir = srcField.getText().trim();
        if (dir.isEmpty()) {
            status.setText("请先选择源目录");
            return;
        }
        List<Row> rows = new ArrayList<>();
        try (Stream<Path> s = Files.list(Paths.get(dir))) {
            for (Path p : s.filter(Files::isDirectory).sorted().toList()) {
                String name = p.getFileName().toString();
                if (!name.matches("^20\\d{6}$")) continue;
                int files = 0;
                long bytes = 0;
                try (Stream<Path> inner = Files.walk(p)) {
                    for (Path f : inner.filter(Files::isRegularFile).toList()) {
                        files++;
                        bytes += AudioEngine.sizeOf(f);
                    }
                }
                rows.add(new Row(p, name, files, bytes));
            }
        } catch (IOException e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        table.getItems().setAll(rows);
        status.setText("扫描到 " + rows.size() + " 个日期文件夹");
    }

    private void migrate() {
        var selected = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        String dst = dstField.getText().trim();
        if (selected.isEmpty()) {
            status.setText("请先选中要迁移的文件夹（可多选）");
            return;
        }
        if (dst.isEmpty()) {
            status.setText("请选择目标目录");
            return;
        }
        boolean move = mode.getSelectionModel().getSelectedIndex() == 0;
        log.clear();
        status.setText("迁移中…");

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() throws Exception {
                int okFiles = 0, failFiles = 0;
                Path dstRoot = Paths.get(dst);
                Files.createDirectories(dstRoot);
                for (Row row : selected) {
                    Path target = dstRoot.resolve(row.name());
                    appendLog((move ? "🚚 移动 " : "📋 复制 ") + row.name() + " → " + target);
                    try {
                        if (Files.exists(target)) {
                            // 目标已存在：逐文件合并，同名加 _N
                            try (Stream<Path> s = Files.walk(row.dir())) {
                                for (Path f : s.filter(Files::isRegularFile).toList()) {
                                    Path rel = row.dir().relativize(f);
                                    Path dest = uniqueTarget(target.resolve(rel));
                                    Files.createDirectories(dest.getParent());
                                    if (move) Files.move(f, dest);
                                    else Files.copy(f, dest);
                                    okFiles++;
                                }
                            }
                            if (move) deleteEmptyDirs(row.dir());
                        } else {
                            if (move) Files.move(row.dir(), target);
                            else copyDir(row.dir(), target);
                            okFiles += row.files();
                        }
                    } catch (Exception e) {
                        failFiles += Math.max(1, row.files());
                        appendLog("   ❌ " + e.getMessage());
                    }
                }
                return new int[] {okFiles, failFiles};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            status.setText("完成：成功 " + r[0] + " 个文件" + (r[1] > 0 ? "，失败 " + r[1] : ""));
            toast("迁移完成：" + r[0] + " 个文件");
            scan();
        });
        task.setOnFailed(e -> status.setText("迁移失败：" + task.getException().getMessage()));
        new Thread(task, "file-migrate").start();
    }

    private void appendLog(String text) {
        Platform.runLater(() -> {
            log.appendText(text + "\n");
            log.setScrollTop(Double.MAX_VALUE);
        });
    }

    private static Path uniqueTarget(Path p) {
        if (!Files.exists(p)) return p;
        String name = p.getFileName().toString();
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        String ext = dot > 0 ? name.substring(dot) : "";
        int i = 1;
        while (true) {
            Path candidate = p.getParent().resolve(base + "_" + i + ext);
            if (!Files.exists(candidate)) return candidate;
            i++;
        }
    }

    private static void copyDir(Path src, Path dst) throws IOException {
        try (Stream<Path> s = Files.walk(src)) {
            for (Path p : s.toList()) {
                Path rel = src.relativize(p);
                Path target = dst.resolve(rel);
                if (Files.isDirectory(p)) Files.createDirectories(target);
                else {
                    Files.createDirectories(target.getParent());
                    Files.copy(p, target, StandardCopyOption.COPY_ATTRIBUTES);
                }
            }
        }
    }

    private static void deleteEmptyDirs(Path dir) {
        try (Stream<Path> s = Files.walk(dir)) {
            s.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try {
                    if (Files.isDirectory(p)) Files.deleteIfExists(p);
                } catch (IOException ignored) {
                }
            });
        } catch (IOException ignored) {
        }
    }
}
