package cn.fmjj.toolbox.engine.image;

import javax.imageio.ImageIO;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.Set;

/**
 * 图片处理：裁切 / 缩放 / 封面制作（对应 Python 版 image_crop / cover_maker 的核心）。
 * 纯 ImageIO + Graphics2D，不依赖 ffmpeg。
 */
public final class ImageTools {

    public static final Set<String> EXTS = Set.of(
            ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp");

    /** 裁切方式。 */
    public enum CropMode {
        /** 居中裁成正方形（封面常用）。 */
        CENTER_SQUARE,
        /** 居中裁成 16:9。 */
        CENTER_16_9,
        /** 居中裁成 3:4（竖屏海报）。 */
        CENTER_3_4,
        /** 缩放到指定宽高（可能变形）。 */
        STRETCH
    }

    private ImageTools() {}

    public static boolean isImage(Path p) {
        String n = p.getFileName().toString().toLowerCase(Locale.ROOT);
        int dot = n.lastIndexOf('.');
        return dot > 0 && EXTS.contains(n.substring(dot));
    }

    /** 读图（支持 jpg/png/bmp/gif；webp 需额外插件，读不到会报错）。 */
    public static BufferedImage read(Path file) throws IOException {
        BufferedImage img = ImageIO.read(file.toFile());
        if (img == null) throw new IOException("无法解码图片（格式可能不受支持）：" + file.getFileName());
        return img;
    }

    /** 保存（按扩展名选格式；jpg 用白底压平透明通道）。 */
    public static void write(BufferedImage img, Path target) throws IOException {
        String name = target.getFileName().toString().toLowerCase(Locale.ROOT);
        String fmt = name.endsWith(".png") ? "png" : name.endsWith(".bmp") ? "bmp" : "jpg";
        BufferedImage out = img;
        if ("jpg".equals(fmt) && img.getType() != BufferedImage.TYPE_INT_RGB) {
            out = new BufferedImage(img.getWidth(), img.getHeight(), BufferedImage.TYPE_INT_RGB);
            Graphics2D g = out.createGraphics();
            g.setColor(java.awt.Color.WHITE);
            g.fillRect(0, 0, img.getWidth(), img.getHeight());
            g.drawImage(img, 0, 0, null);
            g.dispose();
        }
        Files.createDirectories(target.toAbsolutePath().getParent());
        if (!ImageIO.write(out, fmt, target.toFile())) {
            throw new IOException("保存失败（不支持的格式）：" + fmt);
        }
    }

    /**
     * 裁切/缩放。
     *
     * @param maxWidth  输出最大宽度（0 = 不限制）
     * @param maxHeight 输出最大高度（0 = 不限制）
     */
    public static BufferedImage crop(BufferedImage src, CropMode mode, int maxWidth, int maxHeight) {
        int w = src.getWidth();
        int h = src.getHeight();
        BufferedImage cropped = switch (mode) {
            case CENTER_SQUARE -> centerCrop(src, 1, 1);
            case CENTER_16_9 -> centerCrop(src, 16, 9);
            case CENTER_3_4 -> centerCrop(src, 3, 4);
            case STRETCH -> src;
        };
        int tw = maxWidth > 0 ? maxWidth : cropped.getWidth();
        int th = maxHeight > 0 ? maxHeight : cropped.getHeight();
        if (maxWidth > 0 && maxHeight > 0 && mode != CropMode.STRETCH) {
            // 按目标框等比缩放（不放大）
            double scale = Math.min((double) tw / cropped.getWidth(), (double) th / cropped.getHeight());
            if (scale < 1) {
                tw = (int) Math.round(cropped.getWidth() * scale);
                th = (int) Math.round(cropped.getHeight() * scale);
            } else {
                tw = cropped.getWidth();
                th = cropped.getHeight();
            }
        }
        if (tw == cropped.getWidth() && th == cropped.getHeight()) return cropped;
        return scale(cropped, tw, th);
    }

    /** 按比例居中裁切（ratioW:ratioH）。 */
    static BufferedImage centerCrop(BufferedImage src, int ratioW, int ratioH) {
        int w = src.getWidth();
        int h = src.getHeight();
        double targetRatio = (double) ratioW / ratioH;
        double cur = (double) w / h;
        int cw = w, ch = h;
        if (cur > targetRatio) {
            cw = (int) Math.round(h * targetRatio);
        } else {
            ch = (int) Math.round(w / targetRatio);
        }
        int x = (w - cw) / 2;
        int y = (h - ch) / 2;
        BufferedImage out = new BufferedImage(cw, ch, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = out.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
        g.drawImage(src, 0, 0, cw, ch, x, y, x + cw, y + ch, null);
        g.dispose();
        return out;
    }

    /** 等比缩放到指定宽高（不保持比例时变形）。 */
    public static BufferedImage scale(BufferedImage src, int w, int h) {
        BufferedImage out = new BufferedImage(Math.max(1, w), Math.max(1, h), BufferedImage.TYPE_INT_RGB);
        Graphics2D g = out.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
        g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
        g.drawImage(src, 0, 0, Math.max(1, w), Math.max(1, h), null);
        g.dispose();
        return out;
    }

    /**
     * 封面：图片做背景（居中裁成 16:9）+ 底部半透明条 + 标题/副标题文字。
     *
     * @param width   输出宽度（高度按 16:9）
     * @param title   主标题（可空）
     * @param subtitle 副标题（可空）
     */
    public static BufferedImage makeCover(BufferedImage src, int width, String title, String subtitle) {
        int w = Math.max(320, width);
        int h = (int) Math.round(w * 9.0 / 16.0);
        BufferedImage out = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = out.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC);
        g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING,
                RenderingHints.VALUE_TEXT_ANTIALIAS_ON);

        // 背景：等比铺满 + 居中裁切
        double scale = Math.max((double) w / src.getWidth(), (double) h / src.getHeight());
        int dw = (int) Math.ceil(src.getWidth() * scale);
        int dh = (int) Math.ceil(src.getHeight() * scale);
        g.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh, null);

        // 底部渐变条
        int barH = Math.max(64, h / 3);
        g.setPaint(new java.awt.GradientPaint(0, h - barH, new java.awt.Color(0, 0, 0, 0),
                0, h, new java.awt.Color(0, 0, 0, 200)));
        g.fillRect(0, h - barH, w, barH);

        int margin = Math.max(16, w / 40);
        int titleSize = Math.max(18, w / 18);
        int subSize = Math.max(12, w / 32);
        int y = h - margin;

        if (subtitle != null && !subtitle.isBlank()) {
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, subSize));
            g.setColor(new java.awt.Color(220, 220, 220));
            y -= subSize;
            g.drawString(subtitle, margin, y);
            y -= Math.max(8, subSize / 2);
        }
        if (title != null && !title.isBlank()) {
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, titleSize));
            g.setColor(java.awt.Color.WHITE);
            y -= titleSize;
            g.drawString(title, margin, y);
        }
        g.dispose();
        return out;
    }
}
