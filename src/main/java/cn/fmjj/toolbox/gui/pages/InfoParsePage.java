package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.MediaItem;
import cn.fmjj.toolbox.engine.platform.bili.BiliDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliLink;
import cn.fmjj.toolbox.engine.platform.bili.BiliParser;
import cn.fmjj.toolbox.engine.net.HttpClient;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinLink;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinParser;
import cn.fmjj.toolbox.engine.platform.kuaishou.KuaishouParser;
import cn.fmjj.toolbox.engine.platform.weibo.WeiboParser;
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
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 🔍 单个解析页（多平台入口）。
 *
 * 已支持：B站视频（详情/画质/DASH 合并下载，可多线程分段）、微博（图文/视频媒体下载）。
 * 待支持：抖音（受 Argus 风控，需 M5 第三刀 playwright-java 浏览器上下文）、
 * 快手/小红书（签名/Cookie 依赖，M5 第三刀）。
 */
public class InfoParsePage extends BasePage {

    private enum Site { BILIBILI, WEIBO, DOUYIN, KUAISHOU, UNKNOWN }

    private final TextField linkField = new TextField();
    private final ComboBox<String> qualityBox = new ComboBox<>();
    private final ComboBox<String> threadsBox = new ComboBox<>(
            FXCollections.observableArrayList("单线程", "4 线程", "8 线程"));
    private final Label infoTitle = new Label("尚未解析");
    private final Label infoMeta = new Label("");
    private final TextArea log = new TextArea();
    private final ProgressBar progress = new ProgressBar(0);
    private final Label status = new Label("就绪");
    private final Button parseBtn = new Button("🔍 解析");
    private final Button downloadBtn = new Button("⬇ 下载");

    private final BiliParser biliParser = new BiliParser();
    private final BiliDownloader biliDownloader = new BiliDownloader();
    private final WeiboParser weiboParser = new WeiboParser();
    private final FileDownloader fileDownloader = new FileDownloader();
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);

    private Site site = Site.UNKNOWN;
    private BiliParser.VideoInfo videoInfo;
    private BiliParser.PlayInfo playInfo;
    private List<BiliParser.Quality> qualities = new ArrayList<>();
    private MediaItem weiboItem;
    private DouyinParser.DouyinVideo douyinVideo;
    private KuaishouParser.KuaishouVideo kuaishouVideo;
    private final KuaishouParser kuaishouParser = new KuaishouParser();
    private final DouyinParser douyinParser = new DouyinParser();

    private Consumer<String> toast;

    @Override
    public String title() { return "🔍 单个解析"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        linkField.setPromptText("粘贴链接或分享文本（B站 / 微博 / 抖音）");
        linkField.setOnAction(e -> parse());
        downloadBtn.setDisable(true);
        progress.setPrefWidth(200);
        log.setEditable(false);
        log.setPrefRowCount(8);
        threadsBox.getSelectionModel().select(1);

        infoTitle.setStyle("-fx-font-size: 15px; -fx-font-weight: bold;");
        infoTitle.setWrapText(true);
        infoMeta.setStyle("-fx-text-fill: #6b7280;");
        infoMeta.setWrapText(true);

        parseBtn.setOnAction(e -> parse());
        downloadBtn.setOnAction(e -> download());
        Button openDir = new Button("📁 打开导出目录");
        openDir.setOnAction(e -> AudioEngine.openInExplorer(Settings.load().effectiveExportDir()));

        HBox inputRow = new HBox(8, new Label("链接"), linkField, parseBtn);
        inputRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(linkField, Priority.ALWAYS);

        HBox actionRow = new HBox(10, new Label("画质"), qualityBox,
                new Label("下载线程"), threadsBox, downloadBtn, openDir, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("B站：详情 + 画质 + DASH 音视频合并（可多线程分段下载）；"
                + "微博：图文/视频媒体下载；抖音：走系统 Edge/Chrome 渲染（约 10-15 秒）；"
                + "解析结果自动入库（config/collected.db）");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

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

    private int threads() {
        int idx = threadsBox.getSelectionModel().getSelectedIndex();
        return idx == 0 ? 1 : idx == 1 ? 4 : 8;
    }

    // ==================== 解析 ====================

    private void parse() {
        String text = linkField.getText() == null ? "" : linkField.getText().trim();
        if (text.isEmpty()) {
            status.setText("请先粘贴链接");
            return;
        }
        log.clear();
        downloadBtn.setDisable(true);

        BiliLink.LinkInfo bili = BiliLink.parseText(text);
        if (bili != null) {
            if (bili.type() != BiliLink.Type.VIDEO) {
                status.setText("B站链接类型（" + bili.type().key + "）暂未支持，请用 video/BV 链接");
                return;
            }
            site = Site.BILIBILI;
            parseBili(bili.id());
            return;
        }

        String weiboId = WeiboParser.extractId(text);
        if (!weiboId.isEmpty()) {
            site = Site.WEIBO;
            parseWeibo(weiboId);
            return;
        }

        var douyin = DouyinLink.parseText(text);
        if (douyin != null) {
            site = Site.DOUYIN;
            parseDouyin(text, douyin);
            return;
        }

        String ksId = KuaishouParser.extractId(text);
        if (!ksId.isEmpty()) {
            site = Site.KUAISHOU;
            parseKuaishou(ksId);
            return;
        }

        status.setText("无法识别的链接（支持 B站 / 微博 / 抖音 / 快手）");
    }

    /** 抖音：短链先展开，再抓分享页 SSR 数据。 */
    private void parseDouyin(String rawText, DouyinLink.LinkInfo link) {
        parseBtn.setDisable(true);
        status.setText("解析中…");
        javafx.concurrent.Task<DouyinParser.DouyinVideo> task = new javafx.concurrent.Task<>() {
            @Override
            protected DouyinParser.DouyinVideo call() throws Exception {
                DouyinLink.LinkInfo resolved = link;
                if (link.shortLink()) {
                    resolved = DouyinLink.resolve(rawText, new HttpClient());
                    if (resolved == null) throw new IllegalStateException("短链展开失败（链接可能已失效）");
                }
                if (resolved.type() != DouyinLink.Type.VIDEO) {
                    throw new IllegalStateException("该链接类型（" + resolved.type()
                            + "）暂不支持，请用作品链接");
                }
                return douyinParser.parse(resolved.id());
            }
        };
        task.setOnSucceeded(e -> {
            parseBtn.setDisable(false);
            applyDouyin(task.getValue());
        });
        task.setOnFailed(e -> {
            parseBtn.setDisable(false);
            status.setText("解析失败：" + task.getException().getMessage());
        });
        new Thread(task, "douyin-parse").start();
    }

    private void applyDouyin(DouyinParser.DouyinVideo v) {
        this.douyinVideo = v;
        infoTitle.setText(v.title());
        var m = DouyinParser.summary(v);
        infoMeta.setText("作者：" + m.get("作者") + "　时长：" + m.get("时长")
                + "　点赞：" + m.get("点赞") + "　评论：" + m.get("评论") + "　" + m.get("类型"));
        qualityBox.setItems(FXCollections.observableArrayList(
                v.isImagePost() ? "图集（原图）" : "原画（无水印）"));
        qualityBox.getSelectionModel().select(0);
        qualityBox.setDisable(true);
        downloadBtn.setDisable(v.isImagePost() ? v.images().isEmpty() : v.videoUrl().isEmpty());
        appendLog("✅ 解析成功：" + m.get("类型") + "，作者 " + m.get("作者"));
    }

    private void parseBili(String id) {
        parseBtn.setDisable(true);
        status.setText("解析中…");
        String cookie = CookieStore.get("bilibili");
        javafx.concurrent.Task<Void> task = new javafx.concurrent.Task<>() {
            @Override
            protected Void call() throws Exception {
                BiliParser.VideoInfo info = biliParser.videoInfo(id, cookie);
                BiliParser.PlayInfo play = biliParser.playInfo(info.bvid(), info.cid(), cookie);
                Platform.runLater(() -> applyBili(info, play));
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

    private void applyBili(BiliParser.VideoInfo info, BiliParser.PlayInfo play) {
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
        qualityBox.setDisable(false);
        downloadBtn.setDisable(items.isEmpty());
        appendLog("✅ 解析成功：" + info.title() + "，可用画质 " + items.size() + " 个");
    }

    private void parseWeibo(String id) {
        parseBtn.setDisable(true);
        status.setText("解析中…");
        String cookie = CookieStore.get("weibo");
        javafx.concurrent.Task<MediaItem> task = new javafx.concurrent.Task<>() {
            @Override
            protected MediaItem call() throws Exception {
                return weiboParser.parse(id, cookie);
            }
        };
        task.setOnSucceeded(e -> {
            parseBtn.setDisable(false);
            applyWeibo(task.getValue());
        });
        task.setOnFailed(e -> {
            parseBtn.setDisable(false);
            status.setText("解析失败：" + task.getException().getMessage());
        });
        new Thread(task, "weibo-parse").start();
    }

    private void applyWeibo(MediaItem item) {
        this.weiboItem = item;
        qualityBox.setItems(FXCollections.observableArrayList("媒体（视频/图片）"));
        qualityBox.getSelectionModel().select(0);
        qualityBox.setDisable(true);

        long videos = item.streams().stream().filter(s -> "video".equals(s.kind())).count();
        long images = item.streams().stream().filter(s -> "image".equals(s.kind())).count();
        infoTitle.setText(item.title().isEmpty() ? "（无正文）" : item.title());
        infoMeta.setText("作者：" + item.author()
                + "　视频：" + videos + "　图片：" + images
                + "　发布：" + (item.publishTime().isEmpty() ? "—" : item.publishTime())
                + "　正文 " + item.description().length() + " 字");
        downloadBtn.setDisable(item.streams().isEmpty());
        appendLog("✅ 解析成功：视频 " + videos + " 个，图片 " + images + " 张");
        status.setText(item.streams().isEmpty() ? "该微博没有可下载媒体（纯文字）" : "解析完成");
    }

    private static String formatDuration(int seconds) {
        if (seconds <= 0) return "—";
        return String.format("%02d:%02d", seconds / 60, seconds % 60);
    }

    // ==================== 下载 ====================

    private void download() {
        if (site == Site.BILIBILI && videoInfo != null) downloadBili();
        else if (site == Site.WEIBO && weiboItem != null) downloadWeibo();
        else if (site == Site.DOUYIN && douyinVideo != null) downloadDouyin();
        else if (site == Site.KUAISHOU && kuaishouVideo != null) downloadKuaishou();
        else status.setText("请先解析");
    }

    /** 抖音：视频单文件 / 图集逐张。 */
    private void downloadDouyin() {
        DouyinParser.DouyinVideo v = douyinVideo;
        Path outDir = Settings.load().effectiveExportDir()
                .resolve("抖音_媒体")
                .resolve(BiliParser.sanitizeFilename(v.author().isBlank() ? "未知" : v.author(), 80, "未知"));
        stopFlag.set(false);
        downloadBtn.setDisable(true);
        progress.setProgress(0);
        status.setText("下载中…");

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                Map<String, String> headers = Map.of(
                        "Referer", "https://www.douyin.com/",
                        "User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                                + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1");
                if (v.isImagePost()) {
                    for (int i = 0; i < v.images().size(); i++) {
                        if (stopFlag.get()) break;
                        Path target = outDir.resolve(v.awemeId() + "_" + (i + 1) + ".jpg");
                        var r = fileDownloader.download(List.of(v.images().get(i)), target, headers,
                                p -> { }, stopFlag::get);
                        if (r.success()) {
                            ok++;
                            appendLog("✅ " + target.getFileName());
                        } else {
                            fail++;
                            appendLog("❌ " + target.getFileName() + "：" + r.error());
                        }
                    }
                } else {
                    Path target = outDir.resolve(v.awemeId() + ".mp4");
                    var r = fileDownloader.download(List.of(v.videoUrl()), target, headers,
                            p -> {
                                if (p.total() > 0) Platform.runLater(() -> progress.setProgress(p.ratio()));
                            }, stopFlag::get);
                    if (r.success()) {
                        ok++;
                        appendLog("🎉 " + target);
                    } else {
                        fail++;
                        appendLog("❌ " + r.error());
                    }
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            downloadBtn.setDisable(false);
            progress.setProgress(1);
            status.setText("下载完成：成功 " + r[0] + "，失败 " + r[1]);
            if (r[0] > 0) {
                recordDouyin(v, outDir.toString());
                toast("抖音媒体已下载：" + r[0] + " 个");
            }
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "douyin-download").start();
    }

    // ==================== 快手 ====================

    private void parseKuaishou(String photoId) {
        parseBtn.setDisable(true);
        status.setText("解析中…");
        javafx.concurrent.Task<KuaishouParser.KuaishouVideo> task = new javafx.concurrent.Task<>() {
            @Override
            protected KuaishouParser.KuaishouVideo call() throws Exception {
                return kuaishouParser.parse(photoId);
            }
        };
        task.setOnSucceeded(e -> {
            parseBtn.setDisable(false);
            applyKuaishou(task.getValue());
        });
        task.setOnFailed(e -> {
            parseBtn.setDisable(false);
            status.setText("解析失败：" + task.getException().getMessage());
        });
        new Thread(task, "kuaishou-parse").start();
    }

    private void applyKuaishou(KuaishouParser.KuaishouVideo v) {
        this.kuaishouVideo = v;
        infoTitle.setText(v.title());
        infoMeta.setText("作者：" + (v.author().isBlank() ? "—" : v.author())
                + "　时长：" + formatDuration(v.durationSeconds())
                + "　ID：" + v.photoId());
        qualityBox.setItems(FXCollections.observableArrayList("原画"));
        qualityBox.getSelectionModel().select(0);
        qualityBox.setDisable(true);
        downloadBtn.setDisable(v.videoUrl().isEmpty());
        appendLog("✅ 解析成功：" + v.title());
    }

    private void downloadKuaishou() {
        KuaishouParser.KuaishouVideo v = kuaishouVideo;
        Path outDir = Settings.load().effectiveExportDir()
                .resolve("快手_媒体")
                .resolve(BiliParser.sanitizeFilename(v.author().isBlank() ? "未知" : v.author(), 80, "未知"));
        Path target = outDir.resolve(BiliParser.sanitizeFilename(
                (v.author().isBlank() ? "" : v.author() + " - ") + v.title(), 100, v.photoId()) + ".mp4");
        stopFlag.set(false);
        downloadBtn.setDisable(true);
        progress.setProgress(0);
        status.setText("下载中…");

        javafx.concurrent.Task<FileDownloader.Result> task = new javafx.concurrent.Task<>() {
            @Override
            protected FileDownloader.Result call() {
                return fileDownloader.download(List.of(v.videoUrl()), target,
                        Map.of("Referer", "https://www.kuaishou.com/", "User-Agent", HttpClient.UA_CHROME),
                        p -> {
                            if (p.total() > 0) Platform.runLater(() -> progress.setProgress(p.ratio()));
                        }, stopFlag::get);
            }
        };
        task.setOnSucceeded(e -> {
            var r = task.getValue();
            downloadBtn.setDisable(false);
            progress.setProgress(r.success() ? 1 : 0);
            if (r.success()) {
                status.setText("下载完成：" + r.path().getFileName());
                appendLog("🎉 " + r.path());
                recordKuaishou(v, outDir.toString());
                toast("快手视频已下载：" + r.path().getFileName());
            } else {
                status.setText("下载失败：" + r.error());
                appendLog("❌ " + r.error());
            }
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "kuaishou-download").start();
    }

    private void recordKuaishou(KuaishouParser.KuaishouVideo v, String savedDir) {
        try (MediaStore store = new MediaStore()) {
            List<MediaItem.Stream> streams = new ArrayList<>();
            streams.add(new MediaItem.Stream("video", "原画", v.videoUrl(), List.of(), 0));
            MediaItem item = MediaItem.of("kuaishou", v.photoId(), v.title(), v.author(),
                    v.cover(), "", v.durationSeconds(), v.caption(), streams, v.rawJson());
            store.upsert(item, savedDir);
            appendLog("📦 已写入已获取库");
        } catch (Exception e) {
            appendLog("⚠️ 入库失败：" + e.getMessage());
        }
    }

    private void recordDouyin(DouyinParser.DouyinVideo v, String savedDir) {
        try (MediaStore store = new MediaStore()) {
            List<MediaItem.Stream> streams = new ArrayList<>();
            if (v.isImagePost()) {
                for (String u : v.images()) streams.add(new MediaItem.Stream("image", "原图", u, List.of(), 0));
            } else {
                streams.add(new MediaItem.Stream("video", "原画", v.videoUrl(), List.of(), 0));
            }
            MediaItem item = MediaItem.of("douyin", v.awemeId(), v.title(), v.author(),
                    v.cover(), "", v.durationSeconds(), v.desc(), streams, v.rawJson());
            store.upsert(item, savedDir);
            appendLog("📦 已写入已获取库");
        } catch (Exception e) {
            appendLog("⚠️ 入库失败：" + e.getMessage());
        }
    }

    private void downloadBili() {
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
        int threads = threads();

        javafx.concurrent.Task<BiliDownloader.Outcome> task = new javafx.concurrent.Task<>() {
            @Override
            protected BiliDownloader.Outcome call() {
                return biliDownloader.download(info.bvid(), qn, cookie, outRoot,
                        msg -> appendLog(msg),
                        p -> Platform.runLater(() -> {
                            if (p.total() > 0) progress.setProgress(p.ratio());
                            status.setText("下载中… " + AudioEngine.formatSize(p.done())
                                    + (p.total() > 0 ? " / " + AudioEngine.formatSize(p.total()) : ""));
                        }),
                        stopFlag::get, threads);
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
                recordBili(info, r.path());
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

    private void downloadWeibo() {
        MediaItem item = weiboItem;
        Path outDir = Settings.load().effectiveExportDir()
                .resolve("微博_媒体")
                .resolve(BiliParser.sanitizeFilename(item.author().isEmpty() ? "未知" : item.author(), 80, "未知"));
        stopFlag.set(false);
        downloadBtn.setDisable(true);
        progress.setProgress(0);
        status.setText("下载媒体中…");

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                List<MediaItem.Stream> streams = item.streams();
                for (int i = 0; i < streams.size(); i++) {
                    if (stopFlag.get()) break;
                    MediaItem.Stream s = streams.get(i);
                    String ext = "video".equals(s.kind()) ? ".mp4" : ".jpg";
                    Path target = outDir.resolve(item.mediaId() + "_" + (i + 1) + ext);
                    var r = fileDownloader.download(List.of(s.url()), target,
                            java.util.Map.of("Referer", "https://m.weibo.cn/",
                                    "User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"),
                            p -> {
                                if (p.total() > 0) {
                                    Platform.runLater(() -> progress.setProgress(p.ratio()));
                                }
                            }, stopFlag::get);
                    if (r.success()) {
                        ok++;
                        appendLog("✅ " + target.getFileName() + "（" + AudioEngine.formatSize(r.bytes()) + "）");
                    } else {
                        fail++;
                        appendLog("❌ " + target.getFileName() + "：" + r.error());
                    }
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            downloadBtn.setDisable(false);
            progress.setProgress(1);
            status.setText("媒体下载完成：成功 " + r[0] + "，失败 " + r[1]);
            if (r[0] > 0) {
                recordWeibo(item, outDir.toString());
                toast("微博媒体已下载：" + r[0] + " 个");
            }
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "weibo-download").start();
    }

    // ==================== 入库 ====================

    private void recordBili(BiliParser.VideoInfo info, Path saved) {
        try (MediaStore store = new MediaStore()) {
            List<MediaItem.Stream> streams = new ArrayList<>();
            if (playInfo != null) {
                for (BiliParser.StreamInfo s : playInfo.video()) {
                    streams.add(new MediaItem.Stream("video", s.qualityText(),
                            s.urls().isEmpty() ? "" : s.urls().get(0),
                            s.urls().size() > 1 ? s.urls().subList(1, s.urls().size()) : List.of(), 0));
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

    private void recordWeibo(MediaItem item, String savedDir) {
        try (MediaStore store = new MediaStore()) {
            store.upsert(item, savedDir);
            appendLog("📦 已写入已获取库");
        } catch (Exception e) {
            appendLog("⚠️ 入库失败：" + e.getMessage());
        }
    }
}
