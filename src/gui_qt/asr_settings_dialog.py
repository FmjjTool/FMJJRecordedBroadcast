"""语音转字幕设置弹窗：Whisper API 配置 + 本地识别程序/模型下载。"""

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from toolbox.asr_engine import (
    LOCAL_MODELS, download_model, download_program, local_model_ready,
    local_program_path, local_program_ready,
)
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.utils import format_size

from gui_qt.common import post_to_main


class AsrSettingsDialog(QDialog):
    """Whisper API 参数 + 本地模型管理。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("语音转字幕设置")
        self.resize(640, 560)
        self._busy = False
        self._cancel: threading.Event | None = None

        cfg = load_convert_config()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        # ---------- Whisper API ----------
        api_title = QLabel("Whisper API（OpenAI 兼容接口，需自己填 Key）")
        api_title.setStyleSheet("font-weight: bold;")
        root.addWidget(api_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.base_edit = QLineEdit(cfg.get("asr_whisper_base", ""))
        self.base_edit.setPlaceholderText("https://api.openai.com/v1")
        self.key_edit = QLineEdit(cfg.get("asr_whisper_key", ""))
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.model_edit = QLineEdit(cfg.get("asr_whisper_model", "whisper-1"))
        self.model_edit.setPlaceholderText("whisper-1")
        for row, (label, widget) in enumerate((
                ("Base URL", self.base_edit),
                ("API Key", self.key_edit),
                ("模型名", self.model_edit))):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        # ---------- 本地模型 ----------
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #d0d6df;")
        root.addWidget(line)

        local_title = QLabel("本地识别（离线，faster-whisper）")
        local_title.setStyleSheet("font-weight: bold;")
        root.addWidget(local_title)

        prog_row = QHBoxLayout()
        self.program_label = QLabel("")
        prog_row.addWidget(self.program_label, 1)
        self.program_btn = QPushButton("下载识别程序")
        self.program_btn.clicked.connect(self._download_program)
        prog_row.addWidget(self.program_btn)
        root.addLayout(prog_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["模型", "大小", "状态", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 90)
        root.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.hint = QLabel("提示：识别程序约 84MB；模型越大越准也越慢，长录播建议 Base 或 Small。")
        self.hint.setStyleSheet("color: #888; font-size: 11px;")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        # ---------- 底部 ----------
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)
        ok_btn = QPushButton("保存")
        ok_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 18px;")
        ok_btn.clicked.connect(self.accept)
        bottom.addWidget(ok_btn)
        root.addLayout(bottom)

        self._refresh_status()

    # ==================== 保存 ====================

    def accept(self):
        cfg = load_convert_config()
        cfg["asr_whisper_base"] = self.base_edit.text().strip()
        cfg["asr_whisper_key"] = self.key_edit.text().strip()
        cfg["asr_whisper_model"] = self.model_edit.text().strip() or "whisper-1"
        save_convert_config(cfg)
        super().accept()

    # ==================== 状态刷新 ====================

    def _refresh_status(self):
        if local_program_ready():
            size = format_size(local_program_path().stat().st_size)
            self.program_label.setText(f"识别程序：已下载（{size}）")
            self.program_label.setStyleSheet("color: #2e7d32;")
            self.program_btn.setText("重新下载")
        else:
            self.program_label.setText("识别程序：未下载（约 84MB）")
            self.program_label.setStyleSheet("color: #c62828;")
            self.program_btn.setText("下载识别程序")

        self.table.setRowCount(len(LOCAL_MODELS))
        for row, (key, label, size_mb) in enumerate(LOCAL_MODELS):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem(f"约 {size_mb} MB"))
            ready = local_model_ready(key)
            status = QTableWidgetItem("已下载" if ready else "未下载")
            status.setForeground(Qt.darkGreen if ready else Qt.darkRed)
            self.table.setItem(row, 2, status)
            btn = QPushButton("重新下载" if ready else "下载")
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _c=False, k=key: self._download_model(k))
            self.table.setCellWidget(row, 3, btn)
        self.program_btn.setEnabled(not self._busy)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.program_btn.setEnabled(not busy)
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 3)
            if widget:
                widget.setEnabled(not busy)
        if not busy:
            self.progress.setValue(0)

    # ==================== 下载 ====================

    def _download_program(self):
        if self._busy:
            return

        def work():
            cancel = threading.Event()
            self._cancel = cancel
            download_program(
                progress=lambda p, m: post_to_main(self._on_progress, p, m),
                cancel=cancel.is_set,
            )

        self._run_download(work, "识别程序下载完成")

    def _download_model(self, key: str):
        if self._busy:
            return

        def work():
            cancel = threading.Event()
            self._cancel = cancel
            download_model(
                key,
                progress=lambda p, m: post_to_main(self._on_progress, p, m),
                cancel=cancel.is_set,
            )

        self._run_download(work, f"模型 {key} 下载完成")

    def _run_download(self, work, success_msg: str):
        self._set_busy(True)
        self.progress.setValue(0)
        self.hint.setText("正在下载...")

        def runner():
            try:
                work()
            except Exception as exc:  # noqa: BLE001
                post_to_main(self._on_failed, str(exc))
            else:
                post_to_main(self._on_finished, success_msg)

        threading.Thread(target=runner, daemon=True).start()

    def _on_progress(self, percent: int, message: str):
        self.progress.setValue(percent)
        self.hint.setText(message)

    def _on_finished(self, message: str):
        self._cancel = None
        self._set_busy(False)
        self.progress.setValue(100)
        self.hint.setText(f"✅ {message}")
        self._refresh_status()

    def _on_failed(self, error: str):
        self._cancel = None
        self._set_busy(False)
        self.hint.setText(f"❌ 下载失败：{error[:120]}")

    # ==================== 关闭 ====================

    def reject(self):
        if self._cancel is not None:
            self._cancel.set()
        super().reject()

    def closeEvent(self, event):
        if self._cancel is not None:
            self._cancel.set()
        super().closeEvent(event)
