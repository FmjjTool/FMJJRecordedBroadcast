package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.MusicUnlock;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
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
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🔓 音乐解密页：批量解密加密音乐（网易云 NCM / QQ音乐 QMC / 酷狗 KGM / 酷我 KWM）。
 * 移植自 Python 版 gui_qt/pages/music_unlock.py 的核心链路。
 */
public class MusicUnlockPage extends BasePage {

    private static final class Row {
        final Path file;
        String status = "";
        Row(Path file) { this.file = file; }
    }

    private final TextField dirField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final CheckBox deleteSource = new CheckBox("解密成功后删除原加密文件（进回收站）");
    private final CheckBox recursive = new CheckBox("包含子文件夹");
    private final Label status = new Label("就绪");
    private final ProgressBar progress = new ProgressBar(0);
    private final Button runBtn = new Button("🔓 开始解密");
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);

    private Consumer<String> toast;

    @Override
    public String title() { return "🔓 音乐解密"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        progress.setPrefWidth(200);
        recursive.setSelected(true);

        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());
        Button openOut = new Button("📁 打开输出目录");
        openOut.setOnAction(e -> AudioEngine.openInExplorer(outDir()));

        HBox top = new HBox(8, new Label("目录"), dirField, browse, scan, openOut);
        top.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(dirField, Priority.ALWAYS);

        TableColumn<Row, String> cName = new TableColumn<>("文件");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().file.getFileName().toString()));
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(
                AudioEngine.formatSize(AudioEngine.sizeOf(d.getValue().file))));
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().status));
        table.getColumns().addAll(List.of(cName, cSize, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        runBtn.setOnAction(e -> run());
        Button stop = new Button("⏹ 停止");
        stop.setOnAction(e -> stopFlag.set(true));
        HBox actions = new HBox(10, runBtn, stop, deleteSource, recursive, progress, status);
        actions.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("支持：NCM（网易云）/ QMC 系列（QQ音乐 v1+v2）/ KGM·KGMA·VPR（酷狗）/ KWM（酷我）；"
                + "输出按歌曲元数据命名到「导出目录/解密音乐」");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table, actions);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Path outDir() {
        return Settings.load().effectiveExportDir().resolve("解密音乐");
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void chooseDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择含加密音乐的目录");
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
        List<Row> rows = new ArrayList<>();
        try (Stream<Path> s = Files.walk(Paths.get(dir), recursive.isSelected() ? Integer.MAX_VALUE : 1)) {
            s.filter(Files::isRegularFile)
                    .filter(p -> MusicUnlock.isSupported(extOf(p)))
                    .sorted()
                    .forEach(p -> rows.add(new Row(p)));
        } catch (IOException e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        table.getItems().setAll(rows);
        status.setText("扫描到 " + rows.size() + " 个加密文件");
    }

    private static String extOf(Path p) {
        String n = p.getFileName().toString();
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot + 1) : "";
    }

    private void run() {
        if (table.getItems().isEmpty()) {
            status.setText("请先扫描出待解密文件");
            return;
        }
        stopFlag.set(false);
        runBtn.setDisable(true);
        progress.setProgress(0);
        List<Row> rows = new ArrayList<>(table.getItems());
        Path out = outDir();
        boolean remove = deleteSource.isSelected();

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (int i = 0; i < rows.size(); i++) {
                    if (stopFlag.get()) break;
                    Row row = rows.get(i);
                    try {
                        Path saved = MusicUnlock.decryptTo(row.file, out);
                        row.status = "✅ " + saved.getFileName();
                        ok++;
                        if (remove) {
                            var del = AudioEngine.deleteFilesSafe(List.of(row.file));
                            if (del.ok() == 0) row.status += "（源删除失败）";
                        }
                    } catch (Exception e) {
                        row.status = "❌ " + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
                        fail++;
                    }
                    int idx = i;
                    Platform.runLater(() -> {
                        table.refresh();
                        progress.setProgress((double) (idx + 1) / rows.size());
                        status.setText("解密中… " + (idx + 1) + "/" + rows.size());
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
            toast("解密完成：" + r[0] + " 个成功 / " + r[1] + " 失败");
        });
        task.setOnFailed(e -> {
            runBtn.setDisable(false);
            status.setText("解密异常：" + task.getException().getMessage());
        });
        new Thread(task, "music-unlock").start();
    }
}
