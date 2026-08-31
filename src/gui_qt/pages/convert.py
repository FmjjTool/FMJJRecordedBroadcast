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

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
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
from gui_qt.toolbox_common import count_date_subfolders


class ConvertPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []
        self.config = load_convert_config()
        self.cache = {}
        self.running = False
        self._enter_stage = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # 目录状态
        dir_row = QHBoxLayout()
        self.dir_label = QLabel(app.work_dir)
        self.dir_label.setStyleSheet(
            "background: transparent; color: #2f3440; padding: 4px 8px;")
        dir_row.addWidget(self.dir_label, 1)
        self.dir_date_label = QLabel("")
        self.dir_date_label.setStyleSheet("color: #888;")
        dir_row.addWidget(self.dir_date_label)
        layout.addLayout(dir_row)
        self._update_dir_date_label()

        # 扫描
        scan_row = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 扫描视频文件")
        self.scan_btn.clicked.connect(self._scan)
        scan_row.addWidget(self.scan_btn)
        self.stats_label = QLabel("")
        scan_row.addWidget(self.stats_label, 1)
        layout.addLayout(scan_row)

        # 选项
        opt = QGridLayout()
        opt.setHorizontalSpacing(12)
        opt.addWidget(QLabel("输出格式:"), 0, 0)
        self.out_ext = QComboBox()
        self.out_ext.addItems([".mp4", ".mkv", ".mov"])
        self.out_ext.setCurrentText(self.config.get("output_extension", ".mp4"))
        self.out_ext.currentTextChanged.connect(lambda _: self._save_config())
        opt.addWidget(self.out_ext, 0, 1)
        opt.addWidget(QLabel("并发:"), 0, 2)
        self.concurrent = QComboBox()
        self.concurrent.addItems(["1", "2", "3", "4"])
        self.concurrent.setCurrentText(str(self.config.get("concurrent_count", 1)))
        self.concurrent.currentTextChanged.connect(lambda _: self._save_config())
        opt.addWidget(self.concurrent, 0, 3)
        self.preserve_var = QCheckBox("保留元数据")
        self.preserve_var.setChecked(self.config.get("preserve_metadata", True))
        self.preserve_var.toggled.connect(lambda _: self._save_config())
        opt.addWidget(self.preserve_var, 1, 0)
        self.timestamp_var = QCheckBox("保留时间戳")
        self.timestamp_var.setChecked(self.config.get("preserve_timestamps", True))
        self.timestamp_var.toggled.connect(lambda _: self._save_config())
        opt.addWidget(self.timestamp_var, 1, 1)
        self.cache_var = QCheckBox("启用缓存")
        self.cache_var.setChecked(self.config.get("enable_cache", True))
        self.cache_var.toggled.connect(lambda _: self._save_config())
        opt.addWidget(self.cache_var, 1, 2)
        self.encode_fallback_var = QCheckBox("无损失败转码")
        self.encode_fallback_var.setChecked(self.config.get("enable_encode_fallback", False))
        self.encode_fallback_var.toggled.connect(lambda _: self._save_config())
        opt.addWidget(self.encode_fallback_var, 2, 0)
        self.delete_src_var = QCheckBox("成功后删源 ⚠️")
        self.delete_src_var.setChecked(self.config.get("delete_source_after_success", False))
        self.delete_src_var.toggled.connect(lambda _: self._save_config())
        opt.addWidget(self.delete_src_var, 2, 1)
        layout.addLayout(opt)

        # 按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶️ 开始转换")
        self.start_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.start_btn.clicked.connect(self._start_convert)
        self.start_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setStyleSheet("background: #c62828; color: white;")
        self.stop_btn.clicked.connect(self._stop_convert)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        clear_btn = QPushButton("🗑 清除转换缓存")
        clear_btn.setToolTip("清除本页的已转换记录（只影响缓存判定，不删除任何文件）：\n"
                             "清除后，之前被跳过的视频会重新参与转换")
        clear_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c8cdd6; color: #2f3440;")
        clear_btn.clicked.connect(self._clear_cache)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 进度
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        # 日志
        layout.addWidget(QLabel("运行日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

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
        self._update_dir_date_label()

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

    def _scan(self):
        if not self.app.validate_work_dir():
            return
        self._save_config()
        self.config = load_convert_config()
        root = Path(self.app.work_dir)
        self.cache = load_convert_cache() if self.config.get("enable_cache") else {}
        self.videos = find_videos(root, self.config)
        cached_count = sum(1 for v in self.videos if is_convert_cached(self.cache, v))

        total_size = sum(v.stat().st_size for v in self.videos if v.exists())
        self.stats_label.setText(
            f"共 {len(self.videos)} 个文件 | 总大小 {format_size(total_size)} | 缓存命中 {cached_count}")
        self.start_btn.setEnabled(bool(self.videos))
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
        self.cache = load_convert_cache() if self.config.get("enable_cache") else {}
        self.running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_text.clear()
        self._log("🚀 开始转换...")
        threading.Thread(target=self._convert_thread, daemon=True).start()

    def _stop_convert(self):
        self.running = False
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
        self._finish()

    def _finish(self):
        self.running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)

    def _update_progress(self, current, total):
        if total:
            post_to_main(self.progress.setValue, int(current / total * 100))

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
