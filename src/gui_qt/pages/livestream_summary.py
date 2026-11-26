# -*- coding: utf-8 -*-
"""直播总结页（模型接口 / 可选本地模型 / 本地规则）。

把直播转录 TXT / SRT 交给所选推理方式，按内置「封茗囧菌直播总结」规则
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
from gui_qt.local_llm_panel import LocalLlmOptionsWidget
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolSectionCard
from toolbox import livestream_summary_rule as rule
from toolbox.config_manager import get_export_subdir
from toolbox.livestream_summary import config as ls_config
from toolbox.livestream_summary import pipeline
from toolbox.livestream_summary.client import is_local_base_url
from toolbox.livestream_summary.session import client_session


class LivestreamSummaryPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._running = False
        self._cancel: threading.Event | None = None
        self._local_refreshing = False
        self._source: Path | None = None
        self._result_text = ""
        self._icons: list[tuple[QWidget, str, str]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_body(), 1)
        self._load_settings()
        self._refresh_native_status()
        self._refresh_files()

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
        for index, name in ((0, "robot"), (1, "document"), (2, "chip")):
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
        self.file_combo = QComboBox()
        self.file_combo.setMinimumContentsLength(24)
        self.file_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.file_combo.currentIndexChanged.connect(self._on_file_selected)
        file_row.addWidget(self.file_combo, 1)
        scan_btn = QPushButton("刷新")
        scan_btn.setProperty("variant", "secondary")
        scan_btn.setToolTip("重新扫描工作文件夹里的转录文件（只读工作文件夹，不能选别处）")
        scan_btn.clicked.connect(self._refresh_files)
        self._register_icon(scan_btn, "refresh")
        file_row.addWidget(scan_btn)
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
        left.addWidget(self._build_model_card())
        self.local_card = self._build_local_card()
        left.addWidget(self.local_card)
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

    def _build_local_card(self) -> ToolSectionCard:
        card = ToolSectionCard(
            "本地模型配置", "CPU / GPU 模块与模型请在全局设置的「运行环境与组件」中准备。", icon="chip")
        self._register_icon(card, "chip", "title")
        self.local_status = QLabel("未检测本地总结组件")
        self.local_status.setWordWrap(True)
        card.content_layout.addWidget(self.local_status)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addStretch(1)
        self.local_detect_btn = QPushButton("刷新模型")
        self.local_detect_btn.setProperty("variant", "secondary")
        self.local_detect_btn.clicked.connect(self._refresh_native_status)
        self._register_icon(self.local_detect_btn, "refresh")
        status_row.addWidget(self.local_detect_btn)
        card.content_layout.addLayout(status_row)
        self.local_options = LocalLlmOptionsWidget()
        card.content_layout.addWidget(self.local_options)
        note = QLabel("线程数设为自动时由引擎选择；GPU 卸载层数只在 GPU 模块生效。模型准备完成后，在此选择并保存。")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        card.content_layout.addWidget(note)
        return card

    def _build_model_card(self) -> ToolSectionCard:
        card = ToolSectionCard(
            "总结方式", "选择模型接口、本地模型或本地规则保底；推理组件完全可选。", icon="compass")
        self._register_icon(card, "compass", "title")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("大模型总结（OpenAI 兼容接口）", "ai")
        self.mode_combo.addItem("本地规则保底（不联网、不用 Key）", "rule")
        self.mode_combo.addItem("本地模型（CPU / GPU）", "local")
        self.mode_combo.setMinimumContentsLength(8)
        self.mode_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo.setFocusPolicy(Qt.StrongFocus)
        self.mode_combo.currentIndexChanged.connect(self._sync_mode)
        self._refresh_mode_icons()
        card.content_layout.addWidget(self.mode_combo)
        self.model_box = QWidget()
        box = QVBoxLayout(self.model_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(QLabel("接口地址"))
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText(ls_config.DEFAULT_BASE_URL)
        box.addWidget(self.base_url_edit)
        local_hint = QLabel("也可连接已运行的 Ollama / LM Studio 等兼容接口；本机地址可以不填 Key。")
        local_hint.setProperty("role", "caption")
        local_hint.setWordWrap(True)
        box.addWidget(local_hint)
        box.addWidget(QLabel("API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
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
        self.model_edit.setPlaceholderText("服务商或已运行的本地服务提供的模型名称")
        box.addWidget(self.model_edit)
        self.key_status = QLabel("")
        self.key_status.setProperty("role", "caption")
        self.key_status.setWordWrap(True)
        box.addWidget(self.key_status)
        card.content_layout.addWidget(self.model_box)
        chunk_row = QHBoxLayout()
        chunk_row.setSpacing(8)
        chunk_row.addWidget(QLabel("每块字数"))
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(2000, 20000)
        self.chunk_spin.setSingleStep(1000)
        self.chunk_spin.setSuffix(" 字")
        self.chunk_spin.setToolTip("长转录按此字数分块；应为模型指令和输出保留足够上下文。")
        chunk_row.addWidget(self.chunk_spin, 1)
        card.content_layout.addLayout(chunk_row)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setProperty("variant", "secondary")
        self.test_btn.setToolTip("本地模式将加载所选模型并执行一次短测试")
        self.test_btn.clicked.connect(self._test_connection)
        self._register_icon(self.test_btn, "pulse")
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        self.save_btn = QPushButton("保存设置")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self._save_settings)
        self._register_icon(self.save_btn, "save", "primary")
        buttons.addWidget(self.save_btn)
        card.content_layout.addLayout(buttons)
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

    # ==================== 本地模型状态（只读） ====================

    def _refresh_native_status(self):
        if self._local_refreshing or self._running:
            return
        self._local_refreshing = True
        self.local_detect_btn.setEnabled(False)

        def work():
            from toolbox.livestream_summary import optional_runtime
            return optional_runtime.status()

        def done(snapshot, error):
            self._local_refreshing = False
            self.local_detect_btn.setEnabled(not self._running)
            if error:
                self.local_status.setText("检测本地总结组件失败，请刷新后重试。")
                return
            snapshot = snapshot or {}
            self.local_options.set_status(snapshot)
            installed = [str(item.get("label") or item["id"])
                         for item in snapshot.get("runtimes", ()) if item.get("installed")]
            if not snapshot.get("supported"):
                message = str(snapshot.get("note") or "本地推理模块仅支持 Windows x64。")
            else:
                message = ("已安装模块：" + " / ".join(installed) if installed
                           else "尚未安装推理模块，请在全局设置准备 CPU 或 GPU 模块。")
                message += f" · {len(snapshot.get('models', ()))} 个模型"
            self.local_status.setText(message)

        run_bg(work, done, owner=self)

    # ==================== 设置 ====================

    def _load_settings(self):
        settings = ls_config.load_settings()
        self.base_url_edit.setText(settings.base_url)
        self.model_edit.setText(settings.model)
        self.chunk_spin.setValue(settings.chunk_chars)
        self.local_options.set_values(settings.local)
        index = self.mode_combo.findData(settings.mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self._refresh_key_status()
        self._sync_mode()

    def _sync_mode(self):
        mode = self.mode_combo.currentData()
        ai = mode == "ai"
        self.model_box.setVisible(ai)
        self.model_box.setEnabled(ai and not self._running)
        if ai:
            self._refresh_key_status()
        if hasattr(self, "local_card"):
            self.local_card.setVisible(mode == "local")
            self.local_options.setEnabled(mode == "local" and not self._running)
        self.mode_combo.setEnabled(not self._running)
        self.chunk_spin.setEnabled(mode != "rule" and not self._running)
        self.save_btn.setEnabled(not self._running)
        self.test_btn.setEnabled(mode != "rule" and not self._running)
        if hasattr(self, "local_detect_btn"):
            self.local_detect_btn.setEnabled(not self._running and not self._local_refreshing)

    def _refresh_key_status(self):
        if self.mode_combo.currentData() != "ai":
            return
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

    @staticmethod
    def _default_output_dir() -> str:
        """输出固定跟随全局统一导出目录。"""
        return str(get_export_subdir("livestream_summary"))

    def _collect_settings(self) -> ls_config.Settings:
        current = ls_config.load_settings()
        mode = self.mode_combo.currentData() or "ai"
        return ls_config.Settings(
            base_url=(self.base_url_edit.text().strip() or ls_config.DEFAULT_BASE_URL)
            if mode == "ai" else current.base_url,
            model=self.model_edit.text().strip() if mode == "ai" else current.model,
            chunk_chars=int(self.chunk_spin.value()), output_dir="", mode=mode,
            local=self.local_options.values() if mode == "local" else current.local,
        )

    def _save_settings(self, quiet: bool = False) -> bool:
        try:
            settings = self._collect_settings()
        except ValueError as error:
            self.app.toast(str(error), "warning")
            return False
        if settings.mode == "ai":
            typed_key = self.api_key_edit.text().strip()
            if typed_key:
                encrypted = ls_config.save_api_key(typed_key)
                self.api_key_edit.clear()
                if not encrypted:
                    self._append_log("提示：本机缺少 cryptography，API Key 以降级方式保存")
        ls_config.save_settings(settings)
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

    @staticmethod
    def _display_name(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return path.name

    def _refresh_files(self):
        """只扫描工作文件夹（根目录 + 日期文件夹）里的 TXT / SRT，最新在前。"""
        if self._running:
            return
        root_text = getattr(self.app, "work_dir", "") or ""
        root = Path(root_text) if root_text else None
        files = rule.scan_transcripts(root_text) if root_text else []
        previous = str(self._source) if self._source else ""
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        if root is None or not root.is_dir():
            self.file_combo.addItem("未设置工作文件夹，请到全局设置里选择录播目录", "")
        elif not files:
            self.file_combo.addItem("工作文件夹内没有转录文件（TXT / SRT）", "")
        else:
            for path in files:
                self.file_combo.addItem(self._display_name(path, root), str(path))
        index = self.file_combo.findData(previous) if previous else -1
        self.file_combo.setCurrentIndex(max(0, index))
        self.file_combo.blockSignals(False)
        self._on_file_selected()

    def _on_file_selected(self, *_args):
        data = self.file_combo.currentData()
        path = Path(data) if data else None
        changed = path != self._source
        self._source = path
        self.start_btn.setEnabled(path is not None and not self._running)
        if path is None:
            self.file_combo.setToolTip(self.file_combo.currentText())
            self.date_label.setText("直播日期：未选择文件")
            return
        self.file_combo.setToolTip(str(path))
        if not changed:
            return
        self._result_text = ""
        self.result_view.clear()
        self.date_label.setText("直播日期：读取中…")
        self._inspect_source(path)

    def _inspect_source(self, path: Path):
        def work():
            text = rule.read_transcript(path)
            body = rule.strip_srt(text)
            date_text, source = rule.extract_date(
                path.name, body, str(path.parent))
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
            self._append_log(f"已读取转录：{chars} 字 · {path.name}")

        run_bg(work, done, owner=self)

    # ==================== 执行 ====================

    def _start(self):
        if self._running:
            self.app.toast("总结正在进行中", "info")
            return
        if self._source is None or not self._source.is_file():
            self.app.toast("工作文件夹内没有可用的转录文件（TXT / SRT）", "info")
            return
        if not self._save_settings(quiet=True):
            return
        settings = self._collect_settings()
        if settings.mode == "local" and not settings.local["model"]:
            self.app.toast("请选择已准备的本地模型", "warning")
            return

        self._running = True
        self._cancel = threading.Event()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._sync_mode()
        self.progress.setValue(0)
        self.status_label.setText("准备中…")
        self._result_text = ""
        self.result_view.clear()
        self.result_stats.setText("")
        if settings.mode == "rule":
            self._append_log(f"开始总结（本地规则，不联网）：{self._source.name}")
        else:
            self._append_log(
                f"开始总结：{self._source.name} · 模型 {settings.local['model'] if settings.mode == 'local' else settings.model} · "
                f"每块 {settings.chunk_chars} 字")

        source = self._source
        cancel = self._cancel

        def work():
            text = rule.read_transcript(source)
            folder = str(source.parent)
            if settings.mode == "rule":
                return pipeline.run_rule(text, source.name, folder=folder)
            with client_session(settings, cancel=cancel,
                                progress=lambda percent, message: post_to_main(
                                    self._on_progress, "load", percent, message)) as client:
                extra = ({"max_tokens": settings.local["max_tokens"]}
                         if settings.mode == "local" else {})
                return pipeline.run(
                    text, source.name, client,
                    progress=lambda stage, percent, message: post_to_main(
                        self._on_progress, stage, percent, message),
                    cancel=cancel, chunk_chars=settings.chunk_chars, folder=folder, **extra)

        def done(result, err):
            self._running = False
            self._cancel = None
            self.start_btn.setEnabled(self._source is not None)
            self.stop_btn.setEnabled(False)
            self._sync_mode()
            if err:
                if "已停止" in str(err) or "已取消" in str(err):
                    self.status_label.setText("已取消")
                    self._append_log("任务已取消")
                else:
                    self.status_label.setText("失败")
                    self._append_log(f"总结失败：{err}")
                    self.app.toast(f"总结失败：{err}", "error")
                return
            self._on_finished(result, source)

        run_bg(work, done, owner=self)

    def _on_finished(self, result: pipeline.PipelineResult, source: Path):
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

        # 输出位置固定跟随全局统一导出目录，不受页面设置影响
        target = pipeline.default_output_path(
            result.text, source, self._default_output_dir())
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
        if self._running or not self._save_settings(quiet=True):
            return
        settings = self._collect_settings()
        if settings.mode == "rule":
            return
        if settings.mode == "local" and not settings.local["model"]:
            self.app.toast("请选择已准备的本地模型", "warning")
            return
        self._running = True
        self._cancel = threading.Event()
        cancel = self._cancel
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._sync_mode()
        self.progress.setValue(0)
        self.status_label.setText("测试连接中…")
        self._append_log("开始本地模型测试" if settings.mode == "local" else "开始模型接口测试")

        def work():
            with client_session(settings, cancel=cancel,
                                progress=lambda percent, message: post_to_main(
                                    self._on_progress, "load", percent, message)) as client:
                return client.test_connection(cancel=cancel)

        def done(result, err):
            self._running = False
            self._cancel = None
            self.start_btn.setEnabled(self._source is not None)
            self.stop_btn.setEnabled(False)
            self._sync_mode()
            if err:
                if cancel.is_set():
                    self.status_label.setText("已取消")
                    self._append_log("模型测试已取消")
                    return
                self.status_label.setText("连接失败")
                self._append_log(f"连接失败：{err}")
                self.app.toast(f"连接失败：{err}", "error")
                return
            self.progress.setValue(100)
            self.status_label.setText("连接正常")
            self._append_log(f"连接正常，模型回复：{str(result)[:60]}")
            self.app.toast("模型连接正常", "success")

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
        default_dir = self._default_output_dir()
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

    def on_show(self):
        """切回本页时重新扫描工作文件夹，新转录的文件自动出现在下拉里。"""
        self._refresh_files()
        self._refresh_native_status()

    def has_active_tasks(self) -> bool:
        return self._running

    def shutdown(self):
        self._stop()
        super().shutdown()
