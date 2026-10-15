package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.engine.lyrics.LyricsEngine;
import cn.fmjj.toolbox.engine.lyrics.LyricsEngine.ConvertResult;
import cn.fmjj.toolbox.engine.lyrics.LyricsEngine.ConvertResult.Output;
import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.ProgressIndicator;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TextArea;
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
import java.util.List;
import java.util.function.Consumer;

/**
 * 🎤 歌词转换页。移植自 Python 版 gui_qt/pages/lyrics_tool.py（功能对齐版）。
 *
 * Tab1 歌词转换：.lrc/.krc/.ksc/.qrc/.trc/.elrc/纯文本 → TXT/LRC/SRT/ASS/KSC
 * Tab2 KRCD 工具：酷狗 KRC（加密）↔ KRCD（明文）互转
 */
public class LyricsPage extends BasePage {

    private final LyricsEngine engine = new LyricsEngine();

    private final ObservableList<String> files = FXCollections.observableArrayList();
    private final ListView<String> listView = new ListView<>(files);

    private final ComboBox<String> outFormat = new ComboBox<>(
            FXCollections.observableArrayList("txt", "lrc", "srt", "ass", "ksc"));
    private final TextField outDir = new TextField();
    private final Label status = new Label("就绪");
    private final ProgressIndicator progress = new ProgressIndicator();

    private final TextArea krcdArea = new TextArea();

    private Consumer<String> toast;

    @Override
    public String title() { return "🎤 歌词转换"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        outFormat.getSelectionModel().select(0);
        progress.setVisible(false);
        progress.setPrefSize(18, 18);
        Path defaultOut = AppPaths.downloadDir().resolve("歌词转换");
        outDir.setText(defaultOut.toString());
        outDir.setEditable(false);

        Tab convertTab = new Tab("歌词转换", buildConvertPane());
        convertTab.setClosable(false);
        Tab krcdTab = new Tab("KRCD 工具", buildKrcdPane());
        krcdTab.setClosable(false);

        TabPane tabs = new TabPane(convertTab, krcdTab);
        tabs.getStyleClass().add("page-tabs");

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        VBox wrapper = new VBox(10, pageTitle, tabs);
        wrapper.getStyleClass().add("page-wrapper");
        VBox.setVgrow(tabs, Priority.ALWAYS);
        return wrapper;
    }

    // ==================== Tab1：歌词转换 ====================

    private Node buildConvertPane() {
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
        outBtn.setOnAction(e -> chooseDir());

        HBox toolbar = new HBox(8, addBtn, removeBtn, clearBtn);
        toolbar.getStyleClass().add("toolbar");

        HBox outRow = new HBox(8, new Label("导出目录"), outDir, outBtn, new Label("输出格式"), outFormat);
        outRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(outDir, Priority.ALWAYS);

        Button convertBtn = new Button("▶ 开始转换");
        convertBtn.setDefaultButton(true);
        convertBtn.setOnAction(e -> startConvert());
        HBox actionRow = new HBox(10, convertBtn, progress, status);
        actionRow.setAlignment(Pos.CENTER_LEFT);

        listView.setPlaceholder(new Label("尚未添加歌词文件（.lrc/.krc/.ksc/.qrc/.trc/.elrc/纯文本）"));

        VBox pane = new VBox(12, toolbar, listView, outRow, actionRow);
        VBox.setVgrow(listView, Priority.ALWAYS);
        pane.getStyleClass().add("page-root");
        pane.setPadding(new Insets(16));
        return pane;
    }

    // ==================== Tab2：KRCD 工具 ====================

    private Node buildKrcdPane() {
        Label hint = new Label("KRCD = 酷狗 KRC 的明文格式（解密后可编辑歌词文本）。"
                + "解密：选择 .krc 文件 → 得到明文；加密：把明文保存为 .krc。");
        hint.setWrapText(true);

        Button decryptBtn = new Button("🔓 选择 .krc 文件解密");
        decryptBtn.setOnAction(e -> decryptKrc());
        Button encryptBtn = new Button("🔒 加密上方文本并保存 .krc");
        encryptBtn.setOnAction(e -> encryptKrcd());
        HBox buttons = new HBox(10, decryptBtn, encryptBtn);
        buttons.getStyleClass().add("toolbar");

        krcdArea.setPromptText("解密后的 KRCD 明文会显示在这里；也可直接粘贴文本后点「加密」");
        krcdArea.setPrefRowCount(16);

        VBox pane = new VBox(12, hint, buttons, krcdArea);
        pane.getStyleClass().add("page-root");
        pane.setPadding(new Insets(16));
        return pane;
    }

    // ==================== 交互 ====================

    private Window owner() {
        return listView.getScene() == null ? null : listView.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void chooseFiles() {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择歌词文件");
        chooser.getExtensionFilters().addAll(
                new FileChooser.ExtensionFilter("歌词文件",
                        "*.lrc", "*.krc", "*.ksc", "*.qrc", "*.trc", "*.elrc", "*.txt"),
                new FileChooser.ExtensionFilter("所有文件", "*.*"));
        List<File> picked = chooser.showOpenMultipleDialog(owner());
        if (picked != null) {
            for (File f : picked) {
                String p = f.getAbsolutePath();
                if (!files.contains(p)) files.add(p);
            }
        }
    }

    private void chooseDir() {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle("选择导出目录");
        File dir = chooser.showDialog(owner());
        if (dir != null) outDir.setText(dir.getAbsolutePath());
    }

    private void startConvert() {
        if (files.isEmpty()) {
            status.setText("请先添加歌词文件");
            return;
        }
        String fmt = outFormat.getValue();
        Path out = Paths.get(outDir.getText().trim().isEmpty()
                ? AppPaths.downloadDir().resolve("歌词转换").toString() : outDir.getText().trim());

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
                        ConvertResult r = engine.convert(src.getFileName().toString(), content, fmt);
                        if (!r.success()) { fail++; continue; }
                        for (Output of : r.outputFiles()) {
                            Files.write(out.resolve(Paths.get(of.filename()).getFileName().toString()),
                                    of.content());
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
            toast("歌词转换完成，输出目录：" + out);
        });
        task.setOnFailed(e -> {
            progress.setVisible(false);
            status.setText("转换失败：" + task.getException().getMessage());
        });
        new Thread(task, "lyrics-convert").start();
    }

    private void decryptKrc() {
        FileChooser chooser = new FileChooser();
        chooser.setTitle("选择 KRC 文件");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("酷狗 KRC", "*.krc"));
        File f = chooser.showOpenDialog(owner());
        if (f == null) return;
        try {
            byte[] bytes = Files.readAllBytes(f.toPath());
            krcdArea.setText(engine.krcToKrcd(bytes));
            status.setText("已解密：" + f.getName());
        } catch (Exception ex) {
            status.setText("解密失败：" + ex.getMessage());
        }
    }

    private void encryptKrcd() {
        String text = krcdArea.getText();
        if (text == null || text.isBlank()) {
            status.setText("请先在文本框中粘贴 KRCD 明文");
            return;
        }
        FileChooser chooser = new FileChooser();
        chooser.setTitle("保存 KRC 文件");
        chooser.getExtensionFilters().add(new FileChooser.ExtensionFilter("酷狗 KRC", "*.krc"));
        File f = chooser.showSaveDialog(owner());
        if (f == null) return;
        try {
            Files.write(f.toPath(), engine.krcdToKrc(text));
            status.setText("已加密并保存：" + f.getName());
            toast("KRC 已保存：" + f.getAbsolutePath());
        } catch (Exception ex) {
            status.setText("加密失败：" + ex.getMessage());
        }
    }
}
