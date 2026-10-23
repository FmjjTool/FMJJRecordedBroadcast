package cn.fmjj.toolbox.gui.pages;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.awt.image.BufferedImage;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 封面渲染纯逻辑测试（不依赖 JavaFX 场景）。 */
class CoverMakerRenderTest {

    private BufferedImage src(int w, int h, int rgb) {
        BufferedImage b = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
        var g = b.createGraphics();
        g.setColor(new java.awt.Color(rgb));
        g.fillRect(0, 0, w, h);
        g.dispose();
        return b;
    }

    @Test
    @DisplayName("render：输出尺寸与目标分辨率一致")
    void size() {
        var out = CoverMakerPage.render(src(800, 600, 0xFF8800), 1280, 720, true,
                "标题", "副标题", 72, 36, "#ffffff", "#dcdcdc", true);
        assertEquals(1280, out.getWidth());
        assertEquals(720, out.getHeight());
    }

    @Test
    @DisplayName("裁剪填充：整幅被底图覆盖（角落不是黑）")
    void cropFill() {
        int bgColor = 0x00FF00;   // 纯绿底图
        var out = CoverMakerPage.render(src(800, 800, bgColor), 1280, 720, true,
                "", "", 72, 36, "#ffffff", "#dcdcdc", false);
        // 左上角应是底图颜色（800x800 放大铺满 1280x720）
        int corner = out.getRGB(2, 2) & 0xFFFFFF;
        assertEquals(0x00FF00, corner, "裁剪填充应铺满，角落为底图色");
    }

    @Test
    @DisplayName("完整包含：宽图在 16:9 画布上下留黑边")
    void containLetterbox() {
        var out = CoverMakerPage.render(src(1600, 400, 0x00FF00), 1280, 720, false,
                "", "", 72, 36, "#ffffff", "#dcdcdc", false);
        // 超宽图完整包含 → 上下黑边
        int top = out.getRGB(640, 2) & 0xFFFFFF;
        int middle = out.getRGB(640, 360) & 0xFFFFFF;
        assertEquals(0x000000, top, "上方应为黑边");
        assertEquals(0x00FF00, middle, "中间应为底图色");
    }

    @Test
    @DisplayName("无底图：仍能渲染（黑底），不抛异常")
    void nullBackground() {
        var out = CoverMakerPage.render(null, 640, 360, true,
                "标题", "", 48, 24, "#ffffff", "#cccccc", true);
        assertEquals(640, out.getWidth());
        assertTrue((out.getRGB(5, 5) & 0xFFFFFF) == 0, "无底图应为黑底");
    }
}
