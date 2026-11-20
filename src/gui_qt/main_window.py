"""PySide6 主窗口：左侧导航 + 右侧 QStackedWidget，首页后分步预载。

与原版（customtkinter）一一对应：
- 侧栏底部 Cookie 状态区（总开关 + 已登录计数 + 管理入口）
- 全局拖放（字幕/歌词/音乐标签/图片裁切/媒体筛选/媒体压缩/音频提取页接收文件，其他页切换工作目录）
- 工作目录持久化 + on_dir_changed 广播
- 回车键统一分发到当前页 on_return_press（输入框聚焦时不拦截）
"""

import os

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt import common, theme
from gui_qt.navigation import line_icon, tool_icon_name
from gui_qt.toolbox_common import has_date_subfolders
from gui_qt.skeleton import SkeletonPage
from gui_qt.toast import show_toast
from gui_qt.page_lifecycle import PageLifecycleManager, PagePolicy, restore_page_state
from gui_qt.page_preloader import PagePreloader
from gui_qt.window_chrome import (
    TitleBar, WindowControlBar, apply_round_corners, install_resize_handles,
)


def _brand_icon_pixmap():
    """侧栏品牌图标：优先项目根 icon.ico，打包环境回落到应用窗口图标。"""
    root_icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "icon.ico")
    icon = QIcon(root_icon) if os.path.exists(root_icon) else QApplication.windowIcon()
    if icon.isNull():
        return None
    # QIcon.pixmap 已按屏幕 DPR 放大物理像素并自带 devicePixelRatio，
    # 这里只传逻辑尺寸 34；再乘 DPR 会把 51 逻辑像素塞进 34 的 QLabel 被裁掉。
    return icon.pixmap(QSize(34, 34))


# 页面注册：key -> (模块路径, 类名)。
# 首页先显示，其余模块随分步预载导入；导航提前点击时立即创建并复用。
PAGE_CLASSES = {
    "home": ("gui_qt.pages.home", "HomePage"),
    "info_accounts": ("gui_qt.pages.info_accounts", "InfoAccountsPage"),
    "info_results": ("gui_qt.pages.info_results", "InfoResultsPage"),
    "douyin_index": ("gui_qt.pages.douyin_index", "DouyinIndexPage"),
    "wechat_article": ("gui_qt.pages.wechat_article", "WechatArticlePage"),
    "asset": ("gui_qt.pages.asset", "AssetPage"),
    "toolbox_organize": ("gui_qt.pages.organize", "OrganizePage"),
    "toolbox_rename": ("gui_qt.pages.rename", "RenamePage"),
    "toolbox_convert": ("gui_qt.pages.editor", "EditorPage"),
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
    "account_registry": ("gui_qt.pages.account_registry", "AccountRegistryPage"),
    "douyin_danmaku": ("gui_qt.pages.douyin_danmaku", "DouyinDanmakuPage"),
    "live_record": ("gui_qt.pages.live_record", "LiveRecordPage"),
    "bili_resource": ("gui_qt.pages.bili_resource", "BiliResourcePage"),
    "video_comments": ("gui_qt.pages.video_comments", "VideoCommentsPage"),
    "weibo_chaohua": ("gui_qt.pages.weibo_chaohua", "WeiboChaohuaPage"),
    "tcdoc": ("gui_qt.pages.tcdoc", "TcDocPage"),
    "localwiki": ("gui_qt.pages.localwiki", "LocalWikiPage"),
    "rename_batch": ("gui_qt.pages.rename_batch", "RenameBatchPage"),
    "tickets": ("gui_qt.pages.tickets_page", "TicketsPage"),
    "shopping": ("gui_qt.pages.shopping_page", "ShoppingPage"),
    "douyin_shop": ("gui_qt.pages.douyin_shop_page", "DouyinShopPage"),
    "bili_shop": ("gui_qt.pages.bili_shop_page", "BiliShopPage"),
    "fan_groups": ("gui_qt.pages.fan_groups_page", "FanGroupsPage"),
    "bili_danmaku": ("gui_qt.pages.bili_danmaku", "BiliDanmakuPage"),
    "image_crop": ("gui_qt.pages.image_crop", "ImageCropPage"),
    "cover_maker": ("gui_qt.pages.cover_maker", "CoverMakerPage"),
    "certificate_maker": ("gui_qt.pages.certificate_maker", "CertificateMakerPage"),
    "image_sorter": ("gui_qt.pages.image_sorter", "ImageSorterPage"),
    "global_settings": ("gui_qt.pages.settings", "GlobalSettingsPage"),
    "livestream_summary": ("gui_qt.pages.livestream_summary", "LivestreamSummaryPage"),
    "netdisk": ("gui_qt.pages.netdisk", "NetdiskPage"),
    "tasks": ("gui_qt.pages.tasks", "TasksPage"),
    "performance": ("gui_qt.pages.performance", "PerformancePage"),
}


PAGE_ALIASES = {
    "test_page": "douyin_danmaku",
    # 网盘备份已并入「网盘」页（下载 / 备份两个页签），旧地址统一指向新页
    "quark_backup": "netdisk",
    "quark_download": "netdisk",
    # 备份与恢复已收起为全局设置里的面板，旧地址指向全局设置
    "backup_center": "global_settings",
}


def _resolve_page_key(key):
    """兼容旧配置/外部链接：test_page → douyin_danmaku。"""
    return PAGE_ALIASES.get(key, key)


def _page_class(key):
    """按注册表懒导入页面类。"""
    import importlib
    mod_name, cls_name = PAGE_CLASSES[key]
    return getattr(importlib.import_module(mod_name), cls_name)

NAV_GROUPS = [
    ("内容采集", [
        ("info_accounts", "信息获取"),
        ("info_results", "已获取"),
        ("info_tools", "信息工具"),
    ]),
    ("资产管理", [
        ("asset", "资产管理"),
        ("netdisk", "网盘"),
    ]),
    ("录播工具箱", [
        ("toolbox_organize", "自动整理"),
        ("toolbox_editor", "视频操作"),
        ("toolbox_rename", "交互重命名 / 投稿"),
        ("toolbox_audio", "音频提取"),
        ("toolbox_subtitle", "字幕转换"),
        ("toolbox_launcher", "继续操作"),
        ("toolbox_calendar", "日历浏览"),
        ("toolbox_migrate", "文件迁移"),
    ]),
    ("账户追踪", [
        ("music_tracker", "音乐账号追踪"),
        ("tracker", "网媒账号追踪"),
        ("douyin_index", "抖音指数"),
        ("tcdoc", "腾讯文档监控"),
        ("weibo_chaohua", "微博超话"),
        ("fan_groups", "粉丝群查询"),
    ]),
    ("票务购物", [
        ("tickets", "票务解析"),
        ("shopping", "购物解析"),
        ("douyin_shop", "抖音商品"),
        ("bili_shop", "B站小店"),
    ]),
    ("音乐工具", [
        ("music_search_download", "音乐搜索下载"),
        ("music_file_tools", "音乐标签"),
    ]),
    ("直播录制", [
        # 直播录制与弹幕姬都内嵌在这一页（页签切换），侧栏只占一个入口
        ("live_record", "直播录制"),
    ]),
    ("本地工具", [
        ("localwiki", "本地资料站"),
        ("livestream_summary", "直播总结"),
        ("image_crop", "图片裁切"),
        ("cover_maker", "套版封面制作"),
        ("certificate_maker", "证书生成器"),
        ("image_sorter", "媒体筛选分类"),
        ("rename_batch", "批量重命名"),
    ]),
    ("全局", [
        ("tasks", "定时任务"),
        ("global_settings", "全局设置"),
        ("performance", "性能诊断"),
    ]),
]

# Cookie 管理平台列表取自 gui_qt.cookie_manager.COOKIE_PLATFORMS（单一事实来源），
# 这里不再复制一份：以前两处各写一遍，新增平台时计数口径会悄悄对不上。

# 回车键分发时，这些控件的回车留给控件自身处理（对应原版 event.widget 判断）
_TEXT_INPUT_TYPES = None  # 延迟初始化（QApplication 未建时不能 import 全部控件类型）

_EXTRA_TOOLS = {
    "wechat_article": "公众号解析",
    "bili_resource": "B站资源", "video_comments": "视频评论区",
}

_SHELL_QSS = """
QWidget#workbenchShell { border: 1px solid palette(shadow); }
QWidget#workbenchShell[windowMaximized="true"] { border: none; }
QWidget#workbenchSidebar { background: palette(base); border-right: 1px solid palette(shadow); }
QWidget#workbenchNavItems, QWidget#workbenchNavViewport, QScrollArea#workbenchNavScroll,
QWidget#workbenchNavGroups, QWidget#workbenchSearchResults { background: transparent; border: none; }
QPushButton#workbenchBrand { background: transparent; color: palette(text); border: none; text-align: left; font-size: 16px; font-weight: bold; padding: 0; }
QLabel#workbenchBrandMark { background: transparent; border: none; }
QLabel#workbenchBrandSub { background: transparent; color: palette(placeholder-text); border: none; font-size: 10px; }
QLineEdit#workbenchSearch { background: palette(window); border: 1px solid palette(shadow); border-radius: 8px; padding: 8px 10px; font-size: 12px; }
QLineEdit#workbenchSearch:focus { border-color: palette(highlight); }
QLabel#workbenchGroup { background: transparent; color: palette(placeholder-text); border: none; font-size: 10px; font-weight: bold; padding: 15px 10px 5px; }
QPushButton#workbenchNavButton { background: transparent; color: palette(text); border: 1px solid transparent; border-radius: 8px; text-align: left; padding: 8px 11px; font-size: 12px; }
QPushButton#workbenchNavButton:hover { background: palette(alternate-base); color: palette(link); }
QPushButton#workbenchNavButton:checked { background: palette(midlight); color: palette(link); border-color: palette(midlight); font-weight: bold; }
QPushButton#workbenchNavButton:focus { border-color: palette(highlight); }
QFrame#workbenchTopbar { background: palette(base); border: none; border-bottom: 1px solid palette(shadow); }
QLabel#workbenchContext { color: palette(placeholder-text); background: transparent; font-size: 11px; }
QLabel#workbenchPageTitle { color: palette(text); background: transparent; font-size: 14px; font-weight: bold; }
QPushButton#workbenchDirectory { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0; }
QPushButton#workbenchDirectory:hover { background: palette(alternate-base); border-color: palette(shadow); }
QFrame#workbenchTopbarDivider { background: palette(shadow); border: none; }
QPushButton#windowControl { background: transparent; border: none; border-radius: 8px; padding: 0; }
QPushButton#windowControl:hover { background: palette(alternate-base); }
QPushButton#windowControl:pressed { background: palette(midlight); }
QPushButton#windowControlClose { background: transparent; border: none; border-radius: 8px; padding: 0; }
QPushButton#windowControlClose:hover { background: #e81123; }
QPushButton#windowControlClose:pressed { background: #c42b1c; }
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

        # 无边框外壳：系统标题栏隐藏，最小化/最大化/关闭收进顶部栏右侧
        # （顶部栏同时是拖动区，四边/四角热区负责缩放，见 window_chrome）
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)

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
        self._closed = False
        self._quitting = False
        self._tray = None
        self._tray_menu = None
        self._tray_hint_shown = False
        self.performance_monitor = None  # 用户开启后才创建，服务归主程序持有。
        self.lifecycle_policy = PagePolicy.CORE
        self.task_manager = common.TASKS
        self.page_lifecycle = PageLifecycleManager(self)
        self.page_preloader = PagePreloader(
            self, [key for key in PAGE_CLASSES
                   if key != "toolbox_convert" and key not in _EXTRA_TOOLS])
        self.nav_buttons = {}
        self._search_buttons = {}
        self._tool_names = {"home": ("工作台", "总览")}
        for group_title, items in NAV_GROUPS:
            for key, title in items:
                self._tool_names[key] = (title, group_title)
        self._tool_names.update({key: (title, "信息工具") for key, title in _EXTRA_TOOLS.items()})
        self.current_page = None

        central = QWidget()
        central.setObjectName("workbenchShell")
        central.setAttribute(Qt.WA_StyledBackground, True)
        central.setStyleSheet(_SHELL_QSS)
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 无边框后自己补回窗口边缘缩放：透明热区交给系统做原生 resize
        self._resize_handles = install_resize_handles(self)

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
        mark = QLabel()
        mark.setObjectName("workbenchBrandMark")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        brand_pixmap = _brand_icon_pixmap()
        if brand_pixmap is not None:
            mark.setPixmap(brand_pixmap)
        else:
            mark.setText("F")
        brand_row.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        self.nav_title = QPushButton("封茗囧菌数据汇总库")
        self.nav_title.setObjectName("workbenchBrand")
        # 只作品牌标题，不再当「返回工作台」的入口（避免误触跳页）
        self.nav_title.setFocusPolicy(Qt.NoFocus)
        self.nav_title.setCursor(Qt.ArrowCursor)
        self.nav_title.setToolTip("封茗囧菌数据汇总库")
        brand_text.addWidget(self.nav_title)
        brand_sub = QLabel(f"统一维护工具箱 - v{APP_VERSION}")
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
        self.preload_status = QLabel("页面预载：准备中")
        self.statusBar().addPermanentWidget(self.preload_status)

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
        self._build_tray()

    def _build_tray(self):
        """系统托盘：关闭窗口时隐藏到这里，后台任务与录制继续运行。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        from version import APP_DISPLAY_NAME, APP_VERSION

        icon = QApplication.windowIcon()
        if icon.isNull():
            root_icon = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "icon.ico")
            if os.path.exists(root_icon):
                icon = QIcon(root_icon)
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(f"{APP_DISPLAY_NAME} v{APP_VERSION} · 后台运行中")
        menu = QMenu()
        show_action = menu.addAction("显示主界面")
        show_action.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.quit_from_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        # 关闭后台模式时连托盘图标一起收起，不留常驻图标。
        if bool(load_convert_config().get("close_to_tray", True)):
            tray.show()
        self._tray = tray
        self._tray_menu = menu

    def set_background_enabled(self, enabled):
        """设置页切换后台模式时同步托盘图标。"""
        if self._tray is None:
            return
        if enabled:
            self._tray.show()
        else:
            self._tray.hide()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self, *_args):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self, *_args):
        """托盘退出：先停止录制并打包，再走与关闭窗口相同的清理流程。"""
        lrw = getattr(self, "live_record_win", None)
        if lrw is not None:
            try:
                act = lrw.activity()
            except Exception:
                act = None
            if act and (act["recording"] or act["monitoring"]):
                if not lrw.stop_and_package(self):
                    return
        self._quitting = True
        self.close()

    def _notify_background(self):
        if self._tray is None or self._tray_hint_shown:
            return
        self._tray.showMessage(
            "已隐藏到后台",
            "程序仍在后台运行，任务和录制不会中断。\n"
            "双击托盘图标恢复窗口，右键托盘图标可退出。",
            QSystemTrayIcon.Information, 5000)
        self._tray_hint_shown = True

    def _deferred_boot(self, first_key):
        """事件循环就绪后的启动收尾：构造首个真实页面 + 重依赖初始化。"""
        if self._closed:
            return
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
        self.page_preloader.start()

    # ==================== 侧栏 ====================

    def _build_topbar(self):
        bar = TitleBar()
        bar.setObjectName("workbenchTopbar")
        bar.setFixedHeight(54)
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 0, 8, 0)
        row.setSpacing(4)
        self.context_label = QLabel("总览  / ")
        self.context_label.setObjectName("workbenchContext")
        row.addWidget(self.context_label)
        self.page_title_label = QLabel("工作台")
        self.page_title_label.setObjectName("workbenchPageTitle")
        row.addWidget(self.page_title_label)
        row.addStretch(1)
        # 工作目录只留图标入口（路径进 tooltip），原来「工作目录 xxx」那行文字
        # 让位给窗口控制按钮；点击仍直达全局设置里的目录管理。
        self.directory_btn = QPushButton()
        self.directory_btn.setObjectName("workbenchDirectory")
        self.directory_btn.setCursor(Qt.PointingHandCursor)
        self.directory_btn.setFixedSize(32, 30)
        self.directory_btn.setIconSize(QSize(16, 16))
        self.directory_btn.clicked.connect(lambda: self.show_page("global_settings"))
        row.addWidget(self.directory_btn)
        divider = QFrame()
        divider.setObjectName("workbenchTopbarDivider")
        divider.setFixedSize(1, 18)
        row.addWidget(divider)
        self.window_controls = WindowControlBar(bar)
        self.window_controls.minimize_requested.connect(self.showMinimized)
        self.window_controls.maximize_requested.connect(self.toggle_max_restore)
        self.window_controls.close_requested.connect(self.close)
        row.addWidget(self.window_controls)
        self._update_directory_button()
        return bar

    def _update_directory_button(self):
        self.directory_btn.setToolTip(
            f"当前工作目录：{self.work_dir}\n点击在全局设置中管理")

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
            is_bottom = group_title == "全局"
            if not is_bottom:
                gl = QLabel(group_title)
                gl.setObjectName("workbenchGroup")
                nav_layout.addWidget(gl)
            for key, item_title in items:
                btn = self._make_nav_button(key, item_title)
                if is_bottom:
                    btn.setObjectName("workbenchFooter")
                    btn.setText({"tasks": "任务", "global_settings": "设置", "performance": "性能"}[key])
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
        self.window_controls.refresh_icons()
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
        label = "已跟随系统" if src == "system" else "已定时"
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

        mgr_btn = QPushButton("管理账号与登录")
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
        # 账号配置在同一对话框里维护：关闭后刷新已实例化的账号页
        for key in ("info_accounts", "tracker", "music_tracker"):
            refresh = getattr(self.pages.get(key), "refresh_accounts", None)
            if not callable(refresh):
                continue
            try:
                refresh()
            except Exception:
                pass

    # ==================== 页面切换 ====================

    def show_page(self, key):
        if self._closed:
            return
        key = _resolve_page_key(key)
        self.page_preloader.defer_for_navigation()
        monitor = self.performance_monitor
        try:
            token = monitor.page_started() if monitor is not None and key in PAGE_CLASSES else None
        except Exception:
            token = None
        failed = False
        try:
            self._show_page(key)
        except Exception:
            failed = True
            raise
        finally:
            if monitor is not None:
                try:
                    monitor.page_finished(token, key, failed)
                except Exception:
                    pass

    def _show_page(self, key):
        key = _resolve_page_key(key)
        if key == "toolbox_convert":
            self.show_page("toolbox_editor")
            self.pages["toolbox_editor"].open_convert_tab()
            return
        if key not in PAGE_CLASSES:
            return
        # 原独立信息页的所有入口都落到同一个容器实例，避免搜索/首页/
        # 其他页面跳转各自创建一份采集页，造成重复任务或状态不一致。
        if key in _EXTRA_TOOLS:
            self.show_page("info_tools")
            self.pages["info_tools"].open_tab(key)
            self._update_page_context()
            return
        # 高亮当前按钮（首页不在导航列表里，单独标记侧栏「工作台」按钮）
        self.home_btn.setChecked(key == "home")
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)
        for k, btn in self._search_buttons.items():
            btn.setChecked(k == key)

        page = self._ensure_page(key)
        if self.current_page == key:
            return

        old_key = self.current_page
        old_page = self.pages.get(old_key)
        self.stack.setCurrentWidget(page)
        self.current_page = key
        page._ever_shown = True
        if old_page is not None:
            self.page_lifecycle.leave(old_key, old_page)
        self._update_page_context()
        if key not in ("tasks", "global_settings", "performance") and not self.nav_search.text().strip():
            button = self.home_btn if key == "home" else self.nav_buttons.get(key)
            if button is not None:
                self.nav_scroll.ensureWidgetVisible(button, 0, 24)
        theme.adapt(page)
        if key in self._theme_dirty:
            self._theme_dirty.discard(key)
            try:
                if hasattr(page, "on_theme_changed"):
                    page.on_theme_changed()
            except Exception:
                pass
        page.on_show()

    def _ensure_page(self, key):
        """导航与预载共用一个创建入口，不切页、不触发 on_show。"""
        key = _resolve_page_key(key)
        if key not in self.pages:
            page = self.page_lifecycle.take(key)
            if page is None:
                page = _page_class(key)(self)
                restore_page_state(page, self.page_lifecycle.states.get(key))
                self.page_lifecycle.states.pop(key, None)
            self.stack.addWidget(page)
            self.pages[key] = page
            if key == "info_tools" and hasattr(page, "tabs"):
                page.tabs.currentChanged.connect(self._update_page_context)
            # 功能型复选框：点击时右下角 toast「已开启/已关闭 XXX」
            common.bind_checkbox_toasts(page)
            # 构造函数可能启动界面计时器，隐藏预载不能持续渲染或播放。
            page.on_hide()
        return self.pages[key]

    def closeEvent(self, event):
        cfg = load_convert_config()
        lrw = getattr(self, "live_record_win", None)
        act = self._live_activity()
        has_activity = bool(act and (act["recording"] or act["monitoring"]))
        background = (not self._quitting and self._tray is not None
                      and bool(cfg.get("close_to_tray", True)))
        if background:
            if has_activity:
                choice = self._ask_close_with_live(act)
                if choice == "cancel":
                    event.ignore()
                    return
                if choice == "quit":
                    if lrw is not None and not lrw.stop_and_package(self):
                        event.ignore()
                        return
                    self._quitting = True
                else:
                    event.ignore()
                    self.hide()
                    self._notify_background()
                    return
            else:
                event.ignore()
                self.hide()
                self._notify_background()
                return
        elif has_activity and not self._quitting:
            # 没有后台模式：关闭即退出，先确认再停止录制并打包
            box = QMessageBox(self)
            box.setWindowTitle("确认退出")
            box.setIcon(QMessageBox.Warning)
            if act["recording"]:
                names = "、".join(item["name"] for item in act["recording"][:5])
                box.setText(f"正在录制 {len(act['recording'])} 场：{names}")
                box.setInformativeText(
                    "直接退出可能损坏正在写入的文件。\n"
                    "点「停止并打包后退出」会逐个停止录制、等文件写完，"
                    "再把分段合并、封装成 mp4 后退出。")
                ok = box.addButton("停止并打包后退出", QMessageBox.AcceptRole)
            else:
                box.setText("监控正在运行")
                box.setInformativeText("退出会停止开播检测；当前没有正在写入的录制文件。")
                ok = box.addButton("停止监控并退出", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(ok)
            box.exec()
            if box.clickedButton() is not ok:
                event.ignore()
                return
            if lrw is not None:
                lrw.stop_and_package(self)
        self._quitting = True
        self._closed = True
        self.page_preloader.stop()
        if self.performance_monitor is not None:
            self.performance_monitor.stop()
        self.page_lifecycle.shutdown()
        # 直播录制二级窗口（独立窗口，随主程序一起收尾）
        if lrw is not None:
            try:
                lrw.shutdown()
                lrw.close()
            except Exception:
                pass
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def _live_activity(self):
        """直播录制的录制 / 监控状态；窗口没打开时返回 None。"""
        lrw = getattr(self, "live_record_win", None)
        if lrw is None:
            return None
        try:
            return lrw.activity()
        except Exception:
            return None

    def _ask_close_with_live(self, act) -> str:
        """后台模式下带录制 / 监控关闭主窗口：返回 hide / quit / cancel。"""
        box = QMessageBox(self)
        box.setWindowTitle("正在录制")
        box.setIcon(QMessageBox.Warning)
        if act["recording"]:
            names = "、".join(item["name"] for item in act["recording"][:5])
            more = " 等" if len(act["recording"]) > 5 else ""
            box.setText(f"正在录制 {len(act['recording'])} 场：{names}{more}")
            box.setInformativeText(
                "关闭主窗口会隐藏到系统托盘，录制继续，不会损坏文件。\n"
                "要退出软件请选「停止录制、打包并退出」。")
            hide_btn = box.addButton("隐藏到托盘继续录制", QMessageBox.AcceptRole)
        else:
            names = "、".join(item["name"] for item in act["monitoring"][:6])
            box.setText(f"监控正在运行：{names}")
            box.setInformativeText("关闭主窗口会隐藏到系统托盘，监控继续。")
            hide_btn = box.addButton("隐藏到托盘继续监控", QMessageBox.AcceptRole)
        quit_btn = box.addButton("停止录制、打包并退出", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(hide_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is hide_btn:
            return "hide"
        if clicked is quit_btn:
            return "quit"
        return "cancel"

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

        # 抖音弹幕姬：拖入 zip 回放包 → 进入回放模式
        if self.current_page == "douyin_danmaku":
            p = self.pages.get("douyin_danmaku")
            zips = [f for f in file_list if f.lower().endswith(".zip")]
            if p is not None and zips:
                p.load_replay_zip(zips[0])
                return

        # 其他页：拖入文件夹 → 设为工作目录（锁定时拒绝改变，直到解锁）
        target = dirs[0] if dirs else os.path.dirname(file_list[0])
        if load_convert_config().get("work_dir_locked"):
            self.toast("默认工作文件夹已锁定，拖入文件夹不改变（可在全局设置解锁）", "info")
            return
        self.work_dir = target
        self._refresh_all()
        self.toast(f"已切换: {target}", "success")

    # ==================== 公共方法 ====================

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self.sidebar.setFixedWidth(200 if self.width() < 1040 else 228)
        handles = getattr(self, "_resize_handles", None)
        if handles is not None:
            handles.reposition()
            handles.set_enabled(not (self.isMaximized() or self.isFullScreen()))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_window_shell()

    def showEvent(self, event):
        super().showEvent(event)
        # 窗口四周圆角交给 DWM（Win11）：整窗（含顶栏/侧栏/页面）一起裁，
        # 最大化时系统自动恢复直角
        if not getattr(self, "_corners_ready", False):
            self._corners_ready = apply_round_corners(self)
        # 状态栏等后创建的兄弟控件会盖住窗口边缘热区，显示后统一压到最上层
        handles = getattr(self, "_resize_handles", None)
        if handles is not None:
            handles.raise_all()

    def toggle_max_restore(self):
        """最大化 / 还原（顶部栏标题按钮与双击顶栏共用）。"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_window_shell()

    def _sync_window_shell(self):
        """同步外壳：最大化图标、缩放热区、窗口边框。"""
        maximized = self.isMaximized() or self.isFullScreen()
        controls = getattr(self, "window_controls", None)
        if controls is not None:
            controls.set_maximized(maximized)
        handles = getattr(self, "_resize_handles", None)
        if handles is not None:
            handles.set_enabled(not maximized)
        shell = self.centralWidget()
        if shell is not None and shell.property("windowMaximized") != maximized:
            shell.setProperty("windowMaximized", maximized)
            shell.style().unpolish(shell)
            shell.style().polish(shell)

    def _refresh_all(self):
        self._update_directory_button()
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
