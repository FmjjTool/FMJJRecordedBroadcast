"""从腾讯文档「完整歌曲」本地快照选歌的只读对话框。

原属「音乐信息获取」页；两页合并后由音乐搜索下载页共用。
"""

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gui_qt import table_sort


class CompleteSongDialog(QDialog):
    """从本地腾讯文档「完整歌曲」快照选一首，只读匹配。"""

    def __init__(self, songs, parent=None):
        super().__init__(parent)
        self.songs = list(songs)
        self.setWindowTitle("从完整歌曲选择")
        self.resize(880, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索歌曲名 / 演唱者 / 专辑 / 日期")
        self.search_edit.textChanged.connect(self._filter)
        layout.addWidget(self.search_edit)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["歌曲名称", "演唱者", "首发日期", "时长", "所属专辑"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        # 行号 == self.songs 下标：排序时同步重排歌曲列表，_current_song 才能取对
        self._sorter = table_sort.install(self.table, rows=self.songs, skip_columns=())
        self.table.setRowCount(len(self.songs))
        for row, song in enumerate(self.songs):
            values = (song.get("title"), song.get("singer"), song.get("date"),
                      song.get("duration"), song.get("album"))
            for column, value in enumerate(values):
                text = str(value or "")
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
        self._sorter.attach(self.songs).apply()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in ((1, 120), (2, 100), (3, 70), (4, 220)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.table.setColumnWidth(column, width)
        header.sectionClicked.connect(
            lambda _c: self._filter(self.search_edit.text()))
        self.table.cellDoubleClicked.connect(lambda row, _col: self._accept_row(row))
        self.table.itemSelectionChanged.connect(self._sync_ok)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel(f"共 {len(self.songs)} 首")
        self.count_label.setProperty("role", "caption")
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.ok_btn = QPushButton("使用选中歌曲")
        self.ok_btn.setProperty("variant", "primary")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._accept_selected)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.search_edit.setFocus()

    def _filter(self, text):
        keyword = text.strip().lower()
        for row, song in enumerate(self.songs):
            haystack = " ".join(str(song.get(key) or "") for key in
                                ("title", "singer", "album", "date")).lower()
            self.table.setRowHidden(row, bool(keyword) and keyword not in haystack)
        self._sync_ok()

    def _current_song(self):
        rows = self.table.selectionModel().selectedRows()
        row = rows[0].row() if rows else self.table.currentRow()
        if row < 0 or self.table.isRowHidden(row):
            return None
        return self.songs[row]

    def _sync_ok(self):
        self.ok_btn.setEnabled(self._current_song() is not None)

    def _accept_row(self, row):
        if row < 0 or self.table.isRowHidden(row):
            return
        self.table.selectRow(row)
        self.accept()

    def _accept_selected(self):
        if self._current_song() is not None:
            self.accept()

    def selected_song(self):
        return self._current_song()
