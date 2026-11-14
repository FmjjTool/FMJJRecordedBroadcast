"""视频转换页（PySide6 版）。

对应原版 gui/toolbox_pages.py ConvertPage：
- 工作目录 + 日期文件夹计数标签（on_dir_changed 同步）
- 扫描（find_videos + 缓存命中统计）
- 选项：输出格式/并发/保留元数据/保留时间戳/启用缓存/无损失败转码/成功后删源
- 开始转换（lossless → encode fallback 两级）+ 停止 + 清除缓存
- 进度条 + 运行日志（线程安全）
- 回车两段式：第一次扫描，第二次开始转换
"""

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout,
    QWidget,
)

from toolbox.config_manager import (
    load_convert_config, save_convert_config, load_convert_cache, save_convert_cache,
)
from toolbox.convert_engine import (
    find_videos, is_convert_cached, convert_one_file, get_output_path, update_convert_cache,
)
from toolbox.utils import format_size

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolPageHeader, ToolSectionCard, count_date_subfolders


class ConvertPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []
        self.config = load_convert_config()
        self.cache = {}
        self.running = False
        self._enter_stage = 0

        self.setObjectName("convertPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("convertScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.content = QWidget()
        self.content.setObjectName("convertContent")
        self.scroll.setWidget(self.content)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(14)
        self.header = ToolPageHeader("视频转换", "整理录播格式，优先无损封装；按需开启转码兜底。")
        layout.addWidget(self.header)

        self._cards_layout = QGridLayout()
        self._cards_layout.setHorizontalSpacing(12)
        self._cards_layout.setVerticalSpacing(12)
        self._cards_layout.setColumnStretch(0, 1)
        self._cards_layout.setColumnStretch(1, 1)
        self._card_columns = 2
        layout.addLayout(self._cards_layout)

        # 输入卡片：保留全局工作目录和原有扫描入口。
        self.source_card = ToolSectionCard("输入文件")
        self.source_card.setObjectName("convertSourceCard")
        self.dir_date_label = QLabel("")
        self.dir_date_label.setProperty("role", "caption")
        self.source_card.heading_layout.addWidget(self.dir_date_label)
        source_layout = self.source_card.content_layout
        caption = QLabel("工作目录")
        caption.setProperty("role", "caption")
        source_layout.addWidget(caption)
        self.dir_label = QLabel(app.work_dir)
        self.dir_label.setObjectName("convertDirectory")
        self.dir_label.setWordWrap(True)
        self.dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dir_label.setMinimumWidth(0)
        self.dir_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.dir_label.setToolTip(app.work_dir)
        source_layout.addWidget(self.dir_label)
        self.input_formats_label = QLabel()
        self.input_formats_label.setProperty("role", "muted")
        self.input_formats_label.setWordWrap(True)
        source_layout.addWidget(self.input_formats_label)
        source_layout.addStretch(1)
        self.stats_label = QLabel("尚未扫描，点击下方按钮读取视频文件。")
        self.stats_label.setObjectName("convertStatistics")
        self.stats_label.setProperty("role", "muted")
        self.stats_label.setWordWrap(True)
        source_layout.addWidget(self.stats_label)
        self.scan_btn = QPushButton("扫描视频文件")
        self.scan_btn.setProperty("variant", "secondary")
        self.scan_btn.setMinimumHeight(34)
        self.scan_btn.clicked.connect(self._scan)
        source_layout.addWidget(self.scan_btn)
        self._cards_layout.addWidget(self.source_card, 0, 0)

        # 输出卡片：配置项及默认值与原界面一致，仅调整分组和布局。
        self.output_card = ToolSectionCard("输出与选项")
        self.output_card.setObjectName("convertOutputCard")
        options_layout = self.output_card.content_layout
        fields = QHBoxLayout()
        fields.setSpacing(12)
        ext_field = QVBoxLayout()
        ext_field.setSpacing(4)
        ext_label = QLabel("输出格式")
        ext_label.setProperty("role", "caption")
        ext_field.addWidget(ext_label)
        self.out_ext = QComboBox()
        self.out_ext.addItems([".mp4", ".mkv", ".mov"])
        self.out_ext.setCurrentText(self.config.get("output_extension", ".mp4"))
        ext_field.addWidget(self.out_ext)
        fields.addLayout(ext_field, 1)
        count_field = QVBoxLayout()
        count_field.setSpacing(4)
        count_label = QLabel("并发任务")
        count_label.setProperty("role", "caption")
        count_field.addWidget(count_label)
        self.concurrent = QComboBox()
        self.concurrent.addItems(["1", "2", "3", "4"])
        self.concurrent.setCurrentText(str(self.config.get("concurrent_count", 1)))
        count_field.addWidget(self.concurrent)
        fields.addLayout(count_field, 1)
        options_layout.addLayout(fields)

        opt = QGridLayout()
        opt.setHorizontalSpacing(10)
        opt.setVerticalSpacing(8)
        self.preserve_var = QCheckBox("保留元数据")
        self.preserve_var.setChecked(self.config.get("preserve_metadata", True))
        opt.addWidget(self.preserve_var, 0, 0)
        self.timestamp_var = QCheckBox("保留时间戳")
        self.timestamp_var.setChecked(self.config.get("preserve_timestamps", True))
        opt.addWidget(self.timestamp_var, 0, 1)
        self.cache_var = QCheckBox("启用缓存")
        self.cache_var.setChecked(self.config.get("enable_cache", True))
        opt.addWidget(self.cache_var, 1, 0)
        self.encode_fallback_var = QCheckBox("无损失败转码")
        self.encode_fallback_var.setChecked(self.config.get("enable_encode_fallback", False))
        opt.addWidget(self.encode_fallback_var, 1, 1)
        self.delete_src_var = QCheckBox("成功后删源 ⚠️")
        self.delete_src_var.setChecked(self.config.get("delete_source_after_success", False))
        self.delete_src_var.setToolTip("开启后，转换成功会删除原始视频文件。")
        opt.addWidget(self.delete_src_var, 2, 0, 1, 2)
        options_layout.addLayout(opt)
        self.output_hint = QLabel()
        self.output_hint.setProperty("role", "muted")
        self.output_hint.setWordWrap(True)
        self.output_hint.setMinimumWidth(0)
        self.output_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        options_layout.addWidget(self.output_hint)
        self._cards_layout.addWidget(self.output_card, 0, 1)

        self.out_ext.currentTextChanged.connect(lambda _: self._save_config())
        self.concurrent.currentTextChanged.connect(lambda _: self._save_config())
        for checkbox in (self.preserve_var, self.timestamp_var, self.cache_var,
                         self.encode_fallback_var, self.delete_src_var):
            checkbox.toggled.connect(lambda _: self._save_config())

        # 操作与进度：主操作、停止和缓存管理保持原有信号/确认逻辑。
        self.task_panel = QFrame()
        self.task_panel.setObjectName("convertTaskPanel")
        self.task_panel.setProperty("variant", "toolbar")
        task_layout = QVBoxLayout(self.task_panel)
        task_layout.setContentsMargins(12, 12, 12, 12)
        task_layout.setSpacing(10)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton("开始转换")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setMinimumHeight(34)
        self.start_btn.clicked.connect(self._start_convert)
        self.start_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("variant", "danger")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.clicked.connect(self._stop_convert)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        self.clear_cache_btn = QPushButton("清除转换缓存")
        self.clear_cache_btn.setProperty("variant", "secondary")
        self.clear_cache_btn.setMinimumHeight(34)
        self.clear_cache_btn.setToolTip("清除本页的已转换记录（只影响缓存判定，不删除任何文件）：\n"
                                        "清除后，之前被跳过的视频会重新参与转换")
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        btn_row.addWidget(self.clear_cache_btn)
        task_layout.addLayout(btn_row)
        progress_row = QHBoxLayout()
        self.phase_label = QLabel("等待扫描")
        self.phase_label.setProperty("role", "muted")
        progress_row.addWidget(self.phase_label, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setProperty("role", "caption")
        progress_row.addWidget(self.percent_label)
        task_layout.addLayout(progress_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        task_layout.addWidget(self.progress)
        layout.addWidget(self.task_panel)

        self.log_card = ToolSectionCard("运行日志")
        self.log_card.setObjectName("convertLogCard")
        log_hint = QLabel("自动显示最新输出")
        log_hint.setProperty("role", "caption")
        self.log_card.heading_layout.addWidget(log_hint)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("convertLog")
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("扫描结果、转换进度和失败原因会显示在这里。")
        self.log_text.setMinimumHeight(100)
        self.log_card.content_layout.addWidget(self.log_text, 1)
        layout.addWidget(self.log_card, 1)
        self._update_dir_date_label()
        self._refresh_config_summary()

    # ==================== 钩子 ====================

    def on_return_press(self):
        if self._enter_stage == 0:
            self._enter_stage = 1
            self._scan()
            self._log("\n⏎ 再次按回车将开始转换（或点「开始转换」）\n")
        else:
            self._enter_stage = 0
            self._start_convert()

    def on_dir_changed(self, path):
        self.dir_label.setText(path)
        self.dir_label.setToolTip(path)
        self._update_dir_date_label()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 1 if self.width() < 620 else 2
        if columns == self._card_columns:
            return
        self._card_columns = columns
        self._cards_layout.removeWidget(self.output_card)
        self._cards_layout.addWidget(self.output_card, 0 if columns == 2 else 1,
                                     1 if columns == 2 else 0)
        self._cards_layout.setColumnStretch(1, 1 if columns == 2 else 0)

    def _update_dir_date_label(self):
        count = count_date_subfolders(self.app.work_dir)
        self.dir_date_label.setText(
            f"📅 {count} 个日期文件夹" if count else "⚠️ 无日期文件夹")

    # ==================== 配置/扫描 ====================

    def _save_config(self):
        self.config["output_extension"] = self.out_ext.currentText()
        self.config["concurrent_count"] = int(self.concurrent.currentText())
        self.config["preserve_metadata"] = self.preserve_var.isChecked()
        self.config["preserve_timestamps"] = self.timestamp_var.isChecked()
        self.config["enable_cache"] = self.cache_var.isChecked()
        self.config["enable_encode_fallback"] = self.encode_fallback_var.isChecked()
        self.config["delete_source_after_success"] = self.delete_src_var.isChecked()
        save_convert_config(self.config)
        self._refresh_config_summary()

    def _refresh_config_summary(self):
        formats = self.config.get("input_extensions", [".flv", ".mkv", ".ts"])
        self.input_formats_label.setText("输入格式  " + " · ".join(str(ext) for ext in formats))
        output_dir = self.config.get("output_dir", "")
        self.output_hint.setText("输出位置  " + (str(output_dir) if output_dir else "与源文件相同目录"))
        self.output_hint.setToolTip(str(output_dir) if output_dir else "转换后的文件保存在源文件旁。")

    def _scan(self):
        if not self.app.validate_work_dir():
            return
        self._save_config()
        self.config = load_convert_config()
        self._refresh_config_summary()
        root = Path(self.app.work_dir)
        self.cache = load_convert_cache() if self.config.get("enable_cache") else {}
        self.videos = find_videos(root, self.config)
        cached_count = sum(1 for v in self.videos if is_convert_cached(self.cache, v))

        total_size = sum(v.stat().st_size for v in self.videos if v.exists())
        self.stats_label.setText(
            f"共 {len(self.videos)} 个文件 | 总大小 {format_size(total_size)} | 缓存命中 {cached_count}")
        self.start_btn.setEnabled(bool(self.videos))
        self.phase_label.setText("扫描完成，可以开始转换" if self.videos else "未找到可转换文件")
        self.log_text.clear()

        if not self.videos:
            self._log("⚠️ 未找到可转换的视频文件，请检查工作目录和输入扩展名配置")
        else:
            self._log(f"📂 扫描完成，共找到 {len(self.videos)} 个文件:")
            for i, v in enumerate(self.videos, 1):
                try:
                    rel = v.relative_to(root)
                except ValueError:
                    rel = v
                size = format_size(v.stat().st_size)
                cached = "💾" if is_convert_cached(self.cache, v) else "  "
                self._log(f"  [{i}] {cached} {rel}  ({size})")
            self._log(f"\n总大小: {format_size(total_size)} | 缓存命中: {cached_count}")
            self._log("💡 按「开始转换」处理这些文件")

    # ==================== 转换 ====================

    def _start_convert(self):
        if not self.videos:
            return
        self._save_config()
        self.config = load_convert_config()
        self._refresh_config_summary()
        self.cache = load_convert_cache() if self.config.get("enable_cache") else {}
        self.running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self.phase_label.setText(f"正在处理 {len(self.videos)} 个文件")
        self.log_text.clear()
        self._log("🚀 开始转换...")
        threading.Thread(target=self._convert_thread, daemon=True).start()

    def _stop_convert(self):
        self.running = False
        self.phase_label.setText("正在停止当前任务")
        self._log("\n⏸️ 请求停止...")

    def _convert_thread(self):
        root = Path(self.app.work_dir)
        total = len(self.videos)
        stats = {"success": 0, "failed": 0, "skipped": 0, "lossless": 0, "encode": 0}
        failed_list = []

        for i, src in enumerate(self.videos):
            if not self.running:
                break
            try:
                if is_convert_cached(self.cache, src):
                    dst = get_output_path(src, root, self.config)
                    if dst.exists():
                        try:
                            rel = str(src.relative_to(root))
                        except ValueError:
                            rel = str(src)
                        self._log(f"💾 {rel} -> 缓存命中，跳过")
                        stats["skipped"] += 1
                        self._update_progress(i + 1, total)
                        continue

                result = convert_one_file(
                    src, root, self.config,
                    progress_callback=lambda msg: self._log(msg),
                    should_stop=lambda: not self.running,
                )
                if result.interrupted:
                    continue
                if result.success:
                    stats["success"] += 1
                    if result.mode == "lossless":
                        stats["lossless"] += 1
                    elif result.mode == "encode":
                        stats["encode"] += 1
                    update_convert_cache(self.cache, src, True, result.dst)
                else:
                    stats["failed"] += 1
                    failed_list.append(str(result.rel))
                    update_convert_cache(self.cache, src, False)
                if i % 5 == 0:
                    save_convert_cache(self.cache)
            except Exception as e:
                stats["failed"] += 1
                try:
                    failed_list.append(str(src.relative_to(root)))
                except ValueError:
                    failed_list.append(str(src))
                self._log(f"❌ 异常: {e}")
            self._update_progress(i + 1, total)

        save_convert_cache(self.cache)
        self._log("\n" + "=" * 50)
        self._log(f"✅ 完成: {stats['success'] + stats['skipped']} / {total}")
        self._log(f"   成功: {stats['success']} (无损{stats['lossless']}/转码{stats['encode']})")
        self._log(f"   跳过: {stats['skipped']} | 失败: {stats['failed']}")
        if failed_list:
            self._log(f"\n❌ 失败列表 ({len(failed_list)} 个):")
            for f in failed_list:
                self._log(f"   - {f}")
        post_to_main(self._finish)

    def _finish(self):
        completed = self.running
        self.running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self.phase_label.setText("本轮处理完成" if completed else "已停止")

    def _update_progress(self, current, total):
        if total:
            percent = int(current / total * 100)
            post_to_main(self.progress.setValue, percent)
            post_to_main(self.percent_label.setText, f"{percent}%")

    def _clear_cache(self):
        if QMessageBox.question(self, "确认", "确定要清除转换缓存吗？",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            save_convert_cache({})
            self.cache = {}
            self._log("🗑 缓存已清除")

    # ==================== 日志 ====================

    def _log(self, msg):
        post_to_main(self._insert_log, msg)

    def _insert_log(self, msg):
        self.log_text.append(msg)
        self.log_text.ensureCursorVisible()
