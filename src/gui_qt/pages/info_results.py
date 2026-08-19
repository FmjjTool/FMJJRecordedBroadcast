"""已获取页（PySide6 版）：按平台分三栏（抖音/B站/微博）展示。"""

import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from gui_qt.common import COLLECTED, _fmt, post_to_main
from gui_qt.pages.base import BasePage

PLATFORM_CN = {"douyin": "抖音", "bilibili": "B站", "weibo": "微博"}
PLATFORMS = ["bilibili", "douyin", "weibo"]


class InfoResultsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📦 已获取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        # 工具栏
        bar = QHBoxLayout()
        self.count_label = QLabel("")
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        bar.addWidget(QPushButton("🔄 刷新", clicked=self._refresh))
        bar.addWidget(QPushButton("📤 导出选中 (JSON)", clicked=self._export_json))
        bar.addWidget(QPushButton("📤 导出选中 (Excel)", clicked=self._export_excel))
        layout.addLayout(bar)

        # 三栏：抖音 / B站 / 微博
        cols = QHBoxLayout()
        cols.setSpacing(8)
        self.cols_list = {}
        for plat in PLATFORMS:
            col = self._build_column(PLATFORM_CN[plat])
            cols.addWidget(col, 1)
        layout.addLayout(cols, 1)

        self._refresh()

    def _build_column(self, name):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        # 标题栏
        head = QFrame()
        head.setStyleSheet("QFrame { background: #23262f; border-radius: 6px; padding: 4px; }")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 2, 8, 2)
        hl.addWidget(QLabel(f"📦 {name}"))
        hl.addStretch(1)
        cb_all = QPushButton("全选")
        cb_all.clicked.connect(lambda checked, p=name: self._select_all(p, True))
        hl.addWidget(cb_all)
        cb_none = QPushButton("全不选")
        cb_none.clicked.connect(lambda checked, p=name: self._select_all(p, False))
        hl.addWidget(cb_none)
        cb_inv = QPushButton("反选")
        cb_inv.clicked.connect(lambda checked, p=name: self._invert(p))
        hl.addWidget(cb_inv)
        v.addWidget(head)
        # 列表
        lst = QListWidget()
        lst.setStyleSheet("QListWidget { background: #1a1d27; border-radius: 6px; }")
        lst.setSelectionMode(QListWidget.ExtendedSelection)
        v.addWidget(lst, 1)
        self.cols_list[name] = lst
        return w

    def _select_all(self, name, val):
        lst = self.cols_list[name]
        for i in range(lst.count()):
            it = lst.item(i)
            it.setCheckState(Qt.Checked if val else Qt.Unchecked)

    def _invert(self, name):
        lst = self.cols_list[name]
        for i in range(lst.count()):
            it = lst.item(i)
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _refresh(self):
        for lst in self.cols_list.values():
            lst.clear()
        counts = {p: 0 for p in PLATFORMS}
        for info in COLLECTED:
            p = info.platform
            if p not in self.cols_list:
                continue
            counts[p] += 1
            text = f"{info.title or '(无标题)'}\n  @{info.author or '-'}  {_fmt(info.play_count)}"
            it = QListWidgetItem(text)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, id(info))
            # 异步加载封面缩略图
            self._load_thumb(info, it)
            self.cols_list[p].addItem(it)
        for p, c in counts.items():
            pass
        total = sum(counts.values())
        self.count_label.setText(f"共 {total} 条（抖音 {counts['douyin']} / B站 {counts['bilibili']} / 微博 {counts['weibo']}）")

    def _load_thumb(self, info, item):
        if not info.cover_url:
            return
        def work():
            import requests
            try:
                r = requests.get(info.cover_url, timeout=10)
                return r.content
            except Exception:
                return None

        def done(data, err):
            if data:
                img = QImage.fromData(data).scaledToHeight(60, Qt.SmoothTransformation)
                item.setIcon(QPixmap.fromImage(img))

        from gui_qt.common import run_bg
        run_bg(work, done)

    def _selected_infos(self):
        out = []
        for lst in self.cols_list.values():
            for i in range(lst.count()):
                it = lst.item(i)
                if it.checkState() == Qt.Checked:
                    out.append(id(it.data(Qt.UserRole)))
        return [info for info in COLLECTED if id(info) in out]

    def _export_json(self):
        from exporters.writer import write_results_json
        infos = self._selected_infos() or COLLECTED
        if not infos:
            QMessageBox.warning(self, "提示", "没有资源")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 JSON", "已获取.json", "JSON (*.json)")
        if not path:
            return
        write_results_json(infos, path)
        self.app.toast(f"已导出 {len(infos)} 条", "success")

    def _export_excel(self):
        from exporters.writer import write_results_excel
        infos = self._selected_infos() or COLLECTED
        if not infos:
            QMessageBox.warning(self, "提示", "没有资源")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "已获取.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        write_results_excel(infos, path)
        self.app.toast(f"已导出 {len(infos)} 条", "success")
