"""录播工具箱页面共享常量与工具函数（对应原版 gui/toolbox_common.py）。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui_qt import theme
from gui_qt.emoji_icons import split_leading_emoji
from gui_qt.navigation import line_icon
from toolbox.date_folders import (  # 兼容旧导入路径，单一来源在 toolbox.date_folders
    DATE_FOLDER_RE, count_date_subfolders, has_date_subfolders,
)


class ToolPageHeader(QWidget):
    """工具页标题与说明；颜色由全局主题的语义角色统一提供。"""

    def __init__(self, title: str, description: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("toolPageHeader")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "title")
        layout.addWidget(self.title_label)
        self.description_label = QLabel(description, self)
        self.description_label.setProperty("role", "subtitle")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        # 设置可见性前先归属页面，避免无父 QLabel 被 Qt 当作独立窗口弹出。
        self.description_label.setVisible(bool(description))


class ToolSectionCard(QFrame):
    """工具页内容卡片，标题行可放状态，content_layout 放原有功能控件。"""

    def __init__(self, title: str, description: str = "", parent=None,
                 icon: str | None = None):
        parsed_icon, parsed_title = split_leading_emoji(title)
        if parsed_icon is not None:
            title = parsed_title
            if icon is None:
                icon = parsed_icon
        super().__init__(parent)
        self.setObjectName("toolSectionCard")
        self.setProperty("variant", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        self.heading_layout = QHBoxLayout()
        self.heading_layout.setContentsMargins(0, 0, 0, 0)
        self.icon_label: QLabel | None = None
        if icon:
            self.icon_label = QLabel()
            self.icon_label.setFixedSize(18, 18)
            self.icon_label.setAlignment(Qt.AlignCenter)
            self.heading_layout.addWidget(self.icon_label)
            self.set_icon(icon)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "sectionTitle")
        self.heading_layout.addWidget(self.title_label)
        self.heading_layout.addStretch(1)
        layout.addLayout(self.heading_layout)
        if description:
            label = QLabel(description)
            label.setProperty("role", "muted")
            label.setWordWrap(True)
            layout.addWidget(label)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        layout.addLayout(self.content_layout, 1)

    def set_icon(self, name: str, color: str | None = None):
        if self.icon_label is None:
            return
        if color is None:
            color = theme.current_palette().color(QPalette.Link).name()
        self.icon_label.setPixmap(line_icon(name, color, 16).pixmap(16, 16))
