"""腾讯文档页 · 字段映射回写面板（官方 OpenAPI）。

把本地 CSV / Excel / JSON 的列映射到当前腾讯文档的智能表字段，
按匹配列预览差异，只新增 / 更新明确勾选的行；不会删除记录。
官方 OpenAPI 的三个 ID 由统一管理入口维护，本面板只读取，不保存凭据。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt import table_columns, table_sort
from gui_qt.common import post_to_main, run_bg
from toolbox.tcdoc import mapping_workflow as workflow, openapi

FIELD_TYPES = openapi.FIELD_TYPES
SUPPORTED_TYPES = openapi.SUPPORTED_TYPES

_MISSING_CREDENTIALS = "需要登录，无法连接文档；请登录后重试"


class TcDocMappingPanel(QWidget):
    def __init__(self, page, get_doc: Callable[[], dict | None]):
        super().__init__(page)
        self.page = page
        self.get_doc = get_doc
        self.doc: dict | None = None
        self.client: openapi.TdClient | None = None
        self.file_id = ""
        self._cred_snapshot: tuple[str, str, str] | None = None
        self.sheets: list[dict] = []
        self.fields: list[dict] = []
        self.field_by_id: dict[str, dict] = {}
        self.sample_records: list[dict] = []
        self.remote_records: list[dict] = []
        self.remote_total = 0
        self.local_headers: list[str] = []
        self.local_rows: list[dict] = []
        self.local_path = ""
        self.mapping: dict[str, str] = {}
        self.actions: list[dict] = []
        self._selected_rows: set[int] = set()
        self._local_page = self._diff_page = 1
        self._page_size = 200
        self._revision = 0
        self._cancel = threading.Event()
        self._busy = False
        self._closed = False
        self._buttons: list[QPushButton] = []
        self._controls: list[QWidget] = []
        self._build_ui()
        self._update_doc_label()

    # ==================== 界面 ====================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        toolbar = QFrame()
        toolbar.setProperty("variant", "toolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(10, 8, 10, 8)
        bar.setSpacing(8)

        self.doc_label = QLabel("当前文档：未选择")
        self.doc_label.setProperty("role", "caption")
        bar.addWidget(self.doc_label, 1)
        self.connect_btn = QPushButton("连接当前文档")
        self.connect_btn.setToolTip("读取当前文档，只列出官方动态与直播记录")
        self.connect_btn.clicked.connect(self.on_connect)
        bar.addWidget(self.connect_btn)
        self.refresh_btn = QPushButton("刷新字段")
        self.refresh_btn.setToolTip("重新读取字段与腾讯表格预览")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self.load_fields)
        bar.addWidget(self.refresh_btn)
        self.conn_status = QLabel("")
        self.conn_status.setProperty("role", "caption")
        bar.addWidget(self.conn_status)
        layout.addWidget(toolbar)
        target_bar = QHBoxLayout()
        target_bar.addWidget(QLabel("目标子表"))
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(140)
        self.sheet_combo.setMinimumContentsLength(10)
        self.sheet_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sheet_combo.currentIndexChanged.connect(self.on_sheet_changed)
        target_bar.addWidget(self.sheet_combo, 1)
        target_bar.addWidget(QLabel("匹配列"))
        self.key_combo = QComboBox()
        self.key_combo.setMinimumWidth(140)
        self.key_combo.setMinimumContentsLength(10)
        self.key_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.key_combo.setToolTip("用于判断同一条记录，优先选择唯一链接或编号；重复值会提示核对")
        self.key_combo.currentIndexChanged.connect(
            lambda *_: self._invalidate_preview("匹配列已修改，请重新对比云端"))
        target_bar.addWidget(self.key_combo, 1)
        self.mapping_btn = QPushButton("字段匹配…")
        self.mapping_btn.clicked.connect(self._show_mapping)
        target_bar.addWidget(self.mapping_btn)
        self.preview_btn = QPushButton("对比云端")
        self.preview_btn.setProperty("variant", "primary")
        self.preview_btn.clicked.connect(self.on_preview)
        target_bar.addWidget(self.preview_btn)
        layout.addLayout(target_bar)

        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedHeight(6)
        self.busy_bar.setVisible(False)
        layout.addWidget(self.busy_bar)
        self.cancel_btn = QPushButton("取消任务")
        self.cancel_btn.clicked.connect(self._cancel.set)
        self.cancel_btn.setVisible(False)
        layout.addWidget(self.cancel_btn, alignment=Qt.AlignRight)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left_panel())
        split.addWidget(self._build_right_panel())
        split.setChildrenCollapsible(False)
        split.setSizes([480, 700])
        layout.addWidget(split, 1)

        self._buttons.extend([
            self.connect_btn, self.refresh_btn, self.pick_btn, self.auto_btn,
            self.clear_map_btn, self.load_map_btn, self.save_map_btn,
            self.remote_refresh_btn, self.preview_btn, self.execute_btn,
            self.mapping_btn, self.local_prev_btn, self.local_next_btn,
            self.diff_prev_btn, self.diff_next_btn, self.clear_selection_btn,
        ])
        self._controls.extend([self.sheet_combo, self.key_combo, self.remote_limit_combo,
                               self.diff_filter])

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        local_box = QGroupBox("本地表格")
        local_layout = QVBoxLayout(local_box)
        row = QHBoxLayout()
        self.local_label = QLabel("尚未导入，选择 CSV / Excel / JSON 文件")
        self.local_label.setWordWrap(True)
        self.local_label.setProperty("role", "caption")
        self.pick_btn = QPushButton("导入文件")
        self.pick_btn.clicked.connect(self._pick_file)
        row.addWidget(self.local_label, 1)
        row.addWidget(self.pick_btn)
        local_layout.addLayout(row)
        self.local_table = self._make_table([])
        self._local_sorter = table_sort.install_callback(
            self.local_table, self._sort_local, skip_columns=(), tri_state=True)
        local_layout.addWidget(self.local_table, 1)
        local_footer = QHBoxLayout()
        self.local_page_label = QLabel("第 1 / 1 页 · 共 0 条")
        self.local_prev_btn = QPushButton("上一页")
        self.local_next_btn = QPushButton("下一页")
        self.local_prev_btn.clicked.connect(lambda: self._move_local(-1))
        self.local_next_btn.clicked.connect(lambda: self._move_local(1))
        local_footer.addWidget(self.local_page_label, 1)
        local_footer.addWidget(self.local_prev_btn)
        local_footer.addWidget(self.local_next_btn)
        local_layout.addLayout(local_footer)
        layout.addWidget(local_box, 1)

        self.mapping_dialog = QDialog(self)
        self.mapping_dialog.setWindowTitle("字段匹配")
        self.mapping_dialog.resize(900, 640)
        self.mapping_dialog.setMinimumSize(420, 300)
        self.mapping_dialog.setModal(False)
        dialog_layout = QVBoxLayout(self.mapping_dialog)
        mapping_box = QGroupBox("本地列 → 云端字段")
        mapping_layout = QVBoxLayout(mapping_box)
        self.map_hint = QLabel(
            "选择本地 CSV / Excel / JSON 后，这里会按列生成本地 → 腾讯字段的映射行。")
        self.map_hint.setProperty("role", "caption")
        self.map_hint.setWordWrap(True)
        mapping_layout.addWidget(self.map_hint)
        self.map_table = self._make_table(["本地列", "腾讯字段", "类型", "示例值"])
        self.map_table.setMinimumHeight(220)
        self._map_sorter = table_sort.install_callback(
            self.map_table, self._sort_mapping, skip_columns=(1,), tri_state=True)
        mapping_layout.addWidget(self.map_table, 1)
        buttons = QHBoxLayout()
        self.auto_btn = QPushButton("自动同名匹配")
        self.auto_btn.clicked.connect(self.auto_match)
        self.clear_map_btn = QPushButton("清空映射")
        self.clear_map_btn.clicked.connect(self.clear_mapping)
        self.load_map_btn = QPushButton("载入映射")
        self.load_map_btn.clicked.connect(self.load_mapping_file)
        self.save_map_btn = QPushButton("保存映射")
        self.save_map_btn.clicked.connect(self.save_mapping_file)
        for btn in (self.auto_btn, self.clear_map_btn,
                    self.load_map_btn, self.save_map_btn):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        mapping_layout.addLayout(buttons)

        dialog_layout.addWidget(mapping_box, 1)
        close_box = QDialogButtonBox()
        close_box.addButton("关闭", QDialogButtonBox.RejectRole)
        close_box.rejected.connect(self.mapping_dialog.hide)
        dialog_layout.addWidget(close_box)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        content_tabs = QTabWidget()

        remote_page = QWidget()
        remote_layout = QVBoxLayout(remote_page)
        remote_layout.setContentsMargins(6, 6, 6, 6)
        remote_layout.setSpacing(6)
        hint = QLabel("云端记录只读；导入本地文件后点击「对比云端」识别差异。")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        remote_layout.addWidget(hint)
        remote_bar = QHBoxLayout()
        self.remote_info = QLabel("连接文档后显示腾讯表格记录")
        self.remote_info.setProperty("role", "caption")
        remote_bar.addWidget(self.remote_info, 1)
        remote_bar.addWidget(QLabel("显示"))
        self.remote_limit_combo = QComboBox()
        for label, value in (("20 条", 20), ("50 条", 50),
                             ("100 条", 100), ("200 条", 200)):
            self.remote_limit_combo.addItem(label, value)
        self.remote_limit_combo.setCurrentIndex(1)
        self.remote_limit_combo.currentIndexChanged.connect(
            lambda *_: self.reload_remote())
        remote_bar.addWidget(self.remote_limit_combo)
        self.remote_refresh_btn = QPushButton("刷新预览")
        self.remote_refresh_btn.clicked.connect(self.reload_remote)
        remote_bar.addWidget(self.remote_refresh_btn)
        remote_layout.addLayout(remote_bar)
        self.remote_table = self._make_table([])
        self._remote_sorter = table_sort.install_callback(
            self.remote_table, self._sort_remote, skip_columns=(), tri_state=True)
        remote_layout.addWidget(self.remote_table, 1)

        diff_page = QWidget()
        diff_layout = QVBoxLayout(diff_page)
        diff_layout.setContentsMargins(6, 6, 6, 6)
        diff_layout.setSpacing(6)
        self.summary_label = QLabel("尚未对比，导入文件后点击「对比云端」")
        self.summary_label.setWordWrap(True)
        font = self.summary_label.font()
        font.setBold(True)
        self.summary_label.setFont(font)
        diff_layout.addWidget(self.summary_label)
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("显示"))
        self.diff_filter = QComboBox()
        for text, value in (("差异行", "diff"), ("仅新增", "新增"),
                            ("仅更新", "更新"), ("错误行", "错误"),
                            ("全部", "all")):
            self.diff_filter.addItem(text, value)
        self.diff_filter.setMinimumWidth(100)
        self.diff_filter.currentIndexChanged.connect(self._filter_diff)
        filter_bar.addWidget(self.diff_filter)
        self.selection_label = QLabel("已勾选 0 条")
        filter_bar.addWidget(self.selection_label, 1)
        self.clear_selection_btn = QPushButton("取消勾选")
        self.clear_selection_btn.clicked.connect(self._clear_selection)
        filter_bar.addWidget(self.clear_selection_btn)
        diff_layout.addLayout(filter_bar)
        self.diff_table = self._make_table(["", "行号", "操作", "匹配值", "说明"])
        self._diff_sorter = table_sort.install_callback(
            self.diff_table, self._sort_diff, skip_columns=(0,), tri_state=True)
        self.diff_table.itemChanged.connect(self._on_diff_checked)
        self.diff_table.itemSelectionChanged.connect(self._show_diff_detail)
        diff_layout.addWidget(self.diff_table, 3)
        pagination = QHBoxLayout()
        self.diff_page_label = QLabel("第 1 / 1 页 · 共 0 条")
        self.diff_prev_btn = QPushButton("上一页")
        self.diff_next_btn = QPushButton("下一页")
        self.diff_prev_btn.clicked.connect(lambda: self._move_diff(-1))
        self.diff_next_btn.clicked.connect(lambda: self._move_diff(1))
        pagination.addWidget(self.diff_page_label, 1)
        pagination.addWidget(self.diff_prev_btn)
        pagination.addWidget(self.diff_next_btn)
        diff_layout.addLayout(pagination)
        self.detail_label = QLabel("点一行查看本地值与云端值")
        self.detail_label.setProperty("role", "caption")
        self.detail_label.setWordWrap(True)
        diff_layout.addWidget(self.detail_label)
        self.detail_table = self._make_table(["字段", "本地值", "云端值"])
        self._detail_sorter = table_sort.install_callback(
            self.detail_table, self._sort_detail, skip_columns=(), tri_state=True)
        diff_layout.addWidget(self.detail_table, 2)
        row = QHBoxLayout()
        self.execute_btn = QPushButton("写入勾选 0 条")
        self.execute_btn.setProperty("variant", "primary")
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self.on_execute)
        row.addStretch(1)
        row.addWidget(self.execute_btn)
        diff_layout.addLayout(row)

        content_tabs.setObjectName("mappingContentTabs")
        content_tabs.setStyleSheet(
            "QTabWidget#mappingContentTabs::pane { border: 0px; }"
            "QTabWidget#mappingContentTabs QTabBar::tab { background: transparent;"
            "border: 0px; border-bottom: 2px solid transparent; padding: 4px 10px; }"
            "QTabWidget#mappingContentTabs QTabBar::tab:selected {"
            "border-bottom-color: palette(highlight); color: palette(link); }")
        content_tabs.addTab(diff_page, "差异记录")
        content_tabs.addTab(remote_page, "只读表格")
        self.content_tabs = content_tabs

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        log_layout.addWidget(self.log_view)
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_view.clear)
        log_layout.addWidget(clear_btn, alignment=Qt.AlignRight)

        content_tabs.addTab(log_page, "任务日志")
        layout.addWidget(content_tabs, 1)
        return panel

    def _make_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setToolTip("点击表头排序（升序 → 降序 → 默认）")
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        table_columns.configure_table_columns(table, auto_fit=True)
        return table

    def _show_mapping(self) -> None:
        self.mapping_dialog.show()
        self.mapping_dialog.raise_()
        self.mapping_dialog.activateWindow()

    def _invalidate_preview(self, message: str) -> None:
        self._revision += 1
        self.actions = []
        self._selected_rows.clear()
        self._diff_page = 1
        self.summary_label.setText(message)
        self._render_diff()

    def _update_selection(self) -> None:
        count = len(self._writable_actions())
        self.selection_label.setText(f"已勾选 {count} 条")
        self.execute_btn.setText(f"写入勾选 {count} 条")
        self.execute_btn.setEnabled(bool(count) and not self._busy)

    def _on_diff_checked(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        row_no = item.data(Qt.UserRole)
        if row_no is None:
            return
        if item.checkState() == Qt.Checked:
            self._selected_rows.add(int(row_no))
        else:
            self._selected_rows.discard(int(row_no))
        self._update_selection()

    def _clear_selection(self) -> None:
        self._selected_rows.clear()
        self._render_diff()

    @staticmethod
    def _ordered(records, sorter, getter):
        if sorter.col < 0:
            return list(records)
        from toolbox.table_sort_keys import sort_key, sort_records
        return sort_records(records, lambda item: sort_key(getter(item, sorter.col)),
                            ascending=sorter.ascending)

    def _sort_local(self, *_args) -> None:
        self._local_page = 1
        self._render_local_table()

    def _move_local(self, offset: int) -> None:
        self._local_page += offset
        self._render_local_table()

    def _sort_diff(self, *_args) -> None:
        self._diff_page = 1
        self._render_diff()

    def _filter_diff(self, *_args) -> None:
        self._diff_page = 1
        self._render_diff()

    def _move_diff(self, offset: int) -> None:
        self._diff_page += offset
        self._render_diff()

    def _sort_mapping(self, *_args) -> None:
        self._rebuild_mapping_table()

    def _sort_remote(self, *_args) -> None:
        self._render_remote()

    def _sort_detail(self, *_args) -> None:
        self._show_diff_detail()

    def _show_diff_detail(self) -> None:
        item = self.diff_table.item(self.diff_table.currentRow(), 0)
        row_no = item.data(Qt.UserRole) if item else None
        action = next((a for a in self.actions if a.get("row_no") == row_no), None)
        self.detail_table.setRowCount(0)
        if action is None:
            self.detail_label.setText("点一行查看本地值与云端值")
            return
        self.detail_label.setText(
            f"本地第 {row_no} 行 · {action.get('op', '')} · {action.get('message', '')}")
        changes = self._ordered(action.get("changes") or [], self._detail_sorter,
                                lambda change, col: change.get(
                                    ("field", "local", "remote")[col], ""))
        self.detail_table.setRowCount(len(changes))
        for row, change in enumerate(changes):
            for col, key in enumerate(("field", "local", "remote")):
                full = str(change.get(key) or "")
                cell = QTableWidgetItem(full.replace("\n", " ")[:300])
                cell.setToolTip(full)
                self.detail_table.setItem(row, col, cell)
        table_columns.fit_table_columns(self.detail_table)

    # ==================== 文档 / 任务状态 ====================

    def set_doc(self, doc: dict | None) -> None:
        if doc and self.doc and doc.get("key") == self.doc.get("key"):
            self.doc = doc
            self._update_doc_label()
            return
        self.doc = doc
        self.client = None
        self.file_id = ""
        self._cred_snapshot = None
        self.sheets = []
        self.fields = []
        self.field_by_id = {}
        self.sample_records = []
        self.remote_records = []
        self.remote_total = 0
        self.mapping = {}
        self._invalidate_preview("文档已切换，请连接后重新对比")
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.blockSignals(False)
        self._rebuild_mapping_table()
        self._render_remote()
        self.execute_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.conn_status.setText("")
        self._update_doc_label()

    def refresh(self) -> None:
        self._update_doc_label()

    def _update_doc_label(self) -> None:
        doc = self.doc
        if not doc:
            self.doc_label.setText("当前文档：未选择（先在页面顶部选择文档）")
            self.connect_btn.setEnabled(False)
            return
        self.connect_btn.setEnabled(not self._busy)
        self.doc_label.setText(
            f"当前文档：{doc.get('name') or doc.get('key')}（{doc.get('kind') or '?'}）")

    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.busy_bar.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.mapping_dialog.setEnabled(not busy)
        self.diff_table.setEnabled(not busy)
        for button in self._buttons:
            button.setEnabled(not busy)
        for widget in self._controls:
            widget.setEnabled(not busy)
        if not busy:
            self.connect_btn.setEnabled(self.doc is not None)
            self.refresh_btn.setEnabled(bool(self.client and self.file_id))
            self._update_selection()
            local_pages = max(1, (len(self.local_rows) + self._page_size - 1) // self._page_size)
            self.local_prev_btn.setEnabled(self._local_page > 1)
            self.local_next_btn.setEnabled(self._local_page < local_pages)
            self._render_diff()

    def _run(self, fn, on_done, label: str, action: str, receipt_callback=None) -> None:
        if self._busy or self._closed:
            return
        self._cancel.clear()
        self._set_busy(True)
        self.log(label)

        def checked_work():
            if self._cancel.is_set():
                raise workflow.MappingError("任务已取消")
            return fn()

        def done(result, err):
            if self._closed:
                return
            self._set_busy(False)
            if self._cancel.is_set() and action != "写入":
                self.log("任务已取消")
                self.summary_label.setText("已取消，可重新对比云端")
                return
            if err:
                self.log(f"[错误] {err}")
                receipt = getattr(err, "receipt", None)
                if receipt and receipt_callback:
                    receipt_callback(receipt)
                if action == "写入":
                    self._invalidate_preview("本次写入已停止，请重新对比云端后核对结果")
                QMessageBox.warning(
                    self, "任务已停止",
                    f"{action}未完成：{err}\n\n请核对当前状态后重试。")
                return
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001 - 失败后保留重试入口
                self.log(f"[错误] {exc}")
                QMessageBox.warning(
                    self, "操作失败",
                    f"{action}失败：{exc}\n\n请检查网络与登录状态后重试。")

        thread = run_bg(checked_work, done, owner=self.page)
        if thread is None:
            self._set_busy(False)
            self.log("任务未启动，请重试")

    def shutdown(self) -> None:
        self._closed = True
        self._cancel.set()
        self.mapping_dialog.hide()

    # ==================== 连接 / 字段 ====================

    def _credentials(self) -> tuple[str, str, str]:
        import auth as auth_module
        raw = auth_module.get_cookie(openapi.CRED_KEY)
        if not raw:
            raise RuntimeError(_MISSING_CREDENTIALS)
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            raise RuntimeError("当前登录不可用，请登录后重试") from None
        values = tuple(str(data.get(key) or "").strip()
                       for key in ("client_id", "access_token", "open_id"))
        if not all(values):
            raise RuntimeError("当前登录不完整，请登录后重试")
        return values  # type: ignore[return-value]

    def on_connect(self) -> None:
        doc = self.get_doc()
        if not doc:
            QMessageBox.warning(self, "没有文档", "请先在页面顶部选择腾讯文档")
            return
        self.set_doc(doc)
        self._invalidate_preview("正在连接，连接后请对比云端")
        if doc.get("key") != openapi.WRITABLE_DOC_KEY:
            QMessageBox.warning(self, "禁止回写此文档",
                                "映射回写仅允许封茗囧菌数据汇总库的官方动态与直播记录")
            return
        kind = (doc.get("kind") or "").lower()
        if kind not in ("sheet", "smartsheet"):
            QMessageBox.warning(self, "文档类型不支持",
                                "官方 OpenAPI 只支持表格 / 智能表格，当前文档不是表格类型")
            return
        key = doc["key"]
        name = doc.get("name") or key

        def work():
            creds = self._credentials()
            client = openapi.TdClient(*creds)
            post_to_main(self.log, f"连接文档：{name}")
            file_id = client.convert_file_id(key)
            post_to_main(self.log, f"fileID = {file_id}")
            sheets = [sheet for sheet in client.list_sheets(file_id)
                      if sheet.get("sheetID") in openapi.WRITABLE_TAB_IDS]
            return client, file_id, sheets, creds

        def done(result):
            client, file_id, sheets, creds = result
            if (self.doc or {}).get("key") != key:
                return
            self.client = client
            self.file_id = file_id
            self.sheets = sheets
            self._cred_snapshot = creds
            self._invalidate_preview("已连接，正在读取字段…")
            self.execute_btn.setEnabled(False)
            self.summary_label.setText("已连接，正在读取字段…")
            self.sheet_combo.blockSignals(True)
            self.sheet_combo.clear()
            for sheet in self.sheets:
                self.sheet_combo.addItem(
                    f"{sheet.get('title') or sheet.get('sheetID')}"
                    f"  ({sheet.get('sheetID')})", sheet.get("sheetID"))
            self.sheet_combo.blockSignals(False)
            self.conn_status.setText(f"已连接 · {len(self.sheets)} 个可写子表")
            self.refresh_btn.setEnabled(True)
            self.content_tabs.setCurrentIndex(0)
            if not self.sheets:
                self.log("文档里没有允许回写的子表")
                return
            self.load_fields()

        self._run(work, done, f"正在连接：{name}", "连接文档")

    def on_sheet_changed(self, _index: int) -> None:
        if self.client and self.file_id:
            self.fields = []
            self.field_by_id = {}
            self.sample_records = []
            self.remote_records = []
            self.remote_total = 0
            self._invalidate_preview("子表已切换，读取字段后请重新对比")
            self._rebuild_mapping_table()
            self._render_remote()
            self.load_fields()

    def load_fields(self) -> None:
        if not (self.client and self.file_id and self.sheet_combo.currentData()):
            return
        client = self.client
        file_id = self.file_id
        sheet_id = self.sheet_combo.currentData()
        limit = self._remote_limit()
        self._invalidate_preview("正在刷新字段与云端记录…")

        def work():
            fields = client.get_fields(file_id, sheet_id)
            page = client.get_records_page(file_id, sheet_id, 0, limit)
            return fields, page.get("records") or [], page.get("total") or 0

        def done(result):
            if (self.client is not client or self.file_id != file_id
                    or self.sheet_combo.currentData() != sheet_id):
                return
            fields, samples, total = result
            self.fields = fields
            self.field_by_id = {f["fieldID"]: f for f in fields}
            self.sample_records = samples
            self.remote_records = samples
            self.remote_total = total
            self._invalidate_preview("字段已匹配，请点击「对比云端」")
            self._rebuild_mapping_table()
            self.auto_match(quiet=True)
            self._render_remote()
            self.log(f"字段 {len(fields)} 个 · 记录 {total} 条 · 已自动同名匹配")

        self._run(work, done, "正在读取字段与腾讯表格预览…", "读取字段")

    def _remote_limit(self) -> int:
        value = self.remote_limit_combo.currentData()
        try:
            return max(1, min(200, int(value or 50)))
        except (TypeError, ValueError):
            return 50

    def reload_remote(self) -> None:
        if not (self.client and self.file_id and self.fields
                and self.sheet_combo.currentData()):
            return
        client = self.client
        file_id = self.file_id
        sheet_id = self.sheet_combo.currentData()
        limit = self._remote_limit()

        def work():
            page = client.get_records_page(file_id, sheet_id, 0, limit)
            return page.get("records") or [], page.get("total") or 0

        def done(result):
            if (self.client is not client or self.file_id != file_id
                    or self.sheet_combo.currentData() != sheet_id):
                return
            self.remote_records, self.remote_total = result
            self.sample_records = self.remote_records
            self._render_remote()
            self._refresh_mapping_labels()
            self.log(f"腾讯表格预览：显示 {len(self.remote_records)} / {self.remote_total} 条")

        self._run(work, done, f"正在读取腾讯表格预览（前 {limit} 条）…", "读取预览")

    def _remote_fields(self) -> list[dict]:
        prefer = ("歌曲名称", "作品名称", "演出名称", "名称", "标题",
                  "直播标题", "无料名", "奖项", "活动名称")

        def rank(field):
            title = str(field.get("fieldTitle") or "")
            for index, name in enumerate(prefer):
                if title == name:
                    return (0, index)
            for index, name in enumerate(prefer):
                if title and name in title:
                    return (1, index)
            return (2, 0)

        return sorted(self.fields, key=rank)

    def _render_remote(self) -> None:
        if not self.fields:
            self.remote_table.setRowCount(0)
            self.remote_table.setColumnCount(0)
            self.remote_info.setText("连接文档后显示腾讯表格记录")
            return
        fields = self._remote_fields()
        headers = [f.get("fieldTitle") or f.get("fieldID") for f in fields]
        if self._remote_sorter.col >= len(headers):
            self._remote_sorter.col = -1
            self.remote_table.horizontalHeader().setSortIndicatorShown(False)
        self.remote_table.setColumnCount(len(headers))
        self.remote_table.setHorizontalHeaderLabels(headers)
        rows = self._ordered(self.remote_records, self._remote_sorter,
                             lambda record, col: openapi.display_value(
                                 fields[col], (record.get("values") or {}).get(
                                     fields[col].get("fieldTitle"))))
        self.remote_table.setRowCount(len(rows))
        self.remote_table.setUpdatesEnabled(False)
        try:
            for row, record in enumerate(rows):
                values = record.get("values") or {}
                for col, field in enumerate(fields):
                    full = openapi.display_value(
                        field, values.get(field.get("fieldTitle")))
                    shown = full if len(full) <= 300 else full[:300] + "…"
                    item = QTableWidgetItem(shown)
                    item.setToolTip(full)
                    self.remote_table.setItem(row, col, item)
        finally:
            self.remote_table.setUpdatesEnabled(True)
        for col in range(len(headers)):
            self.remote_table.setColumnWidth(col, 130)
        table_columns.fit_table_columns(self.remote_table)
        title = (self.sheet_combo.currentText().split("  (")[0]
                 if self.sheet_combo.count() else "")
        total = self.remote_total or len(rows)
        self.remote_info.setText(
            f"{title} · 显示 {len(rows)} / {total} 条 · 只读")

    # ==================== 映射 ====================

    def _rebuild_mapping_table(self) -> None:
        self.map_table.setRowCount(0)
        self.map_hint.setVisible(not self.local_headers)
        if not self.local_headers:
            return
        titles: dict[str, int] = {}
        for field in self.fields:
            title = field.get("fieldTitle") or field.get("fieldID")
            titles[title] = titles.get(title, 0) + 1
        self.mapping = {col: fid for col, fid in self.mapping.items()
                        if col in self.local_headers and fid in self.field_by_id}
        def mapping_value(header, col):
            if col == 0:
                return header
            field = self.field_by_id.get(self.mapping.get(header), {})
            if col == 2:
                return FIELD_TYPES.get(int(field.get("fieldType") or 0), "")
            for record in self.sample_records:
                values = record.get("values") or {}
                if field.get("fieldTitle") in values:
                    return openapi.display_value(field, values[field["fieldTitle"]])
            return ""

        headers = self._ordered(self.local_headers, self._map_sorter, mapping_value)
        self.map_table.setRowCount(len(headers))
        self.map_combos: dict[str, QComboBox] = {}
        for row, header in enumerate(headers):
            name_item = QTableWidgetItem(header)
            name_item.setData(Qt.UserRole, header)
            self.map_table.setItem(row, 0, name_item)
            combo = QComboBox()
            combo.setMinimumWidth(120)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.addItem("（不映射）", "")
            for field in sorted(self.fields, key=lambda f: str(f.get("fieldTitle") or "")):
                field_id = field.get("fieldID")
                title = field.get("fieldTitle") or field_id
                label = f"{title} [{field_id}]" if titles.get(title, 0) > 1 else title
                combo.addItem(label, field_id)
            if combo.count() > 12:
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.NoInsert)
            index = combo.findData(self.mapping.get(header, ""))
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda _idx=0, col=header: self._on_mapping_changed(col))
            self.map_combos[header] = combo
            self.map_table.setCellWidget(row, 1, combo)
            self.map_table.setItem(row, 2, QTableWidgetItem(""))
            self.map_table.setItem(row, 3, QTableWidgetItem(""))
        self.map_table.setColumnWidth(0, 100)
        self.map_table.setColumnWidth(1, 160)
        self.map_table.setColumnWidth(2, 120)
        self.map_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._refresh_mapping_labels()
        self._rebuild_key_combo()

    def _on_mapping_changed(self, header: str) -> None:
        combo = self.map_combos.get(header)
        if combo is None:
            return
        field_id = combo.currentData() or ""
        if field_id:
            self.mapping[header] = field_id
        else:
            self.mapping.pop(header, None)
        self._refresh_mapping_labels()
        self._rebuild_key_combo()
        self._invalidate_preview("字段匹配已修改，请重新对比云端")

    def _refresh_mapping_labels(self) -> None:
        for row in range(self.map_table.rowCount()):
            header = self.map_table.item(row, 0).data(Qt.UserRole)
            field = self.field_by_id.get(self.mapping.get(header, ""))
            type_item = self.map_table.item(row, 2)
            sample_item = self.map_table.item(row, 3)
            if type_item is None or sample_item is None:
                continue
            if field is None:
                type_item.setText("")
                sample_item.setText("")
                continue
            ftype = int(field.get("fieldType") or 0)
            suffix = "" if ftype in SUPPORTED_TYPES else "（不支持）"
            type_item.setText(f"{FIELD_TYPES.get(ftype, ftype)}{suffix}")
            sample = ""
            for record in self.sample_records:
                values = record.get("values") or {}
                if field.get("fieldTitle") in values:
                    sample = openapi.display_value(field, values[field.get("fieldTitle")])
                    break
            sample_item.setText(sample)

    def _rebuild_key_combo(self) -> None:
        current = self.key_combo.currentData()
        self.key_combo.blockSignals(True)
        self.key_combo.clear()
        candidates = [h for h in self.local_headers if self.mapping.get(h)]
        for header in candidates:
            self.key_combo.addItem(header, header)
        index = self.key_combo.findData(current) if current else -1
        if index < 0:
            suggested = workflow.suggest_key(self.local_headers, self.mapping, self.field_by_id)
            index = self.key_combo.findData(suggested)
        if index < 0 and candidates:
            index = 0
        self.key_combo.setCurrentIndex(index if index >= 0 else -1)
        if self.key_combo.count() > 12:
            self.key_combo.setEditable(True)
            self.key_combo.setInsertPolicy(QComboBox.NoInsert)
        self.key_combo.blockSignals(False)

    def auto_match(self, quiet: bool = False) -> None:
        if not (self.local_headers and self.fields):
            if not quiet:
                QMessageBox.information(self, "无法匹配", "请先载入本地文件并连接字段")
            return
        previous_key = self.key_combo.currentData()
        suggested = workflow.suggest_mapping(self.local_headers, self.fields)
        matched = 0
        for header in self.local_headers:
            combo = self.map_combos.get(header)
            if combo is None:
                continue
            field_id = suggested.get(header)
            if not field_id:
                continue
            index = combo.findData(field_id)
            if index >= 0:
                combo.setCurrentIndex(index)
                matched += 1
        self._rebuild_key_combo()
        if not previous_key:
            key = workflow.suggest_key(self.local_headers, self.mapping, self.field_by_id)
            self.key_combo.setCurrentIndex(self.key_combo.findData(key))
        if not quiet:
            self.log(f"同名匹配 {matched} 列")

    def clear_mapping(self) -> None:
        for combo in getattr(self, "map_combos", {}).values():
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.mapping.clear()
        self._refresh_mapping_labels()
        self._rebuild_key_combo()
        self._invalidate_preview("已清空字段匹配，请重新匹配后对比")

    def save_mapping_file(self) -> None:
        if not self.local_headers:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存映射", "tcdoc_mapping.json", "JSON (*.json)")
        if not path:
            return
        payload = {
            "doc_key": (self.doc or {}).get("key", ""),
            "sheet_id": self.sheet_combo.currentData() or "",
            "mapping": self.mapping,
            "primary_key": self.key_combo.currentData() or "",
            "local_headers": self.local_headers,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        self.log(f"映射已保存：{path}")

    def load_mapping_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "载入映射", "", "JSON (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 载入失败要告诉用户原因
            QMessageBox.warning(self, "载入失败", str(exc))
            return
        self.mapping = {str(k): str(v) for k, v in (payload.get("mapping") or {}).items()}
        for header, combo in getattr(self, "map_combos", {}).items():
            index = combo.findData(self.mapping.get(header, ""))
            combo.blockSignals(True)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
        self._refresh_mapping_labels()
        self._rebuild_key_combo()
        key = payload.get("primary_key")
        if key:
            index = self.key_combo.findData(key)
            if index >= 0:
                self.key_combo.setCurrentIndex(index)
        self._invalidate_preview("已载入字段匹配，请重新对比云端")
        self.log(f"映射已载入：{path}")

    # ==================== 本地文件 ====================

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择本地数据",
            "", "表格/JSON (*.csv *.tsv *.xlsx *.xlsm *.json);;所有文件 (*)")
        if path:
            self.load_local_file(path)

    def load_local_file(self, path: str) -> None:
        try:
            headers, rows = openapi.load_table(path)
        except Exception as exc:  # noqa: BLE001 - 读取失败要告诉用户原因
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        self.local_path = path
        self.local_headers, self.local_rows = headers, rows
        self._local_page = 1
        self.local_label.setText(
            f"{Path(path).name}（{len(rows)} 行 × {len(headers)} 列）")
        self.local_label.setToolTip(path)
        self._render_local_table()
        self._invalidate_preview("本地表格已导入，连接后点击「对比云端」")
        self.log(f"载入本地文件：{path}，{len(rows)} 行，{len(headers)} 列")
        self._rebuild_mapping_table()
        self.auto_match(quiet=True)

    def _render_local_table(self) -> None:
        headers = self.local_headers
        if self._local_sorter.col >= len(headers):
            self._local_sorter.col = -1
            self.local_table.horizontalHeader().setSortIndicatorShown(False)
        self.local_table.setColumnCount(len(headers))
        self.local_table.setHorizontalHeaderLabels(headers)
        ordered = self._ordered(self.local_rows, self._local_sorter,
                                lambda record, col: record.get(headers[col], ""))
        pages = max(1, (len(ordered) + self._page_size - 1) // self._page_size)
        self._local_page = max(1, min(self._local_page, pages))
        start = (self._local_page - 1) * self._page_size
        preview = ordered[start:start + self._page_size]
        self.local_table.setRowCount(len(preview))
        for row, record in enumerate(preview):
            for col, header in enumerate(headers):
                text = str(record.get(header, ""))
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.local_table.setItem(row, col, item)
        self.local_table.horizontalHeader().setStretchLastSection(True)
        table_columns.fit_table_columns(self.local_table)
        self.local_page_label.setText(
            f"第 {self._local_page} / {pages} 页 · 共 {len(ordered)} 条")
        self.local_prev_btn.setEnabled(self._local_page > 1 and not self._busy)
        self.local_next_btn.setEnabled(self._local_page < pages and not self._busy)

    # ==================== 预览 / 写入 ====================

    def _writable_actions(self) -> list[dict]:
        return [a for a in self.actions if a.get("op") in ("新增", "更新")
                and a.get("row_no") in self._selected_rows]

    def _validate_ready(self) -> bool:
        if not (self.client and self.file_id and self.sheet_combo.currentData()):
            QMessageBox.warning(self, "未连接", "请先连接当前文档并选择子表")
            return False
        try:
            current = self._credentials()
        except RuntimeError as exc:
            QMessageBox.warning(self, "连接不可用", str(exc))
            return False
        if self._cred_snapshot is not None and current != self._cred_snapshot:
            QMessageBox.warning(
                self, "登录已变更",
                "当前登录已变更或关闭，请重新连接当前文档后再操作。")
            return False
        try:
            openapi.validate_write_scope((self.doc or {}).get("key", ""),
                                         self.sheet_combo.currentData())
            self.client.validate_write_target(self.file_id, self.sheet_combo.currentData())
        except openapi.ApiError as exc:
            QMessageBox.warning(self, "禁止回写此目标", str(exc))
            return False
        if not self.local_headers:
            QMessageBox.warning(self, "没有本地数据", "请先选择 CSV / Excel / JSON 文件")
            return False
        if not self.mapping:
            QMessageBox.warning(self, "没有映射", "请至少映射一列")
            return False
        if not self.key_combo.currentData():
            QMessageBox.warning(self, "没有匹配列", "请选择用于判断同一条记录的匹配列")
            return False
        return True

    def on_preview(self) -> None:
        if not self._validate_ready():
            return
        client = self.client
        file_id = self.file_id
        sheet_id = self.sheet_combo.currentData()
        key_col = self.key_combo.currentData()
        mapping = dict(self.mapping)
        field_by_id = dict(self.field_by_id)
        headers = list(self.local_headers)
        rows = list(self.local_rows)
        self._invalidate_preview("正在对比本地表格与云端…")
        revision = self._revision

        def work():
            records = client.get_records(
                file_id, sheet_id, lambda t: post_to_main(self.log, t))
            if self._cancel.is_set():
                raise RuntimeError("对比已取消")
            actions = workflow.compare(
                headers, rows, mapping, field_by_id, key_col, records)
            return actions, records

        def done(result):
            if (self.client is not client or self.file_id != file_id
                    or self.sheet_combo.currentData() != sheet_id
                    or self._revision != revision):
                return
            actions, records = result
            self.actions = actions
            self.remote_records = records[:self._remote_limit()]
            self.remote_total = len(records)
            self.sample_records = self.remote_records
            self._render_remote()
            self._refresh_mapping_labels()
            counts: dict[str, int] = {}
            for action in actions:
                counts[action["op"]] = counts.get(action["op"], 0) + 1
            self.summary_label.setText(
                f"本地 {len(actions)} 行 · 新增 {counts.get('新增', 0)}"
                f" · 更新 {counts.get('更新', 0)} · 不变 {counts.get('不变', 0)}"
                f" · 错误 {counts.get('错误', 0)} · 腾讯记录 {len(records)} 条")
            self._render_diff()
            self.content_tabs.setCurrentIndex(0)
            self.log(f"预览完成：{self.summary_label.text()}")

        self._run(work, done, "正在读取云端记录并对比差异…", "对比云端")

    def _render_diff(self) -> None:
        self.diff_table.blockSignals(True)
        self.diff_table.setRowCount(0)
        mode = self.diff_filter.currentData()
        filtered = [action for action in self.actions
                    if mode == "all" or (mode == "diff" and action.get("op") != "不变")
                    or action.get("op") == mode]
        columns = ("", "row_no", "op", "key", "message")
        filtered = self._ordered(filtered, self._diff_sorter,
                                 lambda action, col: action.get(columns[col], ""))
        pages = max(1, (len(filtered) + self._page_size - 1) // self._page_size)
        self._diff_page = max(1, min(self._diff_page, pages))
        start = (self._diff_page - 1) * self._page_size
        preview = filtered[start:start + self._page_size]
        self.diff_table.setRowCount(len(preview))
        for row, action in enumerate(preview):
            check = QTableWidgetItem()
            check.setData(Qt.UserRole, action["row_no"])
            if action.get("op") in ("新增", "更新"):
                check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                check.setCheckState(Qt.Checked if action["row_no"] in self._selected_rows
                                    else Qt.Unchecked)
            else:
                check.setFlags(Qt.ItemIsSelectable)
                check.setToolTip("此行无可写差异或需要先处理错误")
            self.diff_table.setItem(row, 0, check)
            values = [str(action.get("row_no", "")), action.get("op", ""),
                      str(action.get("key", "")), action.get("message", "")]
            for col, text in enumerate(values, start=1):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.diff_table.setItem(row, col, item)
        self.diff_table.setColumnWidth(0, 36)
        self.diff_table.setColumnWidth(1, 48)
        self.diff_table.setColumnWidth(2, 60)
        self.diff_table.setColumnWidth(3, 160)
        self.diff_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.diff_table.blockSignals(False)
        self.diff_page_label.setText(
            f"第 {self._diff_page} / {pages} 页 · 共 {len(filtered)} 条")
        self.diff_prev_btn.setEnabled(self._diff_page > 1 and not self._busy)
        self.diff_next_btn.setEnabled(self._diff_page < pages and not self._busy)
        self._show_diff_detail()
        self._update_selection()

    def on_execute(self) -> None:
        if not self._validate_ready():
            return
        writable = self._writable_actions()
        if not writable:
            QMessageBox.information(self, "未勾选记录", "请先对比云端，再勾选要写入的新增或更新行")
            return
        counts: dict[str, int] = {}
        for action in writable:
            counts[action["op"]] = counts.get(action["op"], 0) + 1
        revision = self._revision
        box = QDialog(self)
        box.setWindowTitle("确认写入")
        box.resize(720, 520)
        box.setMinimumSize(420, 300)
        content = QVBoxLayout(box)
        label = QLabel(
            f"{(self.doc or {}).get('name', '')} · {self.sheet_combo.currentText()}\n"
            f"仅写入勾选 {len(writable)} 条：新增 {counts.get('新增', 0)} 条，"
            f"更新 {counts.get('更新', 0)} 条。")
        label.setWordWrap(True)
        content.addWidget(label)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText("\n\n".join(
            f"第 {a['row_no']} 行 · {a['op']} · {a.get('key', '')}\n" +
            "\n".join(f"{c['field']}：{c.get('remote') or '（空）'} → "
                        f"{c.get('local') or '（空）'}" for c in a.get('changes') or [])
            for a in writable))
        content.addWidget(details, 1)
        note = QLabel("写入前再次核对云端；只更新列出的差异字段，空白单元格不清空云端。")
        note.setWordWrap(True)
        content.addWidget(note)
        buttons = QDialogButtonBox()
        cancel = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        ok = buttons.addButton("写入勾选行", QDialogButtonBox.AcceptRole)
        ok.setProperty("variant", "primary")
        cancel.setDefault(True)
        ok.setAutoDefault(False)
        buttons.accepted.connect(box.accept)
        buttons.rejected.connect(box.reject)
        content.addWidget(buttons)
        if box.exec() != QDialog.Accepted or self._revision != revision:
            return

        client = self.client
        file_id = self.file_id
        sheet_id = self.sheet_combo.currentData()
        key_col = self.key_combo.currentData()
        mapping = dict(self.mapping)
        field_by_id = dict(self.field_by_id)
        headers = list(self.local_headers)
        rows = list(self.local_rows)
        preview_actions = list(self.actions)
        selected_row_nos = sorted(self._selected_rows)
        history_doc = dict(self.doc or {})
        history_sheet = dict(next((item for item in self.sheets
                                   if item.get("sheetID") == sheet_id), {}))

        def record_result(result):
            self._record_history(result, sheet_id, doc=history_doc, sheet=history_sheet)

        def work():
            if self._revision != revision or self._credentials() != self._cred_snapshot:
                raise RuntimeError("当前文档或登录已变化，请重新连接后对比")
            return workflow.execute_selected(
                client, file_id, sheet_id, headers, rows, mapping, field_by_id,
                key_col, preview_actions, selected_row_nos,
                lambda t: post_to_main(self.log, t), self._cancel.is_set)

        def done(result):
            self.log(f"写入完成：新增 {result.get('added', 0)}，"
                     f"更新 {result.get('updated', 0)}")
            record_result(result)
            toast = getattr(getattr(self.page, "app", None), "toast", None)
            if callable(toast):
                toast(f"写入完成：新增 {result.get('added', 0)} 条，"
                      f"更新 {result.get('updated', 0)} 条", "success")
            if (self.client is client and self.file_id == file_id
                    and self.sheet_combo.currentData() == sheet_id):
                self.on_preview()

        self._run(work, done, f"正在写入勾选 {len(writable)} 条并读回核对…", "写入",
                  receipt_callback=record_result)

    def _record_history(self, result: dict, sheet_id: str, *, doc=None, sheet=None) -> None:
        try:
            from toolbox.tcdoc import push_history
        except Exception:  # noqa: BLE001 - 历史不可用不影响写入结果
            return
        if sheet is None:
            sheet = next((item for item in self.sheets
                          if item.get("sheetID") == sheet_id), {})
        if doc is None:
            doc = self.doc or {}
        push_history.record(
            tab_name=str(sheet.get("title") or ""), tab_id=str(sheet_id or ""),
            doc_key=str(doc.get("key") or ""), profile="mapping",
            source="映射回写", status="成功" if result.get("verified") else "需核对",
            added=int(result.get("added") or 0),
            note=f"更新 {int(result.get('updated') or 0)} 条；"
                 f"记录：{'、'.join(result.get('record_ids') or [])}")
