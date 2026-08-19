"""网易云音乐下载页（PySide6 版，简化）。"""

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

from toolbox.config_manager import get_export_subdir

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage


class NeteasePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.results = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🎧 音乐下载（网易云）")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.kw_entry = QLineEdit()
        self.kw_entry.setPlaceholderText("搜索歌曲名 / 歌手")
        self.kw_entry.returnPressed.connect(self._search)
        row.addWidget(self.kw_entry, 1)
        self.search_btn = QPushButton("🔍 搜索")
        self.search_btn.clicked.connect(self._search)
        row.addWidget(self.search_btn)
        layout.addLayout(row)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        self.dl_btn = QPushButton("⬇️ 下载选中")
        self.dl_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.dl_btn.clicked.connect(self._download)
        layout.addWidget(self.dl_btn)

    def _search(self):
        kw = self.kw_entry.text().strip()
        if not kw:
            QMessageBox.warning(self, "提示", "请输入关键词")
            return
        self.search_btn.setEnabled(False)

        def work():
            from netease import NeteaseAPI, get_netease_cookie
            api = NeteaseAPI()
            cookies = get_netease_cookie() or {}
            return api.search_music(kw, cookies, limit=20)

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                QMessageBox.critical(self, "错误", str(err))
                return
            self.results = result or []
            self.list.clear()
            for s in self.results:
                name = s.get("name", "")
                artist = " / ".join(a.get("name", "") for a in s.get("artists", []) or s.get("ar", []))
                self.list.addItem(f"{name}  -  {artist}")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _bg(self, fn, on_done):
        try:
            r = fn()
            e = None
        except Exception as ex:
            r, e = None, str(ex)
        post_to_main(on_done, r, e)

    def _download(self):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.results):
            QMessageBox.warning(self, "提示", "请先选中一首歌")
            return
        song = self.results[row]
        sid = song.get("id")
        name = song.get("name", str(sid))
        self.dl_btn.setEnabled(False)

        def work():
            from netease import MusicDownloader
            d = MusicDownloader()
            return d.download_song(sid, name, str(get_export_subdir("songs")))

        def done(result, err):
            self.dl_btn.setEnabled(True)
            if err:
                QMessageBox.critical(self, "错误", str(err))
            else:
                self.app.toast(f"✅ 已下载: {name}")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()
