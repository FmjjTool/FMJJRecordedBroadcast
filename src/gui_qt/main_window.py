"""PySide6 主窗口：左侧导航 + 右侧 QStackedWidget 懒加载。

与原版（customtkinter）一一对应：
- 侧栏底部 Cookie 状态区（总开关 + 已登录计数 + 管理入口）
- 全局拖放（字幕/歌词/音乐标签/图片裁切/媒体筛选/媒体压缩/音频提取页接收文件，其他页切换工作目录）
- 工作目录持久化 + on_dir_changed 广播
- 回车键统一分发到当前页 on_return_press（输入框聚焦时不拦截）
"""

import os

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt import common, theme
from gui_qt.navigation import line_icon, tool_icon_name
from gui_qt.toolbox_common import has_date_subfolders
from gui_qt.skeleton import SkeletonPage
from gui_qt.toast import show_toast

# 页面注册：key -> (模块路径, 类名)。
# 懒导入：首次切到该页才 import（启动只加载首页，避免全部页面模块
# 一次性 import 拖慢启动、也避免把各页的重依赖提前拉起）。
PAGE_CLASSES = {
    "home": ("gui_qt.pages.home", "HomePage"),
    "info_accounts": ("gui_qt.pages.info_accounts", "InfoAccountsPage"),
    "info_results": ("gui_qt.pages.info_results", "InfoResultsPage"),
    "douyin_index": ("gui_qt.pages.douyin_index", "DouyinIndexPage"),
    "wechat_article": ("gui_qt.pages.wechat_article", "WechatArticlePage"),
    "asset": ("gui_qt.pages.asset", "AssetPage"),
    "toolbox_organize": ("gui_qt.pages.organize", "OrganizePage"),
    "toolbox_convert": ("gui_qt.pages.convert", "ConvertPage"),
    "toolbox_editor": ("gui_qt.pages.editor", "EditorPage"),
    "toolbox_audio": ("gui_qt.pages.audio", "AudioPage"),
    "toolbox_subtitle": ("gui_qt.pages.subtitle", "SubtitlePage"),
    "toolbox_launcher": ("gui_qt.pages.launcher", "LauncherPage"),
    "toolbox_calendar": ("gui_qt.pages.calendar", "CalendarPage"),
    "toolbox_migrate": ("gui_qt.pages.migrate", "MigratePage"),
    "music_search_download": ("gui_qt.pages.containers", "MusicSearchDownloadPage"),
    "music_file_tools": ("gui_qt.pages.music_tag", "MusicTagPage"),
    "info_tools": ("gui_qt.pages.containers", "InfoToolsPage"),
    "tracker": ("gui_qt.pages.tracker", "TrackerPage"),
    "music_tracker": ("gui_qt.pages.music_tracker", "MusicTrackerPage"),
    "test_page": ("gui_qt.pages.test_page", "TestPage"),
    "bili_resource": ("gui_qt.pages.bili_resource", "BiliResourcePage"),
    "video_comments": ("gui_qt.pages.video_comments", "VideoCommentsPage"),
    "tcdoc": ("gui_qt.pages.tcdoc", "TcDocPage"),
    "rename_batch": ("gui_qt.pages.rename_batch", "RenameBatchPage"),
    "tickets": ("gui_qt.pages.tickets_page", "TicketsPage"),
    "shopping": ("gui_qt.pages.shopping_page", "ShoppingPage"),
    "douyin_shop": ("gui_qt.pages.douyin_shop_page", "DouyinShopPage"),
    "bili_shop": ("gui_qt.pages.bili_shop_page", "BiliShopPage"),
    "fan_groups": ("gui_qt.pages.fan_groups_page", "FanGroupsPage"),
    "bili_danmaku": ("gui_qt.pages.bili_danmaku", "BiliDanmakuPage"),
    "image_crop": ("gui_qt.pages.image_crop", "ImageCropPage"),
    "cover_maker": ("gui_qt.pages.cover_maker", "CoverMakerPage"),
    "image_sorter": ("gui_qt.pages.image_sorter", "ImageSorterPage"),
    "global_settings": ("gui_qt.pages.settings", "GlobalSettingsPage"),
    "tasks": ("gui_qt.pages.tasks", "TasksPage"),
}


def _page_class(key):
    """按注册表懒导入页面类。"""
    import importlib
    mod_name, cls_name = PAGE_CLASSES[key]
    return getattr(importlib.import_module(mod_name), cls_name)

NAV_GROUPS = [
    ("📥 内容采集", [
        ("info_accounts", "🔍 信息获取"),
        ("info_results", "📦 已获取"),
        ("info_tools", "🧰 信息工具"),
    ]),
    ("🗂 资产管理", [
        ("asset", "🗂 资产管理"),
    ]),
    ("🎬 录播工具箱", [
        ("toolbox_organize", "📂 自动整理"),
        ("toolbox_convert", "🔄 视频转换"),
        ("toolbox_editor", "🎬 视频剪辑"),
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
        ("bili_shop", "🛍 B站小店"),
    ]),
    ("🎵 音乐工具", [
        ("music_search_download", "🎵 音乐搜索下载"),
        ("music_file_tools", "🏷 音乐标签"),
    ]),
    ("👥 账户追踪", [
        ("music_tracker", "🎼 音乐账号追踪"),
        ("tracker", "📈 网媒账号追踪"),
        ("fan_groups", "👥 粉丝群查询"),
    ]),
    ("🔴 直播弹幕录制", [
        ("test_page", "📺 抖音弹幕姬"),
        ("bili_danmaku", "📺 B站弹幕姬"),
    ]),
    ("🧰 本地工具", [
        ("image_crop", "🖼 图片裁切"),
        ("cover_maker", "🎨 套版封面制作"),
        ("image_sorter", "🎞 媒体筛选分类"),
        ("rename_batch", "✏️ 批量重命名"),
    ]),
    ("⚙️ 全局", [
        ("tasks", "⏰ 定时任务"),
        ("global_settings", "⚙️ 全局设置"),
    ]),
]

# Cookie 管理平台列表取自 gui_qt.cookie_manager.COOKIE_PLATFORMS（单一事实来源），
# 这里不再复制一份：以前两处各写一遍，新增平台时计数口径会悄悄对不上。

# 回车键分发时，这些控件的回车留给控件自身处理（对应原版 event.widget 判断）
_TEXT_INPUT_TYPES = None  # 延迟初始化（QApplication 未建时不能 import 全部控件类型）

_EXTRA_TOOLS = {
    "douyin_index": "抖音指数", "wechat_article": "公众号解析",
    "bili_resource": "B站资源", "video_comments": "视频评论区",
    "tcdoc": "腾讯文档监控",
}

_SHELL_QSS = """
QWidget#workbenchSidebar { background: palette(base); border-right: 1px solid palette(shadow); }
QWidget#workbenchNavItems, QWidget#workbenchNavViewport, QScrollArea#workbenchNavScroll,
QWidget#workbenchNavGroups, QWidget#workbenchSearchResults { background: transparent; border: none; }
QPushButton#workbenchBrand { background: transparent; color: palette(text); border: none; text-align: left; font-size: 17px; font-weight: bold; padding: 0; }
QPushButton#workbenchBrand:hover { color: palette(link); }
QLabel#workbenchBrandMark { background: palette(highlight); color: palette(highlighted-text); border: none; border-radius: 10px; font-family: "Segoe UI"; font-size: 18px; font-weight: bold; }
QLabel#workbenchBrandSub { background: transparent; color: palette(placeholder-text); border: none; font-size: 10px; }
QLineEdit#workbenchSearch { background: palette(window); border: 1px solid palette(shadow); border-radius: 8px; padding: 8px 10px; font-size: 12px; }
QLineEdit#workbenchSearch:focus { border-color: palette(highlight); }
QLabel#workbenchGroup { background: transparent; color: palette(placeholder-text); border: none; font-size: 10px; font-weight: bold; padding: 15px 10px 5px; }
QPushButton#workbenchNavButton { background: transparent; color: palette(text); border: 1px solid transparent; border-radius: 8px; text-align: left; padding: 8px 11px; font-size: 12px; }
QPushButton#workbenchNavButton:hover { background: palette(alternate-base); color: palette(link); }
QPushButton#workbenchNavButton:checked { background: palette(midlight); color: palette(link); border-color: palette(midlight); font-weight: bold; }
QPushButton#workbenchNavButton:focus { border-color: palette(highlight); }
QFrame#workbenchTopbar { background: palette(base); border: none; border-bottom: 1px solid palette(shadow); }
QLabel#workbenchContext { color: palette(placeholder-text); background: transparent; font-size: 12px; }
QLabel#workbenchPageTitle { color: palette(text); background: transparent; font-size: 13px; font-weight: bold; }
QPushButton#workbenchDirectory { background: transparent; color: palette(placeholder-text); border: none; padding: 7px 10px; text-align: right; font-size: 11px; }
QPushButton#workbenchDirectory:hover { background: palette(alternate-base); color: palette(link); }
QWidget#workbenchCookie { background: palette(window); border: 1px solid palette(shadow); border-radius: 10px; }
QLabel#workbenchCookieTitle { background: transparent; color: palette(text); font-size: 12px; font-weight: bold; border: none; }
QCheckBox#workbenchCookieToggle { background: transparent; border: none; font-size: 11px; }
QLabel#workbenchCookieStatus { background: transparent; color: palette(placeholder-text); border: none; font-size: 10px; }
QPushButton#workbenchCookieManage { background: transparent; color: palette(link); border: none; padding: 0; font-size: 11px; text-align: right; }
QPushButton#workbenchFooter { background: transparent; color: palette(placeholder-text); border: 1px solid transparent; border-radius: 7px; padding: 7px 2px; font-size: 11px; }
QPushButton#workbenchFooter:hover, QPushButton#workbenchFooter:checked { background: palette(midlight); color: palette(link); }
QLabel#workbenchEmptySearch { background: transparent; color: palette(placeholder-text); padding: 18px 10px; font-size: 12px; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        from version import APP_VERSION, APP_DISPLAY_NAME
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION} · 制作歆慕")
        # 默认给多功能页面留足空间，小屏仍可缩到 880×560。
        self.resize(1240, 820)
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() < 1240 or avail.height() < 820:
                self.resize(min(1240, avail.width()), min(820, avail.height()))
        self.setMinimumSize(880, 560)

        # 全局工作目录（持久化）
        cfg = load_convert_config()
        saved = cfg.get("work_dir", "")
        self.work_dir = saved if saved and os.path.isdir(saved) else os.getcwd()

        # 页面懒加载状态
        self.pages = {}
        self.nav_buttons = {}
        self._search_buttons = {}
        self._tool_names = {"home": ("工作台", "总览")}
        for group_title, items in NAV_GROUPS:
            for key, title in items:
                self._tool_names[key] = (title.split(" ", 1)[-1], group_title.split(" ", 1)[-1])
        self._tool_names.update({key: (title, "信息工具") for key, title in _EXTRA_TOOLS.items()})
        self.current_page = None

        central = QWidget()
        central.setStyleSheet(_SHELL_QSS)
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 左侧导航：外部容器 = 上（可滚动导航）+ 下（固定全局区，不随导航滚动）
        nav_panel = self.sidebar = QWidget()
        nav_panel.setObjectName("workbenchSidebar")
        nav_panel.setAttribute(Qt.WA_StyledBackground, True)
        nav_panel.setFixedWidth(228)
        nav_v = QVBoxLayout(nav_panel)
        nav_v.setContentsMargins(0, 0, 0, 0)
        nav_v.setSpacing(0)

        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(18, 23, 14, 18)
        brand_row.setSpacing(10)
        mark = QLabel("F")
        mark.setObjectName("workbenchBrandMark")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        brand_row.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        self.nav_title = QPushButton("封茗工作台")
        self.nav_title.setObjectName("workbenchBrand")
        self.nav_title.setCheckable(True)
        self.nav_title.setCursor(Qt.PointingHandCursor)
        self.nav_title.setToolTip("返回首页")
        self.nav_title.clicked.connect(lambda: self.show_page("home"))
        brand_text.addWidget(self.nav_title)
        brand_sub = QLabel(f"数据汇总库  /  v{APP_VERSION}")
        brand_sub.setObjectName("workbenchBrandSub")
        brand_text.addWidget(brand_sub)
        brand_row.addLayout(brand_text, 1)
        nav_v.addWidget(brand)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(14, 0, 14, 10)
        self.nav_search = QLineEdit()
        self.nav_search.setObjectName("workbenchSearch")
        self.nav_search.setPlaceholderText("搜索工具  ·  Ctrl+K")
        self.nav_search.setClearButtonEnabled(True)
        self.nav_search.setToolTip("搜索全部工具，输入平台或功能名称；回车打开首个结果")
        search_row.addWidget(self.nav_search)
        nav_v.addLayout(search_row)

        nav_scroll = self.nav_scroll = QScrollArea()
        nav_scroll.setObjectName("workbenchNavScroll")
        nav_scroll.viewport().setObjectName("workbenchNavViewport")
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QScrollArea.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_widget = QWidget()
        nav_widget.setObjectName("workbenchNavItems")
        nav_container = QVBoxLayout(nav_widget)
        nav_container.setContentsMargins(12, 0, 12, 10)
        nav_container.setSpacing(0)
        self.nav_groups = QWidget()
        self.nav_groups.setObjectName("workbenchNavGroups")
        self.nav_layout = QVBoxLayout(self.nav_groups)
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(2)
        nav_container.addWidget(self.nav_groups)
        self.search_results = QWidget()
        self.search_results.setObjectName("workbenchSearchResults")
        self.search_layout = QVBoxLayout(self.search_results)
        self.search_layout.setContentsMargins(0, 0, 0, 0)
        self.search_layout.setSpacing(2)
        nav_container.addWidget(self.search_results)
        nav_container.addStretch(1)
        self.search_results.hide()
        nav_scroll.setWidget(nav_widget)
        nav_v.addWidget(nav_scroll, 1)

        # 底部固定区：Cookie 状态 + 主题切换 + 全局设置入口（全局设置在主题按钮下方）
        self.bottom_layout = QVBoxLayout()
        self.bottom_layout.setContentsMargins(12, 10, 12, 12)
        self.bottom_layout.setSpacing(8)
        nav_v.addLayout(self.bottom_layout)
        layout.addWidget(nav_panel)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_topbar())
        # 右侧内容仍使用同一个栈，页面生命周期、快捷键和拖放路由不变。
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)

        self._build_nav(self.nav_layout)
        self._build_search_results()
        self.nav_search.textChanged.connect(self._filter_navigation)
        self.nav_search.returnPressed.connect(self._open_first_search_result)
        self._search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._search_shortcut.activated.connect(self._focus_search)
        self._sync_shell_icons()
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
        first_key = "home" if "home" in PAGE_CLASSES else NAV_GROUPS[0][1][0][0]
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

    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName("workbenchTopbar")
        bar.setFixedHeight(56)
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 0, 22, 0)
        row.setSpacing(10)
        self.context_label = QLabel("总览  / ")
        self.context_label.setObjectName("workbenchContext")
        row.addWidget(self.context_label)
        self.page_title_label = QLabel("工作台")
        self.page_title_label.setObjectName("workbenchPageTitle")
        row.addWidget(self.page_title_label)
        row.addStretch(1)
        self.directory_btn = QPushButton()
        self.directory_btn.setObjectName("workbenchDirectory")
        self.directory_btn.setCursor(Qt.PointingHandCursor)
        self.directory_btn.clicked.connect(lambda: self.show_page("global_settings"))
        self.directory_btn.setMaximumWidth(260)
        row.addWidget(self.directory_btn)
        self._update_directory_label()
        return bar

    def _update_directory_label(self):
        name = os.path.basename(os.path.normpath(self.work_dir)) or self.work_dir
        label = self.directory_btn.fontMetrics().elidedText(name, Qt.ElideMiddle, 145)
        self.directory_btn.setText(f"工作目录  {label}")
        self.directory_btn.setToolTip(f"当前工作目录：{self.work_dir}\n点击在全局设置中管理")

    def _make_nav_button(self, key, title):
        button = QPushButton(title)
        button.setObjectName("workbenchNavButton")
        button.setIconSize(QSize(18, 18))
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(title)
        return button

    def _build_nav(self, nav_layout):
        self.home_btn = self._make_nav_button("home", "工作台")
        self.home_btn.clicked.connect(lambda: self.show_page("home"))
        nav_layout.addWidget(self.home_btn)
        bottom_buttons = []
        for group_title, items in NAV_GROUPS:
            is_bottom = group_title == "⚙️ 全局"
            if not is_bottom:
                gl = QLabel(group_title.split(" ", 1)[-1])
                gl.setObjectName("workbenchGroup")
                nav_layout.addWidget(gl)
            for key, item_title in items:
                btn = self._make_nav_button(key, item_title.split(" ", 1)[-1])
                if is_bottom:
                    btn.setObjectName("workbenchFooter")
                    btn.setText("任务" if key == "tasks" else "设置")
                    btn.setFixedHeight(34)
                    bottom_buttons.append(btn)
                else:
                    nav_layout.addWidget(btn)
                btn.clicked.connect(lambda checked, k=key: self.show_page(k))
                self.nav_buttons[key] = btn

        self._build_cookie_bar(self.bottom_layout)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(2)
        for btn in bottom_buttons:
            theme_row.addWidget(btn, 1)
        self.theme_btn = QPushButton("主题")
        self.theme_btn.setObjectName("workbenchFooter")
        self.theme_btn.setFixedHeight(34)
        self.theme_btn.setIconSize(QSize(18, 18))
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.theme_btn.setToolTip("切换 浅色 / 深色 界面（自动保存）")
        self.theme_btn.clicked.connect(self._toggle_theme)
        theme_row.addWidget(self.theme_btn, 1)
        self.bottom_layout.addLayout(theme_row)
        self._update_theme_btn()

    def _build_search_results(self):
        for key in PAGE_CLASSES:
            title, group = self._tool_names.get(key, (key.replace("_", " "), "工具"))
            button = self._make_nav_button(key, title)
            button.setToolTip(f"{group} / {title}")
            button.clicked.connect(lambda checked, k=key: self._open_search_target(k))
            self._search_buttons[key] = button
            self.search_layout.addWidget(button)
        self.search_empty = QLabel("没有匹配的工具\n试试平台或功能名称")
        self.search_empty.setObjectName("workbenchEmptySearch")
        self.search_empty.setWordWrap(True)
        self.search_layout.addWidget(self.search_empty)
        self.search_empty.hide()

    def _filter_navigation(self, text):
        query = text.strip().casefold()
        self.nav_groups.setVisible(not query)
        self.search_results.setVisible(bool(query))
        matched = 0
        for key, button in self._search_buttons.items():
            title, group = self._tool_names.get(key, (key, ""))
            visible = query in f"{title} {group} {key}".casefold()
            button.setVisible(visible)
            matched += visible
        self.search_empty.setVisible(bool(query) and not matched)
        self.nav_scroll.verticalScrollBar().setValue(0)

    def _focus_search(self):
        self.nav_search.setFocus()
        self.nav_search.selectAll()

    def _open_first_search_result(self):
        if not self.nav_search.text().strip():
            return
        for key, button in self._search_buttons.items():
            if not button.isHidden():
                self._open_search_target(key)
                return

    def _open_search_target(self, key):
        self.nav_search.clear()
        self.show_page(key)
        self.nav_search.clearFocus()

    def _update_page_context(self, *_args):
        key = self.current_page
        page = self.pages.get(key)
        if key == "info_tools" and hasattr(page, "TAB_KEYS"):
            index = page.tabs.currentIndex()
            if 0 <= index < len(page.TAB_KEYS):
                key = page.TAB_KEYS[index]
        title, group = self._tool_names.get(key, (key or "工作台", "总览"))
        self.context_label.setText(f"{group}  / ")
        self.page_title_label.setText(title)
        for search_key, button in self._search_buttons.items():
            button.setChecked(search_key == key)
        self._sync_shell_icons()

    def _sync_shell_icons(self):
        palette = theme.current_palette()
        normal = palette.color(QPalette.PlaceholderText).name()
        active = palette.color(QPalette.Link).name()
        self.home_btn.setIcon(line_icon("grid", active if self.current_page == "home" else normal))
        for buttons in (self.nav_buttons, self._search_buttons):
            for key, button in buttons.items():
                button.setIcon(line_icon(tool_icon_name(key), active if button.isChecked() else normal))
        self.directory_btn.setIcon(line_icon("folder", normal, 16))
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
        self._sync_shell_icons()
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
        color = theme.current_palette().color(QPalette.PlaceholderText).name()
        self.theme_btn.setIcon(line_icon("moon" if theme.is_dark() else "sun", color, 18))
        self.theme_btn.setToolTip(f"当前为{'深色' if theme.is_dark() else '浅色'}主题，点击切换（自动保存）")

    def _build_cookie_bar(self, nav_layout):
        box = QWidget()
        box.setObjectName("workbenchCookie")
        box.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(box)
        v.setContentsMargins(11, 10, 11, 10)
        v.setSpacing(7)

        head = QHBoxLayout()
        lbl = QLabel("账号与登录")
        lbl.setObjectName("workbenchCookieTitle")
        head.addWidget(lbl)
        head.addStretch(1)
        self.cookie_toggle = QCheckBox("启用")
        self.cookie_toggle.setObjectName("workbenchCookieToggle")
        self.cookie_toggle.setToolTip("Cookie 总开关：启用后允许使用已保存的登录状态")
        self.cookie_toggle.toggled.connect(self._toggle_cookie)
        head.addWidget(self.cookie_toggle)
        v.addLayout(head)

        self.cookie_status_label = QLabel("加载中...")
        self.cookie_status_label.setWordWrap(True)
        self.cookie_status_label.setObjectName("workbenchCookieStatus")
        v.addWidget(self.cookie_status_label)

        mgr_btn = QPushButton("管理 Cookie")
        mgr_btn.setObjectName("workbenchCookieManage")
        mgr_btn.setCursor(Qt.PointingHandCursor)
        mgr_btn.clicked.connect(self._open_cookie_manager)
        v.addWidget(mgr_btn)

        nav_layout.addWidget(box)

    def _refresh_cookie_status(self):
        try:
            from auth import load_cookies, is_cookie_enabled, get_netease_cookie_stored
            enabled = is_cookie_enabled()
            cookies = load_cookies()
            netease_ok = bool(get_netease_cookie_stored().strip())
            from gui_qt.cookie_manager import COOKIE_PLATFORMS as _PLATFORMS
            logged = 0
            total = len(_PLATFORMS)
            for plat, _name, _qr, _browser in _PLATFORMS:
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
        # 原独立信息页的所有入口都落到同一个容器实例，避免搜索/首页/
        # 其他页面跳转各自创建一份采集页，造成重复任务或状态不一致。
        if key in _EXTRA_TOOLS:
            self.show_page("info_tools")
            self.pages["info_tools"].open_tab(key)
            self._update_page_context()
            return
        # 高亮当前按钮（首页不在导航列表里，单独标记标题）
        self.nav_title.setChecked(key == "home")
        self.home_btn.setChecked(key == "home")
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)
        for k, btn in self._search_buttons.items():
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
            if key == "info_tools" and hasattr(page, "tabs"):
                page.tabs.currentChanged.connect(self._update_page_context)
            # 功能型复选框：点击时右下角 toast「已开启/已关闭 XXX」
            common.bind_checkbox_toasts(page)

        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.current_page = key
        self._update_page_context()
        if key not in ("tasks", "global_settings") and not self.nav_search.text().strip():
            button = self.home_btn if key == "home" else self.nav_buttons.get(key)
            if button is not None:
                self.nav_scroll.ensureWidgetVisible(button, 0, 24)
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
        # 直播录制二级窗口（独立窗口，随主程序一起收尾）
        lrw = getattr(self, "live_record_win", None)
        if lrw is not None:
            try:
                lrw.shutdown()
                lrw.close()
            except Exception:
                pass
        super().closeEvent(event)

    # ==================== 回车键统一分发 ====================

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and self.nav_search.hasFocus():
            self.nav_search.clear()
            self.nav_search.clearFocus()
            return True
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
        if self.current_page in ("toolbox_subtitle",
                                 "music_file_tools", "image_crop", "image_sorter",
                                 "toolbox_audio", "toolbox_editor"):
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self.sidebar.setFixedWidth(200 if self.width() < 1040 else 228)
        if hasattr(self, "directory_btn"):
            self.directory_btn.setMaximumWidth(210 if self.width() < 1040 else 300)

    def _refresh_all(self):
        self._update_directory_label()
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
