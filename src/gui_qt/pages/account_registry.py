"""账号名单（只读总览）：三个账号中心的统一视图。

数据来自 `toolbox.accounts_overview.load_overview()`，按「平台 + 规范化链接」去重，
显示每个账号被哪些功能使用、分组与采集类型。

本页不写任何账号文件；增删账号与按功能勾选启用统一在「Cookie 管理 → 账号配置」。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt.pages.base import BasePage
from toolbox.accounts_overview import load_overview

_COLUMNS = ["账号", "平台", "分组", "使用位置", "采集类型", "链接"]


class AccountRegistryPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._overview = {"accounts": [], "stats": {}}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        self.summary = QLabel("加载中…")
        self.summary.setProperty("role", "caption")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索账号名、链接或分组")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("全部平台", "")
        self.platform_combo.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.platform_combo)

        self.source_combo = QComboBox()
        for label, key in (("全部来源", ""), ("信息获取", "info"),
                           ("网媒追踪", "tracker"), ("音乐追踪", "music")):
            self.source_combo.addItem(label, key)
        self.source_combo.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.source_combo)

        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        filters.addWidget(refresh)
        outer.addLayout(filters)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        outer.addWidget(self.table, 1)

        self.note = QLabel(
            "只读总览：账号的添加/删除、分组与「可用于哪些功能」统一在"
            "「Cookie 管理 → 账号配置」里维护，本页不改写任何账号文件。")
        self.note.setProperty("role", "caption")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)

        self.reload()

    # ---------- 数据 ----------
    def reload(self):
        self._overview = load_overview()
        stats = self._overview["stats"]
        by_source = stats.get("by_source", {})
        self.summary.setText(
            f"去重后 {stats.get('unique', 0)} 个账号 · 原始条目 {stats.get('raw', 0)}"
            f"（信息 {by_source.get('info', 0)} / 网媒 {by_source.get('tracker', 0)}"
            f" / 音乐 {by_source.get('music', 0)}）· 跨来源重复 {stats.get('duplicates', 0)} 个")
        self._sync_platform_combo()
        self._apply_filter()

    def _sync_platform_combo(self):
        current = self.platform_combo.currentData()
        names = sorted({a["platform"] for a in self._overview["accounts"] if a["platform"]})
        self.platform_combo.blockSignals(True)
        self.platform_combo.clear()
        self.platform_combo.addItem("全部平台", "")
        for key in names:
            label = next((a["platform_name"] for a in self._overview["accounts"]
                          if a["platform"] == key), key)
            self.platform_combo.addItem(label, key)
        index = self.platform_combo.findData(current)
        self.platform_combo.setCurrentIndex(max(0, index))
        self.platform_combo.blockSignals(False)

    def _apply_filter(self, *_args):
        query = self.search.text().strip().casefold()
        platform = self.platform_combo.currentData() or ""
        source = self.source_combo.currentData() or ""
        rows = []
        for account in self._overview["accounts"]:
            if platform and account["platform"] != platform:
                continue
            if source and source not in account["sources"]:
                continue
            haystack = " ".join([
                account.get("name", ""), account.get("url", ""),
                account.get("groups_text", ""), account.get("sources_text", ""),
            ]).casefold()
            if query and query not in haystack:
                continue
            rows.append(account)
        self._fill(rows)

    def _fill(self, rows):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, account in enumerate(rows):
            values = [
                account.get("name") or "（未命名）",
                account.get("platform_name", ""),
                account.get("groups_text", ""),
                account.get("sources_text", ""),
                account.get("kinds_text", ""),
                account.get("url", ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 5:
                    item.setToolTip(str(value))
                if column in (1, 2, 3, 4):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.AscendingOrder)