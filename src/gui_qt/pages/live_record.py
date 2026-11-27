# -*- coding: utf-8 -*-
"""直播录制页面：录制界面内嵌在侧栏页面里，弹幕姬由页面工具栏按钮打开。

录制 / 监控属于后台业务：切到别的页面只停界面刷新，任务照常运行；
退出软件时由主窗口统一调用 window.shutdown() 收尾。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from gui_qt.pages.base import BasePage


class LiveRecordPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        from gui_qt.live_record_window import LiveRecordWindow

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.window = LiveRecordWindow(app, embedded=True)
        layout.addWidget(self.window)
        # 主窗口 / 托盘退出按这个引用做录制收尾，必须指向内嵌实例
        app.live_record_win = self.window

    def on_show(self):
        self.window.refresh_status()

    def close_danmaku(self):
        """切走页面或退出时收起弹幕姬窗口（它只做弹幕查看/回放）。"""
        win = getattr(self.app, "danmaku_win", None)
        if win is not None:
            try:
                win.close()
            except Exception:
                pass

    def has_critical_services(self):
        try:
            act = self.window.activity()
        except Exception:
            return False
        return bool(act["recording"] or act["monitoring"])

    def shutdown(self):
        self.close_danmaku()
        self.window.shutdown()
