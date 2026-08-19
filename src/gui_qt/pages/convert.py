"""视频转换页（PySide6 版）。"""

import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from toolbox.config_manager import (
    load_convert_config, save_convert_config, load_convert_cache, save_convert_cache,
)
from toolbox.convert_engine import find_videos, is_convert_cached, convert_one_file, get_output_path, update_convert_cache
from toolbox.utils import format_size

from gui_qt.pages.base import BasePage


class ConvertPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []
        self.config = load_convert_config()
        self.cache = {}
        self.running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🔄 视频转换")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        # 扫描
        dir_row = QHBoxLayout()
        self.dir_label = QLabel(app.work_dir)
        self.dir_label.setStyleSheet("color: #888;")
        dir_row.addWidget(self.dir_label, 1)
        self.scan_btn = QPushButton("🔍 扫描视频文件")
        self.scan_btn.clicked.connect(self._scan)
        dir_row.addWidget(self.scan_btn)
        layout.addLayout(dir_row)
        self.stats_label = QLabel("")
        layout.addWidget(self.stats_label)

        # 选项
        opt = QGridLayout()
        opt.addWidget(QLabel("输出格式:"), 0, 0)
        self.out_ext = QComboBox()
        self.out_ext.addItems([".mp4", ".mkv", ".mov"])
        self.out_ext.setCurrentText(self.config.get("output_extension", ".mp4"))
        opt.addWidget(self.out_ext, 0, 1)
        opt.addWidget(QLabel("并发:"), 0, 2)
        self.concurrent = QComboBox()
        self.concurrent.addItems(["1", "2", "3", "4"])
        self.concurrent.setCurrentText(str(self.config.get("concurrent_count", 1)))
        opt.addWidget(self.concurrent, 0, 3)
        self.preserve = QCheckBox("保留元数据")
        self.preserve.setChecked(self.config.get("preserve_metadata", True))
        opt.addWidget(self.preserve, 1, 0)
        self.timestamp = QCheckBox("保留时间戳")
        self.timestamp.setChecked(self.config.get("preserve_timestamps", True))
        opt.addWidget(self.timestamp, 1, 1)
        self.enable_cache = QCheckBox("启用缓存")
        self.enable_cache.setChecked(self.config.get("enable_cache", True))
        opt.addWidget(self.enable_cache, 1, 2)
        self.delete_src = QCheckBox("成功后删源 ⚠️")
        self.delete_src.setChecked(self.config.get("delete_source_after_success", False))
        opt.addWidget(self.delete_src, 1, 3)
        layout.addLayout(opt)

        # 按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶️ 开始转换")
        self.start_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        self.start_btn.clicked.connect(self._start)
        self.start_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 进度
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    def _log(self, msg):
        self.log_text.append(msg)

    def _save_config(self):
        self.config["output_extension"] = self.out_ext.currentText()
        self.config["concurrent_count"] = int(self.concurrent.currentText())
        self.config["preserve_metadata"] = self.preserve.isChecked()
        self.config["preserve_timestamps"] = self.timestamp.isChecked()
        self.config["enable_cache"] = self.enable_cache.isChecked()
        self.config["delete_source_after_success"] = self.delete_src.isChecked()
        save_convert_config(self.config)

    def _scan(self):
        if not self.app.work_dir or not __import__('os').path.isdir(self.app.work_dir):
            self._log("⚠️ 请先在全局设置指定工作目录")
            return
        self._save_config()
        self.config = load_convert_config()
        root = Path(self.app.work_dir)
        self.cache = load_convert_cache() if self.config.get("enable_cache") else {}
        self.videos = find_videos(root, self.config)
        cached = sum(1 for v in self.videos if is_convert_cached(self.cache, v))
        total_size = sum(v.stat().st_size for v in self.videos if v.exists())
        self.stats_label.setText(f"共 {len(self.videos)} 个文件 | 总大小 {format_size(total_size)} | 缓存命中 {cached}")
        self.start_btn.setEnabled(bool(self.videos))
        self.log_text.clear()
        if not self.videos:
            self._log("⚠️ 未找到可转换的视频文件")
            return
        self._log(f"📂 扫描完成，共找到 {len(self.videos)} 个文件\n")

    def _start(self):
        if not self.videos or self.running:
            return
        self._save_config()
        self.running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)
        threading.Thread(target=self._convert_thread, daemon=True).start()

    def _stop(self):
        self.running = False

    def _convert_thread(self):
        root = Path(self.app.work_dir)
        total = len(self.videos)
        ok = fail = skip = 0
        for i, src in enumerate(self.videos):
            if not self.running:
                break
            dst = get_output_path(src, root, self.config)
            if dst.exists() and not is_convert_cached(self.cache, src):
                self._log(f"💾 {src.name} -> 已存在，跳过")
                skip += 1
            else:
                success, msg, _ = convert_one_file(src, dst, self.config)
                if success:
                    ok += 1
                    self._log(f"✅ {src.name}")
                else:
                    fail += 1
                    self._log(f"❌ {src.name}: {msg}")
            update_convert_cache(self.cache, src, True, dst)
            QTimer.singleShot(0, lambda v=int((i + 1) / total * 100): self.progress.setValue(v))
        save_convert_cache(self.cache)
        QTimer.singleShot(0, lambda: self._finish(ok, fail, skip))

    def _finish(self, ok, fail, skip):
        self.running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log(f"\n✅ 完成: 成功 {ok}，失败 {fail}，跳过 {skip}")
