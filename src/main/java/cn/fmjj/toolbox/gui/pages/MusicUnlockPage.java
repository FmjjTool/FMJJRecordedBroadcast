package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.music.MusicUnlock;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.TextArea;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.FileChooser;

import java.io.File;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;

/**
 * 🔓 音乐解密（对齐 Python 版 music_unlock.py 的极简结构）。
 *
 * <p>布局：`🔓 音乐解密`(18px) + 伸展 + `❓ 帮助` → 工具行（`📁 选择文件` + `🗑 清空` +
 * 伸展 + `🔓 开始解密`(绿, padding 6 16)）→ 文件列表（**最大高 200**）→ 日志（只读，伸展）。
 *
 * <p>与原版一致：**选择文件**（多选文件对话框，不是选目录），全程本地解密。
 */
public class MusicUnlockPage extends BasePage {

    private final ListView<String> fileList = new ListView<>();
    private final TextArea log = new TextArea();
    private final Button decryptBtn = new Button("🔓 开始解密");
    private final List<Path> files = new ArrayList<>();

    private Consumer<String> toast;

    @Override
    public String title() { return "🔓 音乐解密"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private void appendLog(String s) {
        Platform.runLater(() -> {
            log.appendText(s + "\n");
            log.setScrollTop(Double.MAX_VALUE);
        });
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("🔓 音乐解密");
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");
        Button help = new Button("❓ 帮助");
        help.setOnAction(e -> showHelp());
        HBox titleRow = new HBox(8, pageTitle, spacer(), help);
        titleRow.setAlignment(Pos.CENTER_LEFT);

        Button selectBtn = new Button("📁 选择文件");
        selectBtn.setOnAction(e -> selectFiles());
        Button clearBtn = new Button("🗑 清空");
        clearBtn.setOnAction(e -> {
            files.clear();
            renderFiles();
        });
        decryptBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white; "
                + "-fx-padding: 6 16 6 16; -fx-background-radius: 4;");
        decryptBtn.setOnAction(e -> decryptAll());
        HBox bar = new HBox(6, selectBtn, clearBtn, spacer(), decryptBtn);
        bar.setAlignment(Pos.CENTER_LEFT);

        fileList.setMaxHeight(200);
        fileList.setPrefHeight(200);
        fileList.setPlaceholder(new Label("尚未选择文件（点「📁 选择文件」）"));

        log.setEditable(false);
        log.setPromptText("解密日志");

        VBox root = new VBox(6, titleRow, bar, fileList, log);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(log, Priority.ALWAYS);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private void showHelp() {
        javafx.scene.control.Alert a = new javafx.scene.control.Alert(
                javafx.scene.control.Alert.AlertType.INFORMATION);
        a.setTitle("音乐解密 · 功能介绍");
        a.setHeaderText("🔓 一键批量解密各平台加密音乐");
        a.setContentText("""
                支持格式：
                · ncm（网易云）
                · qmc0/qmc2/qmc3/qmc4/qmc6/qmc8/qmcflac/qmcogg/mflac/mflac0/mgg/mgg0/mgg1/tkm（QQ音乐）
                · kgm/kgma/vpr（酷狗）
                · kwm（酷我）
                · xm（虾米）、tm0/tm2/tm3/tm6（喜马拉雅）、x2m/x3m（喜马拉雅）

                操作：📁 选择文件（可多选）→ 🔓 开始解密
                输出：统一导出目录/解密音乐/（有元数据时按「标题 - 歌手」命名）
                全程本地解密，不上传、无需 Cookie
                """);
        a.showAndWait();
    }

    private void selectFiles() {
        FileChooser c = new FileChooser();
        c.setTitle("选择加密音乐文件");
        c.getExtensionFilters().addAll(
                new FileChooser.ExtensionFilter("加密音乐",
                        "*.ncm", "*.qmc0", "*.qmc2", "*.qmc3", "*.qmc4", "*.qmc6", "*.qmc8",
                        "*.qmcflac", "*.qmcogg", "*.mflac", "*.mflac0", "*.mgg", "*.mgg0", "*.mgg1",
                        "*.tkm", "*.kgm", "*.kgma", "*.vpr", "*.kwm", "*.xm",
                        "*.tm0", "*.tm2", "*.tm3", "*.tm6", "*.x2m", "*.x3m"),
                new FileChooser.ExtensionFilter("所有文件", "*.*"));
        List<File> picked = c.showOpenMultipleDialog(fileList.getScene() == null
                ? null : fileList.getScene().getWindow());
        if (picked == null || picked.isEmpty()) return;
        Set<Path> all = new LinkedHashSet<>(files);
        for (File f : picked) all.add(f.toPath());
        files.clear();
        files.addAll(all);
        renderFiles();
    }

    private void renderFiles() {
        fileList.setItems(FXCollections.observableArrayList(
                files.stream().map(p -> p.getFileName().toString()).toList()));
    }

    private void decryptAll() {
        if (files.isEmpty()) {
            appendLog("⚠️ 请先选择文件");
            return;
        }
        decryptBtn.setDisable(true);
        Path outDir = Settings.load().effectiveExportDir().resolve("解密音乐");
        List<Path> batch = new ArrayList<>(files);
        appendLog("🔓 开始解密 " + batch.size() + " 个文件 → " + outDir);

        javafx.concurrent.Task<int[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected int[] call() {
                int ok = 0, fail = 0;
                for (Path p : batch) {
                    try {
                        Path saved = MusicUnlock.decryptTo(p, outDir);
                        appendLog("✅ " + p.getFileName() + " → " + saved.getFileName());
                        ok++;
                    } catch (Exception e) {
                        appendLog("❌ " + p.getFileName() + "：" + e.getMessage());
                        fail++;
                    }
                }
                return new int[] {ok, fail};
            }
        };
        task.setOnSucceeded(e -> {
            int[] r = task.getValue();
            decryptBtn.setDisable(false);
            appendLog("完成：成功 " + r[0] + "，失败 " + r[1]);
            toast("解密完成：" + r[0] + " 个成功 / " + r[1] + " 失败");
            if (r[0] > 0) AudioEngine.openInExplorer(outDir);
        });
        task.setOnFailed(e -> {
            decryptBtn.setDisable(false);
            appendLog("❌ 异常：" + task.getException().getMessage());
        });
        new Thread(task, "music-unlock").start();
    }
}
