package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.image.ImageTools;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.MusicTagEngine;
import cn.fmjj.toolbox.engine.music.MusicTagEngine.TagFields;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
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
 * <p>功能：添加文件/文件夹、表单编辑、保存选中/全部保存、清除标签、移除、清空、过滤。
 * **网络匹配 / 批量匹配 / 表格匹配**（需五大音源与 XLSX 解析）暂未实现，点击会明确提示。
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
                colorBtn("🌐 网络匹配", "按文件名搜索网络标签", "#1976d2", () -> notImplemented("网络匹配")),
                colorBtn("⚡ 批量匹配", "对选中文件逐一自动匹配", "#1976d2", () -> notImplemented("批量匹配")),
                colorBtn("📥 表格匹配", "导入 XLSX 批量填充标签", "#1976d2", () -> notImplemented("表格匹配")),
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

    private void notImplemented(String what) {
        status.setText(what + " 未实现（需五大音源接口 / XLSX 解析，随 M6 后续）");
        toast(what + " 暂未实现");
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

                ⚠️ 网络匹配 / 批量匹配 / 表格匹配：需五大音源接口与 XLSX 解析，暂未实现
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
        int ok = 0, fail = 0;
        for (Row r : dirty) {
            try {
                MusicTagEngine.writeFields(r.path, r.fields);
                r.fields = MusicTagEngine.readFields(r.path);
                r.dirty = false;
                ok++;
            } catch (Exception e) {
                fail++;
            }
        }
        table.refresh();
        status.setText("全部保存：成功 " + ok + "，失败 " + fail);
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
        pendingCover = f.toPath();
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
            coverInfo.setText("待写入：" + f.getName());
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
