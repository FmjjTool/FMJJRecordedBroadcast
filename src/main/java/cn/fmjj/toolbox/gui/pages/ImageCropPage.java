package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.TextField;
import javafx.scene.image.Image;
import javafx.scene.image.ImageView;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.stage.FileChooser;

import java.awt.image.BufferedImage;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.function.Consumer;

/**
 * 🖼 图片裁切（对齐 Python 版 image_crop.py 的交互式画布）。
 *
 * <p>工具栏顺序与原版一致：`📁 导入` / `↩` `↪` / `➖ 3x ➕` / `🎯 自动对齐` / `✏️ 精确输入` /
 * `□ 精细模式` `□ 边界吸附` / 伸展 / `— × — px` / `💾 导出` / `📦 批量导出` / `🗑️`；
 * 主体：左侧图片列表（170-260）+ 右侧 Canvas；底部：操作提示 + 文件名。
 *
 * <p>裁切框用**原图坐标**存储（left/top/right/bottom），拖动四条参考线或整体移动；
 * 精细模式下鼠标移动 20px 参考线只动 1px（Shift 加速）。
 */
public class ImageCropPage extends BasePage {

    /** 一张图 + 各自的裁切框（原图坐标）。 */
    private static final class Entry {
        final Path path;
        BufferedImage image;
        int left, top, right, bottom;
        Entry(Path path) { this.path = path; }
        int width() { return right - left; }
        int height() { return bottom - top; }
        Entry copy() {
            Entry e = new Entry(path);
            e.image = image;
            e.left = left; e.top = top; e.right = right; e.bottom = bottom;
            return e;
        }
    }

    private final ListView<String> fileList = new ListView<>();
    private final List<Entry> entries = new ArrayList<>();
    private final Canvas canvas = new Canvas(800, 520);
    private final Label zoomLabel = new Label("3x");
    private final CheckBox fineMode = new CheckBox("精细模式");
    private final CheckBox snapMode = new CheckBox("边界吸附");
    private final Button sizeBtn = new Button("— × — px");
    private final Label statusLabel = new Label("拖动参考线调整裁切 · Ctrl+Z 撤销 · Ctrl+Y 重做 · Shift 加速拖动");
    private final Label fileLabel = new Label("");

    private int magnifierZoom = 3;
    private int dragMode = 0;   // 0=none 1=left 2=right 3=top 4=bottom 5=move
    private double dragStartX, dragStartY;
    private int startLeft, startTop, startRight, startBottom;
    private double imgOffsetX, imgOffsetY, imgScale = 1;
    private final Deque<int[]> undoStack = new ArrayDeque<>();
    private final Deque<int[]> redoStack = new ArrayDeque<>();

    private Consumer<String> toast;

    @Override
    public String title() { return "🖼 图片裁切"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("🖼 图片裁切");
        pageTitle.setStyle("-fx-font-size: 15px; -fx-font-weight: bold;");
        Button help = new Button("❓ 帮助");
        help.setOnAction(e -> showHelp());
        HBox head = new HBox(8, pageTitle, spacer(), help);
        head.setAlignment(Pos.CENTER_LEFT);

        Button importBtn = new Button("📁 导入");
        importBtn.setOnAction(e -> importFiles());
        Button undoBtn = smallBtn("↩", "撤销 (Ctrl+Z)", this::undo);
        Button redoBtn = smallBtn("↪", "重做 (Ctrl+Y)", this::redo);
        Button zoomOut = smallBtn("➖", "缩小放大镜", () -> setZoom(magnifierZoom - 1));
        Button zoomIn = smallBtn("➕", "放大放大镜", () -> setZoom(magnifierZoom + 1));
        zoomLabel.setMinWidth(36);
        zoomLabel.setAlignment(Pos.CENTER);
        zoomLabel.setStyle("-fx-font-family: Consolas; -fx-font-size: 10px;");
        Button autoBtn = new Button("🎯 自动对齐");
        autoBtn.setTooltip(new javafx.scene.control.Tooltip("自动识别内容边界，一键裁掉黑边"));
        autoBtn.setOnAction(e -> autoAlign());
        Button preciseBtn = new Button("✏️ 精确输入");
        preciseBtn.setTooltip(new javafx.scene.control.Tooltip("输入上下左右像素坐标定位参考线"));
        preciseBtn.setOnAction(e -> openPreciseInput());
        fineMode.setSelected(true);
        fineMode.setTooltip(new javafx.scene.control.Tooltip("精细模式：鼠标移动 20px → 参考线移动 1px"));
        snapMode.setSelected(true);
        snapMode.setTooltip(new javafx.scene.control.Tooltip("边界吸附：参考线靠近颜色边缘时自动对齐"));

        sizeBtn.setTooltip(new javafx.scene.control.Tooltip("点击输入精确裁切尺寸"));
        sizeBtn.setOnAction(e -> openPreciseInput());
        Button exportBtn = new Button("💾 导出");
        exportBtn.setOnAction(e -> exportCurrent());
        Button batchBtn = new Button("📦 批量导出");
        batchBtn.setTooltip(new javafx.scene.control.Tooltip("全部图片按各自裁切区域导出"));
        batchBtn.setOnAction(e -> batchExport());
        Button clearBtn = smallBtn("🗑️", "清空全部图片", this::clearAll);

        HBox bar = new HBox(4, importBtn, undoBtn, redoBtn, new Label(" "),
                zoomOut, zoomLabel, zoomIn, new Label(" "),
                autoBtn, preciseBtn, fineMode, snapMode, spacer(),
                sizeBtn, exportBtn, batchBtn, clearBtn);
        bar.setAlignment(Pos.CENTER_LEFT);

        fileList.setMinWidth(170);
        fileList.setMaxWidth(260);
        fileList.setPrefWidth(200);
        fileList.setPlaceholder(new Label("导入图片开始裁切"));
        fileList.getSelectionModel().selectedIndexProperty().addListener((o, a, b) -> onSelect(b.intValue()));

        canvas.widthProperty().addListener((o, a, b) -> redraw());
        canvas.heightProperty().addListener((o, a, b) -> redraw());
        canvas.setOnMousePressed(this::onMousePressed);
        canvas.setOnMouseDragged(this::onMouseDragged);
        canvas.setOnMouseReleased(e -> {
            if (dragMode != 0) pushUndo();
            dragMode = 0;
            redraw();
        });

        // Canvas 不会自动跟随容器：用 Pane 包一层并双向绑定尺寸
        javafx.scene.layout.Pane canvasHolder = new javafx.scene.layout.Pane(canvas);
        canvas.widthProperty().bind(canvasHolder.widthProperty());
        canvas.heightProperty().bind(canvasHolder.heightProperty());
        HBox body = new HBox(6, fileList, canvasHolder);
        HBox.setHgrow(canvasHolder, Priority.ALWAYS);
        VBox.setVgrow(body, Priority.ALWAYS);

        statusLabel.setStyle("-fx-text-fill: #666;");
        HBox statusBar = new HBox(6, statusLabel, spacer(), fileLabel);
        statusBar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(statusLabel, Priority.ALWAYS);

        VBox root = new VBox(6, head, bar, body, statusBar);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(8));
        VBox.setVgrow(body, Priority.ALWAYS);

        // Ctrl+Z / Ctrl+Y
        root.setOnKeyPressed(e -> {
            if (e.isControlDown() && e.getCode() == javafx.scene.input.KeyCode.Z) undo();
            if (e.isControlDown() && e.getCode() == javafx.scene.input.KeyCode.Y) redo();
        });
        root.setFocusTraversable(true);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private Button smallBtn(String text, String tip, Runnable action) {
        Button b = new Button(text);
        b.setMinSize(text.length() > 1 ? 32 : 26, 26);
        b.setMaxSize(text.length() > 1 ? 32 : 26, 26);
        b.setTooltip(new javafx.scene.control.Tooltip(tip));
        b.setOnAction(e -> action.run());
        return b;
    }

    private void showHelp() {
        javafx.scene.control.Alert a = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.INFORMATION);
        a.setTitle("图片裁切 · 功能介绍");
        a.setHeaderText("🖼 四边参考线裁图");
        a.setContentText("""
                · 导入：选择图片（PNG/JPG/BMP/GIF），可多选
                · 裁切：拖动四条参考线围出保留区域（靠近边时拖动该边，内部拖动整体）
                · 精细模式：鼠标移动 20px 参考线才动 1px（按住 Shift 临时加速）
                · 自动对齐：识别内容边界，一键裁掉黑边/留白
                · 精确输入：直接输入上/下/左/右像素坐标
                · 导出：单张按当前区域导出；📦 批量导出全部图片（文件名加 _cropped）
                · Ctrl+Z 撤销 / Ctrl+Y 重做
                """);
        a.showAndWait();
    }

    // ==================== 导入 / 选择 ====================

    private void importFiles() {
        FileChooser c = new FileChooser();
        c.setTitle("选择图片");
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("图片",
                "*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif", "*.webp"));
        List<File> picked = c.showOpenMultipleDialog(canvas.getScene() == null
                ? null : canvas.getScene().getWindow());
        if (picked == null) return;
        for (File f : picked) {
            try {
                BufferedImage img = ImageTools.read(f.toPath());
                Entry e = new Entry(f.toPath());
                e.image = img;
                e.left = 0;
                e.top = 0;
                e.right = img.getWidth();
                e.bottom = img.getHeight();
                entries.add(e);
            } catch (Exception ex) {
                statusLabel.setText("读取失败：" + f.getName() + "（" + ex.getMessage() + "）");
            }
        }
        refreshList();
        if (!entries.isEmpty() && fileList.getSelectionModel().isEmpty()) {
            fileList.getSelectionModel().select(0);
        }
    }

    private void refreshList() {
        List<String> names = new ArrayList<>();
        for (Entry e : entries) {
            names.add(e.path.getFileName() + "  (" + (e.image == null ? "?" : e.image.getWidth())
                    + "×" + (e.image == null ? "?" : e.image.getHeight()) + ")");
        }
        fileList.getItems().setAll(names);
    }

    private Entry current() {
        int idx = fileList.getSelectionModel().getSelectedIndex();
        return idx >= 0 && idx < entries.size() ? entries.get(idx) : null;
    }

    private void onSelect(int idx) {
        undoStack.clear();
        redoStack.clear();
        Entry e = current();
        if (e != null) {
            fileLabel.setText(e.path.toString());
            updateSizeBtn(e);
        } else {
            fileLabel.setText("");
            sizeBtn.setText("— × — px");
        }
        redraw();
    }

    private void updateSizeBtn(Entry e) {
        sizeBtn.setText(e.width() + " × " + e.height() + " px");
    }

    // ==================== 绘制 ====================

    private void redraw() {
        GraphicsContext g = canvas.getGraphicsContext2D();
        double cw = canvas.getWidth(), ch = canvas.getHeight();
        g.setFill(Color.web("#f2f4f7"));
        g.fillRect(0, 0, cw, ch);

        Entry e = current();
        if (e == null || e.image == null) {
            g.setFill(Color.web("#999"));
            g.fillText("导入图片开始裁切", cw / 2 - 60, ch / 2);
            return;
        }
        double iw = e.image.getWidth(), ih = e.image.getHeight();
        imgScale = Math.min((cw - 24) / iw, (ch - 24) / ih);
        imgOffsetX = (cw - iw * imgScale) / 2;
        imgOffsetY = (ch - ih * imgScale) / 2;

        g.drawImage(new Image(e.path.toUri().toString()),
                imgOffsetX, imgOffsetY, iw * imgScale, ih * imgScale);

        // 裁切框外暗化
        double x1 = imgOffsetX + e.left * imgScale;
        double y1 = imgOffsetY + e.top * imgScale;
        double x2 = imgOffsetX + e.right * imgScale;
        double y2 = imgOffsetY + e.bottom * imgScale;
        g.setFill(Color.rgb(0, 0, 0, 0.45));
        g.fillRect(imgOffsetX, imgOffsetY, iw * imgScale, y1 - imgOffsetY);
        g.fillRect(imgOffsetX, y2, iw * imgScale, imgOffsetY + ih * imgScale - y2);
        g.fillRect(imgOffsetX, y1, x1 - imgOffsetX, y2 - y1);
        g.fillRect(x2, y1, imgOffsetX + iw * imgScale - x2, y2 - y1);

        // 框线 + 手柄
        g.setStroke(Color.web("#2e7d32"));
        g.setLineWidth(2);
        g.strokeRect(x1, y1, x2 - x1, y2 - y1);
        g.setFill(Color.web("#2e7d32"));
        for (double[] p : new double[][]{{x1, y1}, {x2, y1}, {x1, y2}, {x2, y2}}) {
            g.fillRect(p[0] - 4, p[1] - 4, 8, 8);
        }
        updateSizeBtn(e);
    }

    // ==================== 拖拽 ====================

    private void onMousePressed(javafx.scene.input.MouseEvent ev) {
        Entry e = current();
        if (e == null) return;
        double mx = ev.getX(), my = ev.getY();
        double x1 = imgOffsetX + e.left * imgScale;
        double y1 = imgOffsetY + e.top * imgScale;
        double x2 = imgOffsetX + e.right * imgScale;
        double y2 = imgOffsetY + e.bottom * imgScale;
        double tol = 8;
        dragStartX = mx;
        dragStartY = my;
        startLeft = e.left;
        startTop = e.top;
        startRight = e.right;
        startBottom = e.bottom;

        if (Math.abs(mx - x1) < tol) dragMode = 1;
        else if (Math.abs(mx - x2) < tol) dragMode = 2;
        else if (Math.abs(my - y1) < tol) dragMode = 3;
        else if (Math.abs(my - y2) < tol) dragMode = 4;
        else if (mx > x1 && mx < x2 && my > y1 && my < y2) dragMode = 5;
        else dragMode = 0;
    }

    private void onMouseDragged(javafx.scene.input.MouseEvent ev) {
        Entry e = current();
        if (e == null || dragMode == 0) return;
        double factor = fineMode.isSelected() && !ev.isShiftDown() ? 20.0 : 1.0;
        int dx = (int) Math.round((ev.getX() - dragStartX) / imgScale / factor);
        int dy = (int) Math.round((ev.getY() - dragStartY) / imgScale / factor);
        int w = e.image.getWidth(), h = e.image.getHeight();

        switch (dragMode) {
            case 1 -> e.left = clamp(startLeft + dx, 0, e.right - 1);
            case 2 -> e.right = clamp(startRight + dx, e.left + 1, w);
            case 3 -> e.top = clamp(startTop + dy, 0, e.bottom - 1);
            case 4 -> e.bottom = clamp(startBottom + dy, e.top + 1, h);
            case 5 -> {
                int nw = e.right - e.left, nh = e.bottom - e.top;
                int nl = clamp(startLeft + dx, 0, w - nw);
                int nt = clamp(startTop + dy, 0, h - nh);
                e.left = nl;
                e.right = nl + nw;
                e.top = nt;
                e.bottom = nt + nh;
            }
            default -> { }
        }
        redraw();
    }

    private static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(max, v));
    }

    // ==================== 撤销 / 重做 ====================

    private void pushUndo() {
        Entry e = current();
        if (e == null) return;
        undoStack.push(new int[]{startLeft, startTop, startRight, startBottom});
        redoStack.clear();
    }

    private void undo() {
        Entry e = current();
        if (e == null || undoStack.isEmpty()) return;
        redoStack.push(new int[]{e.left, e.top, e.right, e.bottom});
        int[] s = undoStack.pop();
        e.left = s[0]; e.top = s[1]; e.right = s[2]; e.bottom = s[3];
        redraw();
    }

    private void redo() {
        Entry e = current();
        if (e == null || redoStack.isEmpty()) return;
        undoStack.push(new int[]{e.left, e.top, e.right, e.bottom});
        int[] s = redoStack.pop();
        e.left = s[0]; e.top = s[1]; e.right = s[2]; e.bottom = s[3];
        redraw();
    }

    private void setZoom(int z) {
        magnifierZoom = clamp(z, 1, 12);
        zoomLabel.setText(magnifierZoom + "x");
    }

    // ==================== 自动对齐 / 精确输入 ====================

    /** 自动对齐：从四边向内找第一个「非纯黑且非纯白」的行/列。 */
    private void autoAlign() {
        Entry e = current();
        if (e == null || e.image == null) return;
        pushUndo();
        BufferedImage img = e.image;
        int w = img.getWidth(), h = img.getHeight();
        int top = 0, bottom = h - 1, left = 0, right = w - 1;
        while (top < h - 1 && isBlankRow(img, top)) top++;
        while (bottom > top && isBlankRow(img, bottom)) bottom--;
        while (left < w - 1 && isBlankCol(img, left, top, bottom)) left++;
        while (right > left && isBlankCol(img, right, top, bottom)) right--;
        e.top = top;
        e.bottom = bottom + 1;
        e.left = left;
        e.right = right + 1;
        redraw();
        statusLabel.setText("自动对齐完成：" + e.width() + " × " + e.height());
    }

    private static boolean isBlankRow(BufferedImage img, int y) {
        int w = img.getWidth();
        for (int x = 0; x < w; x += Math.max(1, w / 200)) {
            int rgb = img.getRGB(x, y) & 0xFFFFFF;
            if (rgb != 0x000000 && rgb != 0xFFFFFF) return false;
        }
        return true;
    }

    private static boolean isBlankCol(BufferedImage img, int x, int top, int bottom) {
        for (int y = top; y <= bottom; y += Math.max(1, (bottom - top + 1) / 200)) {
            int rgb = img.getRGB(x, y) & 0xFFFFFF;
            if (rgb != 0x000000 && rgb != 0xFFFFFF) return false;
        }
        return true;
    }

    private void openPreciseInput() {
        Entry e = current();
        if (e == null) { statusLabel.setText("请先选择图片"); return; }
        TextField l = new TextField(String.valueOf(e.left));
        TextField t = new TextField(String.valueOf(e.top));
        TextField r = new TextField(String.valueOf(e.right));
        TextField b = new TextField(String.valueOf(e.bottom));
        GridPane grid = new GridPane();
        grid.setHgap(6);
        grid.setVgap(6);
        grid.add(new Label("上："), 0, 0);
        grid.add(t, 1, 0);
        grid.add(new Label("下："), 2, 0);
        grid.add(b, 3, 0);
        grid.add(new Label("左："), 0, 1);
        grid.add(l, 1, 1);
        grid.add(new Label("右："), 2, 1);
        grid.add(r, 3, 1);
        Label info = new Label("原图 " + e.image.getWidth() + " × " + e.image.getHeight());
        info.setStyle("-fx-text-fill: #888;");
        grid.add(info, 0, 2, 4, 1);

        javafx.scene.control.Dialog<Void> dlg = new javafx.scene.control.Dialog<>();
        dlg.setTitle("精确输入");
        dlg.getDialogPane().setContent(grid);
        dlg.getDialogPane().getButtonTypes().addAll(
                javafx.scene.control.ButtonType.OK, javafx.scene.control.ButtonType.CANCEL);
        dlg.setResultConverter(bt -> {
            if (bt == javafx.scene.control.ButtonType.OK) {
                pushUndo();
                int w = e.image.getWidth(), h = e.image.getHeight();
                e.left = clamp(parseInt(l.getText(), e.left), 0, w - 1);
                e.top = clamp(parseInt(t.getText(), e.top), 0, h - 1);
                e.right = clamp(parseInt(r.getText(), e.right), e.left + 1, w);
                e.bottom = clamp(parseInt(b.getText(), e.bottom), e.top + 1, h);
                redraw();
            }
            return null;
        });
        dlg.showAndWait();
    }

    private static int parseInt(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (Exception e) {
            return def;
        }
    }

    // ==================== 导出 ====================

    private void exportCurrent() {
        Entry e = current();
        if (e == null) { statusLabel.setText("请先选择图片"); return; }
        Path outDir = Settings.load().effectiveExportDir().resolve("图片裁切");
        try {
            Files.createDirectories(outDir);
            String name = e.path.getFileName().toString();
            int dot = name.lastIndexOf('.');
            String stem = dot > 0 ? name.substring(0, dot) : name;
            String ext = dot > 0 ? name.substring(dot) : ".png";
            Path target = outDir.resolve(stem + "_cropped" + ext);
            BufferedImage sub = e.image.getSubimage(e.left, e.top, e.width(), e.height());
            ImageTools.write(sub, target);
            statusLabel.setText("已导出：" + target);
            toast("已导出：" + target.getFileName());
        } catch (Exception ex) {
            statusLabel.setText("导出失败：" + ex.getMessage());
        }
    }

    private void batchExport() {
        if (entries.isEmpty()) { statusLabel.setText("请先导入图片"); return; }
        Path outDir = Settings.load().effectiveExportDir().resolve("图片裁切");
        int ok = 0, fail = 0;
        for (Entry e : entries) {
            try {
                Files.createDirectories(outDir);
                String name = e.path.getFileName().toString();
                int dot = name.lastIndexOf('.');
                String stem = dot > 0 ? name.substring(0, dot) : name;
                String ext = dot > 0 ? name.substring(dot) : ".png";
                Path target = outDir.resolve(stem + "_cropped" + ext);
                int l = clamp(e.left, 0, e.image.getWidth() - 1);
                int t = clamp(e.top, 0, e.image.getHeight() - 1);
                int r = clamp(e.right, l + 1, e.image.getWidth());
                int b = clamp(e.bottom, t + 1, e.image.getHeight());
                ImageTools.write(e.image.getSubimage(l, t, r - l, b - t), target);
                ok++;
            } catch (Exception ex) {
                fail++;
            }
        }
        statusLabel.setText("批量导出完成：成功 " + ok + "，失败 " + fail + "（" + outDir + "）");
        toast("批量导出：" + ok + " 张");
    }

    private void clearAll() {
        entries.clear();
        fileList.getItems().clear();
        undoStack.clear();
        redoStack.clear();
        redraw();
        statusLabel.setText("已清空");
    }
}
