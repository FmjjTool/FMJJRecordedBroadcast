"""腾讯文档页 · 字段映射回写面板（官方 OpenAPI）。

把本地 CSV / Excel / JSON 的列映射到当前腾讯文档的智能表字段，
按主键预览差异，再批量新增 / 更新。不会删除记录。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from toolbox.tcdoc import openapi

FIELD_TYPES = openapi.FIELD_TYPES
SUPPORTED_TYPES = openapi.SUPPORTED_TYPES


class TcDocMappingPanel(QWidget):
    def __init__(self, page, get_doc: Callable[[], dict | None]):
        super().__init__(page)
        self.page = page
        self.get_doc = get_doc
        self.doc: dict | None = None
        self.client: openapi.TdClient | None = None
        self.file_id = ""
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
        self._busy = False
        self._buttons: list[QPushButton] = []
        self._build_ui()
        self._fill_credentials(openapi.load_credentials())
        self.refresh()

    # ==================== 界面 ====================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(8)

        cred_box = QGroupBox("🔐 官方 OpenAPI 凭证（加密保存，不写入日志）")
        grid = QGridLayout(cred_box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText("Client-Id / APPid")
        self.client_id_edit.setToolTip("腾讯文档开放平台应用的 Client-Id")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Access-Token")
        self.token_edit.setToolTip("OAuth2.0 的 Access-Token，约 30 天过期；到期后重新获取")
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.open_id_edit = QLineEdit()
        self.open_id_edit.setPlaceholderText("Open-Id")
        self.open_id_edit.setToolTip("OAuth2.0 授权返回的 Open-Id")
        self.show_token_cb = QCheckBox("显示")
        self.show_token_cb.toggled.connect(
            lambda on: self.token_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        self.save_cred_btn = QPushButton("💾 保存凭证")
        self.save_cred_btn.setToolTip("保存到 Cookie 管理器的加密文件（本机可解密）")
        self.save_cred_btn.clicked.connect(self._save_credentials)
        self.clear_cred_btn = QPushButton("清除凭证")
        self.clear_cred_btn.clicked.connect(self._clear_credentials)
        self.cred_status = QLabel("")
        self.cred_status.setStyleSheet("color: #888;")

        grid.addWidget(QLabel("Client-Id"), 0, 0)
        grid.addWidget(self.client_id_edit, 0, 1)
        grid.addWidget(QLabel("Access-Token"), 0, 2)
        grid.addWidget(self.token_edit, 0, 3)
        grid.addWidget(self.show_token_cb, 0, 4)
        cred_buttons = QHBoxLayout()
        cred_buttons.setContentsMargins(0, 0, 0, 0)
        cred_buttons.addWidget(self.save_cred_btn)
        cred_buttons.addWidget(self.clear_cred_btn)
        cred_buttons.addStretch(1)
        grid.addWidget(QLabel("Open-Id"), 1, 0)
        grid.addWidget(self.open_id_edit, 1, 1)
        grid.addLayout(cred_buttons, 1, 2, 1, 2)
        grid.addWidget(self.cred_status, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 2)

        doc_row = QHBoxLayout()
        self.doc_label = QLabel("当前文档：未选择")
        self.doc_label.setStyleSheet("color: #555;")
        self.connect_btn = QPushButton("🔗 连接当前文档")
        self.connect_btn.clicked.connect(self.on_connect)
        self.refresh_btn = QPushButton("刷新字段")
        self.refresh_btn.clicked.connect(self.load_fields)
        self.refresh_btn.setEnabled(False)
        self.conn_status = QLabel("")
        self.conn_status.setStyleSheet("color: #2e7d32;")
        doc_row.addWidget(self.doc_label, 1)
        doc_row.addWidget(self.connect_btn)
        doc_row.addWidget(self.refresh_btn)
        doc_row.addWidget(self.conn_status)
        grid.addLayout(doc_row, 2, 0, 1, 5)
        layout.addWidget(cred_box)
        self._buttons.extend([self.save_cred_btn, self.clear_cred_btn,
                              self.connect_btn, self.refresh_btn])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left_panel())
        split.addWidget(self._build_right_panel())
        split.setSizes([600, 660])
        layout.addWidget(split, 1)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        local_box = QGroupBox("📄 本地数据（CSV / Excel / JSON）")
        local_layout = QVBoxLayout(local_box)
        row = QHBoxLayout()
        self.local_label = QLabel("尚未选择文件（选择后这里显示本地预览）")
        self.local_label.setStyleSheet("color: #888;")
        self.pick_btn = QPushButton("选择文件")
        self.pick_btn.clicked.connect(self._pick_file)
        row.addWidget(self.local_label, 1)
        row.addWidget(self.pick_btn)
        local_layout.addLayout(row)
        self.local_table = self._make_table([])
        local_layout.addWidget(self.local_table, 1)

        mapping_box = QGroupBox("🧩 字段映射（本地列 → 腾讯字段）")
        mapping_layout = QVBoxLayout(mapping_box)
        top = QHBoxLayout()
        top.addWidget(QLabel("子表"))
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(220)
        self.sheet_combo.currentIndexChanged.connect(self.on_sheet_changed)
        top.addWidget(self.sheet_combo, 1)
        top.addWidget(QLabel("主键"))
        self.key_combo = QComboBox()
        self.key_combo.setMinimumWidth(150)
        top.addWidget(self.key_combo)
        mapping_layout.addLayout(top)
        self.map_hint = QLabel(
            "选择本地 CSV / Excel / JSON 后，这里会按列生成本地 → 腾讯字段的映射行。")
        self.map_hint.setStyleSheet("color: #888;")
        self.map_hint.setWordWrap(True)
        mapping_layout.addWidget(self.map_hint)
        self.map_table = self._make_table(["本地列", "腾讯字段", "类型", "示例值"])
        self.map_table.setMinimumHeight(220)
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
        self._buttons.extend([self.pick_btn, self.auto_btn, self.clear_map_btn,
                              self.load_map_btn, self.save_map_btn])
        mapping_layout.addLayout(buttons)

        left_split = QSplitter(Qt.Vertical)
        left_split.addWidget(local_box)
        left_split.addWidget(mapping_box)
        left_split.setSizes([220, 500])
        layout.addWidget(left_split, 1)
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
        hint = QLabel("只读预览腾讯文档里的实际记录；本地数据请在左侧选择文件。"
                      "左右滚动可查看全部字段。")
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        remote_layout.addWidget(hint)
        remote_bar = QHBoxLayout()
        self.remote_info = QLabel("连接文档后显示腾讯表格记录")
        self.remote_info.setStyleSheet("color: #555;")
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
        remote_layout.addWidget(self.remote_table, 1)
        self._buttons.extend([self.remote_refresh_btn, self.remote_limit_combo])

        diff_page = QWidget()
        diff_layout = QVBoxLayout(diff_page)
        diff_layout.setContentsMargins(6, 6, 6, 6)
        diff_layout.setSpacing(6)
        self.summary_label = QLabel("尚未生成预览")
        self.summary_label.setStyleSheet("font-weight: 600; color: #1e3a8a;")
        diff_layout.addWidget(self.summary_label)
        self.diff_table = self._make_table(["行号", "操作", "主键", "说明"])
        diff_layout.addWidget(self.diff_table, 1)
        row = QHBoxLayout()
        self.preview_btn = QPushButton("生成预览")
        self.preview_btn.clicked.connect(self.on_preview)
        self.execute_btn = QPushButton("✅ 执行写入")
        self.execute_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self.on_execute)
        row.addStretch(1)
        row.addWidget(self.preview_btn)
        row.addWidget(self.execute_btn)
        diff_layout.addLayout(row)
        self._buttons.extend([self.preview_btn, self.execute_btn])

        content_tabs.addTab(remote_page, "📋 腾讯表格（只读）")
        content_tabs.addTab(diff_page, "🔍 差异预览")
        self.content_tabs = content_tabs

        log_box = QGroupBox("日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        log_layout.addWidget(self.log_view)
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_view.clear)
        log_layout.addWidget(clear_btn, alignment=Qt.AlignRight)

        right_split = QSplitter(Qt.Vertical)
        right_split.addWidget(content_tabs)
        right_split.addWidget(log_box)
        right_split.setSizes([500, 220])
        layout.addWidget(right_split, 1)
        return panel

    def _make_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        return table

    # ==================== 凭证 / 文档 ====================

    def _fill_credentials(self, creds: dict) -> None:
        self.client_id_edit.setText(str(creds.get("client_id") or ""))
        self.token_edit.setText(str(creds.get("access_token") or ""))
        self.open_id_edit.setText(str(creds.get("open_id") or ""))
        self.cred_status.setText("已载入凭证" if creds.get("access_token") else "")

    def _creds(self) -> tuple[str, str, str]:
        return (self.client_id_edit.text().strip(), self.token_edit.text().strip(),
                self.open_id_edit.text().strip())

    def _save_credentials(self) -> None:
        client_id, token, open_id = self._creds()
        if not (client_id and token and open_id):
            QMessageBox.warning(self, "凭证不完整", "请填写 Client-Id、Access-Token、Open-Id")
            return
        try:
            openapi.save_credentials(client_id, token, open_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.cred_status.setText("已加密保存到本机")
        self.log("凭证已加密保存；如已连接，请点「连接当前文档」使新凭证生效")

    def _clear_credentials(self) -> None:
        openapi.clear_credentials()
        self.client_id_edit.clear()
        self.token_edit.clear()
        self.open_id_edit.clear()
        self.client = None
        self.file_id = ""
        self.sheets = []
        self.fields = []
        self.field_by_id = {}
        self.sample_records = []
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.blockSignals(False)
        self.refresh_btn.setEnabled(False)
        self.execute_btn.setEnabled(False)
        self.cred_status.setText("已清除")
        self.conn_status.setText("凭证已清除，请重新连接")
        self.log("已清除本机保存的官方 API 凭证")

    def set_doc(self, doc: dict | None) -> None:
        if doc and self.doc and doc.get("key") == self.doc.get("key"):
            self.doc = doc
            self._update_doc_label()
            return
        self.doc = doc
        self.client = None
        self.file_id = ""
        self.sheets = []
        self.fields = []
        self.field_by_id = {}
        self.sample_records = []
        self.remote_records = []
        self.remote_total = 0
        self.mapping = {}
        self.actions = []
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.blockSignals(False)
        self._rebuild_mapping_table()
        self._render_remote()
        self.summary_label.setText("尚未生成预览")
        self.execute_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.conn_status.setText("")
        self._update_doc_label()

    def refresh(self) -> None:
        if not self.client_id_edit.text() and not self.token_edit.text():
            self._fill_credentials(openapi.load_credentials())
        self._update_doc_label()

    def _update_doc_label(self) -> None:
        doc = self.doc
        if not doc:
            self.doc_label.setText("当前文档：未选择（先在页面顶部选择文档）")
            self.connect_btn.setEnabled(False)
            return
        self.connect_btn.setEnabled(True)
        self.doc_label.setText(
            f"当前文档：{doc.get('name') or doc.get('key')}（{doc.get('kind') or '?'}）")

    # ==================== 后台任务 ====================

    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in self._buttons:
            button.setEnabled(not busy)
        for widget in (self.sheet_combo, self.key_combo):
            widget.setEnabled(not busy)
        if not busy:
            self.connect_btn.setEnabled(self.doc is not None)
            self.refresh_btn.setEnabled(bool(self.client and self.file_id))
            self.execute_btn.setEnabled(bool(self._writable_actions()))

    def _run(self, fn, on_done, label: str) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.log(label)

        def done(result, err):
            self._set_busy(False)
            if err:
                self.log(f"[错误] {err}")
                QMessageBox.warning(self, "操作失败", err)
                return
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[错误] {exc}")
                QMessageBox.warning(self, "操作失败", str(exc))

        run_bg(fn, done)

    # ==================== 连接 / 字段 ====================

    def on_connect(self) -> None:
        doc = self.get_doc()
        if not doc:
            QMessageBox.warning(self, "没有文档", "请先在页面顶部选择腾讯文档")
            return
        self.set_doc(doc)
        kind = (doc.get("kind") or "").lower()
        if kind not in ("sheet", "smartsheet"):
            QMessageBox.warning(self, "文档类型不支持",
                                "官方 OpenAPI 只支持表格 / 智能表格，当前文档不是表格类型")
            return
        client_id, token, open_id = self._creds()
        if not (client_id and token and open_id):
            QMessageBox.warning(self, "缺少凭证", "请填写 Client-Id、Access-Token、Open-Id")
            return
        self.client = openapi.TdClient(client_id, token, open_id)
        client = self.client
        key = doc["key"]
        name = doc.get("name") or key

        def work():
            post_to_main(self.log, f"连接文档：{name}")
            file_id = client.convert_file_id(key)
            post_to_main(self.log, f"fileID = {file_id}")
            sheets = client.list_sheets(file_id)
            return file_id, sheets

        def done(result):
            if self.client is not client:
                return
            self.file_id, self.sheets = result
            self.sheet_combo.blockSignals(True)
            self.sheet_combo.clear()
            for sheet in self.sheets:
                self.sheet_combo.addItem(
                    f"{sheet.get('title') or sheet.get('sheetID')}"
                    f"  ({sheet.get('sheetID')})", sheet.get("sheetID"))
            self.sheet_combo.blockSignals(False)
            self.conn_status.setText(f"已连接 · {len(self.sheets)} 个子表")
            self.refresh_btn.setEnabled(True)
            self.content_tabs.setCurrentIndex(0)
            if not self.sheets:
                self.log("文档里没有智能表子表")
                return
            self.load_fields()

        self._run(work, done, f"正在连接：{name}")

    def on_sheet_changed(self, _index: int) -> None:
        if self.client and self.file_id:
            self.fields = []
            self.field_by_id = {}
            self.sample_records = []
            self.remote_records = []
            self.remote_total = 0
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
            self._rebuild_mapping_table()
            self.auto_match(quiet=True)
            self._render_remote()
            self.log(f"字段 {len(fields)} 个 · 记录 {total} 条 · 已自动同名匹配")

        self._run(work, done, "正在读取字段与腾讯表格预览…")

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

        self._run(work, done, f"正在读取腾讯表格预览（前 {limit} 条）…")

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
        self.remote_table.setColumnCount(len(headers))
        self.remote_table.setHorizontalHeaderLabels(headers)
        rows = self.remote_records
        self.remote_table.setRowCount(len(rows))
        self.remote_table.setUpdatesEnabled(False)
        try:
            for row, record in enumerate(rows):
                values = record.get("values") or {}
                for col, field in enumerate(fields):
                    text = openapi.display_value(
                        field, values.get(field.get("fieldTitle")))
                    if len(text) > 300:
                        text = text[:300] + "…"
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    self.remote_table.setItem(row, col, item)
        finally:
            self.remote_table.setUpdatesEnabled(True)
        for col in range(len(headers)):
            self.remote_table.setColumnWidth(col, 130)
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
        self.map_table.setRowCount(len(self.local_headers))
        self.map_combos: dict[str, QComboBox] = {}
        for row, header in enumerate(self.local_headers):
            self.map_table.setItem(row, 0, QTableWidgetItem(header))
            combo = QComboBox()
            combo.addItem("（不映射）", "")
            for field in sorted(self.fields, key=lambda f: str(f.get("fieldTitle") or "")):
                field_id = field.get("fieldID")
                title = field.get("fieldTitle") or field_id
                label = f"{title} [{field_id}]" if titles.get(title, 0) > 1 else title
                combo.addItem(label, field_id)
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
        self.actions = []
        self.execute_btn.setEnabled(False)
        self.summary_label.setText("映射已修改，请重新生成预览")

    def _refresh_mapping_labels(self) -> None:
        for row, header in enumerate(self.local_headers):
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
            for header in candidates:
                if any(word in header.lower() for word in ("名称", "标题", "name")):
                    index = self.key_combo.findData(header)
                    break
        if index < 0 and candidates:
            index = 0
        self.key_combo.setCurrentIndex(index if index >= 0 else -1)
        self.key_combo.blockSignals(False)

    def auto_match(self, quiet: bool = False) -> None:
        if not (self.local_headers and self.fields):
            if not quiet:
                QMessageBox.information(self, "无法匹配", "请先载入本地文件并连接字段")
            return
        by_title = {}
        for field in self.fields:
            by_title.setdefault(str(field.get("fieldTitle") or "").strip().lower(), field)
        matched = 0
        for header in self.local_headers:
            combo = self.map_combos.get(header)
            if combo is None:
                continue
            field = by_title.get(header.strip().lower())
            if field is None:
                continue
            index = combo.findData(field.get("fieldID"))
            if index >= 0:
                combo.setCurrentIndex(index)
                matched += 1
        self._rebuild_key_combo()
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
        self.actions = []
        self.execute_btn.setEnabled(False)
        self.summary_label.setText("已清空映射")

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
        self.log(f"映射已保存：{path}（不含凭证）")

    def load_mapping_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "载入映射", "", "JSON (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        self.local_path = path
        self.local_headers, self.local_rows = headers, rows
        self.local_label.setText(f"{path}（{len(rows)} 行 × {len(headers)} 列）")
        self._render_local_table()
        self.actions = []
        self.execute_btn.setEnabled(False)
        self.summary_label.setText("本地数据已载入，请生成预览")
        self.log(f"载入本地文件：{path}，{len(rows)} 行，{len(headers)} 列")
        self._rebuild_mapping_table()
        self.auto_match(quiet=True)

    def _render_local_table(self) -> None:
        self.local_table.setColumnCount(len(self.local_headers))
        self.local_table.setHorizontalHeaderLabels(self.local_headers)
        preview = self.local_rows[:100]
        self.local_table.setRowCount(len(preview))
        for row, record in enumerate(preview):
            for col, header in enumerate(self.local_headers):
                self.local_table.setItem(
                    row, col, QTableWidgetItem(str(record.get(header, ""))))
        self.local_table.resizeColumnsToContents()
        self.local_table.horizontalHeader().setStretchLastSection(True)

    # ==================== 预览 / 写入 ====================

    def _writable_actions(self) -> list[dict]:
        return [a for a in self.actions if a.get("op") in ("新增", "更新")]

    def _validate_ready(self) -> bool:
        if not (self.client and self.file_id and self.sheet_combo.currentData()):
            QMessageBox.warning(self, "未连接", "请先连接当前文档并选择子表")
            return False
        if not self.local_headers:
            QMessageBox.warning(self, "没有本地数据", "请先选择 CSV / Excel / JSON 文件")
            return False
        if not self.mapping:
            QMessageBox.warning(self, "没有映射", "请至少映射一列")
            return False
        if not self.key_combo.currentData():
            QMessageBox.warning(self, "没有主键", "请选择用于匹配记录的主键列")
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

        def work():
            records = client.get_records(
                file_id, sheet_id, lambda t: post_to_main(self.log, t))
            actions = openapi.build_actions(
                headers, rows, mapping, field_by_id, key_col, records)
            return actions, records

        def done(result):
            if (self.client is not client or self.file_id != file_id
                    or self.sheet_combo.currentData() != sheet_id):
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
            self.execute_btn.setEnabled(bool(self._writable_actions()))
            self.log(f"预览完成：{self.summary_label.text()}")

        self._run(work, done, "正在读取腾讯记录并计算差异…")

    def _render_diff(self) -> None:
        from PySide6.QtGui import QColor
        self.diff_table.setRowCount(0)
        preview = self.actions[:500]
        self.diff_table.setRowCount(len(preview))
        colors = {
            "新增": QColor("#e9f8ef"), "更新": QColor("#eaf1ff"),
            "不变": QColor("#f4f6f9"), "错误": QColor("#fdecec"),
        }
        for row, action in enumerate(preview):
            values = [str(action.get("row_no", "")), action.get("op", ""),
                      str(action.get("key", "")), action.get("message", "")]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if action.get("op") in colors:
                    item.setBackground(colors[action["op"]])
                self.diff_table.setItem(row, col, item)
        self.diff_table.setColumnWidth(0, 60)
        self.diff_table.setColumnWidth(1, 70)
        self.diff_table.setColumnWidth(2, 180)
        self.diff_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    def on_execute(self) -> None:
        if not self._validate_ready():
            return
        writable = self._writable_actions()
        if not writable:
            QMessageBox.information(self, "没有可写数据", "请先生成预览，且没有新增/更新项")
            return
        counts: dict[str, int] = {}
        for action in writable:
            counts[action["op"]] = counts.get(action["op"], 0) + 1
        answer = QMessageBox.question(
            self, "确认写入",
            f"将向腾讯文档写入：新增 {counts.get('新增', 0)} 条，"
            f"更新 {counts.get('更新', 0)} 条。\n不会删除腾讯表格中的记录。继续？")
        if answer != QMessageBox.Yes:
            return
        client = self.client
        file_id = self.file_id
        sheet_id = self.sheet_combo.currentData()
        key_col = self.key_combo.currentData()
        mapping = dict(self.mapping)
        field_by_id = dict(self.field_by_id)
        headers = list(self.local_headers)
        rows = list(self.local_rows)

        def work():
            records = client.get_records(
                file_id, sheet_id, lambda t: post_to_main(self.log, t))
            actions = openapi.build_actions(
                headers, rows, mapping, field_by_id, key_col, records)
            result = openapi.execute_actions(
                client, file_id, sheet_id, actions,
                lambda t: post_to_main(self.log, t))
            return result

        def done(result):
            self.log(f"写入完成：新增 {result.get('added', 0)}，更新 {result.get('updated', 0)}")
            QMessageBox.information(
                self, "写入完成",
                f"新增 {result.get('added', 0)} 条，更新 {result.get('updated', 0)} 条。\n"
                "正在重新生成预览…")
            if (self.client is client and self.file_id == file_id
                    and self.sheet_combo.currentData() == sheet_id):
                self.on_preview()

        self._run(work, done, "正在写入腾讯文档…")
