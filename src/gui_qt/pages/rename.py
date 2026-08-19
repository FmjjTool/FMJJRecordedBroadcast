"""重命名页（PySide6 版，简化）：按日期文件夹浏览 + 标准格式重命名预览。"""

import os

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.rename_engine import scan_date_folders, extract_time_str, extract_file_title

from gui_qt.pages.base import BasePage


class RenamePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.folders = []
        self.current_files = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("✏️ 重命名")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        bar = QHBoxLayout()
        bar.addWidget(QPushButton("🔄 刷新", clicked=self._refresh))
        bar.addStretch(1)
        layout.addLayout(bar)

        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(140)
        self.folder_list.itemClicked.connect(self._on_folder_click)
        layout.addWidget(self.folder_list)

        self.file_list = QListWidget()
        layout.addWidget(self.file_list, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        layout.addWidget(self.log)

        self._refresh()

    def _refresh(self):
        self.folders = scan_date_folders(self.app.work_dir)
        self.folder_list.clear()
        for name, _ in self.folders:
            self.folder_list.addItem(f"📁 {name}")

    def _on_folder_click(self, item):
        folder = item.text().replace("📁 ", "").strip()
        path = os.path.join(self.app.work_dir, folder)
        self.current_files = []
        self.file_list.clear()
        if not os.path.isdir(path):
            return
        for f in sorted(os.listdir(path)):
            fp = os.path.join(path, f)
            if os.path.isfile(fp):
                self.current_files.append(fp)
                # 预览标准格式新文件名
                ts = extract_time_str(f)
                title = extract_file_title(f)
                preview = f"{ts}_{title}" if ts else f
                self.file_list.addItem(f"{f}\n    → {preview}")
        self.log.append(f"📂 {folder}: {len(self.current_files)} 个文件（重命名预览如上，实际重命名请在原版操作）")
