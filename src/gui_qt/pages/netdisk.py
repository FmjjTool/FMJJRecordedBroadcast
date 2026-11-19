# -*- coding: utf-8 -*-
"""网盘页：备份到网盘 + 文件下载（先备份后下载）。

两个页签直接复用既有页面，不复制任何业务逻辑：

- 备份到网盘 → `QuarkBackupPage`（本地目录 → 云端文件夹的增量备份与运行日志）
- 文件下载 → `QuarkDownloadPage`（浏览三大网盘目录、断点续传下载、可选自动解压）

登录状态由软件统一管理，本页不保存任何凭据；定时备份仍统一在「任务」页配置。
视觉全部使用主题语义角色与变体属性（ui-visual-spec），不写死颜色。
"""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout

from gui_qt.pages.base import BasePage


class NetdiskPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        from gui_qt.pages.quark_backup import QuarkBackupPage
        from gui_qt.pages.quark_download import QuarkDownloadPage

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(0)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        self.backup_page = QuarkBackupPage(app)
        self.tabs.addTab(self.backup_page, "备份到网盘")
        self.download_page = QuarkDownloadPage(app)
        self.tabs.addTab(self.download_page, "文件下载")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ---------- 生命周期 ----------
    def _on_tab_changed(self, index: int):
        page = self.tabs.widget(index)
        if page is not None:
            page.on_show()

    def on_show(self):
        page = self.tabs.currentWidget()
        if page is not None:
            page.on_show()

    def on_hide(self):
        for page in (self.download_page, self.backup_page):
            page.on_hide()

    def shutdown(self):
        for page in (self.download_page, self.backup_page):
            page.shutdown()