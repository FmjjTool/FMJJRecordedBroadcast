package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.JsonMapper;
import cn.fmjj.toolbox.core.Log;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.LiveVideoEngine;
import cn.fmjj.toolbox.engine.net.HttpClient;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinLive;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinLiveClient;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import javafx.animation.Animation;
import javafx.animation.KeyFrame;
import javafx.animation.PauseTransition;
import javafx.animation.Timeline;
import javafx.application.Platform;
import javafx.beans.property.ReadOnlyStringWrapper;
import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.event.ActionEvent;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.geometry.Rectangle2D;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.ButtonBar;
import javafx.scene.control.ButtonType;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Dialog;
import javafx.scene.control.Label;
import javafx.scene.control.ListCell;
import javafx.scene.control.ListView;
import javafx.scene.control.Separator;
import javafx.scene.control.Slider;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.Tooltip;
import javafx.scene.image.Image;
import javafx.scene.image.ImageView;
import javafx.scene.image.PixelFormat;
import javafx.scene.image.WritableImage;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.text.Text;
import javafx.scene.text.TextFlow;
import javafx.stage.FileChooser;
import javafx.util.Duration;

import java.io.BufferedWriter;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

/**
 * 抖音弹幕姬（对应 Python 版 gui_qt/pages/test_page.py 的抖音形态）。
 *
 * <p>三栏布局：左主播信息 / 中弹幕聊天（+视频占位）/ 右房间号 + WS + 打赏榜 + 事件。
 * 实时链路走 {@link DouyinLiveClient}（CDP 签名 + WebSocket + protobuf）。
 *
 * <p>本页已移植：真实弹幕/礼物/进场/点赞/关注、打赏榜连击会话结算、实时 JSONL 落盘、
 * 今日记录恢复、过滤/搜索、统计采集、回放包导出/合并、轮询等开播、
 * 直播预览（FFmpeg rawvideo → JavaFX）、FLV 录制、回放包视频播放。
 */
public class DouyinDanmakuPage extends BasePage {

    private static final String SETTINGS_FILE = "douyin_live.json";
    private static final String DEFAULT_ROOM = "582775732015";
    private static final String DEFAULT_REMARK = "抖音：歆慕\n微博：歆慕XINMU\nb站：歆慕XINMU\n使用信息请标明来源";

    private static final long FANSCLUB_PRICE_FALLBACK = 1;
    private static final Pattern FANSCLUB_NAME_RE = Pattern.compile(
            "粉丝团|灯牌|点亮|会员|守护|团票|续费|入团|团卡|粉丝牌|粉丝徽章|贵族|会员卡");
    private static final Pattern FANSCLUB_ACTION_RE = Pattern.compile("加入|开通|续费|点亮|加入粉丝团");
    private static final Pattern SHORT_LINK_RE = Pattern.compile(
            "https?://(?:v\\.douyin\\.com|b23\\.tv)/[0-9A-Za-z]+", Pattern.CASE_INSENSITIVE);

    private static final DateTimeFormatter TIME_MS = DateTimeFormatter.ofPattern("HH:mm:ss.SSS");
    private static final DateTimeFormatter CLOCK = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final DateTimeFormatter FILE_TS = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss");
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyyMMdd");

    private static final Set<String> RT_CHAT_METHODS = Set.of(
            "WebcastChatMessage", "WebcastEmojiChatMessage", "WebcastGiftMessage",
            "WebcastBindingGiftMessage", "DANMU_MSG", "DANMU_MSG_EMOJI", "SEND_GIFT",
            "SUPER_CHAT_MESSAGE", "GUARD_BUY");

    /** dycast castTypeMap 同款分类（method → 分类 TXT 文件名）。 */
    private static final Map<String, String> CAST_TXT_TYPES = new LinkedHashMap<>();

    static {
        CAST_TXT_TYPES.put("WebcastChatMessage", "弹幕");
        CAST_TXT_TYPES.put("WebcastGiftMessage", "礼物");
        CAST_TXT_TYPES.put("WebcastSocialMessage", "关注");
        CAST_TXT_TYPES.put("WebcastLikeMessage", "点赞");
        CAST_TXT_TYPES.put("WebcastMemberMessage", "进入");
    }

    private static final Object STATS_LOCK = new Object();

    private final ObjectMapper mapper = JsonMapper.get();
    private final HttpClient http = new HttpClient();
    private final Map<String, Object> settings;

    private Consumer<String> toast;

    // ==================== 状态 ====================
    private DouyinLiveClient client;
    private volatile String activeRoom = "";
    private volatile long likeCount;
    private volatile boolean saveStatsOn;
    private volatile byte[] coverBytes;

    private boolean connected;
    private boolean waitingLive;
    private boolean manualStop;
    private boolean resumeKeep;
    private boolean actionDisconnect;
    private boolean timerRunning;
    private long startTs;
    private String currentMode = "danmaku_only";
    private String chatFilterText = "";
    private String eventFilterText = "";
    private String lastBannerText = "";

    private final List<ChatEntry> chatLog = new ArrayList<>();
    private final List<String> eventLog = new ArrayList<>();
    private final List<ObjectNode> sessionCasts = Collections.synchronizedList(new ArrayList<>());
    private final Map<String, RankStat> giftRank = new HashMap<>();
    private final Map<String, ComboSession> giftSessions = new HashMap<>();
    private final ObservableList<ChatEntry> chatRows = FXCollections.observableArrayList();
    private final ObservableList<String> eventRows = FXCollections.observableArrayList();
    private final ObservableList<RankRow> rankRows = FXCollections.observableArrayList();

    private Timeline timer;
    private Timeline pollTicker;
    private PauseTransition chatDebounce;
    private PauseTransition eventDebounce;
    private StatsCollector statsCollector;
    private BufferedWriter rtWriter;
    private String rtRoom = "";

    // ==================== 控件 ====================
    private final TextField roomField = new TextField(DEFAULT_ROOM);
    private final TextField wsField = new TextField();
    private final Button actionBtn = new Button("连接");
    private final Label statusDot = new Label("●");
    private final Label statusLabel = new Label("已断开");
    private final Label timerLabel = new Label("00:00:00");
    private final Label likeLabel = new Label("0");
    private final ToggleButton pollBtn = new ToggleButton("🔁");
    private final Label pollCountdown = new Label("");
    private final ComboBox<ModeItem> modeCombo = new ComboBox<>();
    private final ComboBox<String> qualityCombo = new ComboBox<>();
    private final CheckBox saveStatsBox = new CheckBox("📊 保存统计");
    private final CheckBox autoRecordBox = new CheckBox("⏺ 自动回放包录制");
    private final CheckBox giftFilter = new CheckBox("礼物");
    private final CheckBox chatFilter = new CheckBox("聊天");
    private final CheckBox likeFilter = new CheckBox("点赞");
    private final CheckBox enterFilter = new CheckBox("进入直播间");
    private final CheckBox logFilter = new CheckBox("日志");
    private final TextField searchField = new TextField();
    private final TextField eventSearch = new TextField();
    private final Label searchCount = new Label("");
    private final Label eventCount = new Label("");
    private final ListView<ChatEntry> chatList = new ListView<>(chatRows);
    private final ListView<String> eventList = new ListView<>(eventRows);
    private final TableView<RankRow> giftTable = new TableView<>(rankRows);

    private final Label liveTitle = new Label("未获取直播标题");
    private final Label anchorName = new Label("*****");
    private final Label anchorFans = new Label("*****");
    private final Label onlineCount = new Label("*****");
    private final Label totalViewers = new Label("*****");
    private final Label likeCountField = new Label("*****");
    private final Label nowLabel = new Label("--");
    private final ImageView coverView = new ImageView();
    private final Label coverHint = new Label("暂无封面");
    private final VBox remarkBox = new VBox();

    private final Label videoTitle = new Label("📺 直播预览（仅播放，不录制）");
    private final Label videoStatus = new Label("");
    private final Label videoPlaceholder = new Label();
    private final Label recTime = new Label("");
    private VBox videoCard;
    private final ImageView videoView = new ImageView();
    private WritableImage videoImage;
    private javafx.animation.Timeline videoRender;
    private LiveVideoEngine.Player videoPlayer;
    private LiveVideoEngine.Recorder recorder;
    private final Map<String, String> streamUrls = new LinkedHashMap<>();
    private String currentStream = "";
    private Path replayVideoPath;
    private javafx.animation.Timeline recTimer;
    private long recStartTs;
    private Button recordBtn;
    private Button replayPlayBtn;
    private Button replayPauseBtn;
    private HBox replayCtrlRow;
    private final Slider replaySlider = new Slider(0, 1, 0);
    private final Label replayTimeLabel = new Label("00:00 / 00:00");
    private final ComboBox<String> replaySpeedCombo = new ComboBox<>();
    private double replayDuration;
    private double replayPosition;
    private boolean replayPaused;
    private boolean replaySessionActive;
    private javafx.animation.Timeline replayProgressTimer;
    private boolean updatingReplaySlider;
    private List<JsonNode> replayCasts = new ArrayList<>();
    private int replayCastIdx;
    private long replayFirstMs;
    private long replayRecStartMs;
    private double replayBaseSec;
    private double replayOffsetSec;
    private long replayStartRealMs;
    private ObjectNode replayBundle;
    private byte[] replayCover;
    private LiveVideoEngine.Frame lastVideoFrame;
    private boolean fillingQuality;
    private boolean replayPlaying;
    private boolean exiting;
    private VBox chatCard;
    private HBox liveCtrlRow;

    public DouyinDanmakuPage() {
        this.settings = loadSettings();
    }

    @Override
    public String title() {
        return "🎬 抖音弹幕 · 实时数据接入";
    }

    @Override
    public void bindToast(Consumer<String> sink) {
        this.toast = sink;
    }

    /** 软件退出：断开连接、关闭实时落盘、停统计，并按设置自动保存回放包（同步写完）。 */
    @Override
    public void shutdown() {
        exiting = true;
        stopReplayProgress();
        if (recorder != null) stopRecording(true, true);
        stopVideoPlayer();
        stopRecTimer();
        if (connected) {
            disconnect();
        } else {
            timerRunning = false;
            stopTimer();
            stopPollTimer();
            stopStats();
            DouyinLiveClient c = client;
            client = null;
            if (c != null) c.stop();
            rtClose();
        }
        saveSettings();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    // ==================== 构建 ====================

    @Override
    protected Node build() {
        VBox root = new VBox(6);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(8));

        Label title = new Label(title());
        title.getStyleClass().add("page-title");
        title.setStyle("-fx-font-size:18px; -fx-font-weight:bold;");
        Button help = new Button("❓ 弹幕姬 · 功能介绍");
        help.setOnAction(e -> showHelp());
        Region titleSpacer = new Region();
        HBox.setHgrow(titleSpacer, Priority.ALWAYS);
        HBox titleRow = new HBox(8, title, titleSpacer, help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        Node top = buildTopBar();
        Node toolbar = buildToolbar();

        HBox body = new HBox(4);
        body.getChildren().addAll(buildAnchorCard(), buildCenterPanel(), buildRightPanel());
        body.setPrefHeight(620);
        HBox.setHgrow(body.getChildren().get(1), Priority.ALWAYS);

        Node bottom = buildBottomBar();

        root.getChildren().addAll(titleRow, top, toolbar, body, bottom);

        initControls();
        applyMode();
        refreshRemark();
        updateSearchCount();
        updateEventCount();
        updatePollStyle();
        return root;
    }

    private void showHelp() {
        Alert alert = new Alert(Alert.AlertType.INFORMATION);
        alert.setTitle("弹幕姬 · 功能介绍");
        alert.setHeaderText("抖音直播弹幕姬");
        alert.setContentText("""
                · 连接：输入房间号或直播间链接（留空自动填默认房间号），点「连接」开始；已连接时按钮变为「断开」
                · 实时数据：弹幕 / 礼物 / 进场 / 点赞 / 关注；打赏榜按连击会话增量结算
                · 🔁 轮询：未开播/下播时按间隔自动重试，开播后自动接入
                · 实时落盘：每条弹幕/礼物即写入「导出目录/弹幕文件/实时记录/房间_日期.jsonl」
                · 搜索：弹幕区支持关键字 / @昵称；事件区独立筛选
                · 保存与回放包：💾 保存弹幕、📦 导出 / 📂 导入 / 🧩 合并回放包
                · 视频：连接后自动拉直播流预览；⏺ 回放包录制（FLV，停止/断开自动打包）；
                  导入回放包后「▶ 播放回放」：弹幕按时间戳与视频同步，支持暂停/进度拖动/倍速
                """);
        alert.getDialogPane().setPrefWidth(560);
        alert.showAndWait();
    }

    private Node buildTopBar() {
        pollBtn.setTooltip(new Tooltip("""
                轮询 = 自动等待开播（点击本按钮开始/停止）：
                ① 未开播/已下播：按「轮询间隔」自动重试，主播开播后自动连上；
                ② 直播中不轮询（连接一直保持到下播，不打扰直播）；
                ③ 下播后若本开关仍开着，自动恢复轮询等下次开播。"""));
        pollBtn.setPrefWidth(40);
        pollBtn.setOnAction(e -> onPollToggle());
        pollBtn.selectedProperty().addListener((o, a, b) -> updatePollStyle());
        pollCountdown.setStyle("-fx-text-fill:#e65100; -fx-font-size:11px;");
        pollCountdown.setMinWidth(42);

        Label modeLabel = new Label("模式:");
        modeCombo.getItems().addAll(
                new ModeItem("normal", "正常模式"),
                new ModeItem("video_only", "仅视频模式"),
                new ModeItem("danmaku_only", "仅弹幕模式"),
                new ModeItem("replay", "回放模式"));
        modeCombo.setPrefWidth(120);

        Button settingsBtn = iconButton("⚙", "打开本页详细设置（轮询间隔、统计、过滤等）", 36);
        settingsBtn.setOnAction(e -> openSettings());
        Button linkBtn = iconButton("🔗", "粘贴直播间链接，自动提取房间号并填入", 36);
        linkBtn.setOnAction(e -> openLinkPaste());
        Button accountBtn = new Button("📇 已存账号");
        accountBtn.setTooltip(new Tooltip("从本机已存账号/录播用户里选直播间"));
        accountBtn.setStyle("-fx-font-size:12px; -fx-padding:3 8 3 8;");
        accountBtn.setOnAction(e -> openRoomPicker());
        Button recordWinBtn = new Button("⏺ 录播");
        recordWinBtn.setTooltip(new Tooltip("开始/停止回放包录制（FLV + 弹幕打包）"));
        recordWinBtn.setStyle("-fx-font-size:12px; -fx-padding:3 8 3 8;");
        recordWinBtn.setOnAction(e -> onRecordToggle());
        Button clearBtn = iconButton("🗑", "清空界面临时内容（弹幕/打赏榜/统计/主播信息/封面/计时）", 36);
        clearBtn.setOnAction(e -> clearAll());

        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);

        HBox bar = new HBox(6, pollBtn, pollCountdown, modeLabel, modeCombo,
                settingsBtn, linkBtn, accountBtn, spacer, recordWinBtn, clearBtn);
        bar.getStyleClass().add("card");
        bar.setAlignment(Pos.CENTER_LEFT);
        bar.setPadding(new Insets(6, 10, 6, 10));
        return bar;
    }

    private Node buildToolbar() {
        Button save = new Button("💾 保存弹幕");
        save.setOnAction(e -> saveDanmakuNow());
        Button export = new Button("📦 导出回放包");
        export.setOnAction(e -> exportReplayZip(null, null, false, false));
        Button importBtn = new Button("📂 导入回放包");
        importBtn.setTooltip(new Tooltip("读取回放包并载入回放视频（支持弹幕与 FLV 录制文件）"));
        importBtn.setOnAction(e -> pickReplayZip());
        Button merge = new Button("🧩 合并回放包");
        merge.setTooltip(new Tooltip("选择多个回放包（断线重连产生的多段），按时间排序去重后合并成一个完整回放包"));
        merge.setOnAction(e -> mergeReplayZipsDialog());

        saveStatsBox.setSelected(settingBool("save_stats", true));
        saveStatsBox.setOnAction(e -> onSaveStatsToggle());

        autoRecordBox.setSelected(settingBool("auto_record", false));
        autoRecordBox.setTooltip(new Tooltip("勾选后拿到直播流即自动录制，停止/断开时自动打包回放包"));
        autoRecordBox.setOnAction(e -> {
            setSetting("auto_record", autoRecordBox.isSelected());
            saveSettings();
            if (autoRecordBox.isSelected() && connected && !currentStream.isEmpty()
                    && !isRecording()) {
                startRecording();
            }
        });

        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        Label cookieHint = new Label("Cookie 由侧栏「Cookie 管理」统一提供（自动套用）");
        cookieHint.getStyleClass().add("muted-label");

        HBox bar = new HBox(6, save, export, importBtn, merge, saveStatsBox, autoRecordBox,
                spacer, cookieHint);
        bar.getStyleClass().add("card");
        bar.setAlignment(Pos.CENTER_LEFT);
        bar.setPadding(new Insets(4, 10, 4, 10));
        return bar;
    }

    private Node buildAnchorCard() {
        VBox card = new VBox(6);
        card.getStyleClass().add("card");
        card.setPadding(new Insets(10));
        card.setPrefWidth(275);
        card.setMinWidth(258);
        card.setPrefHeight(600);

        Label head = new Label("📺 主播信息");
        head.setStyle("-fx-font-weight:bold;");
        HBox headRow = new HBox(head);
        headRow.setAlignment(Pos.CENTER_RIGHT);

        liveTitle.setWrapText(true);
        liveTitle.setMaxWidth(Double.MAX_VALUE);
        liveTitle.setAlignment(Pos.CENTER);
        liveTitle.setStyle("-fx-font-size:17px; -fx-font-weight:bold;");

        coverHint.getStyleClass().add("muted-label");
        coverView.setVisible(false);
        coverView.setFitWidth(96);
        coverView.setFitHeight(96);
        coverView.setPreserveRatio(true);
        StackPane coverBox = new StackPane(coverHint, coverView);
        coverBox.setPrefSize(96, 96);
        coverBox.setMinSize(96, 96);
        coverBox.setMaxSize(96, 96);
        coverBox.setStyle("-fx-background-color: rgba(128,128,128,0.10); -fx-background-radius:6;");
        HBox coverRow = new HBox(coverBox);
        coverRow.setAlignment(Pos.CENTER);

        Button saveCover = new Button("💾 保存封面/数据");
        saveCover.setOnAction(e -> saveCoverAndData());
        HBox saveRow = new HBox(saveCover);
        saveRow.setAlignment(Pos.CENTER);

        GridPane grid = new GridPane();
        grid.setHgap(8);
        grid.setVgap(4);
        String[][] fields = {
                {"主播:", ""}, {"主播粉丝数:", ""}, {"在线观众数:", ""},
                {"累计观看人数:", ""}, {"本场点赞数:", ""}};
        Label[] values = {anchorName, anchorFans, onlineCount, totalViewers, likeCountField};
        for (int i = 0; i < fields.length; i++) {
            Label key = new Label(fields[i][0]);
            key.setStyle("-fx-text-fill:#8a93a5;");
            Label val = values[i];
            val.setStyle("-fx-text-fill:#8a93a5;");
            grid.add(key, 0, i);
            grid.add(val, 1, i);
            GridPane.setHalignment(key, javafx.geometry.HPos.RIGHT);
        }
        HBox gridRow = new HBox(grid);
        gridRow.setAlignment(Pos.CENTER);

        Separator sep = new Separator();

        Label timeHead = new Label("UTC+8 北京时间");
        timeHead.setStyle("-fx-font-weight:bold;");
        timeHead.setAlignment(Pos.CENTER);
        timeHead.setMaxWidth(Double.MAX_VALUE);
        nowLabel.setStyle("-fx-text-fill:#8a93a5; -fx-font-size:11px;");
        nowLabel.setAlignment(Pos.CENTER);
        nowLabel.setMaxWidth(Double.MAX_VALUE);
        remarkBox.setAlignment(Pos.CENTER);

        Region spacer = new Region();
        VBox.setVgrow(spacer, Priority.ALWAYS);

        card.getChildren().addAll(headRow, liveTitle, coverRow, saveRow, gridRow, sep,
                timeHead, nowLabel, remarkBox, spacer);
        return card;
    }

    private Node buildCenterPanel() {
        VBox center = new VBox(4);
        center.setMinWidth(300);

        // ---- 视频卡片（FFmpeg 预览 / FLV 录制）----
        VBox vc = new VBox(6);
        vc.getStyleClass().add("card");
        vc.setPadding(new Insets(8, 10, 8, 10));
        vc.setPrefHeight(255);
        videoTitle.setStyle("-fx-font-weight:bold;");
        videoStatus.getStyleClass().add("muted-label");
        Region vSpacer = new Region();
        HBox.setHgrow(vSpacer, Priority.ALWAYS);
        HBox vHead = new HBox(6, videoTitle, vSpacer, videoStatus);
        vHead.setAlignment(Pos.CENTER_LEFT);

        videoPlaceholder.setText("直播视频预览：连接后自动拉流");
        videoPlaceholder.setMaxWidth(Double.MAX_VALUE);
        videoPlaceholder.setMaxHeight(Double.MAX_VALUE);
        videoPlaceholder.setAlignment(Pos.CENTER);
        videoPlaceholder.setWrapText(true);
        videoPlaceholder.setStyle("-fx-text-fill:#9aa0a6; -fx-font-size:13px;");
        videoView.setPreserveRatio(true);
        videoView.setFitWidth(640);
        videoView.setFitHeight(200);
        StackPane videoBox = new StackPane(videoView, videoPlaceholder);
        videoBox.setMinHeight(180);
        videoBox.setStyle("-fx-background-color:#000000; -fx-background-radius:4;");

        qualityCombo.getItems().add("等待连接…");
        qualityCombo.getSelectionModel().selectFirst();
        qualityCombo.setDisable(true);
        qualityCombo.setPrefWidth(110);
        qualityCombo.setOnAction(e -> onQualityChanged());

        recordBtn = new Button("⏺ 回放包录制");
        recordBtn.setStyle("-fx-background-color:#8e24aa; -fx-text-fill:white;");
        recordBtn.setTooltip(new Tooltip("录制当前直播流为 FLV，停止时自动打包回放包（同步保存弹幕）"));
        recordBtn.setOnAction(e -> onRecordToggle());
        replayPlayBtn = new Button("▶ 播放回放");
        replayPlayBtn.setStyle("-fx-background-color:#1976d2; -fx-text-fill:white;");
        replayPlayBtn.setOnAction(e -> onReplayToggle());
        replayPlayBtn.setVisible(false);
        replayPlayBtn.setManaged(false);
        recTime.setStyle("-fx-text-fill:#e91e63; -fx-font-size:11px;");
        liveCtrlRow = new HBox(6, new Label("清晰度:"), qualityCombo,
                recordBtn, replayPlayBtn, recTime);
        liveCtrlRow.setAlignment(Pos.CENTER_LEFT);
        Label q = (Label) liveCtrlRow.getChildren().get(0);
        q.setStyle("-fx-text-fill:#8a93a5;");

        replayPauseBtn = new Button("⏸ 暂停");
        replayPauseBtn.setDisable(true);
        replayPauseBtn.setOnAction(e -> onReplayPause());
        replaySlider.setPrefWidth(240);
        replaySlider.setBlockIncrement(5);
        replaySlider.valueProperty().addListener((o, a, b) -> {
            if (updatingReplaySlider || replayDuration <= 0) return;
            replayTimeLabel.setText(fmtClock(b.doubleValue() * replayDuration)
                    + " / " + fmtClock(replayDuration));
        });
        replaySlider.setOnMouseReleased(e -> onReplaySeek());
        replayTimeLabel.getStyleClass().add("muted-label");
        replayTimeLabel.setMinWidth(96);
        replaySpeedCombo.getItems().addAll("0.5x", "1x", "1.5x", "2x");
        replaySpeedCombo.getSelectionModel().select("1x");
        replaySpeedCombo.setPrefWidth(76);
        replaySpeedCombo.setDisable(true);
        replaySpeedCombo.setOnAction(e -> onReplaySpeedChanged());
        replayCtrlRow = new HBox(6, replayPauseBtn, replaySlider, replayTimeLabel,
                new Label("倍速"), replaySpeedCombo);
        replayCtrlRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(replaySlider, Priority.ALWAYS);
        setVisible(replayCtrlRow, false);

        vc.getChildren().addAll(vHead, videoBox, liveCtrlRow, replayCtrlRow);
        VBox.setVgrow(videoBox, Priority.ALWAYS);

        // ---- 弹幕卡片 ----
        VBox cc = new VBox(6);
        cc.getStyleClass().add("card");
        cc.setPadding(new Insets(10));
        cc.setPrefHeight(340);
        cc.setMinHeight(240);
        Label chatHead = new Label("💬 弹幕信息（聊天+礼物）");
        chatHead.setStyle("-fx-font-weight:bold;");
        giftFilter.setSelected(settingBool("filter_gift", true));
        chatFilter.setSelected(settingBool("filter_chat", true));
        giftFilter.setOnAction(e -> {
            setSetting("filter_gift", giftFilter.isSelected());
            saveSettings();
            applyChatFilter();
            updateSearchCount();
        });
        chatFilter.setOnAction(e -> {
            setSetting("filter_chat", chatFilter.isSelected());
            saveSettings();
            applyChatFilter();
            updateSearchCount();
        });
        Region cSpacer = new Region();
        HBox.setHgrow(cSpacer, Priority.ALWAYS);
        HBox cHead = new HBox(8, chatHead, cSpacer, giftFilter, chatFilter);
        cHead.setAlignment(Pos.CENTER_LEFT);

        searchField.setPromptText("🔍 搜索弹幕内容 / 昵称（点击昵称可筛选此人全部发言）");
        searchField.setMinWidth(120);
        HBox.setHgrow(searchField, Priority.ALWAYS);
        searchCount.getStyleClass().add("muted-label");
        HBox searchRow = new HBox(6, searchField, searchCount);
        searchRow.setAlignment(Pos.CENTER_LEFT);

        chatList.setPlaceholder(new Label("连接后实时显示弹幕 / 礼物 / 进场 / 点赞"));
        chatList.setCellFactory(lv -> new ChatCell());
        VBox.setVgrow(chatList, Priority.ALWAYS);

        cc.getChildren().addAll(cHead, searchRow, chatList);
        VBox.setVgrow(chatList, Priority.ALWAYS);

        center.getChildren().addAll(vc, cc);
        VBox.setVgrow(cc, Priority.ALWAYS);
        this.videoCard = vc;
        this.chatCard = cc;
        return center;
    }

    private Node buildRightPanel() {
        VBox card = new VBox(6);
        card.getStyleClass().add("card");
        card.setPadding(new Insets(10));
        card.setPrefWidth(395);
        card.setMinWidth(360);
        card.setPrefHeight(600);

        Label roomHead = new Label("房间号");
        roomHead.setStyle("-fx-font-weight:bold;");
        roomField.setPromptText(DEFAULT_ROOM);
        HBox.setHgrow(roomField, Priority.ALWAYS);
        roomField.setOnAction(e -> connect());
        Button pickRoom = iconButton("📇", "从本机已存账号 / 录播用户里选直播间", 28);
        pickRoom.setOnAction(e -> openRoomPicker());
        Button clearRoom = iconButton("✕", "清空房间号，快速输入新房间", 28);
        clearRoom.setStyle("-fx-text-fill:#8a919e;");
        clearRoom.setOnAction(e -> {
            roomField.clear();
            roomField.requestFocus();
        });
        actionBtn.setOnAction(e -> {
            if (actionDisconnect) disconnect();
            else connect();
        });
        setActionBtn("连接", false, true);
        HBox roomRow = new HBox(6, roomField, pickRoom, clearRoom, actionBtn);
        roomRow.setAlignment(Pos.CENTER_LEFT);

        Label wsHead = new Label("WS 地址（转发）");
        wsHead.setStyle("-fx-font-weight:bold;");
        wsField.setPromptText("请输入转发地址");
        HBox.setHgrow(wsField, Priority.ALWAYS);
        Button relay = new Button("转发");
        relay.setOnAction(e -> toast("WS 转发已启动（模拟）"));
        HBox wsRow = new HBox(6, wsField, relay);
        wsRow.setAlignment(Pos.CENTER_LEFT);

        Label rankHead = new Label("累计打赏榜");
        rankHead.setStyle("-fx-font-weight:bold;");

        TableColumn<RankRow, String> cRank = new TableColumn<>("排名");
        cRank.setCellValueFactory(d -> new ReadOnlyStringWrapper(d.getValue().rank()));
        cRank.setPrefWidth(44);
        TableColumn<RankRow, String> cUser = new TableColumn<>("昵称");
        cUser.setCellValueFactory(d -> new ReadOnlyStringWrapper(d.getValue().user()));
        cUser.setPrefWidth(140);
        TableColumn<RankRow, String> cCount = new TableColumn<>("礼物数");
        cCount.setCellValueFactory(d -> new ReadOnlyStringWrapper(d.getValue().giftCount()));
        cCount.setPrefWidth(64);
        TableColumn<RankRow, String> cValue = new TableColumn<>("价值(抖币)");
        cValue.setCellValueFactory(d -> new ReadOnlyStringWrapper(d.getValue().totalValue()));
        cValue.setPrefWidth(84);
        TableColumn<RankRow, String> cTime = new TableColumn<>("更新时间");
        cTime.setCellValueFactory(d -> new ReadOnlyStringWrapper(d.getValue().lastTime()));
        cTime.setPrefWidth(72);
        for (TableColumn<RankRow, String> col : List.of(cRank, cUser, cCount, cValue, cTime)) {
            col.setSortable(false);
        }
        giftTable.getColumns().addAll(cRank, cUser, cCount, cValue, cTime);
        giftTable.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        giftTable.setFixedCellSize(24);
        giftTable.setPrefHeight(175);
        giftTable.setMinHeight(120);
        giftTable.setPlaceholder(new Label("暂无打赏"));
        giftTable.setFocusTraversable(false);

        Label eventHead = new Label("弹幕信息（点赞+进入+日志）");
        eventHead.setStyle("-fx-font-weight:bold;");
        likeFilter.setSelected(settingBool("filter_like", true));
        enterFilter.setSelected(settingBool("filter_enter", true));
        logFilter.setSelected(settingBool("filter_log", true));
        likeFilter.setOnAction(e -> onEventFilterToggle("filter_like", likeFilter));
        enterFilter.setOnAction(e -> onEventFilterToggle("filter_enter", enterFilter));
        logFilter.setOnAction(e -> onEventFilterToggle("filter_log", logFilter));
        Region eSpacer = new Region();
        HBox.setHgrow(eSpacer, Priority.ALWAYS);
        HBox eHead = new HBox(6, eventHead, eSpacer, likeFilter, enterFilter, logFilter);
        eHead.setAlignment(Pos.CENTER_LEFT);

        eventSearch.setPromptText("🔍 搜索：点赞/进入/关注/横幅/日志");
        eventSearch.setMinWidth(100);
        HBox.setHgrow(eventSearch, Priority.ALWAYS);
        eventCount.getStyleClass().add("muted-label");
        HBox esRow = new HBox(6, eventSearch, eventCount);
        esRow.setAlignment(Pos.CENTER_LEFT);

        eventList.setPlaceholder(new Label("连接后显示点赞 / 进场 / 关注 / 横幅 / 日志"));
        VBox.setVgrow(eventList, Priority.ALWAYS);

        card.getChildren().addAll(roomHead, roomRow, wsHead, wsRow, rankHead, giftTable,
                eHead, esRow, eventList);
        VBox.setVgrow(eventList, Priority.ALWAYS);
        return card;
    }

    private Node buildBottomBar() {
        statusDot.setStyle("-fx-text-fill:#ff5252; -fx-font-size:14px;");
        statusLabel.setStyle("-fx-text-fill:#8a93a5;");
        timerLabel.setStyle("-fx-font-family:Consolas; -fx-font-weight:bold; -fx-font-size:13px;");
        likeLabel.setStyle("-fx-text-fill:#1976d2; -fx-font-weight:bold;");
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox bar = new HBox(6, statusDot, statusLabel, new Label("│"),
                new Label("⏱ 计时:"), timerLabel, new Label("│"),
                new Label("🛒 本场点赞:"), likeLabel, spacer);
        bar.getStyleClass().add("card");
        bar.setAlignment(Pos.CENTER_LEFT);
        bar.setPadding(new Insets(6, 10, 6, 10));
        return bar;
    }

    private void initControls() {
        pollBtn.setSelected(settingBool("poll_enabled", true));
        String mode = settingStr("mode", "danmaku_only");
        ModeItem selected = modeItem(mode);
        if (selected == null) selected = modeItem("danmaku_only");
        currentMode = selected == null ? "danmaku_only" : selected.key();
        modeCombo.setValue(selected);
        modeCombo.valueProperty().addListener((o, a, b) -> {
            if (b != null) onModeChanged(b.key());
        });

        String lastRoom = settingStr("last_room", DEFAULT_ROOM);
        if (!isValidRoom(lastRoom)) lastRoom = DEFAULT_ROOM;
        roomField.setText(lastRoom);
        saveStatsOn = saveStatsBox.isSelected();

        chatDebounce = new PauseTransition(Duration.millis(300));
        chatDebounce.setOnFinished(e -> applyChatFilter());
        searchField.textProperty().addListener((o, a, b) -> chatDebounce.playFromStart());

        eventDebounce = new PauseTransition(Duration.millis(300));
        eventDebounce.setOnFinished(e -> applyEventFilter());
        eventSearch.textProperty().addListener((o, a, b) -> eventDebounce.playFromStart());

        updatePollStyle();
    }

    private Button iconButton(String text, String tooltip, double width) {
        Button b = new Button(text);
        b.setTooltip(new Tooltip(tooltip));
        b.setPrefWidth(width);
        return b;
    }

    // ==================== 设置读写 ====================

    private static Map<String, Object> loadSettings() {
        Map<String, Object> defaults = new LinkedHashMap<>();
        defaults.put("stats_interval", 1);
        defaults.put("poll_interval", 60);
        defaults.put("save_danmaku", false);
        defaults.put("save_stats", true);
        defaults.put("keep_scroll", true);
        defaults.put("dark_mode", true);
        defaults.put("last_room", DEFAULT_ROOM);
        defaults.put("poll_enabled", true);
        defaults.put("mode", "danmaku_only");
        defaults.put("auto_record", false);
        defaults.put("auto_save_danmaku", true);
        defaults.put("filter_gift", true);
        defaults.put("filter_chat", true);
        defaults.put("filter_like", true);
        defaults.put("filter_enter", true);
        defaults.put("filter_log", true);
        defaults.put("remark_info", DEFAULT_REMARK);

        Map<String, Object> out = new LinkedHashMap<>(defaults);
        Path file = AppPaths.configDir().resolve(SETTINGS_FILE);
        if (Files.isRegularFile(file)) {
            try {
                ObjectMapper m = JsonMapper.get();
                Map<String, Object> data = m.readValue(file.toFile(),
                        new TypeReference<LinkedHashMap<String, Object>>() {});
                if (data != null) {
                    // 与 Python 相同的一次性迁移：旧版默认 save_stats=False 丢弃
                    if (!Boolean.TRUE.equals(data.get("_cfg_v1"))) {
                        data.remove("save_stats");
                        data.put("_cfg_v1", true);
                    }
                    data.remove("room_list");
                    out.putAll(data);
                }
            } catch (Exception e) {
                Log.warn("读取抖音弹幕设置失败：" + e.getMessage());
            }
        }
        return out;
    }

    private void saveSettings() {
        try {
            Path file = AppPaths.configDir().resolve(SETTINGS_FILE);
            Files.createDirectories(file.getParent());
            mapper.writerWithDefaultPrettyPrinter().writeValue(file.toFile(), settings);
        } catch (Exception e) {
            Log.warn("保存抖音弹幕设置失败：" + e.getMessage());
        }
    }

    private String settingStr(String key, String def) {
        Object v = settings.get(key);
        return v == null ? def : String.valueOf(v);
    }

    private int settingInt(String key, int def) {
        Object v = settings.get(key);
        if (v instanceof Number n) return n.intValue();
        try {
            return Integer.parseInt(String.valueOf(v).trim());
        } catch (Exception e) {
            return def;
        }
    }

    private boolean settingBool(String key, boolean def) {
        Object v = settings.get(key);
        if (v instanceof Boolean b) return b;
        if (v instanceof String s) return Boolean.parseBoolean(s);
        return def;
    }

    private void setSetting(String key, Object value) {
        settings.put(key, value);
    }

    private Path danmakuDir() {
        Path dir = Settings.load().effectiveExportDir().resolve("弹幕文件");
        try {
            Files.createDirectories(dir);
        } catch (Exception ignored) {
        }
        return dir;
    }

    private void refreshRemark() {
        remarkBox.getChildren().clear();
        String text = settingStr("remark_info", DEFAULT_REMARK);
        String[] lines = text.replace("\r\n", "\n").split("\n");
        List<String> kept = new ArrayList<>();
        for (String line : lines) {
            if (!line.strip().isEmpty()) kept.add(line.strip());
        }
        for (int i = 0; i < kept.size(); i++) {
            boolean last = i == kept.size() - 1;
            Label label = new Label(kept.get(i));
            label.setWrapText(true);
            label.setAlignment(Pos.CENTER);
            label.setStyle(last ? "-fx-text-fill:#666; -fx-font-size:10px;"
                    : "-fx-text-fill:#8a93a5; -fx-font-size:11px;");
            remarkBox.getChildren().add(label);
        }
    }

    // ==================== 模式 ====================

    private void onModeChanged(String mode) {
        if (mode == null || mode.equals(currentMode)) return;
        currentMode = mode;
        setSetting("mode", mode);
        saveSettings();
        applyMode();
    }

    private void applyMode() {
        boolean normal = "normal".equals(currentMode);
        boolean videoOnly = "video_only".equals(currentMode);
        boolean danmakuOnly = "danmaku_only".equals(currentMode);
        boolean replay = "replay".equals(currentMode);

        setVisible(chatCard, !videoOnly);
        setVisible(videoCard, normal || videoOnly || replay);
        setVisible(liveCtrlRow, normal || videoOnly || replay);
        if (replay) {
            stopLivePreview();
            if (isRecording()) stopRecording(true, false);
            videoTitle.setText("🎬 回放");
            qualityCombo.setDisable(true);
            setVisible(recordBtn, false);
            setVisible(replayPlayBtn, true);
            setVisible(replayCtrlRow, true);
            replayPauseBtn.setDisable(!replaySessionActive);
            replaySpeedCombo.setDisable(replayVideoPath == null && replayCasts.isEmpty());
            updateReplayUi();
            if (replayVideoPath == null && replayCasts.isEmpty()) {
                videoPlaceholder.setText("回放模式：请先「📂 导入回放包」载入视频或弹幕\n"
                        + "（回放包需包含 recording.flv/.mp4 或 data.json）");
                replayPlayBtn.setDisable(true);
            } else {
                if (!replaySessionActive) {
                    videoPlaceholder.setText(replayVideoPath == null
                            ? "已载入弹幕回放（无视频），点「▶ 播放回放」"
                            : "已载入回放视频，点「▶ 播放回放」");
                }
                replayPlayBtn.setDisable(false);
            }
        } else {
            if (replaySessionActive || replayPlaying) stopReplaySession();
            setVisible(replayCtrlRow, false);
            setVisible(recordBtn, true);
            setVisible(replayPlayBtn, false);
            videoTitle.setText("📺 直播预览（仅播放，不录制）");
            if (danmakuOnly) {
                stopLivePreview();
                videoPlaceholder.setText("仅弹幕模式：视频预览已关闭");
            } else if (connected && currentStream.isEmpty()) {
                videoPlaceholder.setText("正在获取直播流...");
            } else if (!connected) {
                videoPlaceholder.setText("直播视频预览：连接后自动拉流");
            }
            if ((normal || videoOnly) && connected && !currentStream.isEmpty()
                    && videoPlayer == null) {
                startLivePreview(currentStream);
            }
        }
    }

    private static void setVisible(Node node, boolean visible) {
        node.setVisible(visible);
        node.setManaged(visible);
    }

    private ModeItem modeItem(String key) {
        for (ModeItem item : modeCombo.getItems()) {
            if (item.key().equals(key)) return item;
        }
        return null;
    }

    private record ModeItem(String key, String label) {
        @Override
        public String toString() {
            return label;
        }
    }

    // ==================== 连接 / 断开 ====================

    private void connect() {
        String text = roomField.getText() == null ? "" : roomField.getText().trim();
        if (!text.isEmpty() && !text.matches("\\d+")) {
            String room = parseRoomLink(text);
            if (!room.isEmpty()) {
                if (!room.equals(text)) roomField.setText(room);
                doConnect();
                return;
            }
            if (SHORT_LINK_RE.matcher(text).find()) {
                setActionBtn("解析链接…", false, false);
                Thread t = new Thread(() -> {
                    String resolved = resolveRoomText(text);
                    Platform.runLater(() -> {
                        if (!resolved.isEmpty()) {
                            roomField.setText(resolved);
                            doConnect();
                        } else {
                            setActionBtn("连接", false, true);
                            toast("❌ 短链解析失败：请打开链接复制完整地址");
                        }
                    });
                }, "douyin-room-resolve");
                t.setDaemon(true);
                t.start();
                return;
            }
        }
        doConnect();
    }

    private void doConnect() {
        String room = roomField.getText() == null ? "" : roomField.getText().trim();
        if (room.isEmpty()) {
            room = defaultRoom();
            roomField.setText(room);
            toast("ℹ️ 未填房间号，已自动填入默认 " + room);
        }
        boolean resume = resumeKeep && !chatLog.isEmpty()
                && room.equals(settingStr("last_room", room));
        resumeKeep = false;
        manualStop = false;
        connected = true;
        activeRoom = room;
        stopVideoPlayer();
        if (recorder != null) stopRecording(true, false);
        streamUrls.clear();
        currentStream = "";
        fillingQuality = true;
        qualityCombo.getItems().setAll("等待连接…");
        qualityCombo.getSelectionModel().selectFirst();
        qualityCombo.setDisable(true);
        fillingQuality = false;
        videoStatus.setText("");
        videoPlaceholder.setText("正在获取直播流...");
        videoPlaceholder.setVisible(true);

        if (!resume) {
            startTs = System.currentTimeMillis();
            likeCount = 0;
            giftRank.clear();
            giftSessions.clear();
            synchronized (sessionCasts) {
                sessionCasts.clear();
            }
            rankRows.clear();
            giftingReset();
        }
        setSetting("last_room", room);
        saveSettings();
        rtOpen(room);

        if (resume) {
            applyChatFilter();
            updateSearchCount();
            eventRows.setAll(last500(eventLog));
            updateEventCount();
            refreshGiftRank();
            likeLabel.setText(String.format("%,d", likeCount));
        } else {
            chatLog.clear();
            eventLog.clear();
            chatRows.clear();
            eventRows.clear();
            searchCount.setText("");
            eventCount.setText("");
            lastBannerText = "";
            resetCover();
            for (Label f : List.of(anchorName, anchorFans, onlineCount, totalViewers)) {
                f.setText("…");
            }
            int restored = loadRtTail(room);
            if (restored > 0) {
                toast("ℹ️ 已恢复今日弹幕记录 " + restored + " 条");
            }
        }

        setActionBtn("连接中…", false, false);
        setStatus("连接中 · 房间 " + room, "#eab308");
        startTimer();

        DouyinLiveClient old = client;
        DouyinLiveClient c = new DouyinLiveClient();
        client = c;
        if (old != null) old.stop();

        c.startAsync(room, new DouyinLiveClient.Listener() {
            @Override
            public void onStatus(String state, String msg) {
                if (c != client) return;
                Platform.runLater(() -> applyStatus(state, msg));
            }

            @Override
            public void onRoomInfo(DouyinLive.RoomInfo info) {
                if (c != client) return;
                Platform.runLater(() -> applyRoomInfo(info));
            }

            @Override
            public void onMessage(DouyinLiveClient.Msg msg) {
                if (c != client) return;
                handleMessage(msg);
            }
        });

        if (saveStatsBox.isSelected()) startStats(room);
        setupPollTimer();
    }

    private void disconnect() {
        stopVideoOnEnd();
        connected = false;
        waitingLive = false;
        manualStop = true;
        timerRunning = false;
        stopTimer();
        rtClose();
        stopPollTimer();
        DouyinLiveClient c = client;
        client = null;
        if (c != null) c.stop();
        stopStats();
        likeLabel.setText("0");
        setActionBtn("连接", false, true);
        setStatus("已断开", "#8a93a5");
        statusDot.setStyle("-fx-text-fill:#ff5252; -fx-font-size:14px;");
        if (settingBool("auto_save_danmaku", true)) autoSaveDanmaku();
    }

    private String defaultRoom() {
        String room = settingStr("last_room", "");
        return isValidRoom(room) ? room : DEFAULT_ROOM;
    }

    private static boolean isValidRoom(String room) {
        return room != null && room.matches("\\d{6,}");
    }

    private static String parseRoomLink(String text) {
        String s = text == null ? "" : text.strip();
        if (s.isEmpty()) return "";
        Matcher m = Pattern.compile("live_web_rid=(\\d+)").matcher(s);
        if (m.find()) return m.group(1);
        m = Pattern.compile("live\\.bilibili\\.com/(?:h5/)?(\\d+)").matcher(s);
        if (m.find()) return m.group(1);
        m = Pattern.compile("(?:live\\.douyin\\.com/|douyin\\.com/live\\?)(\\d{6,})").matcher(s);
        if (m.find()) return m.group(1);
        m = Pattern.compile("anchor_id=(\\d+)").matcher(s);
        if (m.find()) return m.group(1);
        m = Pattern.compile("^\\s*(\\d{4,})\\s*$").matcher(s);
        if (m.matches()) return m.group(1);
        return "";
    }

    private String resolveRoomText(String text) {
        String room = parseRoomLink(text);
        if (!room.isEmpty()) return room;
        Matcher m = SHORT_LINK_RE.matcher(text == null ? "" : text);
        if (!m.find()) return "";
        try {
            var resp = http.get(m.group(0), Map.of("User-Agent", DouyinLive.UA));
            return parseRoomLink(resp.uri().toString());
        } catch (Exception e) {
            return "";
        }
    }

    // ==================== 视频预览 / 录制 / 回放 ====================

    private static final Map<String, Integer> QUALITY_ORDER = Map.ofEntries(
            Map.entry("_origin", 100), Map.entry("_md", 95),
            Map.entry("FULL_HD1", 90), Map.entry("UHD", 85), Map.entry("HD1", 80),
            Map.entry("SD1", 70), Map.entry("SD2", 60), Map.entry("LD", 50));

    private static final Map<String, String> QUALITY_NAMES = Map.ofEntries(
            Map.entry("_origin", "原画"), Map.entry("_md", "蓝光"),
            Map.entry("FULL_HD1", "1080P"), Map.entry("UHD", "超清"),
            Map.entry("HD1", "720P"), Map.entry("SD1", "540P"),
            Map.entry("SD2", "360P"), Map.entry("LD", "流畅"));

    private void loadLiveStreams() {
        if (activeRoom.isEmpty() || "danmaku_only".equals(currentMode)
                || "replay".equals(currentMode)) return;
        String room = activeRoom;
        videoStatus.setText("正在获取直播流...");
        Thread t = new Thread(() -> {
            Map<String, String> streams = new DouyinLive().streams(room);
            Platform.runLater(() -> {
                if (!room.equals(activeRoom)) return;
                applyStreams(streams);
            });
        }, "douyin-streams");
        t.setDaemon(true);
        t.start();
    }

    private void applyStreams(Map<String, String> streams) {
        fillingQuality = true;
        streamUrls.clear();
        qualityCombo.getItems().clear();
        if (streams == null || streams.isEmpty()) {
            qualityCombo.getItems().add("无可用流");
            qualityCombo.setDisable(true);
            fillingQuality = false;
            currentStream = "";
            videoStatus.setText("无可用直播流");
            videoPlaceholder.setText("无可用直播流（直播未开始或需要登录 Cookie）");
            videoPlaceholder.setVisible(true);
            return;
        }
        List<String> keys = new ArrayList<>(streams.keySet());
        keys.sort(Comparator.comparingInt((String k) -> QUALITY_ORDER.getOrDefault(k, 0))
                .reversed().thenComparing(k -> k));
        for (String key : keys) {
            String name = QUALITY_NAMES.get(key);
            String display = name == null ? key : name + "（" + key + "）";
            qualityCombo.getItems().add(display);
            streamUrls.put(display, streams.get(key));
        }
        qualityCombo.getSelectionModel().selectFirst();
        qualityCombo.setDisable(false);
        fillingQuality = false;
        currentStream = streamUrls.getOrDefault(
                qualityCombo.getSelectionModel().getSelectedItem(), "");
        if (!"danmaku_only".equals(currentMode) && !"replay".equals(currentMode)) {
            if (autoRecordBox.isSelected() && recorder == null) startRecording();
            startLivePreview(currentStream);
        }
    }

    private void onQualityChanged() {
        if (fillingQuality) return;
        String display = qualityCombo.getSelectionModel().getSelectedItem();
        String url = streamUrls.getOrDefault(display, "");
        if (url.isEmpty()) return;
        currentStream = url;
        if ("replay".equals(currentMode)) return;
        if (recorder != null) videoStatus.setText("录制中（切换清晰度仅影响预览）");
        startLivePreview(url);
    }

    private void startLivePreview(String url) {
        if (url == null || url.isEmpty() || "replay".equals(currentMode)) return;
        stopVideoPlayer();
        videoImage = new WritableImage(LiveVideoEngine.Player.WIDTH,
                LiveVideoEngine.Player.HEIGHT);
        videoView.setImage(videoImage);
        lastVideoFrame = null;
        videoPlaceholder.setText("正在获取直播流...");
        videoPlaceholder.setVisible(true);
        LiveVideoEngine.Player p = new LiveVideoEngine.Player();
        videoPlayer = p;
        boolean ok = p.start(url, true, (state, detail) -> Platform.runLater(() -> {
            if (videoPlayer != p) return;
            if ("error".equals(state)) {
                videoStatus.setText(detail);
                videoPlaceholder.setText(detail);
                videoPlaceholder.setVisible(true);
                stopVideoRender();
            } else if ("stopped".equals(state) && !"replay".equals(currentMode)) {
                videoStatus.setText("直播流中断");
                videoPlaceholder.setText("直播流中断 / 已下播");
                videoPlaceholder.setVisible(true);
                stopVideoRender();
            }
        }));
        if (!ok) {
            videoPlayer = null;
            return;
        }
        videoStatus.setText("直播预览中");
        startVideoRender();
    }

    private void stopVideoPlayer() {
        stopVideoRender();
        LiveVideoEngine.Player p = videoPlayer;
        videoPlayer = null;
        if (p != null) p.stop();
        lastVideoFrame = null;
        videoView.setImage(null);
        videoImage = null;
    }

    private void stopLivePreview() {
        stopVideoPlayer();
        if (!"replay".equals(currentMode)) {
            videoPlaceholder.setText(connected ? "直播视频预览：等待直播流"
                    : "直播视频预览：连接后自动拉流");
            videoPlaceholder.setVisible(true);
        }
    }

    private void stopVideoOnEnd() {
        stopLivePreview();
        if (recorder != null) stopRecording(true, false);
    }

    private void startVideoRender() {
        stopVideoRender();
        videoRender = new Timeline(new KeyFrame(Duration.millis(1000.0
                / LiveVideoEngine.Player.FPS), e -> {
            LiveVideoEngine.Player p = videoPlayer;
            if (p == null) return;
            LiveVideoEngine.Frame f = p.latest();
            if (f == null || f == lastVideoFrame) return;
            lastVideoFrame = f;
            if (videoImage == null || videoImage.getWidth() != f.width()
                    || videoImage.getHeight() != f.height()) {
                videoImage = new WritableImage(f.width(), f.height());
                videoView.setImage(videoImage);
            }
            videoImage.getPixelWriter().setPixels(0, 0, f.width(), f.height(),
                    PixelFormat.getByteRgbInstance(), f.rgb(), 0, f.width() * 3);
            videoPlaceholder.setVisible(false);
        }));
        videoRender.setCycleCount(javafx.animation.Animation.INDEFINITE);
        videoRender.play();
    }

    private void stopVideoRender() {
        if (videoRender != null) {
            videoRender.stop();
            videoRender = null;
        }
    }

    private boolean isRecording() {
        return recorder != null && recorder.isRunning();
    }

    private void onRecordToggle() {
        if (recorder != null) stopRecording(true, false);
        else startRecording();
    }

    private void startRecording() {
        if (recorder != null) return;
        if ("replay".equals(currentMode)) {
            toast("回放模式不能录制直播");
            return;
        }
        if (currentStream.isEmpty()) {
            toast("无可用直播流（需先连接并获取清晰度）");
            return;
        }
        Path dir = Settings.load().effectiveExportDir().resolve("直播录制");
        try {
            Files.createDirectories(dir);
        } catch (Exception ignored) {
        }
        String room = activeRoom.isEmpty() ? "room" : activeRoom;
        String ts = LocalDateTime.now().format(FILE_TS);
        Path out = dir.resolve(room + "_" + ts + ".flv");
        int n = 1;
        while (Files.exists(out)) {
            out = dir.resolve(room + "_" + ts + "_" + n + ".flv");
            n++;
        }
        LiveVideoEngine.Recorder r = new LiveVideoEngine.Recorder();
        if (!r.start(currentStream, out)) {
            toast("录制启动失败（未找到支持 https 的完整版 FFmpeg）");
            return;
        }
        recorder = r;
        recStartTs = r.startTs();
        videoStatus.setText("录制中：" + out.getFileName());
        updateRecordUi();
        stopRecTimer();
        recTimer = new Timeline(new KeyFrame(Duration.seconds(1), e -> tickRecord()));
        recTimer.setCycleCount(javafx.animation.Animation.INDEFINITE);
        recTimer.play();
        toast("⏺ 已开始录制：" + out.getFileName());
    }

    private void stopRecording(boolean packageIt, boolean sync) {
        LiveVideoEngine.Recorder r = recorder;
        recorder = null;
        stopRecTimer();
        Path path = r == null ? null : r.path();
        long started = r == null ? recStartTs : r.startTs();
        if (r != null) r.stop();
        updateRecordUi();
        if (path == null) return;
        long size = 0;
        try {
            size = Files.size(path);
        } catch (Exception ignored) {
        }
        if (packageIt && size > 1024) {
            ObjectNode meta = mapper.createObjectNode();
            meta.put("startTimestamp", started);
            meta.put("endTimestamp", System.currentTimeMillis());
            meta.put("file", "recording.flv");
            videoStatus.setText("录制已停止，正在打包回放包...");
            exportReplayZip(path, meta, true, sync);
        } else if (packageIt) {
            try {
                Files.deleteIfExists(path);
            } catch (Exception ignored) {
            }
            videoStatus.setText("录制已停止（文件过短已删除）");
        }
    }

    private void updateRecordUi() {
        if (recordBtn == null) return;
        boolean on = recorder != null;
        recordBtn.setText(on ? "⏹ 停止回放包录制" : "⏺ 回放包录制");
        recordBtn.setStyle("-fx-background-color:" + (on ? "#c62828" : "#8e24aa")
                + "; -fx-text-fill:white;");
        if (!on) recTime.setText("");
    }

    private void tickRecord() {
        LiveVideoEngine.Recorder r = recorder;
        if (r == null) return;
        if (!r.isRunning()) {
            stopRecording(true, false);
            return;
        }
        long secs = Math.max(0, (System.currentTimeMillis() - recStartTs) / 1000);
        recTime.setText(String.format("录制中 %02d:%02d:%02d",
                secs / 3600, secs % 3600 / 60, secs % 60));
    }

    private void stopRecTimer() {
        if (recTimer != null) {
            recTimer.stop();
            recTimer = null;
        }
    }

    private void onReplayToggle() {
        if (replaySessionActive) {
            stopReplaySession();
            return;
        }
        startReplayFresh();
    }

    private void startReplayFresh() {
        resetReplayPanel();
        double pos = Math.max(0, replayPosition);
        rebuildReplayTo(pos);
        startReplayAt(pos);
    }

    private void startReplayAt(double sec) {
        if ((replayVideoPath == null || !Files.isRegularFile(replayVideoPath))
                && replayCasts.isEmpty()) {
            toast("请先导入含视频或弹幕的回放包");
            return;
        }
        double start = replayDuration > 0
                ? Math.max(0, Math.min(sec, replayDuration)) : Math.max(0, sec);
        stopVideoPlayer();
        boolean hasVideo = replayVideoPath != null && Files.isRegularFile(replayVideoPath);
        LiveVideoEngine.Player p = null;
        if (hasVideo) {
            videoImage = new WritableImage(LiveVideoEngine.Player.WIDTH,
                    LiveVideoEngine.Player.HEIGHT);
            videoView.setImage(videoImage);
            lastVideoFrame = null;
            videoPlaceholder.setText("正在解码回放视频...");
            videoPlaceholder.setVisible(true);
            p = new LiveVideoEngine.Player();
            videoPlayer = p;
            final LiveVideoEngine.Player pp = p;
            boolean ok = p.start(replayVideoPath.toString(), false,
                    replayBaseSec + start, currentReplaySpeed(),
                    (state, detail) -> Platform.runLater(() -> {
                        if (videoPlayer != pp) return;
                        if ("error".equals(state)) {
                            videoStatus.setText(detail);
                            videoPlayer = null;
                            stopVideoRender();
                        } else if ("stopped".equals(state)) {
                            videoPlayer = null;
                            stopVideoRender();
                            videoStatus.setText("视频结束，弹幕继续回放");
                        }
                    }));
            if (!ok) {
                videoPlayer = null;
                p = null;
                videoView.setImage(null);
                videoImage = null;
            }
        } else {
            videoView.setImage(null);
            videoImage = null;
            videoPlaceholder.setText("回放中（仅弹幕）");
            videoPlaceholder.setVisible(true);
        }
        replayOffsetSec = start;
        replayStartRealMs = System.currentTimeMillis();
        replayPosition = start;
        replaySessionActive = true;
        replayPlaying = true;
        replayPaused = false;
        replayPlayBtn.setText("⏹ 停止回放");
        replayPauseBtn.setText("⏸ 暂停");
        replayPauseBtn.setDisable(false);
        replaySpeedCombo.setDisable(false);
        videoStatus.setText("回放播放中");
        updateReplayUi();
        if (p != null) startVideoRender();
        startReplaySync();
    }

    private void stopReplaySession() {
        stopReplayProgress();
        stopVideoRender();
        LiveVideoEngine.Player p = videoPlayer;
        videoPlayer = null;
        if (p != null) p.stop();
        replaySessionActive = false;
        replayPlaying = false;
        replayPaused = false;
        replayPosition = 0;
        replayOffsetSec = 0;
        replayCastIdx = 0;
        replayPlayBtn.setText("▶ 播放回放");
        if (replayPauseBtn != null) {
            replayPauseBtn.setText("⏸ 暂停");
            replayPauseBtn.setDisable(true);
        }
        videoView.setImage(null);
        videoImage = null;
        lastVideoFrame = null;
        videoPlaceholder.setText("已停止回放");
        videoPlaceholder.setVisible(true);
        updateReplayUi();
    }

    private void stopVideoPlayerKeepFrame() {
        stopVideoRender();
        LiveVideoEngine.Player p = videoPlayer;
        videoPlayer = null;
        if (p != null) p.stop();
    }

    private double replayClockNow() {
        if (replayPaused) return replayPosition;
        double pos = replayOffsetSec
                + (System.currentTimeMillis() - replayStartRealMs) / 1000.0 * currentReplaySpeed();
        return replayDuration > 0 ? Math.min(pos, replayDuration) : pos;
    }

    private void onReplayPause() {
        if (!replaySessionActive) return;
        if (replayPaused) {
            replayPosition = Math.max(0, replayPosition);
            startReplayAt(replayPosition);
            return;
        }
        replayPosition = replayClockNow();
        boolean hadVideo = videoPlayer != null;
        stopVideoPlayerKeepFrame();
        replayPaused = true;
        replayPlaying = false;
        stopReplayProgress();
        replayPauseBtn.setText("▶ 继续");
        videoStatus.setText("回放已暂停");
        if (!hadVideo) {
            videoPlaceholder.setText("回放已暂停（仅弹幕）");
            videoPlaceholder.setVisible(true);
        } else {
            videoPlaceholder.setVisible(false);
        }
        updateReplayUi();
    }

    private void onReplaySeek() {
        if (replayDuration <= 0) return;
        double pos = replaySlider.getValue() * replayDuration;
        boolean active = replaySessionActive;
        boolean paused = replayPaused;
        if (active && !paused) {
            stopReplayProgress();
            stopVideoPlayerKeepFrame();
        }
        resetReplayPanel();
        rebuildReplayTo(pos);
        replayPosition = pos;
        updateReplayUi();
        if (active && !paused) {
            startReplayAt(pos);
        } else if (active) {
            replayPaused = true;
            videoPlayer = null;
            videoPlaceholder.setText("已定位到 " + fmtClock(pos) + "，点「▶ 继续」");
            videoPlaceholder.setVisible(true);
        } else {
            videoPlaceholder.setText("已定位到 " + fmtClock(pos) + "，点「▶ 播放回放」");
            videoPlaceholder.setVisible(true);
        }
    }

    private void onReplaySpeedChanged() {
        double speed = currentReplaySpeed();
        if (!replaySessionActive || speed <= 0) return;
        if (!replayPaused) {
            replayPosition = replayClockNow();
            startReplayAt(replayPosition);
        }
    }

    private double currentReplaySpeed() {
        String v = replaySpeedCombo.getSelectionModel().getSelectedItem();
        if (v == null) return 1;
        try {
            return Double.parseDouble(v.replace("x", "").strip());
        } catch (Exception e) {
            return 1;
        }
    }

    private void startReplaySync() {
        stopReplayProgress();
        replayProgressTimer = new Timeline(new KeyFrame(Duration.millis(200), e -> {
            if (!replaySessionActive || replayPaused) return;
            replayPosition = replayClockNow();
            renderReplayCasts(replayPosition);
            updateReplayUi();
            if (replayDuration > 0 && replayPosition >= replayDuration - 0.05
                    && replayCastIdx >= replayCasts.size()) {
                finishReplayPlayback();
            }
        }));
        replayProgressTimer.setCycleCount(javafx.animation.Animation.INDEFINITE);
        replayProgressTimer.play();
    }

    private void finishReplayPlayback() {
        stopReplayProgress();
        stopVideoPlayerKeepFrame();
        replaySessionActive = false;
        replayPlaying = false;
        replayPaused = false;
        replayPlayBtn.setText("▶ 播放回放");
        replayPauseBtn.setText("⏸ 暂停");
        replayPauseBtn.setDisable(true);
        replayPosition = 0;
        if (replayDuration > 0) {
            updatingReplaySlider = true;
            replaySlider.setValue(1);
            replayTimeLabel.setText(fmtClock(replayDuration) + " / " + fmtClock(replayDuration));
            updatingReplaySlider = false;
        } else {
            updateReplayUi();
        }
        videoStatus.setText("回放结束");
        videoPlaceholder.setText("回放播放结束");
        videoPlaceholder.setVisible(true);
    }

    private void stopReplayProgress() {
        if (replayProgressTimer != null) {
            replayProgressTimer.stop();
            replayProgressTimer = null;
        }
    }

    private void updateReplayUi() {
        if (replaySlider == null) return;
        double dur = replayDuration;
        double pos = Math.max(0, dur > 0 ? Math.min(replayPosition, dur) : replayPosition);
        updatingReplaySlider = true;
        if (dur > 0) replaySlider.setValue(pos / dur);
        replayTimeLabel.setText(fmtClock(pos) + " / " + fmtClock(dur));
        updatingReplaySlider = false;
    }

    // ==================== 回放弹幕同步 ====================

    private void prepareReplayData(ReplayData data, double videoDuration) {
        replayBundle = data.bundle();
        replayCover = data.cover();
        replayCasts = new ArrayList<>();
        for (JsonNode c : data.casts()) {
            if (c.isObject() && c.path("timestamp").asLong(0) > 0) replayCasts.add(c);
        }
        replayCasts.sort(Comparator.comparingLong(c -> c.path("timestamp").asLong(0)));
        replayFirstMs = replayCasts.isEmpty() ? 0
                : replayCasts.get(0).path("timestamp").asLong(0);
        long lastMs = replayCasts.isEmpty() ? 0
                : replayCasts.get(replayCasts.size() - 1).path("timestamp").asLong(0);
        replayRecStartMs = data.recStartMs();
        long totalMs = Math.max(0, lastMs - replayFirstMs);
        replayBaseSec = replayRecStartMs > 0 && replayFirstMs > 0
                ? Math.max(0, (replayFirstMs - replayRecStartMs) / 1000.0) : 0;
        replayDuration = totalMs > 0 ? totalMs / 1000.0 : Math.max(0, videoDuration);
        replayCastIdx = 0;
        replayPosition = 0;
        resetReplayPanel();
        updateReplayUi();
    }

    private void resetReplayPanel() {
        clearAll();
        ObjectNode b = replayBundle;
        if (b != null) {
            String nick = b.path("nickname").asText("");
            String title = b.path("title").asText("");
            String room = b.path("roomId").asText("");
            if (!nick.isEmpty()) anchorName.setText(nick);
            if (!title.isEmpty()) liveTitle.setText(title);
            if (!room.isEmpty()) roomField.setText(room);
            likeCount = b.path("likeCount").asLong(0);
            likeLabel.setText(String.format("%,d", likeCount));
            likeCountField.setText(String.format("%,d", likeCount));
        }
        if (replayCover != null) showCover(replayCover);
        replayCastIdx = 0;
    }

    /** 重建到指定时间点：清空面板并补渲染此前的消息（最多 250 条）。 */
    private void rebuildReplayTo(double timelineSec) {
        if (replayCasts.isEmpty()) return;
        long target = replayFirstMs + (long) (Math.max(0, timelineSec) * 1000);
        int count = 0;
        for (JsonNode c : replayCasts) {
            if (c.path("timestamp").asLong(0) > target) break;
            count++;
        }
        replayCastIdx = count;
        int from = Math.max(0, count - 250);
        for (int i = from; i < count; i++) renderReplayCast(replayCasts.get(i));
        refreshGiftRank();
    }

    private void renderReplayCasts(double timelineSec) {
        if (replayCasts.isEmpty()) return;
        long target = replayFirstMs + (long) (Math.max(0, timelineSec) * 1000);
        int n = 0;
        while (replayCastIdx < replayCasts.size() && n < 400) {
            JsonNode c = replayCasts.get(replayCastIdx);
            if (c.path("timestamp").asLong(0) > target) break;
            renderReplayCast(c);
            replayCastIdx++;
            n++;
        }
    }

    private void renderReplayCast(JsonNode c) {
        long tsMs = c.path("timestamp").asLong(0);
        String method = c.path("method").asText("");
        ChatMsg m = new ChatMsg();
        m.nickname = c.path("user").path("name").asText("");
        m.content = c.path("content").asText("");
        JsonNode gift = c.path("gift");
        if (gift.isObject()) {
            m.giftName = gift.path("name").asText("");
            m.giftCount = gift.path("count").asLong(1);
            m.giftPrice = gift.path("price").asLong(0);
            m.giftIcon = gift.path("icon").asText("");
            m.repeatEnd = gift.path("repeatEnd").asInt(0) == 1;
            m.delta = m.giftCount;
            m.combo = m.giftCount;
        }
        String name = replayMsgName(method);
        String text = replayText(m.nickname, m.content);
        LocalDateTime t = tsMs > 0
                ? LocalDateTime.ofInstant(Instant.ofEpochMilli(tsMs), ZoneId.systemDefault())
                : LocalDateTime.now();
        applyMessage(name, text, m, t, true);
    }

    private static String replayMsgName(String method) {
        return switch (method == null ? "" : method) {
            case "WebcastChatMessage" -> "弹幕";
            case "WebcastEmojiChatMessage" -> "表情弹幕";
            case "WebcastGiftMessage", "WebcastBindingGiftMessage" -> "礼物";
            case "WebcastMemberMessage" -> "进场";
            case "WebcastLikeMessage" -> "点赞";
            case "WebcastSocialMessage" -> "关注";
            case "WebcastFansclubMessage" -> "粉丝团";
            case "WebcastRoomUserSeqMessage" -> "在线人数";
            case "WebcastRoomStatsMessage" -> "直播数据";
            case "WebcastInRoomBannerMessage" -> "横幅";
            case "WebcastLotteryDrawResultEventMessage" -> "抽奖";
            case "WebcastHotChatMessage" -> "热议";
            default -> method == null || method.isEmpty() ? "其他" : method;
        };
    }

    private static String replayText(String nickname, String content) {
        String nick = nickname == null ? "" : nickname.strip();
        String text = content == null ? "" : content.strip();
        if (!nick.isEmpty() && !text.isEmpty()) return "[" + nick + "]：" + text;
        return text;
    }

    private static String fmtClock(double seconds) {
        int s = (int) Math.max(0, seconds);
        return String.format("%02d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60);
    }

    // ==================== 状态机 ====================

    private void applyStatus(String status, String msg) {
        switch (status == null ? "" : status) {
            case "connected" -> {
                setStatus(msg, "#4caf50");
                waitingLive = false;
                connected = true;
                setActionBtn("断开", true, true);
                setupPollTimer();
                loadLiveStreams();
            }
            case "connecting" -> setStatus(msg, "#eab308");
            case "captcha" -> {
                stopVideoOnEnd();
                setStatus(msg, "#ff9800");
                connected = false;
                waitingLive = false;
                timerRunning = false;
                stopTimer();
                stopStats();
                setActionBtn("连接", false, true);
                setupPollTimer();
                toast("⚠️ " + msg);
            }
            case "not_live" -> {
                stopVideoOnEnd();
                boolean wasWaiting = waitingLive;
                waitingLive = true;
                connected = false;
                timerRunning = false;
                stopTimer();
                stopStats();
                statusDot.setStyle("-fx-text-fill:#ff9800; -fx-font-size:14px;");
                statusLabel.setStyle("-fx-text-fill:#ff9800;");
                if (pollBtn.isSelected()) {
                    int interval = pollInterval();
                    statusLabel.setText("主播未开播 · 每" + interval + "秒自动重试中，开播后自动连接");
                    setActionBtn("立即重试", false, true);
                    if (!wasWaiting) {
                        toast("ℹ️ 主播当前未开播：每" + interval + "秒自动重试中，开播后自动连接；"
                                + "点「🔁 轮询」可取消等待");
                    }
                } else {
                    statusLabel.setText("主播未开播（点「🔁 轮询」可自动等待开播）");
                    setActionBtn("连接", false, true);
                    if (!wasWaiting) toast("ℹ️ 主播当前未开播，未建立连接");
                }
                setupPollTimer();
            }
            case "error" -> {
                stopVideoOnEnd();
                setStatus(msg, "#ff5252");
                connected = false;
                waitingLive = false;
                timerRunning = false;
                stopTimer();
                rtClose();
                stopStats();
                setActionBtn("连接", false, true);
                setupPollTimer();
            }
            case "disconnected" -> {
                stopVideoOnEnd();
                boolean wasManual = manualStop;
                connected = false;
                timerRunning = false;
                stopTimer();
                rtClose();
                stopStats();
                if (!wasManual && settingBool("auto_save_danmaku", true)) {
                    autoSaveDanmaku();
                }
                if (wasManual) {
                    waitingLive = false;
                    setStatus(msg, "#8a93a5");
                    statusDot.setStyle("-fx-text-fill:#ff5252; -fx-font-size:14px;");
                    setActionBtn("连接", false, true);
                } else if (pollBtn.isSelected()) {
                    waitingLive = true;
                    int interval = pollInterval();
                    setStatus("直播已结束 · 每" + interval + "秒检测开播中，开播后自动连接", "#ff9800");
                    setActionBtn("立即重试", false, true);
                    toast("ℹ️ 直播已结束：每" + interval + "秒自动检测开播，开播后自动连接");
                } else {
                    waitingLive = false;
                    setStatus("直播已结束（点「🔁 轮询」可自动等待下次开播）", "#8a93a5");
                    statusDot.setStyle("-fx-text-fill:#ff5252; -fx-font-size:14px;");
                    setActionBtn("连接", false, true);
                }
                setupPollTimer();
            }
            default -> setStatus(msg, "#8a93a5");
        }
    }

    private void setStatus(String text, String color) {
        statusLabel.setText(text);
        statusLabel.setStyle("-fx-text-fill:" + color + ";");
        statusDot.setStyle("-fx-text-fill:" + color + "; -fx-font-size:14px;");
    }

    private void setActionBtn(String text, boolean disconnectMode, boolean enabled) {
        actionDisconnect = disconnectMode;
        actionBtn.setText(text);
        actionBtn.setDisable(!enabled);
        String color = !enabled ? "#9e9e9e" : (disconnectMode ? "#c62828" : "#2e7d32");
        actionBtn.setStyle("-fx-background-color:" + color
                + "; -fx-text-fill:white; -fx-background-radius:6;");
    }

    private void applyRoomInfo(DouyinLive.RoomInfo info) {
        if (info == null) return;
        if (info.anchor() != null && !info.anchor().isEmpty()) anchorName.setText(info.anchor());
        if (info.fans() != null && !info.fans().isEmpty()) anchorFans.setText(info.fans());
        if (info.online() != null && !info.online().isEmpty()) onlineCount.setText(info.online());
        if (info.totalViewers() != null && !info.totalViewers().isEmpty()) {
            totalViewers.setText(info.totalViewers());
        }
        if (info.likeCount() != null && !info.likeCount().isEmpty()) {
            likeCountField.setText(info.likeCount());
        }
        if (info.title() != null && !info.title().isEmpty()) liveTitle.setText(info.title());
        String cover = info.cover() == null || info.cover().isEmpty() ? info.avatar() : info.cover();
        if (cover != null && !cover.isEmpty()) downloadCover(cover);
    }

    // ==================== 计时 / 轮询 ====================

    private int pollInterval() {
        return Math.max(15, settingInt("poll_interval", 60));
    }

    private void startTimer() {
        if (timer != null) return;
        timerRunning = true;
        timer = new Timeline(new KeyFrame(Duration.seconds(1), e -> tick()));
        timer.setCycleCount(Animation.INDEFINITE);
        timer.play();
    }

    private void stopTimer() {
        if (timer != null) {
            timer.stop();
            timer = null;
        }
    }

    private void tick() {
        if (!connected) {
            timerRunning = false;
            stopTimer();
            return;
        }
        long elapsed = Math.max(0, (System.currentTimeMillis() - startTs) / 1000);
        long h = elapsed / 3600;
        long rem = elapsed % 3600;
        long m = rem / 60;
        long s = rem % 60;
        timerLabel.setText(String.format("%02d:%02d:%02d", h, m, s));
        nowLabel.setText(LocalDateTime.now().format(CLOCK));
    }

    private void onPollToggle() {
        setSetting("poll_enabled", pollBtn.isSelected());
        saveSettings();
        setupPollTimer();
        if (pollBtn.isSelected()) {
            if (!connected && !waitingLive) {
                connect();
                return;
            }
        } else {
            pollCountdown.setText("");
            if (connected) toast("ℹ️ 轮询已关闭（当前连接保持，不再自动重访）");
        }
        if (!waitingLive) return;
        if (pollBtn.isSelected()) {
            int interval = pollInterval();
            setStatus("主播未开播 · 每" + interval + "秒自动重试中，开播后自动连接", "#ff9800");
            setActionBtn("立即重试", false, true);
        } else {
            setStatus("主播未开播 · 已停止自动重试（可手动点「连接」重试）", "#8a93a5");
            setActionBtn("连接", false, true);
        }
    }

    private void updatePollStyle() {
        if (pollBtn.isSelected()) {
            pollBtn.setStyle("-fx-background-color:#e65100; -fx-text-fill:white;"
                    + " -fx-background-radius:6; -fx-font-size:14px;");
        } else {
            pollBtn.setStyle("-fx-font-size:14px;");
        }
    }

    private void setupPollTimer() {
        stopPollTimer();
        if (!(pollBtn.isSelected() && waitingLive && !connected)) {
            pollCountdown.setText("");
            return;
        }
        int interval = pollInterval();
        final int[] remain = {interval};
        pollCountdown.setText("⏱ " + interval + "s");
        pollTicker = new Timeline(new KeyFrame(Duration.seconds(1), e -> {
            remain[0]--;
            if (remain[0] <= 0) {
                remain[0] = interval;
                if (pollBtn.isSelected() && waitingLive && !connected) {
                    resumeKeep = true;
                    connect();
                }
            }
            pollCountdown.setText("⏱ " + Math.max(0, remain[0]) + "s");
        }));
        pollTicker.setCycleCount(Animation.INDEFINITE);
        pollTicker.play();
    }

    private void stopPollTimer() {
        if (pollTicker != null) {
            pollTicker.stop();
            pollTicker = null;
        }
    }

    // ==================== 统计数据（游客轮询 + CSV） ====================

    private void onSaveStatsToggle() {
        saveStatsOn = saveStatsBox.isSelected();
        setSetting("save_stats", saveStatsOn);
        saveSettings();
        if (saveStatsOn && connected && !activeRoom.isEmpty()) {
            startStats(activeRoom);
        } else if (!saveStatsOn) {
            stopStats();
        }
    }

    private void startStats(String room) {
        stopStats();
        StatsCollector sc = new StatsCollector(room,
                Math.max(1, settingInt("stats_interval", 1)), this::onStats);
        statsCollector = sc;
        sc.start();
    }

    private void stopStats() {
        if (statsCollector != null) {
            statsCollector.stop();
            statsCollector = null;
        }
    }

    private void onStats(DouyinLive.RoomInfo info) {
        if (info == null) return;
        Platform.runLater(() -> {
            if (info.online() != null && !info.online().isEmpty()) onlineCount.setText(info.online());
            if (info.fans() != null && !info.fans().isEmpty()) anchorFans.setText(info.fans());
            if (info.totalViewers() != null && !info.totalViewers().isEmpty()) {
                totalViewers.setText(info.totalViewers());
            }
        });
        if (saveStatsOn) {
            String ts = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm:ss"));
            String line = ts + "," + nullToEmpty(info.online()) + "," + likeCount + ","
                    + nullToEmpty(info.fans()) + "," + nullToEmpty(info.totalViewers());
            appendStatsFile(activeRoom, line);
        }
    }

    private static String nullToEmpty(String s) {
        return s == null ? "" : s;
    }

    private static void appendStatsFile(String room, String line) {
        if (room == null || room.isBlank()) return;
        Path dir = Settings.load().effectiveExportDir().resolve("弹幕文件");
        Path path = dir.resolve(room + "_" + LocalDate.now().format(DAY) + ".csv");
        synchronized (STATS_LOCK) {
            try {
                Files.createDirectories(dir);
                boolean exists = Files.isRegularFile(path) && Files.size(path) > 0;
                try (BufferedWriter w = Files.newBufferedWriter(path,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
                    if (!exists) w.write("\uFEFF时间,在线人数,点赞数,粉丝数,累计观看\n");
                    w.write(line);
                    w.newLine();
                }
            } catch (Exception e) {
                Log.warn("写抖音统计 CSV 失败：" + e.getMessage());
            }
        }
    }

    private static final class StatsCollector {
        private final DouyinLive live = new DouyinLive();
        private final String room;
        private final int interval;
        private final Consumer<DouyinLive.RoomInfo> callback;
        private volatile boolean stopped;
        private Thread thread;

        StatsCollector(String room, int interval, Consumer<DouyinLive.RoomInfo> callback) {
            this.room = room;
            this.interval = Math.max(1, interval);
            this.callback = callback;
        }

        void start() {
            if (thread != null && thread.isAlive()) return;
            stopped = false;
            thread = new Thread(this::run, "douyin-stats");
            thread.setDaemon(true);
            thread.start();
        }

        void stop() {
            stopped = true;
            Thread t = thread;
            if (t != null) t.interrupt();
            thread = null;
        }

        private void run() {
            while (!stopped) {
                try {
                    DouyinLive.RoomInfo info = live.roomInfo(room);
                    if (info != null && info.roomId() > 0) callback.accept(info);
                } catch (Exception e) {
                    Log.warn("抖音统计采集失败：" + e.getMessage());
                }
                long deadline = System.currentTimeMillis() + interval * 1000L;
                while (!stopped && System.currentTimeMillis() < deadline) {
                    try {
                        Thread.sleep(100);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
        }
    }

    // ==================== 消息处理 ====================

    private void handleMessage(DouyinLiveClient.Msg msg) {
        if (msg == null) return;
        String text = formatLine(msg.name(), msg.nickname(), msg.content());
        recordCast(text, msg);
        ChatMsg cm = ChatMsg.from(msg);
        LocalDateTime at = LocalDateTime.now();
        Platform.runLater(() -> applyMessage(msg.name(), text, cm, at, true));
    }

    private static String formatLine(String name, String nickname, String content) {
        String nick = nickname == null ? "" : nickname.strip();
        String body = content == null ? "" : content.strip();
        String n = name == null ? "" : name;
        if (n.equals("进场")) {
            return nick.isEmpty() ? (body.isEmpty() ? "进入直播间" : body)
                    : "[" + nick + "]：" + (body.isEmpty() ? "进入直播间" : body);
        }
        if (n.equals("关注")) return nick.isEmpty() ? "关注了主播" : "[" + nick + "]：关注了主播";
        if (n.equals("点赞")) {
            if (nick.isEmpty()) return body.isEmpty() ? "为主播点赞了" : body;
            return "[" + nick + "]：" + (body.isEmpty() ? "为主播点赞了" : body);
        }
        if (!nick.isEmpty() && !body.isEmpty()) return "[" + nick + "]：" + body;
        if (!nick.isEmpty()) return "[" + nick + "]：";
        return body;
    }

    private void applyMessage(String name, String text, ChatMsg m, LocalDateTime t, boolean rank) {
        String ts = t.format(TIME_MS);
        String type = name == null ? "" : name;

        if (type.equals("礼物") || (notBlank(m.giftName)
                && !type.equals("弹幕") && !type.equals("表情弹幕"))) {
            addGiftRank(m, t, rank);
            if (m.delta == 0) return;
            logChat(ts, "礼物", m, "");
            if (giftFilter.isSelected() && chatFilterText.isEmpty()) appendChatRow(chat(ts, "礼物", m, ""));
        } else if (type.equals("弹幕") || type.equals("表情弹幕")) {
            logChat(ts, type, m, text);
            if (chatFilter.isSelected() && chatFilterText.isEmpty()) {
                appendChatRow(chat(ts, type, m, text));
            }
        } else if (type.equals("粉丝团")) {
            if (!text.isEmpty() && FANSCLUB_ACTION_RE.matcher(text).find()) {
                String gname = text.contains("加入") ? "加入粉丝团" : "点亮粉丝团";
                ChatMsg mg = new ChatMsg();
                mg.nickname = m.nickname;
                mg.content = "送出了" + gname + " x1";
                mg.giftName = gname;
                mg.giftCount = 1;
                mg.giftPrice = m.giftPrice > 0 ? m.giftPrice : FANSCLUB_PRICE_FALLBACK;
                addGiftRank(mg, t, rank);
                logChat(ts, "礼物", mg, "");
                if (giftFilter.isSelected() && chatFilterText.isEmpty()) {
                    appendChatRow(chat(ts, "礼物", mg, ""));
                }
            } else {
                logEvent(ts + " [" + type + "] " + text);
            }
        } else if (type.equals("进场")) {
            if (m.memberCount > 0) setAnchorNumber(onlineCount, m.memberCount);
            logEvent(ts + " 💚 " + text);
        } else if (type.equals("点赞")) {
            if (m.likeTotal > 0) likeCount = m.likeTotal;
            else likeCount++;
            likeLabel.setText(String.format("%,d", likeCount));
            likeCountField.setText(String.format("%,d", likeCount));
            logEvent(ts + " 👍 " + text);
        } else if (type.equals("关注")) {
            if (m.followCount > 0) setAnchorNumber(anchorFans, m.followCount);
            logEvent(ts + " ⭐ " + text);
        } else if (type.equals("在线人数")) {
            if (m.roomTotal > 0) setAnchorNumber(onlineCount, m.roomTotal);
            if (m.roomTotalUser > 0) setAnchorNumber(totalViewers, m.roomTotalUser);
            else if (notBlank(m.roomTotalUserStr)) totalViewers.setText(m.roomTotalUserStr);
        } else if (type.equals("直播数据")) {
            if (m.roomTotal > 0) setAnchorNumber(onlineCount, m.roomTotal);
        } else if (type.equals("横幅")) {
            if (!text.isEmpty() && !text.equals(lastBannerText)) {
                lastBannerText = text;
                logEvent(ts + " 📢 " + text);
            }
        } else if (!text.isEmpty()) {
            logEvent(ts + " [" + type + "] " + text);
        }
    }

    private static boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }

    private static void setAnchorNumber(Label label, long value) {
        label.setText(String.format("%,d", value));
    }

    private void logChat(String ts, String kind, ChatMsg m, String text) {
        ChatEntry entry = new ChatEntry(ts, kind, m, text == null ? "" : text);
        chatLog.add(entry);
        if (chatLog.size() > 8000) chatLog.subList(0, chatLog.size() - 4000).clear();
        if (!chatFilterText.isEmpty() && chatKindVisible(kind)
                && chatMatches(entry, chatFilterText)) {
            appendChatRow(entry);
            updateSearchCount();
        }
    }

    private void logEvent(String text) {
        if (text == null || text.isEmpty()) return;
        eventLog.add(text);
        if (eventLog.size() > 4000) eventLog.subList(0, eventLog.size() - 2000).clear();
        if (eventVisible(text)) appendEventRow(text);
        updateEventCount();
    }

    private boolean eventVisible(String text) {
        if (text.contains("👍")) {
            if (!likeFilter.isSelected()) return false;
        } else if (text.contains("💚")) {
            if (!enterFilter.isSelected()) return false;
        } else if (!logFilter.isSelected()) {
            return false;
        }
        return eventFilterText.isEmpty() || text.toLowerCase(Locale.ROOT)
                .contains(eventFilterText.toLowerCase(Locale.ROOT));
    }

    private void appendChatRow(ChatEntry entry) {
        boolean atBottom = chatList.getItems().isEmpty() || isScrollAtBottom(chatList);
        chatList.getItems().add(entry);
        if (chatList.getItems().size() > 500) {
            chatList.getItems().remove(0, chatList.getItems().size() - 500);
        }
        if (settingBool("keep_scroll", true) || atBottom) {
            chatList.scrollTo(chatList.getItems().size() - 1);
        }
    }

    private void appendEventRow(String text) {
        boolean atBottom = eventList.getItems().isEmpty() || isScrollAtBottom(eventList);
        eventList.getItems().add(text);
        if (eventList.getItems().size() > 500) {
            eventList.getItems().remove(0, eventList.getItems().size() - 500);
        }
        if (settingBool("keep_scroll", true) || atBottom) {
            eventList.scrollTo(eventList.getItems().size() - 1);
        }
    }

    private static boolean isScrollAtBottom(ListView<?> list) {
        // JavaFX ListView 没有公开滚动值；保持滚动开关默认开启时无需精确判断
        return true;
    }

    private void onEventFilterToggle(String key, CheckBox box) {
        setSetting(key, box.isSelected());
        saveSettings();
        applyEventFilter();
    }

    private void applyChatFilter() {
        chatFilterText = searchField.getText() == null ? "" : searchField.getText().trim();
        List<ChatEntry> visible = visibleChatEntries();
        chatRows.setAll(last500(visible));
        if (!chatRows.isEmpty() && settingBool("keep_scroll", true)) {
            chatList.scrollTo(chatRows.size() - 1);
        }
        updateSearchCount();
    }

    private List<ChatEntry> visibleChatEntries() {
        List<ChatEntry> out = new ArrayList<>();
        for (ChatEntry e : chatLog) {
            if (!chatKindVisible(e.kind())) continue;
            if (!chatFilterText.isEmpty() && !chatMatches(e, chatFilterText)) continue;
            out.add(e);
        }
        return out;
    }

    private boolean chatKindVisible(String kind) {
        return "礼物".equals(kind) ? giftFilter.isSelected() : chatFilter.isSelected();
    }

    private static boolean chatMatches(ChatEntry entry, String kw) {
        String nickname = entry.m().nickname == null ? "" : entry.m().nickname;
        if (kw.startsWith("@")) return nickname.equals(kw.substring(1));
        String hay = (nickname + " " + nullToEmpty(entry.m().content) + " "
                + nullToEmpty(entry.text())).strip();
        return hay.toLowerCase(Locale.ROOT).contains(kw.toLowerCase(Locale.ROOT));
    }

    private void updateSearchCount() {
        int n = visibleChatEntries().size();
        searchCount.setText(chatFilterText.isEmpty() ? "共 " + n + " 条" : n + " 条匹配");
    }

    private void applyEventFilter() {
        eventFilterText = eventSearch.getText() == null ? "" : eventSearch.getText().trim();
        List<String> visible = new ArrayList<>();
        for (String line : eventLog) {
            if (eventVisible(line)) visible.add(line);
        }
        eventRows.setAll(last500(visible));
        if (!eventRows.isEmpty() && settingBool("keep_scroll", true)) {
            eventList.scrollTo(eventRows.size() - 1);
        }
        updateEventCount();
    }

    private void updateEventCount() {
        int n = 0;
        for (String line : eventLog) {
            if (eventVisible(line)) n++;
        }
        if (eventFilterText.isEmpty()) {
            eventCount.setText("共 " + n + " 条");
        } else {
            String note = "筛选「" + eventFilterText + "」：" + n + " 条匹配";
            if (n > 500) note += "（显示最近 500 条）";
            eventCount.setText(note);
        }
    }

    private static <T> List<T> last500(List<T> items) {
        if (items.size() <= 500) return new ArrayList<>(items);
        return new ArrayList<>(items.subList(items.size() - 500, items.size()));
    }

    private void filterByUser(String nickname) {
        if (nickname == null || nickname.isBlank()) return;
        searchField.setText("@" + nickname);
    }

    private ChatEntry chat(String ts, String kind, ChatMsg m, String text) {
        return new ChatEntry(ts, kind, m, text == null ? "" : text);
    }

    // ==================== 打赏榜（连击会话结算） ====================

    private void addGiftRank(ChatMsg m, LocalDateTime t, boolean refresh) {
        if (m == null || !notBlank(m.giftName)) return;
        String user = notBlank(m.nickname) ? m.nickname.strip() : "未知用户";
        long count = m.giftCount <= 0 ? 1 : m.giftCount;
        long price = Math.max(0, m.giftPrice);
        if (price <= 0 && FANSCLUB_NAME_RE.matcher(m.giftName).find()) {
            price = FANSCLUB_PRICE_FALLBACK;
        }
        long now = t.atZone(ZoneId.systemDefault()).toEpochSecond();
        String key = user + "\u0000" + m.giftName;

        ComboSession sess = giftSessions.get(key);
        if (sess != null && now - sess.at > 15) sess = null;
        long settled = sess == null ? 0 : sess.settled;
        long delta;
        if (count > settled) {
            delta = count - settled;
        } else if (count == 1) {
            // x1 一律按新一轮 1 个计：会员加入/灯牌反复送不会被当成重推帧
            delta = 1;
        } else {
            delta = 0;
        }
        if (m.repeatEnd) {
            giftSessions.remove(key);
        } else {
            giftSessions.put(key, new ComboSession(count > settled ? count : Math.max(settled, 1), now));
        }
        m.delta = delta;
        m.combo = count;
        if (delta > 0) {
            RankStat stat = giftRank.computeIfAbsent(user, k -> new RankStat());
            stat.giftCount += delta;
            stat.totalValue += delta * price;
            stat.lastTime = t;
        }
        if (refresh) refreshGiftRank();
    }

    private void refreshGiftRank() {
        List<Map.Entry<String, RankStat>> items = new ArrayList<>(giftRank.entrySet());
        items.sort(Comparator
                .comparingLong((Map.Entry<String, RankStat> e) -> -e.getValue().totalValue)
                .thenComparingLong(e -> -e.getValue().giftCount)
                .thenComparing(Map.Entry::getKey));
        List<RankRow> rows = new ArrayList<>();
        int limit = Math.min(150, items.size());
        for (int i = 0; i < limit; i++) {
            Map.Entry<String, RankStat> e = items.get(i);
            RankStat s = e.getValue();
            rows.add(new RankRow(String.valueOf(i + 1), e.getKey(),
                    String.format("%,d", s.giftCount), String.format("%,d", s.totalValue),
                    s.lastTime == null ? "" : s.lastTime.format(DateTimeFormatter.ofPattern("HH:mm:ss"))));
        }
        rankRows.setAll(rows);
    }

    private void giftingReset() {
        likeCount = 0;
        likeLabel.setText("0");
        likeCountField.setText("0");
        anchorName.setText("*****");
        anchorFans.setText("*****");
        onlineCount.setText("*****");
        totalViewers.setText("*****");
        likeCountField.setText("*****");
        liveTitle.setText("未获取直播标题");
    }

    private static final class RankStat {
        long giftCount;
        long totalValue;
        LocalDateTime lastTime;
    }

    private record ComboSession(long settled, long at) {}

    private record RankRow(String rank, String user, String giftCount,
                           String totalValue, String lastTime) {}

    private record ChatEntry(String ts, String kind, ChatMsg m, String text) {}

    private static final class ChatMsg {
        String nickname;
        String content;
        String giftName;
        String giftIcon;
        long giftCount;
        long giftPrice;
        long delta = -1;
        long combo = -1;
        long likeTotal;
        long followCount;
        long roomTotal;
        long roomTotalUser;
        String roomTotalUserStr;
        long memberCount;
        boolean repeatEnd;

        static ChatMsg from(DouyinLiveClient.Msg m) {
            ChatMsg c = new ChatMsg();
            c.nickname = m.nickname();
            c.content = m.content();
            c.giftName = m.giftName();
            c.giftIcon = m.giftIcon();
            c.giftCount = m.giftCount();
            c.giftPrice = m.giftPrice();
            c.likeTotal = m.likeTotal();
            c.followCount = m.followCount();
            c.roomTotal = m.roomTotal();
            c.roomTotalUser = m.roomTotalUser();
            c.roomTotalUserStr = m.roomTotalUserStr();
            c.memberCount = m.memberCount();
            c.repeatEnd = m.repeatEnd();
            return c;
        }
    }

    private final class ChatCell extends ListCell<ChatEntry> {
        private final TextFlow flow = new TextFlow();

        ChatCell() {
            flow.setLineSpacing(2);
            flow.prefWidthProperty().bind(widthProperty().subtract(26));
        }

        @Override
        protected void updateItem(ChatEntry e, boolean empty) {
            super.updateItem(e, empty);
            if (empty || e == null) {
                setGraphic(null);
                return;
            }
            flow.getChildren().clear();
            Text ts = new Text(e.ts() + " ");
            ts.getStyleClass().add("chat-ts");
            flow.getChildren().add(ts);
            if ("礼物".equals(e.kind())) {
                flow.getChildren().add(new Text("🎁 "));
            }
            ChatMsg m = e.m();
            String nickname = m.nickname == null ? "" : m.nickname;
            if (!nickname.isEmpty()) {
                Label nick = new Label(nickname);
                nick.setStyle("-fx-font-weight:bold; -fx-cursor:hand; -fx-text-fill:"
                        + (cn.fmjj.toolbox.gui.Theme.isDark() ? "#c6cbd6" : "#1f2328") + ";");
                nick.setOnMouseClicked(ev -> filterByUser(nickname));
                flow.getChildren().add(nick);
                flow.getChildren().add(new Text("："));
            }
            String body = notBlank(m.content) ? m.content : e.text();
            if ("礼物".equals(e.kind()) && m.delta >= 0 && m.combo >= 0 && m.delta != m.combo) {
                body = body + "（+" + m.delta + "）";
            }
            Text content = new Text(body == null ? "" : body);
            if ("礼物".equals(e.kind())) {
                content.setFill(Color.web(cn.fmjj.toolbox.gui.Theme.isDark() ? "#ffd54f" : "#8a6d00"));
                content.setStyle("-fx-font-weight:bold;");
            }
            flow.getChildren().add(content);
            setGraphic(flow);
        }
    }

    // ==================== 实时落盘 / 恢复 ====================

    private Path rtFilePath(String room) {
        String day = LocalDate.now().format(DAY);
        return danmakuDir().resolve("实时记录").resolve((room == null || room.isBlank() ? "room" : room)
                + "_" + day + ".jsonl");
    }

    private void rtOpen(String room) {
        if (rtWriter != null && room.equals(rtRoom)) return;
        rtClose();
        try {
            Path path = rtFilePath(room);
            Files.createDirectories(path.getParent());
            rtWriter = Files.newBufferedWriter(path,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            rtRoom = room;
        } catch (Exception e) {
            Log.warn("打开抖音实时记录失败：" + e.getMessage());
            rtWriter = null;
        }
    }

    private synchronized void rtWrite(ObjectNode cast) {
        if (rtWriter == null) return;
        try {
            rtWriter.write(mapper.writeValueAsString(cast));
            rtWriter.newLine();
            rtWriter.flush();
        } catch (Exception e) {
            rtClose();
        }
    }

    private synchronized void rtClose() {
        BufferedWriter w = rtWriter;
        rtWriter = null;
        rtRoom = "";
        if (w != null) {
            try {
                w.flush();
                w.close();
            } catch (Exception ignored) {
            }
        }
    }

    private void recordCast(String text, DouyinLiveClient.Msg msg) {
        synchronized (sessionCasts) {
            if (sessionCasts.size() >= 50000) {
                sessionCasts.subList(0, sessionCasts.size() - 25000).clear();
            }
        }
        ObjectNode c = mapper.createObjectNode();
        c.put("method", msg.type() == null ? "" : msg.type());
        c.put("timestamp", msg.ts() > 0 ? msg.ts() : System.currentTimeMillis());
        c.put("content", notBlank(msg.content()) ? msg.content() : (text == null ? "" : text));
        c.putArray("rtfContent");
        if (notBlank(msg.nickname())) {
            ObjectNode user = mapper.createObjectNode();
            user.put("name", msg.nickname());
            c.set("user", user);
        }
        if (notBlank(msg.giftName())) {
            ObjectNode gift = mapper.createObjectNode();
            gift.put("name", msg.giftName());
            gift.put("count", msg.giftCount() > 0 ? msg.giftCount() : 1);
            gift.put("price", Math.max(0, msg.giftPrice()));
            gift.put("icon", nullToEmpty(msg.giftIcon()));
            if (msg.repeatEnd()) gift.put("repeatEnd", 1);
            c.set("gift", gift);
        }
        sessionCasts.add(c);
        rtWrite(c);
    }

    private int loadRtTail(String room) {
        if (!chatLog.isEmpty()) return 0;
        Path path;
        try {
            path = rtFilePath(room);
            if (!Files.isRegularFile(path) || Files.size(path) <= 0) return 0;
        } catch (Exception e) {
            return 0;
        }
        List<String> lines;
        try {
            lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        } catch (Exception e) {
            return 0;
        }
        int from = Math.max(0, lines.size() - 4000);
        List<ObjectNode> casts = new ArrayList<>();
        List<ChatEntry> chat = new ArrayList<>();
        for (int i = from; i < lines.size(); i++) {
            JsonNode c;
            try {
                c = mapper.readTree(lines.get(i));
            } catch (Exception e) {
                continue;
            }
            if (c == null || !c.isObject()) continue;
            casts.add((ObjectNode) c);
            String method = c.path("method").asText("");
            if (!RT_CHAT_METHODS.contains(method)) continue;
            long tsMs = c.path("timestamp").asLong(0);
            String ts = "--:--:--.---";
            if (tsMs > 0) {
                ts = LocalDateTime.ofInstant(Instant.ofEpochMilli(tsMs), ZoneId.systemDefault())
                        .format(TIME_MS);
            }
            JsonNode gift = c.path("gift");
            JsonNode user = c.path("user");
            ChatMsg m = new ChatMsg();
            m.nickname = user.path("name").asText("");
            m.content = c.path("content").asText("");
            String kind;
            if (gift.isObject() && gift.has("name")) {
                m.giftName = gift.path("name").asText("");
                m.giftCount = gift.path("count").asLong(1);
                m.giftPrice = gift.path("price").asLong(0);
                m.giftIcon = gift.path("icon").asText("");
                m.repeatEnd = gift.path("repeatEnd").asInt(0) == 1;
                kind = "礼物";
            } else {
                kind = ("DANMU_MSG".equals(method) || "DANMU_MSG_EMOJI".equals(method))
                        ? "弹幕" : "弹幕";
            }
            chat.add(new ChatEntry(ts, kind, m, ""));
        }
        if (chat.isEmpty()) return 0;
        if (chat.size() > 2000) chat = new ArrayList<>(chat.subList(chat.size() - 2000, chat.size()));
        chatLog.addAll(chat);
        synchronized (sessionCasts) {
            sessionCasts.clear();
            sessionCasts.addAll(casts.subList(Math.max(0, casts.size() - 5000), casts.size()));
        }
        for (ChatEntry e : chatLog) {
            if (!"礼物".equals(e.kind())) continue;
            LocalDateTime at;
            try {
                at = LocalDateTime.of(LocalDate.now(), LocalTime.parse(e.ts(), TIME_MS));
            } catch (Exception ex) {
                at = LocalDateTime.now();
            }
            addGiftRank(e.m(), at, false);
        }
        refreshGiftRank();
        applyChatFilter();
        updateSearchCount();
        return chat.size();
    }

    // ==================== 封面 / 保存主播数据 ====================

    private void downloadCover(String url) {
        Thread t = new Thread(() -> {
            try {
                HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                        .timeout(java.time.Duration.ofSeconds(15))
                        .header("User-Agent", "Mozilla/5.0")
                        .header("Referer", "https://live.douyin.com/")
                        .GET().build();
                HttpResponse<byte[]> resp = http.jdkClient()
                        .send(req, HttpResponse.BodyHandlers.ofByteArray());
                if (resp.statusCode() == 200 && resp.body().length > 0) {
                    byte[] data = resp.body();
                    coverBytes = data;
                    Platform.runLater(() -> showCover(data));
                }
            } catch (Exception e) {
                Log.warn("下载抖音封面失败：" + e.getMessage());
            }
        }, "douyin-cover");
        t.setDaemon(true);
        t.start();
    }

    private void showCover(byte[] data) {
        try {
            Image img = new Image(new ByteArrayInputStream(data));
            if (img.isError() || img.getWidth() <= 0 || img.getHeight() <= 0) return;
            double w = img.getWidth();
            double h = img.getHeight();
            double side = Math.min(w, h);
            coverView.setImage(img);
            coverView.setViewport(new Rectangle2D((w - side) / 2, (h - side) / 2, side, side));
            coverView.setFitWidth(96);
            coverView.setFitHeight(96);
            coverView.setPreserveRatio(true);
            coverView.setVisible(true);
            coverHint.setVisible(false);
        } catch (Exception ignored) {
        }
    }

    private void resetCover() {
        coverBytes = null;
        coverView.setImage(null);
        coverView.setVisible(false);
        coverHint.setVisible(true);
    }

    private void saveCoverAndData() {
        Path outDir = Settings.load().effectiveExportDir();
        String room = roomField.getText() == null ? "" : roomField.getText().trim();
        if (room.isEmpty()) room = "room";
        List<String> saved = new ArrayList<>();
        byte[] data = coverBytes;
        if (data != null && data.length > 0) {
            try {
                Files.createDirectories(outDir);
                Path p = outDir.resolve(room + "_cover.png");
                Files.write(p, data);
                saved.add(p.getFileName().toString());
            } catch (Exception e) {
                Log.warn("保存封面失败：" + e.getMessage());
            }
        }
        StringBuilder sb = new StringBuilder();
        sb.append("保存时间: ").append(LocalDateTime.now().format(CLOCK)).append('\n');
        sb.append("房间号: ").append(room).append('\n');
        sb.append("主播: ").append(anchorName.getText()).append('\n');
        sb.append("粉丝数: ").append(anchorFans.getText()).append('\n');
        sb.append("在线观众数: ").append(onlineCount.getText()).append('\n');
        sb.append("累计观看人数: ").append(totalViewers.getText()).append('\n');
        sb.append("本场点赞数: ").append(likeCountField.getText()).append('\n');
        try {
            Files.createDirectories(outDir);
            Path txt = outDir.resolve(room + "_主播数据.txt");
            Files.writeString(txt, sb.toString(), StandardCharsets.UTF_8);
            saved.add(txt.getFileName().toString());
        } catch (Exception e) {
            Log.warn("保存主播数据失败：" + e.getMessage());
        }
        if (saved.isEmpty()) {
            toast("❌ 保存失败（封面未加载且数据写入失败）");
        } else {
            toast("✅ 已保存: " + String.join(", ", saved) + " → " + outDir);
        }
    }

    // ==================== 清空 ====================

    private void clearAll() {
        chatLog.clear();
        chatRows.clear();
        chatFilterText = "";
        searchField.clear();
        searchCount.setText("");
        eventLog.clear();
        eventRows.clear();
        eventFilterText = "";
        eventSearch.clear();
        eventCount.setText("");
        lastBannerText = "";
        giftRank.clear();
        giftSessions.clear();
        rankRows.clear();
        likeCount = 0;
        likeLabel.setText("0");
        likeCountField.setText("0");
        synchronized (sessionCasts) {
            sessionCasts.clear();
        }
        startTs = System.currentTimeMillis();
        timerLabel.setText("00:00:00");
        liveTitle.setText("未获取直播标题");
        anchorName.setText("*****");
        anchorFans.setText("*****");
        onlineCount.setText("*****");
        totalViewers.setText("*****");
        resetCover();
    }

    // ==================== 保存 / 导出回放包 ====================

    private void saveDanmakuNow() {
        String room = roomField.getText() == null ? "" : roomField.getText().trim();
        if (room.isEmpty()) {
            toast("⚠️ 请先输入房间号");
            return;
        }
        if (autoSaveDanmaku() == null) toast("⚠️ 当前没有弹幕数据可保存");
    }

    private Path autoSaveDanmaku() {
        if (sessionCasts.isEmpty()) return null;
        return exportReplayZip(null, null, true, exiting);
    }

    /**
     * 打包 dycast 全量格式 zip（data.json + 全量弹幕.json + 分类 TXT + cover）。
     * sync=true 同步写完（断开/退出路径，避免进程结束打断）。
     */
    private Path exportReplayZip(Path videoPath, ObjectNode recMeta, boolean quiet, boolean sync) {
        final List<JsonNode> casts;
        synchronized (sessionCasts) {
            casts = new ArrayList<>(sessionCasts);
        }
        if (casts.isEmpty()) {
            if (!quiet) toast("⚠️ 当前没有弹幕数据可打包");
            return null;
        }
        String room = roomField.getText() == null ? "" : roomField.getText().trim();
        if (room.isEmpty()) room = "room";
        String anchor = anchorName.getText() == null || anchorName.getText().isBlank()
                || anchorName.getText().equals("*****") ? "未知主播" : anchorName.getText().trim();
        Path dir = danmakuDir();
        String ts = LocalDateTime.now().format(FILE_TS);
        Path out = dir.resolve(anchor + "-" + room + "-" + ts + "-" + casts.size() + "条.zip");

        ObjectNode bundle = mapper.createObjectNode();
        bundle.put("_bundle", true);
        bundle.put("exportTime", LocalDateTime.now().toString());
        bundle.put("roomId", room);
        bundle.put("nickname", anchor);
        bundle.put("avatar", "");
        bundle.put("cover", "");
        bundle.put("title", liveTitle.getText() == null ? "" : liveTitle.getText().trim());
        bundle.put("likeCount", likeCount);
        ArrayNode arr = bundle.putArray("casts");
        casts.forEach(arr::add);

        Map<String, String> txtFiles = buildCastTxtFiles(casts);
        byte[] cover = coverBytes;
        if (sync) {
            Path result = writeBundleZip(out, bundle, casts, txtFiles, cover, videoPath, recMeta);
            if (!quiet) {
                if (result != null) toast("✅ 回放包已导出: " + result.getFileName());
                else toast("❌ 回放包导出失败");
            }
            return result;
        }
        Thread t = new Thread(() -> {
            Path result = writeBundleZip(out, bundle, casts, txtFiles, cover, videoPath, recMeta);
            Platform.runLater(() -> {
                if (result != null) toast("✅ 回放包已导出: " + result.getFileName());
                else toast("❌ 回放包导出失败");
            });
        }, "douyin-export");
        t.setDaemon(true);
        t.start();
        return null;
    }

    private Map<String, String> buildCastTxtFiles(List<JsonNode> casts) {
        Map<String, String> files = new LinkedHashMap<>();
        for (Map.Entry<String, String> type : CAST_TXT_TYPES.entrySet()) {
            List<String> lines = new ArrayList<>();
            for (JsonNode c : casts) {
                if (!type.getKey().equals(c.path("method").asText(""))) continue;
                String line = castTxtLine(c);
                if (line != null && !line.isEmpty()) lines.add(line);
            }
            if (!lines.isEmpty()) files.put(type.getValue() + ".txt", String.join("\n", lines));
        }
        return files;
    }

    private static String castTxtLine(JsonNode c) {
        long ts = c.path("timestamp").asLong(0);
        String time = "??:??:??.???";
        if (ts > 0) {
            time = LocalDateTime.ofInstant(Instant.ofEpochMilli(ts), ZoneId.systemDefault())
                    .format(TIME_MS);
        }
        String name = c.path("user").path("name").asText("unknown");
        if (name.isEmpty()) name = "unknown";
        JsonNode gift = c.path("gift");
        String text;
        if (gift.isObject() && gift.has("name")) {
            String gname = gift.path("name").asText("礼物");
            if (gname.isEmpty()) gname = "礼物";
            long count = gift.path("count").asLong(1);
            text = "送出了 " + gname + " × " + (count <= 0 ? 1 : count);
        } else {
            text = c.path("content").asText("");
        }
        if (text == null || text.isEmpty()) return null;
        return time + "|[" + name + "]：" + text;
    }

    private Path writeBundleZip(Path out, ObjectNode bundle, List<JsonNode> casts,
                                Map<String, String> txtFiles, byte[] cover,
                                Path videoPath, ObjectNode recMeta) {
        try {
            Files.createDirectories(out.getParent());
            try (ZipOutputStream zip = new ZipOutputStream(Files.newOutputStream(out))) {
                putText(zip, "data.json",
                        mapper.writerWithDefaultPrettyPrinter().writeValueAsString(bundle));
                ArrayNode arr = mapper.createArrayNode();
                casts.forEach(arr::add);
                putText(zip, "全量弹幕.json",
                        mapper.writerWithDefaultPrettyPrinter().writeValueAsString(arr));
                for (Map.Entry<String, String> e : txtFiles.entrySet()) {
                    putText(zip, e.getKey(), e.getValue());
                }
                if (cover != null && cover.length > 0) {
                    zip.putNextEntry(new ZipEntry("cover.jpg"));
                    zip.write(cover);
                    zip.closeEntry();
                }
                if (videoPath != null && Files.isRegularFile(videoPath)
                        && Files.size(videoPath) > 1024) {
                    zip.putNextEntry(new ZipEntry("recording.flv"));
                    Files.copy(videoPath, zip);
                    zip.closeEntry();
                    if (recMeta != null) {
                        putText(zip, "recording_meta.json",
                                mapper.writerWithDefaultPrettyPrinter().writeValueAsString(recMeta));
                    }
                }
            }
            return out;
        } catch (Exception e) {
            Log.warn("导出回放包失败：" + e.getMessage());
            try {
                Files.deleteIfExists(out);
            } catch (Exception ignored) {
            }
            return null;
        }
    }

    private static void putText(ZipOutputStream zip, String name, String content) throws Exception {
        zip.putNextEntry(new ZipEntry(name));
        zip.write(content.getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
    }

    private void pickReplayZip() {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择回放包");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter(
                "回放包 (*.zip, *.json)", "*.zip", "*.json"));
        chooser.setInitialDirectory(danmakuDir().toFile());
        var selected = chooser.showOpenDialog(content().getScene() == null
                ? null : content().getScene().getWindow());
        if (selected == null) return;
        Thread t = new Thread(() -> {
            ReplayData data = readReplay(selected.toPath(), true);
            double duration = data != null && data.video() != null
                    ? LiveVideoEngine.durationSeconds(data.video()) : 0;
            Platform.runLater(() -> {
                if (data == null || (data.casts.isEmpty() && data.video() == null)) {
                    toast("❌ 回放包解析失败（未找到 data.json / 弹幕或录制视频）");
                    return;
                }
                if (replaySessionActive || replayPlaying) stopReplaySession();
                replayVideoPath = data.video();
                replayPauseBtn.setDisable(true);
                replaySpeedCombo.setDisable(data.video() == null && data.casts().isEmpty());
                prepareReplayData(data, duration);
                ModeItem replayItem = modeItem("replay");
                if (replayItem != null) modeCombo.setValue(replayItem);
                applyMode();
                if (data.video() != null) {
                    replayPlayBtn.setDisable(false);
                    videoPlaceholder.setText("已载入回放视频（" + trunc(data.video().getFileName().toString(), 40)
                            + "，弹幕时长 " + fmtClock(replayDuration) + "），点「▶ 播放回放」");
                    videoPlaceholder.setVisible(true);
                } else if (!replayCasts.isEmpty()) {
                    replayPlayBtn.setDisable(false);
                    videoPlaceholder.setText("已载入弹幕回放（无视频，时长 "
                            + fmtClock(replayDuration) + "），点「▶ 播放回放」");
                    videoPlaceholder.setVisible(true);
                } else {
                    replayPlayBtn.setDisable(true);
                    videoPlaceholder.setText("回放包不含录制视频与弹幕数据");
                    videoPlaceholder.setVisible(true);
                }
                toast("✅ 回放包「" + selected.getName() + "」：" + data.casts.size()
                        + " 条弹幕" + (data.video() != null ? "，含录制视频（弹幕同步）" : "，无视频"));
            });
        }, "douyin-replay-read");
        t.setDaemon(true);
        t.start();
    }

    // ==================== 合并回放包 ====================

    private void mergeReplayZipsDialog() {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择要合并的回放包（可多选）");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter(
                "回放包 (*.zip, *.json)", "*.zip", "*.json"));
        chooser.setInitialDirectory(danmakuDir().toFile());
        List<java.io.File> files = chooser.showOpenMultipleDialog(content().getScene() == null
                ? null : content().getScene().getWindow());
        if (files == null || files.isEmpty()) return;
        if (files.size() == 1) {
            toast("⚠️ 只选了一个包，合并至少需要两个");
            return;
        }
        List<Path> paths = files.stream().map(java.io.File::toPath).toList();
        Thread t = new Thread(() -> {
            MergeResult result = mergeReplayZips(paths);
            Platform.runLater(() -> {
                if (result == null) {
                    toast("❌ 合并失败：包里没有可合并的弹幕数据");
                } else {
                    String msg = "✅ 已合并 " + result.files + " 个包 → "
                            + result.out.getFileName() + "（" + result.merged + " 条，去除断线重叠 "
                            + (result.total - result.merged) + " 条）";
                    if (result.skipped > 0) msg += "；跳过 " + result.skipped + " 个无效包";
                    toast(msg);
                }
            });
        }, "douyin-replay-merge");
        t.setDaemon(true);
        t.start();
    }

    private MergeResult mergeReplayZips(List<Path> paths) {
        List<JsonNode> allCasts = new ArrayList<>();
        List<ObjectNode> bundles = new ArrayList<>();
        byte[] cover = null;
        int skipped = 0;
        for (Path p : paths) {
            ReplayData data = readReplay(p, false);
            if (data == null || data.casts.isEmpty()) {
                skipped++;
                continue;
            }
            for (JsonNode c : data.casts) {
                if (c.isObject() && c.path("timestamp").asLong(0) > 0) allCasts.add(c);
            }
            if (data.bundle != null) bundles.add(data.bundle);
            else {
                ObjectNode b = mapper.createObjectNode();
                b.put("_bundle", true);
                b.put("exportTime", "");
                bundles.add(b);
            }
            if (cover == null) cover = data.cover;
        }
        if (allCasts.size() < 2) return null;
        allCasts.sort(Comparator.comparingLong(c -> c.path("timestamp").asLong(0)));
        Set<String> seen = new HashSet<>();
        List<JsonNode> merged = new ArrayList<>();
        for (JsonNode c : allCasts) {
            String key = c.path("timestamp").asLong(0) + "\u0000" + c.path("method").asText("")
                    + "\u0000" + c.path("user").path("name").asText("")
                    + "\u0000" + c.path("content").asText("")
                    + "\u0000" + c.path("gift").path("name").asText("");
            if (!seen.add(key)) continue;
            merged.add(c);
        }
        bundles.sort(Comparator.comparing(b -> b.path("exportTime").asText("")));
        ObjectNode first = bundles.isEmpty() ? mapper.createObjectNode() : bundles.get(0);
        String anchor = first.path("nickname").asText("未知主播").trim();
        if (anchor.isEmpty()) anchor = "未知主播";
        String room = first.path("roomId").asText("room").trim();
        if (room.isEmpty()) room = "room";
        long like = 0;
        for (ObjectNode b : bundles) like = Math.max(like, b.path("likeCount").asLong(0));
        Path dir = danmakuDir();
        String ts = LocalDateTime.now().format(FILE_TS);
        Path out = dir.resolve(anchor + "-" + room + "-合并-" + ts + "-" + merged.size() + "条.zip");

        ObjectNode bundle = mapper.createObjectNode();
        bundle.put("_bundle", true);
        bundle.put("exportTime", LocalDateTime.now().toString());
        ArrayNode from = bundle.putArray("mergedFrom");
        for (Path p : paths) from.add(p.getFileName().toString());
        bundle.put("roomId", room);
        bundle.put("nickname", anchor);
        bundle.put("avatar", first.path("avatar").asText(""));
        bundle.put("cover", "");
        bundle.put("title", first.path("title").asText(""));
        bundle.put("likeCount", like);
        ArrayNode arr = bundle.putArray("casts");
        merged.forEach(arr::add);

        Map<String, String> txtFiles = buildCastTxtFiles(merged);
        Path written = writeBundleZip(out, bundle, merged, txtFiles, cover, null, null);
        if (written == null) return null;
        return new MergeResult(written, allCasts.size(), merged.size(), skipped, paths.size());
    }

    private ReplayData readReplay(Path path, boolean extractVideo) {
        try {
            String name = path.getFileName().toString().toLowerCase(Locale.ROOT);
            if (name.endsWith(".json")) {
                JsonNode root = mapper.readTree(Files.readString(path, StandardCharsets.UTF_8));
                List<JsonNode> casts = new ArrayList<>();
                if (root.isArray()) {
                    root.forEach(casts::add);
                } else if (root.isObject()) {
                    root.path("casts").forEach(casts::add);
                }
                return new ReplayData(casts,
                        root.isObject() && root.path("_bundle").asBoolean(false)
                                ? (ObjectNode) root : null, null, null, 0);
            }
            if (!name.endsWith(".zip")) return null;
            try (ZipFile zip = new ZipFile(path.toFile(), StandardCharsets.UTF_8)) {
                List<JsonNode> casts = new ArrayList<>();
                ObjectNode bundle = null;
                byte[] cover = null;
                ZipEntry all = zip.getEntry("全量弹幕.json");
                if (all != null) {
                    JsonNode arr = mapper.readTree(zip.getInputStream(all));
                    if (arr.isArray()) arr.forEach(casts::add);
                }
                ZipEntry data = zip.getEntry("data.json");
                if (data != null) {
                    JsonNode root = mapper.readTree(zip.getInputStream(data));
                    if (root.isObject()) {
                        if (casts.isEmpty()) root.path("casts").forEach(casts::add);
                        if (root.path("_bundle").asBoolean(false)) bundle = (ObjectNode) root;
                    }
                }
                for (String coverName : List.of("cover.jpg", "cover.png", "cover.webp")) {
                    ZipEntry e = zip.getEntry(coverName);
                    if (e != null) {
                        try (InputStream in = zip.getInputStream(e)) {
                            cover = in.readAllBytes();
                        }
                        break;
                    }
                }
                Path video = null;
                if (extractVideo) {
                    var entries = zip.entries();
                    while (entries.hasMoreElements()) {
                        ZipEntry e = entries.nextElement();
                        if (e.isDirectory()) continue;
                        String lower = e.getName().toLowerCase(Locale.ROOT);
                        if (!lower.endsWith(".flv") && !lower.endsWith(".mp4")) continue;
                        String ext = lower.endsWith(".flv") ? ".flv" : ".mp4";
                        Path tmp = Files.createTempFile("fmjj-replay-", ext);
                        try (InputStream in = zip.getInputStream(e)) {
                            Files.copy(in, tmp, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                        }
                        tmp.toFile().deleteOnExit();
                        video = tmp;
                        break;
                    }
                }
                long recStartMs = 0;
                ZipEntry meta = zip.getEntry("recording_meta.json");
                if (meta != null) {
                    try (InputStream in = zip.getInputStream(meta)) {
                        JsonNode m = mapper.readTree(in.readAllBytes());
                        recStartMs = m.path("startTimestamp").asLong(0);
                    } catch (Exception ignored) {
                    }
                }
                return new ReplayData(casts, bundle, cover, video, recStartMs);
            }
        } catch (Exception e) {
            return null;
        }
    }

    private record ReplayData(List<JsonNode> casts, ObjectNode bundle, byte[] cover,
                              Path video, long recStartMs) {}

    private record MergeResult(Path out, int total, int merged, int skipped, int files) {}

    // ==================== 设置 / 链接对话框 ====================

    private void openSettings() {
        Dialog<ButtonType> dlg = new Dialog<>();
        dlg.setTitle("弹幕姬设置");
        dlg.setResizable(true);
        ButtonType saveType = new ButtonType("💾 保存", ButtonBar.ButtonData.OK_DONE);
        ButtonType cancelType = new ButtonType("取消", ButtonBar.ButtonData.CANCEL_CLOSE);
        dlg.getDialogPane().getButtonTypes().addAll(saveType, cancelType);

        TextField stats = new TextField(String.valueOf(settingInt("stats_interval", 1)));
        stats.setPrefWidth(120);
        TextField poll = new TextField(String.valueOf(settingInt("poll_interval", 60)));
        poll.setPrefWidth(120);
        GridPane grid = new GridPane();
        grid.setHgap(8);
        grid.setVgap(6);
        grid.add(new Label("统计数据采集间隔(秒):"), 0, 0);
        grid.add(stats, 1, 0);
        grid.add(new Label("轮询间隔(秒):"), 0, 1);
        grid.add(poll, 1, 1);

        CheckBox keepScroll = new CheckBox("保持滚动（强制最新）");
        keepScroll.setSelected(settingBool("keep_scroll", true));
        CheckBox autoSave = new CheckBox("自动导出回放包（断开连接 / 退出软件时打包 zip）");
        autoSave.setSelected(settingBool("auto_save_danmaku", true));

        TextArea remark = new TextArea(settingStr("remark_info", DEFAULT_REMARK));
        remark.setPrefRowCount(4);
        remark.setWrapText(true);

        Label autoDir = new Label("自动导出回放包到：" + danmakuDir()
                + "（断开连接 / 退出软件时自动打包 zip）");
        autoDir.getStyleClass().add("muted-label");
        autoDir.setWrapText(true);
        Label rtDir = new Label("实时记录（每条即写，切页/崩溃不丢）：" + danmakuDir().resolve("实时记录"));
        rtDir.getStyleClass().add("muted-label");
        rtDir.setWrapText(true);

        VBox box = new VBox(10,
                new Label("⚙ 弹幕姬设置"),
                grid,
                autoDir,
                rtDir,
                keepScroll,
                autoSave,
                new Label("📝 备注信息（显示在主播信息卡 UTC+8 时间下方，每行一条）:"),
                remark);
        box.setPadding(new Insets(6));
        dlg.getDialogPane().setContent(box);
        dlg.getDialogPane().setPrefWidth(520);

        Optional<ButtonType> result = dlg.showAndWait();
        if (result.isEmpty() || result.get() != saveType) return;
        int statsInterval;
        try {
            statsInterval = Math.max(1, Integer.parseInt(stats.getText().trim()));
        } catch (Exception e) {
            statsInterval = 1;
        }
        int pollInterval;
        try {
            pollInterval = Math.max(15, Integer.parseInt(poll.getText().trim()));
        } catch (Exception e) {
            pollInterval = 60;
        }
        setSetting("stats_interval", statsInterval);
        setSetting("poll_interval", pollInterval);
        settings.remove("monitor_interval");
        settings.remove("room_list");
        setSetting("keep_scroll", keepScroll.isSelected());
        setSetting("auto_save_danmaku", autoSave.isSelected());
        setSetting("remark_info", remark.getText() == null ? "" : remark.getText().strip());
        saveSettings();
        refreshRemark();
        setupPollTimer();
        toast("✅ 设置已保存");
    }

    private void openLinkPaste() {
        Dialog<Void> dlg = new Dialog<>();
        dlg.setTitle("🔗 转房间号");
        ButtonType parseType = new ButtonType("🔍 识别", ButtonBar.ButtonData.LEFT);
        ButtonType applyType = new ButtonType("✅ 填入房间号", ButtonBar.ButtonData.OK_DONE);
        ButtonType closeType = new ButtonType("关闭", ButtonBar.ButtonData.CANCEL_CLOSE);
        dlg.getDialogPane().getButtonTypes().addAll(parseType, applyType, closeType);

        Label tip = new Label("粘贴抖音 / B站直播间链接（支持带参数的推荐页、首页链接、短链），自动提取房间号：");
        tip.getStyleClass().add("muted-label");
        tip.setWrapText(true);
        TextArea edit = new TextArea();
        edit.setPromptText("https://live.douyin.com/?...&live_web_rid=519524225110\n"
                + "https://live.douyin.com/582775732015\n"
                + "https://v.douyin.com/iXXXXXXX/\n"
                + "https://b23.tv/XXXXXXX\n"
                + "https://live.bilibili.com/33989");
        edit.setPrefRowCount(5);
        edit.setWrapText(true);
        Label result = new Label("");
        result.setWrapText(true);
        VBox box = new VBox(8, tip, edit, result);
        box.setPadding(new Insets(6));
        dlg.getDialogPane().setContent(box);
        dlg.getDialogPane().setPrefWidth(560);

        Button parseBtn = (Button) dlg.getDialogPane().lookupButton(parseType);
        Button applyBtn = (Button) dlg.getDialogPane().lookupButton(applyType);
        parseBtn.addEventFilter(ActionEvent.ACTION, ev -> {
            ev.consume();
            resolveAsync(edit.getText(), room -> {
                if (room.isEmpty()) {
                    result.setText("❌ 未识别到房间号（检查链接是否完整）");
                    result.setStyle("-fx-text-fill:#c62828; -fx-font-weight:bold;");
                } else {
                    result.setText("✅ 识别到房间号：" + room);
                    result.setStyle("-fx-text-fill:#2e7d32; -fx-font-weight:bold;");
                }
            }, result);
        });
        applyBtn.addEventFilter(ActionEvent.ACTION, ev -> {
            ev.consume();
            resolveAsync(edit.getText(), room -> {
                if (room.isEmpty()) {
                    result.setText("❌ 未识别到房间号（检查链接是否完整）");
                    result.setStyle("-fx-text-fill:#c62828; -fx-font-weight:bold;");
                    return;
                }
                roomField.setText(room);
                toast("✅ 已填入房间号 " + room);
                dlg.close();
            }, result);
        });
        edit.requestFocus();
        dlg.showAndWait();
    }

    private void openRoomPicker() {
        cn.fmjj.toolbox.gui.dialogs.DouyinRoomPicker.show(
                content().getScene() == null ? null : content().getScene().getWindow(),
                settingStr("last_room", ""), room -> {
                    roomField.setText(room);
                    toast("✅ 已填入房间号 " + room + "，点「连接」开始");
                });
    }

    private void resolveAsync(String text, Consumer<String> onDone, Label resultLabel) {
        String direct = parseRoomLink(text);
        if (!direct.isEmpty() || !SHORT_LINK_RE.matcher(text == null ? "" : text).find()) {
            onDone.accept(direct);
            return;
        }
        resultLabel.setText("⏳ 正在解析短链...");
        Thread t = new Thread(() -> {
            String room = resolveRoomText(text);
            Platform.runLater(() -> onDone.accept(room));
        }, "douyin-link-resolve");
        t.setDaemon(true);
        t.start();
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() > max ? s.substring(0, max) : s;
    }
}
