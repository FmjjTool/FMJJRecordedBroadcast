"""B站资源 · 按表情包、收藏集和空间数据分组。

具体操作保留在各功能页内；这里仅负责聚合入口与收藏集活动跳转。
导航入口位于「信息获取」组。
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
        for page in (self.emoji_page, self.garb_page, self.info_page):
            page._resource_host = self

        self.tabs.addTab(self.emoji_page, "表情包")
        self.tabs.addTab(self.garb_page, "收藏集")
        self.tabs.addTab(self.info_page, "空间数据")

    def show_garb(self, act_id: str):
        """切到「收藏集」并加载活动详情。"""
        self.tabs.setCurrentWidget(self.garb_page)
        self.garb_page.open_act(act_id)

    def on_hide(self):
        self.emoji_page.on_hide()
