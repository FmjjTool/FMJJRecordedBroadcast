"""文件迁移页（PySide6 版，简化）。"""

import os
import shutil

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

from gui_qt.pages.base import BasePage


class MigratePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.src = ""
        self.dst = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🚚 文件迁移")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("源目录:"))
        self.src_label = QLabel("（未选择）")
        self.src_label.setStyleSheet("color: #888;")
        src_row.addWidget(self.src_label, 1)
        src_row.addWidget(QPushButton("📂 选择", clicked=self._pick_src))
        layout.addLayout(src_row)

        dst_row = QHBoxLayout()
        dst_row.addWidget(QLabel("目标目录:"))
        self.dst_label = QLabel("（未选择）")
        self.dst_label.setStyleSheet("color: #888;")
        dst_row.addWidget(self.dst_label, 1)
        dst_row.addWidget(QPushButton("📂 选择", clicked=self._pick_dst))
        layout.addLayout(dst_row)

        self.cut_mode = QCheckBox("剪切（移动）而非复制")
        layout.addWidget(self.cut_mode)

        run = QPushButton("🚚 开始迁移")
        run.setStyleSheet("background: #2e7d32; color: white; padding: 6px 14px; border-radius: 4px;")
        run.clicked.connect(self._migrate)
        layout.addWidget(run)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _pick_src(self):
        d = QFileDialog.getExistingDirectory(self, "选择源目录")
        if d:
            self.src = d
            self.src_label.setText(d)

    def _pick_dst(self):
        d = QFileDialog.getExistingDirectory(self, "选择目标目录")
        if d:
            self.dst = d
            self.dst_label.setText(d)

    def _migrate(self):
        if not self.src or not self.dst:
            return
        self.log.clear()
        os.makedirs(self.dst, exist_ok=True)
        n = 0
        for f in os.listdir(self.src):
            sp = os.path.join(self.src, f)
            if not os.path.isfile(sp):
                continue
            dp = os.path.join(self.dst, f)
            try:
                if self.cut_mode.isChecked():
                    shutil.move(sp, dp)
                else:
                    shutil.copy2(sp, dp)
                n += 1
                self.log.append(f"✅ {f}")
            except Exception as e:
                self.log.append(f"❌ {f}: {e}")
        self.log.append(f"\n✅ 迁移完成: {n} 个文件")
