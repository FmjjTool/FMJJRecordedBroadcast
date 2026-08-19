"""日历浏览页（PySide6 版）：按年份浏览日期文件夹。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from toolbox.rename_engine import scan_date_folders

from gui_qt.pages.base import BasePage


class CalendarPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.date_map = {}  # date -> folder_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📅 日历浏览")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        bar = QHBoxLayout()
        bar.addWidget(QPushButton("🔄 刷新", clicked=self._scan))
        self.path_label = QLabel(app.work_dir)
        self.path_label.setStyleSheet("color: #888;")
        bar.addWidget(self.path_label, 1)
        layout.addLayout(bar)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open)
        layout.addWidget(self.list, 1)

        self._scan()

    def _scan(self):
        from datetime import datetime
        self.date_map = {}
        for name, _ in scan_date_folders(self.app.work_dir):
            try:
                dt = datetime.strptime(name[:8], "%Y%m%d")
                self.date_map[dt.date()] = name
            except Exception:
                continue
        self.list.clear()
        years = sorted({d.year for d in self.date_map}, reverse=True)
        for y in years:
            days = [d for d in self.date_map if d.year == y]
            item = QListWidgetItem(f"📅 {y} 年 —— {len(days)} 个日期文件夹")
            item.setData(Qt.UserRole, None)
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self.list.addItem(item)
            for d in sorted(days):
                folder = self.date_map[d]
                sub = QListWidgetItem(f"    {d.isoformat()}  📁 {folder}")
                sub.setData(Qt.UserRole, folder)
                self.list.addItem(sub)

    def _open(self, item):
        folder = item.data(Qt.UserRole)
        if folder:
            path = os.path.join(self.app.work_dir, folder)
            if os.path.isdir(path):
                os.startfile(path)
