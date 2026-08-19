"""已获取页（PySide6 版）：显示共享 COLLECTED 列表 + 导出。"""

import json
import os

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout,
)

from gui_qt.common import COLLECTED, _fmt, _info_text
from gui_qt.pages.base import BasePage


class InfoResultsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📦 已获取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        self.count_label = QLabel("")
        layout.addWidget(self.count_label)

        bar = QHBoxLayout()
        bar.addWidget(QPushButton("🔄 刷新", clicked=self.refresh_count))
        bar.addWidget(QPushButton("📤 导出 JSON", clicked=self._export_json))
        bar.addWidget(QPushButton("📄 导出 Markdown", clicked=self._export_md))
        bar.addStretch(1)
        layout.addLayout(bar)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.refresh_count()

    def refresh_count(self):
        self.count_label.setText(f"共 {len(COLLECTED)} 条已获取数据")
        self.list.clear()
        for info in COLLECTED:
            self.list.addItem(f"[{info.platform}] {info.title or '(无标题)'}  @{info.author or '-'}  {_fmt(info.play_count)}")

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 JSON", "已获取.json", "JSON (*.json)")
        if not path:
            return
        data = [_info_text(i) for i in COLLECTED]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.app.toast(f"已导出 {len(data)} 条到 {path}")

    def _export_md(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 Markdown", "已获取.md", "Markdown (*.md)")
        if not path:
            return
        lines = ["# 已获取数据\n"]
        for info in COLLECTED:
            lines.append(f"## {info.title or '(无标题)'}\n")
            lines.append(f"- 平台: {info.platform}")
            lines.append(f"- 作者: @{info.author or '-'}")
            lines.append(f"- 播放量: {_fmt(info.play_count)}")
            lines.append(f"- 链接: {info.video_url or info.raw_url}\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.app.toast(f"已导出到 {path}")
