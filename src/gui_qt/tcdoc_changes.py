"""腾讯文档本地同步检测记录的只读、非模态窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from shiboken6 import isValid

from gui_qt import table_columns, table_sort
from gui_qt.common import run_bg
from toolbox.table_sort_keys import sort_key, sort_records
from toolbox.tcdoc import table_preview

_COLUMNS = (("检测时间", "ts"), ("工作表", "tab_name"), ("变更", "kind"),
            ("记录", "record_id"), ("内容", "summary"))


class TcDocChangesDialog(QDialog):
    def __init__(self, doc: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("最近变更")
        self.setModal(False)
        self.setProperty("windowSurface", True)
        self.setMinimumSize(420, 300)
        self.resize(1040, 720)
        self._doc = dict(doc)
        self._rows = []
        self._request = 0
        self._closed = False
        self._loading = False
        self._positioned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        identity = QLabel(str(doc.get("name") or "腾讯文档"))
        identity.setTextFormat(Qt.PlainText)
        layout.addWidget(identity)
        description = QLabel(table_preview.CHANGES_DESCRIPTION)
        description.setWordWrap(True)
        description.setProperty("role", "caption")
        layout.addWidget(description)
        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setToolTip("重新读取当前文档的本地检测记录，不请求云端")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        layout.addLayout(bar)

        self.stack = QStackedWidget()
        self.empty = QLabel("还没有同步检测记录，请先同步表格数据，再点「刷新」")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("role", "muted")
        self.stack.addWidget(self.empty)
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels([name for name, _key in _COLUMNS])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.sorter = table_sort.install_callback(
            self.table, self._render, skip_columns=(), tri_state=True)
        table_columns.configure_table_columns(
            self.table, minimums={0: 145, 1: 90, 2: 50, 3: 75, 4: 140},
            stretch=(4,), auto_fit=True)
        self.stack.addWidget(self.table)
        layout.addWidget(self.stack, 1)
        footer = QDialogButtonBox(QDialogButtonBox.Close)
        footer.button(QDialogButtonBox.Close).setText("关闭")
        footer.rejected.connect(self.close)
        layout.addWidget(footer)

    def refresh(self):
        if self._loading:
            return
        self._closed = False
        self._loading = True
        self._request += 1
        request = self._request
        self.refresh_btn.setEnabled(False)
        self.status.setText("正在读取本地检测记录…")

        def done(rows, error):
            if not isValid(self) or self._closed or request != self._request:
                return
            self._loading = False
            self.refresh_btn.setEnabled(True)
            if error:
                self.status.setText("读取本地检测记录失败，请点「重试」重新读取")
                self.refresh_btn.setText("重试")
                return
            self.refresh_btn.setText("刷新")
            self._rows = rows
            self._render()
            self.status.setText(f"当前文档全部子表 · 最近 {len(rows)} 条 · 按检测顺序新到旧")

        run_bg(lambda: table_preview.recent_changes(self._doc.get("key", "")),
               done, owner=self)

    def _render(self, *_args):
        rows = self._rows
        if self.sorter.col >= 0:
            key = _COLUMNS[self.sorter.col][1]
            rows = sort_records(rows, lambda row: sort_key(row.get(key)),
                                ascending=self.sorter.ascending)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, (_name, key) in enumerate(_COLUMNS):
                value = str(row.get(key) or "")
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.stack.setCurrentWidget(self.table if rows else self.empty)

    def closeEvent(self, event):
        self._closed = True
        self._loading = False
        self._request += 1
        self._positioned = False
        super().closeEvent(event)

    def showEvent(self, event):
        if not self._positioned:
            self._positioned = True
            available = self.screen().availableGeometry()
            self.resize(min(self.width(), available.width() - 32),
                        min(self.height(), available.height() - 32))
            parent = self.parentWidget()
            center = parent.window().frameGeometry().center() if parent else available.center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
            frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
            self.move(frame.topLeft())
        super().showEvent(event)
