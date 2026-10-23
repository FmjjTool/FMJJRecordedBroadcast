package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.wechat.WechatArticleParser;
import cn.fmjj.toolbox.engine.platform.wechat.WechatExporter;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;

import java.io.File;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 📰 公众号解析（对应 Python 版 wechat_article.py）：
 * 批量粘贴 mp.weixin 链接 → 抓正文与图片 → 导出 HTML / Markdown。
 */
public class WechatArticlePage extends BasePage {

    private final TextArea input = new TextArea();
    private final TextField outDirField = new TextField();
    private final CheckBox fmtHtml = new CheckBox("HTML");
    private final CheckBox fmtMd = new CheckBox("Markdown");
    private final TextArea log = new TextArea();
    private final Label status = new Label("就绪");
    private final ProgressBar progress = new ProgressBar(0);
    private final Button startBtn = new Button("📰 开始解析");
    private final Button cancelBtn = new Button("⏹ 取消");
    private final AtomicBoolean cancel = new AtomicBoolean(false);
    private final AtomicBoolean running = new AtomicBoolean(false);

    private final WechatArticleParser parser = new WechatArticleParser();
    private final FileDownloader downloader = new FileDownloader();

    private Consumer<String> toast;

    @Override
    public String title() { return "📰 公众号解析"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        input.setPromptText("每行一个公众号文章链接（https://mp.weixin.qq.com/s/xxx），自动提取去重");
        input.setPrefRowCount(4);
        log.setEditable(false);
        log.setPrefRowCount(10);
        fmtHtml.setSelected(true);
        fmtMd.setSelected(true);
        cancelBtn.setDisable(true);
        progress.setPrefWidth(180);

        outDirField.setText(Settings.load().effectiveExportDir().resolve("公众号").toString());
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> {
            DirectoryChooser c = new DirectoryChooser();
            c.setTitle("选择导出目录");
            File d = c.showDialog(input.getScene() == null ? null : input.getScene().getWindow());
            if (d != null) outDirField.setText(d.getAbsolutePath());
        });
        Button openOut = new Button("📁 打开导出目录");
        openOut.setOnAction(e -> AudioEngine.openInExplorer(Path.of(outDirField.getText().trim())));

        startBtn.setDefaultButton(true);
        startBtn.setOnAction(e -> start());
        cancelBtn.setOnAction(e -> {
            cancel.set(true);
            status.setText("正在取消（完成当前文章后停止）…");
        });

        HBox dirRow = new HBox(8, new Label("导出目录"), outDirField, browse, openOut);
        dirRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(outDirField, Priority.ALWAYS);

        HBox actionRow = new HBox(10, startBtn, cancelBtn, fmtHtml, fmtMd, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("抓取公众号正文与图片，导出为 HTML（图片本地化）或 Markdown；"
                + "批量时串行请求，避免触发微信风控");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, input, dirRow, actionRow, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(log, Priority.ALWAYS);
        return root;
    }

    private void appendLog(String s) {
        Platform.runLater(() -> log.appendText(s + "\n"));
    }

    private void start() {
        if (running.get()) return;
        List<String> urls = WechatArticleParser.extractUrls(input.getText());
        if (urls.isEmpty()) {
            status.setText("未识别到公众号链接（需 mp.weixin.qq.com）");
            return;
        }
        if (!fmtHtml.isSelected() && !fmtMd.isSelected()) {
            status.setText("请至少勾选一种导出格式");
            return;
        }
        Path outDir = Path.of(outDirField.getText().trim());
        boolean doHtml = fmtHtml.isSelected();
        boolean doMd = fmtMd.isSelected();

        running.set(true);
        cancel.set(false);
        startBtn.setDisable(true);
        cancelBtn.setDisable(true);
        progress.setProgress(0);
        log.clear();
        appendLog("📰 开始解析 " + urls.size() + " 篇 → " + outDir);

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (int i = 0; i < urls.size(); i++) {
                    if (cancel.get()) break;
                    String url = urls.get(i);
                    int idx = i;
                    Platform.runLater(() -> {
                        status.setText("解析中… " + (idx + 1) + "/" + urls.size());
                        progress.setProgress((double) idx / urls.size());
                    });
                    try {
                        var art = parser.fetch(url);
                        appendLog("📄 " + art.title());
                        appendLog("   公众号：" + (art.account().isEmpty() ? "-" : art.account())
                                + "  时间：" + (art.publishTime().isEmpty() ? "-" : art.publishTime())
                                + "  图片：" + art.images().size() + " 张");

                        // 下载图片到 {stem}_files/
                        Map<String, String> local = new LinkedHashMap<>();
                        Path imgDir = outDir.resolve(art.stem() + "_files");
                        for (int k = 0; k < art.images().size(); k++) {
                            if (cancel.get()) break;
                            String imgUrl = art.images().get(k);
                            String name = (k + 1) + WechatArticleParser.imageExt(imgUrl);
                            var r = downloader.download(List.of(imgUrl), imgDir.resolve(name),
                                    Map.of("Referer", "https://mp.weixin.qq.com/",
                                            "User-Agent", "Mozilla/5.0"), p -> { }, cancel::get);
                            if (r.success()) local.put(imgUrl, art.stem() + "_files/" + name);
                        }
                        if (doHtml) appendLog("   ✅ " + WechatExporter.exportHtml(art, outDir, local).getFileName());
                        if (doMd) appendLog("   ✅ " + WechatExporter.exportMarkdown(art, outDir, local).getFileName());
                        ok++;
                    } catch (Exception e) {
                        appendLog("   ❌ " + url + "：" + e.getMessage());
                        fail++;
                    }
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            running.set(false);
            startBtn.setDisable(false);
            cancelBtn.setDisable(true);
            progress.setProgress(1);
            status.setText("完成：成功 " + r[0] + "，失败 " + r[1]);
            if (r[0] > 0) toast("公众号解析完成：" + r[0] + " 篇");
        });
        task.setOnFailed(e -> {
            running.set(false);
            startBtn.setDisable(false);
            cancelBtn.setDisable(true);
            status.setText("异常：" + task.getException().getMessage());
        });
        new Thread(task, "wechat-article").start();
    }
}
