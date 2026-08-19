"""继续操作页（PySide6 版）：外部软件/链接启动器。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config

from gui_qt.pages.base import BasePage


class LauncherPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.items = load_convert_config().get("launcher_items", [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("🚀 继续操作")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("点击卡片启动外部软件 / 链接（配置请在原版「继续操作」页设置）")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        grid = QGridLayout()
        for i, item in enumerate(self.items):
            if i >= 12:
                break
            row, col = divmod(i, 4)
            name = item.get("name", f"软件{i + 1}")
            path = item.get("path", "")
            is_link = item.get("type") == "link"
            card = QPushButton(f"{'🔗' if is_link else '📦'} {name}")
            card.setMinimumHeight(80)
            card.setStyleSheet(
                "background: #1a3a2a; border-radius: 12px; font-size: 14px;" if is_link
                else "background: #1e1e3a; border-radius: 12px; font-size: 14px;")
            card.setCursor(Qt.PointingHandCursor)
            card.clicked.connect(lambda checked, p=path: self._launch(p))
            grid.addWidget(card, row, col)
        layout.addLayout(grid)
        layout.addStretch(1)

    def _launch(self, path):
        if path:
            try:
                os.startfile(path)
            except Exception as e:
                self.app.toast(f"启动失败: {e}")
