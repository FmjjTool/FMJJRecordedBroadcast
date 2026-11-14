"""录播工具箱页面共享常量与工具函数（对应原版 gui/toolbox_common.py）。"""

import os
import re

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


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

    def __init__(self, title: str, description: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("toolSectionCard")
        self.setProperty("variant", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        self.heading_layout = QHBoxLayout()
        self.heading_layout.setContentsMargins(0, 0, 0, 0)
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


# 日期文件夹正则（YYYYMMDD）
DATE_FOLDER_RE = re.compile(r"^(20\d{6})")


def has_date_subfolders(directory: str) -> bool:
    """检查目录下是否存在日期格式的二级文件夹"""
    if not os.path.isdir(directory):
        return False
    for name in os.listdir(directory):
        full = os.path.join(directory, name)
        if os.path.isdir(full) and DATE_FOLDER_RE.match(name):
            return True
    return False


def count_date_subfolders(directory: str) -> int:
    """统计目录下日期格式的二级文件夹数量"""
    if not os.path.isdir(directory):
        return 0
    count = 0
    for name in os.listdir(directory):
        full = os.path.join(directory, name)
        if os.path.isdir(full) and DATE_FOLDER_RE.match(name):
            count += 1
    return count
