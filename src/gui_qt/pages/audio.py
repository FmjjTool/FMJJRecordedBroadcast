"""音频提取页（PySide6 版）。

对应原版 gui/toolbox_pages.py AudioPage：
- 目录栏：工作目录 + 日期文件夹计数（随全局工作目录变化）
- 扫描视频 → 复选框文件列表（默认勾选当天 = 北京 UTC+8，按 mtime 判断）
- 全选 / 全不选 / 仅当天；开始按钮随勾选数联动
- 开始提取（后台线程，可停止）/ 停止；进度条 + 日志
- 回车快捷：第一次回车=扫描，第二次回车=开始提取
- 删除日期内音频：弹复选框对话框（默认全选、全选/全不选/取消），后台移入回收站
- 外部拖入：拖入视频文件/文件夹 → 追加到文件列表（标记「外部」，不限工作目录）
"""

import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidgetItem, QProgressBar,
    QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.audio_engine import (
    delete_files_safe, extract_audio_one, find_audio_videos, scan_audio_by_date,
)
from toolbox.utils import format_size, is_video_file

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList
from gui_qt.toolbox_common import count_date_subfolders


class AudioPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []
        self.external_paths = []  # 拖入的外部视频（工作目录之外，扫描/重扫后保留）
        self.running = False
        self._enter_stage = 0  # 回车快捷：0=待扫描，1=待提取

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # 目录栏：工作目录 + 日期文件夹计数
        dir_row = QHBoxLayout()
        self.dir_label = QLabel(app.work_dir)
        dir_row.addWidget(self.dir_label, 1)
        self.dir_date_label = QLabel("")
        self.dir_date_label.setStyleSheet("color: #888;")
        dir_row.addWidget(self.dir_date_label)
        layout.addLayout(dir_row)
        self._update_dir_date_label()

        # 控制栏
        ctrl_row = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 扫描视频")
        self.scan_btn.clicked.connect(self._scan)
        ctrl_row.addWidget(self.scan_btn)
        self.count_label = QLabel("")
        ctrl_row.addWidget(self.count_label)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("🎵 开始提取")
        self.start_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.start_btn.clicked.connect(self._start)
        self.start_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setStyleSheet("background: #c62828; color: white;")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        del_btn = QPushButton("🗑 删除日期内音频")
        del_btn.setStyleSheet("background: #8e3b2f; color: white;")
        del_btn.clicked.connect(self._delete_date_audio)
        btn_row.addWidget(del_btn)
        layout.addLayout(btn_row)

        # 扫描后文件复选框列表（默认勾选当天 = 北京 UTC+8）
        head = QHBoxLayout()
        self.list_head_label = QLabel("（扫描后显示文件列表，默认勾选当天）")
        self.list_head_label.setStyleSheet("color: #888; font-size: 11px;")
        head.addWidget(self.list_head_label)
        head.addStretch(1)
        head.addWidget(self._mini_btn("全选", lambda: self._set_all_checks(True)))
        head.addWidget(self._mini_btn("全不选", lambda: self._set_all_checks(False)))
        head.addWidget(self._mini_btn("仅当天", self._check_today_only))
        layout.addLayout(head)
        self.file_list = RowCheckList()
        self.file_list.setMaximumHeight(180)
        self.file_list.itemChanged.connect(lambda _it: self._refresh_start_btn())
        layout.addWidget(self.file_list)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    def _mini_btn(self, text, slot):
        b = QPushButton(text)
        b.setFixedHeight(22)
        b.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        b.clicked.connect(slot)
        return b

    # ==================== 回车快捷 ====================

    def on_return_press(self):
        if self._enter_stage == 0:
            self._enter_stage = 1
            self._scan()
            self._log("⏎ 再次按回车将开始提取（或点「开始提取」）")
        else:
            self._enter_stage = 0
            self._start()

    # ==================== 北京 UTC+8 当天判断 ====================

    @staticmethod
    def _today_bj_str():
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    @staticmethod
    def _is_today_bj(path):
        """文件 mtime 转为北京时区后，日期是否 == 北京今天。"""
        try:
            ts = Path(path).stat().st_mtime
            dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            return dt.strftime("%Y-%m-%d") == AudioPage._today_bj_str()
        except Exception:
            return False

    # ==================== 目录 ====================

    def on_dir_changed(self, path):
        self.dir_label.setText(path)
        self._update_dir_date_label()

    def _update_dir_date_label(self):
        count = count_date_subfolders(self.app.work_dir)
        self.dir_date_label.setText(
            f"📅 {count} 个日期文件夹" if count else "⚠️ 无日期文件夹")

    # ==================== 扫描 ====================

    def _rel(self, v):
        try:
            return v.relative_to(Path(self.app.work_dir))
        except ValueError:
            return v.name

    def _scan(self):
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            self._log("⚠️ 工作目录不存在，请先在全局设置指定")
            return
        root = Path(self.app.work_dir)
        self.videos = find_audio_videos(root)
        self.count_label.setText(f"共 {len(self.videos)} 个视频文件")
        self.start_btn.setEnabled(False)
        self.log_text.clear()
        if not self.videos:
            self._log("⚠️ 未找到视频文件")
        else:
            self._log(f"📂 扫描完成，共找到 {len(self.videos)} 个视频:")
            for i, v in enumerate(self.videos, 1):
                size = format_size(v.stat().st_size) if v.exists() else "?"
                self._log(f"  [{i}] {self._rel(v)}  ({size})")
            self._log("💡 在下方勾选要提取的文件（默认勾选当天），按「开始提取」")
        self._render_file_checks(self.videos)
        self._refresh_start_btn()

    def _render_file_checks(self, videos):
        """渲染扫描结果的文件复选框列表。默认勾选当天（北京 UTC+8）文件。
        拖入的外部文件（不在本次扫描结果里）追加在列表尾部并保留勾选状态。"""
        self.file_list.blockSignals(True)
        self.file_list.clear()
        if not videos:
            self.list_head_label.setText("（扫描后显示文件列表，默认勾选当天）")
        else:
            self.list_head_label.setText(
                f"📋 扫描到 {len(videos)} 个文件（默认勾选当天 = {self._today_bj_str()}）")
        scanned = {str(v) for v in videos}
        for v in videos:
            is_today = self._is_today_bj(v)
            try:
                size = format_size(v.stat().st_size) if v.exists() else "?"
            except Exception:
                size = "?"
            tag = " 🕐当天" if is_today else ""
            item = QListWidgetItem(f"{self._rel(v)}  ({size}){tag}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if is_today else Qt.Unchecked)
            item.setData(Qt.UserRole, str(v))
            self.file_list.addItem(item)
        for f in self.external_paths:
            if f in scanned:
                continue
            self._append_external_item(f, Qt.Checked)
        self.file_list.blockSignals(False)

    def _append_external_item(self, path, check_state):
        """外部拖入文件加入列表（工作目录外显示完整路径 + 外部标记）。"""
        try:
            size = format_size(Path(path).stat().st_size) if Path(path).exists() else "?"
        except Exception:
            size = "?"
        under_work = self.app.work_dir and str(path).startswith(
            str(Path(self.app.work_dir).resolve()))
        display = self._rel(Path(path)) if under_work else str(path)
        tag = "" if under_work else " 📥外部"
        item = QListWidgetItem(f"{display}  ({size}){tag}")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(check_state)
        item.setData(Qt.UserRole, str(path))
        self.file_list.addItem(item)

    # ==================== 外部拖入 ====================

    def add_dropped_files(self, files):
        """拖入视频文件 → 追加到列表（默认勾选，可提取）。"""
        existing = {self.file_list.item(i).data(Qt.UserRole)
                    for i in range(self.file_list.count())}
        added = []
        for f in files:
            if not is_video_file(f) or f in self.external_paths or f in existing:
                continue
            self.external_paths.append(f)
            added.append(f)
        if added:
            self.file_list.blockSignals(True)
            for f in added:
                self._append_external_item(f, Qt.Checked)
            self.file_list.blockSignals(False)
            self._log(f"📥 拖放添加 {len(added)} 个外部视频:")
            for f in added:
                self._log(f"  + {os.path.basename(f)}")
        else:
            self._log("📥 拖放：无新增视频文件（仅支持视频文件，重复自动忽略）")
        self._refresh_start_btn()

    def add_dropped_folder(self, folder):
        """拖入文件夹 → 递归收集其中的视频文件加入列表。"""
        found = []
        for root, _dirs, names in os.walk(folder):
            for n in names:
                fp = os.path.join(root, n)
                if is_video_file(fp):
                    found.append(fp)
        if found:
            self.add_dropped_files(found)
        else:
            self._log(f"📥 拖放文件夹: {folder}（其中没有视频文件）")

    def _set_all_checks(self, val):
        self.file_list.blockSignals(True)
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.Checked if val else Qt.Unchecked)
        self.file_list.blockSignals(False)
        self._refresh_start_btn()

    def _check_today_only(self):
        self.file_list.blockSignals(True)
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            it.setCheckState(Qt.Checked if self._is_today_bj(it.data(Qt.UserRole)) else Qt.Unchecked)
        self.file_list.blockSignals(False)
        self._refresh_start_btn()

    # ==================== 提取 ====================

    def _selected_videos(self):
        return [Path(self.file_list.item(i).data(Qt.UserRole))
                for i in range(self.file_list.count())
                if self.file_list.item(i).checkState() == Qt.Checked]

    def _refresh_start_btn(self):
        selected = self._selected_videos()
        self.start_btn.setEnabled(bool(selected))
        total = self.file_list.count()
        if total or self.videos:
            self.count_label.setText(f"共 {total} 个视频，已勾选 {len(selected)}")

    def _start(self):
        to_process = self._selected_videos()
        if not to_process:
            self._log("⚠️ 未勾选任何文件")
            return
        self.running = True
        self._to_process = to_process
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_text.clear()
        self._log(f"🎵 开始提取音频（{len(to_process)} 个文件）...")
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
            try:
                dst = src.with_suffix(".m4a")
                if dst.exists():
                    post_to_main(self._log, f"💾 {src.name} -> 已存在，跳过")
                    stats["skipped"] += 1
                else:
                    post_to_main(self._log, f"📂 {src.name}")
                    ok, err = extract_audio_one(src, dst)
                    if ok:
                        stats["success"] += 1
                        post_to_main(self._log, f"  ✅ 完成 -> {dst.name}")
                    else:
                        stats["failed"] += 1
                        post_to_main(self._log, f"  ❌ 失败: {err}")
            except Exception as e:
                stats["failed"] += 1
                post_to_main(self._log, f"  ❌ 异常: {e}")
            post_to_main(self.progress.setValue, int((i + 1) / total * 100))

        post_to_main(self._log,
                     f"\n✅ 完成: 成功{stats['success']}, 失败{stats['failed']}, 跳过{stats['skipped']}")
        post_to_main(self._finish)

    def _finish(self):
        self.running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self._refresh_start_btn()

    def _log(self, msg):
        self.log_text.append(str(msg))

    # ==================== 删除日期内音频（结果写入日志） ====================

    def _delete_date_audio(self):
        """弹复选框对话框，让用户确认要删除的音频（默认全选）。"""
        if not os.path.isdir(self.app.work_dir):
            self._log("⚠️ 工作目录不存在，无法删除")
            return
        groups = scan_audio_by_date(Path(self.app.work_dir))
        all_files = [p for paths in groups.values() for p in paths]
        if not all_files:
            self._log("⚠️ 日期文件夹内未找到音频文件，无需删除")
            return
        self._show_delete_audio_dialog(all_files)

    def _show_delete_audio_dialog(self, all_files):
        """弹带复选框的删除确认对话框，默认全选。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("删除日期内音频")
        dlg.resize(600, 580)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            f"共 {len(all_files)} 个音频文件（默认全选，取消勾选可保留）"))

        lst = RowCheckList()
        for p in all_files:
            try:
                size = format_size(p.stat().st_size) if p.exists() else "?"
            except Exception:
                size = "?"
            item = QListWidgetItem(f"{self._rel(p)}  ({size})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, str(p))
            lst.addItem(item)
        v.addWidget(lst, 1)

        btn_frame = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: self._dlg_set_checks(lst, True))
        btn_frame.addWidget(all_btn)
        none_btn = QPushButton("全不选")
        none_btn.clicked.connect(lambda: self._dlg_set_checks(lst, False))
        btn_frame.addWidget(none_btn)
        btn_frame.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dlg.reject)
        btn_frame.addWidget(cancel_btn)
        ok_btn = QPushButton("🗑 确认删除选中")
        ok_btn.setStyleSheet("background: #c62828; color: white;")
        ok_btn.clicked.connect(lambda: self._do_delete_audio(dlg, lst))
        btn_frame.addWidget(ok_btn)
        v.addLayout(btn_frame)
        dlg.exec()

    def _dlg_set_checks(self, lst, val):
        for i in range(lst.count()):
            lst.item(i).setCheckState(Qt.Checked if val else Qt.Unchecked)

    def _do_delete_audio(self, dlg, lst):
        to_delete = [Path(lst.item(i).data(Qt.UserRole)) for i in range(lst.count())
                     if lst.item(i).checkState() == Qt.Checked]
        dlg.accept()
        if not to_delete:
            self._log("⚠️ 未勾选任何文件，未删除")
            return
        self._log(f"🗑 开始删除 {len(to_delete)} 个音频（移入回收站）...")

        def work():
            return delete_files_safe(to_delete)

        def done(result, err):
            if err:
                self._log(f"❌ 删除失败: {err}")
                return
            ok, failed = result
            self._log(f"✅ 已删除 {ok} 个音频（移入回收站）")
            for f in failed[:20]:
                self._log(f"  ❌ {f}")
            if failed:
                self._log(f"  （共有 {len(failed)} 个失败）")

        run_bg(work, done)
