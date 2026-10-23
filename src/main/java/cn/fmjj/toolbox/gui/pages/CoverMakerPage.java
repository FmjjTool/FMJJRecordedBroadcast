package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TextField;
import javafx.scene.image.ImageView;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;

import java.io.File;
import java.nio.file.Path;
import java.util.function.Consumer;

/**
 * 🖼 封面制作：图片 + 标题/副标题 → 16:9 封面（对应 Python 版 cover_maker）。
 */
public class CoverMakerPage extends BasePage {

    private final TextField imageField = new TextField();
    private final TextField titleField = new TextField();
    private final TextField subtitleField = new TextField();
    private final TextField widthField = new TextField("1280");
    private final ImageView preview = new ImageView();
    private final Label status = new Label("就绪");

    private Consumer<String> toast;

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    public String title() { return "🖼 封面制作"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    @Override
    protected Node build() {
        Button pick = new Button("📂 选图");
        pick.setOnAction(e -> pickImage());
        Button make = new Button("✨ 生成封面");
        make.setDefaultButton(true);
        make.setOnAction(e -> make());
        Button openOut = new Button("📁 打开输出目录");
        openOut.setOnAction(e -> AudioEngine.openInExplorer(outDir()));

        titleField.setPromptText("主标题（大号白字）");
        subtitleField.setPromptText("副标题（小号灰字，可空）");
        imageField.setEditable(false);
        widthField.setPrefWidth(80);

        preview.setFitWidth(480);
        preview.setPreserveRatio(true);
        preview.setStyle("-fx-border-color: #ccc;");

        HBox row1 = new HBox(8, new Label("图片"), imageField, pick, new Label("宽度"), widthField, make, openOut);
        row1.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(imageField, Priority.ALWAYS);
        HBox row2 = new HBox(8, new Label("标题"), titleField, new Label("副标题"), subtitleField);
        row2.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(titleField, Priority.ALWAYS);
        HBox.setHgrow(subtitleField, Priority.ALWAYS);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("背景按比例铺满并居中裁成 16:9，底部加深色渐变条后叠加文字；"
                + "输出到「导出目录/封面」");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, row1, row2, preview, status);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        return root;
    }

    private Path outDir() { return Settings.load().effectiveExportDir().resolve("封面"); }

    /** BufferedImage → JavaFX 图片（避免依赖 javafx-swing 模块；预览前先缩到 480 宽）。 */
    private static javafx.scene.image.Image toFxImage(java.awt.image.BufferedImage src) {
        int w = Math.min(480, src.getWidth());
        int h = (int) Math.round(src.getHeight() * (w / (double) src.getWidth()));
        var scaled = cn.fmjj.toolbox.engine.image.ImageTools.scale(src, w, h);
        var wi = new javafx.scene.image.WritableImage(w, h);
        var pw = wi.getPixelWriter();
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                pw.setArgb(x, y, scaled.getRGB(x, y));
            }
        }
        return wi;
    }

    private void pickImage() {
        FileChooser c = new FileChooser();
        c.setTitle("选择背景图片");
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("图片", "*.jpg", "*.jpeg", "*.png", "*.bmp"));
        File f = c.showOpenDialog(preview.getScene() == null ? null : preview.getScene().getWindow());
        if (f != null) {
            imageField.setText(f.getAbsolutePath());
            try {
                var img = ImageTools.read(f.toPath());
                preview.setImage(toFxImage(img));
                status.setText("已加载：" + img.getWidth() + "×" + img.getHeight());
            } catch (Exception e) {
                status.setText("读取失败：" + e.getMessage());
            }
        }
    }

    private void make() {
        String path = imageField.getText().trim();
        if (path.isEmpty()) { status.setText("请先选图"); return; }
        int w;
        try {
            w = Integer.parseInt(widthField.getText().trim());
        } catch (Exception e) {
            w = 1280;
        }
        try {
            var src = ImageTools.read(Path.of(path));
            var cover = ImageTools.makeCover(src, w, titleField.getText(), subtitleField.getText());
            Path target = outDir().resolve("cover_" + System.currentTimeMillis() + ".png");
            ImageTools.write(cover, target);
            preview.setImage(toFxImage(cover));
            status.setText("已生成：" + target);
            if (toast != null) toast("封面已生成：" + cover.getWidth() + "×" + cover.getHeight());
        } catch (Exception e) {
            status.setText("生成失败：" + e.getMessage());
        }
    }
}
