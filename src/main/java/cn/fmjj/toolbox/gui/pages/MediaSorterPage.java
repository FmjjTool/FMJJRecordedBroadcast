package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.application.Platform;
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
import javafx.scene.control.SplitPane;
import javafx.scene.image.Image;
import javafx.scene.image.ImageView;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🧹 媒体筛选（对齐 Python 版 image_sorter.py 的骨架与「保留/收藏/删除」工作流）。
 *
 * <p>原版 6106 行（4 个文件）：三栏（文件列表 / 预览+文件夹面板 / 文件信息）+ 键盘工作流。
 * 这里实现核心：源文件夹导入 → 三栏浏览 → `S` 保留 / `F` 收藏 / `W` 删除 / `Ctrl+Z` 撤销，
 * 文件按「复制/移动」方式归入 `分类目录/保留|收藏`。
 */
public class MediaSorterPage extends BasePage {

    private static final Set<String> IMAGE_EXTS = Set.of(".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp");
    private static final Set<String> VIDEO_EXTS = Set.of(".mp4", ".flv", ".mkv", ".mov", ".avi", ".webm");

    /** 一条媒体。 */
    private static final class Item {
        final Path path;
        String action = "";   // "", keep, fav, delete
        Item(Path path) { this.path = path; }
    }

    /** 撤销记录。 */
    private record Undo(Item item, String prevAction, Path movedTo) {}

    private final ListView<String> fileList = new ListView<>();
    private final ImageView preview = new ImageView();
    private final Label infoLabel = new Label("（未选择文件）");
    private final Label sourcesLabel = new Label("尚未添加源文件夹");
    private final Label workDirLabel = new Label("");
    private final Label pipelineLabel = new Label("");
    private final Label keepCount = new Label("0");
    private final Label favCount = new Label("0");
    private final Label delCount = new Label("0");
    private final ComboBox<String> viewBox = new ComboBox<>();
    private final ComboBox<String> opModeBox = new ComboBox<>();
    private final CheckBox filterScreened = new CheckBox("过滤已筛选文件(MD5)");

    private final List<Item> items = new ArrayList<>();
    private final List<Path> sources = new ArrayList<>();
    private final Deque<Undo> undoStack = new ArrayDeque<>();
    private Path workDir = Settings.load().effectiveExportDir().resolve("媒体筛选");

    private Consumer<String> toast;

    @Override
    public String title() { return "🧹 媒体筛选"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        // 顶栏1：源文件夹
        Button addBtn = new Button("📂 添加源文件夹");
        addBtn.setOnAction(e -> pickSource());
        Button clearBtn = new Button("🧹 清空源");
        clearBtn.setOnAction(e -> {
            sources.clear();
            items.clear();
            fileList.getItems().clear();
            sourcesLabel.setText("尚未添加源文件夹");
            updateCounts();
        });
        viewBox.getItems().addAll("📅 日期分组", "📋 默认列表", "🎲 随机抽样");
        viewBox.getSelectionModel().select(1);
        viewBox.setOnAction(e -> applyView());
        HBox top = new HBox(6, addBtn, clearBtn, new Label("视图:"), viewBox, spacer(), sourcesLabel);
        top.setAlignment(Pos.CENTER_LEFT);
        sourcesLabel.setStyle("-fx-text-fill: #888;");

        // 顶栏2：分类工作流
        Button workDirBtn = new Button("📁 分类目录");
        workDirBtn.setOnAction(e -> pickWorkDir());
        workDirLabel.setStyle("-fx-text-fill: #6b7280;");
        workDirLabel.setText(workDir.toString());
        opModeBox.getItems().addAll("复制模式", "移动模式");
        opModeBox.getSelectionModel().selectFirst();
        Button keepBtn = new Button("⬇️ 保留(S)");
        keepBtn.setOnAction(e -> classify("keep"));
        Button favBtn = new Button("⭐ 收藏(F)");
        favBtn.setOnAction(e -> classify("fav"));
        Button delBtn = new Button("🗑️ 删除(W)");
        delBtn.setOnAction(e -> classify("delete"));
        Button undoBtn = new Button("↩️ 撤销(Ctrl+Z)");
        undoBtn.setOnAction(e -> undo());
        Button settingsBtn = new Button("⚙️ 设置");
        settingsBtn.setOnAction(e -> toast("设置面板随下一轮补齐（当前：复制/移动、过滤已筛选）"));
        HBox classifyBar = new HBox(6, workDirBtn, workDirLabel, spacer(),
                new Label("分类方式:"), opModeBox, keepBtn, favBtn, delBtn, undoBtn, settingsBtn);
        classifyBar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(workDirLabel, Priority.ALWAYS);

        pipelineLabel.setStyle("-fx-text-fill: #6b7280;");

        // 左：文件列表
        fileList.setPrefWidth(330);
        fileList.setPlaceholder(new Label("添加源文件夹后显示文件"));
        fileList.getSelectionModel().selectedIndexProperty().addListener((o, a, b) -> showPreview(b.intValue()));

        // 中：预览
        preview.setPreserveRatio(true);
        preview.setFitWidth(420);
        preview.setFitHeight(360);
        ScrollPane previewScroll = new ScrollPane(preview);
        previewScroll.setFitToWidth(true);
        previewScroll.setStyle("-fx-background-color: #111;");

        // 右：信息 + 计数
        infoLabel.setWrapText(true);
        Label kL = new Label("保留");
        keepCount.setStyle("-fx-text-fill: #2e7d32; -fx-font-size: 16px; -fx-font-weight: bold;");
        Label fL = new Label("收藏");
        favCount.setStyle("-fx-text-fill: #f9a825; -fx-font-size: 16px; -fx-font-weight: bold;");
        Label dL = new Label("删除");
        delCount.setStyle("-fx-text-fill: #c62828; -fx-font-size: 16px; -fx-font-weight: bold;");
        VBox counts = new VBox(4,
                new HBox(6, kL, keepCount), new HBox(6, fL, favCount), new HBox(6, dL, delCount));
        VBox right = new VBox(8, infoLabel, counts, new Label(""),
                filterScreened, new Label("快捷键：S 保留 / F 收藏 / W 删除 / Ctrl+Z 撤销"));
        right.setPadding(new Insets(8));
        right.setPrefWidth(300);
        ((Label) right.getChildren().get(4)).setStyle("-fx-text-fill: #888; -fx-font-size: 11px;");

        SplitPane split = new SplitPane(fileList, previewScroll, right);
        split.setDividerPositions(0.28, 0.72);
        VBox.setVgrow(split, Priority.ALWAYS);

        VBox root = new VBox(6, top, classifyBar, pipelineLabel, split);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(6));
        VBox.setVgrow(split, Priority.ALWAYS);

        // 键盘工作流
        root.setOnKeyPressed(e -> {
            if (e.isControlDown() && e.getCode() == javafx.scene.input.KeyCode.Z) { undo(); return; }
            switch (e.getCode()) {
                case S -> classify("keep");
                case F -> classify("fav");
                case W -> classify("delete");
                default -> { }
            }
        });
        root.setFocusTraversable(true);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    // ==================== 源 / 分类目录 ====================

    private void pickSource() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择源文件夹");
        File d = c.showDialog(fileList.getScene() == null ? null : fileList.getScene().getWindow());
        if (d == null) return;
        sources.add(d.toPath());
        sourcesLabel.setText(sources.size() + " 个源：" + d.getName());
        loadSources();
    }

    private void loadSources() {
        pipelineLabel.setText("扫描中…");
        javafx.concurrent.Task<List<Path>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<Path> call() throws IOException {
                List<Path> out = new ArrayList<>();
                for (Path src : sources) {
                    try (Stream<Path> s = Files.walk(src)) {
                        for (Path p : s.filter(Files::isRegularFile).toList()) {
                            String ext = extOf(p);
                            if (IMAGE_EXTS.contains(ext) || VIDEO_EXTS.contains(ext)) out.add(p);
                        }
                    }
                }
                out.sort(java.util.Comparator.comparing(Path::toString));
                return out;
            }
        };
        task.setOnSucceeded(e -> {
            items.clear();
            for (Path p : task.getValue()) items.add(new Item(p));
            applyView();
            pipelineLabel.setText("共 " + items.size() + " 个媒体文件");
        });
        task.setOnFailed(e -> pipelineLabel.setText("扫描失败：" + task.getException().getMessage()));
        new Thread(task, "sorter-scan").start();
    }

    private void pickWorkDir() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择分类目录");
        File d = c.showDialog(fileList.getScene() == null ? null : fileList.getScene().getWindow());
        if (d != null) {
            workDir = d.toPath();
            workDirLabel.setText(workDir.toString());
        }
    }

    private void applyView() {
        int mode = viewBox.getSelectionModel().getSelectedIndex();
        List<Item> shown = new ArrayList<>(items);
        if (mode == 2) {   // 随机抽样
            java.util.Collections.shuffle(shown);
            shown = shown.subList(0, Math.min(50, shown.size()));
        } else if (mode == 0) {   // 日期分组（按修改时间排序）
            shown.sort(java.util.Comparator.comparingLong(p -> {
                try {
                    return Files.getLastModifiedTime(p.path).toMillis();
                } catch (IOException e) {
                    return 0L;
                }
            }));
        }
        List<String> names = new ArrayList<>();
        for (Item it : shown) {
            String mark = switch (it.action) {
                case "keep" -> "✅ ";
                case "fav" -> "⭐ ";
                case "delete" -> "🗑 ";
                default -> "";
            };
            names.add(mark + it.path.getFileName());
        }
        fileList.setItems(FXCollections.observableArrayList(names));
        updateCounts();
    }

    // ==================== 预览 / 信息 ====================

    private void showPreview(int idx) {
        if (idx < 0 || idx >= items.size()) {
            infoLabel.setText("（未选择文件）");
            return;
        }
        Item it = items.get(idx);
        String ext = extOf(it.path);
        StringBuilder sb = new StringBuilder();
        sb.append(it.path.getFileName()).append("\n");
        try {
            sb.append("大小：").append(AudioEngine.formatSize(Files.size(it.path))).append("\n");
            sb.append("路径：").append(it.path.getParent()).append("\n");
        } catch (IOException ignored) {
        }
        sb.append("类型：").append(IMAGE_EXTS.contains(ext) ? "图片" : "视频").append("\n");
        if (!it.action.isEmpty()) sb.append("状态：").append(it.action).append("\n");
        infoLabel.setText(sb.toString());

        if (IMAGE_EXTS.contains(ext)) {
            try {
                var img = ImageTools.read(it.path);
                var scaled = ImageTools.scale(img, 420, 360);
                var wi = new javafx.scene.image.WritableImage(420, 360);
                var pw = wi.getPixelWriter();
                for (int y = 0; y < 360; y++) {
                    for (int x = 0; x < 420; x++) pw.setArgb(x, y, scaled.getRGB(x, y));
                }
                preview.setImage(wi);
            } catch (Exception e) {
                preview.setImage(null);
            }
        } else {
            preview.setImage(null);
        }
    }

    // ==================== 分类 ====================

    private void classify(String action) {
        int idx = fileList.getSelectionModel().getSelectedIndex();
        if (idx < 0 || idx >= items.size()) {
            pipelineLabel.setText("请先选中文件");
            return;
        }
        Item it = items.get(idx);
        String prev = it.action;
        Path movedTo = null;
        try {
            if ("delete".equals(action)) {
                var r = AudioEngine.deleteFilesSafe(List.of(it.path));
                if (r.ok() == 0) {
                    pipelineLabel.setText("删除失败（可能已在回收站）");
                    return;
                }
            } else {
                Path targetDir = workDir.resolve("keep".equals(action) ? "保留" : "收藏");
                Files.createDirectories(targetDir);
                Path target = uniqueTarget(targetDir, it.path.getFileName().toString());
                boolean move = opModeBox.getSelectionModel().getSelectedIndex() == 1;
                if (move) {
                    Files.move(it.path, target, StandardCopyOption.REPLACE_EXISTING);
                } else {
                    Files.copy(it.path, target, StandardCopyOption.REPLACE_EXISTING);
                }
                movedTo = target;
            }
            it.action = action;
            undoStack.push(new Undo(it, prev, movedTo));
            pipelineLabel.setText(("keep".equals(action) ? "✅ 保留" : "fav".equals(action) ? "⭐ 收藏" : "🗑 删除")
                    + "：" + it.path.getFileName());
            // 自动跳到下一个
            if (idx + 1 < items.size()) fileList.getSelectionModel().select(idx + 1);
            applyView();
        } catch (Exception e) {
            pipelineLabel.setText("操作失败：" + e.getMessage());
        }
    }

    private static Path uniqueTarget(Path dir, String name) {
        Path target = dir.resolve(name);
        int n = 1;
        String stem = name;
        String ext = "";
        int dot = name.lastIndexOf('.');
        if (dot > 0) {
            stem = name.substring(0, dot);
            ext = name.substring(dot);
        }
        while (Files.exists(target)) {
            target = dir.resolve(stem + "_" + (n++) + ext);
        }
        return target;
    }

    private void undo() {
        if (undoStack.isEmpty()) {
            pipelineLabel.setText("没有可撤销的操作");
            return;
        }
        Undo u = undoStack.pop();
        try {
            if (u.movedTo() != null && Files.exists(u.movedTo())) {
                Files.deleteIfExists(u.movedTo());
            }
            u.item().action = u.prevAction();
            pipelineLabel.setText("已撤销：" + u.item().path.getFileName());
            applyView();
        } catch (Exception e) {
            pipelineLabel.setText("撤销失败：" + e.getMessage());
        }
    }

    private void updateCounts() {
        keepCount.setText(String.valueOf(items.stream().filter(i -> "keep".equals(i.action)).count()));
        favCount.setText(String.valueOf(items.stream().filter(i -> "fav".equals(i.action)).count()));
        delCount.setText(String.valueOf(items.stream().filter(i -> "delete".equals(i.action)).count()));
    }

    private static String extOf(Path p) {
        String n = p.getFileName().toString().toLowerCase(Locale.ROOT);
        int dot = n.lastIndexOf('.');
        return dot > 0 ? n.substring(dot) : "";
    }
}
