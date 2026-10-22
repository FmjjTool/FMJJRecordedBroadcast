package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.MusicTagEngine;
import cn.fmjj.toolbox.engine.music.MusicTagEngine.Tags;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;
import javafx.stage.Window;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🏷 音乐标签页：批量查看/编辑音频标签（对应 Python 版 music_tag.py 的本地编辑部分）。
 *
 * 已实现：扫描目录 → 表格展示 → 表单编辑 → 保存单文件 / 批量应用到选中 → 封面写入。
 * 未实现：xlsx 批量匹配、网络补全封面（原版右侧的「网络匹配」，随 M6 音乐搜索一起补）。
 */
public class MusicTagPage extends BasePage {

    private static final class Row {
        final Path file;
        String status = "";
        Tags tags;
        Row(Path file) { this.file = file; }
    }

    private final TextField dirField = new TextField();
    private final TableView<Row> table = new TableView<>();
    private final TextField titleField = new TextField();
    private final TextField artistField = new TextField();
    private final TextField albumField = new TextField();
    private final TextField yearField = new TextField();
    private final TextField trackField = new TextField();
    private final TextField genreField = new TextField();
    private final Label status = new Label("就绪");
    private Path coverImage;

    private Consumer<String> toast;

    @Override
    public String title() { return "🏷 音乐标签"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());

        HBox top = new HBox(8, new Label("目录"), dirField, browse, scan, status);
        top.setAlignment(Pos.CENTER_LEFT);
        top.getStyleClass().add("toolbar");
        HBox.setHgrow(dirField, Priority.ALWAYS);
        HBox.setHgrow(status, Priority.ALWAYS);

        TableColumn<Row, String> cName = new TableColumn<>("文件");
        cName.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().file.getFileName().toString()));
        cName.setPrefWidth(260);
        TableColumn<Row, String> cTitle = new TableColumn<>("标题");
        cTitle.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().tags.title()));
        cTitle.setPrefWidth(220);
        TableColumn<Row, String> cArtist = new TableColumn<>("艺术家");
        cArtist.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().tags.artist()));
        cArtist.setPrefWidth(140);
        TableColumn<Row, String> cAlbum = new TableColumn<>("专辑");
        cAlbum.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().tags.album()));
        cAlbum.setPrefWidth(160);
        TableColumn<Row, String> cYear = new TableColumn<>("年份");
        cYear.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().tags.year()));
        cYear.setPrefWidth(70);
        TableColumn<Row, String> cStatus = new TableColumn<>("状态");
        cStatus.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().status));
        cStatus.setPrefWidth(160);
        table.getColumns().addAll(List.of(cName, cTitle, cArtist, cAlbum, cYear, cStatus));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.getSelectionModel().setSelectionMode(javafx.scene.control.SelectionMode.MULTIPLE);
        table.getSelectionModel().selectedItemProperty().addListener((obs, old, now) -> fillForm(now));

        GridPane form = new GridPane();
        form.setHgap(8);
        form.setVgap(8);
        form.add(new Label("标题"), 0, 0);
        form.add(titleField, 1, 0);
        form.add(new Label("艺术家"), 2, 0);
        form.add(artistField, 3, 0);
        form.add(new Label("专辑"), 0, 1);
        form.add(albumField, 1, 1);
        form.add(new Label("年份"), 2, 1);
        form.add(yearField, 3, 1);
        form.add(new Label("音轨"), 0, 2);
        form.add(trackField, 1, 2);
        form.add(new Label("流派"), 2, 2);
        form.add(genreField, 3, 2);
        titleField.setPrefWidth(220);
        artistField.setPrefWidth(160);
        albumField.setPrefWidth(220);
        yearField.setPrefWidth(80);
        trackField.setPrefWidth(80);
        genreField.setPrefWidth(160);

        Button save = new Button("💾 保存到选中文件");
        save.setDefaultButton(true);
        save.setOnAction(e -> saveSelected());
        Button applyAll = new Button("📋 应用到全部选中");
        applyAll.setOnAction(e -> applyToAll());
        Button pickCover = new Button("🖼 选择封面…");
        pickCover.setOnAction(e -> pickCover());
        Button clearCover = new Button("🗑 移除封面");
        clearCover.setOnAction(e -> clearCover());
        HBox actions = new HBox(10, save, applyAll, pickCover, clearCover);
        actions.setAlignment(Pos.CENTER_LEFT);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("扫描目录 → 选中文件在下方编辑 → 保存。留空的字段会被清除；支持 mp3/flac/ogg/m4a/wav/wma/aiff");
        hint.getStyleClass().add("stub-desc");
        hint.setWrapText(true);

        VBox root = new VBox(10, pageTitle, hint, top, table, form, actions);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        dirField.setText(Settings.load().effectiveWorkDir().toString());
        return root;
    }

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void chooseDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择音乐目录");
        File d = chooser.showDialog(owner());
        if (d != null) {
            dirField.setText(d.getAbsolutePath());
            scan();
        }
    }

    private void scan() {
        String dir = dirField.getText().trim();
        if (dir.isEmpty()) {
            status.setText("请先选择目录");
            return;
        }
        List<Row> rows = new ArrayList<>();
        try (Stream<Path> s = Files.walk(Paths.get(dir))) {
            s.filter(Files::isRegularFile)
                    .filter(MusicTagEngine::isSupported)
                    .sorted()
                    .forEach(p -> {
                        Row r = new Row(p);
                        r.tags = MusicTagEngine.read(p);
                        rows.add(r);
                    });
        } catch (IOException e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        table.getItems().setAll(rows);
        status.setText("扫描到 " + rows.size() + " 个音频文件");
    }

    private void fillForm(Row row) {
        if (row == null) return;
        titleField.setText(row.tags.title());
        artistField.setText(row.tags.artist());
        albumField.setText(row.tags.album());
        yearField.setText(row.tags.year());
        trackField.setText(row.tags.track());
        genreField.setText(row.tags.genre());
        status.setText("封面：" + (row.tags.hasCover() ? "有" : "无"));
    }

    private Tags formTags(boolean keepCover) {
        return new Tags(titleField.getText(), artistField.getText(), albumField.getText(),
                yearField.getText(), trackField.getText(), genreField.getText(), keepCover);
    }

    private void saveSelected() {
        var selected = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (selected.isEmpty()) {
            status.setText("请先选中文件");
            return;
        }
        int ok = 0, fail = 0;
        for (Row row : selected) {
            try {
                MusicTagEngine.write(row.file, formTags(row.tags.hasCover()));
                if (coverImage != null) MusicTagEngine.writeCover(row.file, coverImage);
                row.tags = MusicTagEngine.read(row.file);
                row.status = "✅ 已保存";
                ok++;
            } catch (Exception e) {
                row.status = "❌ " + e.getMessage();
                fail++;
            }
        }
        table.refresh();
        status.setText("保存完成：成功 " + ok + "，失败 " + fail);
        if (ok > 0) toast("标签已保存：" + ok + " 个文件");
    }

    /** 把表单值应用到全部选中（各自保留原封面）。 */
    private void applyToAll() {
        var selected = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (selected.isEmpty()) {
            status.setText("请先选中文件");
            return;
        }
        int ok = 0, fail = 0;
        for (Row row : selected) {
            try {
                MusicTagEngine.write(row.file, formTags(row.tags.hasCover()));
                row.tags = MusicTagEngine.read(row.file);
                row.status = "✅ 批量写入";
                ok++;
            } catch (Exception e) {
                row.status = "❌ " + e.getMessage();
                fail++;
            }
        }
        table.refresh();
        status.setText("批量应用完成：成功 " + ok + "，失败 " + fail);
    }

    private void pickCover() {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择封面图片");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("图片", "*.jpg", "*.jpeg", "*.png"));
        File f = chooser.showOpenDialog(owner());
        if (f != null) {
            coverImage = f.toPath();
            status.setText("封面已选择：" + f.getName() + "（保存时写入）");
        }
    }

    private void clearCover() {
        var selected = new ArrayList<>(table.getSelectionModel().getSelectedItems());
        if (selected.isEmpty()) {
            status.setText("请先选中文件");
            return;
        }
        int ok = 0;
        for (Row row : selected) {
            try {
                MusicTagEngine.removeCover(row.file);
                row.tags = MusicTagEngine.read(row.file);
                row.status = "✅ 封面已移除";
                ok++;
            } catch (Exception e) {
                row.status = "❌ " + e.getMessage();
            }
        }
        table.refresh();
        status.setText("移除封面：" + ok + " 个文件");
    }
}
