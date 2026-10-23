package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
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
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * ✂️ 图片裁切：批量按比例裁切/缩放（对应 Python 版 image_crop）。
 */
public class ImageCropPage extends BasePage {

    private static final class Row {
        final Path file;
        String status = "";
        Row(Path file) { this.file = file; }
    }

    private final TextField dirField = new TextField();
    private final ComboBox<String> modeBox = new ComboBox<>(FXCollections.observableArrayList(
            "居中裁正方形", "居中裁 16:9", "居中裁 3:4", "强制拉伸"));
    private final TextField widthField = new TextField("1080");
    private final TextField heightField = new TextField("1080");
    private final TableView<Row> table = new TableView<>();
    private final Label status = new Label("就绪");
    private final ProgressBar progress = new ProgressBar(0);
    private final AtomicBoolean stop = new AtomicBoolean(false);

    private Consumer<String> toast;

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    public String title() { return "✂️ 图片裁切"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    @Override
    protected Node build() {
        modeBox.getSelectionModel().select(0);
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());
        Button run = new Button("✂️ 开始处理");
        run.setOnAction(e -> run(run));
        Button openOut = new Button("📁 打开输出目录");
        openOut.setOnAction(e -> AudioEngine.openInExplorer(outDir()));

        HBox top = new HBox(8, new Label("目录"), dirField, browse, scan,
                new Label("方式"), modeBox, new Label("宽"), widthField, new Label("高"), heightField,
                run, openOut, progress, status);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(dirField, Priority.ALWAYS);
        widthField.setPrefWidth(70);
        heightField.setPrefWidth(70);

        TableColumn<Row, String> cName = new TableColumn<>("文件");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().file.getFileName().toString()));
        cName.setPrefWidth(320);
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(
                AudioEngine.formatSize(AudioEngine.sizeOf(d.getValue().file))));
        cSize.setPrefWidth(110);
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().status));
        cStatus.setPrefWidth(240);
        table.getColumns().addAll(List.of(cName, cSize, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("扫描目录里的图片 → 选裁切方式与目标尺寸 → 批量输出到「导出目录/图片处理」；"
                + "等比模式不会放大，只缩小");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Path outDir() { return Settings.load().effectiveExportDir().resolve("图片处理"); }

    private Window owner() { return table.getScene() == null ? null : table.getScene().getWindow(); }

    private void chooseDir() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择图片目录");
        File d = c.showDialog(owner());
        if (d != null) {
            dirField.setText(d.getAbsolutePath());
            scan();
        }
    }

    private void scan() {
        String dir = dirField.getText().trim();
        if (dir.isEmpty()) { status.setText("请先选择目录"); return; }
        List<Row> rows = new ArrayList<>();
        try (Stream<Path> s = Files.walk(Paths.get(dir))) {
            s.filter(Files::isRegularFile).filter(ImageTools::isImage).sorted()
                    .forEach(p -> rows.add(new Row(p)));
        } catch (Exception e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        table.setItems(FXCollections.observableArrayList(rows));
        status.setText("扫描到 " + rows.size() + " 张图片");
    }

    private ImageTools.CropMode mode() {
        return switch (modeBox.getSelectionModel().getSelectedIndex()) {
            case 1 -> ImageTools.CropMode.CENTER_16_9;
            case 2 -> ImageTools.CropMode.CENTER_3_4;
            case 3 -> ImageTools.CropMode.STRETCH;
            default -> ImageTools.CropMode.CENTER_SQUARE;
        };
    }

    private int intOf(TextField f, int def) {
        try {
            return Integer.parseInt(f.getText().trim());
        } catch (Exception e) {
            return def;
        }
    }

    private void run(Button runBtn) {
        if (table.getItems().isEmpty()) { status.setText("请先扫描"); return; }
        stop.set(false);
        runBtn.setDisable(true);
        var rows = new ArrayList<>(table.getItems());
        Path out = outDir();
        var m = mode();
        int w = intOf(widthField, 0);
        int h = intOf(heightField, 0);

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (int i = 0; i < rows.size(); i++) {
                    if (stop.get()) break;
                    Row row = rows.get(i);
                    try {
                        var img = ImageTools.read(row.file);
                        var cropped = ImageTools.crop(img, m, w, h);
                        Path target = out.resolve(row.file.getFileName().toString());
                        ImageTools.write(cropped, target);
                        row.status = "✅ " + cropped.getWidth() + "×" + cropped.getHeight();
                        ok++;
                    } catch (Exception e) {
                        row.status = "❌ " + e.getMessage();
                        fail++;
                    }
                    int idx = i;
                    javafx.application.Platform.runLater(() -> {
                        table.refresh();
                        progress.setProgress((double) (idx + 1) / rows.size());
                        status.setText("处理中… " + (idx + 1) + "/" + rows.size());
                    });
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            runBtn.setDisable(false);
            progress.setProgress(1);
            status.setText("完成：成功 " + r[0] + "，失败 " + r[1]);
            if (r[0] > 0) toast("图片处理完成：" + r[0] + " 张");
        });
        task.setOnFailed(e -> {
            runBtn.setDisable(false);
            status.setText("异常：" + task.getException().getMessage());
        });
        new Thread(task, "image-crop").start();
    }
}
