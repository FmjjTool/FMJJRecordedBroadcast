# -*- coding: utf-8 -*-
"""网络库页：浏览四大网盘目录、断点续传下载、可选自动解压。

复用 QuarkDownloadPage，不复制业务逻辑；所有备份入口统一在全局设置。
登录状态由软件统一管理，本页不保存任何凭据。
视觉全部使用主题语义角色与变体属性（ui-visual-spec），不写死颜色。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from gui_qt.pages.base import BasePage


class NetdiskPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        from gui_qt.pages.quark_download import QuarkDownloadPage

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(0)

        self.download_page = QuarkDownloadPage(app)
        outer.addWidget(self.download_page)

    # ---------- 生命周期 ----------
    def on_theme_changed(self):
        """把主题切换转发给下载页。"""
        self.download_page.on_theme_changed()

    def on_show(self):
        self.download_page.on_show()

    def on_hide(self):
        self.download_page.on_hide()

    def shutdown(self):
        self.download_page.shutdown()
