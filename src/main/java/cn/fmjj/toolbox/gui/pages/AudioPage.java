package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressIndicator;
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

/**
 * 🎵 音频提取页。移植自 Python 版 gui_qt/pages/audio.py（核心链路）：
 * 视频批量提取音轨（-acodec copy → .m4a）、按日期分组浏览、可选删除源文件（回收站）。
 */
public class AudioPage extends BasePage {

    private static final class Row {
        final Path video;
        String status = "";
        Row(Path video) { this.video = video; }
    }

    private final TextField dirField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final CheckBox deleteSource = new CheckBox("提取成功后删除源视频（进回收站）");
    private final Label status = new Label("就绪");
    private final ProgressIndicator progress = new ProgressIndicator();
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);
    private final Button extractBtn = new Button("▶ 开始提取");

    private Consumer<String> toast;

    @Override
    public String title() { return "🎵 音频提取"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        progress.setVisible(false);
        progress.setPrefSize(18, 18);

        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描视频");
        scan.setOnAction(e -> scan());
        Button openDir = new Button("📁 打开目录");
        openDir.setOnAction(e -> {
            String d = dirField.getText().trim();
            if (!d.isEmpty()) AudioEngine.openInExplorer(Paths.get(d));
        });

        HBox top = new HBox(8, new Label("录播目录"), dirField, browse, scan, openDir);
        top.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(dirField, Priority.ALWAYS);

        TableColumn<Row, String> cName = new TableColumn<>("视频文件");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().video.getFileName().toString()));
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(
                AudioEngine.formatSize(AudioEngine.sizeOf(d.getValue().video))));
        TableColumn<Row, String> cTarget = new TableColumn<>("目标音频");
        cTarget.setCellValueFactory(d -> new SimpleStringProperty(
                AudioEngine.audioTarget(d.getValue().video).getFileName().toString()));
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().status));
        table.getColumns().addAll(List.of(cName, cSize, cTarget, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        extractBtn.setOnAction(e -> start());
        Button stopBtn = new Button("⏹ 停止");
        stopBtn.setOnAction(e -> stopFlag.set(true));
        HBox actionRow = new HBox(10, extractBtn, stopBtn, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("无损提取音轨（-acodec copy）为 .m4a，速度极快；"
                + "已存在的音频自动跳过" + (AudioEngine.trashSupported() ? "" : "（本机不支持回收站，删除将不可恢复）"));
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table, deleteSource, actionRow);
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

    private void chooseDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择录播目录");
        File d = chooser.showDialog(owner());
        if (d != null) {
            dirField.setText(d.getAbsolutePath());
            scan();
        }
    }

    private void scan() {
        String dir = dirField.getText().trim();
        if (dir.isEmpty()) {
            status.setText("请先选择目录");
            return;
        }
        List<Path> videos = AudioEngine.findAudioVideos(Paths.get(dir));
        List<Row> rows = new ArrayList<>();
        for (Path p : videos) {
            Row r = new Row(p);
            Path target = AudioEngine.audioTarget(p);
            if (Files.isRegularFile(target)) r.status = "已存在音频";
            rows.add(r);
        }
        table.getItems().setAll(rows);
        status.setText("扫描到 " + rows.size() + " 个视频");
    }

    private void start() {
        if (table.getItems().isEmpty()) {
            status.setText("请先扫描视频");
            return;
        }
        stopFlag.set(false);
        extractBtn.setDisable(true);
        progress.setVisible(true);
        status.setText("提取中…");
        List<Row> rows = new ArrayList<>(table.getItems());
        boolean removeSrc = deleteSource.isSelected();

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, skip = 0, fail = 0;
                for (Row row : rows) {
                    if (stopFlag.get()) break;
                    Path target = AudioEngine.audioTarget(row.video);
                    if (Files.isRegularFile(target)) {
                        row.status = "跳过（已存在）";
                        skip++;
                        Platform.runLater(table::refresh);
                        continue;
                    }
                    var r = AudioEngine.extractAudioOne(row.video, target);
                    if (r.success()) {
                        ok++;
                        row.status = "✅ 已提取";
                        if (removeSrc) {
                            var del = AudioEngine.deleteFilesSafe(List.of(row.video));
                            row.status += del.ok() > 0 ? "，源已删除" : "，源删除失败";
                        }
                    } else {
                        fail++;
                        row.status = "❌ " + (r.error().length() > 60 ? r.error().substring(r.error().length() - 60) : r.error());
                    }
                    Platform.runLater(table::refresh);
                }
                return new int[] {ok, skip, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            extractBtn.setDisable(false);
            progress.setVisible(false);
            status.setText("完成：提取 " + r[0] + "，跳过 " + r[1] + "，失败 " + r[2]);
            toast("音频提取结束：" + r[0] + " 成功 / " + r[2] + " 失败");
        });
        task.setOnFailed(e -> {
            extractBtn.setDisable(false);
            progress.setVisible(false);
            status.setText("提取异常：" + task.getException().getMessage());
        });
        new Thread(task, "audio-extract").start();
    }
}
