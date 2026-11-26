"""配置备份与资产备份面板（嵌在「全局设置」页）。

后端只调用现有实现，不复制备份逻辑：

- 配置 → `toolbox.backup.export_config / import_config`
- 资产 → `toolbox.backup.export_assets / import_assets`

网盘备份与网盘下载在「网络库」页，计划任务由「任务」页管理。
"""

from __future__ import annotations

import threading
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressDialog,
    QPushButton, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg


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


class BackupPanel(QWidget):
    """配置 / 资产备份的按钮与状态行。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._caption(layout, "配置备份：工作目录与导出目录、登录状态、账号名单与分组、"
                              "弹幕姬 / 录制 / 音乐标签等页面设置；不含已获取资产、媒体文件与数据库快照。")
        self._caption(layout, "资产备份：资产数据库快照与统一导出目录全树；导入只补充缺失文件，"
                              "数据库按去重键合并，不删除本地多出的文件。")
        self._caption(layout, "导入配置会覆盖同名配置文件，重启应用后生效；建议先导出当前配置留底，"
                              "备份文件放在软件目录之外。")

        config_row = QHBoxLayout()
        config_row.setSpacing(6)
        config_row.addWidget(self._btn("导出配置…", self._export_config,
                                       "把所有设置打包成 zip（不含已获取资产与媒体）"))
        config_row.addWidget(self._btn("导入配置…", self._import_config,
                                       "导入配置 zip，覆盖同名配置文件，重启后生效"))
        config_row.addStretch(1)
        layout.addLayout(config_row)
        self.config_status = self._status(layout)

        assets_row = QGridLayout()
        assets_row.setSpacing(6)
        assets_row.addWidget(self._btn("导出资产（zip 包）…", self._backup_assets_zip,
                                       "资产数据库与统一导出目录打包成一个 zip"), 0, 0)
        assets_row.addWidget(self._btn("导出资产（文件夹）…", self._backup_assets_folder,
                                       "导出到新建的「资产备份_时间戳」文件夹"), 0, 1)
        assets_row.addWidget(self._btn("导入资产（zip 包）…", self._import_backup_zip,
                                       "从 zip 环境还原资产，文件不覆盖、数据库按去重键合并"), 1, 0)
        assets_row.addWidget(self._btn("导入资产（文件夹）…", self._import_backup_folder,
                                       "从备份文件夹还原资产"), 1, 1)
        assets_row.setColumnStretch(2, 1)
        layout.addLayout(assets_row)
        self.assets_status = self._status(layout)

    # ---------- 通用小件 ----------
    def _caption(self, layout, text: str):
        label = QLabel(text)
        label.setProperty("role", "caption")
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def _btn(self, text: str, slot, tip: str = "") -> QPushButton:
        button = QPushButton(text)
        if tip:
            button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def _status(self, layout) -> QLabel:
        label = QLabel("")
        label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    # ---------- 配置 ----------
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


def _export_config(path: str):
    from toolbox import backup
    return backup.export_config(path)


def _import_config(path: str):
    from toolbox import backup
    return backup.import_config(path)
