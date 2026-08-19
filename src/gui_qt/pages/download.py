"""视频下载页（PySide6 版，简化）。"""

import threading

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage


class VideoDownloadPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📥 视频下载")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("粘贴视频链接（B站/抖音/微博等）")
        self.url_entry.returnPressed.connect(self._download)
        row.addWidget(self.url_entry, 1)
        self.dl_btn = QPushButton("⬇️ 解析并下载")
        self.dl_btn.clicked.connect(self._download)
        row.addWidget(self.dl_btn)
        layout.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _log(self, msg):
        self.log.append(msg)

    def _download(self):
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入链接")
            return
        self.dl_btn.setEnabled(False)
        self.log.clear()
        self._log(f"⏳ 解析并下载: {url}\n")

        def work():
            from multi_downloader import parse_video
            return parse_video(url)

        def done(result, err):
            self.dl_btn.setEnabled(True)
            if err:
                self._log(f"❌ {err}")
                return
            self._log(f"✅ 下载完成: {result}")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _bg(self, fn, on_done):
        try:
            r = fn()
            e = None
        except Exception as ex:
            r, e = None, str(ex)
        post_to_main(on_done, r, e)
