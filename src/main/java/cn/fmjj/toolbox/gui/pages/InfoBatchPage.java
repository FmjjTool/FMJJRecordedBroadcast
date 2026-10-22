package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.LinkRouter;
import cn.fmjj.toolbox.engine.platform.MediaItem;
import cn.fmjj.toolbox.engine.platform.bili.BiliDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliParser;
import cn.fmjj.toolbox.engine.platform.weibo.WeiboParser;
import cn.fmjj.toolbox.engine.store.MediaStore;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 📥 批量导入页：粘贴多行链接 → 识别平台 → 批量解析 → 批量下载。
 * 对应 Python 版 gui_qt/pages/info_batch.py 的核心链路。
 *
 * 当前可解析：B站、微博；抖音/快手/小红书会识别但标注「待支持」。
 */
public class InfoBatchPage extends BasePage {

    /** 一行任务。 */
    private static final class Task {
        final LinkRouter.Target target;
        String title = "";
        String statusText = "待解析";
        BiliParser.VideoInfo biliInfo;
        MediaItem weiboItem;

        Task(LinkRouter.Target target) { this.target = target; }
    }

    private final TextArea input = new TextArea();
    private final TableView<Task> table = new TableView<>();
    private final Label status = new Label("就绪");
    private final ProgressBar progress = new ProgressBar(0);
    private final Button parseBtn = new Button("🔍 批量解析");
    private final Button downloadBtn = new Button("⬇ 批量下载");
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);

    private final BiliParser biliParser = new BiliParser();
    private final BiliDownloader biliDownloader = new BiliDownloader();
    private final WeiboParser weiboParser = new WeiboParser();
    private final FileDownloader fileDownloader = new FileDownloader();

    private Consumer<String> toast;

    @Override
    public String title() { return "📥 批量导入"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        input.setPromptText("每行一个链接（B站 / 微博 / 抖音 / 快手 / 小红书），支持直接粘贴分享文本");
        input.setPrefRowCount(6);
        parseBtn.setDefaultButton(true);
        parseBtn.setOnAction(e -> detect());
        downloadBtn.setDisable(true);
        downloadBtn.setOnAction(e -> downloadAll());
        Button clear = new Button("🧹 清空");
        clear.setOnAction(e -> {
            input.clear();
            table.getItems().clear();
            progress.setProgress(0);
            status.setText("已清空");
            downloadBtn.setDisable(true);
        });
        progress.setPrefWidth(200);

        HBox actionRow = new HBox(10, parseBtn, downloadBtn, clear, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        TableColumn<Task, String> cPlatform = new TableColumn<>("平台");
        cPlatform.setCellValueFactory(d -> new SimpleStringProperty(
                LinkRouter.platformName(d.getValue().target.platform())));
        cPlatform.setPrefWidth(80);
        TableColumn<Task, String> cId = new TableColumn<>("ID");
        cId.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().target.id()));
        cId.setPrefWidth(200);
        TableColumn<Task, String> cTitle = new TableColumn<>("标题");
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().title));
        cTitle.setPrefWidth(360);
        TableColumn<Task, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().statusText));
        cStatus.setPrefWidth(220);
        table.getColumns().addAll(List.of(cPlatform, cId, cTitle, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("识别 → 解析 → 下载；B站取最高画质（DASH 合并），微博下载全部图片/视频。"
                + "抖音/快手/小红书解析待支持（会明确标注）");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, input, actionRow, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        return root;
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    /** 识别（Python 批量导入第一步）：不触网。 */
    private void detect() {
        String text = input.getText();
        if (text == null || text.isBlank()) {
            status.setText("请先粘贴链接");
            return;
        }
        LinkRouter.Outcome outcome = LinkRouter.parseLines(text);
        List<Task> tasks = new ArrayList<>();
        for (LinkRouter.Target t : outcome.targets()) {
            Task task = new Task(t);
            if (!t.parsable()) task.statusText = "待支持（解析器未移植）";
            tasks.add(task);
        }
        table.getItems().setAll(tasks);
        downloadBtn.setDisable(tasks.isEmpty());
        StringBuilder sb = new StringBuilder("识别到 " + tasks.size() + " 条");
        if (!outcome.unparsed().isEmpty()) {
            sb.append("，").append(outcome.unparsed().size()).append(" 行无法识别");
        }
        status.setText(sb.toString());
    }

    /** 批量解析（串行，逐行更新状态）。 */
    private void downloadAll() {
        List<Task> tasks = new ArrayList<>(table.getItems());
        if (tasks.isEmpty()) return;
        stopFlag.set(false);
        parseBtn.setDisable(true);
        downloadBtn.setDisable(true);
        progress.setProgress(0);

        Path outRoot = Settings.load().effectiveExportDir();
        String biliCookie = CookieStore.get("bilibili");
        String weiboCookie = CookieStore.get("weibo");

        javafx.concurrent.Task<int[]> job = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0, skip = 0;
                for (int i = 0; i < tasks.size(); i++) {
                    if (stopFlag.get()) break;
                    Task t = tasks.get(i);
                    int idx = i;
                    if (!t.target.parsable()) {
                        t.statusText = "跳过（待支持）";
                        skip++;
                        refreshRow(t);
                        continue;
                    }
                    try {
                        if ("bilibili".equals(t.target.platform())) {
                            t.statusText = "解析中…";
                            refreshRow(t);
                            BiliParser.VideoInfo info = biliParser.videoInfo(t.target.id(), biliCookie);
                            t.title = info.title();
                            t.biliInfo = info;
                            t.statusText = "下载中…";
                            refreshRow(t);
                            var r = biliDownloader.download(info.bvid(), 0, biliCookie, outRoot,
                                    msg -> {}, p -> {
                                        double base = (double) idx / tasks.size();
                                        double span = 1.0 / tasks.size();
                                        Platform.runLater(() -> progress.setProgress(base + span * p.ratio()));
                                    }, stopFlag::get, 4);
                            if (r.success()) {
                                t.statusText = "✅ " + r.path().getFileName();
                                recordBili(info, r.path());
                                ok++;
                            } else {
                                t.statusText = "❌ " + r.error();
                                fail++;
                            }
                        } else if ("weibo".equals(t.target.platform())) {
                            t.statusText = "解析中…";
                            refreshRow(t);
                            MediaItem item = weiboParser.parse(t.target.id(), weiboCookie);
                            t.title = item.title();
                            t.weiboItem = item;
                            t.statusText = "下载中…";
                            refreshRow(t);
                            int got = downloadWeiboMedia(item, outRoot);
                            if (got > 0) {
                                recordWeibo(item, outRoot);
                                t.statusText = "✅ 已下载 " + got + " 个媒体";
                                ok++;
                            } else {
                                t.statusText = item.streams().isEmpty() ? "— 无媒体（纯文字）" : "❌ 下载失败";
                                if (item.streams().isEmpty()) skip++;
                                else fail++;
                            }
                        }
                    } catch (Exception e) {
                        t.statusText = "❌ " + e.getMessage();
                        fail++;
                    }
                    refreshRow(t);
                    Platform.runLater(() -> progress.setProgress((double) (idx + 1) / tasks.size()));
                }
                return new int[] {ok, fail, skip};
            }
        };
        job.setOnSucceeded(e -> {
            int[] r = job.getValue();
            parseBtn.setDisable(false);
            downloadBtn.setDisable(false);
            progress.setProgress(1);
            status.setText("批量完成：成功 " + r[0] + "，失败 " + r[1] + "，跳过 " + r[2]);
            toast("批量下载完成：" + r[0] + " 成功 / " + r[1] + " 失败");
        });
        job.setOnFailed(e -> {
            parseBtn.setDisable(false);
            downloadBtn.setDisable(false);
            status.setText("批量任务异常：" + job.getException().getMessage());
        });
        new Thread(job, "batch-import").start();
    }

    private void refreshRow(Task t) {
        Platform.runLater(table::refresh);
    }

    private int downloadWeiboMedia(MediaItem item, Path outRoot) {
        Path outDir = outRoot.resolve("微博_媒体")
                .resolve(BiliParser.sanitizeFilename(item.author().isEmpty() ? "未知" : item.author(), 80, "未知"));
        int ok = 0;
        List<MediaItem.Stream> streams = item.streams();
        for (int i = 0; i < streams.size(); i++) {
            if (stopFlag.get()) break;
            MediaItem.Stream s = streams.get(i);
            String ext = "video".equals(s.kind()) ? ".mp4" : ".jpg";
            Path target = outDir.resolve(item.mediaId() + "_" + (i + 1) + ext);
            var r = fileDownloader.download(List.of(s.url()), target,
                    java.util.Map.of("Referer", "https://m.weibo.cn/",
                            "User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"),
                    p -> {}, stopFlag::get);
            if (r.success()) ok++;
        }
        return ok;
    }

    private void recordBili(BiliParser.VideoInfo info, Path saved) {
        try (MediaStore store = new MediaStore()) {
            store.upsert(MediaItem.of("bilibili", info.bvid(), info.title(), info.owner(),
                    info.cover(), info.publishText(), info.duration(), info.desc(), List.of(), "{}"),
                    saved == null ? "" : saved.toString());
        } catch (Exception ignored) {
        }
    }

    private void recordWeibo(MediaItem item, Path dir) {
        try (MediaStore store = new MediaStore()) {
            store.upsert(item, dir == null ? "" : dir.toString());
        } catch (Exception ignored) {
        }
    }
}
