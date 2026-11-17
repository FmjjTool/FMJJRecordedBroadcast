package cn.fmjj.toolbox.engine.platform.wechat;

import org.apache.fontbox.ttf.TrueTypeCollection;
import org.apache.fontbox.ttf.TrueTypeFont;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.font.PDFont;
import org.apache.pdfbox.pdmodel.font.PDType0Font;
import org.apache.pdfbox.pdmodel.font.PDType1Font;
import org.apache.pdfbox.pdmodel.font.Standard14Fonts;
import org.apache.pdfbox.pdmodel.graphics.image.PDImageXObject;
import org.apache.poi.util.Units;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.apache.poi.xwpf.usermodel.XWPFParagraph;
import org.apache.poi.xwpf.usermodel.XWPFRun;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Element;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 公众号文章导出：HTML（图片改本地路径）/ Markdown / Word / PDF。
 * 对应 Python 版 exporters/wechat_writer.py。
 */
public final class WechatExporter {

    private WechatExporter() {}

    /** 导出 HTML（images 传「远程URL → 本地文件名」映射）。 */
    public static Path exportHtml(WechatArticleParser.Article art, Path outDir,
                                  Map<String, String> localImages) throws IOException {
        Files.createDirectories(outDir);
        String body = art.contentHtml();
        for (var e : localImages.entrySet()) {
            body = body.replace(e.getKey(), e.getValue());
        }
        String html = """
                <!DOCTYPE html>
                <html lang="zh-CN"><head><meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>%s</title>
                <style>
                  body{max-width:760px;margin:24px auto;padding:0 16px;font:16px/1.8 -apple-system,"Microsoft YaHei",sans-serif;color:#222}
                  h1{font-size:22px;line-height:1.4;margin:0 0 8px}
                  .meta{color:#888;font-size:13px;margin-bottom:20px}
                  img{max-width:100%%;height:auto;display:block;margin:12px auto}
                </style></head><body>
                <h1>%s</h1>
                <div class="meta">%s%s%s</div>
                %s
                </body></html>
                """.formatted(esc(art.title()), esc(art.title()),
                art.account().isEmpty() ? "" : esc(art.account()),
                art.author().isEmpty() ? "" : " · " + esc(art.author()),
                art.publishTime().isEmpty() ? "" : " · " + esc(art.publishTime()),
                body);
        Path target = outDir.resolve(art.stem() + ".html");
        Files.writeString(target, html, StandardCharsets.UTF_8);
        return target;
    }

    /** 导出 Markdown（图片指向本地文件）。 */
    public static Path exportMarkdown(WechatArticleParser.Article art, Path outDir,
                                      Map<String, String> localImages) throws IOException {
        Files.createDirectories(outDir);
        StringBuilder md = new StringBuilder();
        md.append("# ").append(art.title()).append("\n\n");
        StringBuilder meta = new StringBuilder();
        if (!art.account().isEmpty()) meta.append(art.account());
        if (!art.author().isEmpty()) meta.append(meta.length() > 0 ? " · " : "").append(art.author());
        if (!art.publishTime().isEmpty()) meta.append(meta.length() > 0 ? " · " : "").append(art.publishTime());
        if (meta.length() > 0) md.append("> ").append(meta).append("\n\n");
        if (!art.url().isEmpty()) md.append("原文：").append(art.url()).append("\n\n");
        md.append(htmlToMarkdown(art.contentHtml(), localImages)).append("\n");

        Path target = outDir.resolve(art.stem() + ".md");
        Files.writeString(target, md.toString(), StandardCharsets.UTF_8);
        return target;
    }

    /** 导出 Word（docx，正文段落 + 已下载图片）。 */
    public static Path exportWord(WechatArticleParser.Article art, Path outDir,
                                  Map<String, String> localImages) throws IOException {
        Files.createDirectories(outDir);
        try (XWPFDocument doc = new XWPFDocument()) {
            XWPFParagraph titleP = doc.createParagraph();
            XWPFRun titleR = titleP.createRun();
            titleR.setBold(true);
            titleR.setFontSize(20);
            titleR.setText(art.title());

            StringBuilder meta = new StringBuilder();
            if (!art.account().isEmpty()) meta.append(art.account());
            if (!art.author().isEmpty()) meta.append(meta.length() > 0 ? " · " : "").append(art.author());
            if (!art.publishTime().isEmpty()) meta.append(meta.length() > 0 ? " · " : "").append(art.publishTime());
            if (!art.url().isEmpty()) meta.append(meta.length() > 0 ? "\n" : "").append(art.url());
            if (meta.length() > 0) {
                XWPFParagraph mp = doc.createParagraph();
                XWPFRun mr = mp.createRun();
                mr.setFontSize(9);
                mr.setColor("888888");
                mr.setText(meta.toString());
            }

            Element body = Jsoup.parse(art.contentHtml()).body();
            for (Element el : body.children()) addWordBlock(doc, el, outDir, localImages);

            Path target = outDir.resolve(art.stem() + ".docx");
            try (var out = Files.newOutputStream(target)) {
                doc.write(out);
            }
            return target;
        }
    }

    private static void addWordBlock(XWPFDocument doc, Element el, Path outDir,
                                     Map<String, String> localImages) {
        String tag = el.tagName().toLowerCase();
        if ("img".equals(tag)) {
            addWordImage(doc, el.attr("src"), outDir, localImages);
            return;
        }
        String text = el.text().strip();
        if (!text.isEmpty()) {
            XWPFParagraph p = doc.createParagraph();
            XWPFRun r = p.createRun();
            boolean heading = tag.matches("h[1-6]");
            if (heading) r.setBold(true);
            if ("li".equals(tag)) text = "• " + text;
            r.setText(text);
            r.setFontSize(heading ? 15 : 11);
        }
        for (Element img : el.select("img")) {
            addWordImage(doc, img.attr("src"), outDir, localImages);
        }
    }

    private static void addWordImage(XWPFDocument doc, String src, Path outDir,
                                     Map<String, String> localImages) {
        Path file = resolveLocalImage(src, outDir, localImages);
        if (file == null) return;
        try {
            BufferedImage img = ImageIO.read(file.toFile());
            if (img == null) return;
            int type = pictureType(file);
            int w = 480;
            int h = Math.max(1, (int) (img.getHeight() * 480.0 / Math.max(img.getWidth(), 1)));
            XWPFParagraph p = doc.createParagraph();
            XWPFRun r = p.createRun();
            try (InputStream in = Files.newInputStream(file)) {
                r.addPicture(in, type, file.getFileName().toString(),
                        Units.toEMU(w), Units.toEMU(h));
            }
        } catch (Exception ignored) {
        }
    }

    private static int pictureType(Path file) {
        String n = file.getFileName().toString().toLowerCase(java.util.Locale.ROOT);
        if (n.endsWith(".png")) return XWPFDocument.PICTURE_TYPE_PNG;
        if (n.endsWith(".gif")) return XWPFDocument.PICTURE_TYPE_GIF;
        if (n.endsWith(".bmp")) return XWPFDocument.PICTURE_TYPE_BMP;
        return XWPFDocument.PICTURE_TYPE_JPEG;
    }

    private static Path resolveLocalImage(String src, Path outDir, Map<String, String> localImages) {
        String rel = localImages.get(src);
        if (rel != null) {
            Path p = outDir.resolve(rel);
            if (Files.isRegularFile(p)) return p;
        }
        try {
            Path p = Path.of(src);
            if (Files.isRegularFile(p)) return p;
        } catch (Exception ignored) {
        }
        return null;
    }

    /** 导出 PDF（A4，中文自动换行；图片追加在正文后）。 */
    public static Path exportPdf(WechatArticleParser.Article art, Path outDir,
                                 Map<String, String> localImages) throws IOException {
        Files.createDirectories(outDir);
        try (PDDocument doc = new PDDocument()) {
            PDFont font = loadCjkFont(doc);
            PdfCtx ctx = new PdfCtx(doc, font);
            ctx.heading(art.title());
            StringBuilder meta = new StringBuilder();
            if (!art.account().isEmpty()) meta.append(art.account());
            if (!art.author().isEmpty()) meta.append(meta.length() > 0 ? " · " : "").append(art.author());
            if (!art.publishTime().isEmpty()) meta.append(meta.length() > 0 ? " · " : "").append(art.publishTime());
            if (meta.length() > 0) ctx.meta(meta.toString());
            if (!art.url().isEmpty()) ctx.meta(art.url());
            ctx.space();
            Element body = Jsoup.parse(art.contentHtml()).body();
            for (Element el : body.children()) {
                String text = el.text().strip();
                if (text.isEmpty()) continue;
                boolean heading = el.tagName().toLowerCase().matches("h[1-6]");
                ctx.paragraph((("li".equals(el.tagName().toLowerCase())) ? "• " : "") + text, heading);
            }
            for (Element img : body.select("img")) {
                Path file = resolveLocalImage(img.attr("src"), outDir, localImages);
                if (file != null) ctx.image(file);
            }
            ctx.close();
            Path target = outDir.resolve(art.stem() + ".pdf");
            doc.save(target.toFile());
            return target;
        }
    }

    private static PDFont loadCjkFont(PDDocument doc) {
        String[][] cands = {
                {"C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"},
                {"C:/Windows/Fonts/simsun.ttc", "SimSun"},
                {"C:/Windows/Fonts/simhei.ttf", "SimHei"},
        };
        for (String[] c : cands) {
            File f = new File(c[0]);
            if (!f.isFile()) continue;
            try {
                if (c[0].toLowerCase(java.util.Locale.ROOT).endsWith(".ttc")) {
                    try (TrueTypeCollection ttc = new TrueTypeCollection(f)) {
                        TrueTypeFont ttf = ttc.getFontByName(c[1]);
                        if (ttf != null) return PDType0Font.load(doc, ttf, true);
                    }
                } else {
                    return PDType0Font.load(doc, f);
                }
            } catch (Exception ignored) {
            }
        }
        return new PDType1Font(Standard14Fonts.FontName.HELVETICA);
    }

    private static final class PdfCtx {
        final PDDocument doc;
        final PDFont font;
        final float margin = 50;
        final float maxWidth = PDRectangle.A4.getWidth() - margin * 2;
        PDPage page;
        PDPageContentStream cs;
        float y;

        PdfCtx(PDDocument doc, PDFont font) throws IOException {
            this.doc = doc;
            this.font = font;
            newPage();
        }

        void newPage() throws IOException {
            if (cs != null) cs.close();
            page = new PDPage(PDRectangle.A4);
            doc.addPage(page);
            cs = new PDPageContentStream(doc, page);
            y = page.getMediaBox().getHeight() - margin;
        }

        void heading(String text) throws IOException {
            write(text, 17, true);
            y -= 4;
        }

        void meta(String text) throws IOException {
            write(text, 9, false);
        }

        void paragraph(String text, boolean heading) throws IOException {
            write(text, heading ? 13 : 11, heading);
            y -= 3;
        }

        void space() {
            y -= 6;
        }

        void write(String text, float size, boolean bold) throws IOException {
            if (text == null || text.isEmpty()) return;
            if (font instanceof PDType1Font) text = text.replaceAll("[^\\x00-\\x7F]", "?");
            for (String para : text.split("\\R")) {
                for (String line : wrap(para, size)) {
                    if (y < margin + 20) newPage();
                    cs.beginText();
                    cs.setFont(font, size);
                    cs.newLineAtOffset(margin, y);
                    cs.showText(line);
                    cs.endText();
                    y -= size * 1.6f;
                }
            }
        }

        List<String> wrap(String text, float size) throws IOException {
            List<String> lines = new ArrayList<>();
            StringBuilder cur = new StringBuilder();
            for (int i = 0; i < text.length(); ) {
                int cp = text.codePointAt(i);
                String ch = new String(Character.toChars(cp));
                float w = font.getStringWidth(cur + ch) / 1000f * size;
                if (w > maxWidth && cur.length() > 0) {
                    lines.add(cur.toString());
                    cur.setLength(0);
                }
                cur.append(ch);
                i += Character.charCount(cp);
            }
            if (cur.length() > 0) lines.add(cur.toString());
            if (lines.isEmpty()) lines.add("");
            return lines;
        }

        void image(Path file) throws IOException {
            try {
                PDImageXObject img = PDImageXObject.createFromFile(file.toString(), doc);
                float w = Math.min(480, maxWidth);
                float h = img.getHeight() * w / Math.max(img.getWidth(), 1);
                if (y - h < margin) newPage();
                cs.drawImage(img, margin, y - h, w, h);
                y -= h + 10;
            } catch (Exception ignored) {
            }
        }

        void close() throws IOException {
            if (cs != null) cs.close();
        }
    }

    /** 极简 HTML → Markdown（够用于公众号正文：标题/段落/图片/换行/粗体）。 */
    public static String htmlToMarkdown(String html, Map<String, String> localImages) {        String s = html;
        for (var e : localImages.entrySet()) s = s.replace(e.getKey(), e.getValue());

        s = s.replaceAll("(?is)<script.*?</script>", "")
             .replaceAll("(?is)<style.*?</style>", "");

        // 图片
        s = s.replaceAll("(?is)<img[^>]*src=\"([^\"]+)\"[^>]*>", "\n\n![]($1)\n\n");
        // 标题
        for (int level = 1; level <= 6; level++) {
            String hashes = "#".repeat(level);
            s = s.replaceAll("(?is)<h" + level + "[^>]*>(.*?)</h" + level + ">", "\n\n" + hashes + " $1\n\n");
        }
        // 段落 / 换行
        s = s.replaceAll("(?is)<p[^>]*>", "\n\n").replaceAll("(?is)</p>", "")
             .replaceAll("(?is)<br\\s*/?>", "\n")
             .replaceAll("(?is)<(strong|b)[^>]*>", "**").replaceAll("(?is)</(strong|b)>", "**")
             .replaceAll("(?is)<(em|i)[^>]*>", "*").replaceAll("(?is)</(em|i)>", "*");
        // 列表
        s = s.replaceAll("(?is)<li[^>]*>", "\n- ").replaceAll("(?is)</li>", "");
        // 去掉剩余标签
        s = s.replaceAll("(?is)<[^>]+>", "");
        // 实体
        s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
             .replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'");
        // 压缩空行
        s = s.replaceAll("[ \\t]+\\n", "\n").replaceAll("\\n{3,}", "\n\n");
        return s.strip();
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }

    /** 便捷：空映射。 */
    public static Map<String, String> noImages() {
        return new LinkedHashMap<>();
    }
}
