package cn.fmjj.toolbox.engine.image;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 图片工具测试：裁切比例、缩放、封面、读写往返。 */
class ImageToolsTest {

    private BufferedImage img(int w, int h) {
        BufferedImage b = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
        var g = b.createGraphics();
        g.setColor(java.awt.Color.ORANGE);
        g.fillRect(0, 0, w, h);
        g.setColor(java.awt.Color.BLUE);
        g.fillRect(0, 0, w / 2, h / 2);
        g.dispose();
        return b;
    }

    @Test
    @DisplayName("居中裁正方形：200x100 → 100x100")
    void centerSquare() {
        var out = ImageTools.crop(img(200, 100), ImageTools.CropMode.CENTER_SQUARE, 0, 0);
        assertEquals(100, out.getWidth());
        assertEquals(100, out.getHeight());
    }

    @Test
    @DisplayName("居中裁 16:9：100x200 → 100x56")
    void center169() {
        var out = ImageTools.crop(img(100, 200), ImageTools.CropMode.CENTER_16_9, 0, 0);
        assertEquals(100, out.getWidth());
        assertEquals(56, out.getHeight());
    }

    @Test
    @DisplayName("居中裁 3:4：400x400 → 300x400")
    void center34() {
        var out = ImageTools.crop(img(400, 400), ImageTools.CropMode.CENTER_3_4, 0, 0);
        assertEquals(300, out.getWidth());
        assertEquals(400, out.getHeight());
    }

    @Test
    @DisplayName("限制尺寸：STRETCH 强制到目标框；其他模式等比缩小且不放大")
    void maxSize() {
        // STRETCH = 用户显式要求该尺寸（允许变形/放大）
        var stretched = ImageTools.crop(img(100, 50), ImageTools.CropMode.STRETCH, 400, 400);
        assertEquals(400, stretched.getWidth());
        assertEquals(400, stretched.getHeight());

        // 等比缩小：1000x500 裁方 → 500x500，限 400 → 400x400
        var shrunk = ImageTools.crop(img(1000, 500), ImageTools.CropMode.CENTER_SQUARE, 400, 400);
        assertEquals(400, shrunk.getWidth());
        assertEquals(400, shrunk.getHeight());

        // 不放大：100x100 给 400 的框，仍输出 100
        var notUpscaled = ImageTools.crop(img(100, 100), ImageTools.CropMode.CENTER_SQUARE, 400, 400);
        assertEquals(100, notUpscaled.getWidth());
    }

    @Test
    @DisplayName("封面：16:9 输出，宽高按参数")
    void cover() {
        var out = ImageTools.makeCover(img(800, 600), 1280, "标题", "副标题");
        assertEquals(1280, out.getWidth());
        assertEquals(720, out.getHeight());
    }

    @Test
    @DisplayName("封面：空标题也能生成")
    void coverNoText() {
        var out = ImageTools.makeCover(img(640, 480), 640, "", null);
        assertEquals(640, out.getWidth());
        assertEquals(360, out.getHeight());
    }

    @Test
    @DisplayName("读写往返：PNG 保存后能读回且尺寸一致")
    void writeRead(@TempDir Path tmp) throws IOException {
        Path f = tmp.resolve("out/cover.png");
        ImageTools.write(img(320, 180), f);
        assertTrue(Files.isRegularFile(f));
        var back = ImageTools.read(f);
        assertEquals(320, back.getWidth());
        assertEquals(180, back.getHeight());
    }

    @Test
    @DisplayName("JPG 保存：透明图会被压平（不报错）")
    void writeJpg(@TempDir Path tmp) throws IOException {
        BufferedImage argb = new BufferedImage(64, 64, BufferedImage.TYPE_INT_ARGB);
        Path f = tmp.resolve("a.jpg");
        ImageTools.write(argb, f);
        assertTrue(Files.size(f) > 0);
    }

    @Test
    @DisplayName("isImage：扩展名判断")
    void isImage(@TempDir Path tmp) throws IOException {
        assertTrue(ImageTools.isImage(Path.of("a.JPG")));
        assertTrue(ImageTools.isImage(Path.of("a.webp")));
        assertFalse(ImageTools.isImage(Path.of("a.mp4")));
        assertFalse(ImageTools.isImage(Path.of("noext")));

        Path bad = tmp.resolve("bad.png");
        Files.writeString(bad, "not an image");
        assertThrows(IOException.class, () -> ImageTools.read(bad));
    }
}
