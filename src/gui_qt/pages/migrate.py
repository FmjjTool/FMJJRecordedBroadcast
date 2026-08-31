"""文件迁移页（PySide6 版）：录播工作流最后一步。

对应原版 gui/toolbox_pages.py MigratePage：
- 源目录 = 全局工作目录；目标目录选择后持久化
- 剪切 / 复制两种模式（单选）
- 扫描待迁移：日期文件夹（YYYYMMDD）勾选列表（全选/反选/清除，默认勾选当天）
- 开始迁移：确认后后台执行；目标已存在同名文件夹 → 文件级合并，同名文件记录重复
- 重复项报告：逐项「覆盖目标 / 删除本地」，支持批量（全部覆盖 / 全部删除 / 跳过全部）
"""

import os
import shutil
import threading
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QRadioButton, QScrollArea, QTextEdit,
    QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import DATE_FOLDER_RE


class MigratePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.pending = []       # 待迁移的日期文件夹名列表
        self.duplicates = []    # 重复项：[{name, src, dst}]
        self._dup_rows = {}     # 报告窗口行引用 id(item) -> row
        cfg = load_convert_config()
        self.target_dir = cfg.get("migrate_target_dir", "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # 源目录
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("源目录:"))
        self.src_label = QLabel(app.work_dir)
        src_row.addWidget(self.src_label, 1)
        layout.addLayout(src_row)

        # 目标目录
        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel("目标:"))
        self.tgt_label = QLabel(self.target_dir or "（未设置，点击右侧选择）")
        if not self.target_dir:
            self.tgt_label.setStyleSheet("color: #888;")
        tgt_row.addWidget(self.tgt_label, 1)
        choose_btn = QPushButton("📁 选择目标")
        choose_btn.clicked.connect(self._choose_target)
        tgt_row.addWidget(choose_btn)
        layout.addLayout(tgt_row)

        # 操作模式
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("模式:"))
        self.move_radio = QRadioButton("✂️ 剪切（移动）")
        self.move_radio.setChecked(True)
        mode_row.addWidget(self.move_radio)
        self.copy_radio = QRadioButton("📄 复制（保留源）")
        mode_row.addWidget(self.copy_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        # 控制按钮
        ctrl = QHBoxLayout()
        scan_btn = QPushButton("🔍 扫描待迁移")
        scan_btn.clicked.connect(self._scan)
        ctrl.addWidget(scan_btn)
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: self._set_all_checks(True))
        ctrl.addWidget(all_btn)
        invert_btn = QPushButton("反选")
        invert_btn.clicked.connect(self._invert_checks)
        ctrl.addWidget(invert_btn)
        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(lambda: self._set_all_checks(False))
        ctrl.addWidget(clear_btn)
        ctrl.addStretch(1)
        self.checked_lbl = QLabel("")
        self.checked_lbl.setStyleSheet("color: #888;")
        ctrl.addWidget(self.checked_lbl)
        self.migrate_btn = QPushButton("🚚 开始迁移")
        self.migrate_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.migrate_btn.clicked.connect(self._migrate)
        ctrl.addWidget(self.migrate_btn)
        layout.addLayout(ctrl)

        # 勾选列表 + 日志
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(2, 2, 2, 2)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_container)
        layout.addWidget(self.list_scroll, 1)
        self.folder_checks = {}  # 文件夹名 -> QCheckBox
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(120)
        layout.addWidget(self.log_text)

    # ==================== 目录/模式 ====================

    def on_dir_changed(self, path):
        self.src_label.setText(path)

    def _mode(self):
        return "copy" if self.copy_radio.isChecked() else "move"

    def _choose_target(self):
        path = QFileDialog.getExistingDirectory(self, "选择迁移目标目录")
        if not path:
            return
        self.target_dir = path
        cfg = load_convert_config()
        cfg["migrate_target_dir"] = path
        save_convert_config(cfg)
        self.tgt_label.setText(path)
        self.tgt_label.setStyleSheet("")
        self._insert_log(f"✅ 目标路径已保存: {path}")

    # ==================== 扫描/迁移 ====================

    def _scan(self):
        self.pending = []
        self.folder_checks = {}
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.list_layout.addStretch(1)
        src = self.app.work_dir
        if not os.path.isdir(src):
            self._list_placeholder("⚠️ 源目录不存在，请先在「全局设置」选择工作目录")
            return
        try:
            names = sorted(os.listdir(src))
        except OSError as e:
            self._list_placeholder(f"⚠️ 无法读取源目录: {e}")
            return
        for name in names:
            full = os.path.join(src, name)
            if os.path.isdir(full) and DATE_FOLDER_RE.match(name):
                self.pending.append(name)
        if not self.pending:
            self._list_placeholder("⚠️ 未找到日期文件夹（YYYYMMDD）")
            self._refresh_checked_lbl()
            return
        today = datetime.now().strftime("%Y%m%d")
        for name in self.pending:
            full = os.path.join(src, name)
            try:
                cnt = len(os.listdir(full))
            except Exception:
                cnt = 0
            cb = QCheckBox(f"📁 {name}  ({cnt} 项)")
            # 默认勾选当天（前 8 位日期 == 今天）
            cb.setChecked(name[:8] == today)
            cb.stateChanged.connect(self._refresh_checked_lbl)
            row = QFrame()
            row.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.addWidget(cb)
            self.list_layout.takeAt(self.list_layout.count() - 1)
            self.list_layout.addWidget(row)
            self.list_layout.addStretch(1)
            self.folder_checks[name] = cb
        self._refresh_checked_lbl()

    def _list_placeholder(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #888;")
        lbl.setAlignment(Qt.AlignCenter)
        self.list_layout.takeAt(self.list_layout.count() - 1)
        self.list_layout.addWidget(lbl)
        self.list_layout.addStretch(1)
        self._refresh_checked_lbl()

    def _checked_folders(self):
        return [name for name in self.pending
                if name in self.folder_checks and self.folder_checks[name].isChecked()]

    def _set_all_checks(self, checked):
        for cb in self.folder_checks.values():
            cb.setChecked(checked)
        self._refresh_checked_lbl()

    def _invert_checks(self):
        for cb in self.folder_checks.values():
            cb.setChecked(not cb.isChecked())
        self._refresh_checked_lbl()

    def _refresh_checked_lbl(self):
        n = len(self._checked_folders())
        total = len(self.folder_checks)
        self.checked_lbl.setText(f"已勾选 {n}/{total}" if total else "")

    def _migrate(self):
        if not self.pending:
            self.app.toast("请先扫描待迁移文件夹", "warning")
            return
        checked = self._checked_folders()
        if not checked:
            self.app.toast("请先勾选要迁移的日期文件夹（全选/反选/清除）", "warning")
            return
        if not self.target_dir or not os.path.isdir(self.target_dir):
            self.app.toast("请先选择有效的目标目录", "warning")
            return
        if self._paths_nested(self.app.work_dir, self.target_dir):
            self.app.toast("目标目录不能是源目录的父目录/子目录（会递归迁移）", "error")
            return
        mode = self._mode()
        mode_text = "复制（保留源）" if mode == "copy" else "剪切（移动）"
        if QMessageBox.question(
                self, "确认迁移",
                f"操作模式: {mode_text}\n"
                f"将迁移 {len(checked)} 个日期文件夹到:\n{self.target_dir}\n\n确认？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        self.migrate_btn.setEnabled(False)
        self.log_text.clear()
        self.duplicates = []
        # 后台线程执行迁移，避免大文件操作阻塞 UI
        threading.Thread(target=self._migrate_worker, args=(mode, checked), daemon=True).start()

    def _migrate_worker(self, mode, folders):
        """后台线程：执行迁移（文件操作不阻塞界面）。"""
        success = fail = 0
        for name in folders:
            src_dir = os.path.join(self.app.work_dir, name)
            dst_dir = os.path.join(self.target_dir, name)
            if not os.path.isdir(src_dir):
                self._log(f"⏭️ 跳过（源不存在）: {name}")
                fail += 1
                continue
            # 目标文件夹不存在 → 整体迁移
            if not os.path.exists(dst_dir):
                try:
                    if mode == "copy":
                        shutil.copytree(src_dir, dst_dir)
                    else:
                        shutil.move(src_dir, dst_dir)
                    self._log(f"✅ 成功: {name}")
                    success += 1
                except Exception as e:
                    self._log(f"❌ 失败: {name} → {e}")
                    fail += 1
                continue
            # 目标文件夹已存在 → 文件级合并（逐文件比对文件名）
            moved, dup_count, has_error = self._merge_folder(src_dir, dst_dir, name, mode)
            if has_error:
                fail += 1
            elif moved > 0 or dup_count == 0:
                success += 1
        self._log(f"\n迁移完成: 成功 {success} 个文件夹 / 失败 {fail} 个文件夹 / "
                  f"重复 {len(self.duplicates)} 个文件")
        post_to_main(self._migrate_done)

    def _migrate_done(self):
        """迁移结束（主线程）：恢复按钮，重扫，弹重复项报告。"""
        self.migrate_btn.setEnabled(True)
        self._scan()
        if self.duplicates:
            self._show_duplicate_report()

    def _log(self, text):
        """线程安全的日志：后台线程经 post_to_main 回主线程。"""
        post_to_main(self._insert_log, text)

    def _insert_log(self, text):
        self.log_text.append(text)
        self.log_text.ensureCursorVisible()

    def _paths_nested(self, a, b):
        """判断两个路径是否互为父子关系（避免迁移递归）。"""
        a = os.path.abspath(a)
        b = os.path.abspath(b)
        try:
            common = os.path.commonpath([a, b])
        except ValueError:
            return False  # 不同盘符，无共同路径
        return common == a or common == b

    def _merge_folder(self, src_dir, dst_dir, folder_name, mode):
        """文件级合并：逐文件比对文件名，同名跳过记录重复，不同名迁移。

        返回 (moved, dup_count, has_error)。
        """
        moved = 0
        dup_count = 0
        has_error = False
        for root, dirs, files in os.walk(src_dir):
            rel_root = os.path.relpath(root, src_dir)
            dst_root = dst_dir if rel_root == "." else os.path.join(dst_dir, rel_root)
            for f in files:
                src_file = os.path.join(root, f)
                dst_file = os.path.join(dst_root, f)
                if os.path.exists(dst_file):
                    # 同名文件已存在 → 记录重复，跳过
                    self.duplicates.append({
                        "folder": folder_name,
                        "name": os.path.relpath(src_file, src_dir),
                        "src": src_file,
                        "dst": dst_file,
                    })
                    dup_count += 1
                    continue
                try:
                    os.makedirs(dst_root, exist_ok=True)
                    if mode == "copy":
                        shutil.copy2(src_file, dst_file)
                    else:
                        shutil.move(src_file, dst_file)
                    moved += 1
                except Exception as e:
                    self._log(f"❌ 迁移失败: {os.path.relpath(src_file, src_dir)} → {e}")
                    has_error = True
        # 剪切模式：迁移后清理源中已迁走的空目录（重复文件所在的目录保留）
        if mode == "move":
            for root, dirs, files in os.walk(src_dir, topdown=False):
                try:
                    os.rmdir(root)
                except OSError:
                    pass
        self._log(f"🔀 合并 {folder_name}: 迁移 {moved} 文件, 重复 {dup_count} 文件")
        return moved, dup_count, has_error

    # ==================== 重复项报告 ====================

    def _do_resolve(self, item, action):
        """执行单个重复文件项的处理，返回 (ok, msg)。action: overwrite / delete_local。"""
        name, src, dst = item["name"], item["src"], item["dst"]
        try:
            if action == "overwrite":
                # 覆盖目标：删除目标同名文件，再把源文件复制/移动过去
                if os.path.exists(dst):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.remove(dst)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if self._mode() == "copy":
                    shutil.copy2(src, dst)
                else:
                    shutil.move(src, dst)
                return True, f"✅ 覆盖目标: {name}"
            elif action == "delete_local":
                # 删除本地：目标已有一份，本地这份是重复的
                if os.path.isdir(src):
                    shutil.rmtree(src)
                else:
                    os.remove(src)
                return True, f"🗑 已删除本地: {name}"
        except Exception as e:
            return False, f"❌ 处理失败: {name} → {e}"
        return False, f"❌ 未知操作: {name}"

    def _show_duplicate_report(self):
        """弹出重复项报告窗口，用户对每个重复项选择处理方式（处理一个移除一行，不重建窗口）。"""
        if not self.duplicates:
            return
        win = QDialog(self)
        win.setWindowTitle("重复项报告")
        win.resize(760, 540)
        v = QVBoxLayout(win)
        v.setContentsMargins(12, 14, 12, 10)
        self._dup_title_label = QLabel(f"⚠️ 检测到 {len(self.duplicates)} 个重复文件")
        self._dup_title_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        v.addWidget(self._dup_title_label)
        hint = QLabel("目标位置已存在同名文件，请选择每个重复项的处理方式")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._dup_layout = QVBoxLayout(container)
        self._dup_layout.setContentsMargins(2, 2, 2, 2)
        self._dup_layout.setSpacing(2)
        self._dup_layout.addStretch(1)
        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        self._dup_rows = {}
        for it in self.duplicates:
            self._dup_add_row(it, win)

        # 底部批量操作
        bottom = QHBoxLayout()
        all_over_btn = QPushButton("全部覆盖目标")
        all_over_btn.setStyleSheet("background: #c62828; color: white;")
        all_over_btn.clicked.connect(lambda: self._resolve_all("overwrite", win))
        bottom.addWidget(all_over_btn)
        all_del_btn = QPushButton("全部删除本地")
        all_del_btn.setStyleSheet("background: #ef6c00; color: white;")
        all_del_btn.clicked.connect(lambda: self._resolve_all("delete_local", win))
        bottom.addWidget(all_del_btn)
        bottom.addStretch(1)
        skip_btn = QPushButton("跳过全部")
        skip_btn.setStyleSheet("background: transparent; border: 1px solid #c8cdd6;")
        skip_btn.clicked.connect(win.accept)
        bottom.addWidget(skip_btn)
        v.addLayout(bottom)
        win.exec()

    def _dup_add_row(self, it, win):
        from PySide6.QtWidgets import QFrame
        display = f"{it['folder']}/{it['name']}" if it.get("folder") else it["name"]
        row = QFrame()
        row.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        lbl = QLabel(f"📄 {display}")
        h.addWidget(lbl, 1)
        over_btn = QPushButton("覆盖目标")
        over_btn.setFixedSize(90, 28)
        over_btn.setStyleSheet("background: #c62828; color: white;")
        over_btn.clicked.connect(lambda _=False, i=it: self._resolve_one(i, "overwrite", win))
        h.addWidget(over_btn)
        del_btn = QPushButton("删除本地")
        del_btn.setFixedSize(90, 28)
        del_btn.setStyleSheet("background: #ef6c00; color: white;")
        del_btn.clicked.connect(lambda _=False, i=it: self._resolve_one(i, "delete_local", win))
        h.addWidget(del_btn)
        self._dup_layout.takeAt(self._dup_layout.count() - 1)
        self._dup_layout.addWidget(row)
        self._dup_layout.addStretch(1)
        self._dup_rows[id(it)] = row

    def _resolve_one(self, item, action, win):
        """处理单个重复项：成功后从窗口移除该行（不重建窗口）。"""
        ok, msg = self._do_resolve(item, action)
        self._insert_log(msg)
        if ok:
            self.duplicates = [d for d in self.duplicates if d is not item]
            row = self._dup_rows.pop(id(item), None)
            if row:
                row.deleteLater()
            try:
                self._dup_title_label.setText(f"⚠️ 检测到 {len(self.duplicates)} 个重复文件")
            except Exception:
                pass
        if not self.duplicates:
            try:
                win.accept()
            except Exception:
                pass
            self._scan()

    def _resolve_all(self, action, win):
        """批量处理所有重复项，失败的保留在清单里。"""
        if action == "delete_local" and QMessageBox.question(
                self, "确认删除",
                f"将删除 {len(self.duplicates)} 个本地文件（源），不可恢复，确认？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        failed = []
        for it in list(self.duplicates):
            ok, msg = self._do_resolve(it, action)
            self._insert_log(msg)
            if not ok:
                failed.append(it)
        self.duplicates = failed
        try:
            win.accept()
        except Exception:
            pass
        if self.duplicates:
            self._insert_log(f"⚠️ {len(self.duplicates)} 个处理失败，请重试")
            self._show_duplicate_report()  # 只显示失败的项
        else:
            self._scan()
