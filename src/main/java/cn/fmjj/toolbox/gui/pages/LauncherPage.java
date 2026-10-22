package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.media.AudioEngine;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * 🚀 继续操作页：常用目录与最近日期文件夹的一键入口。
 * 移植自 Python 版 gui_qt/pages/launcher.py 的快捷卡片（原版支持自定义条目，
 * 这里先给系统目录 + 最近日期文件夹，自定义条目随 M6 全局设置一起补）。
 */
public class LauncherPage extends BasePage {

    private final FlowPane grid = new FlowPane(10, 10);
    private final Label status = new Label();

    private Consumer<String> toast;

    @Override
    public String title() { return "🚀 继续操作"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    @Override
    protected Node build() {
        grid.setPadding(new Insets(4));

        Button refresh = new Button("🔄 刷新");
        refresh.setOnAction(e -> refresh());

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        Label hint = new Label("常用目录与最近 8 个日期文件夹，点一下直接打开（资源管理器）");
        hint.getStyleClass().add("stub-desc");

        VBox root = new VBox(10, pageTitle, hint, refresh, grid, status);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(12));
        VBox.setVgrow(grid, Priority.ALWAYS);
        refresh();
        return root;
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private Button card(String emoji, String label, String sub, Path target) {
        Button btn = new Button(emoji + "  " + label + "\n" + sub);
        btn.setPrefSize(190, 78);
        btn.setAlignment(Pos.CENTER_LEFT);
        btn.setStyle("-fx-font-size: 12px; -fx-text-alignment: left;");
        btn.setOnAction(e -> {
            try {
                Files.createDirectories(target);
            } catch (IOException ignored) {
            }
            AudioEngine.openInExplorer(target);
            toast("已打开：" + target);
        });
        return btn;
    }

    private void refresh() {
        grid.getChildren().clear();
        Settings settings = Settings.load();
        Path work = settings.effectiveWorkDir();
        Path export = settings.effectiveExportDir();

        grid.getChildren().addAll(
                card("📁", "工作目录", shorten(work), work),
                card("📦", "导出目录", shorten(export), export),
                card("⚙️", "配置目录", shorten(AppPaths.configDir()), AppPaths.configDir()),
                card("📝", "日志目录", shorten(AppPaths.logsDir()), AppPaths.logsDir()));

        // 最近 8 个日期文件夹（YYYYMMDD）
        List<Path> recent = new ArrayList<>();
        try (Stream<Path> s = Files.list(work)) {
            recent = s.filter(Files::isDirectory)
                    .filter(p -> p.getFileName().toString().matches("^20\\d{6}$"))
                    .sorted(Comparator.comparing((Path p) -> p.getFileName().toString()).reversed())
                    .limit(8)
                    .toList();
        } catch (IOException ignored) {
        }
        for (Path p : recent) {
            int files = 0;
            try (Stream<Path> s = Files.walk(p)) {
                files = (int) s.filter(Files::isRegularFile).count();
            } catch (IOException ignored) {
            }
            grid.getChildren().add(card("🗓", p.getFileName().toString(), files + " 个文件", p));
        }
        status.setText(recent.isEmpty()
                ? "工作目录下暂无日期文件夹（" + work + "）"
                : "最近 " + recent.size() + " 个日期文件夹");
    }

    private static String shorten(Path p) {
        String s = p.toString();
        return s.length() > 24 ? "…" + s.substring(s.length() - 23) : s;
    }
}
