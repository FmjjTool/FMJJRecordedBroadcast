package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.diagnostics.EnvironmentCheck;
import cn.fmjj.toolbox.gui.MainWindow;
import cn.fmjj.toolbox.gui.Theme;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Alert;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.TextArea;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.Window;

import java.io.File;
import java.nio.file.Path;

/**
 * ⚙️ 全局设置页。对应 Python 版 gui_qt/pages/settings.py 的目录/主题子集
 * （主题自动切换的跟随系统/定时策略在 M6 补）。
 */
public class GlobalSettingsPage extends BasePage {

    private final MainWindow window;
    private final Settings settings = Settings.load();

    private final TextField workDir = new TextField();
    private final TextField exportDir = new TextField();
    private final ComboBox<String> theme = new ComboBox<>(
            javafx.collections.FXCollections.observableArrayList("浅色", "深色"));
    private final CheckBox followSystem = new CheckBox("跟随系统深浅色（M6 支持，仅记录设置）");
    private final CheckBox schedule = new CheckBox("定时自动切换（M6 支持，仅记录设置）");
    private final TextField lightTime = new TextField();
    private final TextField darkTime = new TextField();
    private final TextField ffmpegPath = new TextField();
    private final Label status = new Label();

    public GlobalSettingsPage(MainWindow window) {
        this.window = window;
    }

    @Override
    public String title() { return "⚙️ 全局设置"; }

    @Override
    protected Node build() {
        workDir.setText(nvl(settings.workDir()));
        exportDir.setText(nvl(settings.exportDir()));
        theme.getSelectionModel().select("dark".equals(settings.theme()) ? 1 : 0);
        followSystem.setSelected(settings.themeFollowSystem());
        schedule.setSelected(settings.themeScheduleEnabled());
        lightTime.setText(settings.themeLightTime());
        darkTime.setText(settings.themeDarkTime());
        ffmpegPath.setText(nvl(settings.ffmpegPath()));

        workDir.setEditable(false);
        exportDir.setEditable(false);

        Button workBrowse = new Button("📂 浏览");
        workBrowse.setOnAction(e -> workDir.setText(pickDir("选择默认工作文件夹")));
        Button exportBrowse = new Button("📂 浏览");
        exportBrowse.setOnAction(e -> exportDir.setText(pickDir("选择统一导出目录")));

        Button envCheck = new Button("🔍 环境自检");
        envCheck.setOnAction(e -> showEnvironmentCheck());
        Button save = new Button("💾 保存设置");
        save.setDefaultButton(true);
        save.setOnAction(e -> save());

        VBox dirSection = section("目录",
                row(new Label("默认工作文件夹："), workDir, workBrowse),
                row(new Label("统一导出目录："), exportDir, exportBrowse));
        VBox themeSection = section("主题",
                row(new Label("主题："), theme),
                row(followSystem),
                row(schedule),
                row(new Label("浅色时刻："), lightTime, new Label("深色时刻："), darkTime));
        VBox ffmpegSection = section("依赖",
                row(new Label("FFmpeg 路径："), ffmpegPath),
                row(new Label("留空 = 使用系统 PATH（FFmpeg 自定义位置的完整支持在 M4 随转换/音频页一起落地）")));

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        VBox root = new VBox(12, pageTitle, dirSection, themeSection, ffmpegSection,
                new HBox(10, save, envCheck, status));
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        return root;
    }

    /** 环境自检弹窗：依赖是否齐全、目录可写、磁盘、登录态。 */
    private void showEnvironmentCheck() {
        var items = EnvironmentCheck.run();
        StringBuilder sb = new StringBuilder();
        for (var it : items) {
            sb.append(it.icon()).append(' ').append(it.name()).append('：')
              .append(it.detail()).append('\n');
        }
        Alert alert = new Alert(Alert.AlertType.INFORMATION);
        alert.setTitle("环境自检");
        alert.setHeaderText(EnvironmentCheck.allOk(items) ? "全部通过 ✅" : "有需要处理的项（见下方 ❌ / ⚠️）");
        TextArea area = new TextArea(sb.toString());
        area.setEditable(false);
        area.setWrapText(true);
        area.setPrefSize(700, 320);
        alert.getDialogPane().setContent(area);
        alert.showAndWait();
    }

    private static String nvl(String s) { return s == null ? "" : s; }

    private VBox section(String title, Node... rows) {
        Label label = new Label(title);
        label.setStyle("-fx-font-weight: bold;");
        VBox box = new VBox(8, label);
        box.getChildren().addAll(rows);
        box.getStyleClass().add("page-root");
        box.setPadding(new Insets(12));
        return box;
    }

    private HBox row(Node... nodes) {
        HBox box = new HBox(8);
        box.setAlignment(Pos.CENTER_LEFT);
        box.getChildren().addAll(nodes);
        return box;
    }

    private Window owner() {
        return workDir.getScene() == null ? null : workDir.getScene().getWindow();
    }

    private String pickDir(String title) {
        DirectoryChooser chooser = new DirectoryChooser();
        chooser.setTitle(title);
        File d = chooser.showDialog(owner());
        return d == null ? "" : d.getAbsolutePath();
    }

    private void save() {
        settings.setWorkDir(workDir.getText());
        settings.setExportDir(exportDir.getText());
        settings.setTheme(theme.getSelectionModel().getSelectedIndex() == 1 ? "dark" : "light");
        settings.setThemeFollowSystem(followSystem.isSelected());
        settings.setThemeScheduleEnabled(schedule.isSelected());
        settings.setThemeLightTime(lightTime.getText().trim().isEmpty() ? "07:00" : lightTime.getText().trim());
        settings.setThemeDarkTime(darkTime.getText().trim().isEmpty() ? "19:00" : darkTime.getText().trim());
        settings.setFfmpegPath(ffmpegPath.getText());
        settings.save();

        // 立即生效：工作目录广播 + 主题切换
        if (window != null) {
            window.setWorkDir(settings.effectiveWorkDir());
            boolean wantDark = "dark".equals(settings.theme());
            if (wantDark != Theme.isDark() && window.root().getScene() != null) {
                Theme.toggle(window.root().getScene());
            }
        }
        status.setText("已保存");
    }
}
