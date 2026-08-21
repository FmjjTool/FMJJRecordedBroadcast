"""PySide6 主窗口：左侧导航 + 右侧 QStackedWidget 懒加载。

与原版（customtkinter）一一对应：
- 侧栏底部 Cookie 状态区（总开关 + 已登录计数 + 管理入口）
- 全局拖放（字幕/歌词页接收文件，其他页切换工作目录）
- 工作目录持久化 + on_dir_changed 广播
- 回车键统一分发到当前页 on_return_press（输入框聚焦时不拦截）
"""

import os

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt import common, theme
from gui_qt.toolbox_common import has_date_subfolders
from gui_qt.pages.settings import GlobalSettingsPage
from gui_qt.pages.info_parse import InfoParsePage
from gui_qt.pages.info_batch import InfoBatchPage
from gui_qt.pages.info_accounts import InfoAccountsPage
from gui_qt.pages.info_results import InfoResultsPage
from gui_qt.pages.download import VideoDownloadPage
from gui_qt.pages.asset import AssetPage
from gui_qt.pages.subtitle import SubtitlePage
from gui_qt.pages.organize import OrganizePage
from gui_qt.pages.convert import ConvertPage
from gui_qt.pages.rename import RenamePage
from gui_qt.pages.audio import AudioPage
from gui_qt.pages.lyrics_tool import LyricsToolPage
from gui_qt.pages.launcher import LauncherPage
from gui_qt.pages.music_unlock import MusicUnlockPage
from gui_qt.pages.music_tag import MusicTagPage
from gui_qt.pages.calendar import CalendarPage
from gui_qt.pages.migrate import MigratePage
from gui_qt.pages.music_info import MusicInfoPage
from gui_qt.pages.netease import NeteasePage
from gui_qt.pages.tracker import TrackerPage
from gui_qt.pages.dycast import DycastPage
from gui_qt.pages.test_page import TestPage
from gui_qt.pages.bili_emoji import BiliEmojiPage
from gui_qt.pages.bili_garb import BiliGarbPage
from gui_qt.pages.bili_info import BiliInfoPage
from gui_qt.pages.file_time import FileTimePage
from gui_qt.pages.rename_batch import RenameBatchPage
from gui_qt.toast import show_toast

# 页面注册：key -> 页面类
PAGE_CLASSES = {
    "info_parse": InfoParsePage,
    "info_batch": InfoBatchPage,
    "info_accounts": InfoAccountsPage,
    "info_results": InfoResultsPage,
    "download": VideoDownloadPage,
    "asset": AssetPage,
    "toolbox_organize": OrganizePage,
    "toolbox_convert": ConvertPage,
    "toolbox_rename": RenamePage,
    "toolbox_audio": AudioPage,
    "toolbox_subtitle": SubtitlePage,
    "lyrics_tool": LyricsToolPage,
    "toolbox_launcher": LauncherPage,
    "toolbox_calendar": CalendarPage,
    "toolbox_migrate": MigratePage,
    "music_info": MusicInfoPage,
    "netease": NeteasePage,
    "unlock_music": MusicUnlockPage,
    "music_tag": MusicTagPage,
    "tracker": TrackerPage,
    "dycast": DycastPage,
    "test_page": TestPage,
    "bili_emoji": BiliEmojiPage,
    "bili_garb": BiliGarbPage,
    "bili_info": BiliInfoPage,
    "file_time": FileTimePage,
    "rename_batch": RenameBatchPage,
    "global_settings": GlobalSettingsPage,
}

NAV_GROUPS = [
    ("📊 信息获取", [
        ("info_parse", "🔍 单个解析"),
        ("info_batch", "📋 批量导入"),
        ("info_accounts", "👥 账号获取"),
        ("info_results", "📦 已获取"),
        ("download", "📥 视频下载"),
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
    ("🎤 歌词工具", [
        ("lyrics_tool", "🎤 歌词转换"),
    ]),
    ("🎵 音乐服务", [
        ("music_info", "🎵 音乐信息获取"),
        ("netease", "🎧 音乐下载"),
        ("unlock_music", "🔓 音乐解密"),
    ]),
    ("📈 直播·追踪", [
        ("tracker", "📈 账号追踪"),
        ("test_page", "📺 弹幕姬"),
    ]),
    ("🎁 B站资源", [
        ("bili_emoji", "😀 表情包"),
        ("bili_garb", "👕 装扮/收藏集"),
        ("bili_info", "🎬 视频/空间/动态"),
    ]),
    ("🧰 实用工具", [
        ("rename_batch", "✏️ 批量重命名"),
        ("file_time", "🕓 文件时间修改"),
        ("music_tag", "🏷 音乐标签"),
    ]),
    ("⚙️ 全局", [
        ("global_settings", "⚙️ 全局设置"),
    ]),
]

# Cookie 管理平台列表：(key, 中文名, 是否支持扫码登录, 是否支持浏览器登录)
COOKIE_PLATFORMS = [
    ("bilibili", "B站", True, True),
    ("douyin", "抖音", False, True),
    ("weibo", "微博", False, True),
    ("netease", "网易云", True, False),
    ("qq", "QQ音乐", False, False),
    ("kugou", "酷狗", False, False),
    ("kuwo", "酷我", False, False),
    ("5sing", "5sing", False, False),
    ("xiaohongshu", "小红书", False, False),
    ("kuaishou", "快手", False, False),
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

        # 底部固定区：标题 + 全局设置入口 + Cookie 状态 + 主题切换
        self.bottom_layout = QVBoxLayout()
        self.bottom_layout.setContentsMargins(8, 0, 8, 8)
        self.bottom_layout.setSpacing(3)
        nav_v.addLayout(self.bottom_layout)
        layout.addWidget(nav_panel)

        # 右侧内容（懒加载）
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self._build_nav(self.nav_layout)

        self.statusBar().showMessage("就绪")

        # 全局拖放
        self.setAcceptDrops(True)

        # 回车键统一分发（安装到 QApplication，所有子控件共享）
        app = QApplication.instance()
        app.installEventFilter(self)

        # 默认显示第一个页面
        first_key = NAV_GROUPS[0][1][0][0]
        self.show_page(first_key)

        # 延迟刷新 Cookie 状态（内部 import auth 等重依赖，避免阻塞启动）
        self._refresh_cookie_status()

        # 恢复上次「已获取」的数据（config/collected.json，重启不丢）
        common.load_collected()

        # 保存了深色主题时，把构造期内联样式统一适配（含 styleSheet= 关键字参数）
        if theme.is_dark():
            theme.adapt(self)

        # 主题定时开关：启动对齐一次 + 每 10 秒强制对齐（到点即变，无需手动触发）
        self._check_theme_schedule()
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._check_theme_schedule)
        self._theme_timer.start(10_000)

    # ==================== 侧栏 ====================

    def _build_nav(self, nav_layout):
        title = QLabel("📊 数据汇总库 · 工具箱")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 6px 4px 10px 4px;")
        nav_layout.addWidget(title)

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
            target = self.bottom_layout if is_bottom else nav_layout
            for key, item_title in items:
                btn = QPushButton(item_title)
                btn.setCheckable(True)
                btn.setStyleSheet("text-align: left; padding: 5px 8px;")
                btn.clicked.connect(lambda checked, k=key: self.show_page(k))
                target.addWidget(btn)
                self.nav_buttons[key] = btn
        nav_layout.addStretch(1)

        # 底部固定区：Cookie 状态 + 主题切换（全局设置入口已随「全局」组固定）
        self._build_cookie_bar(self.bottom_layout)

        # 主题切换（浅色/深色，持久化）— 底部固定区
        self.theme_btn = QPushButton("🌙 深色主题")
        self.theme_btn.setToolTip("切换 浅色 / 深色 界面（自动保存）")
        self.theme_btn.clicked.connect(self._toggle_theme)
        self.bottom_layout.addWidget(self.theme_btn)
        self._update_theme_btn()

    def _toggle_theme(self):
        name = theme.toggle_theme()
        theme.apply_theme()
        theme.retheme_all()
        self._update_theme_btn()
        show_toast(self, f"已切换到{'深色' if name == 'dark' else '浅色'}主题", "success")

    def _check_theme_schedule(self):
        """定时开关：启用时把主题对齐到当前时段目标（到点即变）。"""
        try:
            name = theme.check_scheduled_switch()
        except Exception:
            return
        if not name:
            return
        theme.apply_theme()
        theme.retheme_all()
        self._update_theme_btn()
        show_toast(self, f"⏰ 已定时切换到{'深色' if name == 'dark' else '浅色'}主题", "info")

    def _update_theme_btn(self):
        # 当前深色 → 按钮显示可切回浅色
        self.theme_btn.setText("☀️ 浅色主题" if theme.is_dark() else "🌙 深色主题")

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
            from auth import load_cookies, is_cookie_enabled, get_netease_cookie
            enabled = is_cookie_enabled()
            cookies = load_cookies()
            netease_ok = bool((get_netease_cookie() or "").strip())
            logged = 0
            total = len(COOKIE_PLATFORMS)
            for plat, _name, _qr, _browser in COOKIE_PLATFORMS:
                if plat == "netease":
                    ok = netease_ok
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
            page = PAGE_CLASSES[key](self)
            self.stack.addWidget(page)
            self.pages[key] = page

        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.current_page = key
        # 主题自适应：构造期 styleSheet= 关键字参数与动态新增的行在此兜底
        theme.adapt(page)
        # 触发页面刷新钩子（如「已获取」页切回时刷新列表）
        if hasattr(page, "on_show"):
            page.on_show()

    def closeEvent(self, event):
        # 关闭窗口：对所有已加载页面触发全停（停抓取/录制/回放/dycast 进程树等）
        for page in self.pages.values():
            fn = getattr(page, "shutdown", None) or getattr(page, "on_hide", None)
            if fn:
                try:
                    fn()
                except Exception:
                    pass
        # 弹幕姬进程树：即使页面未打开过也要兜底终止（父关子关）
        try:
            from gui_qt.pages.dycast import stop_dycast_app
            stop_dycast_app(None)
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

        # 字幕转换页 / 歌词转换页 / 音乐标签页：接受拖放的文件或文件夹（本地列表，不改全局目录）
        if self.current_page in ("toolbox_subtitle", "lyrics_tool", "toolbox_lyrics", "music_tag"):
            p = self.pages.get(self.current_page)
            if p:
                if dirs:
                    p.add_dropped_folder(dirs[0])
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

        # 其他页：拖入文件夹 → 设为工作目录
        target = dirs[0] if dirs else os.path.dirname(file_list[0])
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
        if not os.path.isdir(self.work_dir):
            QMessageBox.critical(self, "路径错误", f"工作目录不存在:\n{self.work_dir}")
            return False
        if not has_date_subfolders(self.work_dir):
            reply = QMessageBox.question(
                self, "未检测到日期文件夹",
                "当前工作目录下没有找到日期格式（YYYYMMDD）的子文件夹。\n\n"
                f"路径: {self.work_dir}\n\n"
                "请确认:\n"
                "1. 工作目录选择是否正确\n"
                "2. 录播文件是否已按日期分文件夹存放\n\n"
                "是否继续？（可能扫描不到文件）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        return True

    def toast(self, msg, kind="info"):
        show_toast(self, msg, kind=kind)
