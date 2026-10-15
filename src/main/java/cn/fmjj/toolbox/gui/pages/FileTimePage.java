package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.filetime.FileTimeEngine;
import cn.fmjj.toolbox.engine.filetime.FileTimeEngine.ApplyResult;
import cn.fmjj.toolbox.engine.filetime.FileTimeEngine.Mode;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.RadioButton;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.control.ToggleGroup;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;
import javafx.stage.Window;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * 🕓 文件时间修改页：批量改 创建/修改/访问 时间。
 * 移植自 Python 版 gui_qt/pages/file_time.py。
 *
 * 已知 parity 差距：Java NIO 大多不支持写 Windows 创建时间（ctime），
 * 引擎尽力而为并在状态列标注「创建时间不支持」。
 */
public class FileTimePage extends BasePage {

    private record Row(Path path, double mtime, double ctime, String newMtime, String status) {}

    private static final DateTimeFormatter DISPLAY = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final TableView<Row> table = new TableView<>();
    private final Label countLabel = new Label("0 个文件");
    private final TextField setTime = new TextField();
    private final TextField offset = new TextField("+0h");
    private final RadioButton rbSet = new RadioButton("指定时间");
    private final RadioButton rbOffset = new RadioButton("偏移量");
    private final RadioButton rbName = new RadioButton("按文件名时间");
    private final Label status = new Label();

    private final List<Path> files = new ArrayList<>();

    private Consumer<String> toast;

    @Override
    public String title() { return "🕓 文件时间修改"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        setTime.setText(LocalDateTime.now().format(DISPLAY));
        setTime.setPromptText("yyyy-MM-dd HH:mm:ss");
        ToggleGroup group = new ToggleGroup();
        rbSet.setToggleGroup(group);
        rbOffset.setToggleGroup(group);
        rbName.setToggleGroup(group);
        rbSet.setSelected(true);
        rbSet.setOnAction(e -> updateMode());
        rbOffset.setOnAction(e -> updateMode());
        rbName.setOnAction(e -> updateMode());

        Button addFiles = new Button("📄 添加文件");
        addFiles.setOnAction(e -> pickFiles(false));
        Button addDir = new Button("📂 添加文件夹");
        addDir.setOnAction(e -> pickFiles(true));
        Button clear = new Button("🧹 清空");
        clear.setOnAction(e -> { files.clear(); table.getItems().clear(); countLabel.setText("0 个文件"); });
        HBox bar = new HBox(8, addFiles, addDir, clear, countLabel);
        bar.getStyleClass().add("toolbar");
        bar.setAlignment(Pos.CENTER_LEFT);

        TableColumn<Row, String> cName = new TableColumn<>("文件");
        cName.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(
                d.getValue().path().getFileName().toString()));
        TableColumn<Row, String> cMtime = new TableColumn<>("当前修改时间");
        cMtime.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(
                FileTimeEngine.formatEpoch(d.getValue().mtime())));
        TableColumn<Row, String> cCtime = new TableColumn<>("当前创建时间");
        cCtime.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(
                FileTimeEngine.formatEpoch(d.getValue().ctime())));
        TableColumn<Row, String> cNew = new TableColumn<>("新修改时间");
        cNew.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().newMtime()));
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().status()));
        table.getColumns().addAll(List.of(cName, cMtime, cCtime, cNew, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        HBox modeRow = new HBox(12, new Label("修改方式："), rbSet, setTime, rbOffset, offset, rbName);
        modeRow.setAlignment(Pos.CENTER_LEFT);
        modeRow.getStyleClass().add("toolbar");

        Button preview = new Button("👁 预览");
        preview.setOnAction(e -> preview());
        Button apply = new Button("✔ 应用");
        apply.setOnAction(e -> apply());
        HBox bottom = new HBox(10, preview, apply, status);
        bottom.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        VBox root = new VBox(10, pageTitle, bar, table, modeRow, bottom);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        updateMode();
        return root;
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private Mode mode() {
        if (rbOffset.isSelected()) return Mode.OFFSET;
        if (rbName.isSelected()) return Mode.FROM_NAME;
        return Mode.SET;
    }

    private void updateMode() {
        setTime.setDisable(!rbSet.isSelected());
        offset.setDisable(!rbOffset.isSelected());
    }

    private LocalDateTime specifiedOrNull() {
        try {
            return LocalDateTime.parse(setTime.getText().trim(), DISPLAY);
        } catch (Exception e) {
            return null;
        }
    }

    private void pickFiles(boolean fromDir) {
        List<Path> picked = new ArrayList<>();
        if (fromDir) {
            DirectoryChooser chooser = new DirectoryChooser();
            chooser.setTitle("选择文件夹");
            File d = chooser.showDialog(owner());
            if (d == null) return;
            try (var s = Files.walk(d.toPath())) {
                s.filter(Files::isRegularFile).forEach(picked::add);
            } catch (IOException e) {
                status.setText("读取目录失败：" + e.getMessage());
                return;
            }
        } else {
            FileChooser chooser = new FileChooser();
            chooser.setTitle("选择文件");
            List<File> fs = chooser.showOpenMultipleDialog(owner());
            if (fs != null) fs.forEach(f -> picked.add(f.toPath()));
        }
        for (Path p : picked) if (!files.contains(p)) files.add(p);
        render();
    }

    private void render() {
        countLabel.setText(files.size() + " 个文件");
        List<Row> rows = new ArrayList<>();
        for (Path p : files) {
            try {
                var attrs = Files.readAttributes(p, java.nio.file.attribute.BasicFileAttributes.class);
                rows.add(new Row(p, attrs.lastModifiedTime().toMillis() / 1000.0,
                        attrs.creationTime().toMillis() / 1000.0, "", ""));
            } catch (IOException ignored) {
                rows.add(new Row(p, 0, 0, "", "读取失败"));
            }
        }
        table.getItems().setAll(rows);
    }

    /** 当前模式的目标 epoch（SET 用输入框，OFFSET 需文件现值，FROM_NAME 按文件名）。 */
    private void preview() {
        if (files.isEmpty()) {
            status.setText("请先添加文件");
            return;
        }
        if (mode() == Mode.SET && specifiedOrNull() == null) {
            status.setText("时间格式应为 yyyy-MM-dd HH:mm:ss");
            return;
        }
        List<Row> rows = new ArrayList<>();
        int ok = 0;
        for (Path p : files) {
            String newM = "";
            String st = "";
            String err = FileTimeEngine.computeTarget(p, mode(), specifiedOrNull(), offset.getText());
            if (err != null) {
                st = "✗ " + err;
            } else {
                switch (mode()) {
                    case SET -> newM = setTime.getText().trim();
                    case OFFSET -> {
                        try {
                            var attrs = Files.readAttributes(p, java.nio.file.attribute.BasicFileAttributes.class);
                            newM = FileTimeEngine.formatEpoch(
                                    attrs.lastModifiedTime().toMillis() / 1000.0
                                            + FileTimeEngine.parseOffsetSeconds(offset.getText()));
                        } catch (Exception e) {
                            newM = "（应用时计算）";
                        }
                    }
                    case FROM_NAME -> newM = FileTimeEngine.formatEpoch(FileTimeEngine.toEpoch(
                            FileTimeEngine.parseTimeFromName(p.getFileName().toString())));
                }
                st = "待应用";
                ok++;
            }
            try {
                var attrs = Files.readAttributes(p, java.nio.file.attribute.BasicFileAttributes.class);
                rows.add(new Row(p, attrs.lastModifiedTime().toMillis() / 1000.0,
                        attrs.creationTime().toMillis() / 1000.0, newM, st));
            } catch (IOException ignored) {
                rows.add(new Row(p, 0, 0, newM, st));
            }
        }
        table.getItems().setAll(rows);
        status.setText("预览：" + ok + "/" + files.size() + " 可应用");
    }

    private void apply() {
        if (files.isEmpty()) {
            status.setText("请先添加文件");
            return;
        }
        if (mode() == Mode.SET && specifiedOrNull() == null) {
            status.setText("时间格式应为 yyyy-MM-dd HH:mm:ss");
            return;
        }
        Mode m = mode();
        LocalDateTime specified = specifiedOrNull();
        String offsetText = offset.getText();
        // 主线程算好参数，后台线程只做 IO（与 Python 同思路）
        javafx.concurrent.Task<Integer> task = new javafx.concurrent.Task<>() {
            @Override
            protected Integer call() {
                int ok = 0;
                for (Path p : files) {
                    ApplyResult r = FileTimeEngine.apply(p, m, specified, offsetText);
                    if (r.ok()) ok++;
                }
                return ok;
            }
        };
        task.setOnSucceeded(e -> {
            render();
            preview();
            status.setText("已应用：" + task.getValue() + "/" + files.size());
            toast("文件时间已修改：" + task.getValue() + " 个");
        });
        task.setOnFailed(e -> status.setText("应用失败：" + task.getException().getMessage()));
        new Thread(task, "filetime-apply").start();
    }
}
