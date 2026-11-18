"""B站资源 · 聚合页：5 个功能 tab 平铺（表情包 / 直播表情 / 装扮收藏集 / 收藏集 / 空间）。

三个子页实例只作为逻辑宿主（控件属性与网络回调挂在其上），内层 tab
被平铺提升到本页顶部一级；搜索、按 ID 详情、装扮表情包等重合功能已在
各子页内合并。子页自身的头部与内层 QTabWidget 不再展示。
导航入口位于「信息获取」组。
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout

from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import BiliEmojiPage
from gui_qt.pages.bili_garb import BiliGarbPage
from gui_qt.pages.bili_info import BiliInfoPage


# 平铺顺序与命名：(子页键, 内层 tab 下标, 顶部标签)
_FLAT = [
    ("emoji", 0, "表情包"),
    ("emoji", 1, "直播表情"),
    ("garb", 0, "装扮 / 收藏集"),
    ("garb", 1, "收藏集活动"),
    ("info", 0, "空间"),
]


class BiliResourcePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # 子页只作为逻辑宿主；先取出各自内层 tab 控件（按本地顺序）
        self.emoji_page = BiliEmojiPage(app)
        self.garb_page = BiliGarbPage(app)
        self.info_page = BiliInfoPage(app)
        subs = {"emoji": self.emoji_page, "garb": self.garb_page, "info": self.info_page}
        for sub in subs.values():
            # 子页需要切回本页的「收藏集」tab，嵌入 tab 容器后 app.pages 里没有本页，
            # 因此保留一个宿主引用供子页回跳。
            sub._resource_host = self
        widgets = {}
        for key, sub in subs.items():
            ws = []
            while sub.tabs.count():
                w = sub.tabs.widget(0)
                sub.tabs.removeTab(0)
                ws.append(w)
            widgets[key] = ws

        # 按 _FLAT 顺序平铺提升到本页顶部（两个子页的 tab 交错），并记录映射表
        tab_map = {"emoji": [], "garb": [], "info": []}
        for key, local, label in _FLAT:
            flat_idx = self.tabs.addTab(widgets[key][local], label)
            tab_map[key].append(flat_idx)
        for key, sub in subs.items():
            sub._flat_tabs = self.tabs
            sub._tab_map = tab_map[key]

    def show_garb(self, act_id: str):
        """表情包 tab 命中收藏集 ID 时切到「收藏集」tab 并加载活动。"""
        self.garb_page.open_act(act_id)
