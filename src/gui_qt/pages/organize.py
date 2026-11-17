"""自动整理页（PySide6 版）。

对应原版 gui/toolbox_pages.py OrganizePage：
- 目录栏：工作目录 + 日期文件夹计数（随全局工作目录变化）
- 扫描并预览：按日期分组预览（每组前 3 个文件）
- 执行整理：确认后执行，完成后自动重新扫描
- 回车快捷：第一次回车=扫描，第二次回车=执行整理
"""

import os

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout,
)
from PySide6.QtCore import Signal

from toolbox.organize_engine import execute_migration, scan_files, scan_sources
from toolbox import bili_upload as BU
from toolbox.utils import format_size

from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import count_date_subfolders
from gui_qt.common import run_bg


class OrganizePage(BasePage):
    progress = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self.groups = {}
        self.no_date_files = []
        self.migration_plan = []
        self.busy = False
        self._plan_dir = ""
        self._generation = 0
        self._migrated_videos = []
        self._enter_stage = 0  # 回车快捷：0=待扫描，1=待执行

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # 目录栏：工作目录 + 日期文件夹计数
        dir_row = QHBoxLayout()
        self.dir_label = QLabel(app.work_dir)
        dir_row.addWidget(self.dir_label, 1)
        self.dir_date_label = QLabel("")
        self.dir_date_label.setProperty("role", "muted")
        dir_row.addWidget(self.dir_date_label)
        layout.addLayout(dir_row)
        self._update_dir_date_label()

        layout.addWidget(QLabel("A端来源文件夹（可多个；迁移视频到当前工作目录，日期可识别时放入日期文件夹）"))
        self.sources = QListWidget()
        self.sources.setMaximumHeight(95)
        settings = BU.load_settings()
        self.sources.addItems(settings.get("sources", []))
        layout.addWidget(self.sources)
        source_row = QHBoxLayout()
        add_source = QPushButton("＋ 添加来源文件夹")
        add_source.clicked.connect(self._add_source)
        source_row.addWidget(add_source)
        remove_source = QPushButton("移除所选来源")
        remove_source.clicked.connect(self._remove_source)
        source_row.addWidget(remove_source)
        self.recursive = QCheckBox("包含来源子文件夹")
        self.recursive.setChecked(bool(settings.get("recursive", False)))
        self.recursive.toggled.connect(self._save_sources)
        source_row.addWidget(self.recursive)
        source_row.addStretch(1)
        layout.addLayout(source_row)
        rename_row = QHBoxLayout()
        rename_row.addWidget(QLabel("下一步：视频交互重命名完成后，再在重命名页投稿"), 1)
        rename_button = QPushButton("✏️ 前往交互重命名 / 投稿")
        rename_button.clicked.connect(self._open_rename)
        rename_row.addWidget(rename_button)
        layout.addLayout(rename_row)

        # 控制按钮
        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 扫描并预览")
        self.scan_btn.clicked.connect(self._scan)
        btn_row.addWidget(self.scan_btn)
        self.exec_btn = QPushButton("📂 执行迁移 / 整理")
        self.exec_btn.setProperty("variant", "primary")
        self.exec_btn.clicked.connect(self._execute)
        btn_row.addWidget(self.exec_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 预览 + 日志
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        layout.addWidget(self.preview_text, 2)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(100)
        layout.addWidget(self.log_text)
        self.progress.connect(self._log)

    # ==================== 回车快捷 ====================

    def on_return_press(self):
        if self._enter_stage == 0:
            self._enter_stage = 1
            self._scan()
            self._log("⏎ 再次按回车将直接执行整理（或点「执行整理」）")
        else:
            self._enter_stage = 0
            self._execute()

    # ==================== 目录 ====================

    def on_dir_changed(self, path):
        self.dir_label.setText(path)
        self._update_dir_date_label()
        self._invalidate_plan()

    def _invalidate_plan(self):
        self._generation += 1
        self.groups = {}
        self.migration_plan = []
        self._plan_dir = ""
        self._enter_stage = 0

    def _source_paths(self):
        return [self.sources.item(index).text() for index in range(self.sources.count())]

    def _save_sources(self, *_):
        self._invalidate_plan()
        settings = BU.load_settings()
        settings.update(sources=self._source_paths(), recursive=self.recursive.isChecked())
        try:
            BU.save_settings(settings)
        except OSError:
            self._log("来源设置保存失败，请检查写入权限")

    def _add_source(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, "选择A端视频来源文件夹")
        if path and os.path.normcase(os.path.realpath(path)) not in {
                os.path.normcase(os.path.realpath(item)) for item in self._source_paths()}:
            self.sources.addItem(path)
            self._save_sources()

    def _remove_source(self):
        if not self.busy:
            for item in self.sources.selectedItems():
                self.sources.takeItem(self.sources.row(item))
            self._save_sources()

    def _open_rename(self):
        if not self.busy:
            self.app.show_page("toolbox_rename")

    def _set_busy(self, busy):
        self.busy = busy
        self.scan_btn.setEnabled(not busy)
        self.exec_btn.setEnabled(not busy)

    def has_active_tasks(self):
        return self.busy

    def _update_dir_date_label(self):
        count = count_date_subfolders(self.app.work_dir)
        self.dir_date_label.setText(
            f"📅 {count} 个日期文件夹" if count else "⚠️ 无日期文件夹")

    # ==================== 扫描 / 执行 ====================

    def _scan(self):
        if self.busy:
            return
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            self._log("⚠️ 工作目录不存在，请先在全局设置指定")
            return
        root = self.app.work_dir
        sources, recursive = self._source_paths(), self.recursive.isChecked()
        generation = self._generation
        self._set_busy(True)
        self.preview_text.setPlainText("正在扫描视频文件…")
        def work():
            return scan_files(root), scan_sources(sources, root, recursive)
        def done(result, error):
            self._set_busy(False)
            if generation != self._generation or root != self.app.work_dir:
                self._log("目录/来源已变化，请重新扫描")
                return
            if error:
                self._log(f"扫描失败：{error}")
                return
            (self.groups, self.no_date_files), (self.migration_plan, skipped) = result
            self._plan_dir = root
            self._show_preview(skipped)
            self._enter_stage = 1
        run_bg(work, done, owner=self)

    def _show_preview(self, skipped):
        self.preview_text.clear()
        self.preview_text.append(f"工作目录：{self._plan_dir}\n")
        if self.migration_plan:
            self.preview_text.append(f"A端迁移：{len(self.migration_plan)} 个视频")
            for item in self.migration_plan[:100]:
                target = os.path.join(self._plan_dir, item["date"], item["name"])
                self.preview_text.append(f"{item['path']} → {target} ({format_size(item['size'])})")
            if len(self.migration_plan) > 100:
                self.preview_text.append(f"…其余 {len(self.migration_plan) - 100} 个视频")
            self.preview_text.append("")
        total = 0
        for date_str, files in self.groups.items():
            total += len(files)
            self.preview_text.append(f"📁 {date_str}/  ({len(files)} 个文件)")
            for f in files[:3]:
                self.preview_text.append(f"   - {f['name']}  ({format_size(f['size'])})")
            if len(files) > 3:
                self.preview_text.append(f"   ... 还有 {len(files) - 3} 个")
            self.preview_text.append("")
        self.preview_text.append(f"工作目录整理：{len(self.groups)} 个日期分组，{total} 个视频")
        if skipped:
            self.preview_text.append("\n暂缓/跳过：\n" + "\n".join(skipped[:30]))
        if self.no_date_files:
            self.preview_text.append(f"\n⚠️ 无法识别日期: {len(self.no_date_files)} 个")
            for n in self.no_date_files[:10]:
                self.preview_text.append(f"   - {n}")

    def _execute(self):
        if self.busy:
            return
        if self._plan_dir != self.app.work_dir:
            self._log("请先扫描当前工作目录")
            return
        if not self.groups and not self.migration_plan:
            self._log("没有待迁移或整理的视频，请先扫描")
            return
        root = self._plan_dir
        plan = list(self.migration_plan) + [dict(item, date=date, source_root=root)
                for date, items in self.groups.items() for item in items]
        ret = QMessageBox.question(
            self, "确认",
            f"将迁移/整理 {len(plan)} 个视频到：\n{root}\n\n同名自动编号，目标校验成功后删除来源。\n完成后请前往交互重命名页处理，再投稿。\n确认执行？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.log_text.clear()
        self._set_busy(True)
        self._invalidate_plan()
        def work():
            results = execute_migration(root, plan, on_progress=lambda current, total, result:
                self.progress.emit(f"{current}/{total} " + (f"已迁移：{result['target']}" if result['ok'] else f"失败：{result['source']} · {result['error']}")))
            return results
        def done(result, error):
            self._set_busy(False)
            if error:
                self._log(f"执行中断：{error}，已完成迁移的视频保留在目标目录")
                return
            results = result
            self._migrated_videos = [item["target"] for item in results if item["ok"]]
            success = len(self._migrated_videos)
            self._log(f"✅ 整理完成：成功 {success}，失败 {len(results) - success}。下一步请前往交互重命名页；此处不会上传投稿。")
            self._scan()
        run_bg(work, done, owner=self)

    def _log(self, msg):
        self.log_text.append(str(msg))
