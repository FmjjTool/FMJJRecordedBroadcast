# -*- coding: utf-8 -*-
"""直播总结页（大模型方案）。

把直播转录 TXT / SRT 交给 OpenAI 兼容接口，按内置「封茗囧菌直播总结」规则
分块提取、合并、生成终稿并做格式校验。

- 规则只读复用 `src/skills/fengming-livestream-summary/`，不在页面里另写一套；
- API Key 单独加密保存在 `config/livestream_summary.json`，与登录态存储隔离；
- 纯规则保底仍可用命令行 `toolbox.livestream_summary_rule`。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolSectionCard
from toolbox import livestream_summary_rule as rule
from toolbox.livestream_summary import config as ls_config
from toolbox.livestream_summary import local_model, pipeline
from toolbox.livestream_summary.client import ChatClient, is_local_base_url

SUPPORTED_FILTER = "直播转录 (*.txt *.srt);;所有文件 (*)"


class LivestreamSummaryPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._running = False
        self._cancel: threading.Event | None = None
        self._pull_cancel: threading.Event | None = None
        self._source: Path | None = None
        self._result_text = ""
        self._icons: list[tuple[QWidget, str, str]] = []
        self._local_ready = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_body(), 1)
        self._load_settings()
        self._detect_local()

    # ==================== 图标 ====================

    def _register_icon(self, widget, name: str, role: str = "button"):
        self._icons.append((widget, name, role))
        self._apply_icon(widget, name, role)

    def _apply_icon(self, widget, name: str, role: str):
        color = self._icon_color(role)
        if isinstance(widget, ToolSectionCard):
            widget.set_icon(name, color)
            return
        widget.setIcon(line_icon(name, color, 16))
        widget.setIconSize(QSize(16, 16))

    @staticmethod
    def _icon_color(role: str) -> str:
        pal = theme.current_palette()
        if role == "primary":
            return pal.color(QPalette.HighlightedText).name()
        if role == "danger":
            return pal.color(QPalette.BrightText).name()
        if role == "title":
            return pal.color(QPalette.Link).name()
        return pal.color(QPalette.ButtonText).name()

    def _refresh_icons(self):
        for widget, name, role in self._icons:
            self._apply_icon(widget, name, role)
        self._refresh_mode_icons()

    def _refresh_mode_icons(self):
        color = theme.current_palette().color(QPalette.Link).name()
        for index, name in ((0, "robot"), (1, "document")):
            self.mode_combo.setItemIcon(index, line_icon(name, color, 16))

    def on_theme_changed(self):
        self._refresh_icons()

    # ==================== 界面搭建 ====================

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("variant", "toolbar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("选择直播转录文件（TXT / SRT）")
        file_row.addWidget(self.file_edit, 1)
        pick_btn = QPushButton("选择文件")
        pick_btn.setProperty("variant", "secondary")
        pick_btn.setToolTip("选择要总结的直播转录文件")
        pick_btn.clicked.connect(self._pick_file)
        self._register_icon(pick_btn, "folder")
        file_row.addWidget(pick_btn)
        self.date_label = QLabel("直播日期：未选择文件")
        self.date_label.setProperty("role", "caption")
        self.date_label.setMinimumWidth(190)
        file_row.addWidget(self.date_label)
        layout.addLayout(file_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.start_btn = QPushButton("开始总结")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setToolTip("按当前模型设置执行分块提取、合并与终稿生成")
        self.start_btn.clicked.connect(self._start)
        self._register_icon(self.start_btn, "play", "primary")
        action_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("variant", "secondary")
        self.stop_btn.setToolTip("停止当前任务（已生成的中间结果不保存）")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self._register_icon(self.stop_btn, "stop")
        action_row.addWidget(self.stop_btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(16)
        action_row.addWidget(self.progress, 1)

        self.status_label = QLabel("空闲")
        self.status_label.setProperty("role", "caption")
        self.status_label.setMinimumWidth(240)
        action_row.addWidget(self.status_label)
        layout.addLayout(action_row)
        return bar

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_input_card())
        left.addWidget(self._build_local_card())
        left.addWidget(self._build_model_card())
        left.addStretch(1)
        left_host = QWidget()
        left_host.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(340)
        left_scroll.setMaximumWidth(450)
        left_scroll.setWidget(left_host)
        body.addWidget(left_scroll)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self._build_result_card(), 3)
        right.addWidget(self._build_log_card(), 1)
        right_wrap = QWidget()
        right_wrap.setLayout(right)
        body.addWidget(right_wrap, 1)
        return body

    def _build_input_card(self) -> ToolSectionCard:
        card = ToolSectionCard("输入与输出", "转录文件只读；总结另存为 TXT。", icon="document")
        self._register_icon(card, "document", "title")
        self.detail_label = QLabel("尚未选择文件")
        self.detail_label.setProperty("role", "caption")
        self.detail_label.setWordWrap(True)
        card.content_layout.addWidget(self.detail_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("输出目录"))
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("留空 = 转录文件同目录")
        row.addWidget(self.output_dir_edit, 1)
        browse = QPushButton()
        browse.setProperty("variant", "secondary")
        browse.setToolTip("选择总结输出目录")
        browse.setFixedWidth(38)
        browse.clicked.connect(self._pick_output_dir)
        self._register_icon(browse, "folder")
        row.addWidget(browse)
        card.content_layout.addLayout(row)

        hint = QLabel("结果会同时显示在右侧；文件名取总结标题，可直接修改后再另存。")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        card.content_layout.addWidget(hint)
        return card

    def _build_local_card(self) -> ToolSectionCard:
        card = ToolSectionCard(
            "本机模型（可选）",
            "在本机跑模型只是可选项：不装也能用——在下方「总结方式」里选云接口（填 Key），"
            "或直接用本地规则。未安装时这里只给下载入口，不会静默安装。",
            icon="chip")
        self._register_icon(card, "chip", "title")

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.local_status = QLabel("未检测")
        self.local_status.setProperty("role", "caption")
        status_row.addWidget(self.local_status, 1)

        self.local_detect_btn = QPushButton("检测")
        self.local_detect_btn.setProperty("variant", "secondary")
        self.local_detect_btn.setToolTip("检测本机 Ollama 服务与已安装模型")
        self.local_detect_btn.clicked.connect(self._detect_local)
        self._register_icon(self.local_detect_btn, "refresh")
        status_row.addWidget(self.local_detect_btn)

        install_btn = QPushButton("下载 Ollama")
        install_btn.setProperty("variant", "secondary")
        install_btn.setToolTip("打开 Ollama 官方下载页；安装后回到本页点检测")
        install_btn.clicked.connect(self._open_ollama_download)
        self._register_icon(install_btn, "globe")
        status_row.addWidget(install_btn)
        card.content_layout.addLayout(status_row)

        self.local_models_combo = QComboBox()
        self.local_models_combo.addItem("尚未检测到模型", None)
        self.local_models_combo.currentIndexChanged.connect(self._sync_local_actions)
        card.content_layout.addWidget(self.local_models_combo)

        use_row = QHBoxLayout()
        use_row.setSpacing(6)
        self.use_local_btn = QPushButton("使用这个模型")
        self.use_local_btn.setProperty("variant", "secondary")
        self.use_local_btn.setToolTip("把接口地址和模型名自动填到下方设置")
        self.use_local_btn.setEnabled(False)
        self.use_local_btn.clicked.connect(self._use_local_model)
        self._register_icon(self.use_local_btn, "check")
        use_row.addWidget(self.use_local_btn)
        use_row.addStretch(1)

        self.delete_local_btn = QPushButton("删除模型")
        self.delete_local_btn.setProperty("variant", "danger")
        self.delete_local_btn.setToolTip("删除当前选中的本机模型，释放磁盘空间")
        self.delete_local_btn.setEnabled(False)
        self.delete_local_btn.clicked.connect(self._delete_local_model)
        self._register_icon(self.delete_local_btn, "trash", "danger")
        use_row.addWidget(self.delete_local_btn)
        card.content_layout.addLayout(use_row)

        pull_row = QHBoxLayout()
        pull_row.setSpacing(6)
        self.recommend_combo = QComboBox()
        for item in local_model.RECOMMENDED_MODELS:
            self.recommend_combo.addItem(
                f"{item['label']} · {item['size']}", item["name"])
        pull_row.addWidget(self.recommend_combo, 1)

        self.pull_btn = QPushButton("下载模型")
        self.pull_btn.setProperty("variant", "secondary")
        self.pull_btn.setToolTip("通过本机 Ollama 下载模型，完成后自动填入配置")
        self.pull_btn.clicked.connect(self._pull_model)
        self._register_icon(self.pull_btn, "download")
        pull_row.addWidget(self.pull_btn)

        self.pull_stop_btn = QPushButton("停止")
        self.pull_stop_btn.setProperty("variant", "ghost")
        self.pull_stop_btn.setEnabled(False)
        self.pull_stop_btn.clicked.connect(self._stop_pull)
        self._register_icon(self.pull_stop_btn, "stop")
        pull_row.addWidget(self.pull_stop_btn)
        card.content_layout.addLayout(pull_row)

        self.pull_progress = QProgressBar()
        self.pull_progress.setRange(0, 100)
        self.pull_progress.setValue(0)
        self.pull_progress.setFixedHeight(16)
        self.pull_progress.setVisible(False)
        card.content_layout.addWidget(self.pull_progress)

        self.install_command = QLabel(
            f"未安装时可在终端执行：{local_model.INSTALL_COMMAND}")
        self.install_command.setProperty("role", "caption")
        self.install_command.setWordWrap(True)
        card.content_layout.addWidget(self.install_command)

        self.local_muted_hint = QLabel(
            "未检测到本机模型不影响使用：云接口与本地规则都不需要 Ollama。")
        self.local_muted_hint.setProperty("role", "caption")
        self.local_muted_hint.setWordWrap(True)
        card.content_layout.addWidget(self.local_muted_hint)
        self._set_local_ready(False)
        return card

    def _build_model_card(self) -> ToolSectionCard:
        card = ToolSectionCard(
            "总结方式",
            "大模型总结质量更好，但需要接口与 Key；本地规则完全离线，"
            "不填任何凭据也能出结果，代价是只做原文摘录。",
            icon="compass")
        self._register_icon(card, "compass", "title")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("大模型总结（OpenAI 兼容接口）", "ai")
        self.mode_combo.addItem("本地规则保底（不联网、不用 Key）", "rule")
        self.mode_combo.setToolTip("本地规则模式不会发起任何网络请求")
        self.mode_combo.currentIndexChanged.connect(self._sync_mode)
        self._refresh_mode_icons()
        card.content_layout.addWidget(self.mode_combo)

        self.model_box = QWidget()
        box = QVBoxLayout(self.model_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        box.addWidget(QLabel("接口地址"))
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText(ls_config.DEFAULT_BASE_URL)
        box.addWidget(self.base_url_edit)

        local_hint = QLabel(
            "本地模型示例：Ollama 填 http://127.0.0.1:11434/v1，"
            "LM Studio 填 http://127.0.0.1:1234/v1；本机地址可以不填 Key。")
        local_hint.setProperty("role", "caption")
        local_hint.setWordWrap(True)
        box.addWidget(local_hint)

        box.addWidget(QLabel("API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        key_row.addWidget(self.api_key_edit, 1)
        clear_btn = QPushButton("清除")
        clear_btn.setProperty("variant", "ghost")
        clear_btn.setToolTip("删除本机保存的 API Key")
        clear_btn.clicked.connect(self._clear_api_key)
        key_row.addWidget(clear_btn)
        box.addLayout(key_row)

        box.addWidget(QLabel("模型名称"))
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("例如 qwen2.5:7b / deepseek-chat / gpt-4o-mini")
        box.addWidget(self.model_edit)

        chunk_row = QHBoxLayout()
        chunk_row.setSpacing(6)
        chunk_row.addWidget(QLabel("每块字数"))
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(2000, 20000)
        self.chunk_spin.setSingleStep(1000)
        self.chunk_spin.setSuffix(" 字")
        self.chunk_spin.setToolTip("转录较长时会按该字数分块提取；太小会增加请求次数")
        chunk_row.addWidget(self.chunk_spin)
        chunk_row.addStretch(1)
        box.addLayout(chunk_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        save_btn = QPushButton("保存设置")
        save_btn.setProperty("variant", "primary")
        save_btn.setToolTip("保存接口地址、模型、分块字数与输入的 Key")
        save_btn.clicked.connect(self._save_settings)
        self._register_icon(save_btn, "save", "primary")
        buttons.addWidget(save_btn)

        self.test_btn = QPushButton("测试连接")
        self.test_btn.setProperty("variant", "secondary")
        self.test_btn.setToolTip("发送一次极短的测试请求；本地模型也会消耗少量算力")
        self.test_btn.clicked.connect(self._test_connection)
        self._register_icon(self.test_btn, "pulse")
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        box.addLayout(buttons)

        self.key_status = QLabel("API Key：未保存")
        self.key_status.setProperty("role", "caption")
        self.key_status.setWordWrap(True)
        box.addWidget(self.key_status)

        card.content_layout.addWidget(self.model_box)
        return card

    def _build_result_card(self) -> ToolSectionCard:
        card = ToolSectionCard(
            "总结结果", "纯文本；校验未通过时会在日志里列出具体问题。", icon="edit")
        self._register_icon(card, "edit", "title")
        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("生成的总结会显示在这里")
        card.content_layout.addWidget(self.result_view, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.copy_btn = QPushButton("复制结果")
        self.copy_btn.setProperty("variant", "secondary")
        self.copy_btn.setToolTip("把完整总结复制到剪贴板")
        self.copy_btn.clicked.connect(self._copy_result)
        self._register_icon(self.copy_btn, "copy")
        row.addWidget(self.copy_btn)

        self.export_btn = QPushButton("另存为")
        self.export_btn.setProperty("variant", "secondary")
        self.export_btn.setToolTip("把总结另存为 TXT")
        self.export_btn.clicked.connect(self._export_result)
        self._register_icon(self.export_btn, "download")
        row.addWidget(self.export_btn)
        row.addStretch(1)
        self.result_stats = QLabel("")
        self.result_stats.setProperty("role", "caption")
        row.addWidget(self.result_stats)
        card.content_layout.addLayout(row)
        return card

    def _build_log_card(self) -> ToolSectionCard:
        card = ToolSectionCard(
            "运行日志", "记录每次分块、合并、校验与保存结果。", icon="clipboard")
        self._register_icon(card, "clipboard", "title")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setPlaceholderText("开始总结后，这里会显示分块与校验过程")
        self.log_view.setStyleSheet(
            'QPlainTextEdit { font-family: Consolas, "Cascadia Mono", monospace;'
            " font-size: 11px; background: palette(base); color: palette(text);"
            " border: 1px solid palette(shadow); border-radius: 8px; padding: 6px; }")
        card.content_layout.addWidget(self.log_view, 1)
        return card

    # ==================== 本机模型 ====================

    def _set_local_ready(self, ready: bool):
        """未检测到本机服务时弱化模型选择与下载控件，只保留检测/下载入口。"""
        self._local_ready = bool(ready)
        self.local_models_combo.setEnabled(self._local_ready)
        self.recommend_combo.setEnabled(self._local_ready)
        self.pull_btn.setEnabled(self._local_ready)
        if not self._local_ready:
            self.pull_stop_btn.setEnabled(False)
        self.install_command.setVisible(not self._local_ready)
        self.local_muted_hint.setVisible(not self._local_ready)
        self._sync_local_actions()

    def _detect_local(self):
        self.local_status.setText("检测中…")
        self.local_detect_btn.setEnabled(False)

        def work():
            if not local_model.is_running():
                return {"running": False, "models": []}
            return {"running": True, "models": local_model.list_models()}

        def done(result, err):
            self.local_detect_btn.setEnabled(True)
            if err:
                self.local_status.setText("检测失败")
                self.local_models_combo.clear()
                self.local_models_combo.addItem("检测失败，请确认 Ollama 已启动", None)
                self._set_local_ready(False)
                self._append_log(f"本机模型检测失败：{err}")
                return
            if not result["running"]:
                self.local_status.setText("未运行 / 未安装（不影响使用）")
                self.local_models_combo.clear()
                self.local_models_combo.addItem("未检测到 Ollama", None)
                self._set_local_ready(False)
                return
            models = result["models"]
            self.local_status.setText(f"Ollama 已运行 · {len(models)} 个模型")
            self._set_local_ready(True)
            self.local_models_combo.clear()
            if models:
                for item in models:
                    self.local_models_combo.addItem(
                        f"{item['name']} · {local_model.format_size(item['size'])}",
                        item["name"])
            else:
                self.local_models_combo.addItem("还没有模型，可从上方推荐下载", None)
            self._sync_local_actions()

        run_bg(work, done, owner=self)

    def _sync_local_actions(self, *_args):
        has_model = bool(self.local_models_combo.currentData()) and self._local_ready
        self.use_local_btn.setEnabled(has_model)
        self.delete_local_btn.setEnabled(has_model)

    def _use_local_model(self):
        name = self.local_models_combo.currentData()
        if not name:
            self.app.toast("请先检测并选择一个已安装模型", "info")
            return
        self.mode_combo.setCurrentIndex(0)
        self.base_url_edit.setText(local_model.chat_base_url())
        self.model_edit.setText(name)
        self._refresh_key_status()
        self._save_settings(quiet=True)
        self._append_log(f"已切换本机模型：{name}")
        self.app.toast(f"已使用本机模型：{name}", "success")

    def _delete_local_model(self):
        name = self.local_models_combo.currentData()
        if not name:
            self.app.toast("请先检测并选择一个已安装模型", "info")
            return
        if self._running:
            self.app.toast("总结正在进行中，暂不能删除模型", "info")
            return
        if self._pull_cancel is not None and not self._pull_cancel.is_set():
            self.app.toast("模型下载正在进行中，请稍后再删除", "info")
            return
        if QMessageBox.question(
                self, "删除本机模型",
                f"从本机 Ollama 删除模型「{name}」？\n\n"
                "删除后需要重新下载才能使用。"
        ) != QMessageBox.Yes:
            return
        self.delete_local_btn.setEnabled(False)
        self.local_detect_btn.setEnabled(False)
        self.local_status.setText(f"正在删除 {name}…")
        self._append_log(f"开始删除本机模型：{name}")

        def work():
            local_model.delete_model(name)
            return name

        def done(result, err):
            self.local_detect_btn.setEnabled(True)
            if err:
                self.local_status.setText("模型删除失败")
                self._append_log(f"模型删除失败：{err}")
                self.app.toast(f"模型删除失败：{err}", "error")
                self._sync_local_actions()
                return
            if (self.model_edit.text().strip() == name
                    and is_local_base_url(self.base_url_edit.text())):
                self.model_edit.clear()
                ls_config.save_settings(self._collect_settings())
                self._append_log(f"已删除模型 {name}，并清空当前模型名")
            else:
                self._append_log(f"已删除模型 {name}")
            self.local_status.setText(f"已删除 {name}")
            self._detect_local()
            self.app.toast(f"已删除模型 {name}", "success")

        run_bg(work, done, owner=self)

    def _pull_model(self):
        if self._pull_cancel is not None and not self._pull_cancel.is_set():
            self.app.toast("模型下载正在进行中", "info")
            return
        name = self.recommend_combo.currentData()
        if not name:
            return
        self._pull_cancel = threading.Event()
        self.pull_btn.setEnabled(False)
        self.pull_stop_btn.setEnabled(True)
        self.pull_progress.setVisible(True)
        self.pull_progress.setValue(0)
        self.local_status.setText(f"正在下载 {name}…")
        self._append_log(f"开始下载本机模型：{name}")

        def work():
            if not local_model.is_running():
                raise local_model.LocalModelError(
                    "Ollama 未运行，请先安装并启动 Ollama")
            local_model.pull_model(
                name,
                progress=lambda percent, message: post_to_main(
                    self._on_pull_progress, percent, message),
                cancel=self._pull_cancel)
            return name

        def done(result, err):
            self.pull_btn.setEnabled(self._local_ready)
            self.pull_stop_btn.setEnabled(False)
            self.pull_progress.setVisible(False)
            self._pull_cancel = None
            if err:
                if "已停止" in str(err):
                    self.local_status.setText("模型下载已停止")
                    self._append_log("模型下载已停止")
                else:
                    self.local_status.setText("模型下载失败")
                    self._append_log(f"模型下载失败：{err}")
                    self.app.toast(f"模型下载失败：{err}", "error")
                return
            self._append_log(f"模型下载完成：{name}")
            self.mode_combo.setCurrentIndex(0)
            self.base_url_edit.setText(local_model.chat_base_url())
            self.model_edit.setText(name)
            self._refresh_key_status()
            self._save_settings(quiet=True)
            self._detect_local()
            self.app.toast(f"模型 {name} 已下载并配置", "success")

        run_bg(work, done, owner=self)

    def _on_pull_progress(self, percent: int, message: str):
        self.pull_progress.setValue(max(0, min(100, int(percent))))
        if message:
            self.local_status.setText(message[:80])

    def _stop_pull(self):
        if self._pull_cancel is not None:
            self._pull_cancel.set()
            self.local_status.setText("正在停止下载…")
            self.pull_stop_btn.setEnabled(False)

    def _open_ollama_download(self):
        import webbrowser
        webbrowser.open(local_model.INSTALL_URL)

    # ==================== 设置 ====================

    def _load_settings(self):
        settings = ls_config.load_settings()
        self.base_url_edit.setText(settings.base_url)
        self.model_edit.setText(settings.model)
        self.chunk_spin.setValue(settings.chunk_chars)
        self.output_dir_edit.setText(settings.output_dir)
        index = self.mode_combo.findData(settings.mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self._refresh_key_status()
        self._sync_mode()

    def _sync_mode(self):
        ai = self.mode_combo.currentData() == "ai"
        self.model_box.setEnabled(ai)
        if not self._running:
            self.test_btn.setEnabled(ai)

    def _refresh_key_status(self):
        if ls_config.has_api_key():
            self.api_key_edit.clear()
            self.api_key_edit.setPlaceholderText("已保存；留空表示不修改")
            self.key_status.setText("API Key：已保存（本机加密，与登录态存储隔离）")
        elif is_local_base_url(self.base_url_edit.text()):
            self.api_key_edit.setPlaceholderText("本机模型可不填")
            self.key_status.setText("API Key：本机地址可不填")
        else:
            self.api_key_edit.setPlaceholderText("粘贴 API Key")
            self.key_status.setText("API Key：未保存")

    def _collect_settings(self) -> ls_config.Settings:
        return ls_config.Settings(
            base_url=self.base_url_edit.text().strip() or ls_config.DEFAULT_BASE_URL,
            model=self.model_edit.text().strip(),
            chunk_chars=int(self.chunk_spin.value()),
            output_dir=self.output_dir_edit.text().strip(),
            mode=self.mode_combo.currentData() or "ai",
        )

    def _save_settings(self, quiet: bool = False) -> bool:
        typed_key = self.api_key_edit.text().strip()
        if typed_key:
            encrypted = ls_config.save_api_key(typed_key)
            self.api_key_edit.clear()
            if not encrypted:
                self._append_log("提示：本机缺少 cryptography，API Key 以降级方式保存")
        ls_config.save_settings(self._collect_settings())
        self._refresh_key_status()
        if not quiet:
            self.app.toast("直播总结设置已保存", "success")
        return True

    def _clear_api_key(self):
        if QMessageBox.question(
                self, "清除 API Key",
                "删除本机保存的直播总结 API Key？\n\n"
                "只删除该功能的 Key，不影响其他登录信息。"
        ) != QMessageBox.Yes:
            return
        ls_config.save_api_key("")
        self._refresh_key_status()
        self.app.toast("已清除 API Key", "info")

    # ==================== 文件 ====================

    def _pick_file(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择直播转录文件", str(Path.home()), SUPPORTED_FILTER)
        if not chosen:
            return
        self._source = Path(chosen)
        self.file_edit.setText(str(self._source))
        self._result_text = ""
        self.result_view.clear()
        self.date_label.setText("直播日期：读取中…")
        self._inspect_source(self._source)

    def _inspect_source(self, path: Path):
        def work():
            text = rule.read_transcript(path)
            body = rule.strip_srt(text)
            date_text, source = rule.extract_date(path.name, body)
            return {"chars": len(body), "date": date_text, "source": source}

        def done(result, err):
            if err:
                self.date_label.setText("直播日期：读取失败")
                self._append_log(f"读取转录失败：{err}")
                return
            chars = result["chars"]
            if result["date"]:
                self.date_label.setText(
                    f"直播日期：{result['date']}（{result['source']}）")
            else:
                self.date_label.setText("直播日期：未识别（按「暂未公开」）")
            self.detail_label.setText(f"转录正文约 {chars} 字 · {path.name}")

        run_bg(work, done, owner=self)

    def _pick_output_dir(self):
        start = self.output_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择总结输出目录", start)
        if chosen:
            self.output_dir_edit.setText(chosen)

    # ==================== 执行 ====================

    def _start(self):
        if self._running:
            self.app.toast("总结正在进行中", "info")
            return
        if self._source is None or not self._source.is_file():
            self.app.toast("请先选择直播转录文件", "info")
            return
        self._save_settings(quiet=True)
        settings = self._collect_settings()
        api_key = ls_config.load_api_key()
        allow_empty_key = is_local_base_url(settings.base_url)
        if settings.mode == "ai":
            if not api_key and not allow_empty_key:
                QMessageBox.warning(self, "缺少 API Key",
                                    "请先填写并保存 API Key；\n"
                                    "如果使用本机模型，把接口地址改成 127.0.0.1 / localhost 即可不填 Key。")
                return
            if not settings.model:
                QMessageBox.warning(self, "缺少模型名称",
                                    "请填写服务商或本地模型名称，例如 deepseek-chat / qwen2.5:7b。")
                return

        self._running = True
        self._cancel = threading.Event()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.test_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("准备中…")
        self._result_text = ""
        self.result_view.clear()
        self.result_stats.setText("")
        if settings.mode == "rule":
            self._append_log(f"开始总结（本地规则，不联网）：{self._source.name}")
        else:
            self._append_log(
                f"开始总结：{self._source.name} · 模型 {settings.model} · "
                f"每块 {settings.chunk_chars} 字")

        source = self._source
        cancel = self._cancel

        def work():
            text = rule.read_transcript(source)
            if settings.mode == "rule":
                return pipeline.run_rule(text, source.name)
            client = ChatClient(settings.base_url, api_key, settings.model,
                                allow_empty_key=allow_empty_key)
            return pipeline.run(
                text, source.name, client,
                progress=lambda stage, percent, message: post_to_main(
                    self._on_progress, stage, percent, message),
                cancel=cancel, chunk_chars=settings.chunk_chars)

        def done(result, err):
            self._running = False
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._sync_mode()
            if err:
                if "已停止" in str(err):
                    self.status_label.setText("已停止")
                    self._append_log("任务已停止")
                else:
                    self.status_label.setText("失败")
                    self._append_log(f"总结失败：{err}")
                    self.app.toast(f"总结失败：{err}", "error")
                return
            self._on_finished(result, source, settings)

        run_bg(work, done, owner=self)

    def _on_finished(self, result: pipeline.PipelineResult, source: Path,
                     settings: ls_config.Settings):
        self._result_text = result.text
        self.result_view.setPlainText(result.text)
        self.progress.setValue(100)
        stats = result.stats
        if result.mode == "rule":
            summary = f"完成：本地规则 · {stats.get('chars', 0)} 字"
        else:
            summary = (f"完成：{stats.get('chunks', 0)} 块 · "
                       f"{stats.get('requests', 0)} 次请求")
        self.status_label.setText(summary)
        self.result_stats.setText(
            f"{stats.get('chars', 0)} 字 · 日期 {stats.get('date') or '未识别'}"
            f"（{stats.get('date_source', '-')}）")
        self._append_log(f"{summary} · 校验问题 {len(result.problems)} 处")

        target = pipeline.default_output_path(result.text, source, settings.output_dir)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result.text, encoding="utf-8")
            self._append_log(f"已保存：{target}")
            self.status_label.setText(summary + " · 已保存")
        except OSError as exc:
            self._append_log(f"保存失败：{exc}")
            self.app.toast(f"总结已生成，但保存失败：{exc}", "error")

        if result.problems:
            for problem in result.problems:
                self._append_log(f"校验未通过：{problem}")
            self.app.toast(
                f"总结已生成，但有 {len(result.problems)} 处格式问题，请查看日志", "warning")
        else:
            self.app.toast("直播总结完成", "success")

    def _stop(self):
        if self._cancel is not None:
            self._cancel.set()
            self.status_label.setText("正在停止…")
            self.stop_btn.setEnabled(False)

    def _test_connection(self):
        self._save_settings(quiet=True)
        settings = self._collect_settings()
        api_key = ls_config.load_api_key()
        allow_empty = is_local_base_url(settings.base_url)
        if not api_key and not allow_empty:
            QMessageBox.warning(self, "缺少 API Key",
                                "请先填写并保存 API Key；本机模型地址可以不填。")
            return
        if not settings.model:
            QMessageBox.warning(self, "缺少模型名称", "请填写模型名称后再测试。")
            return
        self.test_btn.setEnabled(False)
        self.status_label.setText("正在测试连接…")
        self._append_log(f"测试连接：{settings.base_url} · {settings.model}")

        def work():
            return ChatClient(settings.base_url, api_key, settings.model,
                              allow_empty_key=allow_empty).test_connection()

        def done(result, err):
            self.test_btn.setEnabled(True)
            if err:
                self.status_label.setText("连接失败")
                self._append_log(f"连接失败：{err}")
                self.app.toast(f"连接失败：{err}", "error")
                return
            self.status_label.setText("连接正常")
            self._append_log(f"连接正常，模型回复：{str(result)[:60]}")
            self.app.toast("模型接口连接正常", "success")

        run_bg(work, done, owner=self)

    # ==================== 结果操作 ====================

    def _copy_result(self):
        if not self._result_text:
            self.app.toast("还没有可复制的总结", "info")
            return
        QApplication.clipboard().setText(self._result_text)
        self.app.toast("已复制总结到剪贴板", "success")

    def _export_result(self):
        if not self._result_text:
            self.app.toast("还没有可保存的总结", "info")
            return
        default_dir = self.output_dir_edit.text().strip() or (
            str(self._source.parent) if self._source else str(Path.home()))
        default = str(Path(default_dir) / pipeline.output_filename(self._result_text))
        chosen, _ = QFileDialog.getSaveFileName(
            self, "另存总结", default, "文本文件 (*.txt)")
        if not chosen:
            return
        try:
            Path(chosen).write_text(self._result_text, encoding="utf-8")
        except OSError as exc:
            self.app.toast(f"保存失败：{exc}", "error")
            return
        self._append_log(f"已另存：{chosen}")
        self.app.toast("总结已保存", "success")

    # ==================== 日志与进度 ====================

    def _append_log(self, message: str):
        self.log_view.appendPlainText(str(message))

    def _on_progress(self, stage: str, percent: int, message: str):
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status_label.setText(message)
        if stage in ("read", "extract", "merge", "final", "repair", "done"):
            self._append_log(message)

    # ==================== 生命周期 ====================

    def has_active_tasks(self) -> bool:
        return self._running or (
            self._pull_cancel is not None and not self._pull_cancel.is_set())

    def shutdown(self):
        self._stop()
        if self._pull_cancel is not None:
            self._pull_cancel.set()
        super().shutdown()
