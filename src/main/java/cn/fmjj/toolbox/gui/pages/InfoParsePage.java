package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.MediaItem;
import cn.fmjj.toolbox.engine.platform.bili.BiliDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliLink;
import cn.fmjj.toolbox.engine.platform.bili.BiliParser;
import cn.fmjj.toolbox.engine.store.MediaStore;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 🔍 单个解析页。移植自 Python 版 gui_qt/pages/info_parse.py 的 B站链路：
 * 粘贴链接/分享文本 → 解析详情 + 画质 → 下载（DASH 合并）→ 写入已获取库。
 *
 * 当前支持 B站视频；抖音/微博/快手/小红书随 M5 第二刀补（页面会明确提示）。
 */
public class InfoParsePage extends BasePage {

    private final TextField linkField = new TextField();
    private final ComboBox<String> qualityBox = new ComboBox<>();
    private final Label infoTitle = new Label("尚未解析");
    private final Label infoMeta = new Label("");
    private final TextArea log = new TextArea();
    private final ProgressBar progress = new ProgressBar(0);
    private final Label status = new Label("就绪");
    private final Button parseBtn = new Button("🔍 解析");
    private final Button downloadBtn = new Button("⬇ 下载");

    private final BiliParser parser = new BiliParser();
    private final BiliDownloader downloader = new BiliDownloader();
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);

    private BiliParser.VideoInfo videoInfo;
    private BiliParser.PlayInfo playInfo;
    private List<BiliParser.Quality> qualities = new ArrayList<>();

    private Consumer<String> toast;

    @Override
    public String title() { return "🔍 单个解析"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        linkField.setPromptText("粘贴 B站视频链接或分享文本（支持 BV/av/短链）");
        linkField.setOnAction(e -> parse());
        downloadBtn.setDisable(true);
        progress.setPrefWidth(240);
        log.setEditable(false);
        log.setPrefRowCount(8);

        infoTitle.setStyle("-fx-font-size: 15px; -fx-font-weight: bold;");
        infoTitle.setWrapText(true);
        infoMeta.setStyle("-fx-text-fill: #6b7280;");

        parseBtn.setOnAction(e -> parse());
        downloadBtn.setOnAction(e -> download());
        Button openDir = new Button("📁 打开导出目录");
        openDir.setOnAction(e -> AudioEngine.openInExplorer(Settings.load().effectiveExportDir()));

        HBox inputRow = new HBox(8, new Label("链接"), linkField, parseBtn);
        inputRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(linkField, Priority.ALWAYS);

        HBox actionRow = new HBox(10, new Label("画质"), qualityBox, downloadBtn, openDir, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("B站：解析详情 + 画质选择 + DASH 音视频合并下载；解析结果自动入库（config/collected.db）");
        hint.getStyleClass().add("stub-desc");

        VBox infoCard = new VBox(6, infoTitle, infoMeta);
        infoCard.getStyleClass().add("page-root");
        infoCard.setPadding(new Insets(12));

        VBox root = new VBox(10, pageTitle, hint, inputRow, infoCard, actionRow, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(log, Priority.ALWAYS);
        return root;
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void appendLog(String text) {
        Platform.runLater(() -> {
            log.appendText(text + "\n");
            log.setScrollTop(Double.MAX_VALUE);
        });
    }

    private void parse() {
        String text = linkField.getText() == null ? "" : linkField.getText().trim();
        if (text.isEmpty()) {
            status.setText("请先粘贴链接");
            return;
        }
        BiliLink.LinkInfo link = BiliLink.parseText(text);
        if (link == null) {
            status.setText("无法识别的链接（当前仅支持 B站）");
            return;
        }
        if (link.type() != BiliLink.Type.VIDEO) {
            status.setText("该链接类型（" + link.type().key + "）暂未支持，视频解析请用 video/BV 链接");
            return;
        }
        parseBtn.setDisable(true);
        status.setText("解析中…");
        log.clear();
        String cookie = CookieStore.get("bilibili");

        javafx.concurrent.Task<Void> task = new javafx.concurrent.Task<>() {
            @Override
            protected Void call() throws Exception {
                BiliParser.VideoInfo info = parser.videoInfo(link.id(), cookie);
                BiliParser.PlayInfo play = parser.playInfo(info.bvid(), info.cid(), cookie);
                Platform.runLater(() -> applyParsed(info, play));
                return null;
            }
        };
        task.setOnSucceeded(e -> {
            parseBtn.setDisable(false);
            status.setText("解析完成");
        });
        task.setOnFailed(e -> {
            parseBtn.setDisable(false);
            status.setText("解析失败：" + task.getException().getMessage());
        });
        new Thread(task, "bili-parse").start();
    }

    private void applyParsed(BiliParser.VideoInfo info, BiliParser.PlayInfo play) {
        this.videoInfo = info;
        this.playInfo = play;
        this.qualities = play.qualities();

        infoTitle.setText(info.title());
        infoMeta.setText("UP：" + info.owner()
                + "　时长：" + formatDuration(info.duration())
                + "　发布：" + (info.publishText().isEmpty() ? "—" : info.publishText())
                + "　BV：" + info.bvid());
        List<String> items = new ArrayList<>();
        for (BiliParser.Quality q : qualities) items.add(q.desc() + "（qn=" + q.qn() + "）");
        qualityBox.setItems(FXCollections.observableArrayList(items));
        if (!items.isEmpty()) qualityBox.getSelectionModel().select(0);
        downloadBtn.setDisable(items.isEmpty());
        appendLog("✅ 解析成功：" + info.title() + "，可用画质 " + items.size() + " 个");
    }

    private static String formatDuration(int seconds) {
        if (seconds <= 0) return "—";
        return String.format("%02d:%02d", seconds / 60, seconds % 60);
    }

    private void download() {
        if (videoInfo == null) {
            status.setText("请先解析");
            return;
        }
        int idx = qualityBox.getSelectionModel().getSelectedIndex();
        int qn = idx >= 0 && idx < qualities.size() ? qualities.get(idx).qn() : 0;
        stopFlag.set(false);
        downloadBtn.setDisable(true);
        parseBtn.setDisable(true);
        progress.setProgress(0);
        status.setText("下载中…");

        Path outRoot = Settings.load().effectiveExportDir();
        String cookie = CookieStore.get("bilibili");
        BiliParser.VideoInfo info = videoInfo;

        javafx.concurrent.Task<BiliDownloader.Outcome> task = new javafx.concurrent.Task<>() {
            @Override
            protected BiliDownloader.Outcome call() {
                return downloader.download(info.bvid(), qn, cookie, outRoot,
                        msg -> appendLog(msg),
                        p -> Platform.runLater(() -> {
                            if (p.total() > 0) progress.setProgress(p.ratio());
                            status.setText("下载中… " + AudioEngine.formatSize(p.done())
                                    + (p.total() > 0 ? " / " + AudioEngine.formatSize(p.total()) : ""));
                        }),
                        stopFlag::get);
            }
        };
        task.setOnSucceeded(e -> {
            BiliDownloader.Outcome r = task.getValue();
            downloadBtn.setDisable(false);
            parseBtn.setDisable(false);
            progress.setProgress(r.success() ? 1 : 0);
            if (r.success()) {
                status.setText("下载完成：" + r.path().getFileName());
                appendLog("🎉 " + r.path());
                recordToStore(info, r.path());
                toast("下载完成：" + r.path().getFileName());
            } else {
                status.setText("下载失败：" + r.error());
                appendLog("❌ " + r.error());
            }
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            parseBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "bili-download").start();
    }

    /** 解析结果入库（config/collected.db）。 */
    private void recordToStore(BiliParser.VideoInfo info, Path saved) {
        try (MediaStore store = new MediaStore()) {
            List<MediaItem.Stream> streams = new ArrayList<>();
            if (playInfo != null) {
                for (BiliParser.StreamInfo s : playInfo.video()) {
                    streams.add(new MediaItem.Stream("video", s.qualityText(),
                            s.urls().isEmpty() ? "" : s.urls().get(0),
                            s.urls().size() > 1 ? s.urls().subList(1, s.urls().size()) : List.of(),
                            0));
                }
            }
            MediaItem item = MediaItem.of("bilibili", info.bvid(), info.title(), info.owner(),
                    info.cover(), info.publishText(), info.duration(), info.desc(), streams, "{}");
            store.upsert(item, saved == null ? "" : saved.toString());
            appendLog("📦 已写入已获取库");
        } catch (Exception e) {
            appendLog("⚠️ 入库失败：" + e.getMessage());
        }
    }
}
