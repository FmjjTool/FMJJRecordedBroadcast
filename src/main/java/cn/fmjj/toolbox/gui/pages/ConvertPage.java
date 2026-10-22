package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.media.ConvertEngine;
import cn.fmjj.toolbox.engine.media.ConvertEngine.ConvertConfig;
import cn.fmjj.toolbox.engine.media.ConvertEngine.ConvertResult;
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
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.Window;

import java.io.File;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 🔄 视频转换页。移植自 Python 版 gui_qt/pages/convert.py（核心链路）。
 *
 * 无损封装优先（-c copy）→ 可选转码兜底 → 失败重试；已存在目标自动跳过。
 */
public class ConvertPage extends BasePage {

    private record Row(Path src, String size, String status) {}

    private final TextField dirField = new TextField();
    private final TextField outDirField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final TextArea log = new TextArea();
    private final CheckBox fallback = new CheckBox("无损失败时转码兜底");
    private final CheckBox preserveTs = new CheckBox("保留时间戳");
    private final CheckBox deleteSrc = new CheckBox("成功后删除源文件");
    private final TextField retryField = new TextField("2");
    private final Label status = new Label("就绪");
    private final ProgressIndicator progress = new ProgressIndicator();
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);
    private final Button startBtn = new Button("▶ 开始转换");

    private Consumer<String> toast;

    @Override
    public String title() { return "🔄 视频转换"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        preserveTs.setSelected(true);
        progress.setVisible(false);
        progress.setPrefSize(18, 18);
        log.setEditable(false);
        log.setPrefRowCount(8);

        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir(dirField, "选择录播目录"));
        Button browseOut = new Button("📂 浏览");
        browseOut.setOnAction(e -> chooseDir(outDirField, "选择输出目录（留空=源同目录）"));
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());

        HBox dirRow = new HBox(8, new Label("录播目录"), dirField, browse, scan);
        dirRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(dirField, Priority.ALWAYS);
        HBox outRow = new HBox(8, new Label("输出目录"), outDirField, browseOut);
        outRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(outDirField, Priority.ALWAYS);

        TableColumn<Row, String> cName = new TableColumn<>("文件");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().src().getFileName().toString()));
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().size()));
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().status()));
        table.getColumns().addAll(List.of(cName, cSize, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        HBox opts = new HBox(12, fallback, preserveTs, deleteSrc,
                new Label("重试次数"), retryField);
        opts.setAlignment(Pos.CENTER_LEFT);
        retryField.setPrefWidth(50);

        startBtn.setOnAction(e -> start());
        Button stopBtn = new Button("⏹ 停止");
        stopBtn.setOnAction(e -> stopFlag.set(true));
        HBox actionRow = new HBox(10, startBtn, stopBtn, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("无损封装优先（-c copy，速度快、画质不变）；已存在目标文件自动跳过");
        hint.getStyleClass().add("stub-desc");

        VBox root = new VBox(10, pageTitle, hint, dirRow, outRow, opts, table, actionRow, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        Path work = Settings.load().effectiveWorkDir();
        dirField.setText(work.toString());
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

    private ConvertConfig config() {
        int retry;
        try {
            retry = Integer.parseInt(retryField.getText().trim());
        } catch (Exception e) {
            retry = 2;
        }
        ConvertConfig def = ConvertConfig.defaults();
        return new ConvertConfig(def.outputExtension(), outDirField.getText().trim(),
                deleteSrc.isSelected(), Math.max(0, retry), def.retryDelaySeconds(),
                fallback.isSelected(), def.encodeVideoCodec(), def.encodeCrf(), def.encodePreset(),
                def.encodeAudioCopy(), def.preserveMetadata(), preserveTs.isSelected(),
                def.inputExtensions());
    }

    private void scan() {
        String dir = dirField.getText().trim();
        if (dir.isEmpty()) {
            status.setText("请先选择目录");
            return;
        }
        List<Path> videos = ConvertEngine.findVideos(Paths.get(dir), config());
        List<Row> rows = new ArrayList<>();
        for (Path p : videos) rows.add(new Row(p, AudioEngine.formatSize(AudioEngine.sizeOf(p)), ""));
        table.getItems().setAll(rows);
        status.setText("扫描到 " + rows.size() + " 个视频");
    }

    private void appendLog(String text) {
        Platform.runLater(() -> {
            log.appendText(text + "\n");
            log.setScrollTop(Double.MAX_VALUE);
        });
    }

    private void start() {
        String dir = dirField.getText().trim();
        if (dir.isEmpty() || table.getItems().isEmpty()) {
            status.setText("请先扫描出待转换文件");
            return;
        }
        stopFlag.set(false);
        startBtn.setDisable(true);
        progress.setVisible(true);
        log.clear();
        status.setText("转换中…");

        Path root = Paths.get(dir);
        ConvertConfig cfg = config();
        List<Row> rows = new ArrayList<>(table.getItems());

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, skip = 0, fail = 0;
                for (Row row : rows) {
                    if (stopFlag.get()) break;
                    ConvertResult r = ConvertEngine.convertOne(row.src(), root, cfg,
                            msg -> appendLog("[" + row.src().getFileName() + "] " + msg),
                            stopFlag::get);
                    if (r.success()) {
                        if ("skip".equals(r.mode())) skip++;
                        else ok++;
                    } else fail++;
                }
                return new int[] {ok, skip, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            startBtn.setDisable(false);
            progress.setVisible(false);
            status.setText("完成：成功 " + r[0] + "，跳过 " + r[1] + "，失败 " + r[2]);
            toast("视频转换结束：" + r[0] + " 成功 / " + r[2] + " 失败");
            scan();
        });
        task.setOnFailed(e -> {
            startBtn.setDisable(false);
            progress.setVisible(false);
            status.setText("转换异常：" + task.getException().getMessage());
        });
        new Thread(task, "video-convert").start();
    }
}
