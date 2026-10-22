package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.LinkRouter;
import cn.fmjj.toolbox.engine.store.MediaStore;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;
import javafx.stage.Window;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.function.Consumer;

/**
 * 📚 已获取页：浏览解析/下载记录（config/collected.db）。
 * 对应 Python 版 gui_qt/pages/info_results.py 的核心功能：
 * 平台筛选、打开文件/目录、删除记录、导出 CSV。
 */
public class InfoResultsPage extends BasePage {

    private final TableView<MediaStore.Row> table = new TableView<>();
    private final ComboBox<String> platformFilter = new ComboBox<>();
    private final Label status = new Label("就绪");

    private Consumer<String> toast;

    @Override
    public String title() { return "📚 已获取"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        platformFilter.setItems(FXCollections.observableArrayList(
                "全部", "B站", "微博", "抖音", "快手", "小红书"));
        platformFilter.getSelectionModel().select(0);
        platformFilter.setOnAction(e -> refresh());

        Button refresh = new Button("🔄 刷新");
        refresh.setOnAction(e -> refresh());
        Button openFile = new Button("📂 打开文件");
        openFile.setOnAction(e -> openFile());
        Button openDir = new Button("📁 打开所在目录");
        openDir.setOnAction(e -> openDir());
        Button exportCsv = new Button("📤 导出 CSV");
        exportCsv.setOnAction(e -> exportCsv());
        Button delete = new Button("🗑 删除记录");
        delete.setOnAction(e -> deleteSelected());

        HBox top = new HBox(8, new Label("平台"), platformFilter, refresh, openFile, openDir, exportCsv,
                delete, status);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(status, Priority.ALWAYS);

        TableColumn<MediaStore.Row, String> cPlatform = new TableColumn<>("平台");
        cPlatform.setCellValueFactory(d -> new SimpleStringProperty(
                LinkRouter.platformName(d.getValue().platform())));
        cPlatform.setPrefWidth(80);
        TableColumn<MediaStore.Row, String> cTitle = new TableColumn<>("标题");
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().title()));
        cTitle.setPrefWidth(320);
        TableColumn<MediaStore.Row, String> cAuthor = new TableColumn<>("作者");
        cAuthor.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().author()));
        cAuthor.setPrefWidth(120);
        TableColumn<MediaStore.Row, String> cPublish = new TableColumn<>("发布时间");
        cPublish.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().publishTime()));
        cPublish.setPrefWidth(150);
        TableColumn<MediaStore.Row, String> cSaved = new TableColumn<>("保存路径");
        cSaved.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().savedPath()));
        cSaved.setPrefWidth(300);
        TableColumn<MediaStore.Row, String> cCreated = new TableColumn<>("入库时间");
        cCreated.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().createdAt()));
        cCreated.setPrefWidth(150);
        table.getColumns().addAll(List.of(cPlatform, cTitle, cAuthor, cPublish, cSaved, cCreated));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("解析/下载成功的条目自动入库（config/collected.db）；可筛选、打开、导出 CSV");
        hint.getStyleClass().add("stub-desc");

        VBox root = new VBox(10, pageTitle, hint, top, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        refresh();
        return root;
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private String platformKey() {
        return switch (platformFilter.getSelectionModel().getSelectedIndex()) {
            case 1 -> "bilibili";
            case 2 -> "weibo";
            case 3 -> "douyin";
            case 4 -> "kuaishou";
            case 5 -> "xiaohongshu";
            default -> "";
        };
    }

    private void refresh() {
        try (MediaStore store = new MediaStore()) {
            List<MediaStore.Row> rows = store.list(platformKey(), 1000);
            table.getItems().setAll(rows);
            status.setText("共 " + rows.size() + " 条" + (rows.size() >= 1000 ? "（仅显示最近 1000 条）" : ""));
        } catch (Exception e) {
            status.setText("读取失败：" + e.getMessage());
        }
    }

    private void openFile() {
        MediaStore.Row row = table.getSelectionModel().getSelectedItem();
        if (row == null) {
            status.setText("请先选中一行");
            return;
        }
        if (row.savedPath() == null || row.savedPath().isBlank()) {
            status.setText("该记录没有保存路径（可能只解析未下载）");
            return;
        }
        Path p = Paths.get(row.savedPath());
        if (Files.isDirectory(p)) {
            AudioEngine.openInExplorer(p);
            return;
        }
        if (!Files.isRegularFile(p)) {
            status.setText("文件不存在：" + p);
            return;
        }
        try {
            if (java.awt.Desktop.isDesktopSupported()) {
                java.awt.Desktop.getDesktop().open(new File(p.toString()));
            }
        } catch (Exception e) {
            status.setText("打开失败：" + e.getMessage());
        }
    }

    private void openDir() {
        MediaStore.Row row = table.getSelectionModel().getSelectedItem();
        if (row == null || row.savedPath() == null || row.savedPath().isBlank()) {
            status.setText("请先选中带保存路径的行");
            return;
        }
        Path p = Paths.get(row.savedPath());
        Path dir = Files.isDirectory(p) ? p : p.getParent();
        if (dir != null) AudioEngine.openInExplorer(dir);
    }

    private void deleteSelected() {
        MediaStore.Row row = table.getSelectionModel().getSelectedItem();
        if (row == null) {
            status.setText("请先选中一行");
            return;
        }
        try (MediaStore store = new MediaStore()) {
            store.delete(row.id());
            refresh();
            toast("已删除记录：" + row.title());
        } catch (Exception e) {
            status.setText("删除失败：" + e.getMessage());
        }
    }

    private void exportCsv() {
        if (table.getItems().isEmpty()) {
            status.setText("没有可导出的数据");
            return;
        }
        FileChooser chooser = new FileChooser();
        chooser.setTitle("导出已获取数据");
        chooser.setInitialFileName("已获取数据.csv");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("CSV", "*.csv"));
        File file = chooser.showSaveDialog(owner());
        if (file == null) return;
        try {
            StringBuilder sb = new StringBuilder();
            sb.append("平台,标题,作者,发布时间,时长,保存路径,入库时间\n");
            for (MediaStore.Row r : table.getItems()) {
                sb.append(csv(LinkRouter.platformName(r.platform()))).append(',')
                        .append(csv(r.title())).append(',')
                        .append(csv(r.author())).append(',')
                        .append(csv(r.publishTime())).append(',')
                        .append(r.duration()).append(',')
                        .append(csv(r.savedPath())).append(',')
                        .append(csv(r.createdAt())).append('\n');
            }
            byte[] bom = {(byte) 0xEF, (byte) 0xBB, (byte) 0xBF};
            byte[] body = sb.toString().getBytes(StandardCharsets.UTF_8);
            byte[] all = new byte[bom.length + body.length];
            System.arraycopy(bom, 0, all, 0, bom.length);
            System.arraycopy(body, 0, all, bom.length, body.length);
            Files.write(file.toPath(), all);
            status.setText("已导出：" + file.getName());
            toast("导出完成：" + file.getAbsolutePath());
        } catch (Exception e) {
            status.setText("导出失败：" + e.getMessage());
        }
    }

    private static String csv(String value) {
        if (value == null) return "\"\"";
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }
}
