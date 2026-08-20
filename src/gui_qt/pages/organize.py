"""自动整理页（PySide6 版）。

对应原版 gui/toolbox_pages.py OrganizePage：
- 目录栏：工作目录 + 日期文件夹计数（随全局工作目录变化）
- 扫描并预览：按日期分组预览（每组前 3 个文件）
- 执行整理：确认后执行，完成后自动重新扫描
- 回车快捷：第一次回车=扫描，第二次回车=执行整理
"""

import os

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.organize_engine import execute_organize, scan_files
from toolbox.utils import format_size

from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import count_date_subfolders


class OrganizePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.groups = {}
        self.no_date_files = []
        self._enter_stage = 0  # 回车快捷：0=待扫描，1=待执行

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # 目录栏：工作目录 + 日期文件夹计数
        dir_row = QHBoxLayout()
        self.dir_label = QLabel(app.work_dir)
        dir_row.addWidget(self.dir_label, 1)
        self.dir_date_label = QLabel("")
        self.dir_date_label.setStyleSheet("color: #888;")
        dir_row.addWidget(self.dir_date_label)
        layout.addLayout(dir_row)
        self._update_dir_date_label()

        # 控制按钮
        btn_row = QHBoxLayout()
        scan_btn = QPushButton("🔍 扫描并预览")
        scan_btn.clicked.connect(self._scan)
        btn_row.addWidget(scan_btn)
        exec_btn = QPushButton("📂 执行整理")
        exec_btn.setStyleSheet("background: #2e7d32; color: white;")
        exec_btn.clicked.connect(self._execute)
        btn_row.addWidget(exec_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 预览 + 日志
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        layout.addWidget(self.preview_text, 2)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(100)
        layout.addWidget(self.log_text)

    # ==================== 回车快捷 ====================

    def on_return_press(self):
        if self._enter_stage == 0:
            self._enter_stage = 1
            self._scan()
            self._log("⏎ 再次按回车将直接执行整理（或点「执行整理」）")
        else:
            self._enter_stage = 0
            self._execute()

    # ==================== 目录 ====================

    def on_dir_changed(self, path):
        self.dir_label.setText(path)
        self._update_dir_date_label()

    def _update_dir_date_label(self):
        count = count_date_subfolders(self.app.work_dir)
        self.dir_date_label.setText(
            f"📅 {count} 个日期文件夹" if count else "⚠️ 无日期文件夹")

    # ==================== 扫描 / 执行 ====================

    def _scan(self):
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            self._log("⚠️ 工作目录不存在，请先在全局设置指定")
            return
        self.groups, self.no_date_files = scan_files(self.app.work_dir)
        self.preview_text.clear()
        total = 0
        for date_str, files in self.groups.items():
            total += len(files)
            self.preview_text.append(f"📁 {date_str}/  ({len(files)} 个文件)")
            for f in files[:3]:
                self.preview_text.append(f"   - {f['name']}  ({format_size(f['size'])})")
            if len(files) > 3:
                self.preview_text.append(f"   ... 还有 {len(files) - 3} 个")
            self.preview_text.append("")
        self.preview_text.append(f"共 {len(self.groups)} 个日期分组，{total} 个文件")
        if self.no_date_files:
            self.preview_text.append(f"\n⚠️ 无法识别日期: {len(self.no_date_files)} 个")
            for n in self.no_date_files[:10]:
                self.preview_text.append(f"   - {n}")

    def _execute(self):
        if not self.groups:
            return
        n_files = sum(len(v) for v in self.groups.values())
        ret = QMessageBox.question(
            self, "确认",
            f"将整理 {n_files} 个文件到 {len(self.groups)} 个文件夹，确认？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.log_text.clear()
        success, fail, failed_files = execute_organize(self.app.work_dir, self.groups)
        self._log(f"✅ 整理完成: 成功 {success}, 失败 {fail}")
        if failed_files:
            self._log(f"失败: {', '.join(failed_files[:10])}")
        self._scan()

    def _log(self, msg):
        self.log_text.append(str(msg))
