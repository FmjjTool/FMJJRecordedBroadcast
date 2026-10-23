package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.SplitPane;
import javafx.scene.control.TextField;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;

import java.awt.image.BufferedImage;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🎨 套版封面制作（对齐 Python 版 cover_maker.py 的结构，功能取核心子集）。
 *
 * <p>结构：`🎨 套版封面制作`(11pt 粗体) + `❓` + 伸展 + `🎲 随机底图` + `🖼 选择底图` +
 * `📁 上传图片` + 裁剪填充下拉 → SplitPane（左 380 滚动区：🖼 底图组 / 📝 文字 / 🎨 颜色；
 * 右 760 预览画布）→ 状态栏。
 *
 * <p>原版 1884 行（多文字图层、字体选择器、预设颜色面板、拖拽定位、分割线）。
 * 这里实现：底图（文件夹/随机/上传）+ 标题/副标题（文本/字号/颜色）+ 裁剪填充/完整包含 +
 * 预览 + 导出。
 */
public class CoverMakerPage extends BasePage {

    private static final String[][] PRESET_COLORS = {
            {"#ffffff", "白"}, {"#000000", "黑"}, {"#2e7d32", "绿"}, {"#1976d2", "蓝"},
            {"#c62828", "红"}, {"#f9a825", "黄"}, {"#6a1b9a", "紫"}, {"#ef6c00", "橙"},
    };

    private final TextField folderField = new TextField();
    private final Label folderInfo = new Label("已扫描 0 张图片");
    private final ComboBox<String> fitBox = new ComboBox<>();
    private final TextField titleField = new TextField("封茗囧菌");
    private final TextField subtitleField = new TextField("翻唱合集");
    private final TextField titleSizeField = new TextField("72");
    private final TextField subSizeField = new TextField("36");
    private final TextField titleColorField = new TextField("#ffffff");
    private final TextField subColorField = new TextField("#dcdcdc");
    private final CheckBox showBar = new CheckBox("底部渐变条");
    private final ComboBox<String> resolutionBox = new ComboBox<>();
    private final Canvas canvas = new Canvas(720, 405);
    private final Label status = new Label("");

    private final List<Path> backgrounds = new ArrayList<>();
    private BufferedImage currentBg;

    private Consumer<String> toast;

    @Override
    public String title() { return "🖼 封面制作"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("🎨 套版封面制作");
        pageTitle.setStyle("-fx-font-size: 15px; -fx-font-weight: bold;");
        Button help = new Button("❓");
        help.setOnAction(e -> showHelp());
        Button randomBg = new Button("🎲 随机底图");
        randomBg.setOnAction(e -> randomBackground());
        Button pickBg = new Button("🖼 选择底图");
        pickBg.setTooltip(new javafx.scene.control.Tooltip("从底图文件夹里挑一张"));
        pickBg.setOnAction(e -> pickBackground());
        Button upload = new Button("📁 上传图片");
        upload.setTooltip(new javafx.scene.control.Tooltip("选择本地图片临时替换当前底图"));
        upload.setOnAction(e -> uploadBackground());
        fitBox.getItems().addAll("裁剪填充(默认)", "完整包含(留白)");
        fitBox.getSelectionModel().selectFirst();
        fitBox.setTooltip(new javafx.scene.control.Tooltip("裁剪填充：等比放大铺满并居中裁切；完整包含：整图可见"));
        fitBox.setOnAction(e -> redraw());

        HBox head = new HBox(6, pageTitle, help, spacer(), randomBg, pickBg, upload, fitBox);
        head.setAlignment(Pos.CENTER_LEFT);

        SplitPane split = new SplitPane(buildLeft(), buildRight());
        split.setDividerPositions(0.34);
        VBox.setVgrow(split, Priority.ALWAYS);

        status.setStyle("-fx-text-fill: #666;");
        VBox root = new VBox(8, head, split, status);
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

    private void showHelp() {
        javafx.scene.control.Alert a = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.INFORMATION);
        a.setTitle("套版封面制作 · 功能介绍");
        a.setHeaderText("🎨 底图 + 文字 → 16:9 封面");
        a.setContentText("""
                · 底图：📂 更换底图文件夹（递归扫描图片）/ 🎲 随机底图 / 📁 上传图片（临时替换）
                · 裁剪填充：底图等比放大铺满画布并居中裁切；完整包含：整图可见，留白填充
                · 文字：标题 / 副标题，可调字号与颜色（支持 #RRGGBB）
                · 底部渐变条：文字可读性更好
                · 分辨率：1280x720 / 1920x1080 / 2560x1440 / 3840x2160
                · 💾 导出 PNG 到「导出目录/封面」

                ⚠️ 原版的多文字图层、字体选择器、拖拽定位、分割线等高级功能未移植
                """);
        a.showAndWait();
    }

    // ==================== 左栏 ====================

    private Node buildLeft() {
        folderField.setPromptText("底图文件夹路径");
        folderField.setEditable(false);
        Button changeFolder = new Button("📂 更换底图文件夹");
        changeFolder.setOnAction(e -> chooseFolder());
        Button rescan = new Button("🔄 重新扫描");
        rescan.setOnAction(e -> scanFolder());
        HBox row = new HBox(6, changeFolder, rescan);
        row.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(changeFolder, Priority.ALWAYS);
        folderInfo.setStyle("-fx-font-size: 12px; -fx-text-fill: #6b7280;");
        Label hint = new Label("随机底图从文件夹递归抽取全部图片；「上传图片」仅临时替换当前底图");
        hint.setStyle("-fx-font-size: 11px; -fx-text-fill: #8a93a5;");
        hint.setWrapText(true);

        VBox bgBox = group("🖼 底图", folderField, row, folderInfo, hint);

        GridPane textGrid = new GridPane();
        textGrid.setHgap(6);
        textGrid.setVgap(6);
        titleField.textProperty().addListener((o, a, b) -> redraw());
        subtitleField.textProperty().addListener((o, a, b) -> redraw());
        titleSizeField.textProperty().addListener((o, a, b) -> redraw());
        subSizeField.textProperty().addListener((o, a, b) -> redraw());
        titleColorField.textProperty().addListener((o, a, b) -> redraw());
        subColorField.textProperty().addListener((o, a, b) -> redraw());
        showBar.setSelected(true);
        showBar.setOnAction(e -> redraw());
        titleSizeField.setPrefWidth(70);
        subSizeField.setPrefWidth(70);
        titleColorField.setPrefWidth(90);
        subColorField.setPrefWidth(90);
        textGrid.add(new Label("标题："), 0, 0);
        textGrid.add(titleField, 1, 0, 3, 1);
        textGrid.add(new Label("字号："), 0, 1);
        textGrid.add(titleSizeField, 1, 1);
        textGrid.add(new Label("颜色："), 2, 1);
        textGrid.add(titleColorField, 3, 1);
        textGrid.add(new Label("副标题："), 0, 2);
        textGrid.add(subtitleField, 1, 2, 3, 1);
        textGrid.add(new Label("字号："), 0, 3);
        textGrid.add(subSizeField, 1, 3);
        textGrid.add(new Label("颜色："), 2, 3);
        textGrid.add(subColorField, 3, 3);
        textGrid.add(showBar, 0, 4, 4, 1);
        GridPane.setHgrow(titleField, Priority.ALWAYS);
        GridPane.setHgrow(subtitleField, Priority.ALWAYS);

        VBox textBox = group("📝 文字", textGrid);

        GridPane colorGrid = new GridPane();
        colorGrid.setHgap(4);
        colorGrid.setVgap(4);
        int i = 0;
        for (String[] pc : PRESET_COLORS) {
            Button b = new Button(pc[1]);
            b.setMinSize(52, 26);
            b.setStyle("-fx-background-color: " + pc[0] + "; -fx-text-fill: "
                    + ("#ffffff".equals(pc[0]) || "#f9a825".equals(pc[0]) ? "#333" : "white") + ";");
            String hex = pc[0];
            b.setOnAction(e -> {
                titleColorField.setText(hex);
                subColorField.setText(hex);
            });
            colorGrid.add(b, i % 4, i / 4);
            i++;
        }
        VBox colorBox = group("🎨 颜色预设（双击应用到标题+副标题）", colorGrid);

        resolutionBox.getItems().addAll("1280x720", "1920x1080", "2560x1440", "3840x2160");
        resolutionBox.getSelectionModel().select("1920x1080");
        Button export = new Button("💾 导出 PNG");
        export.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white; -fx-padding: 6 16 6 16;");
        export.setOnAction(e -> exportCover());
        HBox expRow = new HBox(6, new Label("分辨率："), resolutionBox, export);
        expRow.setAlignment(Pos.CENTER_LEFT);

        VBox body = new VBox(10, bgBox, textBox, colorBox, expRow);
        body.setPadding(new Insets(8));
        ScrollPane scroll = new ScrollPane(body);
        scroll.setFitToWidth(true);
        scroll.setMinWidth(320);
        scroll.setMaxWidth(460);
        scroll.setStyle("-fx-background-color: white; -fx-border-color: #d0d6df; -fx-border-radius: 4;");
        return scroll;
    }

    private VBox group(String title, Node... children) {
        Label l = new Label(title);
        l.setStyle("-fx-font-weight: 600;");
        VBox box = new VBox(6, l);
        box.getChildren().addAll(children);
        box.setPadding(new Insets(8));
        box.setStyle("-fx-border-color: #e0e4ea; -fx-border-radius: 4;");
        return box;
    }

    // ==================== 右栏 ====================

    private Node buildRight() {
        javafx.scene.layout.Pane holder = new javafx.scene.layout.Pane(canvas);
        canvas.widthProperty().bind(holder.widthProperty());
        canvas.heightProperty().bind(holder.heightProperty());
        canvas.widthProperty().addListener((o, a, b) -> redraw());
        canvas.heightProperty().addListener((o, a, b) -> redraw());
        Platform.runLater(this::redraw);
        return holder;
    }

    // ==================== 底图 ====================

    private void chooseFolder() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择底图文件夹");
        File d = c.showDialog(canvas.getScene() == null ? null : canvas.getScene().getWindow());
        if (d == null) return;
        folderField.setText(d.getAbsolutePath());
        scanFolder();
    }

    private void scanFolder() {
        String dir = folderField.getText();
        if (dir == null || dir.isBlank()) { status.setText("请先选择底图文件夹"); return; }
        backgrounds.clear();
        status.setText("扫描中…");
        javafx.concurrent.Task<List<Path>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<Path> call() throws Exception {
                try (Stream<Path> s = Files.walk(Path.of(dir))) {
                    return s.filter(Files::isRegularFile).filter(ImageTools::isImage).toList();
                }
            }
        };
        task.setOnSucceeded(e -> {
            backgrounds.addAll(task.getValue());
            folderInfo.setText("已扫描 " + backgrounds.size() + " 张图片");
            status.setText("底图：" + backgrounds.size() + " 张");
            if (!backgrounds.isEmpty() && currentBg == null) randomBackground();
        });
        task.setOnFailed(e -> status.setText("扫描失败：" + task.getException().getMessage()));
        new Thread(task, "cover-scan").start();
    }

    private void randomBackground() {
        if (backgrounds.isEmpty()) { status.setText("请先选择底图文件夹"); return; }
        Path p = backgrounds.get(new Random().nextInt(backgrounds.size()));
        loadBackground(p);
    }

    private void pickBackground() {
        if (backgrounds.isEmpty()) { status.setText("请先选择底图文件夹"); return; }
        FileChooser c = new FileChooser();
        c.setTitle("选择底图");
        c.setInitialDirectory(Path.of(folderField.getText()).toFile());
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("图片",
                "*.jpg", "*.jpeg", "*.png", "*.bmp", "*.gif", "*.webp"));
        File f = c.showOpenDialog(canvas.getScene() == null ? null : canvas.getScene().getWindow());
        if (f != null) loadBackground(f.toPath());
    }

    private void uploadBackground() {
        FileChooser c = new FileChooser();
        c.setTitle("上传图片（临时替换）");
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("图片",
                "*.jpg", "*.jpeg", "*.png", "*.bmp", "*.gif", "*.webp"));
        File f = c.showOpenDialog(canvas.getScene() == null ? null : canvas.getScene().getWindow());
        if (f != null) loadBackground(f.toPath());
    }

    private void loadBackground(Path p) {
        try {
            currentBg = ImageTools.read(p);
            status.setText("当前底图：" + p.getFileName());
            redraw();
        } catch (Exception e) {
            status.setText("读取底图失败：" + e.getMessage());
        }
    }

    // ==================== 预览 / 导出 ====================

    private void redraw() {
        GraphicsContext g = canvas.getGraphicsContext2D();
        double cw = canvas.getWidth(), ch = canvas.getHeight();
        if (cw < 10 || ch < 10) return;
        // 画布内画 16:9 的封面区域
        double w = Math.min(cw, ch * 16.0 / 9.0);
        double h = w * 9.0 / 16.0;
        double ox = (cw - w) / 2, oy = (ch - h) / 2;

        g.setFill(Color.web("#f2f4f7"));
        g.fillRect(0, 0, cw, ch);

        if (currentBg == null) {
            g.setFill(Color.web("#ccc"));
            g.fillRect(ox, oy, w, h);
            g.setFill(Color.web("#666"));
            g.fillText("选择底图后预览（🎲 随机底图 / 📁 上传图片）", ox + 20, oy + h / 2);
            return;
        }
        boolean crop = fitBox.getSelectionModel().getSelectedIndex() == 0;
        BufferedImage rendered = render(currentBg, 1280, 720, crop,
                titleField.getText(), subtitleField.getText(),
                intOf(titleSizeField, 72), intOf(subSizeField, 36),
                titleColorField.getText(), subColorField.getText(), showBar.isSelected());
        Path tmp = null;
        try {
            tmp = Files.createTempFile("cover-preview-", ".png");
            ImageTools.write(rendered, tmp);
            g.drawImage(new javafx.scene.image.Image(tmp.toUri().toString()), ox, oy, w, h);
        } catch (Exception e) {
            status.setText("预览失败：" + e.getMessage());
        } finally {
            if (tmp != null) {
                try {
                    Files.deleteIfExists(tmp);
                } catch (Exception ignored) {
                }
            }
        }
    }

    /** 渲染封面（底图按裁剪填充/完整包含铺到 w×h，再叠文字）。 */
    static BufferedImage render(BufferedImage src, int w, int h, boolean crop,
                                String title, String subtitle, int titleSize, int subSize,
                                String titleColor, String subColor, boolean bar) {
        BufferedImage out = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
        java.awt.Graphics2D g = out.createGraphics();
        g.setRenderingHint(java.awt.RenderingHints.KEY_INTERPOLATION,
                java.awt.RenderingHints.VALUE_INTERPOLATION_BICUBIC);
        g.setRenderingHint(java.awt.RenderingHints.KEY_TEXT_ANTIALIASING,
                java.awt.RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        g.setColor(java.awt.Color.BLACK);
        g.fillRect(0, 0, w, h);

        if (src != null) {
            if (crop) {
                double scale = Math.max((double) w / src.getWidth(), (double) h / src.getHeight());
                int dw = (int) Math.ceil(src.getWidth() * scale);
                int dh = (int) Math.ceil(src.getHeight() * scale);
                g.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh, null);
            } else {
                double scale = Math.min((double) w / src.getWidth(), (double) h / src.getHeight());
                int dw = (int) Math.round(src.getWidth() * scale);
                int dh = (int) Math.round(src.getHeight() * scale);
                g.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh, null);
            }
        }

        if (bar) {
            int barH = Math.max(80, h / 3);
            g.setPaint(new java.awt.GradientPaint(0, h - barH, new java.awt.Color(0, 0, 0, 0),
                    0, h, new java.awt.Color(0, 0, 0, 205)));
            g.fillRect(0, h - barH, w, barH);
        }

        int margin = Math.max(24, w / 40);
        int y = h - margin;
        if (subtitle != null && !subtitle.isBlank()) {
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, subSize));
            g.setColor(parseColor(subColor, java.awt.Color.LIGHT_GRAY));
            y -= subSize;
            g.drawString(subtitle, margin, y);
            y -= Math.max(8, subSize / 2);
        }
        if (title != null && !title.isBlank()) {
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, titleSize));
            g.setColor(parseColor(titleColor, java.awt.Color.WHITE));
            y -= titleSize;
            g.drawString(title, margin, y);
        }
        g.dispose();
        return out;
    }

    private static java.awt.Color parseColor(String hex, java.awt.Color def) {
        try {
            String h = hex.trim();
            if (!h.startsWith("#")) h = "#" + h;
            return java.awt.Color.decode(h);
        } catch (Exception e) {
            return def;
        }
    }

    private static int intOf(TextField f, int def) {
        try {
            return Integer.parseInt(f.getText().trim());
        } catch (Exception e) {
            return def;
        }
    }

    private void exportCover() {
        if (currentBg == null) { status.setText("请先选择底图"); return; }
        String res = resolutionBox.getValue();
        int w = 1920, h = 1080;
        if (res != null && res.contains("x")) {
            String[] parts = res.split("x");
            w = Integer.parseInt(parts[0]);
            h = Integer.parseInt(parts[1]);
        }
        boolean crop = fitBox.getSelectionModel().getSelectedIndex() == 0;
        BufferedImage img = render(currentBg, w, h, crop,
                titleField.getText(), subtitleField.getText(),
                intOf(titleSizeField, 72), intOf(subSizeField, 36),
                titleColorField.getText(), subColorField.getText(), showBar.isSelected());
        Path outDir = Settings.load().effectiveExportDir().resolve("封面");
        try {
            Files.createDirectories(outDir);
            Path target = outDir.resolve("cover_" + System.currentTimeMillis() + ".png");
            ImageTools.write(img, target);
            status.setText("已导出：" + target);
            toast("封面已导出：" + w + "×" + h);
        } catch (Exception e) {
            status.setText("导出失败：" + e.getMessage());
        }
    }
}
