package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.media.Ffmpeg;
import javafx.application.Platform;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.Slider;
import javafx.scene.control.SplitPane;
import javafx.scene.control.cell.CheckBoxListCell;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🗜 媒体压缩（对齐 Python 版 media_compress.py 的结构）。
 *
 * <p>顶栏：`📁 添加文件` / `📂 添加文件夹` / `☑️ 全选` / `🔄 反选` / `🗑️ 清空列表` / 伸展 /
 * `共 N 个文件` / `▶ 压缩勾选`(蓝 #2563eb) / `⏹ 停止`；
 * 主体 Splitter：左**可勾选文件列表** + 右参数滚动区（🖼 图片参数 / 🎬 视频参数 /
 * ⚙ CPU 占用限制 滑块 20-100 默认 55）。
 */
public class MediaCompressPage extends BasePage {

    private static final Set<String> VIDEO_EXTS = Set.of(
            ".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".webm", ".m4v");
    private static final Set<String> IMAGE_EXTS = Set.of(
            ".jpg", ".jpeg", ".png", ".bmp", ".webp");

    /** 一行：路径 + 勾选 + 状态。 */
    private static final class Item {
        final Path path;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(true);
        String status = "";
        Item(Path path) { this.path = path; }
        boolean isVideo() { return VIDEO_EXTS.contains(extOf(path)); }
        @Override public String toString() {
            String mark = status.isEmpty() ? "" : "  " + status;
            return path.getFileName() + mark;
        }
    }

    private final ListView<Item> fileList = new ListView<>();
    private final List<Item> items = new ArrayList<>();
    private final Label countLabel = new Label("共 0 个文件");
    private final Label selectedInfo = new Label("选中文件: -");
    private final Label estimate = new Label("大小预估: -");
    private final ComboBox<String> presetBox = new ComboBox<>(FXCollections.observableArrayList(
            "高质量", "均衡（推荐）", "小体积", "只压到 720p"));
    private final Slider qualitySlider = new Slider(1, 100, 70);
    private final ComboBox<String> resolutionBox = new ComboBox<>(FXCollections.observableArrayList(
            "原始分辨率", "1080p", "720p", "480p"));
    private final ComboBox<String> codecBox = new ComboBox<>(FXCollections.observableArrayList(
            "H.264（兼容好）", "H.265（体积小）"));
    private final CheckBox stripMeta = new CheckBox("清除元数据");
    private final Slider cpuSlider = new Slider(20, 100, 55);
    private final Label cpuLabel = new Label("55%");
    private final Button startBtn = new Button("▶ 压缩勾选");
    private final Button stopBtn = new Button("⏹ 停止");
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);

    private Consumer<String> toast;

    @Override
    public String title() { return "🗜 媒体压缩"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Button addFiles = new Button("📁 添加文件");
        addFiles.setOnAction(e -> addFiles());
        Button addFolder = new Button("📂 添加文件夹");
        addFolder.setOnAction(e -> addFolder());
        Button selectAll = new Button("☑️ 全选");
        selectAll.setOnAction(e -> setAll(true));
        Button invert = new Button("🔄 反选");
        invert.setOnAction(e -> {
            for (Item it : items) it.checked.set(!it.checked.get());
            fileList.refresh();
            updateCount();
        });
        Button clear = new Button("🗑️ 清空列表");
        clear.setOnAction(e -> {
            items.clear();
            fileList.getItems().clear();
            updateCount();
        });
        countLabel.setStyle("-fx-text-fill: #6b7280;");
        startBtn.setStyle("-fx-background-color: #2563eb; -fx-text-fill: white; "
                + "-fx-font-weight: 600; -fx-padding: 6 16 6 16; -fx-background-radius: 4;");
        startBtn.setOnAction(e -> startCompress());
        stopBtn.setTooltip(new javafx.scene.control.Tooltip("停止后续任务（正在压缩的文件会完成后结束）"));
        stopBtn.setDisable(true);
        stopBtn.setOnAction(e -> {
            stopFlag.set(true);
            stopBtn.setDisable(true);
            toast("已请求停止");
        });

        HBox top = new HBox(6, addFiles, addFolder, selectAll, invert, clear, spacer(),
                countLabel, startBtn, stopBtn);
        top.setAlignment(Pos.CENTER_LEFT);

        fileList.setCellFactory(CheckBoxListCell.forListView(item -> item.checked));
        fileList.setStyle("-fx-background-color: white; -fx-border-color: #d0d6df; -fx-border-radius: 4;");
        fileList.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> {
            if (b == null) {
                selectedInfo.setText("选中文件: -");
                estimate.setText("大小预估: -");
            } else {
                selectedInfo.setText("选中文件: " + b.path.getFileName()
                        + "（" + AudioEngine.formatSize(AudioEngine.sizeOf(b.path)) + "）");
                estimate.setText("大小预估: " + b.path.getFileName()
                        + (b.isVideo() ? "（视频，按 CRF 估算）" : "（图片，按质量估算）"));
            }
        });

        SplitPane split = new SplitPane(fileList, buildParamPanel());
        split.setDividerPositions(0.55);
        VBox.setVgrow(split, Priority.ALWAYS);

        VBox root = new VBox(8, top, split);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(split, Priority.ALWAYS);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private Node buildParamPanel() {
        selectedInfo.setStyle("-fx-text-fill: #6b7280;");
        selectedInfo.setWrapText(true);
        estimate.setStyle("-fx-text-fill: #6b7280;");

        // 图片参数
        qualitySlider.setShowTickMarks(true);
        qualitySlider.setShowTickLabels(false);
        qualitySlider.setBlockIncrement(5);
        qualitySlider.valueProperty().addListener((o, a, b) -> updateEstimateText());
        resolutionBox.getSelectionModel().selectFirst();
        codecBox.getSelectionModel().selectFirst();
        presetBox.getSelectionModel().select(1);
        presetBox.setOnAction(e -> applyPreset());
        Button applyPresetBtn = new Button("应用预设");
        applyPresetBtn.setOnAction(e -> applyPreset());

        VBox imageGroup = group("🖼 图片压缩参数",
                row(new Label("预设:"), presetBox, applyPresetBtn),
                row(new Label("质量:"), qualitySlider),
                row(new Label("分辨率:"), resolutionBox));

        VBox videoGroup = group("🎬 视频压缩参数",
                row(new Label("编码:"), codecBox),
                row(new Label("分辨率:"), resolutionBox),
                row(stripMeta));

        cpuSlider.setBlockIncrement(5);
        cpuSlider.valueProperty().addListener((o, a, b) ->
                cpuLabel.setText((int) Math.round(b.doubleValue()) + "%"));
        cpuSlider.setTooltip(new javafx.scene.control.Tooltip(
                "按逻辑核心百分比限制 ffmpeg 编码线程数，降低压缩时的 CPU 占用\n100% = 不限制（默认全速）；建议 50~60%"));
        VBox cpuGroup = group("⚙ CPU 占用限制", row(cpuSlider, cpuLabel));

        VBox body = new VBox(10, selectedInfo, imageGroup, videoGroup, cpuGroup, estimate);
        body.setPadding(new Insets(8));
        ScrollPane scroll = new ScrollPane(body);
        scroll.setFitToWidth(true);
        scroll.setStyle("-fx-background-color: white; -fx-border-color: #d0d6df; -fx-border-radius: 4;");
        return scroll;
    }

    private HBox row(Node... nodes) {
        HBox h = new HBox(6, nodes);
        h.setAlignment(Pos.CENTER_LEFT);
        for (Node n : nodes) if (n instanceof Slider) HBox.setHgrow(n, Priority.ALWAYS);
        return h;
    }

    private VBox group(String title, Node... rows) {
        Label l = new Label(title);
        l.setStyle("-fx-font-weight: 600; -fx-text-fill: #5a6474;");
        VBox box = new VBox(6, l);
        box.getChildren().addAll(rows);
        box.setPadding(new Insets(8));
        box.setStyle("-fx-border-color: #e0e4ea; -fx-border-radius: 4;");
        return box;
    }

    private void applyPreset() {
        switch (presetBox.getSelectionModel().getSelectedIndex()) {
            case 0 -> { qualitySlider.setValue(85); resolutionBox.getSelectionModel().selectFirst(); }
            case 2 -> { qualitySlider.setValue(45); resolutionBox.getSelectionModel().selectFirst(); }
            case 3 -> { qualitySlider.setValue(55); resolutionBox.getSelectionModel().select("720p"); }
            default -> { qualitySlider.setValue(70); resolutionBox.getSelectionModel().selectFirst(); }
        }
        updateEstimateText();
    }

    private void updateEstimateText() {
        Item it = fileList.getSelectionModel().getSelectedItem();
        if (it == null) return;
        long size = AudioEngine.sizeOf(it.path);
        double q = qualitySlider.getValue();
        double ratio = it.isVideo() ? 0.25 + (q / 100.0) * 0.55 : 0.15 + (q / 100.0) * 0.7;
        estimate.setText("大小预估: " + AudioEngine.formatSize((long) (size * ratio))
                + "（原 " + AudioEngine.formatSize(size) + "，粗略估算）");
    }

    // ==================== 文件 ====================

    private void addFiles() {
        FileChooser c = new FileChooser();
        c.setTitle("添加媒体文件");
        c.getExtensionFilters().addAll(
                new FileChooser.ExtensionFilter("媒体文件",
                        "*.mp4", "*.flv", "*.mkv", "*.ts", "*.mov", "*.avi", "*.wmv", "*.webm", "*.m4v",
                        "*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"),
                new FileChooser.ExtensionFilter("所有文件", "*.*"));
        List<File> picked = c.showOpenMultipleDialog(fileList.getScene() == null
                ? null : fileList.getScene().getWindow());
        if (picked == null) return;
        for (File f : picked) addPath(f.toPath());
        refresh();
    }

    private void addFolder() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("添加文件夹");
        File d = c.showDialog(fileList.getScene() == null ? null : fileList.getScene().getWindow());
        if (d == null) return;
        try (Stream<Path> s = Files.walk(d.toPath())) {
            for (Path p : s.filter(Files::isRegularFile).toList()) {
                String ext = extOf(p);
                if (VIDEO_EXTS.contains(ext) || IMAGE_EXTS.contains(ext)) addPath(p);
            }
        } catch (IOException e) {
            toast("扫描失败：" + e.getMessage());
        }
        refresh();
    }

    private void addPath(Path p) {
        if (items.stream().anyMatch(i -> i.path.equals(p))) return;
        items.add(new Item(p));
    }

    private void refresh() {
        fileList.setItems(FXCollections.observableArrayList(items));
        updateCount();
    }

    private void setAll(boolean v) {
        for (Item it : items) it.checked.set(v);
        fileList.refresh();
        updateCount();
    }

    private void updateCount() {
        long checked = items.stream().filter(i -> i.checked.get()).count();
        countLabel.setText("共 " + items.size() + " 个文件（勾选 " + checked + "）");
    }

    // ==================== 压缩 ====================

    private void startCompress() {
        List<Item> picked = items.stream().filter(i -> i.checked.get()).toList();
        if (picked.isEmpty()) { toast("请先勾选文件"); return; }
        boolean video = picked.stream().anyMatch(Item::isVideo);
        if (video && !Ffmpeg.available()) {
            toast("未找到 FFmpeg，无法压缩视频（可在全局设置里指定路径）");
            return;
        }
        stopFlag.set(false);
        startBtn.setDisable(true);
        stopBtn.setDisable(false);

        int crf = (int) Math.round(52 - qualitySlider.getValue() * 0.42);   // 质量高→CRF 小
        int threads = Math.max(1, (int) Math.round(
                Runtime.getRuntime().availableProcessors() * cpuSlider.getValue() / 100.0));
        String res = resolutionBox.getValue();
        boolean hevc = codecBox.getSelectionModel().getSelectedIndex() == 1;
        boolean strip = stripMeta.isSelected();
        Path outDir = Settings.load().effectiveExportDir().resolve("压缩");

        javafx.concurrent.Task<int[]> job = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (Item it : picked) {
                    if (stopFlag.get()) break;
                    it.status = "压缩中…";
                    Platform.runLater(fileList::refresh);
                    try {
                        if (it.isVideo()) {
                            Files.createDirectories(outDir);
                            Path target = outDir.resolve(stemOf(it.path) + ".mp4");
                            List<String> args = new ArrayList<>(List.of(
                                    Ffmpeg.ffmpegExe(), "-y", "-i", it.path.toString(),
                                    "-c:v", hevc ? "libx265" : "libx264",
                                    "-preset", "medium", "-crf", String.valueOf(crf),
                                    "-c:a", "aac", "-b:a", "128k",
                                    "-threads", String.valueOf(threads)));
                            if (res != null && !"原始分辨率".equals(res)) {
                                args.addAll(List.of("-vf", "scale=-2:" + res.replace("p", "")));
                            }
                            if (strip) args.addAll(List.of("-map_metadata", "-1"));
                            args.add(target.toString());
                            var r = Ffmpeg.run(args, 3600);
                            if (r.ok() && AudioEngine.sizeOf(target) > 0) {
                                it.status = "✅ " + AudioEngine.formatSize(AudioEngine.sizeOf(target));
                                ok++;
                            } else {
                                it.status = "❌ " + (r.timedOut() ? "超时" : "ffmpeg 失败");
                                fail++;
                            }
                        } else {
                            Files.createDirectories(outDir);
                            Path target = outDir.resolve(stemOf(it.path) + ".jpg");
                            var img = ImageTools.read(it.path);
                            int w = img.getWidth(), h = img.getHeight();
                            if (res != null && !"原始分辨率".equals(res)) {
                                int targetH = Integer.parseInt(res.replace("p", ""));
                                if (h > targetH) {
                                    double scale = (double) targetH / h;
                                    img = ImageTools.scale(img, (int) Math.round(w * scale), targetH);
                                }
                            }
                            ImageTools.write(img, target);
                            it.status = "✅ " + AudioEngine.formatSize(AudioEngine.sizeOf(target));
                            ok++;
                        }
                    } catch (Exception e) {
                        it.status = "❌ " + e.getMessage();
                        fail++;
                    }
                    Platform.runLater(fileList::refresh);
                }
                return new int[] {ok, fail};
            }
        };
        job.setOnSucceeded(e -> {
            int[] r = job.getValue();
            startBtn.setDisable(false);
            stopBtn.setDisable(true);
            toast("压缩完成：成功 " + r[0] + "，失败 " + r[1]);
        });
        job.setOnFailed(e -> {
            startBtn.setDisable(false);
            stopBtn.setDisable(true);
            toast("压缩异常：" + job.getException().getMessage());
        });
        new Thread(job, "media-compress").start();
    }

    private static String stemOf(Path p) {
        String n = p.getFileName().toString();
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(0, dot) : n;
    }

    private static String extOf(Path p) {
        String n = p.getFileName().toString().toLowerCase(Locale.ROOT);
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot) : "";
    }
}
