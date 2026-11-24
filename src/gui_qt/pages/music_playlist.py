"""音乐工具 · 歌单页：把一份歌单编排到网易云 / QQ音乐 / 酷狗。

流程：选来源 → 预览匹配 → 确认写入。
- 默认来源是腾讯文档「完整歌曲」本地快照，表内平台链接是官方歌曲 ID，
  匹配时优先直连，链接缺失的平台才回退平台官方搜索接口。
- 也可选「线下活动曲目」，从线下活动表挑一场，歌单名自动用活动名称，
  曲目复用完整歌曲里的官方链接。
- 也可读取本地歌曲清单（txt / json / csv / xlsx）或已有歌单链接。
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
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import table_sort
from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.pages.music_playlist_dialog import EventPickerDialog
from gui_qt.toolbox_common import ElidedLabel, ToolSectionCard
from music import playlist_service as service
from music.playlist_service import PlaylistWriteError

_COLUMNS = ["平台", "序号", "歌曲", "歌手", "状态", "匹配歌曲", "匹配歌手", "相似度", "说明"]
_WIDTHS = [56, 40, 170, 100, 60, 160, 100, 52]
_STATE_LABELS = {
    "matched": "已匹配",
    "uncertain": "待确认",
    "missing": "未找到",
    "duplicate": "重复",
}

_SOURCE_SNAPSHOT = "snapshot"
_SOURCE_EVENT = "event"
_SOURCE_FILE = "file"
_SOURCE_PLAYLIST = "playlist"


class MusicPlaylistPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._busy = False
        self._cancel = threading.Event()
        self._plans: list[service.PlatformPlan] = []
        self._source_label = ""
        self._event: dict | None = None
        self._report_dir = service.report_dir()
        self._report_paths: list[Path] = []
        self._platform_supported: dict[str, bool] = {}

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
            "「完整歌曲」快照和「线下活动」曲目都用表内官方歌曲 ID 优先直连匹配，"
            "缺失平台才走平台官方搜索；选线下活动时歌单名自动使用活动名称。",
            icon="music",
        )
        content = card.content_layout

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(QLabel("来源"))
        self.source_box = QComboBox()
        self.source_box.addItem("完整歌曲快照", _SOURCE_SNAPSHOT)
        self.source_box.addItem("线下活动曲目", _SOURCE_EVENT)
        self.source_box.addItem("歌曲清单文件", _SOURCE_FILE)
        self.source_box.addItem("现有歌单链接", _SOURCE_PLAYLIST)
        self.source_box.setFixedWidth(150)
        self.source_box.setMinimumHeight(28)
        self.source_box.currentIndexChanged.connect(self._on_source_change)
        source_row.addWidget(self.source_box)

        self.source_input = QLineEdit()
        self.source_input.setMinimumHeight(28)
        self.source_input.setPlaceholderText("使用本地「完整歌曲」快照")
        source_row.addWidget(self.source_input, 1)

        self.pick_btn = QPushButton("选择文件")
        self.pick_btn.setMinimumHeight(28)
        self.pick_btn.setVisible(False)
        self.pick_btn.clicked.connect(self._on_pick)
        source_row.addWidget(self.pick_btn)
        content.addLayout(source_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("歌单名称"))
        self.name_edit = QLineEdit("封茗囧菌歌曲合集")
        self.name_edit.setMinimumHeight(28)
        self.name_edit.setMaxLength(60)
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
            if supported and row["platform"] in ("netease", "qq"):
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
            "相似度不足的候选通常只是同名翻唱或伴奏，默认不写入")
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

        self.status_label = QLabel("选来源 → 预览匹配 → 确认写入")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        content.addWidget(self.status_label)
        self._on_source_change()
        return card

    def _build_result_card(self) -> QWidget:
        card = ToolSectionCard("匹配结果", icon="list")
        self.summary_label = QLabel("尚未预览")
        self.summary_label.setProperty("role", "muted")
        self.summary_label.setWordWrap(True)
        card.content_layout.addWidget(self.summary_label)

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
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        # 序号列不参与排序：其余列可点击表头升 / 降 / 还原
        self._sorter = table_sort.install(self.table, skip_columns=(1,))
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

    def _on_source_change(self, *_):
        mode = self.source_box.currentData()
        is_snapshot = mode == _SOURCE_SNAPSHOT
        is_event = mode == _SOURCE_EVENT
        self.pick_btn.setVisible(mode in (_SOURCE_FILE, _SOURCE_EVENT))
        self.pick_btn.setText("选择活动" if is_event else "选择文件")
        self.source_input.setEnabled(not (is_snapshot or is_event))
        if is_snapshot:
            self.source_input.clear()
            self.source_input.setPlaceholderText("使用本地「完整歌曲」快照")
        elif is_event:
            self.source_input.clear()
            self.source_input.setPlaceholderText("点「选择活动」，歌单名自动使用活动名称")
            if self._event:
                self._show_event(self._event)
        elif mode == _SOURCE_FILE:
            self.source_input.setPlaceholderText("选择 txt / json / csv / xlsx 歌曲清单")
        else:
            self.source_input.setPlaceholderText(
                "粘贴歌单链接或 ID（QQ音乐 / 网易云 / 酷狗 / 酷我 / 5sing）")

    def _show_event(self, event: dict):
        parts = [event.get("date") or "", event.get("city") or ""]
        summary = " · ".join(part for part in parts if part)
        self.source_input.setText(
            f"{summary} · {event.get('song_count') or 0} 首" if summary
            else f"{event.get('song_count') or 0} 首")
        self.name_edit.setText(event.get("name") or self.name_edit.text())

    def _on_pick(self):
        if self.source_box.currentData() == _SOURCE_EVENT:
            self._pick_event()
        else:
            self._pick_file()

    def _pick_event(self):
        try:
            events = service.load_offline_events()
        except OSError as exc:
            QMessageBox.warning(self, "歌单", f"读取线下活动失败：{exc}")
            return
        if not events:
            QMessageBox.information(
                self, "歌单", "本地没有「线下活动」快照，请先在腾讯文档页完成一次同步")
            return
        dialog = EventPickerDialog(events, self)
        if dialog.exec() != EventPickerDialog.Accepted:
            return
        event = dialog.selected_event()
        if not event:
            return
        self._event = event
        self._show_event(event)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择歌曲清单", "",
            "歌曲清单 (*.txt *.json *.csv *.xlsx *.xlsm);;所有文件 (*.*)")
        if path:
            self.source_input.setText(path)

    def _platform_note_text(self) -> str:
        parts = []
        for row in service.capabilities():
            if not row["supports_create"] or not row.get("available", True):
                parts.append(f"{row['name']}：{row.get('note') or '暂不支持创建歌单'}")
            elif row["experimental"]:
                parts.append(f"{row['name']}：实验接口，可能失败（{row.get('note')}）")
            elif row["platform"] == "kugou":
                parts.append("酷狗：云歌单需要 App 扫码令牌，网页登录态写入会失败")
        return "；".join(parts)

    def _target_platforms(self) -> list[str]:
        return [platform for platform, box in self._platform_boxes.items()
                if box.isChecked() and box.isEnabled()]

    # ---------- 预览 ----------

    def _load_sources(self):
        mode = self.source_box.currentData()
        if mode == _SOURCE_SNAPSHOT:
            return service.read_source_complete_songs()
        if mode == _SOURCE_EVENT:
            if not self._event:
                raise ValueError("请先点「选择活动」挑一场线下活动")
            return service.read_source_event(self._event["row_no"])
        value = self.source_input.text().strip()
        if not value:
            raise ValueError("请先填写歌曲清单或歌单链接")
        if mode == _SOURCE_FILE:
            return service.read_source_file(value), f"文件 {Path(value).name}"
        platform = service.detect_platform(value)
        if not platform:
            raise ValueError("无法识别歌单链接所属平台")
        sources, name = service.read_source_playlist(platform, value)
        return sources, f"{service.PLATFORM_NAMES.get(platform, platform)}歌单「{name}」"

    def _start_preview(self):
        if self._busy:
            return
        targets = self._target_platforms()
        if not targets:
            QMessageBox.information(self, "歌单", "请至少选择一个目标平台")
            return
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "歌单", "请填写歌单名称")
            return

        self._cancel.clear()
        self._plans = []
        self._report_paths = []
        self.table.setRowCount(0)
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
            f"来源：{label}；已匹配 {total_matched} 首，确认后可写入")
        self.status_label.setText(
            f"预览完成：{source_count} 首歌，{len(plans)} 个平台"
            + ("；已停止，部分平台未匹配" if self._cancel.is_set() else ""))
        self._log(f"预览完成，报告：{self._report_paths[-1] if self._report_paths else '—'}")
        self.progress.setValue(100)
        self.apply_btn.setEnabled(bool(total_matched))

    def _fill_table(self, plans):
        rows = []
        for plan in plans:
            for index, item in enumerate(plan.items, 1):
                target = item.target
                rows.append([
                    plan.platform_name,
                    str(index),
                    item.source.title,
                    item.source.artist or "—",
                    _STATE_LABELS.get(item.status, item.status),
                    (target.song_name or target.title) if target else "—",
                    (target.singer or "—") if target else "—",
                    f"{item.score:.2f}" if target else "—",
                    item.note,
                ])
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for col, text in enumerate(row):
                    cell = QTableWidgetItem(text)
                    if col in (1, 7):
                        cell.setTextAlignment(Qt.AlignCenter)
                    if col == 8:
                        cell.setToolTip(text)
                    self.table.setItem(row_index, col, cell)
        finally:
            self.table.setUpdatesEnabled(True)

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
        plans = [plan for plan in self._plans if plan.platform in targets]
        if not plans:
            QMessageBox.information(self, "歌单", "当前勾选的平台还没有预览结果，请先预览")
            return

        lines = []
        for plan in plans:
            counts = plan.counts()
            lines.append(f"{plan.platform_name}：写入 {counts['matched']} 首"
                         f"（待确认 {counts['uncertain']} 首、未找到 {counts['missing']} 首不写入）")
        detail = "\n".join(lines)
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
        include_uncertain = self.include_uncertain.isChecked()

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
            return results

        run_bg(work, self._on_apply_done, owner=self)

    def _on_add_progress(self, platform_name: str, done: int, total: int, message: str):
        self.status_label.setText(f"{platform_name} 写入中 {done}/{total}：{message}")

    def _on_apply_done(self, results, err):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._set_busy(False)
        if err:
            self.status_label.setText(err)
            self._log(f"写入失败：{err}")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._report_dir / f"result_{stamp}.json"
        try:
            service.write_report(path, {
                "mode": "apply",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": self._source_label,
                "results": results,
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
                f"跳过 {len(item.get('skipped') or [])} 首")
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
        self.source_box.setEnabled(not busy)
        self.source_input.setEnabled(
            not busy and self.source_box.currentData() not in (_SOURCE_SNAPSHOT, _SOURCE_EVENT))
        self.name_edit.setEnabled(not busy)
        self.pick_btn.setEnabled(not busy)
        for platform, box in self._platform_boxes.items():
            box.setEnabled(not busy and self._platform_supported.get(platform, False))
        self.apply_btn.setEnabled(not busy and bool(self._plans))

    def _log(self, text: str):
        if not text:
            return
        self.log_view.appendPlainText(text)

    def _open_report_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_dir)))
