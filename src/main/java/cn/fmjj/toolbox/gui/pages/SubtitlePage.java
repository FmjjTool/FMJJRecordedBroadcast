package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.engine.subtitle.SubtitleEngine;
import cn.fmjj.toolbox.engine.subtitle.SubtitleEngine.ConvertResult;
import cn.fmjj.toolbox.engine.subtitle.SubtitleEngine.OutputFile;
import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.ProgressIndicator;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;
import javafx.stage.Window;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * 📝 字幕转换页。移植自 Python 版 gui_qt/pages/subtitle.py（功能对齐版）。
 *
 * 输入：.bcc / .json(BCC) / .ass / .srt / .txt
 * 输出：纯文字.txt / 带时间轴.txt / .lrc / .ass / .srt（默认全选）
 */
public class SubtitlePage extends BasePage {

    private final SubtitleEngine engine = new SubtitleEngine();

    private final ObservableList<String> files = FXCollections.observableArrayList();
    private final ListView<String> listView = new ListView<>(files);

    private final CheckBox cbText = new CheckBox("纯文字");
    private final CheckBox cbTimed = new CheckBox("带时间轴");
    private final CheckBox cbLrc = new CheckBox("LRC");
    private final CheckBox cbAss = new CheckBox("ASS");
    private final CheckBox cbSrt = new CheckBox("SRT");

    private final ComboBox<String> nameMode = new ComboBox<>(
            FXCollections.observableArrayList("保留原文件名", "加日期后缀"));
    private final TextField outDir = new TextField();
    private final Label status = new Label("就绪");
    private final ProgressIndicator progress = new ProgressIndicator();

    private Consumer<String> toast; // 延迟绑定（页面构建时主窗口已存在）

    @Override
    public String title() { return "📝 字幕转换"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        cbText.setSelected(true);
        cbTimed.setSelected(true);
        cbLrc.setSelected(true);
        cbAss.setSelected(true);
        cbSrt.setSelected(true);
        nameMode.getSelectionModel().select(0);
        progress.setVisible(false);
        progress.setPrefSize(18, 18);

        Path defaultOut = AppPaths.downloadDir().resolve("字幕转换");
        outDir.setText(defaultOut.toString());
        outDir.setEditable(false);

        Button addBtn = new Button("＋ 添加文件");
        addBtn.setOnAction(e -> chooseFiles());
        Button removeBtn = new Button("－ 移除选中");
        removeBtn.setOnAction(e -> {
            String sel = listView.getSelectionModel().getSelectedItem();
            if (sel != null) files.remove(sel);
        });
        Button clearBtn = new Button("🗑 清空");
        clearBtn.setOnAction(e -> files.clear());
        Button outBtn = new Button("📂 浏览");
        outBtn.setOnAction(e -> chooseOutDir());

        HBox toolbar = new HBox(8, addBtn, removeBtn, clearBtn);
        toolbar.setAlignment(Pos.CENTER_LEFT);
        toolbar.getStyleClass().add("toolbar");

        HBox outRow = new HBox(8, new Label("导出目录"), outDir, outBtn);
        outRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(outDir, Priority.ALWAYS);

        HBox fmtRow = new HBox(10, new Label("输出格式"),
                cbText, cbTimed, cbLrc, cbAss, cbSrt, new Label("命名"), nameMode);
        fmtRow.setAlignment(Pos.CENTER_LEFT);

        Button convertBtn = new Button("▶ 开始转换");
        convertBtn.setDefaultButton(true);
        convertBtn.setOnAction(e -> startConvert());

        HBox actionRow = new HBox(10, convertBtn, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        listView.setPlaceholder(new Label("尚未添加字幕文件（支持 .bcc/.json/.ass/.srt/.txt）"));

        VBox root = new VBox(12,
                toolbar, listView, outRow, fmtRow, actionRow);
        VBox.setVgrow(listView, Priority.ALWAYS);
        root.getStyleClass().add("page-root");
        root.setPadding(new Insets(16));

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        VBox wrapper = new VBox(10, pageTitle, root);
        wrapper.getStyleClass().add("page-wrapper");
        return wrapper;
    }

    @Override
    public void onReturnPressed() { startConvert(); }

    // ==================== 交互 ====================

    private Window owner() {
        return listView.getScene() == null ? null : listView.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void chooseFiles() {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择字幕文件");
        chooser.getExtensionFilters().addAll(
                new FileChooser.ExtensionFilter("字幕文件", "*.bcc", "*.json", "*.ass", "*.srt", "*.txt"),
                new FileChooser.ExtensionFilter("所有文件", "*.*"));
        List<File> picked = chooser.showOpenMultipleDialog(owner());
        if (picked != null) {
            for (File f : picked) {
                String p = f.getAbsolutePath();
                if (!files.contains(p)) files.add(p);
            }
        }
    }

    private void chooseOutDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择导出目录");
        File dir = chooser.showDialog(owner());
        if (dir != null) outDir.setText(dir.getAbsolutePath());
    }

    private List<String> selectedFormats() {
        List<String> formats = new ArrayList<>();
        if (cbText.isSelected()) formats.add("text");
        if (cbTimed.isSelected()) formats.add("timed_text");
        if (cbLrc.isSelected()) formats.add("lrc");
        if (cbAss.isSelected()) formats.add("ass");
        if (cbSrt.isSelected()) formats.add("srt");
        return formats;
    }

    private void startConvert() {
        if (files.isEmpty()) {
            status.setText("请先添加字幕文件");
            return;
        }
        List<String> formats = selectedFormats();
        if (formats.isEmpty()) {
            status.setText("请至少选择一种输出格式");
            return;
        }
        Path out = Paths.get(outDir.getText().trim().isEmpty()
                ? AppPaths.downloadDir().resolve("字幕转换").toString() : outDir.getText().trim());
        String mode = nameMode.getSelectionModel().getSelectedIndex() == 1 ? "date" : "original";

        progress.setVisible(true);
        status.setText("转换中…");

        javafx.concurrent.Task<String> task = new javafx.concurrent.Task<>() {
            @Override
            protected String call() throws Exception {
                int ok = 0, fail = 0;
                Files.createDirectories(out);
                for (String p : List.copyOf(files)) {
                    try {
                        Path src = Paths.get(p);
                        byte[] content = Files.readAllBytes(src);
                        ConvertResult r = engine.convertFile(
                                src.getFileName().toString(), content, formats, mode);
                        if (!r.success()) { fail++; continue; }
                        for (OutputFile of : r.outputFiles()) {
                            Path target = out.resolve(Paths.get(of.filename()).getFileName().toString());
                            Files.write(target, of.content());
                        }
                        ok++;
                    } catch (Exception ex) {
                        fail++;
                    }
                }
                return "完成：" + ok + " 个成功" + (fail > 0 ? "，" + fail + " 个失败" : "");
            }
        };
        task.setOnSucceeded(e -> {
            progress.setVisible(false);
            status.setText(task.getValue());
            toast("字幕转换完成，输出目录：" + out);
        });
        task.setOnFailed(e -> {
            progress.setVisible(false);
            status.setText("转换失败：" + task.getException().getMessage());
        });
        new Thread(task, "subtitle-convert").start();
    }
}
