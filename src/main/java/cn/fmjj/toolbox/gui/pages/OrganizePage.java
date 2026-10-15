package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.organize.OrganizeEngine;
import cn.fmjj.toolbox.engine.organize.OrganizeEngine.ScannedFile;
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
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 📂 自动整理页：按文件名日期归档到 YYYYMMDD 文件夹。
 * 移植自 Python 版 gui_qt/pages/organize.py（引擎 organize_engine.py）。
 */
public class OrganizePage extends BasePage {

    private record Row(String date, int files, long bytes) {}

    private final TextField dirField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final Label noDateLabel = new Label("无日期文件：0");
    private final Label status = new Label();
    private final Button runBtn = new Button("🚀 开始整理");

    private Map<String, List<ScannedFile>> groups = new LinkedHashMap<>();

    private Consumer<String> toast;

    @Override
    public String title() { return "📂 自动整理"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        dirField.setEditable(false);
        dirField.setPromptText("选择要整理的录播目录");

        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());
        HBox top = new HBox(8, new Label("工作目录："), dirField, browse, scan, noDateLabel);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(dirField, Priority.ALWAYS);

        TableColumn<Row, String> cDate = new TableColumn<>("日期");
        cDate.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(d.getValue().date()));
        TableColumn<Row, String> cCount = new TableColumn<>("文件数");
        cCount.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(
                String.valueOf(d.getValue().files())));
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(
                String.format("%.1f MB", d.getValue().bytes() / 1024.0 / 1024.0)));
        table.getColumns().addAll(List.of(cDate, cCount, cSize));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        runBtn.setDisable(true);
        runBtn.setOnAction(e -> run());
        HBox bottom = new HBox(10, runBtn, status);
        bottom.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("扫描后按日期分组预览；执行会把文件移入 YYYYMMDD 子文件夹（同名自动加 _N 防覆盖）");
        hint.getStyleClass().add("stub-desc");
        VBox root = new VBox(10, pageTitle, hint, top, table, bottom);
        root.getStyleClass().add("page-wrapper");
        VBox.setVgrow(table, Priority.ALWAYS);
        root.setPadding(new Insets(12));
        return root;
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void chooseDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择要整理的目录");
        File d = chooser.showDialog(owner());
        if (d != null) {
            dirField.setText(d.getAbsolutePath());
            scan();
        }
    }

    private void scan() {
        String dir = dirField.getText();
        if (dir == null || dir.isBlank()) {
            status.setText("请先选择目录");
            return;
        }
        try {
            var result = OrganizeEngine.scan(Paths.get(dir));
            groups = result.groups();
            table.getItems().setAll(groups.entrySet().stream()
                    .map(e -> new Row(e.getKey(), e.getValue().size(),
                            e.getValue().stream().mapToLong(ScannedFile::size).sum()))
                    .toList());
            noDateLabel.setText("无日期文件：" + result.noDateFiles().size());
            runBtn.setDisable(groups.isEmpty());
            status.setText("扫描完成：" + groups.size() + " 个日期分组");
        } catch (Exception e) {
            status.setText("扫描失败：" + e.getMessage());
        }
    }

    private void run() {
        if (groups.isEmpty()) return;
        runBtn.setDisable(true);
        javafx.concurrent.Task<OrganizeEngine.OrganizeResult> task = new javafx.concurrent.Task<>() {
            @Override
            protected OrganizeEngine.OrganizeResult call() {
                return OrganizeEngine.execute(Paths.get(dirField.getText()), groups);
            }
        };
        task.setOnSucceeded(e -> {
            OrganizeEngine.OrganizeResult r = task.getValue();
            status.setText("完成：" + r.success() + " 个成功" + (r.fail() > 0 ? "，" + r.fail() + " 个失败" : ""));
            toast("自动整理完成：" + r.success() + " 个文件已归档");
            scan();
        });
        task.setOnFailed(e -> {
            status.setText("整理失败：" + task.getException().getMessage());
            runBtn.setDisable(false);
        });
        new Thread(task, "organize-run").start();
    }
}
