"""B站资源 · 聚合页：表情包 / 收藏集 / 空间综合搜索（原三个独立页合并为一个）。

三个子页保持原结构（各自的标题+功能介绍浮层+内部 Tab），本页只做一级 Tab 容器，
导航入口位于「📊 信息获取」组。
"""

from PySide6.QtWidgets import QTabWidget, QVBoxLayout

from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import BiliEmojiPage
from gui_qt.pages.bili_garb import BiliGarbPage
from gui_qt.pages.bili_info import BiliInfoPage


class BiliResourcePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.emoji_page = BiliEmojiPage(app)
        self.garb_page = BiliGarbPage(app)
        self.info_page = BiliInfoPage(app)
        self.tabs.addTab(self.emoji_page, "😀 表情包")
        self.tabs.addTab(self.garb_page, "🎁 收藏集")
        self.tabs.addTab(self.info_page, "🔍 空间综合搜索")

    def show_garb(self, act_id: str):
        """表情包子页命中收藏集 ID 时切换到收藏集子页并加载活动。"""
        self.tabs.setCurrentWidget(self.garb_page)
        self.garb_page.open_act(act_id)
