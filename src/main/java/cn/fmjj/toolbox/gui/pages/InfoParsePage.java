package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.MediaItem;
import cn.fmjj.toolbox.engine.platform.bili.BiliDownloader;
import cn.fmjj.toolbox.engine.platform.bili.BiliResource;
import cn.fmjj.toolbox.engine.platform.bili.BiliLink;
import cn.fmjj.toolbox.engine.platform.bili.BiliParser;
import cn.fmjj.toolbox.engine.net.HttpClient;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinLink;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinParser;
import cn.fmjj.toolbox.engine.platform.kuaishou.KuaishouParser;
import cn.fmjj.toolbox.engine.platform.xiaohongshu.XiaohongshuParser;
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
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
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

    private enum Site { BILIBILI, WEIBO, DOUYIN, KUAISHOU, XIAOHONGSHU, UNKNOWN }

    private final TextField linkField = new TextField();
    private final ComboBox<String> qualityBox = new ComboBox<>();
    private final ComboBox<String> threadsBox = new ComboBox<>(
            FXCollections.observableArrayList("单线程", "4 线程", "8 线程"));
    private final ComboBox<String> modeBox = new ComboBox<>(FXCollections.observableArrayList(
            "🔗 链接模式", "🔎 搜索模式"));
    private final ComboBox<String> searchPlatBox = new ComboBox<>(FXCollections.observableArrayList(
            "全部平台", "B站", "抖音", "微博"));
    private final TextArea resultArea = new TextArea();
    private String resultTitle = "尚未解析";
    private String resultMeta = "";
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
    private final BiliResource biliResource = new BiliResource();
    private final DouyinParser douyinParser = new DouyinParser();

    private Consumer<String> toast;

    @Override
    public String title() { return "🔍 单个解析"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        linkField.setPromptText("https://www.bilibili.com/video/BV...");
        linkField.setOnAction(e -> submit());
        downloadBtn.setDisable(true);
        log.setEditable(false);
        log.setPrefRowCount(5);
        log.setMinHeight(110);
        log.setMaxHeight(110);
        log.setPromptText("下载日志");
        threadsBox.getSelectionModel().select(1);
        qualityBox.getSelectionModel().selectFirst();
        qualityBox.setPrefWidth(110);
        threadsBox.setPrefWidth(64);

        resultArea.setEditable(false);
        resultArea.setPromptText("解析结果将显示在这里");
        resultArea.setWrapText(true);

        // 模式切换：链接模式 / 搜索模式（搜索模式才显示平台下拉）
        modeBox.setPrefWidth(104);
        modeBox.setTooltip(new javafx.scene.control.Tooltip(
                "链接模式：粘贴链接解析；搜索模式：输入关键词全局搜索视频"));
        modeBox.getSelectionModel().selectFirst();
        searchPlatBox.setPrefWidth(90);
        searchPlatBox.setTooltip(new javafx.scene.control.Tooltip("搜索的平台范围（全部=多平台并发）"));
        searchPlatBox.setVisible(false);
        searchPlatBox.setManaged(false);
        modeBox.setOnAction(e -> onModeChange());

        parseBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        parseBtn.setOnAction(e -> submit());
        downloadBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        downloadBtn.setTooltip(new javafx.scene.control.Tooltip(
                "下载最近一次解析成功的条目（B站按画质直下，其余走媒体下载）"));
        downloadBtn.setOnAction(e -> download());

        Label pageTitle = new Label(title());
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        HBox row = new HBox(8, modeBox, searchPlatBox, linkField, parseBtn);
        row.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(linkField, Priority.ALWAYS);

        HBox dlRow = new HBox(8, downloadBtn, spacer(),
                new Label("画质:"), qualityBox, new Label("线程:"), threadsBox, progress, status);
        dlRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        VBox root = new VBox(8, pageTitle, row, dlRow, resultArea, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(resultArea, Priority.ALWAYS);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private void onModeChange() {
        boolean search = modeBox.getSelectionModel().getSelectedIndex() == 1;
        searchPlatBox.setVisible(search);
        searchPlatBox.setManaged(search);
        linkField.setPromptText(search ? "输入关键词（如：封茗囧菌 翻唱）"
                : "https://www.bilibili.com/video/BV...");
        parseBtn.setText(search ? "🔎 搜索" : "🔍 解析");
    }

    /** 按钮/回车统一入口：按模式分流。 */
    private void submit() {
        if (modeBox.getSelectionModel().getSelectedIndex() == 1) searchVideos();
        else parse();
    }

    // ==================== 搜索模式 ====================

    /** 搜索模式：支持 B站（综合搜索）、微博（m.weibo.cn）与抖音（CDP 截获搜索）。 */
    private void searchVideos() {
        String kw = linkField.getText() == null ? "" : linkField.getText().trim();
        if (kw.isEmpty()) { status.setText("请输入搜索关键词"); return; }
        String plat = searchPlatBox.getValue();
        boolean all = plat == null || "全部平台".equals(plat);
        boolean bili = all || "B站".equals(plat);
        boolean weibo = all || "微博".equals(plat);
        if ("抖音".equals(plat)) {
            status.setText("抖音搜索触发验证码，暂不可用（可粘贴作品链接解析）");
            appendLog("ℹ️ 抖音搜索被验证码拦截，已跳过");
            return;
        }
        if (!bili && !weibo) {
            status.setText(plat + " 搜索暂未支持");
            return;
        }
        parseBtn.setDisable(true);
        setResultTitle("⏳ 搜索中：" + kw);
        setResultMeta("");

        final boolean doBili = bili;
        final boolean doWeibo = weibo;
        javafx.concurrent.Task<java.util.List<BiliResource.SearchItem>> task = new javafx.concurrent.Task<>() {
            @Override
            protected java.util.List<BiliResource.SearchItem> call() throws Exception {
                java.util.List<BiliResource.SearchItem> out = new ArrayList<>();
                if (doBili) out.addAll(biliResource.searchAll(kw, 1, CookieStore.get("bilibili")));
                if (doWeibo) {
                    for (WeiboParser.SearchHit hit : new WeiboParser().search(
                            kw, 1, CookieStore.get("weibo"))) {
                        out.add(new BiliResource.SearchItem("微博", hit.title(),
                                hit.author(), hit.url(), hit.cover(), hit.desc()));
                    }
                }
                return out;
            }
        };
        task.setOnSucceeded(e -> {
            parseBtn.setDisable(false);
            var items = task.getValue();
            setResultTitle("✅ 搜索「" + kw + "」获取 " + items.size() + " 条");
            setResultMeta("=".repeat(40) + "\n双击结果弹窗中的条目即可自动解析下载");
            status.setText("搜索完成：" + items.size() + " 条");
            if (!items.isEmpty()) showSearchResults(items);
        });
        task.setOnFailed(e -> {
            parseBtn.setDisable(false);
            setResultTitle("❌ 搜索失败：" + task.getException().getMessage());
            status.setText("搜索失败");
        });
        new Thread(task, "bili-search").start();
    }

    /** 搜索结果弹窗：7 列（平台/标题/作者/播放/时长/发布时间/链接），双击回填并解析。 */
    private void showSearchResults(java.util.List<BiliResource.SearchItem> items) {
        javafx.scene.control.Dialog<Void> dlg = new javafx.scene.control.Dialog<>();
        dlg.setTitle("搜索结果（双击条目解析）");
        dlg.setResizable(true);

        Label head = new Label("双击条目（或选中后点「解析选中」）→ 自动填入链接并解析");
        head.setStyle("-fx-text-fill: #666;");

        TableView<BiliResource.SearchItem> table = new TableView<>();
        String[] cols = {"平台", "标题", "作者", "播放", "时长", "发布时间", "链接"};
        for (int i = 0; i < cols.length; i++) {
            final int idx = i;
            TableColumn<BiliResource.SearchItem, String> c = new TableColumn<>(cols[i]);
            c.setCellValueFactory(d -> new javafx.beans.property.SimpleStringProperty(cell(d.getValue(), idx)));
            c.setPrefWidth(idx == 1 ? 320 : idx == 6 ? 280 : 100);
            table.getColumns().add(c);
        }
        table.setItems(FXCollections.observableArrayList(items));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.setPrefSize(980, 460);

        Button parseSel = new Button("🔍 解析选中");
        parseSel.setOnAction(e -> {
            var sel = table.getSelectionModel().getSelectedItem();
            if (sel == null) return;
            dlg.close();
            linkField.setText(sel.url());
            modeBox.getSelectionModel().selectFirst();
            onModeChange();
            parse();
        });
        Button close = new Button("关闭");
        close.setOnAction(e -> dlg.close());
        HBox btns = new HBox(8, parseSel, spacer(), close);

        table.setOnMouseClicked(e -> {
            if (e.getClickCount() == 2) {
                var sel = table.getSelectionModel().getSelectedItem();
                if (sel == null) return;
                dlg.close();
                linkField.setText(sel.url());
                modeBox.getSelectionModel().selectFirst();
                onModeChange();
                parse();
            }
        });

        VBox box = new VBox(8, head, table, btns);
        box.setPadding(new Insets(12));
        dlg.getDialogPane().setContent(box);
        dlg.getDialogPane().getButtonTypes().add(javafx.scene.control.ButtonType.CLOSE);
        dlg.showAndWait();
    }

    private static String cell(BiliResource.SearchItem it, int idx) {
        return switch (idx) {
            case 0 -> it.type() != null && it.type().startsWith("微博") ? "微博"
                    : it.type() != null && it.type().startsWith("抖音") ? "抖音" : "B站";
            case 1 -> it.title() == null ? "" : (it.title().length() > 60 ? it.title().substring(0, 60) : it.title());
            case 2 -> it.author() == null ? "" : it.author();
            case 6 -> it.url() == null ? "" : it.url();
            default -> "—";
        };
    }

    private void setResultTitle(String t) {
        resultTitle = t == null ? "" : t;
        refreshResult();
    }

    private void setResultMeta(String m) {
        resultMeta = m == null ? "" : m;
        refreshResult();
    }

    private void refreshResult() {
        resultArea.setText(resultTitle + (resultMeta.isEmpty() ? "" : "\n" + resultMeta));
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

        String xhsId = XiaohongshuParser.extractNoteId(text);
        if (!xhsId.isEmpty()) {
            site = Site.XIAOHONGSHU;
            parseXhs(text, xhsId);
            return;
        }

        String ksId = KuaishouParser.extractId(text);
        if (!ksId.isEmpty()) {
            site = Site.KUAISHOU;
            parseKuaishou(ksId);
            return;
        }

        status.setText("无法识别的链接（支持 B站 / 微博 / 抖音 / 快手 / 小红书）");
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
        setResultTitle(v.title());
        var m = DouyinParser.summary(v);
        setResultMeta("作者：" + m.get("作者") + "　时长：" + m.get("时长")
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
        setResultTitle(info.title());
        setResultMeta("UP：" + info.owner()
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
        setResultTitle(item.title().isEmpty() ? "（无正文）" : item.title());
        setResultMeta("作者：" + item.author()
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
        else if (site == Site.XIAOHONGSHU && xhsMedia != null) downloadXhs();
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

    // ==================== 小红书 ====================

    /**
     * 小红书：用 CDP 打开笔记页（带登录 Cookie），读取 og:video / og:image 与正文大图。
     */
    private void parseXhs(String text, String noteId) {
        String token = XiaohongshuParser.extractXsecToken(text);
        this.xhsUrl = "https://www.xiaohongshu.com/explore/" + noteId
                + (token.isEmpty() ? "" : "?xsec_token=" + token + "&xsec_source=pc_share");
        this.xhsMedia = null;
        downloadBtn.setDisable(true);
        parseBtn.setDisable(true);
        setResultTitle("小红书笔记：" + noteId);
        setResultMeta("正在用浏览器读取笔记媒体…");
        status.setText("解析中…");
        String url = this.xhsUrl;
        javafx.concurrent.Task<XiaohongshuParser.NoteMedia> task = new javafx.concurrent.Task<>() {
            @Override
            protected XiaohongshuParser.NoteMedia call() {
                return new XiaohongshuParser().fetchNoteMedia(url, CookieStore.get("xiaohongshu"));
            }
        };
        task.setOnSucceeded(e -> {
            parseBtn.setDisable(false);
            XiaohongshuParser.NoteMedia media = task.getValue();
            if (media == null) {
                setResultMeta("未能读取笔记媒体（可能被安全校验拦截，或链接需要 xsec_token）");
                status.setText("小红书解析失败");
                appendLog("ℹ️ 小红书：笔记页未返回可下载媒体");
                return;
            }
            xhsMedia = media;
            setResultTitle(media.title().isBlank() ? "小红书笔记：" + noteId : media.title());
            setResultMeta("笔记：" + noteId
                    + "　视频：" + (media.videoUrl().isBlank() ? "无" : "有")
                    + "　图片：" + media.images().size() + " 张"
                    + (media.desc().isBlank() ? "" : "\n" + media.desc()));
            qualityBox.setItems(FXCollections.observableArrayList("原画"));
            qualityBox.getSelectionModel().select(0);
            qualityBox.setDisable(true);
            downloadBtn.setDisable(media.videoUrl().isBlank() && media.images().isEmpty());
            status.setText("解析成功：" + (media.title().isBlank() ? noteId : media.title()));
            appendLog("✅ 小红书媒体：视频 " + (media.videoUrl().isBlank() ? "0" : "1")
                    + " 个，图片 " + media.images().size() + " 张");
        });
        task.setOnFailed(e -> {
            parseBtn.setDisable(false);
            setResultMeta("解析失败：" + task.getException().getMessage());
            status.setText("解析失败");
        });
        new Thread(task, "xhs-media").start();
    }

    private String xhsUrl;
    private XiaohongshuParser.NoteMedia xhsMedia;

    private void downloadXhs() {
        XiaohongshuParser.NoteMedia media = xhsMedia;
        if (media == null) {
            status.setText("请先解析小红书笔记");
            return;
        }
        String noteId = XiaohongshuParser.extractNoteId(xhsUrl);
        String title = media.title().isBlank() ? noteId : media.title();
        Path outDir = Settings.load().effectiveExportDir()
                .resolve("小红书_媒体")
                .resolve(BiliParser.sanitizeFilename(title, 80, "小红书"));
        stopFlag.set(false);
        downloadBtn.setDisable(true);
        progress.setProgress(0);
        status.setText("下载中…");
        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                Map<String, String> headers = Map.of(
                        "Referer", "https://www.xiaohongshu.com/",
                        "User-Agent", HttpClient.UA_CHROME);
                if (!media.videoUrl().isBlank()) {
                    Path target = outDir.resolve(noteId + ".mp4");
                    var r = fileDownloader.download(List.of(media.videoUrl()), target, headers,
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
                for (int i = 0; i < media.images().size(); i++) {
                    if (stopFlag.get()) break;
                    String u = media.images().get(i);
                    String ext = u.toLowerCase(java.util.Locale.ROOT).contains("webp") ? ".webp" : ".jpg";
                    Path target = outDir.resolve(noteId + "_" + (i + 1) + ext);
                    var r = fileDownloader.download(List.of(u), target, headers,
                            p -> { }, stopFlag::get);
                    if (r.success()) {
                        ok++;
                        appendLog("✅ " + target.getFileName());
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
            status.setText("下载完成：成功 " + r[0] + "，失败 " + r[1]);
            if (r[0] > 0) {
                recordXhs(media, noteId, outDir.toString());
                toast("小红书媒体已下载：" + r[0] + " 个");
            }
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "xhs-download").start();
    }

    private void recordXhs(XiaohongshuParser.NoteMedia media, String noteId, String savedDir) {
        try (MediaStore store = new MediaStore()) {
            List<MediaItem.Stream> streams = new ArrayList<>();
            if (!media.videoUrl().isBlank()) {
                streams.add(new MediaItem.Stream("video", "原画", media.videoUrl(), List.of(), 0));
            }
            for (String u : media.images()) {
                streams.add(new MediaItem.Stream("image", "原图", u, List.of(), 0));
            }
            MediaItem item = MediaItem.of("xiaohongshu", noteId, media.title(), "", media.cover(),
                    "", 0, media.desc(), streams, "");
            store.upsert(item, savedDir);
            appendLog("📦 已写入已获取库");
        } catch (Exception e) {
            appendLog("⚠️ 入库失败：" + e.getMessage());
        }
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
        setResultTitle(v.title());
        setResultMeta("作者：" + (v.author().isBlank() ? "—" : v.author())
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
