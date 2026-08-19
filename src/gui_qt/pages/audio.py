"""音频提取页（PySide6 版）。"""

import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.audio_engine import find_audio_videos, extract_audio_one, scan_audio_by_date, delete_files_safe
from toolbox.utils import format_size

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage


class AudioPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []
        self.running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🎵 音频提取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        dir_row = QHBoxLayout()
        self.dir_label = QLabel(app.work_dir)
        self.dir_label.setStyleSheet("color: #888;")
        dir_row.addWidget(self.dir_label, 1)
        self.scan_btn = QPushButton("🔍 扫描视频")
        self.scan_btn.clicked.connect(self._scan)
        dir_row.addWidget(self.scan_btn)
        layout.addLayout(dir_row)
        self.count_label = QLabel("")
        layout.addWidget(self.count_label)

        # 复选框列表
        bar = QHBoxLayout()
        bar.addWidget(QLabel("📋 文件列表（默认勾选当天）:"))
        bar.addWidget(QPushButton("全选", clicked=lambda: self._sel("all")))
        bar.addWidget(QPushButton("全不选", clicked=lambda: self._sel("none")))
        bar.addWidget(QPushButton("仅当天", clicked=self._sel_today))
        bar.addStretch(1)
        layout.addLayout(bar)
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(160)
        layout.addWidget(self.file_list)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("🎵 开始提取")
        self.start_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        self.start_btn.clicked.connect(self._start)
        self.start_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        del_btn = QPushButton("🗑 删除日期内音频")
        del_btn.clicked.connect(self._delete_date_audio)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    def _log(self, msg):
        self.log_text.append(msg)

    def _today_str(self):
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    def _is_today(self, p):
        try:
            dt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone(timedelta(hours=8)))
            return dt.strftime("%Y-%m-%d") == self._today_str()
        except Exception:
            return False

    def _scan(self):
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            self._log("⚠️ 请先在全局设置指定工作目录")
            return
        self.videos = find_audio_videos(Path(self.app.work_dir))
        self.count_label.setText(f"共 {len(self.videos)} 个视频文件")
        self.log_text.clear()
        self._refresh_list()
        if not self.videos:
            self._log("⚠️ 未找到视频文件")
            return
        self._log(f"📂 扫描完成，共找到 {len(self.videos)} 个视频")

    def _refresh_list(self):
        self.file_list.clear()
        for v in self.videos:
            item = QListWidgetItem(f"{v.name}  ({format_size(v.stat().st_size)})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if self._is_today(v) else Qt.Unchecked)
            item.setData(Qt.UserRole, str(v))
            self.file_list.addItem(item)

    def _sel(self, mode):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.Checked if mode == "all" else Qt.Unchecked)

    def _sel_today(self):
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            it.setCheckState(Qt.Checked if self._is_today(Path(it.data(Qt.UserRole))) else Qt.Unchecked)

    def _checked(self):
        return [Path(self.file_list.item(i).data(Qt.UserRole)) for i in range(self.file_list.count())
                if self.file_list.item(i).checkState() == Qt.Checked]

    def _start(self):
        to_process = self._checked()
        if not to_process:
            self._log("⚠️ 未勾选任何文件")
            return
        self.running = True
        self._to_process = to_process
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)
        self.progress.setValue(0)
        self._log(f"🎵 开始提取音频（{len(to_process)} 个文件）...\n")
        threading.Thread(target=self._extract_thread, daemon=True).start()

    def _stop(self):
        self.running = False

    def _extract_thread(self):
        to_process = self._to_process
        total = len(to_process)
        stats = {"success": 0, "failed": 0, "skipped": 0}
        for i, src in enumerate(to_process):
            if not self.running:
                break
            dst = src.with_suffix(".m4a")
            if dst.exists():
                post_to_main(self._log, f"💾 {src.name} -> 已存在，跳过")
                stats["skipped"] += 1
            else:
                ok, err = extract_audio_one(src, dst)
                if ok:
                    stats["success"] += 1
                    post_to_main(self._log, f"  ✅ 完成 -> {dst.name}")
                else:
                    stats["failed"] += 1
                    post_to_main(self._log, f"  ❌ 失败: {err}")
            post_to_main(self.progress.setValue, int((i + 1) / total * 100))
        post_to_main(self._finish, stats)

    def _finish(self, stats):
        self.running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self._log(f"\n✅ 完成: 成功{stats['success']}, 失败{stats['failed']}, 跳过{stats['skipped']}")

    def _delete_date_audio(self):
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            return
        groups = scan_audio_by_date(Path(self.app.work_dir))
        all_files = [p for paths in groups.values() for p in paths]
        if not all_files:
            self._log("⚠️ 日期文件夹内未找到音频文件")
            return
        # 弹确认对话框（默认全选）
        dlg = QDialog(self)
        dlg.setWindowTitle("删除日期内音频")
        dlg.resize(560, 480)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"共 {len(all_files)} 个音频文件（默认全选，取消勾选可保留）"))
        lst = QListWidget()
        for p in all_files:
            item = QListWidgetItem(str(p))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, str(p))
            lst.addItem(item)
        v.addWidget(lst)
        btn = QPushButton("🗑 确认删除选中")
        btn.setStyleSheet("background: #c62828; color: white; padding: 6px; border-radius: 4px;")
        btn.clicked.connect(lambda: self._do_delete(dlg, lst))
        v.addWidget(btn)
        dlg.exec()

    def _do_delete(self, dlg, lst):
        to_delete = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                     if lst.item(i).checkState() == Qt.Checked]
        dlg.accept()
        if not to_delete:
            self._log("⚠️ 未勾选任何文件，未删除")
            return
        ok, failed = delete_files_safe(to_delete)
        self._log(f"🗑 已删除 {ok} 个音频（移入回收站）")
        for f in failed[:10]:
            self._log(f"  ❌ {f}")
