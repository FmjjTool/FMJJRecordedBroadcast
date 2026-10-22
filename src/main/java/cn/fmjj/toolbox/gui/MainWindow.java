package cn.fmjj.toolbox.gui;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.Log;
import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.gui.dialogs.CookieManagerDialog;
import cn.fmjj.toolbox.gui.pages.AudioPage;
import cn.fmjj.toolbox.gui.pages.BasePage;
import cn.fmjj.toolbox.gui.pages.CalendarPage;
import cn.fmjj.toolbox.gui.pages.ConvertPage;
import cn.fmjj.toolbox.gui.pages.FileTimePage;
import cn.fmjj.toolbox.gui.pages.GlobalSettingsPage;
import cn.fmjj.toolbox.gui.pages.InfoBatchPage;
import cn.fmjj.toolbox.gui.pages.InfoParsePage;
import cn.fmjj.toolbox.gui.pages.InfoResultsPage;
import cn.fmjj.toolbox.gui.pages.LauncherPage;
import cn.fmjj.toolbox.gui.pages.LyricsPage;
import cn.fmjj.toolbox.gui.pages.MigratePage;
import cn.fmjj.toolbox.gui.pages.OrganizePage;
import cn.fmjj.toolbox.gui.pages.RenameBatchPage;
import cn.fmjj.toolbox.gui.pages.StubPage;
import cn.fmjj.toolbox.gui.pages.SubtitlePage;
import javafx.css.PseudoClass;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.ComboBoxBase;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.TextInputControl;
import javafx.scene.control.ToggleButton;
import javafx.scene.input.KeyCode;
import javafx.scene.input.KeyEvent;
import javafx.scene.layout.BorderPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.StackPane;
import javafx.scene.layout.VBox;
import javafx.stage.Stage;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

/**
 * 主窗口：左侧导航 + 右侧内容区懒加载。
 * 结构对应 Python 版 main_window.py：NAV_GROUPS / PAGE_CLASSES 懒实例化 /
 * 生命周期钩子分发 / 工作目录广播 / 主题切换。
 */
public class MainWindow {

    /** 导航项。 */
    public record NavItem(String key, String label) {}

    /** 导航分组。 */
    public record NavGroup(String title, List<NavItem> items) {}

    private static final PseudoClass SELECTED = PseudoClass.getPseudoClass("selected");

    /** 全部导航页 key（冒烟自检用）。 */
    public static List<String> allPageKeys() {
        return NAV_GROUPS.stream().flatMap(g -> g.items().stream()).map(NavItem::key).toList();
    }

    /** 侧栏分组与页面（与 Python 版 NAV_GROUPS 一致，9 组 35 页）。 */
    public static final List<NavGroup> NAV_GROUPS = List.of(
            new NavGroup("📊 信息获取", List.of(
                    new NavItem("info_parse", "🔍 单个解析"),
                    new NavItem("info_batch", "📋 批量导入"),
                    new NavItem("info_accounts", "👥 账号获取"),
                    new NavItem("info_results", "📦 已获取"),
                    new NavItem("douyin_index", "📈 抖音指数"),
                    new NavItem("wechat_article", "📰 公众号解析"),
                    new NavItem("bili_resource", "🎁 B站资源"),
                    new NavItem("video_comments", "💬 视频评论区"))),
            new NavGroup("🗂 资产管理", List.of(
                    new NavItem("asset", "🗂 资产管理"))),
            new NavGroup("🎬 录播工具箱", List.of(
                    new NavItem("toolbox_organize", "📂 自动整理"),
                    new NavItem("toolbox_convert", "🔄 视频转换"),
                    new NavItem("toolbox_rename", "✏️ 重命名"),
                    new NavItem("toolbox_audio", "🎵 音频提取"),
                    new NavItem("toolbox_subtitle", "📝 字幕转换"),
                    new NavItem("toolbox_launcher", "🚀 继续操作"),
                    new NavItem("toolbox_calendar", "📅 日历浏览"),
                    new NavItem("toolbox_migrate", "🚚 文件迁移"))),
            new NavGroup("🎫 票务购物", List.of(
                    new NavItem("tickets", "🎫 票务解析"),
                    new NavItem("shopping", "🛒 购物解析"))),
            new NavGroup("🎵 音乐服务", List.of(
                    new NavItem("music_info", "🎵 音乐信息获取"),
                    new NavItem("netease", "🎧 音乐下载"),
                    new NavItem("unlock_music", "🔓 音乐解密"))),
            new NavGroup("👥 账户追踪", List.of(
                    new NavItem("music_tracker", "🎼 音乐账号追踪"),
                    new NavItem("tracker", "📈 网媒账号追踪"))),
            new NavGroup("🔴 直播弹幕录制", List.of(
                    new NavItem("douyin_danmaku", "📺 抖音弹幕姬"),
                    new NavItem("bili_danmaku", "📺 B站弹幕姬"))),
            new NavGroup("🧰 实用工具", List.of(
                    new NavItem("image_crop", "🖼 图片裁切"),
                    new NavItem("cover_maker", "🎨 套版封面制作"),
                    new NavItem("image_sorter", "🎞 媒体筛选分类"),
                    new NavItem("media_compress", "🗜 媒体压缩"),
                    new NavItem("rename_batch", "✏️ 批量重命名"),
                    new NavItem("file_time", "🕓 文件时间修改"),
                    new NavItem("music_tag", "🏷 音乐标签"),
                    new NavItem("lyrics_tool", "🎤 歌词转换"))),
            new NavGroup("⚙️ 全局", List.of(
                    new NavItem("global_settings", "⚙️ 全局设置"))));

    /** 页面工厂：已移植页面给真实实现，其余给占位页（对应 Python 侧 PAGE_CLASSES）。 */
    private final Map<String, Supplier<BasePage>> pageFactories = new LinkedHashMap<>();

    /** 已实例化页面缓存。 */
    private final Map<String, BasePage> pageCache = new LinkedHashMap<>();

    private final BorderPane root = new BorderPane();
    private final StackPane contentArea = new StackPane();
    private final Label dirLabel = new Label();
    private final VBox navBox = new VBox(2);

    private final Stage stage;
    private Scene scene;
    private BasePage current;
    private Path workDir;
    private Label cookieCountLabel;

    /** 侧栏 Cookie 状态（已登录平台数 / 总开关状态）。 */
    private void refreshCookieStatus() {
        if (cookieCountLabel == null) return;
        var cookies = cn.fmjj.toolbox.engine.auth.CookieStore.loadCookies();
        long loggedIn = cookies.values().stream().filter(v -> !v.isEmpty()).count();
        boolean enabled = cn.fmjj.toolbox.engine.auth.CookieStore.isEnabled();
        cookieCountLabel.setText(enabled ? "🍪 已登录 " + loggedIn + " 个平台" : "🍪 Cookie 已关闭");
    }

    public MainWindow(Stage stage) {
        this.stage = stage;
        this.workDir = Settings.load().effectiveWorkDir();
        registerPages();
        buildSidebar();
        root.setCenter(wrapContent());
        buildStatusBar();
        navigate("toolbox_subtitle"); // 启动默认页
    }

    /** 主窗口根节点。 */
    public BorderPane root() { return root; }

    /** 主窗口显示后调用：绑定 Scene 并应用主题。 */
    public void attachScene(Scene scene) {
        this.scene = scene;
        Theme.apply(scene);
        // Enter / Esc 统一分发到当前页（输入控件聚焦时不拦截）
        scene.addEventFilter(KeyEvent.KEY_PRESSED, e -> {
            if (current == null) return;
            Node focusOwner = scene.getFocusOwner();
            boolean typingInInput = focusOwner instanceof TextInputControl
                    || focusOwner instanceof ComboBoxBase<?>;
            if (e.getCode() == KeyCode.ENTER && !typingInInput) {
                current.onReturnPressed();
                e.consume();
            } else if (e.getCode() == KeyCode.ESCAPE) {
                current.onEscapePressed();
            }
        });
    }

    // ==================== 构建 ====================

    private void registerPages() {
        // 已移植（M1）
        pageFactories.put("toolbox_subtitle", SubtitlePage::new);
        pageFactories.put("lyrics_tool", LyricsPage::new);
        // 已移植（M2）
        pageFactories.put("rename_batch", RenameBatchPage::new);
        pageFactories.put("toolbox_organize", OrganizePage::new);
        pageFactories.put("file_time", FileTimePage::new);
        pageFactories.put("global_settings", () -> new GlobalSettingsPage(this));
        // 已移植（M4）
        pageFactories.put("toolbox_convert", ConvertPage::new);
        pageFactories.put("toolbox_audio", AudioPage::new);
        pageFactories.put("toolbox_migrate", MigratePage::new);
        pageFactories.put("toolbox_calendar", CalendarPage::new);
        pageFactories.put("toolbox_launcher", LauncherPage::new);
        // 已移植（M5 第一刀）
        pageFactories.put("info_parse", InfoParsePage::new);
        // 已移植（M5 第三刀）
        pageFactories.put("info_batch", InfoBatchPage::new);
        pageFactories.put("info_results", InfoResultsPage::new);
        // 占位（路线图见 docs/JAVA_PORT_PLAN.md）
        for (NavGroup group : NAV_GROUPS) {
            for (NavItem item : group.items()) {
                pageFactories.putIfAbsent(item.key(), () -> new StubPage(item.label()));
            }
        }
    }

    private void buildSidebar() {
        navBox.getStyleClass().add("nav-box");
        navBox.setPadding(new Insets(10, 8, 10, 8));

        Label appTitle = new Label("封茗囧菌工具箱");
        appTitle.getStyleClass().add("app-title");

        VBox header = new VBox(appTitle);
        header.getStyleClass().add("sidebar-header");

        for (NavGroup group : NAV_GROUPS) {
            Label groupLabel = new Label(group.title());
            groupLabel.getStyleClass().add("nav-group-label");
            navBox.getChildren().add(groupLabel);
            for (NavItem item : group.items()) {
                Button btn = new Button(item.label());
                btn.getStyleClass().add("nav-button");
                btn.setMaxWidth(Double.MAX_VALUE);
                btn.setAlignment(Pos.BASELINE_LEFT);
                btn.setOnAction(e -> navigate(item.key()));
                navBox.getChildren().add(btn);
            }
        }

        ScrollPane scroll = new ScrollPane(navBox);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        scroll.getStyleClass().add("sidebar-scroll");

        // 底部：主题切换 + Cookie 管理入口
        ToggleButton themeBtn = new ToggleButton("☀️ 浅色");
        themeBtn.getStyleClass().add("theme-toggle");
        themeBtn.setMaxWidth(Double.MAX_VALUE);
        themeBtn.setOnAction(e -> {
            boolean nowDark = Theme.toggle(scene);
            themeBtn.setText(nowDark ? "🌙 深色" : "☀️ 浅色");
        });
        Button cookieBtn = new Button("🍪 Cookie 管理");
        cookieBtn.getStyleClass().add("theme-toggle");
        cookieBtn.setMaxWidth(Double.MAX_VALUE);
        cookieBtn.setOnAction(e -> CookieManagerDialog.show(stage, this::refreshCookieStatus));
        Label cookieStatus = new Label();
        cookieStatus.getStyleClass().add("cookie-status");
        this.cookieCountLabel = cookieStatus;

        VBox sidebarBottom = new VBox(6, themeBtn, cookieBtn, cookieStatus);
        sidebarBottom.getStyleClass().add("sidebar-bottom");
        sidebarBottom.setPadding(new Insets(8));
        refreshCookieStatus();

        BorderPane sidebarPane = new BorderPane(scroll);
        sidebarPane.setTop(header);
        sidebarPane.setBottom(sidebarBottom);
        sidebarPane.getStyleClass().add("sidebar");
        root.setLeft(sidebarPane);
    }

    private StackPane wrapContent() {
        contentArea.getStyleClass().add("content-area");
        return contentArea;
    }

    private void buildStatusBar() {
        dirLabel.setText("📁 " + workDir);
        dirLabel.getStyleClass().add("status-label");
        HBox bar = new HBox(dirLabel);
        bar.getStyleClass().add("status-bar");
        bar.setAlignment(Pos.CENTER_LEFT);
        root.setBottom(bar);
    }

    // ==================== 导航与生命周期 ====================

    /** 切换到指定页面（懒实例化 + 缓存 + 钩子分发）。 */
    public void navigate(String key) {
        Supplier<BasePage> factory = pageFactories.get(key);
        if (factory == null) {
            Log.warn("未注册的页面: " + key);
            return;
        }
        BasePage page = pageCache.computeIfAbsent(key, f -> factory.get());
        if (page == current) return;

        if (current != null) current.onHide();
        current = page;
        page.bindToast(this::toast);

        ScrollPane pageScroll = new ScrollPane(page.content());
        pageScroll.setFitToWidth(true);
        pageScroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        pageScroll.getStyleClass().add("page-scroll");
        contentArea.getChildren().setAll(pageScroll);

        markSelected(key);
        page.onShow();
        Log.info("导航 -> " + key + " " + page.title());
    }

    private void markSelected(String key) {
        for (NavGroup group : NAV_GROUPS) {
            for (NavItem item : group.items()) {
                for (Node node : navBox.getChildren()) {
                    if (node instanceof Button btn && btn.getText().equals(item.label())) {
                        btn.pseudoClassStateChanged(SELECTED, item.key().equals(key));
                    }
                }
            }
        }
    }

    /** 全局工作目录变更：状态栏刷新 + 广播到当前页。 */
    public void setWorkDir(Path dir) {
        this.workDir = dir;
        dirLabel.setText("📁 " + dir);
        if (current != null) current.onDirChanged(dir);
    }

    public Path workDir() { return workDir; }

    public BasePage currentPage() { return current; }

    public Stage stage() { return stage; }

    /** 供页面调用的右下角提示。 */
    public void toast(String msg) { Toast.show(stage, msg); }
}
