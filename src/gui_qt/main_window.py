"""PySide6 主窗口：左侧导航 + 右侧 QStackedWidget 懒加载。

与原版（customtkinter）对应，22 个页面按 key 注册。已迁移的页面用真实类，
未迁移的用占位页（PlaceholderPage），逐步替换即可。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config

from gui_qt.pages.placeholder import PlaceholderPage
from gui_qt.pages.settings import GlobalSettingsPage
from gui_qt.pages.info_parse import InfoParsePage
from gui_qt.pages.info_batch import InfoBatchPage
from gui_qt.pages.info_accounts import InfoAccountsPage
from gui_qt.pages.info_results import InfoResultsPage
from gui_qt.pages.download import VideoDownloadPage
from gui_qt.pages.subtitle import SubtitlePage
from gui_qt.pages.organize import OrganizePage
from gui_qt.pages.convert import ConvertPage
from gui_qt.pages.rename import RenamePage
from gui_qt.pages.audio import AudioPage
from gui_qt.pages.lyrics_tool import LyricsToolPage
from gui_qt.pages.launcher import LauncherPage
from gui_qt.pages.music_unlock import MusicUnlockPage
from gui_qt.pages.calendar import CalendarPage
from gui_qt.pages.migrate import MigratePage
from gui_qt.pages.music_info import MusicInfoPage
from gui_qt.pages.netease import NeteasePage

# 页面注册：key -> (标题, 页面类或 None=占位)
PAGE_CLASSES = {
    "info_parse": InfoParsePage,
    "info_batch": InfoBatchPage,
    "info_accounts": InfoAccountsPage,
    "info_results": InfoResultsPage,
    "download": VideoDownloadPage,
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
        ("dycast", "📺 抖音弹幕姬"),
    ]),
    ("⚙️ 全局", [
        ("test_page", "🧪 测试"),
        ("global_settings", "⚙️ 全局设置"),
    ]),
]

DARK_QSS = """
QMainWindow, QWidget { background-color: #1a1d27; color: #c6cbd6; font-family: "Microsoft YaHei"; font-size: 13px; }
QScrollArea { border: none; background: #15171d; }
QPushButton { background: transparent; color: #c6cbd6; padding: 7px 12px; border: none; border-radius: 4px; text-align: left; }
QPushButton:hover { background: #23262f; }
QPushButton:checked { background: #2a2d3a; color: #ffffff; }
QLineEdit { background: #23262f; color: #c6cbd6; border: 1px solid #333; border-radius: 4px; padding: 6px; }
QStatusBar { background: #15171d; color: #888; }
QLabel { color: #c6cbd6; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        from version import APP_VERSION, APP_DISPLAY_NAME
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION} · 制作歆慕 (PySide6)")
        self.resize(1600, 1000)
        self.setMinimumSize(1280, 800)

        # 全局工作目录
        cfg = load_convert_config()
        saved = cfg.get("work_dir", "")
        self.work_dir = saved if saved and os.path.isdir(saved) else os.getcwd()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 左侧导航
        nav_scroll = QScrollArea()
        nav_scroll.setFixedWidth(210)
        nav_scroll.setWidgetResizable(True)
        nav_widget = QWidget()
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(8, 8, 8, 8)
        nav_layout.setSpacing(3)
        nav_scroll.setWidget(nav_widget)
        layout.addWidget(nav_scroll)

        # 右侧内容（懒加载）
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.nav_buttons = {}
        self.pages = {}
        self._build_nav(nav_layout)

        self.statusBar().showMessage("就绪")

    def _build_nav(self, nav_layout):
        title = QLabel("📊 数据汇总库 · 工具箱")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 6px 4px 10px 4px;")
        nav_layout.addWidget(title)

        for group_title, items in NAV_GROUPS:
            gl = QLabel(group_title)
            gl.setStyleSheet("color: #5a6072; font-size: 11px; padding: 10px 4px 2px 4px;")
            nav_layout.addWidget(gl)
            for key, item_title in items:
                btn = QPushButton(item_title)
                btn.setCheckable(True)
                btn.clicked.connect(lambda checked, k=key: self.show_page(k))
                nav_layout.addWidget(btn)
                self.nav_buttons[key] = btn
        nav_layout.addStretch(1)

    def show_page(self, key):
        # 高亮当前按钮
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)

        # 懒加载
        if key not in self.pages:
            page_cls = PAGE_CLASSES.get(key)
            if page_cls is not None:
                page = page_cls(self)
            else:
                title = self._find_title(key)
                page = PlaceholderPage(self, title)
            self.stack.addWidget(page)
            self.pages[key] = page

        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()

    def _find_title(self, key):
        for _g, items in NAV_GROUPS:
            for k, t in items:
                if k == key:
                    return t
        return key

    def toast(self, msg, _type="info"):
        self.statusBar().showMessage(msg, 3000)
