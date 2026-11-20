# -*- coding: utf-8 -*-
"""弹幕姬窗口：抖音 / B站两个页签，由「直播录制」页工具栏的「弹幕姬」按钮打开。

不做成像以前那样贴右侧的常驻窗口：按需打开、居中显示、可自由拖动；
页面直接复用 DouyinDanmakuPage / BiliDanmakuPage，不另写连接逻辑。
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from gui_qt.pages.bili_danmaku import BiliDanmakuPage
from gui_qt.pages.douyin_danmaku import DouyinDanmakuPage

_PAGES = (
    ("douyin", DouyinDanmakuPage, "抖音弹幕"),
    ("bili", BiliDanmakuPage, "B站弹幕"),
)


class DanmakuTabs(QTabWidget):
    """两个平台弹幕页；切页签时停旧页渲染、启新页。"""

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.pages = {}
        for key, cls, title in _PAGES:
            page = cls(app)
            self.pages[key] = page
            self.addTab(page, title)
        # addTab 期间也会发 currentChanged，连接放在建好之后
        self.currentChanged.connect(self._on_tab_changed)

    def _current_page(self):
        index = self.currentIndex()
        keys = list(self.pages)
        return self.pages[keys[index]] if 0 <= index < len(keys) else None

    def _on_tab_changed(self, _index):
        if not self.isVisible():
            return
        current = self._current_page()
        for page in self.pages.values():
            if page is not current:
                page.on_hide()
        if current is not None:
            current.on_show()

    def on_show(self):
        page = self._current_page()
        if page is not None:
            page.on_show()

    def on_hide(self):
        for page in self.pages.values():
            page.on_hide()


class DanmakuWindow(QMainWindow):
    def __init__(self, app=None):
        super().__init__()
        self.app = app
        self.setWindowTitle("弹幕姬 · 抖音 / B站（实时弹幕 / 回放包 / 转 TXT / 表情包）")
        self.resize(1320, 800)
        self.setMinimumSize(960, 560)
        self.tabs = DanmakuTabs(app, self)
        self.setCentralWidget(self.tabs)
        self._centered = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered:
            self._center_on_parent()
            self._centered = True
        self.tabs.on_show()

    def _center_on_parent(self):
        parent = self.app.window() if self.app is not None else None
        if parent is None:
            return
        geo = parent.frameGeometry()
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)

    def closeEvent(self, event):
        # 关窗口＝停弹幕渲染与回放；录制页的录制任务不受影响
        self.tabs.on_hide()
        super().closeEvent(event)


def open_danmaku_window(app):
    """打开（或复用）弹幕姬窗口，返回窗口实例。"""
    win = getattr(app, "danmaku_win", None)
    if win is None:
        win = DanmakuWindow(app)
        if app is not None:
            app.danmaku_win = win
    win.show()
    win.raise_()
    win.activateWindow()
    return win
