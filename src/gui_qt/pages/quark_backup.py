# -*- coding: utf-8 -*-
"""全局设置中的网盘备份面板。

把本地目录按「增量追加」备份到夸克网盘的指定文件夹（云端多余文件不删）：

- 映射列表：多组「本地目录 → 云端文件夹」，可逐条启用 / 编辑 / 重传 / 删除
- 立即备份：与定时任务同一条引擎（`toolbox.quark.backup`），带进度与日志
- 计划任务统一在「任务」页面配置，本页只执行立即备份

登录状态由软件统一管理，本页不保存任何凭据。
视觉全部使用主题语义角色与变体属性（ui-visual-spec），不写死颜色。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import icon_button, post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ElidedLabel, ToolSectionCard
from toolbox.quark import backup as qbackup
from toolbox.quark import config as qconfig
from toolbox.quark import state as qstate


class MappingDialog(QDialog):
    """新增 / 编辑一条「本地目录 → 云端文件夹」映射。"""

    def __init__(self, parent=None, mapping: qconfig.Mapping | None = None):
        super().__init__(parent)
        self.setWindowTitle("网盘备份 · 备份目录")
        self.setMinimumWidth(520)
        self._mapping = mapping or qconfig.Mapping()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("备份到哪个网盘"))
        self.provider_combo = QComboBox()
        for key, label in qconfig.PROVIDERS.items():
            self.provider_combo.addItem(label, key)
        index = self.provider_combo.findData(self._mapping.provider or "quark")
        self.provider_combo.setCurrentIndex(max(0, index))
        self.provider_combo.setToolTip("同一份本地目录可以分别备份到不同网盘，各建一条映射")
        layout.addWidget(self.provider_combo)
        self.provider_hint = QLabel("")
        self.provider_hint.setProperty("role", "caption")
        self.provider_hint.setWordWrap(True)
        layout.addWidget(self.provider_hint)
        self.provider_combo.currentIndexChanged.connect(self._sync_provider)
        self._sync_provider()

        layout.addWidget(QLabel("本地目录"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.local_edit = QLineEdit(self._mapping.local_dir)
        self.local_edit.setPlaceholderText("选择要备份的本地文件夹")
        row.addWidget(self.local_edit, 1)
        browse = QPushButton("选择")
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

        share_row = QHBoxLayout()
        share_row.setSpacing(6)
        self.share_cb = QCheckBox("备份后给本次上传的文件夹生成分享链接")
        self.share_cb.setChecked(bool(self._mapping.share))
        self.share_cb.setToolTip(
            "本次上传涉及多个子文件夹时，分享它们的共同目录（最外到上面的云端文件夹）。\n"
            "没有新文件上传时不会重复建分享。")
        share_row.addWidget(self.share_cb)
        share_row.addStretch(1)
        share_row.addWidget(QLabel("有效期"))
        self.share_expire = QComboBox()
        self.share_expire.addItems(qconfig.SHARE_EXPIRES)
        self.share_expire.setCurrentText(qconfig.normalize_share_expire(self._mapping.share_expire))
        share_row.addWidget(self.share_expire)
        self.share_passcode = QCheckBox("提取码")
        self.share_passcode.setChecked(bool(self._mapping.share_passcode))
        self.share_passcode.setToolTip("勾选后生成 4 位随机提取码，链接里带 ?pwd=")
        share_row.addWidget(self.share_passcode)
        layout.addLayout(share_row)
        self.share_cb.toggled.connect(self._sync_share)
        self._sync_share(self.share_cb.isChecked())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync_share(self, enabled: bool):
        self.share_expire.setEnabled(bool(enabled))
        self.share_passcode.setEnabled(bool(enabled))

    def _sync_provider(self, _index: int = 0):
        """115 走 OSS 直传，单文件有 5 GB 上限；选中时说清楚，其余网盘无附加限制。"""
        provider = str(self.provider_combo.currentData() or "quark")
        if provider == "115":
            self.provider_hint.setText(
                "115 网盘上传走阿里云 OSS 直传（先试秒传），单文件上限 5 GB。")
        else:
            self.provider_hint.setText("")

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
        mapping.provider = self.provider_combo.currentData() or "quark"
        mapping.local_dir = self.local_edit.text().strip()
        mapping.remote_dir = qconfig.normalize_remote_dir(self.remote_edit.text())
        mapping.exclude = self.exclude_edit.text().strip()
        mapping.max_file_mb = int(self.max_size.value())
        mapping.replace_changed = self.replace_cb.isChecked()
        mapping.enabled = self.enabled_cb.isChecked()
        mapping.share = self.share_cb.isChecked()
        mapping.share_expire = self.share_expire.currentText()
        mapping.share_passcode = self.share_passcode.isChecked()
        return mapping


class QuarkBackupPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._mappings: list[qconfig.Mapping] = []
        self._running = False
        self._stop_event: threading.Event | None = None
        self._task_info: dict = {}
        self._icons: list[tuple[QWidget, str, str]] = []

        root = QVBoxLayout(self)
        # 本面板只嵌入全局设置的备份与恢复，左右留白交给设置卡片
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_toolbar())
        self._body_layout = self._build_body()
        root.addLayout(self._body_layout, 1)
        self.setMinimumHeight(420)

        self.reload()

    def has_active_tasks(self):
        """切离设置时保留正在执行备份的面板。"""
        return self._running

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 760
        direction = QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight
        if self._body_layout.direction() != direction:
            self._body_layout.setDirection(direction)
            self.setMinimumHeight(680 if narrow else 420)

    # ==================== 图标（主题切换后重生成） ====================

    def _register_icon(self, widget: QWidget, name: str, role: str = "button"):
        self._icons.append((widget, name, role))
        self._apply_icon(widget, name, role)

    def _apply_icon(self, widget: QWidget, name: str, role: str):
        widget.setIcon(line_icon(name, self._icon_color(role), 16))
        widget.setIconSize(QSize(16, 16))

    @staticmethod
    def _icon_color(role: str) -> str:
        pal = theme.current_palette()
        if role == "primary":
            return pal.color(QPalette.HighlightedText).name()
        if role == "danger":
            return pal.color(QPalette.BrightText).name()
        return pal.color(QPalette.ButtonText).name()

    def on_theme_changed(self):
        for widget, name, role in self._icons:
            self._apply_icon(widget, name, role)
        # 映射卡片里的编辑/重传/删除图标是建卡时烘进像素的，整表重建
        self._render_list()

    # ==================== 界面搭建 ====================

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("variant", "toolbar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # 第一行：操作按钮靠左，上传并发靠右
        top = QHBoxLayout()
        top.setSpacing(8)

        self.run_btn = QPushButton("立即备份")
        self.run_btn.setProperty("variant", "primary")
        self.run_btn.setToolTip("按当前启用的映射执行一次增量备份（与定时任务同一条链路）")
        self.run_btn.clicked.connect(self._start_backup)
        self._register_icon(self.run_btn, "play", "primary")
        top.addWidget(self.run_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("variant", "secondary")
        self.stop_btn.setToolTip("停止当前正在进行的备份（已上传的文件保留）")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_backup)
        self._register_icon(self.stop_btn, "stop")
        top.addWidget(self.stop_btn)

        self.add_btn = QPushButton("新增备份目录")
        self.add_btn.setProperty("variant", "secondary")
        self.add_btn.setToolTip("添加一组「本地目录 → 云端文件夹」映射")
        self.add_btn.clicked.connect(self._add_mapping)
        self._register_icon(self.add_btn, "plus")
        top.addWidget(self.add_btn)

        top.addStretch(1)
        lanes_label = QLabel("上传并发")
        lanes_label.setProperty("role", "caption")
        top.addWidget(lanes_label, 0, Qt.AlignVCenter)
        self.lanes_combo = QComboBox()
        for lanes in qconfig.UPLOAD_LANES:
            self.lanes_combo.addItem(f"{lanes} 路", lanes)
        index = self.lanes_combo.findData(qconfig.load_upload_lanes())
        self.lanes_combo.setCurrentIndex(max(0, index))
        self.lanes_combo.setFixedWidth(90)
        self.lanes_combo.setToolTip(
            "夸克 / 百度的分片上传路数，2-4 路。\n"
            "移动云盘不走这里：它的 EOS 要求分片按序号顺序上传，乱序会直接报\n"
            "InvalidPartOrder，所以固定为顺序上传；115 为单次直传，同样不受影响。\n"
            "云服务器出网带宽低于 2 Mbps 时建议选 2 路，避免单片超时。\n"
            "修改后在下一次备份生效，断点续传与百度节点回退保持有效。")
        self.lanes_combo.currentIndexChanged.connect(self._save_lanes)
        top.addWidget(self.lanes_combo, 0, Qt.AlignVCenter)
        outer.addLayout(top)

        # 第二行：进度条 + 状态文字。单独占一行，窗口变窄时文件名才有完整宽度，
        # 不会被按钮组挤成「1…中心单机-流程.mp4」这种看不清的形态。
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(16)
        self.progress.setMinimumWidth(120)
        self.progress.setToolTip("当前备份进度")
        bottom.addWidget(self.progress, 1)

        self.progress_label = ElidedLabel("空闲", role="caption")
        # ElidedLabel 默认 Ignored 策略会把最小宽度也忽略掉，进度条会把它挤出容器；
        # 这里改成受控区间：至少 240 放得下「上传中 1/1 · 文件名」的前半段，最多 380。
        self.progress_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Preferred)
        self.progress_label.setMinimumWidth(320)
        self.progress_label.setMaximumWidth(420)
        self.progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.progress_label, 0)
        outer.addLayout(bottom)
        return bar

    def _save_lanes(self, _index: int = 0):
        qconfig.save_upload_lanes(self.lanes_combo.currentData())

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(12)

        left = ToolSectionCard(
            "备份目录", "每一组映射单独判定增量，互不影响；定时备份在「任务」页设置。")
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
        body.addWidget(self._build_log_card(), 2)
        return body

    def _build_log_card(self) -> ToolSectionCard:
        card = ToolSectionCard("本次运行日志", "定时任务日志写入 config/quark_backup_task.log")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setPlaceholderText("点「立即备份」后显示每个文件的处理结果")
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

    def _render_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先脱离父级再排队销毁：deleteLater 要等事件循环，期间旧卡片
                # 仍留在原位，会和重建后的空状态/新卡片叠在一起
                widget.setParent(None)
                widget.deleteLater()
        self.count_label.setText(f"{len(self._mappings)} 组")

        if not self._mappings:
            empty = QLabel("还没有备份目录。点上方「新增备份目录」添加一组："
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

        # 第 1 行：存储后端标签 + 云端目标目录（中间省略，完整值进 tooltip）
        top = QHBoxLayout()
        top.setSpacing(6)
        badge = QLabel(mapping.provider_label())
        badge.setStyleSheet(
            "QLabel { font-size: 11px; color: palette(placeholder-text);"
            " border: 1px solid palette(shadow); border-radius: 4px; padding: 0 6px; }")
        badge.setToolTip("存储后端")
        top.addWidget(badge, 0, Qt.AlignVCenter)
        target = ElidedLabel(mapping.remote_dir or "（未设云端目录）")
        font = target.font()
        font.setBold(True)
        target.setFont(font)
        target.setMinimumWidth(120)
        top.addWidget(target, 1)
        info.addLayout(top)

        # 第 2 行：本地目录
        source = ElidedLabel(f"本地 {mapping.local_dir or '（未选本地目录）'}",
                             role="caption")
        info.addWidget(source)

        # 第 3 行：统计 / 上次结果 / 排除与上限，全部压成一条
        stats = qstate.mapping_stats(mapping.id)
        parts = [f"{stats['files']} 个文件", qbackup.human_size(stats['bytes'])]
        if mapping.last_run:
            parts.append(f"上次 {mapping.last_run}")
        if mapping.last_result:
            parts.append(mapping.last_result)
        if mapping.exclude:
            parts.append(f"排除 {mapping.exclude}")
        if mapping.max_file_mb:
            parts.append(f"单文件上限 {mapping.max_file_mb} MB")
        summary = ElidedLabel(" · ".join(parts),
                              role="danger" if "失败" in (mapping.last_result or "")
                              else "caption")
        info.addWidget(summary)

        if mapping.last_share_url:
            share_line = QHBoxLayout()
            share_line.setSpacing(6)
            link = QLabel("分享：" + mapping.last_share_url)
            link.setProperty("role", "caption")
            link.setWordWrap(True)
            link.setTextInteractionFlags(Qt.TextSelectableByMouse)
            share_line.addWidget(link, 1)
            copy_btn = QPushButton("复制")
            copy_btn.setProperty("variant", "secondary")
            copy_btn.setToolTip("复制最近一次生成的分享链接")
            copy_btn.clicked.connect(
                lambda _=False, url=mapping.last_share_url: self._copy_share(url))
            share_line.addWidget(copy_btn)
            info.addLayout(share_line)

        if not mapping.local_dir:
            warn = QLabel("本地目录未设置，这条映射不会执行")
            warn.setStyleSheet("color: theme(danger);")
            warn.setWordWrap(True)
            info.addWidget(warn)
        layout.addLayout(info, 1)

        icon_color = theme.color("text")
        edit_btn = icon_button("", "编辑这条备份目录", on_click=lambda _=False, m=mapping: self._edit_mapping(m))
        edit_btn.setIcon(line_icon("edit", icon_color, 16))
        layout.addWidget(edit_btn, 0, Qt.AlignTop)
        reset_btn = icon_button("", "清空同步记录，下次备份重新上传全部文件",
                                on_click=lambda _=False, m=mapping: self._reset_mapping(m))
        reset_btn.setIcon(line_icon("refresh", icon_color, 16))
        layout.addWidget(reset_btn, 0, Qt.AlignTop)
        del_btn = icon_button("", "删除这条备份目录（云端文件不受影响）",
                              on_click=lambda _=False, m=mapping: self._delete_mapping(m))
        del_btn.setIcon(line_icon("trash", icon_color, 16))
        layout.addWidget(del_btn, 0, Qt.AlignTop)
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
                "只删除本地配置，云盘里已上传的文件不受影响。"
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
        # 同步记录清掉后，卡片上的「上次结果 / 上次时间 / 分享链接」也要一起清，
        # 否则状态文字会一直停在上一次的上传/失败统计上
        mapping.last_result = ""
        mapping.last_run = ""
        mapping.last_share_url = ""
        qconfig.update_mapping(mapping)
        self.reload()
        self.app.toast(f"已清空 {removed} 条同步记录与状态", "info")

    def _toggle_mapping(self, mapping: qconfig.Mapping, checked: bool):
        mapping.enabled = bool(checked)
        qconfig.update_mapping(mapping)

    # ==================== 立即备份 ====================

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
            shares = [r for r in results if r.share_url]
            for result in shares:
                self._append_log("分享：" + result.share_url +
                                 (f"  提取码：{result.share_passcode}" if result.share_passcode else ""))
            if shares:
                QGuiApplication.clipboard().setText(
                    "\n".join(r.share_url for r in shares))
                self.app.toast(f"已生成 {len(shares)} 条分享链接，已复制到剪贴板", "success")
            if failed:
                self.app.toast(f"备份完成，但有 {len(failed)} 组失败：{failed[0].message}", "error")
            else:
                self.app.toast(
                    f"备份完成：上传 {uploaded} 个文件，{qbackup.human_size(total_bytes)}",
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
            if result.share_url:
                mapping.last_share_url = result.share_url
            qconfig.update_mapping(mapping)
            break

    def _copy_share(self, url: str):
        QGuiApplication.clipboard().setText(url or "")
        self.app.toast("分享链接已复制", "success")

    def _stop_backup(self):
        if self._stop_event is not None:
            self._stop_event.set()
            self.progress_label.setText("正在停止…")
            self.stop_btn.setEnabled(False)

    @staticmethod
    @staticmethod
    def _short_name(name: str, limit: int = 60) -> str:
        name = name or ""
        return name if len(name) <= limit else name[:limit - 1] + "…"

    def _on_progress(self, payload: dict):
        if not isinstance(payload, dict):
            return
        phase = payload.get("phase")
        percent = int(payload.get("percent") or 0)
        self.progress.setValue(max(0, min(100, percent)))
        name = self._short_name(payload.get("name") or "")
        index = int(payload.get("current") or 0)
        total = int(payload.get("total") or 0)
        if phase == "mapping":
            self.progress.setFormat("%p%")
            self.progress_label.setText(f"第 {index + 1}/{total} 组：{name}")
        elif phase == "hash":
            # 序号挪进进度条，标签整段留给文件名，窄窗口下也能看清是哪个文件
            self.progress.setFormat(f"{index + 1}/{total} · %p%")
            self.progress_label.setText(f"校验中 · {name}")
        elif phase == "file":
            self.progress.setFormat(f"{index + 1}/{total} · %p%")
            self.progress_label.setText(f"上传中 · {name}")
        elif phase == "run":
            self.progress.setFormat("%p%")
            self.progress_label.setText(f"{index}/{total} {name}")
        elif phase == "done":
            self.progress.setFormat("%p%")
            self.progress_label.setText("收尾中…")

    def _append_log(self, message: str):
        if not message:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")
