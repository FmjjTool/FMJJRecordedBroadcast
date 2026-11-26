"""封面批量补全窗口：只维护交互状态，文件扫描与生成统一调用应用服务。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Event

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gui_qt import table_columns, table_sort
from gui_qt.common import post_to_main, run_bg
from toolbox.cover_automation import generate_missing, scan_videos


_PAGE_SIZE = 200
_STATUS_TEXT = {
    "ready": "待生成", "exists": "已跳过", "invalid": "已跳过",
    "conflict": "已跳过", "generated": "已完成", "skipped": "已跳过",
    "failed": "失败", "cancelled": "已取消",
}


class CoverAutomationDialog(QDialog):
    """使用封面页当前样式，在视频旁补充缺失的同名封面。"""

    def __init__(self, template: dict, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("批量补封面")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setProperty("windowSurface", True)
        self.setMinimumSize(720, 480)
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        self.resize(min(1040, available.width() - 32) if available else 1040,
                    min(720, available.height() - 64) if available else 720)
        self._template = deepcopy(template)
        self._root: Path | None = None
        self._rows: list[dict] = []
        self._selected: set[str] = set()
        self._page = 0
        self._busy = ""
        self._cancel = Event()
        self._fill_position = 0
        self._page_rows: list[dict] = []
        self._centered = False
        self._fill_timer = QTimer(self)
        self._fill_timer.setInterval(0)
        self._fill_timer.timeout.connect(self._fill_next_rows)
        self._build_ui()
        self._refresh_controls()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        controls = QHBoxLayout(bar)
        controls.setContentsMargins(12, 10, 12, 10)
        controls.setSpacing(8)
        controls.addWidget(QLabel("视频目录", bar))
        self.directory_edit = QLineEdit(bar)
        self.directory_edit.setReadOnly(True)
        self.directory_edit.setPlaceholderText("先选择需要补封面的视频目录")
        self.directory_edit.setMinimumWidth(80)
        controls.addWidget(self.directory_edit, 1)
        self.browse_button = QPushButton("浏览", bar)
        self.browse_button.setProperty("variant", "secondary")
        self.browse_button.clicked.connect(self._choose_directory)
        controls.addWidget(self.browse_button)
        root.addWidget(bar)

        options = QHBoxLayout()
        options.setSpacing(8)
        self.recursive_checkbox = QCheckBox("包含子目录", self)
        self.recursive_checkbox.setChecked(True)
        self.recursive_checkbox.toggled.connect(self._invalidate_scan)
        options.addWidget(self.recursive_checkbox)
        self.template_label = QLabel(self)
        self.template_label.setProperty("role", "caption")
        bg = Path(str(self._template.get("bg") or ""))
        self.template_label.setText("使用当前样式与底图" if str(self._template.get("bg") or "")
                                    else "尚未选择底图")
        self.template_label.setToolTip(
            f"底图：{bg.name or '未选择'}\n标题和日期从文件名识别，备注沿用当前内容；已有封面跳过")
        options.addWidget(self.template_label, 1)
        self.scan_button = QPushButton("扫描", self)
        self.scan_button.setProperty("variant", "secondary")
        self.scan_button.clicked.connect(self._scan)
        options.addWidget(self.scan_button)
        root.addLayout(options)

        card = QFrame(self)
        card.setProperty("variant", "card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 12)
        cv.setSpacing(8)
        self.empty_label = QLabel("尚未扫描，选择视频目录后点「扫描」", card)
        self.empty_label.setProperty("role", "caption")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        cv.addWidget(self.empty_label, 1)
        self.table = QTableWidget(0, 6, card)
        self.table.setHorizontalHeaderLabels(["", "视频文件", "标题", "日期", "状态", "原因"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 36)
        for col, width in enumerate((36, 230, 200, 110, 84, 200)):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.hide()
        self._sorter = table_sort.install_callback(self.table, self._on_sort, skip_columns=(0,))
        cv.addWidget(self.table, 1)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.select_checkbox = QCheckBox("全选待生成", card)
        self.select_checkbox.toggled.connect(self._select_ready)
        footer.addWidget(self.select_checkbox)
        footer.addStretch(1)
        self.previous_button = QPushButton("上一页", card)
        self.previous_button.clicked.connect(lambda: self._change_page(-1))
        footer.addWidget(self.previous_button)
        self.page_label = QLabel("第 0 / 0 页", card)
        self.page_label.setProperty("role", "caption")
        footer.addWidget(self.page_label)
        self.next_button = QPushButton("下一页", card)
        self.next_button.clicked.connect(lambda: self._change_page(1))
        footer.addWidget(self.next_button)
        cv.addLayout(footer)
        table_columns.install()
        root.addWidget(card, 1)

        self.status_label = QLabel("标题与日期识别后可先复核，再生成缺失封面", self)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        root.addWidget(self.progress)
        self.buttons = QDialogButtonBox(self)
        self.close_button = self.buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        self.cancel_button = self.buttons.addButton("停止", QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.start_button = self.buttons.addButton("生成封面", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setProperty("variant", "primary")
        self.start_button.clicked.connect(self._start)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered:
            self._centered = True
            parent = self.parentWidget()
            if parent is not None:
                frame = self.frameGeometry()
                frame.moveCenter(parent.window().frameGeometry().center())
                screen = self.screen() or QApplication.primaryScreen()
                available = screen.availableGeometry() if screen else None
                point = frame.topLeft()
                if available is not None:
                    point.setX(max(available.left(), min(point.x(), available.right() - frame.width())))
                    point.setY(max(available.top(), min(point.y(), available.bottom() - frame.height())))
                self.move(point)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.update()

    def _choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择视频目录", str(self._root or ""))
        if path:
            self._root = Path(path)
            self.directory_edit.setText(path)
            self.directory_edit.setCursorPosition(len(path))
            self.directory_edit.setToolTip(path)
            self._invalidate_scan()

    def _invalidate_scan(self, *_args):
        self._fill_timer.stop()
        self._rows = []
        self._selected.clear()
        self._page = 0
        self.table.setRowCount(0)
        self.table.hide()
        self.empty_label.setText("目录或范围已改变，点「扫描」识别视频标题与日期")
        self.empty_label.show()
        self.status_label.setText("尚未扫描，已有封面将自动跳过")
        self.progress.hide()
        self._refresh_controls()

    def _scan(self):
        if self._busy or self._root is None:
            return
        root, recursive = self._root, self.recursive_checkbox.isChecked()
        self._busy = "scan"
        self._fill_timer.stop()
        self.table.hide()
        self.empty_label.setText("扫描中，正在识别视频标题、日期和已有封面")
        self.empty_label.show()
        self.status_label.setText("扫描中")
        self.progress.show()
        self.progress.setRange(0, 0)
        self._refresh_controls()
        run_bg(lambda: scan_videos(root, recursive=recursive), self._scan_done,
               owner=self.parentWidget())

    def _scan_done(self, rows, error):
        self._busy = ""
        self.progress.hide()
        if error:
            self.empty_label.setText("扫描失败，请检查目录是否可访问后点「扫描」重试")
            self.status_label.setText(self._error_message("扫描失败", error))
            self._rows = []
            self._selected.clear()
            self._refresh_controls()
            return
        self._rows = [dict(row) for row in rows or []]
        self._selected = {row["video"] for row in self._rows if row.get("status") == "ready"}
        self._page = 0
        self.status_label.setText("扫描已完成，勾选待生成条目后点「生成封面」")
        self._sorter.col = -1
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self._render_page()

    def _render_page(self):
        self._fill_timer.stop()
        self.table.blockSignals(True)
        start = self._page * _PAGE_SIZE
        self._page_rows = self._rows[start:start + _PAGE_SIZE]
        self._fill_position = 0
        self.table.setRowCount(len(self._page_rows))
        self.table.blockSignals(False)
        has_rows = bool(self._rows)
        self.empty_label.setVisible(not has_rows)
        if not has_rows:
            self.empty_label.setText("没有找到视频，请选择其他目录或勾选「包含子目录」后重新扫描")
        self.table.setVisible(has_rows)
        if self._page_rows:
            self._fill_timer.start()
        self._refresh_controls()

    def _fill_next_rows(self):
        self.table.blockSignals(True)
        end = min(self._fill_position + 20, len(self._page_rows))
        for row_index in range(self._fill_position, end):
            row = self._page_rows[row_index]
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, row["video"])
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if row.get("status") == "ready":
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                check.setCheckState(Qt.CheckState.Checked if row["video"] in self._selected
                                    else Qt.CheckState.Unchecked)
            check.setFlags(flags)
            self.table.setItem(row_index, 0, check)
            values = (row.get("video"), row.get("title"), row.get("date"),
                      _STATUS_TEXT.get(row.get("status"), "已跳过"), row.get("reason"))
            for col, value in enumerate(values, 1):
                text = str(value or "—")
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 1 and row.get("cover"):
                    item.setToolTip(f"视频：{text}\n封面：{row['cover']}")
                self.table.setItem(row_index, col, item)
        self._fill_position = end
        self.table.blockSignals(False)
        if end >= len(self._page_rows):
            self._fill_timer.stop()
            table_columns.fit_table_columns(self.table)
        self._refresh_controls()

    def _on_sort(self, col, ascending):
        keys = {1: "video", 2: "title", 3: "date", 4: "status", 5: "reason"}
        key = keys.get(col)
        if key is None or self._busy:
            return
        self._rows = table_sort.sort_records(
            self._rows, lambda row: table_sort.sort_key(
                _STATUS_TEXT.get(row.get(key), row.get(key)) if key == "status" else row.get(key)),
            ascending=ascending)
        self._page = 0
        self._render_page()

    def _on_item_changed(self, item):
        if item.column() != 0 or self._busy:
            return
        video = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(video)
        else:
            self._selected.discard(video)
        self._refresh_controls()

    def _select_ready(self, checked):
        if self._busy:
            return
        self._selected = ({row["video"] for row in self._rows if row.get("status") == "ready"}
                          if checked else set())
        self._render_page()

    def _change_page(self, delta):
        if self._busy or self._fill_timer.isActive():
            return
        self._page += delta
        self._render_page()

    def _refresh_controls(self):
        busy = bool(self._busy)
        filling = self._fill_timer.isActive()
        pages = (len(self._rows) + _PAGE_SIZE - 1) // _PAGE_SIZE
        self.page_label.setText(f"第 {self._page + 1 if pages else 0} / {pages} 页 · 共 {len(self._rows):,} 条")
        self.browse_button.setEnabled(not busy)
        self.recursive_checkbox.setEnabled(not busy)
        self.scan_button.setEnabled(not busy and self._root is not None)
        self.table.setEnabled(not busy and not filling)
        self.previous_button.setEnabled(not busy and not filling and self._page > 0)
        self.next_button.setEnabled(not busy and not filling and self._page + 1 < pages)
        ready = {row["video"] for row in self._rows if row.get("status") == "ready"}
        self.select_checkbox.blockSignals(True)
        self.select_checkbox.setChecked(bool(ready) and ready <= self._selected)
        self.select_checkbox.blockSignals(False)
        self.select_checkbox.setEnabled(not busy and bool(ready))
        self.start_button.setText(f"生成封面 {len(self._selected)}" if self._selected else "生成封面")
        self.start_button.setEnabled(not busy and not filling and bool(self._selected)
                                     and bool(self._template.get("bg")))
        self.start_button.setToolTip("按当前样式补生成勾选视频的同名 JPG 封面"
                                     if self._template.get("bg") else "请先在封面制作页选择底图，再重新打开此窗口")
        self.cancel_button.setVisible(self._busy == "generate")
        self.cancel_button.setEnabled(not self._cancel.is_set())
        self.close_button.setToolTip("关闭窗口后任务继续在后台执行" if busy else "关闭窗口")

    def _start(self):
        if self._busy or self._root is None:
            return
        rows = [dict(row) for row in self._rows
                if row.get("status") == "ready" and row["video"] in self._selected]
        if not rows:
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("生成封面")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(f"为「{self._root.name}」中勾选的 {len(rows)} 条视频生成封面？")
        confirm.setInformativeText("封面按识别到的标题和日期套用当前样式，保存到视频旁；已有封面跳过。")
        cancel = confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        go = confirm.addButton("生成封面", QMessageBox.ButtonRole.AcceptRole)
        confirm.setDefaultButton(cancel)
        confirm.setEscapeButton(cancel)
        confirm.exec()
        if confirm.clickedButton() is not go:
            return
        self._cancel.clear()
        self._busy = "generate"
        self.progress.setRange(0, len(rows))
        self.progress.setValue(0)
        self.progress.show()
        self.status_label.setText(f"生成中 · 0 / {len(rows)} 条")
        self._refresh_controls()
        root, template = self._root, deepcopy(self._template)
        def progress(done, total, message):
            post_to_main(self._generation_progress, done, total, message)
        run_bg(lambda: generate_missing(root, rows, template,
                                       is_cancelled=self._cancel.is_set,
                                       on_progress=progress), self._generation_done,
               owner=self.parentWidget())

    def _generation_progress(self, done, total, message):
        self.progress.setRange(0, max(1, int(total)))
        self.progress.setValue(int(done))
        self.status_label.setText(f"生成中 · {done} / {total} 条 · {message}")

    def _generation_done(self, result, error):
        self._busy = ""
        if error:
            self.status_label.setText(self._error_message("生成失败", error))
            self._refresh_controls()
            return
        result = result or {}
        updates = {item.get("video"): item for item in result.get("items", [])}
        for row in self._rows:
            item = updates.get(row["video"])
            if item is None:
                continue
            row.update({key: item[key] for key in ("status", "reason", "cover") if key in item})
            if row.get("status") != "ready":
                self._selected.discard(row["video"])
        label = "已取消" if result.get("cancelled") else "已完成"
        self.status_label.setText(
            f"{label} · 生成 {result.get('generated', 0)} 张 · "
            f"跳过 {result.get('skipped', 0)} 条 · 失败 {result.get('failed', 0)} 条；"
            "封面已保存到各视频旁，可重新扫描复核")
        self._render_page()

    def _request_cancel(self):
        self._cancel.set()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("停止中，将保留已生成的封面")

    def _error_message(self, action, error):
        text = str(error)
        for path in (self._root, self._template.get("bg")):
            if path:
                text = text.replace(str(path), "所选文件")
        return f"{action}：{text[:220]}；请检查文件后重新扫描"

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
            self.hide()
            return
        self._fill_timer.stop()
        super().closeEvent(event)
