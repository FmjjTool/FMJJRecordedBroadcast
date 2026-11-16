"""当前工作目录的视频复选框与日期选择，不修改视频。"""
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QDateEdit, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from toolbox import bili_upload as BU


def video_item(record, work_dir, checked=False, blocked=()):
    path = Path(record["path"])
    try:
        name = str(path.relative_to(work_dir)) if work_dir else str(path)
    except ValueError:
        name = str(path)
    ready = record["renamed"] and BU.video_path_key(path) not in {BU.video_path_key(value) for value in blocked}
    suffix = "" if ready else "（需先完成重命名/版权处理）"
    item = QListWidgetItem(name + suffix)
    item.setData(Qt.UserRole, str(path))
    item.setToolTip(str(path))
    item.setCheckState(Qt.Checked if checked and ready else Qt.Unchecked)
    if not ready:
        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled)
    return item


def checked_paths(listing):
    return BU.unique_video_paths(listing.item(index).data(Qt.UserRole) for index in range(listing.count())
                                 if listing.item(index).checkState() == Qt.Checked)


class VideoSelectionDialog(QDialog):
    def __init__(self, parent, work_dir, records, selected, day=None, blocked=()):
        super().__init__(parent)
        self.setWindowTitle("选择工作目录视频")
        self.resize(800, 540)
        self.work_dir, self.records = work_dir, BU.unique_video_records(records)
        self.blocked = set(blocked)
        self._checked = {BU.video_path_key(path) for path in selected}
        self._visited = {day or BU.today_compact()}
        layout = QVBoxLayout(self)
        label = QLabel("当前工作目录：" + (work_dir or "未设置"))
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QHBoxLayout()
        row.addWidget(QLabel("视频日期"))
        self.date = QDateEdit(QDate.fromString(day or BU.today_compact(), "yyyyMMdd"))
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setCalendarPopup(True)
        row.addWidget(self.date)
        today = QPushButton("今天")
        today.clicked.connect(lambda: self.date.setDate(QDate.fromString(BU.today_compact(), "yyyyMMdd")))
        row.addWidget(today)
        self.all_dates = QCheckBox("显示全部日期")
        row.addWidget(self.all_dates)
        row.addStretch(1)
        select_all = QPushButton("全选当前列表")
        select_all.clicked.connect(lambda: self.set_checked(True))
        row.addWidget(select_all)
        clear = QPushButton("取消全选")
        clear.clicked.connect(lambda: self.set_checked(False))
        row.addWidget(clear)
        layout.addLayout(row)
        self.files = QListWidget()
        layout.addWidget(self.files, 1)
        self.count = QLabel()
        layout.addWidget(self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("使用勾选视频")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.date.dateChanged.connect(self.refresh)
        self.all_dates.toggled.connect(self.refresh)
        self.files.itemChanged.connect(self.remember)
        self.refresh()

    def visible_records(self):
        day = self.date.date().toString("yyyyMMdd")
        return [record for record in self.records if self.all_dates.isChecked() or record["date"] == day]

    def remember(self, *_):
        visible = {BU.video_path_key(self.files.item(index).data(Qt.UserRole)) for index in range(self.files.count())}
        self._checked.difference_update(visible)
        self._checked.update(BU.video_path_key(path) for path in checked_paths(self.files))
        self.count.setText(f"本列表 {self.files.count()} 个视频，已勾选 {len(checked_paths(self.files))} 个；未重命名的视频暂不可选")

    def refresh(self, *_):
        self.remember()
        day = self.date.date().toString("yyyyMMdd")
        records = self.visible_records()
        if not self.all_dates.isChecked() and day not in self._visited:
            blocked = {BU.video_path_key(path) for path in self.blocked}
            self._checked.update(BU.video_path_key(record["path"]) for record in records
                                 if record["renamed"] and BU.video_path_key(record["path"]) not in blocked)
            self._visited.add(day)
        self.files.blockSignals(True)
        self.files.clear()
        for record in records:
            self.files.addItem(video_item(record, self.work_dir, BU.video_path_key(record["path"]) in self._checked, self.blocked))
        self.files.blockSignals(False)
        self.remember()

    def set_checked(self, checked):
        for index in range(self.files.count()):
            item = self.files.item(index)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def selected_paths(self):
        return checked_paths(self.files)
