package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.download.FileDownloader;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.KuwoScraper;
import cn.fmjj.toolbox.engine.music.MusicDownloader;
import cn.fmjj.toolbox.engine.music.MusicSong;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
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
 * 🎵 音乐搜索下载页（酷我）。
 * 对应 Python 版 music_tracker / music 页的搜索+下载核心链路。
 *
 * 当前平台：酷我（接口无需签名，可直连）；网易云/QQ/酷狗/5sing 随 M6 后续。
 */
public class MusicSearchPage extends BasePage {

    private final TextField keywordField = new TextField();
    private final ComboBox<String> bitrateBox = new ComboBox<>(
            FXCollections.observableArrayList("128kmp3", "320kmp3", "flac"));
    private final TableView<MusicSong> table = new TableView<>();
    private final Label status = new Label("就绪");
    private final ProgressBar progress = new ProgressBar(0);
    private final Button searchBtn = new Button("🔍 搜索");
    private final Button downloadBtn = new Button("⬇ 下载选中");
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);

    private final KuwoScraper kuwo = new KuwoScraper();
    private final MusicDownloader downloader = new MusicDownloader(kuwo);

    private Consumer<String> toast;

    @Override
    public String title() { return "🎵 音乐搜索下载"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        keywordField.setPromptText("歌名 或 歌手 歌名（酷我）");
        keywordField.setOnAction(e -> search());
        bitrateBox.getSelectionModel().select(0);
        progress.setPrefWidth(180);
        downloadBtn.setDisable(true);

        searchBtn.setOnAction(e -> search());
        downloadBtn.setOnAction(e -> downloadSelected());
        Button openDir = new Button("📁 打开音乐目录");
        openDir.setOnAction(e -> AudioEngine.openInExplorer(musicDir()));

        HBox top = new HBox(8, new Label("关键词"), keywordField, searchBtn,
                new Label("音质"), bitrateBox, downloadBtn, openDir, progress, status);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(keywordField, Priority.ALWAYS);
        HBox.setHgrow(status, Priority.ALWAYS);

        TableColumn<MusicSong, String> cName = new TableColumn<>("歌名");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().songName()));
        cName.setPrefWidth(300);
        TableColumn<MusicSong, String> cSinger = new TableColumn<>("歌手");
        cSinger.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().singer()));
        cSinger.setPrefWidth(180);
        TableColumn<MusicSong, String> cAlbum = new TableColumn<>("专辑");
        cAlbum.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().albumName()));
        cAlbum.setPrefWidth(200);
        TableColumn<MusicSong, String> cDur = new TableColumn<>("时长");
        cDur.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().durationText()));
        cDur.setPrefWidth(80);
        TableColumn<MusicSong, String> cId = new TableColumn<>("ID");
        cId.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().songId()));
        cId.setPrefWidth(110);
        table.getColumns().addAll(List.of(cName, cSinger, cAlbum, cDur, cId));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.getSelectionModel().setSelectionMode(javafx.scene.control.SelectionMode.MULTIPLE);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("酷我搜索（双搜策略）→ 选音质 → 下载到「导出目录/音乐/歌手/」，自动带同名 .lrc 歌词；"
                + "网易云/QQ/酷狗/5sing 随 M6 后续");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);
        return root;
    }

    private Path musicDir() {
        return Settings.load().effectiveExportDir().resolve("音乐");
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void search() {
        String kw = keywordField.getText() == null ? "" : keywordField.getText().trim();
        if (kw.isEmpty()) {
            status.setText("请输入关键词");
            return;
        }
        searchBtn.setDisable(true);
        status.setText("搜索中…");
        javafx.concurrent.Task<List<MusicSong>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<MusicSong> call() {
                return kuwo.searchSong(kw, 1, 30);
            }
        };
        task.setOnSucceeded(e -> {
            searchBtn.setDisable(false);
            List<MusicSong> songs = task.getValue();
            table.setItems(FXCollections.observableArrayList(songs));
            downloadBtn.setDisable(songs.isEmpty());
            status.setText("找到 " + songs.size() + " 首");
        });
        task.setOnFailed(e -> {
            searchBtn.setDisable(false);
            status.setText("搜索失败：" + task.getException().getMessage());
        });
        new Thread(task, "kuwo-search").start();
    }

    private void downloadSelected() {
        List<MusicSong> selected = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (selected.isEmpty()) {
            status.setText("请先选中歌曲（可多选）");
            return;
        }
        stopFlag.set(false);
        downloadBtn.setDisable(true);
        String bitrate = bitrateBox.getValue();
        Path dir = musicDir();

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (int i = 0; i < selected.size(); i++) {
                    if (stopFlag.get()) break;
                    MusicSong song = selected.get(i);
                    int idx = i;
                    Platform.runLater(() -> status.setText("下载中… " + (idx + 1) + "/" + selected.size()
                            + "：" + song.songName()));
                    var r = downloader.download(song, dir, bitrate, p -> {
                        if (p.total() > 0) Platform.runLater(() -> progress.setProgress(p.ratio()));
                    }, stopFlag::get);
                    if (r.success()) ok++;
                    else fail++;
                    Platform.runLater(() -> progress.setProgress((double) (idx + 1) / selected.size()));
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            downloadBtn.setDisable(false);
            progress.setProgress(1);
            status.setText("下载完成：成功 " + r[0] + "，失败 " + r[1] + "（" + dir + "）");
            toast("音乐下载完成：" + r[0] + " 首");
        });
        task.setOnFailed(e -> {
            downloadBtn.setDisable(false);
            status.setText("下载异常：" + task.getException().getMessage());
        });
        new Thread(task, "kuwo-download").start();
    }
}
