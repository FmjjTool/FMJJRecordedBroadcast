"""歌单页 · 线下活动选择对话框：从腾讯文档「线下活动」本地快照选一场活动。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gui_qt import table_sort

_COLUMNS = ["活动名称", "日期", "城市", "曲目数"]


class EventPickerDialog(QDialog):
    def __init__(self, events: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择线下活动")
        self.resize(760, 520)
        self._events = list(events)
        self._selected: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入活动名称、城市或日期筛选")
        self.search_edit.setMinimumHeight(28)
        self.search_edit.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_edit, 1)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "caption")
        search_row.addWidget(self.count_label)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index, width in ((1, 100), (2, 90), (3, 70)):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            self.table.setColumnWidth(index, width)
        self._sorter = table_sort.install(self.table, skip_columns=())
        self.table.cellDoubleClicked.connect(lambda *_: self._accept_selected())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_btn = QPushButton("使用该活动")
        self.ok_btn.setProperty("variant", "primary")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._accept_selected)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.table.itemSelectionChanged.connect(
            lambda: self.ok_btn.setEnabled(self._selected_row() is not None))
        self._fill()

    # ---------- 数据 ----------

    def _fill(self):
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(self._events))
            for row_index, event in enumerate(self._events):
                name = QTableWidgetItem(event.get("name") or "")
                name.setData(Qt.UserRole, event.get("row_no"))
                name.setToolTip(event.get("name") or "")
                self.table.setItem(row_index, 0, name)
                self.table.setItem(row_index, 1, QTableWidgetItem(event.get("date") or ""))
                self.table.setItem(row_index, 2, QTableWidgetItem(event.get("city") or ""))
                count = QTableWidgetItem(str(event.get("song_count") or 0))
                count.setTextAlignment(Qt.AlignCenter)
                count.setData(table_sort.SORT_KEY_ROLE, int(event.get("song_count") or 0))
                self.table.setItem(row_index, 3, count)
        finally:
            self.table.setUpdatesEnabled(True)
        self._apply_filter()

    def _apply_filter(self, *_):
        keyword = self.search_edit.text().strip().lower()
        shown = 0
        for row in range(self.table.rowCount()):
            texts = [self.table.item(row, col).text().lower()
                     for col in range(len(_COLUMNS)) if self.table.item(row, col)]
            visible = not keyword or any(keyword in text for text in texts)
            self.table.setRowHidden(row, not visible)
            shown += 1 if visible else 0
        self.count_label.setText(f"共 {shown} / {self.table.rowCount()} 场")

    def _selected_row(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else None

    def _accept_selected(self):
        row = self._selected_row()
        if row is None:
            return
        row_no = self.table.item(row, 0).data(Qt.UserRole)
        self._selected = next(
            (event for event in self._events if event.get("row_no") == row_no), None)
        if self._selected is not None:
            self.accept()

    def selected_event(self) -> dict | None:
        return self._selected
