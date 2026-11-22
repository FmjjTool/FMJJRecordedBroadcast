"""语音转字幕设置弹窗：Whisper API 配置 + 本地组件状态。

安装（识别程序 / 模型）统一在「全局设置 → 检测依赖」完成（约定 11），
本弹窗只维护 API 参数、显示状态：本地组件不提供单独卸载（随软件卸载）。
"""

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from toolbox.asr_engine import (
    LOCAL_MODELS, local_model_ready, local_program_path, local_program_ready,
)
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.utils import format_size

from gui_qt import table_sort, theme

# 安装入口固定文案：依赖安装只在全局设置，页面不放安装控件（约定 11）
INSTALL_HINT = "「全局设置 → 检测依赖」"


class AsrSettingsDialog(QDialog):
    """Whisper API 参数 + 本地模型管理。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("语音转字幕设置")
        self.resize(640, 560)

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

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["模型", "大小", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 90)
        self._sorter = table_sort.install(self.table)
        root.addWidget(self.table, 1)

        self.hint = QLabel(
            f"安装识别程序与模型请到{INSTALL_HINT}（可选，约 84MB + 模型）；"
            "本地组件不提供单独卸载，这里只显示状态。长录播建议 Base 或 Small。")
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
        self._sorter.reapply()