package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.media.Ffmpeg;
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
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🗜 媒体压缩：批量视频压缩（libx264 + CRF，对应 Python 版 media_compress）。
 */
public class MediaCompressPage extends BasePage {

    private static final Set<String> VIDEO_EXTS = Set.of(
            ".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".webm", ".m4v");

    private static final class Row {
        final Path file;
        String status = "";
        Row(Path file) { this.file = file; }
    }

    private final TextField dirField = new TextField();
    private final ComboBox<String> presetBox = new ComboBox<>(FXCollections.observableArrayList(
            "高质量（CRF 20）", "均衡（CRF 23）", "小体积（CRF 28）", "只压到 720p（CRF 26）"));
    private final TableView<Row> table = new TableView<>();
    private final Label status = new Label("就绪");
    private final ProgressBar progress = new ProgressBar(0);
    private final AtomicBoolean stop = new AtomicBoolean(false);

    private Consumer<String> toast;

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    public String title() { return "🗜 媒体压缩"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    @Override
    protected Node build() {
        presetBox.getSelectionModel().select(1);
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());
        Button run = new Button("🗜 开始压缩");
        run.setOnAction(e -> run(run));
        Button openOut = new Button("📁 打开输出目录");
        openOut.setOnAction(e -> AudioEngine.openInExplorer(outDir()));

        HBox top = new HBox(8, new Label("目录"), dirField, browse, scan,
                new Label("预设"), presetBox, run, openOut, progress, status);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(dirField, Priority.ALWAYS);

        TableColumn<Row, String> cName = new TableColumn<>("文件");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().file.getFileName().toString()));
        cName.setPrefWidth(340);
        TableColumn<Row, String> cSize = new TableColumn<>("原大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(
                AudioEngine.formatSize(AudioEngine.sizeOf(d.getValue().file))));
        cSize.setPrefWidth(110);
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().status));
        cStatus.setPrefWidth(320);
        table.getColumns().addAll(List.of(cName, cSize, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("用 FFmpeg libx264 + CRF 压缩视频，输出到「导出目录/压缩」；"
                + "原文件保留不动，压缩结果同名 .mp4");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Path outDir() { return Settings.load().effectiveExportDir().resolve("压缩"); }

    private Window owner() { return table.getScene() == null ? null : table.getScene().getWindow(); }

    private void chooseDir() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择视频目录");
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
            s.filter(Files::isRegularFile).filter(p -> VIDEO_EXTS.contains(ext(p))).sorted()
                    .forEach(p -> rows.add(new Row(p)));
        } catch (Exception e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        table.setItems(FXCollections.observableArrayList(rows));
        status.setText("扫描到 " + rows.size() + " 个视频");
    }

    private static String ext(Path p) {
        String n = p.getFileName().toString().toLowerCase(java.util.Locale.ROOT);
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot) : "";
    }

    /** 预设 → ffmpeg 参数。 */
    private List<String> ffmpegArgs(Path in, Path out) {
        List<String> args = new ArrayList<>(List.of(
                Ffmpeg.ffmpegExe(), "-y", "-i", in.toString(),
                "-c:v", "libx264", "-preset", "medium", "-c:a", "aac", "-b:a", "128k"));
        int idx = presetBox.getSelectionModel().getSelectedIndex();
        switch (idx) {
            case 0 -> args.addAll(List.of("-crf", "20"));
            case 2 -> args.addAll(List.of("-crf", "28"));
            case 3 -> args.addAll(List.of("-crf", "26", "-vf", "scale=-2:720"));
            default -> args.addAll(List.of("-crf", "23"));
        }
        args.add(out.toString());
        return args;
    }

    private void run(Button runBtn) {
        if (!Ffmpeg.available()) {
            status.setText("未找到 FFmpeg（可在全局设置里指定路径）");
            return;
        }
        if (table.getItems().isEmpty()) { status.setText("请先扫描"); return; }
        stop.set(false);
        runBtn.setDisable(true);
        var rows = new ArrayList<>(table.getItems());
        Path out = outDir();

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (int i = 0; i < rows.size(); i++) {
                    if (stop.get()) break;
                    Row row = rows.get(i);
                    String base = row.file.getFileName().toString();
                    int dot = base.lastIndexOf('.');
                    Path target = out.resolve((dot > 0 ? base.substring(0, dot) : base) + ".mp4");
                    var r = Ffmpeg.run(ffmpegArgs(row.file, target), 1800);
                    long before = AudioEngine.sizeOf(row.file);
                    long after = AudioEngine.sizeOf(target);
                    if (r.ok() && after > 0) {
                        row.status = "✅ " + AudioEngine.formatSize(before) + " → " + AudioEngine.formatSize(after);
                        ok++;
                    } else {
                        row.status = "❌ " + (r.timedOut() ? "超时" : tail(r.stderr()));
                        fail++;
                    }
                    int idx = i;
                    javafx.application.Platform.runLater(() -> {
                        table.refresh();
                        progress.setProgress((double) (idx + 1) / rows.size());
                        status.setText("压缩中… " + (idx + 1) + "/" + rows.size());
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
            if (r[0] > 0) toast("压缩完成：" + r[0] + " 个");
        });
        task.setOnFailed(e -> {
            runBtn.setDisable(false);
            status.setText("异常：" + task.getException().getMessage());
        });
        new Thread(task, "media-compress").start();
    }

    private static String tail(String s) {
        if (s == null) return "";
        String t = s.strip();
        return t.length() > 120 ? t.substring(t.length() - 120) : t;
    }
}
