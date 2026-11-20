"""检查更新对话框（P1）：展示更新说明 → 下载安装包 → SHA256 校验 → 静默安装。

安装前必须真正退出主程序（关闭窗口只是隐藏到托盘，exe 仍被占用），由 updater 写
临时 cmd 等 PID 退出后运行安装器；对话框本身只负责确认与收尾。
"""

import os
import threading

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTextBrowser, QVBoxLayout,
)

from gui_qt.common import post_to_main, run_bg
from toolbox import updater
from version import APP_VERSION


class UpdateDialog(QDialog):
    def __init__(self, app, info: updater.ReleaseInfo, parent=None):
        super().__init__(parent or app)
        self.app = app
        self.info = info
        self._cancel = threading.Event()
        self._downloading = False
        self._installer = None

        self.setWindowTitle("软件更新")
        self.setMinimumWidth(600)
        self.setModal(True)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        head = QLabel(f"发现新版本 v{info.version}")
        head.setStyleSheet("font-size: 16px; font-weight: bold;")
        v.addWidget(head)

        meta = QLabel(f"当前版本 v{APP_VERSION} · 安装包 {info.size_text}"
                      + (f" · 发布于 {info.published_at[:10]}" if info.published_at else ""))
        meta.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        v.addWidget(meta)

        self.notes = QTextBrowser()
        self.notes.setPlainText((info.notes or "").strip() or "（该版本没有填写更新说明）")
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(180)
        self.notes.setMaximumHeight(260)
        v.addWidget(self.notes)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        v.addWidget(self.hint)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.hide()
        v.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        v.addWidget(self.status)

        self.ignore_cb = QCheckBox("以后不再提醒这个版本")
        v.addWidget(self.ignore_cb)

        row = QHBoxLayout()
        self.page_btn = QPushButton("打开下载页")
        self.page_btn.clicked.connect(self._open_page)
        row.addWidget(self.page_btn)

        self.later_btn = QPushButton("稍后")
        self.later_btn.clicked.connect(self.reject)
        row.addWidget(self.later_btn)
        row.addStretch(1)

        self.primary = QPushButton("立即更新")
        self.primary.setDefault(True)
        self.primary.setMinimumWidth(120)
        self.primary.clicked.connect(self._on_primary)
        row.addWidget(self.primary)
        v.addLayout(row)

        supported, reason = updater.self_update_supported()
        self._supported = supported
        if supported:
            self.hint.setText(
                "点「立即更新」会下载安装包并校验 SHA256；随后自动关闭本程序、"
                "静默安装并重新启动。安装时可能出现系统权限（UAC）确认，请点「是」。")
        else:
            self.hint.setText(f"{reason}。可点「打开下载页」手动下载安装。")
            self.hint.setStyleSheet("color: palette(bright-text); font-size: 11px;")
            self.primary.setText("打开下载页")

    # ---- 交互 ----
    def _open_page(self):
        QDesktopServices.openUrl(QUrl(self.info.page_url or updater.RELEASES_PAGE))

    def _on_primary(self):
        if self._downloading:
            self._cancel.set()
            self.primary.setEnabled(False)
            self.status.setText("正在取消…")
            return
        if not self._supported:
            self._open_page()
            return
        self._start_download()

    def _start_download(self):
        self._downloading = True
        self._cancel.clear()
        self.primary.setText("取消下载")
        self.later_btn.setEnabled(False)
        self.page_btn.setEnabled(False)
        self.progress.setRange(0, 0)   # 未知大小时先走忙碌态
        self.progress.show()
        self.status.setText("正在下载安装包…")

        info = self.info

        def work():
            return updater.download(
                info, updater.download_dir(),
                progress=lambda done, total: post_to_main(self._on_progress, done, total),
                cancel=self._cancel)

        run_bg(work, self._on_downloaded, owner=self)

    def _on_progress(self, done, total):
        if total and total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 / total)))
            self.progress.setFormat("%p%")
            self.status.setText(
                f"正在下载安装包… {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
        else:
            self.status.setText(f"正在下载安装包… {done / 1024 / 1024:.1f} MB")

    def _reset_buttons(self):
        self._downloading = False
        self.primary.setEnabled(True)
        self.primary.setText("立即更新")
        self.later_btn.setEnabled(True)
        self.page_btn.setEnabled(True)

    def _on_downloaded(self, path, err):
        self.progress.hide()
        self._reset_buttons()
        if err:
            if self._cancel.is_set():
                self.status.setText("已取消下载")
                return
            self.status.setText(f"下载失败：{err}")
            self.status.setStyleSheet("color: palette(bright-text); font-size: 11px;")
            return
        self._installer = path
        self.status.setText(f"已下载并校验通过：{path.name}")
        self._confirm_install(path)

    def _confirm_install(self, path):
        act = None
        try:
            act = self.app._live_activity()
        except Exception:
            act = None
        if act and (act["recording"] or act["monitoring"]):
            QMessageBox.warning(
                self, "请先停止录制",
                "直播录制或监控正在运行，更新会强制退出程序。\n"
                "请先到「直播录制」停止录制/监控，再回来更新。")
            return
        if self.ignore_cb.isChecked():
            self._remember_skip()
        ok = QMessageBox.question(
            self, "开始安装",
            f"即将关闭本程序并安装 v{self.info.version}。\n\n"
            "安装过程可能弹出系统权限确认（UAC），请点「是」。\n"
            "安装完成后会自动重新启动。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ok != QMessageBox.Yes:
            return
        try:
            updater.launch_installer(path, os.getpid())
        except Exception as exc:
            QMessageBox.critical(self, "无法启动安装", f"安装程序启动失败：{exc}\n"
                                                     "请手动运行已下载的安装包。")
            return
        self.status.setText("正在退出并安装…")
        self.accept()
        # 真正退出（不能只隐藏到托盘，否则 exe 被占用、安装器无法覆盖）
        self.app._quitting = True
        self.app.close()

    def _remember_skip(self):
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            cfg["update_skip_version"] = self.info.version
            save_convert_config(cfg)
        except Exception:
            pass

    def reject(self):
        if self._downloading:
            self._cancel.set()
            return
        if self.ignore_cb.isChecked():
            self._remember_skip()
        super().reject()