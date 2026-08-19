"""资产管理页（PySide6 版，简化）：已获取资源搜索 + 批量操作。"""

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

from gui_qt.common import COLLECTED, _fmt
from gui_qt.pages.base import BasePage


class AssetPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🗂 资产管理")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题 / 作者（输入即时过滤）")
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        self.count_label = QLabel("")
        layout.addWidget(self.count_label)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.list, 1)

        bar = QHBoxLayout()
        dl_btn = QPushButton("⬇️ 下载选中")
        dl_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        dl_btn.clicked.connect(self._download)
        bar.addWidget(dl_btn)
        del_btn = QPushButton("🗑 删除选中")
        del_btn.clicked.connect(self._delete)
        bar.addWidget(del_btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._filter("")

    def _filter(self, kw):
        self.list.clear()
        kw = kw.strip().lower()
        shown = 0
        for info in COLLECTED:
            text = f"[{info.platform}] {info.title or '(无标题)'}  @{info.author or '-'}  {_fmt(info.play_count)}"
            if kw and kw not in text.lower():
                continue
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, id(info))
            self.list.addItem(it)
            shown += 1
        self.count_label.setText(f"显示 {shown} / 共 {len(COLLECTED)}")

    def _selected_infos(self):
        ids = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
               if self.list.item(i).isSelected()}
        return [info for info in COLLECTED if id(info) in ids]

    def _download(self):
        infos = self._selected_infos()
        if not infos:
            QMessageBox.warning(self, "提示", "请先选中资源")
            return

        def work():
            from media_downloader import download_media
            from toolbox.config_manager import get_export_subdir
            out = str(get_export_subdir("video_data"))
            ok = 0
            for info in infos:
                url = info.video_url or info.raw_url
                if not url:
                    continue
                try:
                    download_media(url, out)
                    ok += 1
                except Exception:
                    pass
            return ok

        def done(result, err):
            self.app.toast(f"✅ 下载完成 {result}/{len(infos)}")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _delete(self):
        infos = self._selected_infos()
        if not infos:
            return
        for info in infos:
            if info in COLLECTED:
                COLLECTED.remove(info)
        self._filter(self.search.text())

    def _bg(self, fn, on_done):
        try:
            r = fn()
            e = None
        except Exception as ex:
            r, e = None, str(ex)
        QTimer.singleShot(0, lambda: on_done(r, e))
