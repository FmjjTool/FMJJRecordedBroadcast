"""音乐解密页（PySide6 版）。"""

import os

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.config_manager import get_export_subdir

from gui_qt.pages.base import BasePage


class MusicUnlockPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🔓 音乐解密")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("解密 ncm / qmc / mflac / mgg / kgm / vpr / kwm / xm / tm / x2m / x3m 等 32 种加密格式",
                                styleSheet="color: #888;"))

        btn_row = QHBoxLayout()
        btn = QPushButton("📄 选择加密文件（可多选）")
        btn.clicked.connect(self._unlock)
        btn_row.addWidget(btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _log(self, msg):
        self.log.append(msg)

    def _unlock(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择加密音乐", "",
            "加密音乐 (*.ncm *.qmc* *.mflac* *.mgg* *.kgm* *.vpr *.kwm *.xm *.tm *.x2m *.x3m);;所有文件 (*.*)")
        if not files:
            return
        from music_unlock import decrypt_to
        out_dir = str(get_export_subdir("unlock"))
        os.makedirs(out_dir, exist_ok=True)
        self.log.clear()
        for fp in files:
            try:
                out = decrypt_to(fp, out_dir)
                self._log(f"✅ {os.path.basename(fp)} → {out}")
            except Exception as e:
                self._log(f"❌ {os.path.basename(fp)}: {e}")
        self._log("\n✅ 解密完成")
