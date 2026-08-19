"""重命名页（PySide6 版）：按标准格式批量重命名。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.rename_engine import scan_date_folders, extract_time_str, extract_file_title
from toolbox.config_manager import load_rename_config
from toolbox.utils import clean_special_chars, is_video_file

from gui_qt.pages.base import BasePage


class RenamePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.cfg = load_rename_config()
        self.folders = []
        self.current_files = []
        self.current_folder = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("✏️ 重命名")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        bar = QHBoxLayout()
        bar.addWidget(QPushButton("🔄 刷新", clicked=self._refresh))
        bar.addWidget(QLabel("账户:"))
        self.account = QComboBox()
        self.account.addItems(self.cfg.account_list or ["封茗囧菌"])
        bar.addWidget(self.account)
        bar.addWidget(QLabel("平台:"))
        self.platform = QComboBox()
        self.platform.addItems(self.cfg.platform_list or ["抖音"])
        bar.addWidget(self.platform)
        bar.addWidget(QLabel("版本:"))
        self.version = QComboBox()
        self.version.addItems(["无弹幕", "有弹幕"])
        bar.addWidget(self.version)
        bar.addStretch(1)
        layout.addLayout(bar)

        # 类型多选
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("直播类型(可多选):"))
        self.type_checks = {}
        for t in self.cfg.live_types or ["聊天"]:
            cb = QCheckBox(t)
            cb.toggled.connect(lambda checked: self._update_preview())
            type_row.addWidget(cb)
            self.type_checks[t] = cb
        type_row.addStretch(1)
        layout.addLayout(type_row)

        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(120)
        self.folder_list.itemClicked.connect(self._on_folder_click)
        layout.addWidget(self.folder_list)

        self.file_list = QListWidget()
        layout.addWidget(self.file_list, 1)

        btn_row = QHBoxLayout()
        rename_btn = QPushButton("✅ 重命名当前选中文件")
        rename_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        rename_btn.clicked.connect(self._rename_selected)
        btn_row.addWidget(rename_btn)
        self.preview_label = QLabel("预览: ...")
        self.preview_label.setStyleSheet("color: #64b5f6;")
        btn_row.addWidget(self.preview_label, 1)
        layout.addLayout(btn_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        layout.addWidget(self.log)

        self._refresh()

    def _log(self, msg):
        self.log.append(msg)

    def _refresh(self):
        self.folders = scan_date_folders(self.app.work_dir)
        self.folder_list.clear()
        for name, _ in self.folders:
            self.folder_list.addItem(f"📁 {name}")

    def _on_folder_click(self, item):
        folder = item.text().replace("📁 ", "").strip()
        path = os.path.join(self.app.work_dir, folder)
        self.current_folder = path
        self.current_files = []
        self.file_list.clear()
        if not os.path.isdir(path):
            return
        for f in sorted(os.listdir(path)):
            fp = os.path.join(path, f)
            if os.path.isfile(fp) and is_video_file(fp):
                self.current_files.append(fp)
                self.file_list.addItem(f)
        self._update_preview()

    def _selected_types(self):
        return [t for t, cb in self.type_checks.items() if cb.isChecked()]

    def _build_filename(self, src_name):
        account = self.account.currentText()
        type_str = "+".join(self._selected_types())
        version = self.version.currentText()
        platform = self.platform.currentText()
        ts = extract_time_str(src_name) or "00000000_00-00-00"
        title = clean_special_chars(extract_file_title(src_name))
        parts = [ts]
        if account:
            parts.append(account)
        if type_str:
            parts.append(type_str)
        if version:
            parts.append(version)
        if platform:
            parts.append(platform)
        if title:
            parts.append(title)
        return "_".join(parts) + self.cfg.video_suffix

    def _update_preview(self):
        cur = self.file_list.currentItem()
        if cur:
            name = cur.text()
            self.preview_label.setText(f"预览: {self._build_filename(name)}")

    def _rename_selected(self):
        cur = self.file_list.currentItem()
        if not cur or not self.current_files:
            self._log("⚠️ 请先选中一个文件")
            return
        idx = self.file_list.row(cur)
        src = self.current_files[idx]
        new_name = self._build_filename(os.path.basename(src))
        dst = os.path.join(self.current_folder, new_name)
        if os.path.exists(dst):
            self._log(f"⚠️ 目标已存在: {new_name}")
            return
        try:
            os.rename(src, dst)
            self._log(f"✅ {os.path.basename(src)} → {new_name}")
            self.current_files[idx] = dst
            cur.setText(new_name)
        except Exception as e:
            self._log(f"❌ 失败: {e}")
