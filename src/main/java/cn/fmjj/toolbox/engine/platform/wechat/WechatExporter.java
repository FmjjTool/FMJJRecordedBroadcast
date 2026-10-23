package cn.fmjj.toolbox.engine.platform.wechat;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 公众号文章导出：HTML（图片改本地路径）/ Markdown。
 * 对应 Python 版 exporters/wechat_writer.py 的 html / md 两个格式。
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

    /** 极简 HTML → Markdown（够用于公众号正文：标题/段落/图片/换行/粗体）。 */
    public static String htmlToMarkdown(String html, Map<String, String> localImages) {
        String s = html;
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
