"""备份与恢复中心：配置备份、资产备份、网盘备份三个入口。

后端只调用现有实现，不复制备份逻辑：
- 配置 / 资产 → `toolbox.backup`（export_config / import_config / export_assets / import_assets）
- 网盘 → 内嵌 `QuarkBackupPage`，映射、立即备份与日志保持原样

计划任务仍统一由「任务」页管理，本页不提供定时设置。
"""

from __future__ import annotations

import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressDialog,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolSectionCard


class _ProgressBridge(QObject):
    """工作线程 → GUI 线程的进度信号桥（queued connection 保证线程安全）。"""

    updated = Signal(int, int, str)

    def emit_progress(self, done: int, total: int, name: str):
        self.updated.emit(done, total, name)


def _make_progress_dlg(parent, title: str, total: int, cancel_event: threading.Event):
    """进度对话框：显示 N/M + 当前文件名，可取消。"""
    dlg = QProgressDialog("准备中...", "取消", 0, max(total, 1), parent)
    dlg.setWindowTitle(title)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setMinimumDuration(0)
    dlg.setMinimumWidth(420)
    dlg.canceled.connect(cancel_event.set)
    dlg.show()
    return dlg


class BackupCenterPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        from gui_qt.pages.quark_backup import QuarkBackupPage

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(0)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        self.tabs.addTab(self._build_config_tab(), "配置")
        self.tabs.addTab(self._build_assets_tab(), "资产")
        self.quark_page = QuarkBackupPage(app)
        self.tabs.addTab(self.quark_page, "网盘")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ---------- 通用小件 ----------
    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "caption")
        label.setWordWrap(True)
        return label

    def _btn(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _tab_shell(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        return page, layout

    # ---------- 配置 ----------
    def _build_config_tab(self) -> QWidget:
        page, v = self._tab_shell()
        card = ToolSectionCard(
            "配置备份",
            "打包全部设置为 zip（工作目录 / 登录状态 / 账号名单 / 分组 / 弹幕姬配置等，"
            "不含已获取资产）；导入会覆盖同名配置文件，重启应用后全部生效。")
        card.content_layout.addWidget(self._caption(
            "包含：工作目录与导出目录、登录状态、账号名单与分组、弹幕姬 / 录制 / 音乐标签等页面设置。"))
        card.content_layout.addWidget(self._caption(
            "不含：已获取资产、媒体文件、数据库快照（这些请用「资产」页备份）。"))
        card.content_layout.addWidget(self._caption(
            "使用建议：换机、重装或大改设置前先导出；导入会覆盖同名配置，建议先导出当前配置留底；"
            "备份文件请放在软件目录之外。"))
        row = QHBoxLayout()
        row.addWidget(self._btn("导出配置…", self._export_config))
        row.addWidget(self._btn("导入配置…", self._import_config))
        row.addStretch(1)
        card.content_layout.addLayout(row)
        self.config_status = QLabel("")
        self.config_status.setProperty("role", "caption")
        card.content_layout.addWidget(self.config_status)
        v.addWidget(card, 0, Qt.AlignTop)
        v.addStretch(1)
        return page

    def _export_config(self):
        default = f"配置备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", default, "zip 包 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self.config_status.setText("正在导出配置…")

        def done(result, err):
            if err:
                self.config_status.setText(f"导出失败：{err}")
                self.app.toast(f"导出配置失败：{err}", "error")
                return
            text = f"已导出 {result['files']} 个配置文件（{result['size_text']}）"
            self.config_status.setText(text)
            self.app.toast(text, "success")

        run_bg(lambda: _export_config(path), done)

    def _import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "配置备份 (*.zip)")
        if not path:
            return
        if QMessageBox.question(
                self, "导入配置",
                "导入会覆盖当前同名配置文件，重启应用后全部生效。\n确定继续吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        self.config_status.setText("正在导入配置…")

        def done(result, err):
            if err:
                self.config_status.setText(f"导入失败：{err}")
                self.app.toast(f"导入配置失败：{err}", "error")
                return
            text = f"已导入 {result['files']} 个配置文件，重启应用后生效"
            self.config_status.setText(text)
            self.app.toast(text, "success")

        run_bg(lambda: _import_config(path), done)

    # ---------- 资产 ----------
    def _build_assets_tab(self) -> QWidget:
        page, v = self._tab_shell()
        card = ToolSectionCard(
            "资产备份",
            "资产备份包含资产数据库与统一导出目录（已获取数据 / 媒体 / 弹幕 / 录制等）。"
            "导入时文件不覆盖，数据库按去重键合并；不含配置与登录状态（配置请用「配置」页）。")
        card.content_layout.addWidget(self._caption(
            "使用建议：大批量整理或换机前整包导出；导入只补充缺失文件，不删除本地多出的文件；"
            "长期增量备份请用「网盘」页。"))
        row = QHBoxLayout()
        row.addWidget(self._btn("导出所有资产（zip 包）…", self._backup_assets_zip))
        row.addWidget(self._btn("导出所有资产（文件夹）…", self._backup_assets_folder))
        row.addStretch(1)
        card.content_layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self._btn("导入备份（zip 包）…", self._import_backup_zip))
        row2.addWidget(self._btn("导入备份（文件夹）…", self._import_backup_folder))
        row2.addStretch(1)
        card.content_layout.addLayout(row2)
        self.assets_status = QLabel("")
        self.assets_status.setProperty("role", "caption")
        card.content_layout.addWidget(self.assets_status)
        v.addWidget(card, 0, Qt.AlignTop)
        v.addStretch(1)
        return page

    def _backup_assets_zip(self):
        default = f"资产备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出所有资产", default, "zip 包 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self._run_assets_export(path, "zip")

    def _backup_assets_folder(self):
        dest = QFileDialog.getExistingDirectory(
            self, "选择备份保存位置（将自动创建「资产备份_时间戳」子文件夹）")
        if not dest:
            return
        self._run_assets_export(dest, "folder")

    def _run_assets_export(self, dest: str, mode: str):
        cancel = threading.Event()
        bridge = _ProgressBridge()
        dlg = _make_progress_dlg(self, "备份所有资产…", 1, cancel)

        def on_ui(done, total, name):
            dlg.setMaximum(max(total, 1))
            dlg.setValue(min(done, total))
            dlg.setLabelText(f"备份中 {done}/{total}：{name}")

        bridge.updated.connect(on_ui)
        self.assets_status.setText("正在备份资产…")

        def work():
            from toolbox import backup
            return backup.export_assets(
                dest, mode=mode,
                on_progress=lambda d, t, n: bridge.emit_progress(d, t, n),
                cancel=cancel)

        def done(result, err):
            bridge.updated.disconnect(on_ui)
            dlg.close()
            if err:
                self.assets_status.setText(f"备份失败：{err}")
                self.app.toast(f"备份失败：{err}", "error")
                return
            if result.get("cancelled"):
                self.assets_status.setText("已取消备份")
                self.app.toast("已取消备份", "warning")
                return
            text = f"已备份 {result['files']} 个文件（{result['size_text']}）→ {result['path']}"
            self.assets_status.setText(text)
            self.app.toast(text, "success")

        run_bg(work, done)

    def _import_backup_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入资产备份（zip 包）", "", "资产备份 (*.zip)")
        if path:
            self._run_assets_import(path)

    def _import_backup_folder(self):
        path = QFileDialog.getExistingDirectory(self, "导入资产备份（选择备份文件夹）")
        if path:
            self._run_assets_import(path)

    def _run_assets_import(self, src: str):
        cancel = threading.Event()
        bridge = _ProgressBridge()
        dlg = _make_progress_dlg(self, "导入资产备份…", 1, cancel)

        def on_ui(done, total, name):
            dlg.setMaximum(max(total, 1))
            dlg.setValue(min(done, total))
            dlg.setLabelText(f"导入中 {done}/{total}：{name}")

        bridge.updated.connect(on_ui)
        self.assets_status.setText("正在导入资产…")

        def work():
            from toolbox import backup
            return backup.import_assets(
                src, on_progress=lambda d, t, n: bridge.emit_progress(d, t, n),
                cancel=cancel)

        def done(result, err):
            bridge.updated.disconnect(on_ui)
            dlg.close()
            if err == "已取消":
                self.assets_status.setText("已取消导入")
                self.app.toast("已取消导入", "warning")
                return
            if err:
                self.assets_status.setText(f"导入失败：{err}")
                self.app.toast(f"导入失败：{err}", "error")
                return
            text = f"已导入 {result['files']} 个文件（{result['size_text']}）"
            self.assets_status.setText(text)
            self.app.toast(text, "success")

        run_bg(work, done)

    # ---------- 生命周期 ----------
    def _on_tab_changed(self, index: int):
        if self.tabs.widget(index) is self.quark_page:
            self.quark_page.on_show()

    def on_show(self):
        if self.tabs.currentWidget() is self.quark_page:
            self.quark_page.on_show()

    def on_hide(self):
        self.quark_page.on_hide()

    def shutdown(self):
        self.quark_page.shutdown()


def _export_config(path: str):
    from toolbox import backup
    return backup.export_config(path)


def _import_config(path: str):
    from toolbox import backup
    return backup.import_config(path)