# -*- coding: utf-8 -*-
"""夸克网盘备份页。

把本地目录按「增量追加」备份到夸克网盘的指定文件夹（云端多余文件不删）：

- 映射列表：多组「本地目录 → 云端文件夹」，可逐条启用 / 编辑 / 重传 / 删除
- 计划：每天或按间隔，注册 Windows 计划任务 `FengMingQuarkBackup`
- 立即备份：与定时任务同一条引擎（`toolbox.quark.backup`），带进度与日志

登录走「Cookie 管理 → 夸克网盘」（项目约定 5），本页不保存任何凭据。
视觉全部使用主题语义角色与变体属性（ui-visual-spec），不写死颜色。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import icon_button, post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolPageHeader, ToolSectionCard
from toolbox import quark_backup_task
from toolbox.quark import backup as qbackup
from toolbox.quark import config as qconfig
from toolbox.quark import state as qstate


class MappingDialog(QDialog):
    """新增 / 编辑一条「本地目录 → 云端文件夹」映射。"""

    def __init__(self, parent=None, mapping: qconfig.Mapping | None = None):
        super().__init__(parent)
        self.setWindowTitle("夸克网盘备份 · 备份目录")
        self.setMinimumWidth(520)
        self._mapping = mapping or qconfig.Mapping()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("本地目录"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.local_edit = QLineEdit(self._mapping.local_dir)
        self.local_edit.setPlaceholderText("选择要备份的本地文件夹")
        row.addWidget(self.local_edit, 1)
        browse = QPushButton("📁 选择")
        browse.setProperty("variant", "secondary")
        browse.setToolTip("选择本地要备份的文件夹")
        browse.clicked.connect(self._pick_dir)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addWidget(QLabel("云端文件夹"))
        self.remote_edit = QLineEdit(self._mapping.remote_dir)
        self.remote_edit.setPlaceholderText("例如：备份/录播素材（不存在会自动创建）")
        layout.addWidget(self.remote_edit)

        hint = QLabel("云端路径相对网盘根目录，可用 / 分层；留空表示根目录。")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(QLabel("排除规则"))
        self.exclude_edit = QLineEdit(self._mapping.exclude)
        self.exclude_edit.setPlaceholderText("分号分隔的 glob，例如：*.tmp; cache/*; 临时/*")
        layout.addWidget(self.exclude_edit)

        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        size_row.addWidget(QLabel("单文件上限"))
        self.max_size = QSpinBox()
        self.max_size.setRange(0, 102400)
        self.max_size.setValue(int(self._mapping.max_file_mb or 0))
        self.max_size.setSuffix(" MB")
        self.max_size.setFixedWidth(120)
        self.max_size.setToolTip("超过该大小的文件跳过不传；0 表示不限")
        size_row.addWidget(self.max_size)
        size_row.addSpacing(8)
        self.enabled_cb = QCheckBox("启用这条备份")
        self.enabled_cb.setChecked(bool(self._mapping.enabled))
        size_row.addWidget(self.enabled_cb)
        size_row.addStretch(1)
        layout.addLayout(size_row)

        self.replace_cb = QCheckBox("文件内容变化时覆盖云端旧版本")
        self.replace_cb.setChecked(bool(self._mapping.replace_changed))
        self.replace_cb.setToolTip(
            "开启：本地文件改了，先删掉本工具上次上传的那一份再传新版本（只删自己传的文件）。\n"
            "关闭：只追加不上删，云端会保留旧版本并生成 a(1).txt 这类副本。")
        layout.addWidget(self.replace_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _pick_dir(self):
        start = self.local_edit.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(self, "选择要备份的本地文件夹", start)
        if chosen:
            self.local_edit.setText(chosen)

    def _on_ok(self):
        if not self.local_edit.text().strip():
            QMessageBox.warning(self, "缺少本地目录", "请先选择要备份的本地文件夹。")
            return
        self.accept()

    def result_mapping(self) -> qconfig.Mapping:
        mapping = self._mapping
        mapping.local_dir = self.local_edit.text().strip()
        mapping.remote_dir = qconfig.normalize_remote_dir(self.remote_edit.text())
        mapping.exclude = self.exclude_edit.text().strip()
        mapping.max_file_mb = int(self.max_size.value())
        mapping.replace_changed = self.replace_cb.isChecked()
        mapping.enabled = self.enabled_cb.isChecked()
        return mapping


class QuarkBackupPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._mappings: list[qconfig.Mapping] = []
        self._running = False
        self._stop_event: threading.Event | None = None
        self._task_info: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(ToolPageHeader(
            "夸克网盘备份",
            "把本地目录增量备份到夸克网盘指定文件夹：只传新增与变化的文件，"
            "云端多余文件一律不删。登录在「Cookie 管理 → 夸克网盘」完成。"))

        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_body(), 1)

        self.reload()

    # ==================== 界面搭建 ====================

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("variant", "toolbar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.run_btn = QPushButton("📥 立即备份")
        self.run_btn.setProperty("variant", "primary")
        self.run_btn.setToolTip("按当前启用的映射执行一次增量备份（与定时任务同一条链路）")
        self.run_btn.clicked.connect(self._start_backup)
        row.addWidget(self.run_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setProperty("variant", "secondary")
        self.stop_btn.setToolTip("停止当前正在进行的备份（已上传的文件保留）")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_backup)
        row.addWidget(self.stop_btn)

        self.add_btn = QPushButton("＋ 新增备份目录")
        self.add_btn.setProperty("variant", "secondary")
        self.add_btn.setToolTip("添加一组「本地目录 → 云端文件夹」映射")
        self.add_btn.clicked.connect(self._add_mapping)
        row.addWidget(self.add_btn)
        row.addStretch(1)

        self.login_btn = QPushButton("🔑 打开 Cookie 管理")
        self.login_btn.setProperty("variant", "ghost")
        self.login_btn.setToolTip("夸克网盘登录 / 重新登录（统一由 Cookie 管理负责）")
        self.login_btn.clicked.connect(self._open_cookie_manager)
        row.addWidget(self.login_btn)
        layout.addLayout(row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(16)
        progress_row.addWidget(self.progress, 1)
        self.progress_label = QLabel("空闲")
        self.progress_label.setProperty("role", "caption")
        self.progress_label.setMinimumWidth(220)
        self.progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)
        return bar

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(12)

        left = ToolSectionCard("🗂 备份目录", "每一组映射单独判定增量，互不影响。")
        self.count_label = QLabel("0 组")
        self.count_label.setProperty("role", "caption")
        left.heading_layout.addWidget(self.count_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        left.content_layout.addWidget(self.scroll, 1)
        body.addWidget(left, 3)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_schedule_card())
        right.addWidget(self._build_log_card(), 1)
        wrapper = QWidget()
        wrapper.setLayout(right)
        right.setContentsMargins(0, 0, 0, 0)
        body.addWidget(wrapper, 2)
        return body

    def _build_schedule_card(self) -> ToolSectionCard:
        card = ToolSectionCard("🗓 计划", "注册 Windows 计划任务后，关闭软件也会按计划自动备份。")
        self.task_status = QLabel("未读取计划任务状态")
        self.task_status.setProperty("role", "caption")
        self.task_status.setWordWrap(True)
        card.heading_layout.addWidget(self.task_status)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("方式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("每天", "daily")
        self.mode_combo.addItem("按间隔", "interval")
        self.mode_combo.setFixedWidth(96)
        self.mode_combo.currentIndexChanged.connect(self._sync_mode_visibility)
        row.addWidget(self.mode_combo)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setFixedWidth(84)
        self.time_edit.setToolTip("每天自动备份的时间点")
        row.addWidget(self.time_edit)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 1439)
        self.interval_spin.setSuffix(" 分钟")
        self.interval_spin.setFixedWidth(110)
        self.interval_spin.setToolTip("每隔多少分钟备份一次（60 的倍数按小时显示）")
        row.addWidget(self.interval_spin)
        row.addStretch(1)
        card.content_layout.addLayout(row)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.install_btn = QPushButton("✅ 保存并安装")
        self.install_btn.setProperty("variant", "primary")
        self.install_btn.setToolTip("保存计划设置并注册 / 更新 Windows 计划任务")
        self.install_btn.clicked.connect(self._install_schedule)
        buttons.addWidget(self.install_btn)
        self.uninstall_btn = QPushButton("🗑 卸载任务")
        self.uninstall_btn.setProperty("variant", "secondary")
        self.uninstall_btn.setToolTip("删除 Windows 计划任务（删除后关闭软件不再自动备份）")
        self.uninstall_btn.clicked.connect(self._uninstall_schedule)
        buttons.addWidget(self.uninstall_btn)
        buttons.addStretch(1)
        card.content_layout.addLayout(buttons)
        return card

    def _build_log_card(self) -> ToolSectionCard:
        card = ToolSectionCard("📄 本次运行日志", "定时任务的日志另存为 config/quark_backup_task.log。")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setPlaceholderText("点「📥 立即备份」后，这里会显示每个文件的处理结果")
        self.log_view.setStyleSheet(
            'QPlainTextEdit { font-family: Consolas, "Cascadia Mono", monospace;'
            " font-size: 11px; background: palette(base); color: palette(text);"
            " border: 1px solid palette(shadow); border-radius: 8px; padding: 6px; }")
        card.content_layout.addWidget(self.log_view, 1)
        return card

    # ==================== 数据 ====================

    def reload(self):
        self._mappings = qconfig.load_mappings()
        self._render_list()
        self._load_schedule()
        self._refresh_task_status()

    def _render_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.count_label.setText(f"{len(self._mappings)} 组")

        if not self._mappings:
            empty = QLabel("还没有备份目录。点上方「＋ 新增备份目录」添加一组，"
                           "选好本地文件夹与云端目标文件夹即可。")
            empty.setProperty("role", "caption")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignTop)
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            return

        for mapping in self._mappings:
            self.list_layout.addWidget(self._mapping_card(mapping))
        self.list_layout.addStretch(1)

    def _mapping_card(self, mapping: qconfig.Mapping) -> QFrame:
        card = QFrame()
        card.setProperty("variant", "subtle")
        card.setProperty("role", "mappingCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        toggle = QCheckBox()
        toggle.setChecked(bool(mapping.enabled))
        toggle.setToolTip("启用 / 暂停这条备份")
        toggle.toggled.connect(lambda checked, m=mapping: self._toggle_mapping(m, checked))
        layout.addWidget(toggle, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(2)
        title = QLabel(mapping.title())
        title.setProperty("role", "sectionTitle")
        title.setWordWrap(True)
        info.addWidget(title)

        stats = qstate.mapping_stats(mapping.id)
        detail = f"已备份 {stats['files']} 个文件 · {qbackup.human_size(stats['bytes'])}"
        if mapping.exclude:
            detail += f" ｜ 排除：{mapping.exclude}"
        if mapping.max_file_mb:
            detail += f" ｜ 单文件上限 {mapping.max_file_mb} MB"
        detail_label = QLabel(detail)
        detail_label.setProperty("role", "caption")
        detail_label.setWordWrap(True)
        info.addWidget(detail_label)

        if mapping.last_run:
            last = QLabel(f"上次：{mapping.last_run} {mapping.last_result}")
            last.setProperty("role", "caption")
            last.setWordWrap(True)
            info.addWidget(last)

        if not mapping.local_dir:
            warn = QLabel("本地目录未设置，这条映射不会执行")
            warn.setProperty("role", "caption")
            warn.setWordWrap(True)
            info.addWidget(warn)
        layout.addLayout(info, 1)

        layout.addWidget(icon_button("✏", "编辑这条备份目录", on_click=lambda _=False, m=mapping: self._edit_mapping(m)))
        layout.addWidget(icon_button("♻", "清空同步记录，下次备份重新上传全部文件",
                                    on_click=lambda _=False, m=mapping: self._reset_mapping(m)))
        layout.addWidget(icon_button("🗑", "删除这条备份目录（云端文件不受影响）",
                                    on_click=lambda _=False, m=mapping: self._delete_mapping(m)))
        return card

    # ==================== 映射增删改 ====================

    def _add_mapping(self):
        dialog = MappingDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        mapping = dialog.result_mapping()
        qconfig.update_mapping(mapping)
        self.reload()
        self.app.toast(f"已添加备份目录：{mapping.title()}", "success")

    def _edit_mapping(self, mapping: qconfig.Mapping):
        dialog = MappingDialog(self, mapping)
        if dialog.exec() != QDialog.Accepted:
            return
        qconfig.update_mapping(dialog.result_mapping())
        self.reload()

    def _delete_mapping(self, mapping: qconfig.Mapping):
        if QMessageBox.question(
                self, "删除备份目录",
                f"删除这条映射？\n\n{mapping.title()}\n\n"
                "只删除本地配置，夸克网盘里已上传的文件不受影响。"
        ) != QMessageBox.Yes:
            return
        qconfig.delete_mapping(mapping.id)
        self.reload()
        self.app.toast("已删除备份目录", "info")

    def _reset_mapping(self, mapping: qconfig.Mapping):
        if QMessageBox.question(
                self, "清空同步记录",
                f"清空这条映射的同步记录？\n\n{mapping.title()}\n\n"
                "下次备份会把这些文件重新上传一遍（同名文件云端会生成副本）。"
        ) != QMessageBox.Yes:
            return
        removed = qstate.forget_mapping(mapping.id)
        self.reload()
        self.app.toast(f"已清空 {removed} 条同步记录", "info")

    def _toggle_mapping(self, mapping: qconfig.Mapping, checked: bool):
        mapping.enabled = bool(checked)
        qconfig.update_mapping(mapping)

    # ==================== 计划 ====================

    def _load_schedule(self):
        schedule = qconfig.load_schedule()
        index = 0 if schedule["mode"] != "interval" else 1
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(index)
        self.mode_combo.blockSignals(False)
        try:
            hour, minute = [int(x) for x in str(schedule["time"]).split(":")[:2]]
        except (TypeError, ValueError):
            hour, minute = 2, 30
        self.time_edit.setTime(QTime(hour, minute))
        self.interval_spin.setValue(int(schedule["interval_minutes"]))
        self._sync_mode_visibility()

    def _sync_mode_visibility(self):
        daily = self.mode_combo.currentData() == "daily"
        self.time_edit.setVisible(daily)
        self.interval_spin.setVisible(not daily)

    def _current_schedule(self) -> dict:
        return {
            "mode": self.mode_combo.currentData() or "daily",
            "time": self.time_edit.time().toString("HH:mm"),
            "interval_minutes": int(self.interval_spin.value()),
        }

    def _install_schedule(self):
        schedule = self._current_schedule()
        qconfig.save_schedule(schedule["mode"], schedule["time"],
                              schedule["interval_minutes"])
        self.install_btn.setEnabled(False)

        def work():
            return quark_backup_task.apply_schedule(
                schedule["mode"], schedule["time"], schedule["interval_minutes"])

        def done(result, err):
            self.install_btn.setEnabled(True)
            if err:
                self.app.toast(f"安装计划任务失败：{err}", "error")
                return
            ok, message = result
            if ok:
                self.app.toast(f"✅ {message}", "success")
            else:
                QMessageBox.warning(self, "安装计划任务失败", message)
            self._refresh_task_status()

        run_bg(work, done)

    def _uninstall_schedule(self):
        if QMessageBox.question(
                self, "卸载计划任务",
                "删除夸克网盘备份的 Windows 计划任务？\n\n删除后关闭软件就不再自动备份，"
                "可随时回到本页重新安装。"
        ) != QMessageBox.Yes:
            return
        self.uninstall_btn.setEnabled(False)

        def done(result, err):
            self.uninstall_btn.setEnabled(True)
            if err:
                self.app.toast(f"卸载失败：{err}", "error")
                return
            ok, message = result
            self.app.toast(message if ok else f"卸载失败：{message}",
                           "success" if ok else "error")
            self._refresh_task_status()

        run_bg(quark_backup_task.uninstall, done)

    def _refresh_task_status(self):
        def work():
            return quark_backup_task.info()

        def done(data, err):
            if err:
                self.task_status.setText("计划任务状态读取失败")
                return
            if not data:
                self.task_status.setText("○ 定时任务未安装")
                self.uninstall_btn.setEnabled(False)
                return
            nxt = data.get("Next Run Time") or data.get("下次运行时间") or "-"
            self.task_status.setText(f"● 已安装 · 下次运行 {nxt}")
            self.uninstall_btn.setEnabled(True)

        run_bg(work, done)

    # ==================== 立即备份 ====================

    def _open_cookie_manager(self):
        try:
            from gui_qt.cookie_manager import CookieManagerDialog
            CookieManagerDialog(self, on_changed=self.reload).exec()
        except Exception as exc:  # noqa: BLE001 - 管理入口异常不阻断页面
            QMessageBox.warning(self, "打开 Cookie 管理失败", str(exc))

    def _start_backup(self):
        if self._running:
            self.app.toast("备份正在进行中", "info")
            return
        items = [m for m in self._mappings if m.enabled and m.local_dir]
        if not items:
            self.app.toast("没有启用的备份目录，请先新增或启用一条映射", "info")
            return

        self._running = True
        self._stop_event = threading.Event()
        self.run_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)
        self.progress_label.setText("准备中…")
        self._append_log(f"开始备份：{len(items)} 组映射")
        started = time.strftime("%Y-%m-%d %H:%M:%S")

        def work():
            return qbackup.run_all(
                items,
                progress=lambda payload: post_to_main(self._on_progress, payload),
                log=lambda message: post_to_main(self._append_log, message),
                stop_event=self._stop_event)

        def done(results, err):
            self._running = False
            self.run_btn.setEnabled(True)
            self.add_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            if err:
                self.progress_label.setText("失败")
                self._append_log(f"备份异常终止：{err}")
                self.app.toast(f"备份失败：{err}", "error")
                return
            results = results or []
            failed = [r for r in results if not r.ok]
            uploaded = sum(r.uploaded for r in results)
            skipped = sum(r.skipped for r in results)
            total_bytes = sum(r.bytes for r in results)
            self.progress.setValue(100)
            self.progress_label.setText(
                f"完成：上传 {uploaded} · 跳过 {skipped} · {qbackup.human_size(total_bytes)}")
            for result in results:
                self._remember_result(result, started)
            if failed:
                self.app.toast(f"备份完成，但有 {len(failed)} 组失败：{failed[0].message}", "error")
            else:
                self.app.toast(
                    f"✅ 备份完成：上传 {uploaded} 个文件，{qbackup.human_size(total_bytes)}",
                    "success")
            self.reload()

        run_bg(work, done)

    def _remember_result(self, result, started: str):
        for mapping in self._mappings:
            if mapping.id != result.mapping_id:
                continue
            mapping.last_run = started
            mapping.last_result = f"上传 {result.uploaded} · 跳过 {result.skipped}" + (
                f" · 失败 {result.failed}" if result.failed else "")
            qconfig.update_mapping(mapping)
            break

    def _stop_backup(self):
        if self._stop_event is not None:
            self._stop_event.set()
            self.progress_label.setText("正在停止…")
            self.stop_btn.setEnabled(False)

    def _on_progress(self, payload: dict):
        if not isinstance(payload, dict):
            return
        phase = payload.get("phase")
        percent = int(payload.get("percent") or 0)
        self.progress.setValue(max(0, min(100, percent)))
        name = payload.get("name") or ""
        if phase == "mapping":
            index = int(payload.get("index") or 0) + 1
            total = int(payload.get("total") or 1)
            self.progress_label.setText(f"第 {index}/{total} 组：{name}")
        elif phase in ("run", "file"):
            current = int(payload.get("current") or 0)
            total = int(payload.get("total") or 0)
            if total:
                self.progress_label.setText(f"{current}/{total}  {name}")
        elif phase == "done":
            self.progress_label.setText("收尾中…")

    def _append_log(self, message: str):
        if not message:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")
