package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.rename.RenameRule;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.RadioButton;
import javafx.scene.control.TextField;
import javafx.scene.control.ToggleGroup;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * ✏️ 交互重命名（对齐 Python 版 rename.py 的三栏结构）。
 *
 * <p>三栏：📁 日期文件夹 + 🎬 文件列表 / 👤 直播账户 + 🎯 直播类型（多选，按点击顺序）+
 * 🗑 清除所有选择 / 📹 视频信息（版本 · 平台 · 标题三模式）+ 新文件名预览 + 执行重命名。
 *
 * <p>命名规则见 {@link RenameRule}：`日期_时间_账户_类型1+类型2_有弹幕|无弹幕_平台_标题 + 后缀`。
 */
public class RenamePage extends BasePage {

    private final ListView<String> folderList = new ListView<>();
    private final ListView<String> fileList = new ListView<>();
    private final ListView<String> accountList = new ListView<>();
    private final ListView<String> typeList = new ListView<>();
    private final TextField accountField = new TextField();
    private final ToggleGroup versionGroup = new ToggleGroup();
    private final RadioButton withDanmaku = new RadioButton("有弹幕");
    private final RadioButton withoutDanmaku = new RadioButton("无弹幕");
    private final ComboBox<String> platformBox = new ComboBox<>();
    private final ComboBox<String> titleModeBox = new ComboBox<>();
    private final TextField titleField = new TextField();
    private final Label newNameLabel = new Label("");
    private final Label folderCount = new Label("");
    private final Label progressLabel = new Label("");
    private final Label status = new Label("");

    private final List<Path> folders = new ArrayList<>();
    private final List<Path> videos = new ArrayList<>();
    private final List<String> typeOrder = new ArrayList<>();
    private String ctxDate = "";
    private String ctxTime = "";
    private String autoTitle = "";

    private Consumer<String> toast;

    @Override
    public String title() { return "✏️ 重命名"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("✏️ 交互重命名");
        pageTitle.setStyle("-fx-font-size: 16px; -fx-font-weight: bold;");
        Button refresh = new Button("🔄 刷新");
        refresh.setPrefWidth(80);
        refresh.setOnAction(e -> scanFolders());
        HBox head = new HBox(8, pageTitle, refresh, folderCount, spacer(), progressLabel, status);
        head.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        HBox body = new HBox(8, buildColumn1(), buildColumn2(), buildColumn3());
        HBox.setHgrow(body.getChildren().get(0), Priority.ALWAYS);
        HBox.setHgrow(body.getChildren().get(1), Priority.ALWAYS);
        HBox.setHgrow(body.getChildren().get(2), Priority.ALWAYS);
        VBox.setVgrow(body, Priority.ALWAYS);

        VBox root = new VBox(8, head, body);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(10));
        VBox.setVgrow(body, Priority.ALWAYS);
        Platform.runLater(this::scanFolders);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private VBox column(String title) {
        Label l = new Label(title);
        l.setStyle("-fx-font-weight: bold;");
        VBox box = new VBox(6);
        box.getChildren().add(l);
        box.setPadding(new Insets(8));
        box.setStyle("-fx-border-color: #e0e4ea; -fx-border-radius: 4;");
        return box;
    }

    // ==================== 左栏 ====================

    private Node buildColumn1() {
        VBox col = column("📁 日期文件夹");
        folderList.setPlaceholder(new Label("（无日期文件夹）"));
        folderList.getSelectionModel().selectedIndexProperty().addListener((o, a, b) -> loadFiles(b.intValue()));
        VBox.setVgrow(folderList, Priority.ALWAYS);

        Label fileLabel = new Label("🎬 文件列表");
        fileLabel.setStyle("-fx-font-weight: bold;");
        fileList.setPlaceholder(new Label("（选择文件夹）"));
        fileList.getSelectionModel().selectedIndexProperty().addListener((o, a, b) -> onSelectVideo(b.intValue()));
        VBox.setVgrow(fileList, Priority.ALWAYS);

        col.getChildren().addAll(folderList, fileLabel, fileList);
        return col;
    }

    // ==================== 中栏 ====================

    private Node buildColumn2() {
        VBox col = column("👤 直播账户");
        accountList.setPrefHeight(150);
        accountList.setPlaceholder(new Label("（从文件名猜测或手动添加）"));
        accountList.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> updatePreview());
        HBox addRow = new HBox(4, accountField, smallBtn("＋", "添加账户", this::addAccount));
        HBox.setHgrow(accountField, Priority.ALWAYS);
        accountField.setPromptText("账户名");

        Label typeLabel = new Label("🎯 直播类型（多选，按点击顺序）");
        typeLabel.setStyle("-fx-font-weight: bold;");
        typeList.setItems(FXCollections.observableArrayList(RenameRule.DEFAULT_LIVE_TYPES));
        typeList.getSelectionModel().setSelectionMode(javafx.scene.control.SelectionMode.MULTIPLE);
        typeList.setOnMouseClicked(e -> onTypeClick());
        VBox.setVgrow(typeList, Priority.ALWAYS);

        Button clear = new Button("🗑 清除所有选择");
        clear.setOnAction(e -> {
            accountList.getSelectionModel().clearSelection();
            typeList.getSelectionModel().clearSelection();
            typeOrder.clear();
            updatePreview();
        });
        col.getChildren().addAll(accountList, addRow, typeLabel, typeList, clear);
        return col;
    }

    private Button smallBtn(String text, String tip, Runnable action) {
        Button b = new Button(text);
        b.setMinSize(30, 26);
        b.setMaxSize(30, 26);
        b.setTooltip(new javafx.scene.control.Tooltip(tip));
        b.setOnAction(e -> action.run());
        return b;
    }

    // ==================== 右栏 ====================

    private Node buildColumn3() {
        VBox col = column("📹 视频信息");
        Label info = new Label("请选择左侧文件夹");
        info.setWrapText(true);
        info.setStyle("-fx-text-fill: #6b7280;");

        withDanmaku.setToggleGroup(versionGroup);
        withoutDanmaku.setToggleGroup(versionGroup);
        withDanmaku.setSelected(true);
        versionGroup.selectedToggleProperty().addListener((o, a, b) -> updatePreview());
        HBox verRow = new HBox(6, new Label("📋 版本:"), withDanmaku, withoutDanmaku);
        verRow.setAlignment(Pos.CENTER_LEFT);

        platformBox.getItems().addAll(RenameRule.DEFAULT_PLATFORMS);
        platformBox.getSelectionModel().selectFirst();
        platformBox.setOnAction(e -> updatePreview());
        HBox platRow = new HBox(6, new Label("📡 平台:"), platformBox);
        platRow.setAlignment(Pos.CENTER_LEFT);

        titleModeBox.getItems().addAll("自动提取", "手动输入", "不保留");
        titleModeBox.getSelectionModel().selectFirst();
        titleModeBox.setOnAction(e -> {
            titleField.setDisable(titleModeBox.getSelectionModel().getSelectedIndex() != 1);
            updatePreview();
        });
        titleField.setDisable(true);
        titleField.setPromptText("手动输入的标题");
        titleField.textProperty().addListener((o, a, b) -> updatePreview());
        HBox titleRow = new HBox(6, new Label("🏷 标题:"), titleModeBox, titleField);
        titleRow.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(titleField, Priority.ALWAYS);

        Label thumb = new Label("📸 预览截图（未实现缩略图）");
        thumb.setMinHeight(120);
        thumb.setMaxWidth(Double.MAX_VALUE);
        thumb.setAlignment(Pos.CENTER);
        thumb.setStyle("-fx-background-color: #f2f4f7; -fx-border-color: #d0d6df; -fx-text-fill: #888;");

        newNameLabel.setWrapText(true);
        newNameLabel.setStyle("-fx-background-color: #eef7ee; -fx-border-color: #2e7d32; "
                + "-fx-padding: 6; -fx-text-fill: #1f2937;");
        Label nameTitle = new Label("新文件名：");
        nameTitle.setStyle("-fx-font-weight: bold;");

        Button renameBtn = new Button("✅ 执行重命名");
        renameBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white; -fx-padding: 6 16 6 16;");
        renameBtn.setOnAction(e -> doRename());

        col.getChildren().addAll(info, verRow, platRow, titleRow, thumb, nameTitle, newNameLabel, renameBtn);
        return col;
    }

    // ==================== 数据 ====================

    private void scanFolders() {
        folders.clear();
        Path workDir = Settings.load().effectiveWorkDir();
        try (Stream<Path> s = Files.list(workDir)) {
            for (Path p : s.filter(Files::isDirectory).toList()) {
                if (p.getFileName().toString().matches("^20\\d{6}$")) folders.add(p);
            }
        } catch (IOException e) {
            status.setText("扫描失败：" + e.getMessage());
            return;
        }
        folders.sort(java.util.Comparator.comparing(Path::toString));
        List<String> names = new ArrayList<>();
        for (Path p : folders) names.add("📁 " + p.getFileName());
        folderList.setItems(FXCollections.observableArrayList(names));
        folderCount.setText("共 " + folders.size() + " 个文件夹");
        status.setText("工作目录：" + workDir);
    }

    private void loadFiles(int idx) {
        videos.clear();
        typeOrder.clear();
        if (idx < 0 || idx >= folders.size()) {
            fileList.getItems().clear();
            return;
        }
        Path dir = folders.get(idx);
        try (Stream<Path> s = Files.walk(dir)) {
            for (Path p : s.filter(Files::isRegularFile).toList()) {
                String n = p.getFileName().toString().toLowerCase(java.util.Locale.ROOT);
                if (n.endsWith(".mp4") || n.endsWith(".flv") || n.endsWith(".mkv")
                        || n.endsWith(".ts") || n.endsWith(".mov")) {
                    videos.add(p);
                }
            }
        } catch (IOException e) {
            status.setText("读取文件失败：" + e.getMessage());
            return;
        }
        videos.sort(java.util.Comparator.comparing(Path::toString));
        List<String> names = new ArrayList<>();
        for (Path p : videos) names.add("🎬 " + p.getFileName());
        fileList.setItems(FXCollections.observableArrayList(names));

        // 从文件名单词猜测账户
        Set<String> guessed = new LinkedHashSet<>();
        for (Path p : videos) {
            String acc = RenameRule.guessAccount(p.getFileName().toString());
            if (!acc.isEmpty()) guessed.add(acc);
        }
        accountList.setItems(FXCollections.observableArrayList(guessed));
        if (!guessed.isEmpty()) accountList.getSelectionModel().selectFirst();
        progressLabel.setText(dir.getFileName() + " · " + videos.size() + " 个视频");
    }

    private void onSelectVideo(int idx) {
        if (idx < 0 || idx >= videos.size()) return;
        Path p = videos.get(idx);
        String[] dt = RenameRule.parseDateTime(p.getFileName().toString());
        ctxDate = dt[0];
        ctxTime = dt[1];
        autoTitle = RenameRule.guessAccount(p.getFileName().toString());
        status.setText("已选择：" + p.getFileName());
        updatePreview();
    }

    private void addAccount() {
        String name = accountField.getText() == null ? "" : accountField.getText().trim();
        if (name.isEmpty()) return;
        List<String> items = new ArrayList<>(accountList.getItems());
        if (!items.contains(name)) items.add(name);
        accountList.setItems(FXCollections.observableArrayList(items));
        accountList.getSelectionModel().select(name);
        accountField.clear();
        updatePreview();
    }

    private void onTypeClick() {
        typeOrder.clear();
        for (String t : typeList.getSelectionModel().getSelectedItems()) typeOrder.add(t);
        updatePreview();
    }

    private String currentAccount() {
        String s = accountList.getSelectionModel().getSelectedItem();
        return s == null ? "" : s;
    }

    private void updatePreview() {
        if (videos.isEmpty()) {
            newNameLabel.setText("");
            return;
        }
        String suffix = "";
        int idx = fileList.getSelectionModel().getSelectedIndex();
        if (idx >= 0 && idx < videos.size()) {
            String n = videos.get(idx).getFileName().toString();
            int dot = n.lastIndexOf('.');
            suffix = dot > 0 ? n.substring(dot) : "";
        }
        String title = switch (titleModeBox.getSelectionModel().getSelectedIndex()) {
            case 0 -> autoTitle;
            case 1 -> titleField.getText();
            default -> "";
        };
        var parts = new RenameRule.Parts(ctxDate, ctxTime, currentAccount(), new ArrayList<>(typeOrder),
                withDanmaku.isSelected(), platformBox.getValue(), title);
        newNameLabel.setText(RenameRule.build(parts, suffix));
    }

    private void doRename() {
        int idx = fileList.getSelectionModel().getSelectedIndex();
        if (idx < 0 || idx >= videos.size()) {
            status.setText("请先选择文件");
            return;
        }
        if (currentAccount().isEmpty()) {
            status.setText("⚠️ 请选择直播账户");
            return;
        }
        if (typeOrder.isEmpty()) {
            status.setText("⚠️ 请至少选择一种直播类型");
            return;
        }
        String newName = newNameLabel.getText();
        if (newName == null || newName.isBlank()) {
            status.setText("新文件名为空");
            return;
        }
        Path src = videos.get(idx);
        Path dst = src.resolveSibling(newName);
        if (Files.exists(dst)) {
            status.setText("⚠️ 目标已存在：" + newName);
            return;
        }
        try {
            Files.move(src, dst, StandardCopyOption.ATOMIC_MOVE);
            status.setText("✅ " + src.getFileName() + " → " + newName);
            toast("已重命名：" + newName);
            // 刷新列表并选中下一个
            int next = Math.min(idx + 1, videos.size() - 1);
            loadFiles(folderList.getSelectionModel().getSelectedIndex());
            if (!videos.isEmpty()) fileList.getSelectionModel().select(next);
        } catch (Exception e) {
            status.setText("重命名失败：" + e.getMessage());
        }
    }
}
