package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.music.KuwoScraper;
import cn.fmjj.toolbox.engine.music.MusicSong;
import javafx.application.Platform;
import javafx.beans.property.SimpleBooleanProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.TextField;
import javafx.scene.control.cell.CheckBoxListCell;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 🎵 音乐信息获取（对齐 Python 版 music_info.py 的五平台五列对比）。
 *
 * <p>结构：`🎵 音乐信息获取`(18px) → 搜索行（歌手 150 / 歌曲名 伸展 / `🔍 搜索五平台`）→
 * **五列**（酷狗 / QQ / 网易云 / 酷我 / 5sing，每列：平台名 + 全选/反选/清除 + 可勾选列表）→
 * 底部（`📥 获取选中歌曲详情并导出` + 状态）。
 *
 * <p>当前仅 **酷我** 有接口；其余四列显示「接口未移植」，不做假数据。
 */
public class MusicInfoPage extends BasePage {

    private static final String[][] PLATFORMS = {
            {"kugou", "酷狗音乐"}, {"qq", "QQ音乐"}, {"netease", "网易云音乐"},
            {"kuwo", "酷我音乐"}, {"5sing", "5sing音乐"},
    };

    /** 一行结果 + 勾选。 */
    private static final class Item {
        final MusicSong song;
        final SimpleBooleanProperty checked = new SimpleBooleanProperty(false);
        Item(MusicSong song) { this.song = song; }
        @Override public String toString() {
            return song.songName() + " - " + song.singer() + "（" + song.durationText() + "）";
        }
    }

    private final TextField singerField = new TextField();
    private final TextField songField = new TextField();
    private final Button searchBtn = new Button("🔍 搜索五平台");
    private final Button getBtn = new Button("📥 获取选中歌曲详情并导出");
    private final Label status = new Label("");
    private final Map<String, ListView<Item>> columns = new LinkedHashMap<>();

    private final KuwoScraper kuwo = new KuwoScraper();

    private Consumer<String> toast;

    @Override
    public String title() { return "🎵 音乐信息"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("🎵 音乐信息获取");
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        singerField.setPromptText("歌手（留空=封茗囧菌）");
        singerField.setPrefWidth(150);
        songField.setPromptText("歌曲名 / 专辑名");
        songField.setOnAction(e -> search());
        searchBtn.setOnAction(e -> search());
        HBox searchRow = new HBox(6, singerField, songField, searchBtn);
        searchRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(songField, Priority.ALWAYS);

        HBox cols = new HBox(6);
        for (String[] p : PLATFORMS) {
            cols.getChildren().add(buildColumn(p[0], p[1]));
        }
        for (Node n : cols.getChildren()) HBox.setHgrow(n, Priority.ALWAYS);
        VBox.setVgrow(cols, Priority.ALWAYS);

        getBtn.setOnAction(e -> exportSelected());
        status.setStyle("-fx-text-fill: #888;");
        HBox bottom = new HBox(8, getBtn, status);
        bottom.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        VBox root = new VBox(6, pageTitle, searchRow, cols, bottom);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(cols, Priority.ALWAYS);
        return root;
    }

    private Node buildColumn(String key, String name) {
        Label title = new Label(name);
        title.setStyle("-fx-font-weight: bold;");

        Button all = tinyBtn("全选", () -> select(key, "all"));
        Button invert = tinyBtn("反选", () -> select(key, "invert"));
        Button none = tinyBtn("清除", () -> select(key, "none"));
        HBox bar = new HBox(2, all, invert, none);
        bar.setAlignment(Pos.CENTER_LEFT);

        ListView<Item> list = new ListView<>();
        list.setCellFactory(CheckBoxListCell.forListView(i -> i.checked));
        list.setStyle("-fx-background-color: white; -fx-border-color: #d0d6df; -fx-border-radius: 4;");
        if (!"kuwo".equals(key)) {
            list.setPlaceholder(new Label("（" + name + " 接口未移植）"));
        } else {
            list.setPlaceholder(new Label("（搜索后显示）"));
        }
        columns.put(key, list);
        VBox.setVgrow(list, Priority.ALWAYS);

        VBox col = new VBox(4, title, bar, list);
        col.setPadding(new Insets(6));
        col.setStyle("-fx-border-color: #e0e4ea; -fx-border-radius: 4;");
        col.setPrefWidth(220);
        return col;
    }

    private static Button tinyBtn(String text, Runnable action) {
        Button b = new Button(text);
        b.setMinSize(40, 22);
        b.setMaxSize(40, 22);
        b.setStyle("-fx-font-size: 10px;");
        b.setOnAction(e -> action.run());
        return b;
    }

    private void select(String key, String mode) {
        ListView<Item> list = columns.get(key);
        if (list == null) return;
        for (Item it : list.getItems()) {
            it.checked.set(switch (mode) {
                case "all" -> true;
                case "none" -> false;
                default -> !it.checked.get();
            });
        }
        list.refresh();
    }

    // ==================== 搜索 ====================

    private void search() {
        String singer = singerField.getText() == null ? "" : singerField.getText().trim();
        if (singer.isEmpty()) singer = "封茗囧菌";
        String song = songField.getText() == null ? "" : songField.getText().trim();
        String query = (singer + " " + song).trim();

        searchBtn.setDisable(true);
        status.setText("搜索中：" + query + "（当前仅酷我可用）");
        String q = query;

        javafx.concurrent.Task<List<MusicSong>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<MusicSong> call() {
                return kuwo.searchSong(q, 1, 30);
            }
        };
        task.setOnSucceeded(e -> {
            searchBtn.setDisable(false);
            var songs = task.getValue();
            List<Item> items = new ArrayList<>();
            for (MusicSong s : songs) items.add(new Item(s));
            columns.get("kuwo").setItems(FXCollections.observableArrayList(items));
            // 其余四列显示明确提示
            for (String[] p : PLATFORMS) {
                if ("kuwo".equals(p[0])) continue;
                columns.get(p[0]).getItems().clear();
                columns.get(p[0]).setPlaceholder(new Label("（" + p[1] + " 接口未移植）"));
            }
            status.setText("酷我找到 " + songs.size() + " 首；其余 4 个平台接口未移植");
        });
        task.setOnFailed(e -> {
            searchBtn.setDisable(false);
            status.setText("搜索失败：" + task.getException().getMessage());
        });
        new Thread(task, "music-info-search").start();
    }

    // ==================== 导出 ====================

    private void exportSelected() {
        List<Item> picked = new ArrayList<>();
        for (ListView<Item> list : columns.values()) {
            for (Item it : list.getItems()) if (it.checked.get()) picked.add(it);
        }
        if (picked.isEmpty()) {
            status.setText("请先勾选歌曲");
            return;
        }
        Path dir = Settings.load().effectiveExportDir().resolve("音乐信息");
        try {
            Files.createDirectories(dir);
            String stamp = java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss")
                    .format(java.time.LocalDateTime.now());
            Path json = dir.resolve("音乐信息_" + stamp + ".json");
            StringBuilder sb = new StringBuilder("[\n");
            for (int i = 0; i < picked.size(); i++) {
                MusicSong s = picked.get(i).song;
                sb.append("  {\"platform\":\"").append(esc(s.platform()))
                  .append("\",\"songId\":\"").append(esc(s.songId()))
                  .append("\",\"songName\":\"").append(esc(s.songName()))
                  .append("\",\"singer\":\"").append(esc(s.singer()))
                  .append("\",\"album\":\"").append(esc(s.albumName()))
                  .append("\",\"duration\":").append(s.durationSeconds())
                  .append(",\"cover\":\"").append(esc(s.coverUrl()))
                  .append("\",\"pageUrl\":\"").append(esc(s.pageUrl()))
                  .append("\"}").append(i == picked.size() - 1 ? "" : ",").append("\n");
            }
            sb.append("]\n");
            Files.writeString(json, sb.toString(), StandardCharsets.UTF_8);

            Path csv = dir.resolve("音乐信息_" + stamp + ".csv");
            StringBuilder c = new StringBuilder("平台,歌曲ID,歌名,歌手,专辑,时长(秒),封面,链接\n");
            for (Item it : picked) {
                MusicSong s = it.song;
                c.append('"').append(s.platform()).append("\",\"")
                 .append(esc(s.songId())).append("\",\"")
                 .append(esc(s.songName()).replace("\"", "\"\"")).append("\",\"")
                 .append(esc(s.singer()).replace("\"", "\"\"")).append("\",\"")
                 .append(esc(s.albumName()).replace("\"", "\"\"")).append("\",")
                 .append(s.durationSeconds()).append(",\"")
                 .append(esc(s.coverUrl())).append("\",\"")
                 .append(esc(s.pageUrl())).append("\"\n");
            }
            Files.writeString(csv, "\uFEFF" + c, StandardCharsets.UTF_8);

            status.setText("已导出：" + json.getFileName() + " / " + csv.getFileName());
            toast("音乐信息已导出：" + picked.size() + " 首");
        } catch (Exception e) {
            status.setText("导出失败：" + e.getMessage());
        }
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }
}
