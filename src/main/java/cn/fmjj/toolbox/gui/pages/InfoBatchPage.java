package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.platform.LinkRouter;
import cn.fmjj.toolbox.engine.platform.MediaItem;
import cn.fmjj.toolbox.engine.platform.bili.BiliDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliParser;
import cn.fmjj.toolbox.engine.platform.weibo.WeiboParser;
import cn.fmjj.toolbox.engine.store.MediaStore;
import javafx.application.Platform;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.control.cell.CheckBoxTableCell;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 📋 批量导入（对齐 Python 版 info_batch.py 的控件树与两阶段流程）。
 *
 * <p>布局：标题(18px) → 输入框(固定高 150) → 按钮行(批量解析/取消/暂停/导入文件 | 伸展 |
 * 下载勾选(绿)/画质(110)/线程(64)) → 计数行(蓝字 + 全选/反选/清除) → 可勾选列表(伸展) →
 * 日志(固定高 110)。
 *
 * <p>流程与原版一致：**先批量解析**（只填标题与状态），**再下载勾选的条目**。
 */
public class InfoBatchPage extends BasePage {

    /** 一行任务。 */
    private static final class Task {
        final LinkRouter.Target target;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);
        String title = "";
        String statusText = "待解析";
        BiliParser.VideoInfo biliInfo;
        MediaItem weiboItem;

        Task(LinkRouter.Target target) { this.target = target; }
    }

    private final TextArea input = new TextArea();
    private final TableView<Task> table = new TableView<>();
    private final Label countLabel = new Label("已解析 0 条 · 勾选 0 条");
    private final TextArea log = new TextArea();
    private final Button parseBtn = new Button("批量解析");
    private final Button cancelBtn = new Button("❌");
    private final Button pauseBtn = new Button("⏸️");
    private final Button dlBtn = new Button("⬇️");
    private final ComboBox<String> qualityBox = new ComboBox<>(FXCollections.observableArrayList(
            "最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"));
    private final ComboBox<String> threadsBox = new ComboBox<>(FXCollections.observableArrayList(
            "1", "2", "4", "8", "16"));

    private final AtomicBoolean stopFlag = new AtomicBoolean(false);
    private final AtomicBoolean paused = new AtomicBoolean(false);

    private final BiliParser biliParser = new BiliParser();
    private final BiliDownloader biliDownloader = new BiliDownloader();
    private final WeiboParser weiboParser = new WeiboParser();
    private final FileDownloader fileDownloader = new FileDownloader();

    private Consumer<String> toast;

    @Override
    public String title() { return "📋 批量导入"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void appendLog(String s) {
        Platform.runLater(() -> {
            log.appendText(s + "\n");
            log.setScrollTop(Double.MAX_VALUE);
        });
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label(title());
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        input.setPromptText("每行一个 URL...");
        input.setPrefHeight(150);
        input.setMinHeight(150);
        input.setMaxHeight(150);

        parseBtn.setDefaultButton(true);
        parseBtn.setOnAction(e -> parseAll());
        cancelBtn.setMinSize(40, 30);
        cancelBtn.setMaxSize(40, 30);
        cancelBtn.setTooltip(new javafx.scene.control.Tooltip(
                "取消当前解析：未开始的 URL 直接跳过，正在解析的 URL 自然结束"));
        cancelBtn.setDisable(true);
        cancelBtn.setOnAction(e -> {
            stopFlag.set(true);
            paused.set(false);
            appendLog("⏹ 已请求取消（当前条目解析完后停止）");
        });
        pauseBtn.setMinSize(40, 30);
        pauseBtn.setMaxSize(40, 30);
        pauseBtn.setTooltip(new javafx.scene.control.Tooltip(
                "暂停后当前 URL 解析完即停住，点「继续」恢复"));
        pauseBtn.setDisable(true);
        pauseBtn.setOnAction(e -> {
            paused.set(!paused.get());
            pauseBtn.setText(paused.get() ? "▶️" : "⏸️");
            appendLog(paused.get() ? "⏸ 已暂停" : "▶️ 已继续");
        });
        Button importBtn = new Button("📁");
        importBtn.setMinSize(40, 30);
        importBtn.setMaxSize(40, 30);
        importBtn.setTooltip(new javafx.scene.control.Tooltip("从文件导入 URL 列表"));
        importBtn.setOnAction(e -> importFile());

        dlBtn.setMinSize(40, 30);
        dlBtn.setMaxSize(40, 30);
        dlBtn.setStyle("-fx-font-size: 15px; -fx-background-color: #2e7d32; -fx-text-fill: white;");
        dlBtn.setTooltip(new javafx.scene.control.Tooltip("下载勾选的条目（按所选画质）"));
        dlBtn.setDisable(true);
        dlBtn.setOnAction(e -> downloadChecked());

        qualityBox.getSelectionModel().selectFirst();
        qualityBox.setPrefWidth(110);
        threadsBox.getSelectionModel().select("4");
        threadsBox.setPrefWidth(64);

        HBox btnRow = new HBox(6, parseBtn, cancelBtn, pauseBtn, importBtn, spacer(),
                dlBtn, new Label("画质:"), qualityBox, new Label("线程:"), threadsBox);
        btnRow.setAlignment(Pos.CENTER_LEFT);

        countLabel.setStyle("-fx-text-fill: #1976d2;");
        Button selAll = smallBtn("☑️", "全选", () -> selectRows("all"));
        Button selInvert = smallBtn("🔄", "反选", () -> selectRows("invert"));
        Button selNone = smallBtn("❌", "清除选择", () -> selectRows("none"));
        HBox listHead = new HBox(4, countLabel, spacer(), selAll, selInvert, selNone);
        listHead.setAlignment(Pos.CENTER_LEFT);

        TableColumn<Task, Boolean> cCheck = new TableColumn<>("☑");
        cCheck.setCellValueFactory(d -> d.getValue().checked);
        cCheck.setCellFactory(CheckBoxTableCell.forTableColumn(cCheck));
        cCheck.setEditable(true);
        cCheck.setPrefWidth(44);
        TableColumn<Task, String> cPlat = new TableColumn<>("平台");
        cPlat.setCellValueFactory(d -> new SimpleStringProperty(
                LinkRouter.platformName(d.getValue().target.platform())));
        cPlat.setPrefWidth(80);
        TableColumn<Task, String> cTitle = new TableColumn<>("标题");
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().title));
        cTitle.setPrefWidth(420);
        TableColumn<Task, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().statusText));
        cStatus.setPrefWidth(240);
        table.getColumns().addAll(List.of(cCheck, cPlat, cTitle, cStatus));
        table.setEditable(true);
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.setPlaceholder(new Label("粘贴链接后点「批量解析」"));
        table.setOnMouseClicked(e -> {
            if (e.getClickCount() == 2) openRowUrl();
        });

        log.setEditable(false);
        log.setPrefRowCount(5);
        log.setMinHeight(110);
        log.setMaxHeight(110);
        log.setPromptText("解析 / 下载日志");

        VBox root = new VBox(8, pageTitle, input, btnRow, listHead, table, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        table.getItems().addListener((javafx.collections.ListChangeListener<Task>) c -> {
            while (c.next()) {
                for (Task t : c.getAddedSubList()) {
                    t.checked.addListener((o, a, b) -> updateCounts());
                }
            }
            updateCounts();
        });
        return root;
    }

    private static Button smallBtn(String text, String tip, Runnable action) {
        Button b = new Button(text);
        b.setMinSize(32, 24);
        b.setMaxSize(32, 24);
        b.setStyle("-fx-font-size: 12px;");
        b.setTooltip(new javafx.scene.control.Tooltip(tip));
        b.setOnAction(e -> action.run());
        return b;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private void updateCounts() {
        int total = table.getItems().size();
        long checked = table.getItems().stream().filter(t -> t.checked.get()).count();
        countLabel.setText("已解析 " + total + " 条 · 勾选 " + checked + " 条");
        dlBtn.setDisable(checked == 0);
    }

    private void selectRows(String mode) {
        for (Task t : table.getItems()) {
            t.checked.set(switch (mode) {
                case "all" -> true;
                case "none" -> false;
                default -> !t.checked.get();
            });
        }
        updateCounts();
    }

    private void importFile() {
        FileChooser c = new FileChooser();
        c.setTitle("选择 URL 列表文件");
        c.getExtensionFilters().addAll(
                new FileChooser.ExtensionFilter("文本文件", "*.txt"),
                new FileChooser.ExtensionFilter("所有文件", "*.*"));
        File f = c.showOpenDialog(input.getScene() == null ? null : input.getScene().getWindow());
        if (f == null) return;
        try {
            String text = Files.readString(f.toPath());
            input.setText(input.getText().isBlank() ? text : input.getText() + "\n" + text);
            appendLog("📁 已导入 " + f.getName());
        } catch (Exception e) {
            appendLog("❌ 导入失败：" + e.getMessage());
        }
    }

    private void openRowUrl() {
        Task t = table.getSelectionModel().getSelectedItem();
        if (t == null) return;
        String url = switch (t.target.platform()) {
            case "bilibili" -> "https://www.bilibili.com/video/" + t.target.id();
            case "weibo" -> "https://m.weibo.cn/detail/" + t.target.id();
            case "douyin" -> "https://www.douyin.com/video/" + t.target.id();
            case "kuaishou" -> "https://www.kuaishou.com/short-video/" + t.target.id();
            default -> "";
        };
        if (url.isEmpty()) return;
        try {
            java.awt.Desktop.getDesktop().browse(java.net.URI.create(url));
        } catch (Exception ignored) {
        }
    }

    // ==================== 阶段一：批量解析 ====================

    private void parseAll() {
        String text = input.getText();
        if (text == null || text.isBlank()) {
            appendLog("⚠️ 请先粘贴链接");
            return;
        }
        LinkRouter.Outcome outcome = LinkRouter.parseLines(text);
        List<Task> tasks = new ArrayList<>();
        for (LinkRouter.Target t : outcome.targets()) {
            Task task = new Task(t);
            if (!t.parsable()) task.statusText = "待支持（解析器未移植）";
            tasks.add(task);
        }
        table.setItems(FXCollections.observableArrayList(tasks));
        appendLog("识别到 " + tasks.size() + " 条"
                + (outcome.unparsed().isEmpty() ? "" : "，" + outcome.unparsed().size() + " 行无法识别"));

        stopFlag.set(false);
        paused.set(false);
        pauseBtn.setText("⏸️");
        parseBtn.setDisable(true);
        cancelBtn.setDisable(false);
        pauseBtn.setDisable(false);

        String biliCookie = CookieStore.get("bilibili");
        String weiboCookie = CookieStore.get("weibo");
        javafx.concurrent.Task<int[]> job = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0, skip = 0;
                for (Task t : tasks) {
                    while (paused.get() && !stopFlag.get()) {
                        try {
                            Thread.sleep(200);
                        } catch (InterruptedException e) {
                            return new int[] {ok, fail, skip};
                        }
                    }
                    if (stopFlag.get()) break;
                    if (!t.target.parsable()) {
                        t.statusText = "跳过（待支持）";
                        skip++;
                        refreshRow();
                        continue;
                    }
                    try {
                        t.statusText = "解析中…";
                        refreshRow();
                        if ("bilibili".equals(t.target.platform())) {
                            var info = biliParser.videoInfo(t.target.id(), biliCookie);
                            t.biliInfo = info;
                            t.title = info.title();
                            t.statusText = "✅ 已解析（" + (info.duration() / 60) + " 分 " + (info.duration() % 60) + " 秒）";
                            t.checked.set(true);
                            ok++;
                        } else if ("weibo".equals(t.target.platform())) {
                            MediaItem item = weiboParser.parse(t.target.id(), weiboCookie);
                            t.weiboItem = item;
                            t.title = item.title();
                            t.statusText = "✅ 已解析（" + item.streams().size() + " 个媒体）";
                            t.checked.set(!item.streams().isEmpty());
                            ok++;
                        } else {
                            t.statusText = "待支持";
                            skip++;
                        }
                    } catch (Exception e) {
                        t.statusText = "❌ " + e.getMessage();
                        fail++;
                    }
                    refreshRow();
                }
                return new int[] {ok, fail, skip};
            }
        };
        job.setOnSucceeded(e -> {
            int[] r = job.getValue();
            parseBtn.setDisable(false);
            cancelBtn.setDisable(true);
            pauseBtn.setDisable(true);
            appendLog("✅ 解析完成：成功 " + r[0] + "，失败 " + r[1] + "，跳过 " + r[2]);
            updateCounts();
            toast("批量解析完成：" + r[0] + " 条");
        });
        job.setOnFailed(e -> {
            parseBtn.setDisable(false);
            cancelBtn.setDisable(true);
            pauseBtn.setDisable(true);
            appendLog("❌ 解析异常：" + job.getException().getMessage());
        });
        new Thread(job, "batch-parse").start();
    }

    private void refreshRow() {
        Platform.runLater(table::refresh);
    }

    // ==================== 阶段二：下载勾选 ====================

    private void downloadChecked() {
        List<Task> tasks = table.getItems().stream().filter(t -> t.checked.get()).toList();
        if (tasks.isEmpty()) {
            appendLog("⚠️ 没有勾选的条目");
            return;
        }
        stopFlag.set(false);
        dlBtn.setDisable(true);
        parseBtn.setDisable(true);
        Path outRoot = Settings.load().effectiveExportDir();
        String biliCookie = CookieStore.get("bilibili");

        javafx.concurrent.Task<int[]> job = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (Task t : tasks) {
                    if (stopFlag.get()) break;
                    try {
                        if ("bilibili".equals(t.target.platform()) && t.biliInfo != null) {
                            t.statusText = "下载中…";
                            refreshRow();
                            var r = biliDownloader.download(t.biliInfo.bvid(), 0, biliCookie, outRoot,
                                    msg -> {}, p -> { }, stopFlag::get, threads());
                            if (r.success()) {
                                t.statusText = "✅ " + r.path().getFileName();
                                recordBili(t.biliInfo, r.path());
                                ok++;
                            } else {
                                t.statusText = "❌ " + r.error();
                                fail++;
                            }
                        } else if ("weibo".equals(t.target.platform()) && t.weiboItem != null) {
                            t.statusText = "下载中…";
                            refreshRow();
                            int got = downloadWeiboMedia(t.weiboItem, outRoot);
                            if (got > 0) {
                                recordWeibo(t.weiboItem, outRoot);
                                t.statusText = "✅ 已下载 " + got + " 个媒体";
                                ok++;
                            } else {
                                t.statusText = "❌ 下载失败";
                                fail++;
                            }
                        } else {
                            t.statusText = "跳过（未解析）";
                        }
                    } catch (Exception e) {
                        t.statusText = "❌ " + e.getMessage();
                        fail++;
                    }
                    refreshRow();
                }
                return new int[] {ok, fail};
            }
        };
        job.setOnSucceeded(e -> {
            int[] r = job.getValue();
            dlBtn.setDisable(false);
            parseBtn.setDisable(false);
            appendLog("✅ 下载完成：成功 " + r[0] + "，失败 " + r[1]);
            toast("批量下载完成：" + r[0] + " 成功 / " + r[1] + " 失败");
        });
        job.setOnFailed(e -> {
            dlBtn.setDisable(false);
            parseBtn.setDisable(false);
            appendLog("❌ 下载异常：" + job.getException().getMessage());
        });
        new Thread(job, "batch-download").start();
    }

    private int threads() {
        try {
            return Integer.parseInt(threadsBox.getValue());
        } catch (Exception e) {
            return 4;
        }
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
                    p -> { }, stopFlag::get);
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
