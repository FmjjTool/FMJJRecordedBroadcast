package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import cn.fmjj.toolbox.engine.platform.bili.BiliComments;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.Spinner;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * 💬 视频评论区（对齐 Python 版 video_comments.py 的结构）。
 *
 * <p>结构：平台下拉（抖音/B站/微博）+ 模式下拉 → 输入行（链接/ID + 视频数 + 页数 + 🔍抓取评论 +
 * ⏹停止）→ 第二行（视频下拉 + 🖼导出长图 + 💾保存原始数据）→ 筛选行（搜索/昵称/带图/最低赞/排序）
 * → 评论表格。
 *
 * <p>当前仅 **B站** 可用（`x/v2/reply` + WBI）；抖音/微博评论接口未移植，切换时明确提示。
 */
public class VideoCommentsPage extends BasePage {

    private static final String[][] PLATFORMS = {
            {"bili", "📺 B站"}, {"douyin", "📱 抖音"}, {"weibo", "🔴 微博"}
    };

    private final ComboBox<String> platformBox = new ComboBox<>();
    private final ComboBox<String> modeBox = new ComboBox<>();
    private final TextField inputField = new TextField();
    private final Spinner<Integer> videoCountSpinner = new Spinner<>(1, 50, 1);
    private final Spinner<Integer> pagesSpinner = new Spinner<>(1, 50, 5);
    private final Button fetchBtn = new Button("🔍 抓取评论");
    private final Button stopBtn = new Button("⏹ 停止");
    private final ComboBox<String> videoBox = new ComboBox<>();
    private final Button exportImgBtn = new Button("🖼 导出长图");
    private final Button saveBtn = new Button("💾 保存原始数据");
    private final TextField kwField = new TextField();
    private final TextField userField = new TextField();
    private final ComboBox<String> imageBox = new ComboBox<>();
    private final TextField minLikeField = new TextField("0");
    private final ComboBox<String> sortBox = new ComboBox<>();
    private final TableView<BiliComments.Comment> table = new TableView<>();
    private final Label status = new Label("就绪");

    private final AtomicBoolean stopFlag = new AtomicBoolean(false);
    private final BiliComments comments = new BiliComments();
    private List<BiliComments.Comment> all = new ArrayList<>();
    private String currentVideo = "";

    private Consumer<String> toast;

    @Override
    public String title() { return "💬 视频评论区"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        for (String[] p : PLATFORMS) platformBox.getItems().add(p[1]);
        platformBox.getSelectionModel().selectFirst();
        platformBox.setPrefWidth(120);
        platformBox.setOnAction(e -> onPlatformChange());
        modeBox.getItems().addAll("链接模式", "作者模式（仅抖音）");
        modeBox.getSelectionModel().selectFirst();
        modeBox.setPrefWidth(150);

        inputField.setPromptText("视频链接或 ID（例：BV1xx411c7mD / https://www.bilibili.com/video/BV...）");
        videoCountSpinner.setPrefWidth(70);
        pagesSpinner.setPrefWidth(70);
        fetchBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        fetchBtn.setOnAction(e -> fetch());
        stopBtn.setDisable(true);
        stopBtn.setOnAction(e -> {
            stopFlag.set(true);
            status.setText("正在停止…");
        });
        HBox row1 = new HBox(6, platformBox, modeBox, inputField,
                new Label("视频数"), videoCountSpinner, new Label("页数"), pagesSpinner, fetchBtn, stopBtn);
        row1.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(inputField, Priority.ALWAYS);

        videoBox.setPrefWidth(360);
        exportImgBtn.setOnAction(e -> exportImage());
        saveBtn.setOnAction(e -> saveRaw());
        HBox row2 = new HBox(6, new Label("视频："), videoBox, exportImgBtn, saveBtn, spacer(), status);
        row2.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        kwField.setPromptText("评论文本关键词");
        kwField.setPrefWidth(150);
        kwField.textProperty().addListener((o, a, b) -> applyFilter());
        userField.setPromptText("昵称");
        userField.setPrefWidth(120);
        userField.textProperty().addListener((o, a, b) -> applyFilter());
        imageBox.getItems().addAll("全部", "🖼 仅带图", "✏️ 仅纯文字");
        imageBox.getSelectionModel().selectFirst();
        imageBox.setOnAction(e -> applyFilter());
        minLikeField.setPrefWidth(70);
        minLikeField.textProperty().addListener((o, a, b) -> applyFilter());
        sortBox.getItems().addAll("默认", "♥ 点赞", "🕘 时间");
        sortBox.getSelectionModel().selectFirst();
        sortBox.setOnAction(e -> applyFilter());
        HBox filterRow = new HBox(6, new Label("🔍 搜索"), kwField, new Label("👤"), userField,
                imageBox, new Label("最低赞"), minLikeField, new Label("排序"), sortBox, spacer());
        filterRow.setAlignment(Pos.CENTER_LEFT);

        TableColumn<BiliComments.Comment, String> cUser = new TableColumn<>("用户");
        cUser.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().user()));
        cUser.setPrefWidth(140);
        TableColumn<BiliComments.Comment, String> cContent = new TableColumn<>("评论");
        cContent.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().content()));
        cContent.setPrefWidth(620);
        TableColumn<BiliComments.Comment, String> cLike = new TableColumn<>("点赞");
        cLike.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().likes())));
        cLike.setPrefWidth(80);
        TableColumn<BiliComments.Comment, String> cImg = new TableColumn<>("图");
        cImg.setCellValueFactory(d -> new SimpleStringProperty(String.valueOf(d.getValue().images().size())));
        cImg.setPrefWidth(50);
        TableColumn<BiliComments.Comment, String> cTime = new TableColumn<>("时间");
        cTime.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().timeText()));
        cTime.setPrefWidth(140);
        table.getColumns().addAll(List.of(cUser, cContent, cLike, cImg, cTime));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.setPlaceholder(new Label("抓取评论后显示"));

        VBox root = new VBox(6, row1, row2, filterRow, table);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(10));
        VBox.setVgrow(table, Priority.ALWAYS);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private void onPlatformChange() {
        int idx = platformBox.getSelectionModel().getSelectedIndex();
        String key = idx >= 0 ? PLATFORMS[idx][0] : "bili";
        boolean bili = "bili".equals(key);
        modeBox.setDisable(!bili || true);   // 作者模式仅抖音
        inputField.setPromptText(bili
                ? "视频链接或 ID（例：BV1xx411c7mD）"
                : "该平台评论接口未移植（当前仅 B站可用）");
        status.setText(bili ? "B站评论区可用" : "抖音/微博评论接口未移植");
    }

    // ==================== 抓取 ====================

    private void fetch() {
        String id = inputField.getText() == null ? "" : inputField.getText().trim();
        if (id.isEmpty()) {
            status.setText("请输入视频链接或 ID");
            return;
        }
        int idx = platformBox.getSelectionModel().getSelectedIndex();
        String platform = idx >= 0 ? PLATFORMS[idx][0] : "bili";
        if (!"bili".equals(platform)) {
            status.setText("该平台评论接口未移植（当前仅 B站）");
            return;
        }
        // 从链接里取 BV 号
        var m = java.util.regex.Pattern.compile("(BV[0-9A-Za-z]{10})").matcher(id);
        String bvid = m.find() ? m.group(1) : id;

        stopFlag.set(false);
        fetchBtn.setDisable(true);
        stopBtn.setDisable(false);
        status.setText("抓取中…");
        int pages = pagesSpinner.getValue();
        String cookie = CookieStore.get("bilibili");

        javafx.concurrent.Task<List<BiliComments.Comment>> task = new javafx.concurrent.Task<>() {
            @Override
            protected List<BiliComments.Comment> call() throws Exception {
                return comments.fetch(bvid, pages, cookie, msg ->
                        Platform.runLater(() -> status.setText(msg)));
            }
        };
        task.setOnSucceeded(e -> {
            fetchBtn.setDisable(false);
            stopBtn.setDisable(true);
            all = task.getValue();
            currentVideo = bvid;
            videoBox.getItems().setAll(bvid + "（" + all.size() + " 条评论）");
            videoBox.getSelectionModel().selectFirst();
            applyFilter();
            status.setText("抓取完成：" + all.size() + " 条");
            if (!all.isEmpty()) toast("抓取到 " + all.size() + " 条评论");
        });
        task.setOnFailed(e -> {
            fetchBtn.setDisable(false);
            stopBtn.setDisable(true);
            status.setText("抓取失败：" + task.getException().getMessage());
        });
        new Thread(task, "bili-comments").start();
    }

    private void applyFilter() {
        String kw = kwField.getText() == null ? "" : kwField.getText().trim().toLowerCase(Locale.ROOT);
        String user = userField.getText() == null ? "" : userField.getText().trim().toLowerCase(Locale.ROOT);
        int imageMode = imageBox.getSelectionModel().getSelectedIndex();
        int minLike = parseInt(minLikeField.getText(), 0);
        int sort = sortBox.getSelectionModel().getSelectedIndex();

        List<BiliComments.Comment> rows = new ArrayList<>();
        for (var c : all) {
            if (!kw.isEmpty() && !c.content().toLowerCase(Locale.ROOT).contains(kw)) continue;
            if (!user.isEmpty() && !c.user().toLowerCase(Locale.ROOT).contains(user)) continue;
            if (imageMode == 1 && c.images().isEmpty()) continue;
            if (imageMode == 2 && !c.images().isEmpty()) continue;
            if (c.likes() < minLike) continue;
            rows.add(c);
        }
        if (sort == 1) rows.sort((a, b) -> Integer.compare(b.likes(), a.likes()));
        if (sort == 2) rows.sort((a, b) -> Long.compare(b.ctime(), a.ctime()));
        table.setItems(FXCollections.observableArrayList(rows));
        status.setText("显示 " + rows.size() + " / " + all.size() + " 条");
    }

    private static int parseInt(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (Exception e) {
            return def;
        }
    }

    // ==================== 导出 ====================

    private void saveRaw() {
        if (all.isEmpty()) { status.setText("没有可保存的评论"); return; }
        Path dir = Settings.load().effectiveExportDir().resolve("评论区")
                .resolve("B站").resolve(currentVideo);
        try {
            Files.createDirectories(dir);
            StringBuilder json = new StringBuilder("[\n");
            StringBuilder txt = new StringBuilder();
            for (int i = 0; i < all.size(); i++) {
                var c = all.get(i);
                json.append("  {\"user\":\"").append(esc(c.user()))
                    .append("\",\"content\":\"").append(esc(c.content()))
                    .append("\",\"likes\":").append(c.likes())
                    .append(",\"ctime\":").append(c.ctime())
                    .append(",\"images\":[");
                for (int k = 0; k < c.images().size(); k++) {
                    json.append("\"").append(esc(c.images().get(k))).append("\"")
                        .append(k == c.images().size() - 1 ? "" : ",");
                }
                json.append("]}").append(i == all.size() - 1 ? "" : ",").append("\n");
                txt.append("[").append(c.timeText()).append("] ").append(c.user())
                   .append("（").append(c.likes()).append(" 赞）：").append(c.content()).append("\n");
            }
            json.append("]\n");
            Files.writeString(dir.resolve("comments.json"), json.toString(), StandardCharsets.UTF_8);
            Files.writeString(dir.resolve("comments.txt"), txt.toString(), StandardCharsets.UTF_8);
            status.setText("已保存：" + dir);
            toast("评论已保存：" + all.size() + " 条");
        } catch (Exception e) {
            status.setText("保存失败：" + e.getMessage());
        }
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "");
    }

    /** 导出评论区长图（Graphics2D 绘制：昵称 + 内容 + 点赞 + 时间）。 */
    private void exportImage() {
        if (all.isEmpty()) { status.setText("没有可导出的评论"); return; }
        int width = 900;
        int padding = 24;
        int lineH = 24;
        int headH = 90;
        // 先算高度
        List<String[]> wrapped = new ArrayList<>();
        int bodyH = 0;
        for (var c : all) {
            List<String> lines = wrap(c.content(), 46);
            wrapped.add(lines.toArray(new String[0]));
            bodyH += 30 + lines.size() * lineH + 14;
        }
        int height = headH + bodyH + padding;
        java.awt.image.BufferedImage img =
                new java.awt.image.BufferedImage(width, height, java.awt.image.BufferedImage.TYPE_INT_RGB);
        var g = img.createGraphics();
        g.setRenderingHint(java.awt.RenderingHints.KEY_TEXT_ANTIALIASING,
                java.awt.RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        g.setColor(java.awt.Color.WHITE);
        g.fillRect(0, 0, width, height);

        g.setColor(java.awt.Color.decode("#1f2937"));
        g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, 22));
        g.drawString("B站评论 · " + currentVideo + "（" + all.size() + " 条）", padding, 44);
        g.setColor(java.awt.Color.decode("#9ca3af"));
        g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 13));
        g.drawString("导出时间 " + java.time.LocalDateTime.now()
                .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")), padding, 68);

        int y = headH;
        for (int i = 0; i < all.size(); i++) {
            var c = all.get(i);
            g.setColor(java.awt.Color.decode("#2563eb"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.BOLD, 15));
            g.drawString(c.user(), padding, y + 18);
            g.setColor(java.awt.Color.decode("#9ca3af"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 12));
            g.drawString("♥ " + c.likes() + "   " + c.timeText(), padding + 260, y + 18);

            g.setColor(java.awt.Color.decode("#1f2937"));
            g.setFont(new java.awt.Font("Microsoft YaHei", java.awt.Font.PLAIN, 15));
            String[] lines = wrapped.get(i);
            for (int k = 0; k < lines.length; k++) {
                g.drawString(lines[k], padding, y + 44 + k * lineH);
            }
            y += 30 + lines.length * lineH + 14;
            g.setColor(java.awt.Color.decode("#e5e7eb"));
            g.drawLine(padding, y - 8, width - padding, y - 8);
        }
        g.dispose();

        Path dir = Settings.load().effectiveExportDir().resolve("评论区").resolve("B站");
        try {
            Files.createDirectories(dir);
            Path target = dir.resolve("评论长图_" + currentVideo + ".png");
            javax.imageio.ImageIO.write(img, "png", target.toFile());
            status.setText("已导出长图：" + target);
            toast("评论长图已导出");
        } catch (Exception e) {
            status.setText("导出长图失败：" + e.getMessage());
        }
    }

    /** 按字符宽度粗略折行（中文按 1 字宽，英文按 0.55 估算）。 */
    static List<String> wrap(String text, int maxUnits) {
        List<String> out = new ArrayList<>();
        if (text == null || text.isEmpty()) {
            out.add("");
            return out;
        }
        StringBuilder line = new StringBuilder();
        double units = 0;
        for (char ch : text.toCharArray()) {
            double w = ch < 128 ? 0.55 : 1.0;
            if (units + w > maxUnits) {
                out.add(line.toString());
                line.setLength(0);
                units = 0;
            }
            line.append(ch);
            units += w;
        }
        if (line.length() > 0) out.add(line.toString());
        return out;
    }
}
