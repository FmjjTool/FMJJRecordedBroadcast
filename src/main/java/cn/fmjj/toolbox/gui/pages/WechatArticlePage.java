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
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.control.TextArea;
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
 * 📰 公众号文章解析（对齐 Python 版 wechat_article.py 的控件顺序）。
 *
 * <p>自上而下：标题(18px) + 帮助 → 输入框（**最大高 110**，placeholder 两行示例）→
 * `导出格式:` HTML / PDF / Word / 图片文本(MD) → `导出到:` 默认位置/固定位置 + 📁(30×26) + 路径 →
 * 进度条 → 状态(灰) → 按钮行（`🔍 解析并导出`(绿) + `⏹ 取消`(红) + `📂 打开导出文件夹`）→ 日志。
 *
 * <p>已实现 HTML / MD 导出；**PDF / Word 未实现**（勾选会提示，不假装）。
 */
public class WechatArticlePage extends BasePage {

    private final TextArea input = new TextArea();
    private final CheckBox fmtHtml = new CheckBox("HTML");
    private final CheckBox fmtPdf = new CheckBox("PDF");
    private final CheckBox fmtDocx = new CheckBox("Word");
    private final CheckBox fmtMd = new CheckBox("图片文本(MD)");
    private final ComboBox<String> locBox = new ComboBox<>();
    private final Label locLabel = new Label("");
    private final ProgressBar progress = new ProgressBar(0);
    private final Label status = new Label("就绪");
    private final TextArea log = new TextArea();
    private final Button startBtn = new Button("🔍 解析并导出");
    private final Button cancelBtn = new Button("⏹ 取消");

    private final AtomicBoolean cancel = new AtomicBoolean(false);
    private final AtomicBoolean running = new AtomicBoolean(false);
    private Path customDir;

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
        Label pageTitle = new Label("📰 公众号文章解析");
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");
        Button help = new Button("❓ 帮助");
        help.setOnAction(e -> showHelp());
        HBox titleRow = new HBox(8, pageTitle, spacer(), help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        input.setPromptText("""
                输入公众号文章链接，每行一个（支持单个和批量）：
                https://mp.weixin.qq.com/s/xxxxxxxx
                https://mp.weixin.qq.com/s?__biz=xxx&mid=xxx&idx=1&sn=xxx""");
        input.setMaxHeight(110);
        input.setPrefHeight(110);

        fmtHtml.setSelected(true);
        fmtMd.setSelected(true);
        HBox fmtRow = new HBox(8, new Label("导出格式:"), fmtHtml, fmtPdf, fmtDocx, fmtMd, spacer());
        fmtRow.setAlignment(Pos.CENTER_LEFT);

        locBox.getItems().addAll("默认位置", "固定位置");
        locBox.getSelectionModel().selectFirst();
        locBox.setOnAction(e -> onLocChange());
        Button pickDir = new Button("📁");
        pickDir.setMinSize(30, 26);
        pickDir.setMaxSize(30, 26);
        pickDir.setTooltip(new javafx.scene.control.Tooltip("选择固定位置（文件夹）"));
        pickDir.setOnAction(e -> pickDir());
        locLabel.setStyle("-fx-text-fill: #888; -fx-font-size: 11px;");
        HBox locRow = new HBox(6, new Label("导出到:"), locBox, pickDir, locLabel);
        locRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(locLabel, Priority.ALWAYS);
        refreshLocLabel();

        progress.setPrefWidth(Double.MAX_VALUE);
        status.setStyle("-fx-text-fill: #888;");

        startBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white; "
                + "-fx-padding: 5 14 5 14; -fx-background-radius: 4;");
        startBtn.setOnAction(e -> start());
        cancelBtn.setStyle("-fx-background-color: #c62828; -fx-text-fill: white;");
        cancelBtn.setDisable(true);
        cancelBtn.setOnAction(e -> {
            cancel.set(true);
            status.setText("正在取消（完成当前文章后停止）…");
        });
        Button openBtn = new Button("📂 打开导出文件夹");
        openBtn.setOnAction(e -> AudioEngine.openInExplorer(outDir()));
        HBox btnRow = new HBox(6, startBtn, cancelBtn, openBtn, spacer());
        btnRow.setAlignment(Pos.CENTER_LEFT);

        log.setEditable(false);
        log.setPromptText("解析日志");

        VBox root = new VBox(6, titleRow, input, fmtRow, locRow, progress, status, btnRow, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(10));
        VBox.setVgrow(log, Priority.ALWAYS);
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
        a.setTitle("公众号文章解析 · 功能介绍");
        a.setHeaderText("📰 批量抓取公众号正文与图片");
        a.setContentText("""
                · 链接：每行一个 mp.weixin.qq.com 链接（自动提取去重）
                · 导出格式：HTML（图片本地化）/ 图片文本(MD) 已实现；
                  PDF / Word 暂未实现（勾选会提示）
                · 导出位置：默认位置 = 统一导出目录/公众号；固定位置 = 自选文件夹
                · 批量时串行请求，避免触发微信风控（操作频繁/环境异常）

                提示：图片会下载到 `{标题}_files/` 并替换 HTML/MD 里的引用
                """);
        a.showAndWait();
    }

    private void onLocChange() {
        if (locBox.getSelectionModel().getSelectedIndex() == 1 && customDir == null) pickDir();
        refreshLocLabel();
    }

    private void pickDir() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择固定导出位置");
        File d = c.showDialog(log.getScene() == null ? null : log.getScene().getWindow());
        if (d != null) {
            customDir = d.toPath();
            locBox.getSelectionModel().select(1);
            refreshLocLabel();
        }
    }

    private void refreshLocLabel() {
        Path dir = outDir();
        locLabel.setText(dir == null ? "（未选择固定位置）" : dir.toString());
    }

    private Path outDir() {
        if (locBox.getSelectionModel().getSelectedIndex() == 1) return customDir;
        return Settings.load().effectiveExportDir().resolve("公众号");
    }

    private void appendLog(String s) {
        Platform.runLater(() -> {
            log.appendText(s + "\n");
            log.setScrollTop(Double.MAX_VALUE);
        });
    }

    private void start() {
        if (running.get()) return;
        List<String> urls = WechatArticleParser.extractUrls(input.getText());
        if (urls.isEmpty()) {
            status.setText("未识别到公众号链接（需 mp.weixin.qq.com）");
            return;
        }
        if (!fmtHtml.isSelected() && !fmtMd.isSelected()) {
            status.setText("HTML / MD 至少勾选一种（PDF/Word 未实现）");
            return;
        }
        if (fmtPdf.isSelected() || fmtDocx.isSelected()) {
            appendLog("ℹ️ PDF / Word 导出未实现，已跳过（仅导出 HTML / MD）");
        }
        Path dir = outDir();
        if (dir == null) {
            status.setText("请先选择固定导出位置");
            return;
        }
        boolean doHtml = fmtHtml.isSelected();
        boolean doMd = fmtMd.isSelected();

        running.set(true);
        cancel.set(false);
        startBtn.setDisable(true);
        cancelBtn.setDisable(false);
        progress.setProgress(0);
        log.clear();
        appendLog("📰 开始解析 " + urls.size() + " 篇 → " + dir);

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

                        Map<String, String> local = new LinkedHashMap<>();
                        Path imgDir = dir.resolve(art.stem() + "_files");
                        for (int k = 0; k < art.images().size(); k++) {
                            if (cancel.get()) break;
                            String imgUrl = art.images().get(k);
                            String name = (k + 1) + WechatArticleParser.imageExt(imgUrl);
                            var r = downloader.download(List.of(imgUrl), imgDir.resolve(name),
                                    Map.of("Referer", "https://mp.weixin.qq.com/",
                                            "User-Agent", "Mozilla/5.0"), p -> { }, cancel::get);
                            if (r.success()) local.put(imgUrl, art.stem() + "_files/" + name);
                        }
                        if (doHtml) appendLog("   ✅ " + WechatExporter.exportHtml(art, dir, local).getFileName());
                        if (doMd) appendLog("   ✅ " + WechatExporter.exportMarkdown(art, dir, local).getFileName());
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
