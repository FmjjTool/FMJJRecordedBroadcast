"""音乐工具 · 歌单页：把线下活动曲目编排到网易云 / QQ音乐 / 酷狗。

流程：选来源 → 预览匹配 → 确认写入。
- 来源固定为腾讯文档「线下活动」本地快照，默认选日期最新的活动，
  也可清空、搜索或改选；曲目复用完整歌曲里的官方链接。
- 歌单名默认使用「封茗囧菌数据汇总库-」加活动名，完整名称均可编辑。
- 预览只生成计划、不碰平台；写入前二次确认，逐平台写 journal 支持断点。

凭据只通过统一管理接口只读使用，本页不保存、不刷新、不展示登录内容。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import table_sort
from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ElidedLabel, ToolSectionCard
from music import playlist_service as service
from music.playlist_service import PlaylistWriteError

_COLUMNS = ["写入", "平台", "序号", "歌曲", "歌手", "状态", "匹配歌曲", "匹配歌手", "相似度", "说明"]
_WIDTHS = [44, 56, 40, 170, 100, 60, 160, 100, 52]
# 结果表列下标（勾选列在最前，其余整体右移一位）
_COL_CHECK, _COL_PLATFORM, _COL_INDEX = 0, 1, 2
_COL_SCORE, _COL_NOTE = 8, 9
_STATE_LABELS = {
    "matched": "已匹配",
    "uncertain": "待确认",
    "missing": "未找到",
    "duplicate": "重复",
}

# 默认勾选的目标平台：线下歌单主要落这三个（酷我/5sing 需要时手动勾）
DEFAULT_PLATFORMS = ("netease", "qq", "kugou")


def _writeback_text(receipt) -> str:
    """回写回执 → 界面/log 用的一行说明。"""
    from toolbox.tcdoc import event_playlist

    return event_playlist.summary_text(receipt)


class MusicPlaylistPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._busy = False
        self._cancel = threading.Event()
        self._plans: list[service.PlatformPlan] = []
        self._source_label = ""
        self._event: dict | None = None
        self._events: list[dict] = []
        self._events_loaded = False
        self._setting_event_text = False
        self._report_dir = service.report_dir()
        self._report_paths: list[Path] = []
        self._platform_supported: dict[str, bool] = {}
        # 结果表「写入」列勾选：key -> MatchItem / 复选框单元格（排序会移动单元格，键跟着走）
        self._selection: dict[str, service.MatchItem] = {}
        self._check_cells: dict[str, QTableWidgetItem] = {}
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        content = QVBoxLayout(body)
        content.setContentsMargins(16, 16, 16, 16)
        content.setSpacing(12)

        content.addWidget(self._build_setup_card())
        content.addWidget(self._build_result_card(), 1)
        content.addWidget(self._build_log_card())

    # ---------- 界面 ----------

    def _build_setup_card(self) -> QWidget:
        card = ToolSectionCard(
            "来源与目标",
            "仅使用所选线下活动的登记曲目；优先匹配表内官方歌曲 ID，"
            "缺失平台时才走平台官方搜索。",
            icon="music",
        )
        content = card.content_layout

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(QLabel("来源"))
        source_row.addWidget(QLabel("线下活动曲目"))
        source_row.addWidget(QLabel("活动"))

        # 可输入搜索或点箭头改选；清除文字会同步清空当前活动与预览。
        self.event_box = QComboBox()
        self.event_box.setEditable(True)
        self.event_box.setInsertPolicy(QComboBox.NoInsert)
        self.event_box.setMinimumWidth(160)
        self.event_box.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.event_box.setMinimumContentsLength(8)
        self.event_box.setMinimumHeight(28)
        self.event_box.lineEdit().setClearButtonEnabled(True)
        self.event_box.lineEdit().setPlaceholderText("搜索或选择线下活动")
        self.event_box.lineEdit().textChanged.connect(self._on_event_text_edited)
        self.event_box.activated.connect(self._on_event_activated)
        self.event_box.lineEdit().returnPressed.connect(self._on_event_return)
        source_row.addWidget(self.event_box, 1)
        content.addLayout(source_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("歌单名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setMinimumHeight(28)
        self.name_edit.setMaxLength(service.MAX_PLAYLIST_NAME_LENGTH)
        self.name_edit.setPlaceholderText("输入歌单名称，可修改或删除默认前缀")
        self.name_edit.textChanged.connect(self._invalidate_preview)
        name_row.addWidget(self.name_edit, 1)
        content.addLayout(name_row)

        platform_row = QHBoxLayout()
        platform_row.setSpacing(14)
        platform_row.addWidget(QLabel("目标平台"))
        self._platform_boxes: dict[str, QCheckBox] = {}
        for row in service.capabilities():
            box = QCheckBox(row["name"])
            supported = bool(row["supports_create"]) and bool(row.get("available", True))
            box.setEnabled(supported)
            note = row.get("note") or ""
            if not supported:
                note = note or "该平台没有公开的歌单创建接口"
            box.setToolTip(note)
            if supported and row["platform"] in DEFAULT_PLATFORMS:
                box.setChecked(True)
            self._platform_boxes[row["platform"]] = box
            self._platform_supported[row["platform"]] = supported
            platform_row.addWidget(box)
        platform_row.addStretch(1)
        content.addLayout(platform_row)

        self.platform_note = QLabel(self._platform_note_text())
        self.platform_note.setProperty("role", "caption")
        self.platform_note.setWordWrap(True)
        content.addWidget(self.platform_note)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.include_uncertain = QCheckBox("同时加入待确认歌曲")
        self.include_uncertain.setToolTip(
            "相似度不足的候选通常只是同名翻唱或伴奏，默认不写入；\n"
            "勾上后结果表里「待确认」的行会全部打勾，仍可逐首取消")
        self.include_uncertain.toggled.connect(self._on_include_uncertain_changed)
        action_row.addWidget(self.include_uncertain)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        action_row.addWidget(self.progress)
        action_row.addStretch(1)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(30)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        action_row.addWidget(self.stop_btn)

        self.apply_btn = QPushButton("写入平台")
        self.apply_btn.setMinimumHeight(30)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("先预览匹配，确认结果后再写入")
        self.apply_btn.clicked.connect(self._start_apply)
        action_row.addWidget(self.apply_btn)

        self.preview_btn = QPushButton("预览匹配")
        self.preview_btn.setMinimumHeight(30)
        self.preview_btn.setProperty("variant", "primary")
        self.preview_btn.clicked.connect(self._start_preview)
        action_row.addWidget(self.preview_btn)
        content.addLayout(action_row)

        self.status_label = QLabel("选择线下活动 → 预览匹配 → 确认写入")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        content.addWidget(self.status_label)
        self._ensure_events()
        return card

    def _build_result_card(self) -> QWidget:
        card = ToolSectionCard("匹配结果", icon="list")
        self.summary_label = QLabel("尚未预览")
        self.summary_label.setProperty("role", "muted")
        self.summary_label.setWordWrap(True)
        card.content_layout.addWidget(self.summary_label)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self.selection_label = QLabel("")
        self.selection_label.setProperty("role", "muted")
        pick_row.addWidget(self.selection_label)
        pick_row.addStretch(1)
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setToolTip("勾选已匹配 + 待确认的全部歌曲")
        self.select_all_btn.clicked.connect(lambda: self._bulk_select("all"))
        pick_row.addWidget(self.select_all_btn)
        self.select_matched_btn = QPushButton("只选已匹配")
        self.select_matched_btn.setToolTip("只勾选相似度达标的歌曲，待确认的取消勾选")
        self.select_matched_btn.clicked.connect(lambda: self._bulk_select("matched"))
        pick_row.addWidget(self.select_matched_btn)
        self.select_none_btn = QPushButton("全不选")
        self.select_none_btn.clicked.connect(lambda: self._bulk_select("none"))
        pick_row.addWidget(self.select_none_btn)
        for button in (self.select_all_btn, self.select_matched_btn,
                       self.select_none_btn):
            button.setEnabled(False)
        card.content_layout.addLayout(pick_row)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for index, width in enumerate(_WIDTHS):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            self.table.setColumnWidth(index, width)
        header.setSectionResizeMode(_COL_NOTE, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_check_changed)
        # 序号列 / 勾选列不参与排序：其余列可点击表头升 / 降 / 还原
        self._sorter = table_sort.install(
            self.table, skip_columns=(_COL_CHECK, _COL_INDEX))
        card.content_layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.report_label = ElidedLabel("报告目录：" + str(self._report_dir))
        footer.addWidget(self.report_label, 1)
        self.open_dir_btn = QPushButton("打开报告目录")
        self.open_dir_btn.clicked.connect(self._open_report_dir)
        footer.addWidget(self.open_dir_btn)
        card.content_layout.addLayout(footer)
        return card

    def _build_log_card(self) -> QWidget:
        card = ToolSectionCard("运行日志", icon="pulse")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(96)
        self.log_view.setPlaceholderText("预览 / 写入过程中的进度与失败原因会显示在这里")
        card.content_layout.addWidget(self.log_view)
        return card

    # ---------- 来源 ----------

    def _ensure_events(self):
        """首次构造页面时读取活动；共享服务按日期最新优先返回。"""
        if self._events_loaded:
            return
        try:
            events = service.load_offline_events()
        except OSError as exc:
            QMessageBox.warning(self, "歌单", f"读取线下活动失败：{exc}")
            return
        self._events_loaded = True
        self._events = events
        self._setting_event_text = True
        self.event_box.blockSignals(True)
        self.event_box.clear()
        for event in events:
            self.event_box.addItem(self._event_label(event), event["row_no"])
        self.event_box.setCurrentIndex(0 if events else -1)
        self.event_box.setEditText(
            self._event_label(events[0]) if events else "")
        self.event_box.blockSignals(False)
        self._setting_event_text = False
        completer = self.event_box.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setCompletionMode(QCompleter.PopupCompletion)
        if events:
            self._apply_event(events[0])
        else:
            self.event_box.lineEdit().setPlaceholderText("本地暂无线下活动")
            self.status_label.setText("本地没有线下活动快照，请先同步活动数据")

    @staticmethod
    def _event_label(event: dict) -> str:
        parts = [event.get("name") or "", event.get("date") or "",
                 event.get("city") or "", f"{event.get('song_count') or 0} 首"]
        return " · ".join(part for part in parts if part)

    def _on_event_activated(self, index: int):
        if 0 <= index < len(self._events):
            self._apply_event(self._events[index])
        elif index < 0:
            self._apply_event_by_text()

    def _on_event_return(self):
        self._apply_event_by_text()

    def _on_event_text_edited(self, text: str):
        if self._setting_event_text:
            return
        if self._event and text.strip() == self._event_label(self._event):
            return
        self._event = None
        self.name_edit.clear()
        self.event_box.setToolTip("")
        self._invalidate_preview()

    def _apply_event_by_text(self):
        text = self.event_box.currentText().strip()
        if not text:
            return
        for index, event in enumerate(self._events):
            if self._event_label(event) == text:
                self._apply_event(event)
                return
        lowered = text.lower()
        for event in self._events:
            if lowered in self._event_label(event).lower():
                self._apply_event(event)
                return

    def _apply_event(self, event: dict):
        changed = (self._event or {}).get("row_no") != event.get("row_no")
        self._event = event
        if changed:
            self.name_edit.setText(service.default_playlist_name(event.get("name") or ""))
        index = self.event_box.findData(event.get("row_no"))
        self._setting_event_text = True
        self.event_box.blockSignals(True)
        self.event_box.setCurrentIndex(index)
        self.event_box.setEditText(self._event_label(event))
        self.event_box.setToolTip(self._event_label(event))
        self.event_box.blockSignals(False)
        self._setting_event_text = False
        if changed:
            self._invalidate_preview()

    def _invalidate_preview(self, *_):
        if not hasattr(self, "table"):
            return
        self._plans = []
        self._selection = {}
        self._check_cells = {}
        self._source_label = ""
        self.table.setRowCount(0)
        self.summary_label.setText("尚未预览")
        self.selection_label.setText("请先选择线下活动并预览匹配")
        self.apply_btn.setEnabled(False)
        for button in (self.select_all_btn, self.select_matched_btn,
                       self.select_none_btn):
            button.setEnabled(False)

    def _platform_note_text(self) -> str:
        parts = []
        for row in service.capabilities():
            if not row["supports_create"] or not row.get("available", True):
                parts.append(f"{row['name']}：{row.get('note') or '暂不支持创建歌单'}")
            elif row["experimental"]:
                parts.append(f"{row['name']}：实验接口，可能失败（{row.get('note')}）")
            elif row["platform"] == "kugou":
                parts.append("酷狗：云歌单需要有效登录状态，未登录时跳过该平台")
        netease_slot = service.login_slot_label("netease")
        if netease_slot:
            parts.append(f"网易云建单账号：{netease_slot}")
        return "；".join(parts)

    def _target_platforms(self) -> list[str]:
        return [platform for platform, box in self._platform_boxes.items()
                if box.isChecked() and box.isEnabled()]

    # ---------- 预览 ----------

    def _load_sources(self):
        if not self._event:
            raise ValueError("请先选择一场线下活动")
        return service.read_source_event(self._event["row_no"])

    def _start_preview(self):
        if self._busy:
            return
        targets = self._target_platforms()
        if not targets:
            QMessageBox.information(self, "歌单", "请至少选择一个目标平台")
            return
        if not self._event:
            QMessageBox.information(self, "歌单", "请先选择一场线下活动")
            return
        try:
            name = service.validate_playlist_name(self.name_edit.text())
        except ValueError as exc:
            QMessageBox.information(self, "歌单", str(exc))
            return

        self._cancel.clear()
        self._plans = []
        self._report_paths = []
        self._selection = {}
        self._check_cells = {}
        self.table.setRowCount(0)
        self.selection_label.setText("正在匹配…")
        self.summary_label.setText("正在读取来源…")
        self.log_view.clear()
        self.apply_btn.setEnabled(False)
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self._log("开始读取来源…")

        def work():
            sources, label = self._load_sources()
            plans = []
            for platform in targets:
                if self._cancel.is_set():
                    break
                post_to_main(self._log,
                             f"正在匹配 {service.PLATFORM_NAMES.get(platform, platform)}…")
                plan = service.build_plan(
                    platform, sources, name,
                    progress=lambda index, total, source, item, p=platform:
                        post_to_main(self._on_match_progress, p, index, total, item),
                    cancel=self._cancel,
                )
                plan.source_label = label
                plans.append(plan)
            return label, len(sources), plans

        run_bg(work, self._on_preview_done, owner=self)

    def _on_match_progress(self, platform: str, index: int, total: int, item):
        label = service.PLATFORM_NAMES.get(platform, platform)
        self.status_label.setText(
            f"{label} 匹配中 {index}/{total}：{item.source.title}"
            f"（{_STATE_LABELS.get(item.status, item.status)}）")

    def _on_preview_done(self, result, err):
        self.progress.setRange(0, 100)
        self._set_busy(False)
        if err:
            self.summary_label.setText("预览失败")
            self.status_label.setText(err)
            self._log(f"预览失败：{err}")
            return
        label, source_count, plans = result
        self._source_label = label
        self._plans = plans
        self._fill_table(plans)
        self._write_preview_report(plans)

        if not plans:
            self.summary_label.setText("已停止，未生成匹配结果")
            return
        total_matched = sum(plan.counts()["matched"] for plan in plans)
        self.summary_label.setText(
            f"来源：{label}；已匹配 {total_matched} 首，"
            "可在「写入」列逐首勾选后确认写入")
        self.status_label.setText(
            f"预览完成：{source_count} 首歌，{len(plans)} 个平台"
            + ("；已停止，部分平台未匹配" if self._cancel.is_set() else ""))
        self._log(f"预览完成，报告：{self._report_paths[-1] if self._report_paths else '—'}")
        self.progress.setValue(100)
        self._refresh_selection()

    def _fill_table(self, plans):
        include_uncertain = self.include_uncertain.isChecked()
        self._selection = {}
        self._check_cells = {}
        rows = []
        for plan in plans:
            for index, item in enumerate(plan.items, 1):
                key = f"{plan.platform}#{index}"
                self._selection[key] = item
                # 默认勾选落成显式状态，用户随后逐首勾选 / 取消都以此为准
                item.selected = item.is_selected(include_uncertain)
                rows.append((key, plan.platform_name, index, item))

        self._filling = True
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for row_index, (key, platform_name, index, item) in enumerate(rows):
                target = item.target
                cell = self._check_cell(key, item)
                self.table.setItem(row_index, _COL_CHECK, cell)
                if item.selectable:
                    self._check_cells[key] = cell
                values = [
                    platform_name,
                    str(index),
                    item.source.title,
                    item.source.artist or "—",
                    _STATE_LABELS.get(item.status, item.status),
                    (target.song_name or target.title) if target else "—",
                    (target.singer or "—") if target else "—",
                    f"{item.score:.2f}" if target else "—",
                    item.note,
                ]
                for offset, text in enumerate(values):
                    col = _COL_PLATFORM + offset
                    body = QTableWidgetItem(text)
                    if col in (_COL_INDEX, _COL_SCORE):
                        body.setTextAlignment(Qt.AlignCenter)
                    if col == _COL_NOTE:
                        body.setToolTip(text)
                    self.table.setItem(row_index, col, body)
        finally:
            self.table.setUpdatesEnabled(True)
            self._filling = False
        self._refresh_selection()

    def _check_cell(self, key: str, item) -> QTableWidgetItem:
        """「写入」列：能写入的行给复选框，其余留空说明原因。"""
        cell = QTableWidgetItem()
        if not item.selectable:
            cell.setText("—")
            cell.setTextAlignment(Qt.AlignCenter)
            cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            cell.setToolTip("未找到平台曲目或重复，无法写入")
            return cell
        cell.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                      | Qt.ItemIsSelectable)
        # 用键定位 MatchItem：表头排序后单元格会换行，键跟着单元格走
        cell.setData(Qt.UserRole, key)
        cell.setCheckState(Qt.Checked if item.selected else Qt.Unchecked)
        cell.setToolTip("勾选＝写入这首歌，取消勾选＝这次跳过")
        return cell

    def _on_check_changed(self, cell: QTableWidgetItem):
        if self._filling or cell.column() != _COL_CHECK:
            return
        key = str(cell.data(Qt.UserRole) or "")
        item = self._selection.get(key)
        if item is None or not item.selectable:
            return
        item.selected = cell.checkState() == Qt.Checked
        self._refresh_selection()

    def _on_include_uncertain_changed(self, checked: bool):
        """顶部开关同步到结果表：待确认的行整批勾上 / 取消。"""
        if not self._selection or self._filling:
            return
        self._filling = True
        try:
            for key, item in self._selection.items():
                if item.status != "uncertain" or not item.selectable:
                    continue
                item.selected = bool(checked)
                cell = self._check_cells.get(key)
                if cell is not None:
                    cell.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self._filling = False
        self._refresh_selection()

    def _bulk_select(self, mode: str):
        if not self._selection:
            return
        self._filling = True
        try:
            for key, item in self._selection.items():
                if not item.selectable:
                    continue
                if mode == "all":
                    item.selected = True
                elif mode == "matched":
                    item.selected = item.status == "matched"
                else:
                    item.selected = False
                cell = self._check_cells.get(key)
                if cell is not None:
                    cell.setCheckState(
                        Qt.Checked if item.selected else Qt.Unchecked)
        finally:
            self._filling = False
        self._refresh_selection()

    def _chosen_plans(self) -> list[service.PlatformPlan]:
        """当前勾选中有歌要写的平台计划（未勾选任何歌的平台不参与写入）。"""
        return [plan for plan in self._plans if plan.selected()]

    def _refresh_selection(self):
        chosen = sum(plan.selected_count() for plan in self._plans) if self._plans else 0
        total = sum(len(plan.items) for plan in self._plans) if self._plans else 0
        if not self._plans:
            self.selection_label.setText("预览后可在「写入」列逐首勾选")
        else:
            self.selection_label.setText(
                f"已勾选 {chosen} / {total} 首写入（「写入」列可逐首勾选）")
        if not self._busy:
            self.apply_btn.setEnabled(bool(chosen))
            for button in (self.select_all_btn, self.select_matched_btn,
                           self.select_none_btn):
                button.setEnabled(bool(self._plans))

    def _write_preview_report(self, plans):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._report_dir / f"preview_{stamp}.json"
        try:
            service.write_report(path, {
                "mode": "preview",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": self._source_label,
                "plans": [plan.to_dict() for plan in plans],
            })
        except OSError as exc:
            self._log(f"预览报告写入失败：{exc}")
            return
        self._report_paths.append(path)

    # ---------- 写入 ----------

    def _start_apply(self):
        if self._busy or not self._plans:
            return
        targets = set(self._target_platforms())
        plans = [plan for plan in self._chosen_plans() if plan.platform in targets]
        if not plans:
            QMessageBox.information(
                self, "歌单", "还没有勾选任何要写入的歌曲，请先在「写入」列勾选")
            return

        lines = []
        for plan in plans:
            counts = plan.counts()
            chosen = plan.selected_count()
            lines.append(f"{plan.platform_name}：勾选写入 {chosen} 首"
                         f"（另有待确认 {counts['uncertain']} 首、"
                         f"未找到 {counts['missing']} 首未勾选）")
        detail = "\n".join(lines)
        if any(plan.platform == "kugou" for plan in plans):
            detail += ("\n\n酷狗云歌单需要有效登录状态；"
                       "未登录时该平台会提示登录，不影响其他平台写入。")
        detail += "\n\n写完会把各平台歌单链接回填到腾讯文档「活动歌单_*」三列。"
        answer = QMessageBox.question(
            self, "确认写入歌单",
            f"歌单名称：{plans[0].playlist_name}\n\n{detail}\n\n"
            "写入会在各平台创建同名歌单并逐首添加；确认继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        self._cancel.clear()
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("正在写入平台…")
        self._log("开始写入平台…")
        # 逐首勾选已经在结果表里落成显式状态，写入完全以勾选为准
        include_uncertain = False
        # 写完平台歌单后，按原有链路回填腾讯文档「活动歌单_*」三列。
        event = dict(self._event) if self._event else None

        def work():
            results = []
            for plan in plans:
                if self._cancel.is_set():
                    break
                post_to_main(self._log, f"写入 {plan.platform_name}：{plan.playlist_name}")
                try:
                    cookie = service.get_login_jar(plan.platform)
                except PlaylistWriteError as exc:
                    results.append({
                        "platform": plan.platform,
                        "platform_name": plan.platform_name,
                        "playlist_name": plan.playlist_name,
                        "stopped": str(exc),
                    })
                    post_to_main(self._log, f"{plan.platform_name} 跳过：{exc}")
                    continue
                slot_note = service.login_slot_label(plan.platform)
                if slot_note:
                    post_to_main(self._log, f"{plan.platform_name} 建单账号：{slot_note}")
                journal = self._report_dir / f"journal_{plan.platform}.json"
                try:
                    result = service.apply_plan(
                        plan, cookie,
                        include_uncertain=include_uncertain,
                        journal_path=journal,
                        cancel=self._cancel,
                        on_progress=lambda done, total, message, p=plan.platform_name:
                            post_to_main(self._on_add_progress, p, done, total, message),
                    )
                except PlaylistWriteError as exc:
                    results.append({
                        "platform": plan.platform,
                        "platform_name": plan.platform_name,
                        "playlist_name": plan.playlist_name,
                        "stopped": str(exc),
                    })
                    post_to_main(self._log, f"{plan.platform_name} 失败：{exc}")
                    continue
                data = result.to_dict()
                data["platform_name"] = plan.platform_name
                results.append(data)

            writeback = None
            if event and results:
                post_to_main(self._log, "正在回写活动歌单链接…")
                try:
                    from toolbox.tcdoc import event_playlist

                    writeback = event_playlist.write_results(event, results)
                    post_to_main(self._log, _writeback_text(writeback))
                except Exception as exc:  # noqa: BLE001 - 回写失败不影响歌单已创建
                    writeback = {"ok": False, "items": [],
                                 "message": f"回写失败：{exc}"}
                    post_to_main(self._log, f"活动歌单回写失败：{exc}")
            return results, writeback

        run_bg(work, self._on_apply_done, owner=self)

    def _on_add_progress(self, platform_name: str, done: int, total: int, message: str):
        self.status_label.setText(f"{platform_name} 写入中 {done}/{total}：{message}")

    def _on_apply_done(self, result, err):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._set_busy(False)
        if err:
            self.status_label.setText(err)
            self._log(f"写入失败：{err}")
            return
        results, writeback = result

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._report_dir / f"result_{stamp}.json"
        try:
            service.write_report(path, {
                "mode": "apply",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": self._source_label,
                "results": results,
                "writeback": writeback,
            })
            self._report_paths.append(path)
        except OSError as exc:
            self._log(f"执行报告写入失败：{exc}")

        lines = []
        for item in results:
            name = item.get("platform_name") or item.get("platform", "")
            if item.get("stopped"):
                lines.append(f"{name}：{item['stopped']}")
                continue
            lines.append(
                f"{name}：已添加 {len(item.get('added') or [])} 首，"
                f"失败 {len(item.get('failed') or [])} 首，"
                f"跳过 {len(item.get('skipped') or [])} 首"
                + ("（复用同名歌单，未新建）" if item.get("reused") else ""))
        if writeback is not None:
            lines.append(_writeback_text(writeback))
        text = "\n".join(lines) or "没有写入任何平台"
        self._log(text)
        self.status_label.setText("写入流程结束")
        QMessageBox.information(self, "歌单", text)

    # ---------- 公共 ----------

    def _stop(self):
        self._cancel.set()
        self.status_label.setText("正在停止…当前批次结束后不再继续")
        self._log("已请求停止")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.progress.setVisible(busy)
        self.preview_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.event_box.setEnabled(not busy)
        self.name_edit.setEnabled(not busy)
        for platform, box in self._platform_boxes.items():
            box.setEnabled(not busy and self._platform_supported.get(platform, False))
        for button in (self.select_all_btn, self.select_matched_btn,
                       self.select_none_btn):
            button.setEnabled(not busy and bool(self._plans))
        self.apply_btn.setEnabled(
            not busy and bool(self._plans) and any(
                plan.selected_count() for plan in self._plans))

    def _log(self, text: str):
        if not text:
            return
        self.log_view.appendPlainText(text)

    def _open_report_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_dir)))
