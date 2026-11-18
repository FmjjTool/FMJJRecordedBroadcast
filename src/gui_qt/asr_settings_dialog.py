"""语音转字幕设置弹窗：Whisper API 配置 + 本地模型管理。

安装（识别程序 / 模型）统一在「全局设置 → 检测依赖」完成（约定 11），
本弹窗只维护 API 参数、显示状态与删除已下载模型。
"""

import threading

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from toolbox.asr_engine import (
    LOCAL_MODELS, delete_model, local_model_present, local_model_ready,
    local_program_path, local_program_ready,
)
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.utils import format_size

from gui_qt import theme
from gui_qt.common import post_to_main

# 安装入口固定文案：依赖安装只在全局设置，页面不放安装控件（约定 11）
INSTALL_HINT = "「全局设置 → 检测依赖」"


class AsrSettingsDialog(QDialog):
    """Whisper API 参数 + 本地模型管理。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("语音转字幕设置")
        self.resize(640, 560)
        self._busy = False

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
        line.setStyleSheet("color: palette(shadow);")
        root.addWidget(line)

        local_title = QLabel("本地识别（离线，faster-whisper）")
        local_title.setStyleSheet("font-weight: bold;")
        root.addWidget(local_title)

        self.program_label = QLabel("")
        self.program_label.setWordWrap(True)
        root.addWidget(self.program_label)

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

        self.hint = QLabel(
            f"安装识别程序与模型请到{INSTALL_HINT}（可选，约 84MB + 模型）；"
            "这里只显示状态，可删除已下载模型释放空间。长录播建议 Base 或 Small。")
        self.hint.setProperty("role", "caption")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        # ---------- 底部 ----------
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel_btn = QPushButton("关闭")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)
        ok_btn = QPushButton("保存")
        ok_btn.setProperty("variant", "primary")
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
            self.program_label.setText(f"识别程序：已安装（{size}）")
            self.program_label.setStyleSheet("color: palette(link-visited);")
        else:
            self.program_label.setText(f"识别程序：未安装（在{INSTALL_HINT}下载）")
            self.program_label.setStyleSheet("color: palette(placeholder-text);")

        self.table.setRowCount(len(LOCAL_MODELS))
        for row, (key, label, size_mb) in enumerate(LOCAL_MODELS):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem(f"约 {size_mb} MB"))
            ready = local_model_ready(key)
            status = QTableWidgetItem("已下载" if ready else "未下载")
            status.setForeground(QBrush(QColor(
                theme.color("success") if ready else theme.color("muted"))))
            self.table.setItem(row, 2, status)

            cell = QWidget()
            cell_row = QHBoxLayout(cell)
            cell_row.setContentsMargins(0, 0, 0, 0)
            cell_row.setSpacing(4)
            if local_model_present(key):
                del_btn = QPushButton("删除")
                del_btn.setProperty("variant", "danger")
                del_btn.setFixedHeight(24)
                del_btn.setToolTip("删除本地模型文件，释放磁盘空间")
                del_btn.clicked.connect(lambda _c=False, k=key: self._delete_model(k))
                cell_row.addWidget(del_btn)
            else:
                cell_row.addWidget(QLabel("—"))
            cell_row.addStretch(1)
            self.table.setCellWidget(row, 3, cell)
        self._apply_busy()

    def _apply_busy(self):
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 3)
            if widget:
                widget.setEnabled(not self._busy)

    # ==================== 删除 ====================

    def _delete_model(self, key: str):
        if self._busy:
            return
        label = next((text for k, text, _size in LOCAL_MODELS if k == key), key)
        if QMessageBox.question(
                self, "删除本地模型",
                f"删除本地模型「{label}」？\n\n"
                "将删除已下载的模型文件，之后需要重新下载才能离线识别。"
        ) != QMessageBox.Yes:
            return
        self._busy = True
        self._apply_busy()
        self.hint.setText(f"正在删除模型 {key}…")

        def runner():
            try:
                delete_model(key)
            except Exception as exc:  # noqa: BLE001
                post_to_main(self._on_delete_failed, str(exc))
            else:
                post_to_main(self._on_deleted, key)

        threading.Thread(target=runner, daemon=True).start()

    def _on_deleted(self, key: str):
        self._busy = False
        self.hint.setText(f"已删除本地模型 {key}；如需再用请到{INSTALL_HINT}重新下载")
        self._refresh_status()

    def _on_delete_failed(self, error: str):
        self._busy = False
        self.hint.setText(f"删除失败：{error[:120]}")
        self._refresh_status()