"""合并页容器：把多个独立页面收进同一个导航入口，内部用页签切换。

子页仍是原来的 BasePage 实例，业务逻辑不动；容器只负责：
- 页签按需加载（首个 tab 在打开容器时加载，其余首次切到才构造）
- 页签切换时转发 on_show / on_hide
- 窗口级钩子（退出、主题、工作目录、回车、拖放）转发给已加载子页
"""

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from gui_qt.pages.base import BasePage


class TabbedPage(BasePage):
    """带页签的页面容器。

    tabs: [(页签标题, 页面类), ...]
    lazy: True 时除当前页外，其余页面在首次切到该页签时才构造。
    """

    def __init__(self, app, tabs, lazy=False):
        super().__init__(app)
        self._app = app
        self._lazy = lazy
        self._factories = []
        self._hosts = []
        self._pages = []
        self._current_index = -1
        self._current = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        for title, page_cls in tabs:
            host = QWidget()
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            self.tabs.addTab(host, title)
            self._hosts.append(host)
            self._factories.append(page_cls)
            if lazy:
                self._pages.append(None)
            else:
                page = page_cls(app)
                host_layout.addWidget(page)
                self._pages.append(page)

        if not lazy and self._pages:
            self._current_index = 0
            self._current = self._pages[0]
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ==================== 内部工具 ====================

    @staticmethod
    def _call(page, name, *args):
        fn = getattr(page, name, None)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            pass

    def _ensure_page(self, index):
        if index < 0 or index >= len(self._factories):
            return None
        page = self._pages[index]
        if page is None:
            page = self._factories[index](self._app)
            self._pages[index] = page
            self._hosts[index].layout().addWidget(page)
        return page

    def _on_tab_changed(self, index):
        if index == self._current_index:
            return
        if self._current is not None:
            self._call(self._current, "on_hide")
        page = self._ensure_page(index)
        self._current_index = index
        self._current = page
        if page is not None:
            self._call(page, "on_show")

    # ==================== 页面钩子转发 ====================

    def on_show(self):
        page = self._ensure_page(self.tabs.currentIndex())
        self._current_index = self.tabs.currentIndex()
        self._current = page
        self._call(page, "on_show")

    def on_hide(self):
        for page in self._pages:
            if page is not None:
                self._call(page, "on_hide")

    def shutdown(self):
        for page in self._pages:
            if page is not None:
                self._call(page, "shutdown")

    def on_dir_changed(self, work_dir):
        for page in self._pages:
            if page is not None:
                self._call(page, "on_dir_changed", work_dir)

    def on_theme_changed(self):
        for page in self._pages:
            if page is not None:
                self._call(page, "on_theme_changed")

    def on_return_press(self):
        self._call(self._current, "on_return_press")

    # ==================== 拖放 ====================

    def _drop_target(self, name):
        """优先当前页；当前页不支持时退回第一个支持该钩子的已加载子页。"""
        if self._current is not None and hasattr(self._current, name):
            return self._current
        for page in self._pages:
            if page is not None and hasattr(page, name):
                return page
        return None

    def add_dropped_files(self, files):
        target = self._drop_target("add_dropped_files")
        if target is not None:
            self._call(target, "add_dropped_files", files)

    def add_dropped_folder(self, path):
        target = self._drop_target("add_dropped_folder")
        if target is not None:
            self._call(target, "add_dropped_folder", path)


class InfoToolsPage(TabbedPage):
    """信息工具：原侧栏五个独立信息页合并为一个页签容器。"""

    TAB_KEYS = [
        "douyin_index",
        "wechat_article",
        "bili_resource",
        "video_comments",
        "tcdoc",
    ]

    def __init__(self, app):
        from gui_qt.pages.bili_resource import BiliResourcePage
        from gui_qt.pages.douyin_index import DouyinIndexPage
        from gui_qt.pages.tcdoc import TcDocPage
        from gui_qt.pages.video_comments import VideoCommentsPage
        from gui_qt.pages.wechat_article import WechatArticlePage
        super().__init__(app, [
            ("📈 抖音指数", DouyinIndexPage),
            ("📰 公众号解析", WechatArticlePage),
            ("🎁 B站资源", BiliResourcePage),
            ("💬 视频评论区", VideoCommentsPage),
            ("📄 腾讯文档监控", TcDocPage),
        ], lazy=True)
        self._tab_index = {key: i for i, key in enumerate(self.TAB_KEYS)}

    def open_tab(self, key):
        """按页面 key 切到对应页签（首页快捷卡片用）。"""
        index = self._tab_index.get(key)
        if index is not None:
            self.tabs.setCurrentIndex(index)


class MusicSearchDownloadPage(TabbedPage):
    """音乐搜索与下载：五平台信息对比 + 多平台下载。"""

    def __init__(self, app):
        from gui_qt.pages.music_info import MusicInfoPage
        from gui_qt.pages.netease import NeteasePage
        super().__init__(app, [
            ("🎵 音乐信息获取", MusicInfoPage),
            ("🎧 音乐下载", NeteasePage),
        ])
