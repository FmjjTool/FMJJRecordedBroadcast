package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.FiveSingScraper;
import cn.fmjj.toolbox.engine.music.KugouScraper;
import cn.fmjj.toolbox.engine.music.KuwoScraper;
import cn.fmjj.toolbox.engine.music.MusicSong;
import cn.fmjj.toolbox.engine.music.MusicTagEngine;
import cn.fmjj.toolbox.engine.music.MusicTagEngine.TagFields;
import cn.fmjj.toolbox.engine.music.NeteaseScraper;
import cn.fmjj.toolbox.engine.music.QQMusicScraper;
import cn.fmjj.toolbox.engine.net.HttpClient;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.ButtonBar;
import javafx.scene.control.ButtonType;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Dialog;
import javafx.scene.control.Label;
import javafx.scene.control.SplitPane;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.image.ImageView;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🏷 音乐标签（对齐 Python 版 music_tag.py 的结构）。
 *
 * <p>布局：工具栏（10 个按钮 + 伸展 + ❓）→ SplitPane（左：表单标题 + 12 字段 + 封面区；
 * 右：11 列文件表格）→ 状态栏（状态 + 伸展 + `过滤：` 220 宽）。
 *
 * <p>功能：添加文件/文件夹、表单编辑、保存选中/全部保存、清除标签/封面、移除、清空、过滤、
 * 网络匹配（五平台候选）、批量匹配（自动第一候选）、XLSX 表格匹配。
 */
public class MusicTagPage extends BasePage {

    private static final String[][] COLUMNS = {
            {"file", "文件名"}, {"dir", "目录"}, {"fmt", "格式"}, {"title", "标题"},
            {"artist", "艺术家"}, {"album", "专辑"}, {"albumartist", "专辑艺术家"},
            {"year", "年份"}, {"track", "音轨"}, {"disc", "碟"}, {"genre", "风格"}
    };

    /** 一行：路径 + 当前字段 + 是否修改。 */
    private static final class Row {
        final Path path;
        TagFields fields;
        boolean dirty;
        Row(Path path, TagFields fields) {
            this.path = path;
            this.fields = fields;
        }
        String fileName() { return path.getFileName().toString(); }
        String dir() {
            Path p = path.getParent();
            return p == null ? "" : p.toString();
        }
        String fmt() {
            String n = fileName().toLowerCase(Locale.ROOT);
            int dot = n.lastIndexOf('.');
            return dot > 0 ? n.substring(dot + 1) : "";
        }
    }

    private final TableView<Row> table = new TableView<>();
    private final List<Row> allRows = new ArrayList<>();
    private final Label formTitle = new Label("（未选择文件）");
    private final Map<String, TextField> edits = new LinkedHashMap<>();
    private final TextArea lyricsArea = new TextArea();
    private final Label coverLabel = new Label("无封面");
    private final Label coverInfo = new Label("");
    private final Label status = new Label(
            "添加音频文件开始编辑（支持 FLAC/MP3/M4A/OGG/WAV/WMA/AIFF 等格式）");
    private final TextField filterField = new TextField();
    private Path pendingCover;
    private final HttpClient http = new HttpClient();
    private final NeteaseScraper netease = new NeteaseScraper();
    private final QQMusicScraper qq = new QQMusicScraper();
    private final KuwoScraper kuwo = new KuwoScraper();
    private final KugouScraper kugou = new KugouScraper();
    private final FiveSingScraper fivesing = new FiveSingScraper();

    private Consumer<String> toast;

    @Override
    public String title() { return "🏷 音乐标签"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        VBox root = new VBox(6, buildToolbar(), buildBody(), buildStatusbar());
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(6));
        VBox.setVgrow(root.getChildren().get(1), Priority.ALWAYS);
        return root;
    }

    // ==================== 工具栏 ====================

    private Node buildToolbar() {
        HBox row = new HBox(6);
        row.setAlignment(Pos.CENTER_LEFT);
        row.getChildren().addAll(
                plainBtn("＋ 添加文件", "选择音频文件加入列表", this::addFiles),
                plainBtn("📂 添加文件夹", "扫描文件夹内全部音频", this::addFolder),
                colorBtn("💾 保存选中", "把左侧表单内容写入所有选中文件", "#2e7d32", this::saveSelected),
                colorBtn("💾 全部保存", "保存所有已修改（标记 ●）文件", "#2e7d32", this::saveAll),
                colorBtn("🌐 网络匹配", "按当前选中文件的文件名搜索网络标签，手动选择候选", "#1976d2", this::netMatch),
                colorBtn("⚡ 批量匹配", "对选中文件逐一自动匹配（取第一候选，覆盖文本标签）", "#1976d2", this::netBatch),
                colorBtn("📥 表格匹配", "导入 XLSX 表格批量填充标签", "#1976d2", this::tableMatch),
                plainBtn("✖ 移除选中", "从列表移除（不删文件）", this::removeSelected),
                plainBtn("🗑 清除标签", "清空选中文件的全部标签与封面", this::clearTags),
                plainBtn("🧹 清空列表", "", this::clearList));
        HBox spacer = new HBox();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        row.getChildren().add(spacer);
        Button help = new Button("❓ 帮助");
        help.setOnAction(e -> showHelp());
        row.getChildren().add(help);
        return row;
    }

    private Button plainBtn(String text, String tip, Runnable action) {
        Button b = new Button(text);
        b.setStyle("-fx-padding: 5 12 5 12;");
        if (tip != null && !tip.isEmpty()) b.setTooltip(new javafx.scene.control.Tooltip(tip));
        b.setOnAction(e -> action.run());
        return b;
    }

    private Button colorBtn(String text, String tip, String color, Runnable action) {
        Button b = new Button(text);
        b.setStyle("-fx-background-color: " + color + "; -fx-text-fill: white; -fx-padding: 5 14 5 14;");
        if (tip != null && !tip.isEmpty()) b.setTooltip(new javafx.scene.control.Tooltip(tip));
        b.setOnAction(e -> action.run());
        return b;
    }

    // ==================== 网络匹配 / 批量匹配 / 表格匹配 ====================

    private static String sourceKey(String label) {
        if (label == null) return "netease";
        if (label.startsWith("QQ")) return "qq";
        if (label.startsWith("酷我")) return "kuwo";
        if (label.startsWith("酷狗")) return "kugou";
        if (label.startsWith("5sing")) return "5sing";
        return "netease";
    }

    private List<MusicSong> searchPlatform(String key, String query) {
        return switch (key) {
            case "qq" -> qq.searchSong(query, 1, 10);
            case "kuwo" -> kuwo.searchSong(query, 1, 10);
            case "kugou" -> kugou.searchSong(query, 1, 10);
            case "5sing" -> fivesing.searchSong(query, 1, 10);
            default -> netease.searchSong(query, 1, 10);
        };
    }

    private String lyricsFor(MusicSong song) {
        try {
            return switch (song.platform()) {
                case "qq" -> qq.getLyric(song.songId());
                case "kuwo" -> kuwo.getLyric(song.songId());
                case "kugou" -> kugou.getLyric(song.songId());
                case "5sing" -> song.songId().contains("/") ? "" : kugou.getLyric(song.songId());
                default -> netease.getLyric(song.songId());
            };
        } catch (Exception e) {
            return "";
        }
    }

    private Path downloadCover(String url) {
        if (url == null || url.isBlank()) return null;
        try {
            var req = java.net.http.HttpRequest.newBuilder(java.net.URI.create(url))
                    .header("User-Agent", HttpClient.UA_CHROME)
                    .header("Referer", "https://music.163.com/")
                    .GET().build();
            byte[] data = http.jdkClient().send(req,
                    java.net.http.HttpResponse.BodyHandlers.ofByteArray()).body();
            if (data == null || data.length < 500) return null;
            Path tmp = Files.createTempFile("fmjj-tag-cover-", ".jpg");
            Files.write(tmp, data);
            tmp.toFile().deleteOnExit();
            return tmp;
        } catch (Exception e) {
            return null;
        }
    }

    private static String guessKeyword(Row row) {
        String title = row.fields.title() == null ? "" : row.fields.title().strip();
        String artist = row.fields.artist() == null ? "" : row.fields.artist().strip();
        if (!title.isEmpty()) return (artist + " " + title).strip();
        String name = row.fileName();
        int dot = name.lastIndexOf('.');
        if (dot > 0) name = name.substring(0, dot);
        name = name.replace('_', ' ').replaceAll("^\\s*\\d+[\\s.\\-]+", "").strip();
        return (artist + " " + name).strip();
    }

    private void applySong(Row row, MusicSong song) {
        if (row == null || song == null) return;
        if (!song.songName().isBlank()) edits.get("title").setText(song.songName());
        if (!song.singer().isBlank()) edits.get("artist").setText(song.singer());
        if (!song.albumName().isBlank()) edits.get("album").setText(song.albumName());
        markDirty("title");
        row.fields = formFields(row.fields.hasCover());
        status.setText("已填充「" + song.songName() + "」，正在获取歌词/封面…");
        Thread t = new Thread(() -> {
            String lrc = lyricsFor(song);
            Path cover = downloadCover(song.coverUrl());
            Platform.runLater(() -> {
                if (table.getSelectionModel().getSelectedItem() != row) return;
                if (!lrc.isBlank()) lyricsArea.setText(lrc);
                if (cover != null) applyPendingCover(cover);
                row.fields = formFields(row.fields.hasCover() || pendingCover != null);
                row.dirty = true;
                table.refresh();
                status.setText("网络匹配完成（记得保存）：" + song.songName());
                toast("已填充标签：" + song.songName());
            });
        }, "music-tag-net");
        t.setDaemon(true);
        t.start();
    }

    private void netMatch() {
        Row row = table.getSelectionModel().getSelectedItem();
        if (row == null) {
            toast("请先选中一个文件");
            return;
        }
        Dialog<MusicSong> dlg = new Dialog<>();
        dlg.setTitle("网络匹配标签");
        dlg.setResizable(true);
        dlg.getDialogPane().setPrefWidth(760);
        dlg.getDialogPane().setPrefHeight(480);
        ButtonType okType = new ButtonType("应用选中", ButtonBar.ButtonData.OK_DONE);
        dlg.getDialogPane().getButtonTypes().addAll(ButtonType.CANCEL, okType);

        ComboBox<String> source = new ComboBox<>();
        source.getItems().addAll("网易云", "QQ音乐", "酷我", "酷狗", "5sing");
        source.getSelectionModel().selectFirst();
        source.setPrefWidth(110);
        TextField query = new TextField(guessKeyword(row));
        query.setPrefWidth(360);
        Button search = new Button("🔍 搜索");
        HBox top = new HBox(6, new Label("来源:"), source, new Label("关键词:"), query, search);
        top.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(query, Priority.ALWAYS);

        TableView<MusicSong> result = new TableView<>();
        TableColumn<MusicSong, String> cTitle = new TableColumn<>("歌曲");
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().songName()));
        cTitle.setPrefWidth(280);
        TableColumn<MusicSong, String> cArtist = new TableColumn<>("歌手");
        cArtist.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().singer()));
        cArtist.setPrefWidth(180);
        TableColumn<MusicSong, String> cAlbum = new TableColumn<>("专辑");
        cAlbum.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().albumName()));
        cAlbum.setPrefWidth(180);
        TableColumn<MusicSong, String> cTime = new TableColumn<>("时长");
        cTime.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().durationText()));
        cTime.setPrefWidth(70);
        result.getColumns().addAll(List.of(cTitle, cArtist, cAlbum, cTime));
        result.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        Label info = new Label("选择来源后点「搜索」；双击候选可直接应用");
        info.setStyle("-fx-text-fill:#888;");
        VBox box = new VBox(8, top, info, result);
        box.setPadding(new Insets(10));
        VBox.setVgrow(result, Priority.ALWAYS);
        dlg.getDialogPane().setContent(box);
        dlg.setResultConverter(bt -> bt == okType
                ? result.getSelectionModel().getSelectedItem() : null);

        Runnable doSearch = () -> {
            String q = query.getText() == null ? "" : query.getText().strip();
            if (q.isEmpty()) {
                info.setText("请输入关键词");
                return;
            }
            search.setDisable(true);
            info.setText("⏳ 搜索中…");
            Thread t = new Thread(() -> {
                List<MusicSong> songs = searchPlatform(sourceKey(
                        source.getSelectionModel().getSelectedItem()), q);
                Platform.runLater(() -> {
                    search.setDisable(false);
                    result.setItems(FXCollections.observableArrayList(songs));
                    info.setText(songs.isEmpty() ? "（无结果，可换来源/关键词重试）"
                            : "找到 " + songs.size() + " 首，选中后点「应用选中」");
                });
            }, "music-tag-search");
            t.setDaemon(true);
            t.start();
        };
        search.setOnAction(e -> doSearch.run());
        query.setOnAction(e -> doSearch.run());
        result.setRowFactory(tv -> {
            javafx.scene.control.TableRow<MusicSong> tr = new javafx.scene.control.TableRow<>();
            tr.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !tr.isEmpty()) {
                    dlg.setResult(tr.getItem());
                    dlg.close();
                }
            });
            return tr;
        });
        dlg.setOnShown(e -> doSearch.run());
        dlg.showAndWait().ifPresent(song -> applySong(row, song));
    }

    private void netBatch() {
        List<Row> rows = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (rows.isEmpty()) {
            toast("请先选中要批量匹配的文件行");
            return;
        }
        Alert confirm = new Alert(Alert.AlertType.CONFIRMATION);
        confirm.setTitle("批量匹配");
        confirm.setHeaderText("对 " + rows.size() + " 个文件按文件名自动匹配");
        confirm.setContentText("取第一候选，覆盖标题/艺术家/专辑/歌词（封面与其它字段保持不变）。继续？");
        if (confirm.showAndWait().orElse(ButtonType.CANCEL) != ButtonType.OK) return;
        status.setText("批量匹配中（0/" + rows.size() + "）……");
        Thread t = new Thread(() -> {
            int ok = 0;
            List<String> failed = new ArrayList<>();
            for (int i = 0; i < rows.size(); i++) {
                Row r = rows.get(i);
                String q = guessKeyword(r);
                MusicSong song = null;
                for (String key : List.of("netease", "qq", "kugou", "kuwo", "5sing")) {
                    List<MusicSong> songs = searchPlatform(key, q);
                    if (!songs.isEmpty()) {
                        song = songs.get(0);
                        break;
                    }
                }
                if (song != null) {
                    TagFields old = r.fields;
                    String lrc = lyricsFor(song);
                    r.fields = new TagFields(
                            song.songName().isBlank() ? old.title() : song.songName(),
                            song.singer().isBlank() ? old.artist() : song.singer(),
                            song.albumName().isBlank() ? old.album() : song.albumName(),
                            old.year(), old.genre(), old.albumArtist(),
                            old.composer(), old.lyricist(), old.comment(),
                            old.track(), old.disc(),
                            lrc.isBlank() ? old.lyrics() : lrc, old.hasCover());
                    r.dirty = true;
                    ok++;
                } else {
                    failed.add(r.fileName());
                }
                int n = i + 1;
                int okN = ok;
                Platform.runLater(() -> status.setText("批量匹配中（" + n + "/" + rows.size()
                        + "），成功 " + okN));
            }
            int finalOk = ok;
            Platform.runLater(() -> {
                table.refresh();
                fillForm(table.getSelectionModel().getSelectedItem());
                status.setText("批量匹配完成：成功 " + finalOk + "/" + rows.size()
                        + (failed.isEmpty() ? "" : "，失败 " + failed.size())
                        + "（记得点「全部保存」）");
                toast("批量匹配成功 " + finalOk + " 个");
            });
        }, "music-tag-batch");
        t.setDaemon(true);
        t.start();
    }

    private void tableMatch() {
        if (allRows.isEmpty()) {
            toast("请先添加音频文件，再导入表格匹配");
            return;
        }
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择 XLSX 表格");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter(
                "表格 (*.xlsx)", "*.xlsx"));
        File file = chooser.showOpenDialog(table.getScene() == null
                ? null : table.getScene().getWindow());
        if (file == null) return;
        status.setText("正在读取表格…");
        Thread t = new Thread(() -> {
            int changed = 0;
            String error = "";
            try (var wb = org.apache.poi.ss.usermodel.WorkbookFactory.create(file)) {
                var sheet = wb.getSheetAt(0);
                var fmt = new org.apache.poi.ss.usermodel.DataFormatter();
                var header = sheet.getRow(0);
                if (header == null) throw new IllegalArgumentException("表格为空");
                Map<String, Integer> cols = new LinkedHashMap<>();
                for (int c = 0; c < header.getLastCellNum(); c++) {
                    String h = fmt.formatCellValue(header.getCell(c))
                            .replace(" ", "").toLowerCase(Locale.ROOT);
                    if (h.isEmpty()) continue;
                    if (h.equals("文件名") || h.equals("file") || h.equals("文件") || h.equals("name")) {
                        cols.put("file", c);
                    } else if (h.equals("标题") || h.equals("title") || h.equals("歌曲") || h.equals("歌名")) {
                        cols.put("title", c);
                    } else if (h.equals("艺术家") || h.equals("歌手") || h.equals("artist") || h.equals("singer")) {
                        cols.put("artist", c);
                    } else if (h.equals("专辑") || h.equals("album")) {
                        cols.put("album", c);
                    } else if (h.equals("年份") || h.equals("year")) {
                        cols.put("year", c);
                    } else if (h.equals("音轨") || h.equals("音轨号") || h.equals("track")) {
                        cols.put("track", c);
                    } else if (h.equals("碟") || h.equals("碟号") || h.equals("disc")) {
                        cols.put("disc", c);
                    } else if (h.equals("风格") || h.equals("genre")) {
                        cols.put("genre", c);
                    } else if (h.equals("专辑艺术家") || h.equals("albumartist")) {
                        cols.put("albumartist", c);
                    } else if (h.equals("歌词") || h.equals("lyrics")) {
                        cols.put("lyrics", c);
                    }
                }
                if (!cols.containsKey("file") && !cols.containsKey("title")) {
                    throw new IllegalArgumentException("表格缺少「文件名」或「标题」列");
                }
                for (int r = 1; r <= sheet.getLastRowNum(); r++) {
                    var row = sheet.getRow(r);
                    if (row == null) continue;
                    String fileName = cell(fmt, row, cols.get("file"));
                    String title = cell(fmt, row, cols.get("title"));
                    Row target = findRow(fileName, title);
                    if (target == null) continue;
                    TagFields old = target.fields;
                    target.fields = new TagFields(
                            pick(title, old.title()),
                            pick(cell(fmt, row, cols.get("artist")), old.artist()),
                            pick(cell(fmt, row, cols.get("album")), old.album()),
                            pick(cell(fmt, row, cols.get("year")), old.year()),
                            pick(cell(fmt, row, cols.get("genre")), old.genre()),
                            pick(cell(fmt, row, cols.get("albumartist")), old.albumArtist()),
                            old.composer(), old.lyricist(), old.comment(),
                            pick(cell(fmt, row, cols.get("track")), old.track()),
                            pick(cell(fmt, row, cols.get("disc")), old.disc()),
                            pick(cell(fmt, row, cols.get("lyrics")), old.lyrics()),
                            old.hasCover());
                    target.dirty = true;
                    changed++;
                }
            } catch (Exception e) {
                error = e.getMessage() == null ? e.toString() : e.getMessage();
            }
            int finalChanged = changed;
            String finalError = error;
            Platform.runLater(() -> {
                table.refresh();
                fillForm(table.getSelectionModel().getSelectedItem());
                if (finalError.isEmpty()) {
                    status.setText("表格匹配完成：更新 " + finalChanged + " 个文件（记得点「全部保存」）");
                    toast("表格匹配更新 " + finalChanged + " 个文件");
                } else {
                    status.setText("表格匹配失败：" + finalError);
                    toast("表格匹配失败：" + finalError);
                }
            });
        }, "music-tag-xlsx");
        t.setDaemon(true);
        t.start();
    }

    private Row findRow(String fileName, String title) {
        String fn = fileName == null ? "" : fileName.strip().toLowerCase(Locale.ROOT);
        String tt = title == null ? "" : title.strip().toLowerCase(Locale.ROOT);
        for (Row r : allRows) {
            String rf = r.fileName().toLowerCase(Locale.ROOT);
            if (!fn.isEmpty() && (rf.equals(fn) || rf.contains(fn))) return r;
            if (!tt.isEmpty()) {
                String rt = r.fields.title() == null ? "" : r.fields.title().strip()
                        .toLowerCase(Locale.ROOT);
                if (rt.equals(tt) || rf.contains(tt)) return r;
            }
        }
        return null;
    }

    private static String cell(org.apache.poi.ss.usermodel.DataFormatter fmt,
                               org.apache.poi.ss.usermodel.Row row, Integer col) {
        if (col == null) return "";
        var c = row.getCell(col);
        return c == null ? "" : fmt.formatCellValue(c).strip();
    }

    private static String pick(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private void showHelp() {
        javafx.scene.control.Alert a = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.INFORMATION);
        a.setTitle("音乐标签 · 功能介绍");
        a.setHeaderText("🏷 音频标签（Tag）查看与批量编辑");
        a.setContentText("""
                布局：左侧标签表单 + 右侧文件列表

                · 加文件：＋添加文件 / 📂添加文件夹（mp3/flac/m4a/ogg/wav/wma/aiff）
                · 表单 12 字段：标题/艺术家/专辑/年份/风格/专辑艺术家/作曲家/作词家/注释/音轨号/碟号
                  + 歌词（点「⛶ 展开编辑」看全文）+ 封面（更换/导出/移除）
                · 保存：💾 保存选中（表单写入选中文件）/ 💾 全部保存（所有标记 ● 的文件）
                · 修改过的文件标记 ●，避免误保存；纯本地编辑，不上传

                · 🌐 网络匹配：选择来源（网易云/QQ/酷我/酷狗/5sing）→ 搜索 → 选中候选应用
                  （标题/艺术家/专辑 + 歌词/封面；封面在保存选中时写入）
                · ⚡ 批量匹配：对选中文件按文件名自动取第一候选，覆盖文本标签与歌词
                · 📥 表格匹配：导入 XLSX（文件名/标题/艺术家/专辑/年份/音轨/碟/风格/专辑艺术家/歌词）
                """);
        a.showAndWait();
    }

    // ==================== 主体 ====================

    private Node buildBody() {
        SplitPane sp = new SplitPane(buildForm(), buildTable());
        sp.setDividerPositions(0.32);
        VBox.setVgrow(sp, Priority.ALWAYS);
        return sp;
    }

    private Node buildForm() {
        formTitle.setStyle("-fx-font-weight: bold; -fx-text-fill: #2f3440;");
        GridPane form = new GridPane();
        form.setHgap(6);
        form.setVgap(4);
        String[][] fields = {
                {"title", "标题："}, {"artist", "艺术家："}, {"album", "专辑："},
                {"year", "年份："}, {"genre", "风格："}, {"albumartist", "专辑艺术家："},
                {"composer", "作曲家："}, {"lyricist", "作词家："}, {"comment", "注释："}
        };
        int r = 0;
        for (String[] f : fields) {
            Label l = new Label(f[1]);
            l.setMinWidth(72);
            l.setAlignment(Pos.CENTER_RIGHT);
            TextField e = new TextField();
            e.textProperty().addListener((o, a, b) -> markDirty(f[0]));
            edits.put(f[0], e);
            form.add(l, 0, r);
            form.add(e, 1, r);
            GridPane.setHgrow(e, Priority.ALWAYS);
            r++;
        }
        // 音轨号 + 碟号 同一行
        Label numLabel = new Label("编号：");
        numLabel.setMinWidth(72);
        numLabel.setAlignment(Pos.CENTER_RIGHT);
        HBox numRow = new HBox(4);
        TextField track = new TextField();
        track.setPrefWidth(80);
        track.textProperty().addListener((o, a, b) -> markDirty("track"));
        edits.put("track", track);
        TextField disc = new TextField();
        disc.setPrefWidth(80);
        disc.textProperty().addListener((o, a, b) -> markDirty("disc"));
        edits.put("disc", disc);
        numRow.getChildren().addAll(new Label("音轨号："), track, new Label("碟号："), disc);
        form.add(numLabel, 0, r);
        form.add(numRow, 1, r);
        r++;

        // 歌词 + 展开按钮
        Label lyrLabel = new Label("歌词：");
        lyrLabel.setMinWidth(72);
        lyrLabel.setAlignment(Pos.TOP_RIGHT);
        lyricsArea.setPrefHeight(140);
        lyricsArea.setMaxHeight(140);
        lyricsArea.setWrapText(true);
        lyricsArea.textProperty().addListener((o, a, b) -> markDirty("lyrics"));
        Button expand = new Button("⛶\n展开\n编辑");
        expand.setMinWidth(52);
        expand.setMaxWidth(52);
        expand.setTooltip(new javafx.scene.control.Tooltip("打开二级窗口查看/编辑完整歌词"));
        expand.setOnAction(e -> editLyricsDialog());
        VBox lyrSide = new VBox(expand);
        HBox lyrRow = new HBox(4, lyricsArea, lyrSide);
        HBox.setHgrow(lyricsArea, Priority.ALWAYS);
        form.add(lyrLabel, 0, r);
        form.add(lyrRow, 1, r);

        // 封面区
        coverLabel.setMinSize(140, 140);
        coverLabel.setMaxSize(140, 140);
        coverLabel.setAlignment(Pos.CENTER);
        coverLabel.setStyle("-fx-border-color: #c3cad6; -fx-background-color: white;");
        coverInfo.setStyle("-fx-text-fill: #888;");
        Button setCover = new Button("🖼 更换封面");
        setCover.setOnAction(e -> pickCover());
        Button exportCover = new Button("💾 导出封面");
        exportCover.setOnAction(e -> toast("导出封面：请先用「更换封面」选择图片（读取原封面导出随下一轮）"));
        Button delCover = new Button("✖ 移除封面");
        delCover.setOnAction(e -> removeCover());
        VBox covSide = new VBox(4, coverInfo, setCover, exportCover, delCover);
        HBox covRow = new HBox(8, coverLabel, covSide);
        HBox.setHgrow(covSide, Priority.ALWAYS);

        VBox left = new VBox(6, formTitle, form, covRow);
        left.setPadding(new Insets(0, 6, 0, 0));
        return left;
    }

    private Node buildTable() {
        TableColumn<Row, String> cFile = new TableColumn<>(COLUMNS[0][1]);
        cFile.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fileName()));
        cFile.setPrefWidth(200);
        TableColumn<Row, String> cDir = new TableColumn<>(COLUMNS[1][1]);
        cDir.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().dir()));
        cDir.setPrefWidth(160);
        TableColumn<Row, String> cFmt = new TableColumn<>(COLUMNS[2][1]);
        cFmt.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fmt()));
        cFmt.setPrefWidth(60);
        TableColumn<Row, String> cTitle = new TableColumn<>(COLUMNS[3][1]);
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.title()));
        cTitle.setPrefWidth(220);
        TableColumn<Row, String> cArtist = new TableColumn<>(COLUMNS[4][1]);
        cArtist.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.artist()));
        cArtist.setPrefWidth(130);
        TableColumn<Row, String> cAlbum = new TableColumn<>(COLUMNS[5][1]);
        cAlbum.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.album()));
        cAlbum.setPrefWidth(140);
        TableColumn<Row, String> cAA = new TableColumn<>(COLUMNS[6][1]);
        cAA.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.albumArtist()));
        cAA.setPrefWidth(110);
        TableColumn<Row, String> cYear = new TableColumn<>(COLUMNS[7][1]);
        cYear.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.year()));
        cYear.setPrefWidth(60);
        TableColumn<Row, String> cTrack = new TableColumn<>(COLUMNS[8][1]);
        cTrack.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.track()));
        cTrack.setPrefWidth(50);
        TableColumn<Row, String> cDisc = new TableColumn<>(COLUMNS[9][1]);
        cDisc.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.disc()));
        cDisc.setPrefWidth(50);
        TableColumn<Row, String> cGenre = new TableColumn<>(COLUMNS[10][1]);
        cGenre.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().fields.genre()));
        cGenre.setPrefWidth(90);
        table.getColumns().addAll(List.of(cFile, cDir, cFmt, cTitle, cArtist, cAlbum,
                cAA, cYear, cTrack, cDisc, cGenre));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.getSelectionModel().setSelectionMode(javafx.scene.control.SelectionMode.MULTIPLE);
        table.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> fillForm(b));
        return table;
    }

    private Node buildStatusbar() {
        status.setStyle("-fx-text-fill: #666;");
        filterField.setPromptText("按文件名/标题/艺术家过滤");
        filterField.setPrefWidth(220);
        filterField.textProperty().addListener((o, a, b) -> applyFilter());
        HBox row = new HBox(6, status, spacer(), new Label("过滤："), filterField);
        row.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);
        return row;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    // ==================== 列表 / 表单 ====================

    private void addFiles() {
        FileChooser c = new FileChooser();
        c.setTitle("选择音频文件");
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("音频文件",
                "*.mp3", "*.flac", "*.m4a", "*.ogg", "*.oga", "*.wav", "*.wma", "*.aiff", "*.aif", "*.mp4"));
        List<File> picked = c.showOpenMultipleDialog(table.getScene() == null
                ? null : table.getScene().getWindow());
        if (picked == null) return;
        for (File f : picked) addPath(f.toPath());
        applyFilter();
    }

    private void addFolder() {
        DirectoryChooser c = new DirectoryChooser();
        c.setTitle("选择音频文件夹");
        File d = c.showDialog(table.getScene() == null ? null : table.getScene().getWindow());
        if (d == null) return;
        status.setText("扫描中…");
        javafx.concurrent.Task<List<Path>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<Path> call() throws Exception {
                try (Stream<Path> s = Files.walk(d.toPath())) {
                    return s.filter(Files::isRegularFile).filter(MusicTagEngine::isSupported).toList();
                }
            }
        };
        task.setOnSucceeded(e -> {
            for (Path p : task.getValue()) addPath(p);
            applyFilter();
        });
        task.setOnFailed(e -> status.setText("扫描失败：" + task.getException().getMessage()));
        new Thread(task, "tag-scan").start();
    }

    private void addPath(Path p) {
        if (allRows.stream().anyMatch(r -> r.path.equals(p))) return;
        allRows.add(new Row(p, MusicTagEngine.readFields(p)));
    }

    private void applyFilter() {
        String kw = filterField.getText() == null ? "" : filterField.getText().trim().toLowerCase(Locale.ROOT);
        List<Row> shown = new ArrayList<>();
        for (Row r : allRows) {
            if (kw.isEmpty()
                    || r.fileName().toLowerCase(Locale.ROOT).contains(kw)
                    || r.fields.title().toLowerCase(Locale.ROOT).contains(kw)
                    || r.fields.artist().toLowerCase(Locale.ROOT).contains(kw)) {
                shown.add(r);
            }
        }
        table.setItems(FXCollections.observableArrayList(shown));
        status.setText("共 " + allRows.size() + " 个文件"
                + (shown.size() != allRows.size() ? "（显示 " + shown.size() + "）" : ""));
    }

    private void fillForm(Row row) {
        if (row == null) {
            formTitle.setText("（未选择文件）");
            return;
        }
        formTitle.setText((row.dirty ? "● " : "") + row.fileName());
        TagFields f = row.fields;
        edits.get("title").setText(f.title());
        edits.get("artist").setText(f.artist());
        edits.get("album").setText(f.album());
        edits.get("year").setText(f.year());
        edits.get("genre").setText(f.genre());
        edits.get("albumartist").setText(f.albumArtist());
        edits.get("composer").setText(f.composer());
        edits.get("lyricist").setText(f.lyricist());
        edits.get("comment").setText(f.comment());
        edits.get("track").setText(f.track());
        edits.get("disc").setText(f.disc());
        lyricsArea.setText(f.lyrics());
        coverLabel.setText(f.hasCover() ? "有封面" : "无封面");
        coverLabel.setGraphic(null);
        coverInfo.setText(f.hasCover() ? "当前文件已有封面（更换后可写入）" : "");
        pendingCover = null;
    }

    private void markDirty(String key) {
        Row row = table.getSelectionModel().getSelectedItem();
        if (row == null) return;
        if (!row.dirty) {
            row.dirty = true;
            formTitle.setText("● " + row.fileName());
        }
    }

    private TagFields formFields(boolean hasCover) {
        return new TagFields(
                text("title"), text("artist"), text("album"), text("year"), text("genre"),
                text("albumartist"), text("composer"), text("lyricist"), text("comment"),
                text("track"), text("disc"), lyricsArea.getText(), hasCover);
    }

    private String text(String key) {
        TextField f = edits.get(key);
        return f == null || f.getText() == null ? "" : f.getText().trim();
    }

    private void editLyricsDialog() {
        TextArea area = new TextArea(lyricsArea.getText());
        area.setPrefSize(680, 520);
        area.setWrapText(true);
        javafx.scene.control.Dialog<Void> dlg = new javafx.scene.control.Dialog<>();
        dlg.setTitle("编辑歌词");
        dlg.getDialogPane().setContent(area);
        dlg.getDialogPane().getButtonTypes().addAll(
                javafx.scene.control.ButtonType.OK, javafx.scene.control.ButtonType.CANCEL);
        dlg.setResultConverter(bt -> {
            if (bt == javafx.scene.control.ButtonType.OK) lyricsArea.setText(area.getText());
            return null;
        });
        dlg.showAndWait();
    }

    // ==================== 保存 / 清理 ====================

    private void saveSelected() {
        List<Row> rows = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (rows.isEmpty()) { status.setText("请先选中文件"); return; }
        TagFields f = formFields(rows.get(0).fields.hasCover() || pendingCover != null);
        int ok = 0, fail = 0;
        for (Row r : rows) {
            try {
                MusicTagEngine.writeFields(r.path, f);
                if (pendingCover != null) MusicTagEngine.writeCover(r.path, pendingCover);
                r.fields = MusicTagEngine.readFields(r.path);
                r.dirty = false;
                ok++;
            } catch (Exception e) {
                fail++;
            }
        }
        pendingCover = null;
        table.refresh();
        status.setText("保存完成：成功 " + ok + "，失败 " + fail);
        toast("已写入标签：" + ok + " 个文件");
    }

    private void saveAll() {
        List<Row> dirty = allRows.stream().filter(r -> r.dirty).toList();
        if (dirty.isEmpty()) { status.setText("没有修改过的文件"); return; }
        Row current = table.getSelectionModel().getSelectedItem();
        int ok = 0, fail = 0;
        for (Row r : dirty) {
            try {
                boolean cover = r == current && pendingCover != null;
                MusicTagEngine.writeFields(r.path, cover ? withCover(r.fields) : r.fields);
                if (cover) MusicTagEngine.writeCover(r.path, pendingCover);
                r.fields = MusicTagEngine.readFields(r.path);
                r.dirty = false;
                ok++;
            } catch (Exception e) {
                fail++;
            }
        }
        pendingCover = null;
        table.refresh();
        status.setText("全部保存：成功 " + ok + "，失败 " + fail);
    }

    private static TagFields withCover(TagFields f) {
        return new TagFields(f.title(), f.artist(), f.album(), f.year(), f.genre(),
                f.albumArtist(), f.composer(), f.lyricist(), f.comment(),
                f.track(), f.disc(), f.lyrics(), true);
    }

    private void removeSelected() {
        var rows = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        allRows.removeAll(rows);
        applyFilter();
        status.setText("已从列表移除 " + rows.size() + " 个（文件未删除）");
    }

    private void clearTags() {
        var rows = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (rows.isEmpty()) { status.setText("请先选中文件"); return; }
        int ok = 0;
        for (Row r : rows) {
            try {
                MusicTagEngine.writeFields(r.path, TagFields.empty());
                MusicTagEngine.removeCover(r.path);
                r.fields = TagFields.empty();
                r.dirty = false;
                ok++;
            } catch (Exception ignored) {
            }
        }
        table.refresh();
        fillForm(table.getSelectionModel().getSelectedItem());
        status.setText("已清除标签：" + ok + " 个文件");
    }

    private void clearList() {
        allRows.clear();
        applyFilter();
        formTitle.setText("（未选择文件）");
        status.setText("列表已清空");
    }

    private void pickCover() {
        FileChooser c = new FileChooser();
        c.setTitle("选择封面图片");
        c.getExtensionFilters().add(new FileChooser.ExtensionFilter("图片", "*.jpg", "*.jpeg", "*.png"));
        File f = c.showOpenDialog(table.getScene() == null ? null : table.getScene().getWindow());
        if (f == null) return;
        applyPendingCover(f.toPath());
    }

    private void applyPendingCover(Path path) {
        pendingCover = path;
        try {
            var img = ImageTools.read(pendingCover);
            var scaled = ImageTools.scale(img, 140, 140);
            var wi = new javafx.scene.image.WritableImage(140, 140);
            var pw = wi.getPixelWriter();
            for (int y = 0; y < 140; y++) {
                for (int x = 0; x < 140; x++) pw.setArgb(x, y, scaled.getRGB(x, y));
            }
            coverLabel.setText("");
            coverLabel.setGraphic(new ImageView(wi));
            coverInfo.setText("待写入：" + path.getFileName());
        } catch (Exception e) {
            coverInfo.setText("读取封面失败：" + e.getMessage());
        }
    }

    private void removeCover() {
        Row row = table.getSelectionModel().getSelectedItem();
        if (row == null) { status.setText("请先选中文件"); return; }
        try {
            MusicTagEngine.removeCover(row.path);
            row.fields = MusicTagEngine.readFields(row.path);
            coverLabel.setText("无封面");
            coverLabel.setGraphic(null);
            coverInfo.setText("");
            status.setText("已移除封面：" + row.fileName());
        } catch (Exception e) {
            status.setText("移除封面失败：" + e.getMessage());
        }
    }
}
