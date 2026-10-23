package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.KuwoScraper;
import cn.fmjj.toolbox.engine.music.MusicDownloader;
import cn.fmjj.toolbox.engine.music.MusicSong;
import javafx.application.Platform;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.TextField;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.ToggleGroup;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 🎧 音乐下载工具箱（对齐 Python 版 netease.py 的结构）。
 *
 * <p>结构：标题(18px) + 伸展 + `❓ 使用说明`；平台下拉（6 平台） + 模式按钮组；
 * 动态输入区（search 模式＝歌手+关键词+🔍获取，其余＝单链接+解析）；
 * 白底带边框详情卡片区（伸展）；底部操作栏（☑全选/🔄反选/□全不选 + □自动换源 +
 * 音质 + 导出位置 + 📁 + `⬇ 下载选中`(绿) + 状态）。
 *
 * <p>当前可下载：**酷我**（搜索/下载/歌词已实现）；网易云/QQ/酷狗/5sing/概念版
 * 接口未移植，切到这些平台时明确提示，不假装可用。
 */
public class MusicSearchPage extends BasePage {

    /** 平台：key、显示名、支持的模式。 */
    private static final String[][] PLATFORMS = {
            {"netease", "🎧 网易云音乐", "search,song,playlist,album"},
            {"qq", "🐧 QQ音乐", "search,song,playlist"},
            {"kugou", "🐕 酷狗音乐", "search,song,playlist"},
            {"kuwo", "🐼 酷我音乐", "search,song,playlist"},
            {"5sing", "🎤 5sing音乐", "search,song,playlist"},
            {"kugou_lite", "🎶 酷狗概念版", "search,song"},
    };

    private static final Map<String, String> MODE_NAMES = new LinkedHashMap<>() {{
        put("search", "🎵 歌曲搜索");
        put("song", "🎶 单曲解析");
        put("playlist", "📋 歌单解析");
        put("album", "💿 专辑解析");
    }};

    /** 一条结果 + 勾选。 */
    private static final class Item {
        final MusicSong song;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);
        Item(MusicSong song) { this.song = song; }
    }

    private final ComboBox<String> platformBox = new ComboBox<>();
    private final HBox modeRow = new HBox(4);
    private final HBox inputRow = new HBox(8);
    private final VBox detailList = new VBox(2);
    private final ComboBox<String> qualityBox = new ComboBox<>(FXCollections.observableArrayList(
            "128kmp3", "320kmp3", "flac"));
    private final ComboBox<String> exportLocBox = new ComboBox<>(FXCollections.observableArrayList(
            "默认位置", "固定位置"));
    private final CheckBox fallbackCheck = new CheckBox("自动换源");
    private final Button downloadBtn = new Button("⬇ 下载选中");
    private final Label status = new Label("");

    private final TextField singerField = new TextField();
    private final TextField keywordField = new TextField();
    private final TextField linkField = new TextField();
    private final Button fetchBtn = new Button("🔍 获取");

    private String currentPlatform = "netease";
    private String currentMode = "search";
    private final List<Item> items = new ArrayList<>();

    private final KuwoScraper kuwo = new KuwoScraper();
    private final MusicDownloader downloader = new MusicDownloader(kuwo);

    private Consumer<String> toast;

    @Override
    public String title() { return "🎧 音乐下载"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("🎧 音乐下载工具箱");
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");
        Button help = new Button("❓ 使用说明");
        help.setStyle("-fx-border-color: #e57373; -fx-border-radius: 4; -fx-background-color: #fff5f5;");
        help.setOnAction(e -> showHelp());
        HBox titleRow = new HBox(8, pageTitle, spacer(), help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        for (String[] p : PLATFORMS) platformBox.getItems().add(p[1]);
        platformBox.getSelectionModel().selectFirst();
        platformBox.setPrefWidth(150);
        platformBox.setOnAction(e -> switchPlatform());

        HBox top = new HBox(8, platformBox, modeRow, spacer());
        top.setAlignment(Pos.CENTER_LEFT);

        qualityBox.getSelectionModel().selectFirst();
        exportLocBox.getSelectionModel().selectFirst();

        Button selAll = new Button("☑ 全选");
        selAll.setOnAction(e -> select("all"));
        Button selInvert = new Button("🔄 反选");
        selInvert.setOnAction(e -> select("invert"));
        Button selNone = new Button("□ 全不选");
        selNone.setOnAction(e -> select("none"));
        fallbackCheck.setTooltip(new javafx.scene.control.Tooltip(
                "下载失败或仅试听时自动到其他平台找同名曲（当前仅酷我可下载）"));
        Button pickDir = new Button("📁");
        pickDir.setMinSize(30, 26);
        pickDir.setMaxSize(30, 26);
        pickDir.setTooltip(new javafx.scene.control.Tooltip("选择固定导出位置"));
        pickDir.setOnAction(e -> toast("固定导出位置随下一轮补齐（当前用统一导出目录/音乐）"));
        downloadBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white; "
                + "-fx-padding: 5 14 5 14; -fx-background-radius: 4;");
        downloadBtn.setOnAction(e -> downloadSelected());
        status.setStyle("-fx-text-fill: #888;");

        HBox bottom = new HBox(6, selAll, selInvert, selNone, fallbackCheck,
                new Label("音质:"), qualityBox, new Label("导出:"), exportLocBox, pickDir,
                downloadBtn, status);
        bottom.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        ScrollPane detailScroll = new ScrollPane(detailList);
        detailScroll.setFitToWidth(true);
        detailScroll.setStyle("-fx-background-color: white; -fx-border-color: #d0d6df; "
                + "-fx-border-radius: 6; -fx-background-radius: 6;");
        VBox.setVgrow(detailScroll, Priority.ALWAYS);

        VBox v = new VBox(6, titleRow, top, inputRow, detailScroll, bottom);
        v.setPadding(new Insets(10));
        v.getStyleClass().add("page-wrapper");

        applyPlatform("netease");
        return v;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private void showHelp() {
        javafx.scene.control.Alert a = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.INFORMATION);
        a.setTitle("音乐下载工具箱 · 使用说明");
        a.setHeaderText("🎧 音乐下载工具箱");
        a.setContentText("""
                平台：网易云 / QQ音乐 / 酷狗 / 酷我 / 5sing / 酷狗概念版
                模式：歌曲搜索 / 单曲解析 / 歌单解析 / 专辑解析（按平台支持情况显示）

                ✅ 当前可下载：酷我（搜索 → 勾选 → ⬇ 下载选中，自动带同名 .lrc 歌词）
                ⚠️ 其他平台：接口未移植，切过去会明确提示，不会假装可用

                导出：默认位置 = 统一导出目录/音乐/{歌手}/
                音质：128kmp3 / 320kmp3 / flac
                """);
        a.showAndWait();
    }

    // ==================== 平台 / 模式切换 ====================

    private void switchPlatform() {
        int idx = platformBox.getSelectionModel().getSelectedIndex();
        if (idx < 0) return;
        applyPlatform(PLATFORMS[idx][0]);
    }

    private void applyPlatform(String key) {
        currentPlatform = key;
        String[] def = null;
        for (String[] p : PLATFORMS) if (p[0].equals(key)) def = p;
        if (def == null) return;

        String[] modes = def[2].split(",");
        modeRow.getChildren().clear();
        ToggleGroup group = new ToggleGroup();
        for (String m : modes) {
            ToggleButton btn = new ToggleButton(MODE_NAMES.getOrDefault(m, m));
            btn.setToggleGroup(group);
            btn.setUserData(m);
            btn.setStyle("-fx-font-size: 11px;");
            btn.setOnAction(e -> {
                currentMode = m;
                buildInputRow();
            });
            modeRow.getChildren().add(btn);
            if (modeRow.getChildren().size() == 1) {
                btn.setSelected(true);
                currentMode = m;
            }
        }
        buildInputRow();
        status.setText("当前平台：" + def[1] + ("kuwo".equals(key)
                ? "（可搜索与下载）" : "（接口未移植，仅界面占位）"));
    }

    private void buildInputRow() {
        inputRow.getChildren().clear();
        detailList.getChildren().clear();
        items.clear();

        if ("search".equals(currentMode)) {
            singerField.setPromptText("歌手（可空）");
            singerField.setPrefWidth(150);
            keywordField.setPromptText("关键词 / 歌名");
            fetchBtn.setText("🔍 获取");
            fetchBtn.setOnAction(e -> doSearch());
            keywordField.setOnAction(e -> doSearch());
            inputRow.getChildren().addAll(new Label("歌手:"), singerField,
                    new Label("关键词:"), keywordField, fetchBtn);
            HBox.setHgrow(keywordField, Priority.ALWAYS);
        } else {
            linkField.setPromptText(placeholderFor(currentPlatform, currentMode));
            fetchBtn.setText("🔍 解析");
            fetchBtn.setOnAction(e -> doParseLink());
            linkField.setOnAction(e -> doParseLink());
            inputRow.getChildren().addAll(linkField, fetchBtn);
            HBox.setHgrow(linkField, Priority.ALWAYS);
        }
    }

    private static String placeholderFor(String platform, String mode) {
        return switch (mode) {
            case "song" -> "歌曲链接或 ID";
            case "playlist" -> "歌单链接或 ID";
            case "album" -> "专辑链接 / ID / 专辑名";
            default -> "关键词";
        };
    }

    // ==================== 搜索 / 解析 ====================

    private void doSearch() {
        if (!"kuwo".equals(currentPlatform)) {
            setDetail("⚠️ " + platformLabel() + " 的搜索接口未移植\n\n"
                    + "当前可下载的平台：酷我。请在上方平台下拉里切到「🐼 酷我音乐」。");
            status.setText(platformLabel() + " 未实现");
            return;
        }
        String singer = singerField.getText() == null ? "" : singerField.getText().trim();
        String kw = keywordField.getText() == null ? "" : keywordField.getText().trim();
        String query = (singer.isEmpty() ? "" : singer + " ") + kw;
        if (query.isBlank()) {
            status.setText("请输入歌手或关键词");
            return;
        }
        fetchBtn.setDisable(true);
        setDetail("⏳ 搜索中：" + query);
        javafx.concurrent.Task<List<MusicSong>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<MusicSong> call() {
                return kuwo.searchSong(query, 1, 30);
            }
        };
        task.setOnSucceeded(e -> {
            fetchBtn.setDisable(false);
            var songs = task.getValue();
            if (songs.isEmpty()) {
                setDetail("（无结果）");
                status.setText("没有搜到：" + query);
                return;
            }
            items.clear();
            detailList.getChildren().clear();
            for (MusicSong s : songs) {
                Item it = new Item(s);
                it.checked.addListener((o, a, b) -> updateStatus());
                items.add(it);
                detailList.getChildren().add(card(it));
            }
            status.setText("找到 " + songs.size() + " 首（勾选后点「⬇ 下载选中」）");
        });
        task.setOnFailed(e -> {
            fetchBtn.setDisable(false);
            setDetail("❌ 搜索失败：" + task.getException().getMessage());
            status.setText("搜索失败");
        });
        new Thread(task, "music-search").start();
    }

    private void doParseLink() {
        setDetail("⚠️ " + platformLabel() + " 的「" + MODE_NAMES.get(currentMode) + "」未移植\n\n"
                + "当前可下载的平台：酷我（歌曲搜索）。");
        status.setText("该模式未实现");
    }

    private String platformLabel() {
        for (String[] p : PLATFORMS) if (p[0].equals(currentPlatform)) return p[1];
        return currentPlatform;
    }

    private void setDetail(String text) {
        detailList.getChildren().clear();
        Label l = new Label(text);
        l.setWrapText(true);
        l.setStyle("-fx-text-fill: #666; -fx-padding: 8;");
        detailList.getChildren().add(l);
    }

    private Node card(Item item) {
        MusicSong s = item.song;
        CheckBox cb = new CheckBox();
        cb.selectedProperty().bindBidirectional(item.checked);

        Label name = new Label(s.songName());
        name.setStyle("-fx-font-size: 12px; -fx-font-weight: bold;");
        name.setWrapText(true);
        Label meta = new Label(s.singer() + "　" + (s.albumName() == null ? "" : s.albumName())
                + "　" + s.durationText());
        meta.setStyle("-fx-font-size: 10px; -fx-text-fill: #888;");
        VBox text = new VBox(1, name, meta);
        HBox.setHgrow(text, Priority.ALWAYS);

        Button dl = new Button("⬇ 立即下载");
        dl.setStyle("-fx-font-size: 10px;");
        dl.setOnAction(e -> {
            item.checked.set(true);
            downloadSelected();
        });

        HBox row = new HBox(6, cb, text, dl);
        row.setAlignment(Pos.CENTER_LEFT);
        row.setPadding(new Insets(4, 6, 4, 6));
        row.setStyle("-fx-border-color: #f0f0f0; -fx-border-radius: 4;");
        return row;
    }

    private void select(String mode) {
        for (Item i : items) {
            i.checked.set(switch (mode) {
                case "all" -> true;
                case "none" -> false;
                default -> !i.checked.get();
            });
        }
        updateStatus();
    }

    private void updateStatus() {
        long n = items.stream().filter(i -> i.checked.get()).count();
        status.setText("共 " + items.size() + " 首 · 勾选 " + n + " 首");
    }

    // ==================== 下载 ====================

    private void downloadSelected() {
        List<Item> picked = items.stream().filter(i -> i.checked.get()).toList();
        if (picked.isEmpty()) {
            status.setText("请先勾选要下载的歌曲");
            return;
        }
        if (!"kuwo".equals(currentPlatform)) {
            status.setText(platformLabel() + " 下载未实现（当前仅酷我）");
            return;
        }
        downloadBtn.setDisable(true);
        String bitrate = qualityBox.getValue();
        Path dir = Settings.load().effectiveExportDir().resolve("音乐");
        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (int i = 0; i < picked.size(); i++) {
                    var song = picked.get(i).song;
                    int idx = i;
                    Platform.runLater(() -> status.setText("下载中… " + (idx + 1) + "/" + picked.size()
                            + "：" + song.songName()));
                    var r = downloader.download(song, dir, bitrate, p -> { }, () -> false);
                    if (r.success()) ok++; else fail++;
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            downloadBtn.setDisable(false);
            status.setText("下载完成：成功 " + r[0] + "，失败 " + r[1] + "（" + dir + "）");
            toast("音乐下载完成：" + r[0] + " 首");
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "music-download").start();
    }
}
