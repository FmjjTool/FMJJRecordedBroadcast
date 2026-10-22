package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
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
import javafx.scene.control.TextField;
import javafx.scene.input.MouseButton;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.Window;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 📅 日历浏览页：按 年 → 月 → 日 浏览录播目录（YYYYMMDD 文件夹）。
 * 移植自 Python 版 gui_qt/pages/calendar.py 的三级下钻视图（简化为下拉 + 表格）。
 */
public class CalendarPage extends BasePage {

    private record Row(String day, int files, long bytes, Path dir) {}

    private final TextField rootField = new TextField();
    private final ComboBox<String> yearBox = new ComboBox<>();
    private final ComboBox<String> monthBox = new ComboBox<>();
    private final TableView<Row> table = new TableView<>();
    private final Label status = new Label("就绪");

    /** 日期文件夹全量：YYYYMMDD → Path。 */
    private final TreeMap<String, Path> dayDirs = new TreeMap<>();

    private Consumer<String> toast;

    @Override
    public String title() { return "📅 日历浏览"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        Button browse = new Button("📂 浏览");
        browse.setOnAction(e -> chooseDir());
        Button scan = new Button("🔍 扫描");
        scan.setOnAction(e -> scan());
        Button open = new Button("📁 打开选中日期");
        open.setOnAction(e -> openSelected());

        HBox top = new HBox(8, new Label("录播目录"), rootField, browse, scan);
        top.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(rootField, Priority.ALWAYS);

        yearBox.setOnAction(e -> refreshMonths());
        monthBox.setOnAction(e -> refreshDays());
        HBox filters = new HBox(8, new Label("年份"), yearBox, new Label("月份"), monthBox, open);
        filters.setAlignment(Pos.CENTER_LEFT);

        TableColumn<Row, String> cDay = new TableColumn<>("日期");
        cDay.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().day()));
        TableColumn<Row, String> cFiles = new TableColumn<>("文件数");
        cFiles.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().files())));
        TableColumn<Row, String> cSize = new TableColumn<>("大小");
        cSize.setCellValueFactory(d -> new SimpleStringProperty(AudioEngine.formatSize(d.getValue().bytes())));
        table.getColumns().addAll(List.of(cDay, cFiles, cSize));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.setOnMouseClicked(e -> {
            if (e.getButton() == MouseButton.PRIMARY && e.getClickCount() == 2) openSelected();
        });

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("扫描录播目录下的 YYYYMMDD 日期文件夹，按年/月筛选；双击行直接打开文件夹");
        hint.getStyleClass().add("stub-desc");

        VBox root = new VBox(10, pageTitle, hint, top, filters, table, status);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(table, Priority.ALWAYS);

        rootField.setText(Settings.load().effectiveWorkDir().toString());
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
        chooser.setTitle("选择录播目录");
        File d = chooser.showDialog(owner());
        if (d != null) {
            rootField.setText(d.getAbsolutePath());
            scan();
        }
    }

    private void scan() {
        String dir = rootField.getText().trim();
        if (dir.isEmpty()) {
            status.setText("请先选择目录");
            return;
        }
        dayDirs.clear();
        try (Stream<Path> s = Files.list(Paths.get(dir))) {
            for (Path p : s.filter(Files::isDirectory).toList()) {
                String name = p.getFileName().toString();
                if (name.matches("^20\\d{6}$")) dayDirs.put(name, p);
            }
        } catch (IOException e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        TreeSet<String> years = new TreeSet<>();
        for (String day : dayDirs.keySet()) years.add(day.substring(0, 4));
        yearBox.setItems(FXCollections.observableArrayList(years));
        if (!years.isEmpty()) yearBox.getSelectionModel().selectLast();
        refreshMonths();
        status.setText("共 " + dayDirs.size() + " 个日期文件夹");
    }

    private void refreshMonths() {
        String year = yearBox.getValue();
        TreeSet<String> months = new TreeSet<>();
        if (year != null) {
            for (String day : dayDirs.keySet()) {
                if (day.startsWith(year)) months.add(day.substring(4, 6));
            }
        }
        monthBox.setItems(FXCollections.observableArrayList(months));
        if (!months.isEmpty()) monthBox.getSelectionModel().selectLast();
        refreshDays();
    }

    private void refreshDays() {
        String year = yearBox.getValue();
        String month = monthBox.getValue();
        List<Row> rows = new ArrayList<>();
        if (year != null && month != null) {
            String prefix = year + month;
            for (var e : dayDirs.entrySet()) {
                if (!e.getKey().startsWith(prefix)) continue;
                int files = 0;
                long bytes = 0;
                try (Stream<Path> s = Files.walk(e.getValue())) {
                    for (Path f : s.filter(Files::isRegularFile).toList()) {
                        files++;
                        bytes += AudioEngine.sizeOf(f);
                    }
                } catch (IOException ignored) {
                }
                String day = e.getKey().substring(6);
                rows.add(new Row(year + "-" + month + "-" + day, files, bytes, e.getValue()));
            }
        }
        table.getItems().setAll(rows);
    }

    private void openSelected() {
        Row row = table.getSelectionModel().getSelectedItem();
        if (row == null) {
            status.setText("请先选中一行");
            return;
        }
        AudioEngine.openInExplorer(row.dir());
        toast("已打开：" + row.dir());
    }
}
