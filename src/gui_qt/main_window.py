"""PySide6 主窗口：左侧导航 + 右侧 QStackedWidget 懒加载。

与原版（customtkinter）一一对应：
- 侧栏底部 Cookie 状态区（总开关 + 已登录计数 + 管理入口）
- 全局拖放（字幕/歌词/音乐标签/图片裁切/媒体筛选/媒体压缩/音频提取页接收文件，其他页切换工作目录）
- 工作目录持久化 + on_dir_changed 广播
- 回车键统一分发到当前页 on_return_press（输入框聚焦时不拦截）
"""

import os

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt import common, theme
from gui_qt.toolbox_common import has_date_subfolders
from gui_qt.skeleton import SkeletonPage
from gui_qt.toast import show_toast

# 页面注册：key -> (模块路径, 类名)。
# 懒导入：首次切到该页才 import（启动只加载首个页面，避免 36 个页面模块
# 全量 import 拖慢启动、也避免把各页的重依赖提前拉起）。
PAGE_CLASSES = {
    "info_parse": ("gui_qt.pages.info_parse", "InfoParsePage"),
    "info_batch": ("gui_qt.pages.info_batch", "InfoBatchPage"),
    "info_accounts": ("gui_qt.pages.info_accounts", "InfoAccountsPage"),
    "info_results": ("gui_qt.pages.info_results", "InfoResultsPage"),
    "douyin_index": ("gui_qt.pages.douyin_index", "DouyinIndexPage"),
    "wechat_article": ("gui_qt.pages.wechat_article", "WechatArticlePage"),
    "asset": ("gui_qt.pages.asset", "AssetPage"),
    "toolbox_organize": ("gui_qt.pages.organize", "OrganizePage"),
    "toolbox_convert": ("gui_qt.pages.convert", "ConvertPage"),
    "toolbox_rename": ("gui_qt.pages.rename", "RenamePage"),
    "toolbox_audio": ("gui_qt.pages.audio", "AudioPage"),
    "toolbox_subtitle": ("gui_qt.pages.subtitle", "SubtitlePage"),
    "lyrics_tool": ("gui_qt.pages.lyrics_tool", "LyricsToolPage"),
    "toolbox_launcher": ("gui_qt.pages.launcher", "LauncherPage"),
    "toolbox_calendar": ("gui_qt.pages.calendar", "CalendarPage"),
    "toolbox_migrate": ("gui_qt.pages.migrate", "MigratePage"),
    "music_info": ("gui_qt.pages.music_info", "MusicInfoPage"),
    "netease": ("gui_qt.pages.netease", "NeteasePage"),
    "unlock_music": ("gui_qt.pages.music_unlock", "MusicUnlockPage"),
    "music_tag": ("gui_qt.pages.music_tag", "MusicTagPage"),
    "tracker": ("gui_qt.pages.tracker", "TrackerPage"),
    "music_tracker": ("gui_qt.pages.music_tracker", "MusicTrackerPage"),
    "test_page": ("gui_qt.pages.test_page", "TestPage"),
    "bili_resource": ("gui_qt.pages.bili_resource", "BiliResourcePage"),
    "video_comments": ("gui_qt.pages.video_comments", "VideoCommentsPage"),
    "file_time": ("gui_qt.pages.file_time", "FileTimePage"),
    "rename_batch": ("gui_qt.pages.rename_batch", "RenameBatchPage"),
    "tickets": ("gui_qt.pages.tickets_page", "TicketsPage"),
    "shopping": ("gui_qt.pages.shopping_page", "ShoppingPage"),
    "douyin_shop": ("gui_qt.pages.douyin_shop_page", "DouyinShopPage"),
    "bili_danmaku": ("gui_qt.pages.bili_danmaku", "BiliDanmakuPage"),
    "image_crop": ("gui_qt.pages.image_crop", "ImageCropPage"),
    "cover_maker": ("gui_qt.pages.cover_maker", "CoverMakerPage"),
    "image_sorter": ("gui_qt.pages.image_sorter", "ImageSorterPage"),
    "media_compress": ("gui_qt.pages.media_compress", "MediaCompressPage"),
    "global_settings": ("gui_qt.pages.settings", "GlobalSettingsPage"),
}


def _page_class(key):
    """按注册表懒导入页面类。"""
    import importlib
    mod_name, cls_name = PAGE_CLASSES[key]
    return getattr(importlib.import_module(mod_name), cls_name)

NAV_GROUPS = [
    ("📊 信息获取", [
        ("info_parse", "🔍 单个解析"),
        ("info_batch", "📋 批量导入"),
        ("info_accounts", "👥 账号获取"),
        ("info_results", "📦 已获取"),
        ("douyin_index", "📈 抖音指数"),
        ("wechat_article", "📰 公众号解析"),
        ("bili_resource", "🎁 B站资源"),
        ("video_comments", "💬 视频评论区"),
    ]),
    ("🗂 资产管理", [
        ("asset", "🗂 资产管理"),
    ]),
    ("🎬 录播工具箱", [
        ("toolbox_organize", "📂 自动整理"),
        ("toolbox_convert", "🔄 视频转换"),
        ("toolbox_rename", "✏️ 重命名"),
        ("toolbox_audio", "🎵 音频提取"),
        ("toolbox_subtitle", "📝 字幕转换"),
        ("toolbox_launcher", "🚀 继续操作"),
        ("toolbox_calendar", "📅 日历浏览"),
        ("toolbox_migrate", "🚚 文件迁移"),
    ]),
    ("🎫 票务购物", [
        ("tickets", "🎫 票务解析"),
        ("shopping", "🛒 购物解析"),
        ("douyin_shop", "🏬 抖音商品"),
    ]),
    ("🎵 音乐服务", [
        ("music_info", "🎵 音乐信息获取"),
        ("netease", "🎧 音乐下载"),
        ("unlock_music", "🔓 音乐解密"),
    ]),
    ("👥 账户追踪", [
        ("music_tracker", "🎼 音乐账号追踪"),
        ("tracker", "📈 网媒账号追踪"),
    ]),
    ("🔴 直播弹幕录制", [
        ("test_page", "📺 抖音弹幕姬"),
        ("bili_danmaku", "📺 B站弹幕姬"),
    ]),
    ("🧰 实用工具", [
        ("image_crop", "🖼 图片裁切"),
        ("cover_maker", "🎨 套版封面制作"),
        ("image_sorter", "🎞 媒体筛选分类"),
        ("media_compress", "🗜 媒体压缩"),
        ("rename_batch", "✏️ 批量重命名"),
        ("file_time", "🕓 文件时间修改"),
        ("music_tag", "🏷 音乐标签"),
        ("lyrics_tool", "🎤 歌词转换"),
    ]),
    ("⚙️ 全局", [
        ("global_settings", "⚙️ 全局设置"),
    ]),
]

# Cookie 管理平台列表：(key, 中文名, 是否支持扫码登录, 是否支持浏览器登录)
# 酷狗概念版登录态是会话文件（非 Cookie），计数时单独判断
COOKIE_PLATFORMS = [
    ("bilibili", "B站", True, True),
    ("douyin", "抖音", False, True),
    ("weibo", "微博", False, True),
    ("netease", "网易云", True, False),
    ("qq", "QQ音乐", True, True),
    ("kugou", "酷狗", False, True),
    ("kugou_lite", "酷狗概念版", True, False),
    ("kuwo", "酷我", False, True),
    ("5sing", "5sing", False, True),
    ("xiaohongshu", "小红书", False, True),
    ("kuaishou", "快手", False, True),
]

# 回车键分发时，这些控件的回车留给控件自身处理（对应原版 event.widget 判断）
_TEXT_INPUT_TYPES = None  # 延迟初始化（QApplication 未建时不能 import 全部控件类型）


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        from version import APP_VERSION, APP_DISPLAY_NAME
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION} · 制作歆慕")
        # 默认 1120×700（原 1600×1000 缩小 30%），小屏自动收到可用区域内
        self.resize(1120, 700)
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() < 1120 or avail.height() < 700:
                self.resize(min(1120, avail.width()), min(700, avail.height()))
        self.setMinimumSize(880, 560)

        # 全局工作目录（持久化）
        cfg = load_convert_config()
        saved = cfg.get("work_dir", "")
        self.work_dir = saved if saved and os.path.isdir(saved) else os.getcwd()

        # 页面懒加载状态
        self.pages = {}
        self.nav_buttons = {}
        self.current_page = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 左侧导航：外部容器 = 上（可滚动导航）+ 下（固定全局区，不随导航滚动）
        nav_panel = QWidget()
        nav_panel.setFixedWidth(210)
        nav_v = QVBoxLayout(nav_panel)
        nav_v.setContentsMargins(0, 0, 0, 0)
        nav_v.setSpacing(0)

        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QScrollArea.NoFrame)
        nav_widget = QWidget()
        self.nav_layout = QVBoxLayout(nav_widget)
        self.nav_layout.setContentsMargins(8, 8, 8, 8)
        self.nav_layout.setSpacing(3)
        nav_scroll.setWidget(nav_widget)
        nav_v.addWidget(nav_scroll, 1)

        # 底部固定区：Cookie 状态 + 主题切换 + 全局设置入口（全局设置在主题按钮下方）
        self.bottom_layout = QVBoxLayout()
        self.bottom_layout.setContentsMargins(8, 0, 8, 8)
        self.bottom_layout.setSpacing(3)
        nav_v.addLayout(self.bottom_layout)
        layout.addWidget(nav_panel)

        # 右侧内容（懒加载）
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self._build_nav(self.nav_layout)
        # 侧栏底部固定区（Cookie 总开关）复选框 toast 绑定（页面内的在各页懒加载时绑）
        common.bind_checkbox_toasts(nav_panel)

        self.statusBar().showMessage("就绪")

        # 全局拖放
        self.setAcceptDrops(True)

        # 回车键统一分发（安装到 QApplication，所有子控件共享）
        app = QApplication.instance()
        app.installEventFilter(self)

        # 骨架加载：先显示骨架占位页（窗口立即可见），真实首页/重依赖初始化
        # 延迟到事件循环启动后（QTimer.singleShot(0)），避免启动期间界面空白转圈
        self._theme_dirty = set()  # 主题切换后待刷新的页面 key
        self.skeleton = SkeletonPage()
        self.stack.addWidget(self.skeleton)
        self.stack.setCurrentWidget(self.skeleton)
        first_key = NAV_GROUPS[0][1][0][0]
        QTimer.singleShot(0, lambda: self._deferred_boot(first_key))

        # 主题定时开关：启动对齐一次 + 每 10 秒强制对齐（到点即变，无需手动触发）
        self._check_theme_schedule()
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._check_theme_schedule)
        self._theme_timer.start(10_000)

    def _deferred_boot(self, first_key):
        """事件循环就绪后的启动收尾：构造首个真实页面 + 重依赖初始化。"""
        if first_key in PAGE_CLASSES:
            self.show_page(first_key)
        # 骨架退场
        if self.skeleton is not None:
            self.stack.removeWidget(self.skeleton)
            self.skeleton.deleteLater()
            self.skeleton = None
        # 延迟刷新 Cookie 状态（内部 import auth 等重依赖，避免阻塞启动）
        self._refresh_cookie_status()
        # 恢复上次「已获取」的数据（config/collected.json，重启不丢）
        common.load_collected()
        # 保存了深色主题时，把构造期内联样式统一适配（含 styleSheet= 关键字参数）
        if theme.is_dark():
            theme.adapt(self)

    # ==================== 侧栏 ====================

    def _build_nav(self, nav_layout):
        title = QLabel("📊 数据汇总库 · 工具箱")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 6px 4px 10px 4px;")
        nav_layout.addWidget(title)

        # 「全局」组按钮延迟到主题切换按钮之后再添加（全局设置放深色主题下方）
        bottom_buttons = []
        for group_title, items in NAV_GROUPS:
            # 「全局」组放底部固定区（只留按钮，不再加分组标题），其余放可滚动导航区
            is_bottom = group_title == "⚙️ 全局"
            if is_bottom:
                sep = QLabel("─" * 14)
                sep.setStyleSheet("color: #b6bdca; font-size: 11px; padding: 0; background: transparent;")
                self.bottom_layout.addWidget(sep)
            else:
                gl = QLabel(group_title)
                gl.setStyleSheet("color: #5a6072; font-size: 11px; padding: 10px 4px 2px 4px;")
                nav_layout.addWidget(gl)
            for key, item_title in items:
                btn = QPushButton(item_title)
                btn.setCheckable(True)
                # 底部固定区按钮文字居中（与主题方块同行、缩短让位），导航区左对齐
                btn.setStyleSheet("text-align: center; padding: 5px 8px;" if is_bottom
                                  else "text-align: left; padding: 5px 8px;")
                btn.clicked.connect(lambda checked, k=key: self.show_page(k))
                if is_bottom:
                    bottom_buttons.append(btn)
                else:
                    nav_layout.addWidget(btn)
                self.nav_buttons[key] = btn
        nav_layout.addStretch(1)

        # 底部固定区：Cookie 状态 + 「全局设置（居中缩短）+ 主题方块」一行
        self._build_cookie_bar(self.bottom_layout)

        # 主题切换方块（正方形：深色显 🌙 / 浅色显 ☀️，点击切换，持久化）
        theme_row = QHBoxLayout()
        theme_row.setSpacing(4)
        for btn in bottom_buttons:
            theme_row.addWidget(btn, 1)
        self.theme_btn = QPushButton("🌙")
        self.theme_btn.setFixedSize(30, 30)
        self.theme_btn.setStyleSheet(common.icon_btn_style(True, "font-size: 16px;"))
        self.theme_btn.setToolTip("切换 浅色 / 深色 界面（自动保存）")
        self.theme_btn.clicked.connect(self._toggle_theme)
        theme_row.addWidget(self.theme_btn)
        self.bottom_layout.addLayout(theme_row)
        self._update_theme_btn()

    def _toggle_theme(self):
        name = theme.toggle_theme()
        theme.apply_theme()
        theme.retheme_all(self)
        self._notify_theme_changed()
        self._update_theme_btn()
        msg = f"已切换到{'深色' if name == 'dark' else '浅色'}主题"
        # 手动优先：手动切换时关闭自动切换（跟随系统/定时，需到全局设置里重新打开）
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            auto_off = False
            if cfg.get("theme_schedule_enabled"):
                cfg["theme_schedule_enabled"] = False
                auto_off = True
            if cfg.get("theme_follow_system"):
                cfg["theme_follow_system"] = False
                auto_off = True
            if auto_off:
                save_convert_config(cfg)
                msg += "（已关闭自动切换）"
        except Exception:
            pass
        show_toast(self, msg, "success")
        # 修复 Bug：手动切换后，全局设置面板仍显示「定时切换:已启用」
        # 同时实时同步到本页 —— 不必等用户切到设置 Tab 才刷新
        settings_page = self.pages.get("global_settings")
        if settings_page is not None and hasattr(settings_page, "sync_theme_schedule_from_config"):
            try:
                settings_page.sync_theme_schedule_from_config()
            except Exception:
                pass

    def _check_theme_schedule(self):
        """自动主题检查：跟随系统优先、定时切换兜底（到点即变）。"""
        try:
            src, name = theme.check_auto_switch()
        except Exception:
            return
        if not name:
            return
        theme.apply_theme()
        theme.retheme_all(self)
        self._notify_theme_changed()
        self._update_theme_btn()
        label = "🖥 已跟随系统" if src == "system" else "⏰ 已定时"
        show_toast(self, f"{label}切换到{'深色' if name == 'dark' else '浅色'}主题", "info")

    def _notify_theme_changed(self):
        """主题切换后：只通知当前页刷新自绘 HTML 内容（QTextBrowser 等富文本
        内容色需页面自行重建）；未激活页记入 _theme_dirty，切回时在 show_page 补通知，
        避免一次切换唤醒全部已加载页面的重建逻辑。"""
        current_page = self.pages.get(self.current_page)
        for key, page in self.pages.items():
            if page is None:
                continue
            if page is not current_page:
                self._theme_dirty.add(key)
                continue
            try:
                cb = getattr(page, "on_theme_changed", None)
                if cb:
                    cb()
            except Exception:
                pass

    def _update_theme_btn(self):
        # 方块图标反映当前主题：深色显 🌙 / 浅色显 ☀️
        self.theme_btn.setText("🌙" if theme.is_dark() else "☀️")

    def _build_cookie_bar(self, nav_layout):
        box = QWidget()
        box.setStyleSheet("QWidget { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(4)

        head = QHBoxLayout()
        lbl = QLabel("🍪 Cookie")
        lbl.setStyleSheet("font-weight: bold; font-size: 12px; padding: 0; background: transparent;")
        head.addWidget(lbl)
        head.addStretch(1)
        self.cookie_toggle = QCheckBox("总开关")
        self.cookie_toggle.setStyleSheet("font-size: 11px; padding: 0; background: transparent;")
        self.cookie_toggle.toggled.connect(self._toggle_cookie)
        head.addWidget(self.cookie_toggle)
        v.addLayout(head)

        self.cookie_status_label = QLabel("加载中...")
        self.cookie_status_label.setWordWrap(True)
        self.cookie_status_label.setStyleSheet(
            "font-size: 11px; color: #9aa0b4; padding: 0; background: transparent;")
        v.addWidget(self.cookie_status_label)

        mgr_btn = QPushButton("管理 Cookie")
        mgr_btn.setStyleSheet("font-size: 11px; padding: 3px;")
        mgr_btn.clicked.connect(self._open_cookie_manager)
        v.addWidget(mgr_btn)

        nav_layout.addWidget(box)

    def _refresh_cookie_status(self):
        try:
            from auth import load_cookies, is_cookie_enabled, get_netease_cookie_stored
            enabled = is_cookie_enabled()
            cookies = load_cookies()
            netease_ok = bool(get_netease_cookie_stored().strip())
            logged = 0
            total = len(COOKIE_PLATFORMS)
            for plat, _name, _qr, _browser in COOKIE_PLATFORMS:
                if plat == "netease":
                    ok = netease_ok
                elif plat == "kugou_lite":
                    try:
                        from music.scrapers.kugou_lite import session_logged_in
                        ok = session_logged_in()
                    except Exception:
                        ok = False
                else:
                    ok = bool(cookies.get(plat, "") and len(cookies.get(plat, "")) > 10)
                if ok:
                    logged += 1
            self.cookie_toggle.blockSignals(True)
            self.cookie_toggle.setChecked(enabled)
            self.cookie_toggle.blockSignals(False)
            self.cookie_status_label.setText(f"总开关 {'开' if enabled else '关'} · 已登录 {logged}/{total}")
        except Exception:
            pass

    def _toggle_cookie(self, checked):
        from auth import set_cookie_enabled
        set_cookie_enabled(bool(checked))
        self._refresh_cookie_status()

    def _open_cookie_manager(self):
        from gui_qt.cookie_manager import CookieManagerDialog
        dlg = CookieManagerDialog(self)
        dlg.exec()
        self._refresh_cookie_status()

    # ==================== 页面切换 ====================

    def show_page(self, key):
        if key not in PAGE_CLASSES:
            return
        # 高亮当前按钮
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)

        # 退出旧页面钩子（全局同步：切走时停后台任务）
        old_page = self.pages.get(self.current_page)
        if old_page is not None and self.current_page != key and hasattr(old_page, "on_hide"):
            try:
                old_page.on_hide()
            except Exception:
                pass

        # 懒加载
        if key not in self.pages:
            page = _page_class(key)(self)
            self.stack.addWidget(page)
            self.pages[key] = page
            # 功能型复选框：点击时右下角 toast「已开启/已关闭 XXX」
            common.bind_checkbox_toasts(page)

        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.current_page = key
        # 主题自适应：构造期 styleSheet= 关键字参数与动态新增的行在此兜底
        theme.adapt(page)
        # 主题切换期间未激活（跳过了 on_theme_changed 通知）的页：切回时补通知
        if key in self._theme_dirty:
            self._theme_dirty.discard(key)
            try:
                cb = getattr(page, "on_theme_changed", None)
                if cb:
                    cb()
            except Exception:
                pass
        # 触发页面刷新钩子（如「已获取」页切回时刷新列表）
        if hasattr(page, "on_show"):
            page.on_show()

    def closeEvent(self, event):
        # 关闭窗口：对所有已加载页面触发全停（停抓取/录制/回放等）
        for page in self.pages.values():
            fn = getattr(page, "shutdown", None) or getattr(page, "on_hide", None)
            if fn:
                try:
                    fn()
                except Exception:
                    pass
        super().closeEvent(event)

    # ==================== 回车键统一分发 ====================

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            from PySide6.QtWidgets import (
                QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
            )
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox)):
                return super().eventFilter(obj, event)  # 留给控件自身处理
            page = self.pages.get(self.current_page)
            if page is not None and hasattr(page, "on_return_press"):
                try:
                    page.on_return_press()
                    return True
                except Exception:
                    pass
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        # ESC 分发到当前页（重命名页跳过等）
        if event.key() == Qt.Key_Escape:
            page = self.pages.get(self.current_page)
            if page is not None and hasattr(page, "on_escape_press"):
                try:
                    page.on_escape_press()
                    return
                except Exception:
                    pass
        super().keyPressEvent(event)

    # ==================== 拖放处理 ====================

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(u.isLocalFile() for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if not files:
            return

        dirs = [f for f in files if os.path.isdir(f)]
        file_list = [f for f in files if os.path.isfile(f)]

        # 字幕转换页 / 歌词转换页 / 音乐标签页 / 图片裁切页 / 媒体筛选分类页 / 媒体压缩页
        # / 音频提取页：接受拖放的文件或文件夹（本地列表，不改全局目录）
        if self.current_page in ("toolbox_subtitle", "lyrics_tool", "toolbox_lyrics",
                                 "music_tag", "image_crop", "image_sorter",
                                 "media_compress", "toolbox_audio"):
            p = self.pages.get(self.current_page)
            if p:
                for d in dirs:
                    p.add_dropped_folder(d)
                if file_list:
                    p.add_dropped_files(file_list)
            return

        # 弹幕测试页：拖入 zip 回放包 → 进入回放模式
        if self.current_page == "test_page":
            p = self.pages.get("test_page")
            zips = [f for f in file_list if f.lower().endswith(".zip")]
            if p is not None and zips:
                p.load_replay_zip(zips[0])
                return

        # 其他页：拖入文件夹 → 设为工作目录（锁定时拒绝改变，直到解锁）
        target = dirs[0] if dirs else os.path.dirname(file_list[0])
        if load_convert_config().get("work_dir_locked"):
            self.toast("🔒 默认工作文件夹已锁定，拖入文件夹不改变（可在全局设置解锁）", "info")
            return
        self.work_dir = target
        self._refresh_all()
        self.toast(f"✅ 已切换: {target}", "success")

    # ==================== 公共方法 ====================

    def _refresh_all(self):
        for page in self.pages.values():
            fn = getattr(page, "on_dir_changed", None)
            if fn:
                try:
                    fn(self.work_dir)
                except Exception:
                    pass
        # 持久化工作目录
        cfg = load_convert_config()
        cfg["work_dir"] = self.work_dir
        save_convert_config(cfg)

    def validate_work_dir(self) -> bool:
        """校验工作目录；不存在/无日期文件夹时直接弹目录选择器就地修正。

        免去「提示 → 进设置页 → 改目录 → 保存 → 回来重新扫描」的流程：
        就地选目录即保存全局生效，随后立即继续原操作。
        """
        if not os.path.isdir(self.work_dir):
            self.toast(f"工作目录不存在: {self.work_dir}", "warning")
            if not self._pick_work_dir("选择工作目录（录播文件所在位置）"):
                return False
        if not has_date_subfolders(self.work_dir):
            self.toast("未检测到日期文件夹（YYYYMMDD），请选择正确的录播目录", "warning")
            if not self._pick_work_dir("选择包含日期文件夹（YYYYMMDD）的工作目录"):
                return False
            # 新选目录仍无日期文件夹 → 尊重用户选择，按当前目录继续
            if not has_date_subfolders(self.work_dir):
                self.toast("该目录仍无日期文件夹，按当前目录继续扫描", "info")
        return True

    def _pick_work_dir(self, title) -> bool:
        """就地弹目录选择器修正工作目录：选中即保存并全局生效。"""
        from PySide6.QtWidgets import QFileDialog
        picked = QFileDialog.getExistingDirectory(self, title, self.work_dir or "")
        if not picked:
            return False
        self.work_dir = picked
        self._refresh_all()
        return True

    def toast(self, msg, kind="info"):
        show_toast(self, msg, kind=kind)
