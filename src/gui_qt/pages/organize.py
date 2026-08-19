"""自动整理页（PySide6 版）。"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from toolbox.organize_engine import scan_files, execute_organize
from toolbox.utils import format_size

from gui_qt.pages.base import BasePage


class OrganizePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.groups = {}
        self.no_date_files = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📂 自动整理")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        dir_label = QLabel(app.work_dir)
        dir_label.setStyleSheet("color: #888;")
        layout.addWidget(dir_label)

        btn_row = QHBoxLayout()
        scan_btn = QPushButton("🔍 扫描并预览")
        scan_btn.clicked.connect(self._scan)
        btn_row.addWidget(scan_btn)
        self.exec_btn = QPushButton("📂 执行整理")
        self.exec_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        self.exec_btn.clicked.connect(self._execute)
        btn_row.addWidget(self.exec_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        layout.addWidget(self.preview_text, 2)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        layout.addWidget(self.log_text, 1)

    def _log(self, msg):
        self.log_text.append(msg)

    def _scan(self):
        if not self.app.work_dir or not __import__('os').path.isdir(self.app.work_dir):
            self._log("⚠️ 请先在全局设置指定工作目录")
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
            self._log("⚠️ 请先扫描")
            return
        moved, failed, errors = execute_organize(self.app.work_dir, self.groups)
        self._log(f"✅ 整理完成: 移动 {moved} 个，失败 {failed} 个")
        for e in errors[:10]:
            self._log(f"  ❌ {e}")
        self._scan()
