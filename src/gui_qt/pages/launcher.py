"""继续操作页（PySide6 版）：外部软件/链接启动器 + 配置页。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt.pages.base import BasePage


class LauncherPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.items = load_convert_config().get("launcher_items", [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_panel(), "功能")
        self.tabs.addTab(self._build_config(), "设置")

    def _build_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.panel_grid = QGridLayout()
        v.addLayout(self.panel_grid)
        v.addStretch(1)
        self._refresh_panel()
        return w

    def _build_config(self):
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(QPushButton("➕ 添加软件", clicked=self._add_item))
        bar.addWidget(QPushButton("🔗 添加链接", clicked=self._add_link))
        save = QPushButton("💾 保存并刷新面板")
        save.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        save.clicked.connect(self._save)
        bar.addWidget(save)
        bar.addStretch(1)
        v.addLayout(bar)

        self.cfg_scroll = QScrollArea()
        self.cfg_scroll.setWidgetResizable(True)
        self.cfg_widget = QWidget()
        self.cfg_layout = QVBoxLayout(self.cfg_widget)
        self.cfg_layout.setAlignment(Qt.AlignTop)
        self.cfg_scroll.setWidget(self.cfg_widget)
        v.addWidget(self.cfg_scroll, 1)
        self._refresh_config()
        return w

    def _refresh_panel(self):
        while self.panel_grid.count():
            item = self.panel_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, item in enumerate(self.items):
            if i >= 12:
                break
            row, col = divmod(i, 4)
            name = item.get("name", f"软件{i + 1}")
            path = item.get("path", "")
            is_link = item.get("type") == "link"
            card = QPushButton(f"{'🔗' if is_link else '📦'} {name}")
            card.setMinimumHeight(80)
            card.setStyleSheet(
                "background: #1a3a2a; border-radius: 12px; font-size: 14px;" if is_link
                else "background: #1e1e3a; border-radius: 12px; font-size: 14px;")
            card.setCursor(Qt.PointingHandCursor)
            card.clicked.connect(lambda checked, p=path: self._launch(p))
            self.panel_grid.addWidget(card, row, col)

    def _refresh_config(self):
        while self.cfg_layout.count():
            item = self.cfg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.name_entries = []
        self.path_entries = []
        for i, item in enumerate(self.items):
            is_link = item.get("type") == "link"
            row = QHBoxLayout()
            name_e = QLineEdit(item.get("name", ""))
            name_e.setPlaceholderText("名称")
            name_e.setFixedWidth(120)
            row.addWidget(name_e)
            path_e = QLineEdit(item.get("path", ""))
            path_e.setPlaceholderText("http/https 链接" if is_link else "程序路径")
            row.addWidget(path_e, 1)
            if not is_link:
                row.addWidget(QPushButton("📁", clicked=lambda e=path_e: self._browse(e)))
            row.addWidget(QPushButton("⬆", clicked=lambda idx=i: self._move(idx, -1)))
            row.addWidget(QPushButton("⬇", clicked=lambda idx=i: self._move(idx, 1)))
            del_btn = QPushButton("✕")
            del_btn.setStyleSheet("background: #c62828; color: white;")
            del_btn.clicked.connect(lambda checked, idx=i: self._delete(idx))
            row.addWidget(del_btn)
            self.name_entries.append(name_e)
            self.path_entries.append(path_e)
            cw = QWidget()
            cw.setLayout(row)
            self.cfg_layout.addWidget(cw)

    def _browse(self, entry):
        p, _ = QFileDialog.getOpenFileName(self, "选择程序", "", "可执行文件 (*.exe);;所有文件 (*.*)")
        if p:
            entry.setText(p)

    def _add_item(self):
        self.items.append({"name": "", "path": "", "icon": ""})
        self._refresh_config()

    def _add_link(self):
        self.items.append({"name": "", "path": "", "icon": "", "type": "link"})
        self._refresh_config()

    def _move(self, idx, direction):
        ni = idx + direction
        if 0 <= ni < len(self.items):
            self.items.insert(ni, self.items.pop(idx))
            self._refresh_config()

    def _delete(self, idx):
        if 0 <= idx < len(self.items):
            self.items.pop(idx)
            self._refresh_config()

    def _save(self):
        for i in range(min(len(self.items), len(self.name_entries))):
            self.items[i]["name"] = self.name_entries[i].text().strip()
            self.items[i]["path"] = self.path_entries[i].text().strip()
        cfg = load_convert_config()
        cfg["launcher_items"] = self.items
        save_convert_config(cfg)
        self._refresh_panel()
        self.app.toast("软件面板已更新")

    def _launch(self, path):
        if path:
            try:
                os.startfile(path)
            except Exception as e:
                self.app.toast(f"启动失败: {e}")
