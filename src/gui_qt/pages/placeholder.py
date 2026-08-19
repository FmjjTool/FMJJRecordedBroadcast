"""占位页：尚未从 customtkinter 迁移到 PySide6 的页面。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from gui_qt.pages.base import BasePage


class PlaceholderPage(BasePage):
    def __init__(self, app, title: str):
        super().__init__(app)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)

        icon = QLabel("🚧")
        icon.setAlignment(Qt.AlignCenter)
        font = icon.font()
        font.setPointSize(48)
        icon.setFont(font)

        name = QLabel(title)
        name.setAlignment(Qt.AlignCenter)
        font2 = name.font()
        font2.setPointSize(16)
        font2.setBold(True)
        name.setFont(font2)

        hint = QLabel("该页面正在从 customtkinter 迁移到 PySide6，稍后开放")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #888;")
        hint2 = QLabel("已迁移页面可正常使用，其余功能请在原版（平台信息获取/）中使用")
        hint2.setAlignment(Qt.AlignCenter)
        hint2.setStyleSheet("color: #666;")

        layout.addStretch(1)
        layout.addWidget(icon)
        layout.addWidget(name)
        layout.addWidget(hint)
        layout.addWidget(hint2)
        layout.addStretch(1)
