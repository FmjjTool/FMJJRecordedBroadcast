"""自动整理页（PySide6 版）。

对应原版 gui/toolbox_pages.py OrganizePage：
- 目录栏：工作目录 + 日期文件夹计数（随全局工作目录变化）
- 扫描并预览：按日期分组预览（每组前 3 个文件）
- 执行整理：确认后执行，完成后自动重新扫描
- 回车快捷：第一次回车=扫描，第二次回车=执行整理
"""

import json
import os
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal

from toolbox.date_folders import scan_date_folders
from toolbox.organize_engine import execute_migration, scan_files, scan_sources
from toolbox.rename_engine import is_standard_recording_name
from toolbox.sidecar_merge import (
    DANMAKU_SUFFIX, TIMELINE_SUFFIX, copy_cover, list_cover_files,
    list_danmaku_files, list_timeline_files, merge_danmaku, merge_timelines,
)
from toolbox import bili_upload as BU
from toolbox.utils import format_size, is_video_file

from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolSectionCard
from gui_qt.common import run_bg


class MergeSidecarsDialog(QDialog):
    """选择日期文件夹、目标视频与要合并的弹幕 / 时间轴（只收集选择）。"""

    def __init__(self, parent, work_dir, folders):
        super().__init__(parent)
        self.setWindowTitle("合并弹幕 / 时间轴")
        self.resize(900, 640)
        self.setMinimumSize(640, 420)
        self.work_dir = work_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        form = QHBoxLayout()
        form.setSpacing(8)
        form.addWidget(QLabel("日期文件夹"))
        self.folder_combo = QComboBox()
        self.folder_combo.setMinimumWidth(160)
        for name, _label in folders:
            self.folder_combo.addItem(name)
        self.folder_combo.currentIndexChanged.connect(self._load_folder)
        form.addWidget(self.folder_combo)
        form.addSpacing(12)
        form.addWidget(QLabel("目标视频"))
        self.video_combo = QComboBox()
        self.video_combo.setMinimumWidth(320)
        self.video_combo.currentIndexChanged.connect(self._update_summary)
        form.addWidget(self.video_combo, 1)
        layout.addLayout(form)

        hint = QLabel("弹幕按时间排序去重；时间轴按录制顺序换算到目标视频。"
                      "已有同名附属文件会先备份为 .bak-时间。")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        lists = QHBoxLayout()
        lists.setSpacing(12)
        self.danmaku_list = self._build_list("弹幕文件", lists)
        self.timeline_list = self._build_list("时间轴", lists)
        layout.addLayout(lists, 1)

        cover_row = QHBoxLayout()
        self.cover_check = QCheckBox("同时整理封面（复制最新一张为 目标视频_封面）")
        self.cover_check.setChecked(True)
        self.cover_check.toggled.connect(self._update_summary)
        cover_row.addWidget(self.cover_check)
        cover_row.addStretch(1)
        layout.addLayout(cover_row)

        self.summary = QLabel("")
        self.summary.setProperty("role", "muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.merge_btn = buttons.addButton("合并", QDialogButtonBox.AcceptRole)
        self.merge_btn.setProperty("variant", "primary")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._accept)
        layout.addWidget(buttons)

        self._load_folder()

    def _build_list(self, title, parent_layout):
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        items = QListWidget()
        items.setAlternatingRowColors(True)
        items.itemChanged.connect(lambda _item: self._update_summary())
        box_layout.addWidget(items)
        parent_layout.addWidget(box, 1)
        return items

    def _folder_path(self):
        name = self.folder_combo.currentText()
        return os.path.join(self.work_dir, name) if name else ""

    def _load_folder(self):
        self.danmaku_list.blockSignals(True)
        self.timeline_list.blockSignals(True)
        self.danmaku_list.clear()
        self.timeline_list.clear()
        self.video_combo.clear()
        folder = self._folder_path()
        self._danmaku_paths = list_danmaku_files(folder)
        self._timeline_paths = list_timeline_files(folder)
        self._covers = list_cover_files(folder)
        for path in self._danmaku_paths:
            self._add_item(self.danmaku_list, path)
        for path in self._timeline_paths:
            self._add_item(self.timeline_list, path)
        videos = []
        if folder and os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and is_video_file(path):
                    videos.append(path)
        videos.sort(key=lambda item: (
            not is_standard_recording_name(Path(item).name),
            -os.path.getsize(item)))
        for path in videos:
            self.video_combo.addItem(os.path.basename(path), path)
        self.danmaku_list.blockSignals(False)
        self.timeline_list.blockSignals(False)
        self._update_summary()

    @staticmethod
    def _clock(ms):
        try:
            return time.strftime("%H:%M:%S", time.localtime(int(ms) / 1000))
        except (TypeError, ValueError, OSError):
            return ""

    @classmethod
    def _sidecar_label(cls, path):
        label = f"{path.name}  ({format_size(os.path.getsize(path))})"
        if path.name.endswith(TIMELINE_SUFFIX):
            start = cls._timeline_start(path)
            if start:
                label += f"   首锚点 {start}"
        elif path.name.endswith(DANMAKU_SUFFIX):
            start = cls._danmaku_start(path)
            if start:
                label += f"   首条 {start}"
        return label

    @staticmethod
    def _timeline_start(path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        walls = []
        for entry in data.get("files") or []:
            if not isinstance(entry, dict):
                continue
            for anchor in entry.get("anchors") or []:
                if isinstance(anchor, dict) and anchor.get("wall_ms"):
                    walls.append(anchor["wall_ms"])
        return MergeSidecarsDialog._clock(min(walls)) if walls else ""

    @staticmethod
    def _danmaku_start(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    stamp = message.get("timestamp") or message.get("ts")
                    if stamp:
                        return MergeSidecarsDialog._clock(stamp)
        except OSError:
            return ""
        return ""

    def _add_item(self, items, path):
        try:
            label = self._sidecar_label(path)
        except OSError:
            label = path.name
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, str(path))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        items.addItem(item)

    @staticmethod
    def _checked(items):
        return [Path(items.item(index).data(Qt.UserRole))
                for index in range(items.count())
                if items.item(index).checkState() == Qt.Checked]

    def _target(self):
        value = self.video_combo.currentData()
        return Path(value) if value else None

    def _latest_cover(self):
        covers = getattr(self, "_covers", [])
        if not covers:
            return None

        def stamp(path):
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0

        return max(covers, key=lambda path: (stamp(path), path.name))

    def _update_summary(self):
        target = self._target()
        danmaku = self._checked(self.danmaku_list)
        timelines = self._checked(self.timeline_list)
        if target is None:
            self.summary.setText("当前日期文件夹没有视频，无法确定合并后的文件名")
            self.merge_btn.setEnabled(False)
            return
        parts = []
        if danmaku:
            parts.append(f"{len(danmaku)} 个弹幕 → {target.stem}{DANMAKU_SUFFIX}")
        if timelines:
            parts.append(f"{len(timelines)} 个时间轴 → {target.stem}{TIMELINE_SUFFIX}")
        cover = self._latest_cover()
        if cover is not None and self.cover_check.isChecked():
            parts.append(f"封面 ← {cover.name}")
        self.summary.setText("；".join(parts) if parts else "请至少勾选一个弹幕、时间轴或封面")
        self.merge_btn.setEnabled(bool(parts))

    def _accept(self):
        if self._target() is not None and (
                self._checked(self.danmaku_list) or self._checked(self.timeline_list)
                or (self.cover_check.isChecked() and self._latest_cover() is not None)):
            self.accept()

    def request(self):
        target = self._target()
        cover = self._latest_cover()
        return {"target": str(target) if target else "",
                "danmaku": [str(path) for path in self._checked(self.danmaku_list)],
                "timelines": [str(path) for path in self._checked(self.timeline_list)],
                "cover": str(cover) if self.cover_check.isChecked() and cover else ""}


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
        self._last_scan_skipped = []
        self._enter_stage = 0  # 回车快捷：0=待扫描，1=待执行

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        sources_card = self._section(
            "A端来源文件夹", "folder",
            "可多个；迁移视频到当前工作目录，日期可识别时放入日期文件夹")
        self.sources = QListWidget()
        self.sources.setMaximumHeight(95)
        settings = BU.load_settings()
        self.sources.addItems(settings.get("sources", []))
        sources_card.content_layout.addWidget(self.sources)
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
        sources_card.content_layout.addLayout(source_row)
        rename_row = QHBoxLayout()
        rename_row.addWidget(QLabel("下一步：在命名投稿页完成命名后，再投稿"), 1)
        rename_button = QPushButton("前往命名投稿")
        rename_button.clicked.connect(self._open_rename)
        rename_row.addWidget(rename_button)
        sources_card.content_layout.addLayout(rename_row)
        layout.addWidget(sources_card)

        # 控制按钮
        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("扫描并预览")
        self.scan_btn.clicked.connect(self._scan)
        btn_row.addWidget(self.scan_btn)
        self.exec_btn = QPushButton("执行迁移 / 整理")
        self.exec_btn.setProperty("variant", "primary")
        self.exec_btn.clicked.connect(self._execute)
        btn_row.addWidget(self.exec_btn)
        self.source_btn = QPushButton("从来源迁移视频（按日期）")
        self.source_btn.setToolTip(
            "只迁移 A 端来源里的视频：文件名能识别日期就用文件名，否则按修改时间建日期文件夹；"
            "工作目录里的视频不动")
        self.source_btn.clicked.connect(self._migrate_sources)
        btn_row.addWidget(self.source_btn)
        self.merge_btn = QPushButton("合并弹幕 / 时间轴")
        self.merge_btn.setToolTip(
            "把选中日期文件夹里的多个弹幕 JSONL / 时间轴合并到目标视频，"
            "输出与目标视频同名的附属文件；原名文件保留，同名目标会先备份")
        self.merge_btn.clicked.connect(self._open_merge)
        btn_row.addWidget(self.merge_btn)
        btn_row.addStretch(1)
        actions_card = self._section("操作", "play")
        actions_card.content_layout.addLayout(btn_row)
        layout.addWidget(actions_card)

        # 预览 + 日志
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        preview_card = self._section("迁移预览", "list")
        preview_card.content_layout.addWidget(self.preview_text)
        layout.addWidget(preview_card, 2)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(100)
        log_card = self._section("运行日志", "document")
        log_card.content_layout.addWidget(self.log_text)
        layout.addWidget(log_card)
        self.progress.connect(self._log)

    def _section(self, title, icon, description=""):
        card = ToolSectionCard(title, description, icon=icon)
        card.layout().setContentsMargins(12, 12, 12, 12)
        card.content_layout.setSpacing(6)
        return card

    # ==================== 回车快捷 ====================

    def on_return_press(self):
        if self._enter_stage == 0:
            self._enter_stage = 1
            self._scan()
            self._log("再次按回车将直接执行整理（或点「执行整理」）")
        else:
            self._enter_stage = 0
            self._execute()

    # ==================== 目录 ====================

    def on_dir_changed(self, path):
        self._invalidate_plan()

    def _invalidate_plan(self):
        self._generation += 1
        self.groups = {}
        self.migration_plan = []
        self._plan_dir = ""
        self._last_scan_skipped = []
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
        self.source_btn.setEnabled(not busy)
        self.merge_btn.setEnabled(not busy)

    def has_active_tasks(self):
        return self.busy

    # ==================== 扫描 / 执行 ====================

    def _scan(self):
        if self.busy:
            return
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            self._log("工作目录不存在，请先在全局设置指定")
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
            self._last_scan_skipped = list(skipped)
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
                extra = ""
                if item.get("companions"):
                    extra = f"  [附带 {len(item['companions'])} 个：{'、'.join(item['companions'][:3])}"
                    extra += "…]" if len(item["companions"]) > 3 else "]"
                self.preview_text.append(
                    f"{item['path']} → {target} ({format_size(item['size'])}){extra}")
            if len(self.migration_plan) > 100:
                self.preview_text.append(f"…其余 {len(self.migration_plan) - 100} 个视频")
            self.preview_text.append("")
        total = 0
        for date_str, files in self.groups.items():
            total += len(files)
            self.preview_text.append(f"{date_str}/  ({len(files)} 个文件)")
            for f in files[:3]:
                self.preview_text.append(f"   - {f['name']}  ({format_size(f['size'])})")
            if len(files) > 3:
                self.preview_text.append(f"   ... 还有 {len(files) - 3} 个")
            self.preview_text.append("")
        self.preview_text.append(f"工作目录整理：{len(self.groups)} 个日期分组，{total} 个视频")
        if skipped:
            self.preview_text.append("\n暂缓/跳过：\n" + "\n".join(skipped[:30]))
        if self.no_date_files:
            self.preview_text.append(f"\n无法识别日期: {len(self.no_date_files)} 个")
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
            f"将迁移/整理 {len(plan)} 个视频到：\n{root}\n\n同名自动编号，目标校验成功后删除来源。\n完成后请前往命名投稿页处理，再投稿。\n确认执行？",
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
            attached = sum(len(item.get("companions") or []) for item in results if item.get("ok"))
            skipped_companions = [reason for item in results
                                  for reason in (item.get("companion_skipped") or [])]
            extra = f"，带出弹幕/封面等 {attached} 个附属文件" if attached else ""
            self._log(f"整理完成：成功 {success}，失败 {len(results) - success}{extra}。"
                      "下一步请前往命名投稿页；此处不会上传投稿。")
            if skipped_companions:
                self._log("附属文件跳过：" + "；".join(skipped_companions[:5]))
            if self._last_scan_skipped:
                self._log(f"另有 {len(self._last_scan_skipped)} 个未迁移（仍留在来源）："
                          + "；".join(self._last_scan_skipped[:5]))
            self._scan()
        run_bg(work, done, owner=self)

    def _open_merge(self):
        if self.busy:
            return
        if not self.app.work_dir or not os.path.isdir(self.app.work_dir):
            self._log("工作目录不存在，请先在全局设置指定")
            return
        folders = scan_date_folders(self.app.work_dir)
        if not folders:
            self._log("工作目录下没有日期文件夹")
            return
        dialog = MergeSidecarsDialog(self, self.app.work_dir, folders)
        if dialog.exec() != QDialog.Accepted:
            return
        request = dialog.request()
        self._set_busy(True)
        self._log("正在合并弹幕 / 时间轴…")

        def work():
            target = Path(request["target"])
            result = {}
            if request["danmaku"]:
                result["danmaku"] = merge_danmaku(
                    request["danmaku"],
                    target.parent / (target.stem + DANMAKU_SUFFIX))
            if request["timelines"]:
                result["timeline"] = merge_timelines(
                    request["timelines"],
                    target.parent / (target.stem + TIMELINE_SUFFIX), target)
            if request.get("cover"):
                result["cover"] = copy_cover(
                    request["cover"], target.parent / (target.stem + "_封面"))
            return result

        def done(result, error):
            self._set_busy(False)
            if error:
                self._log(f"合并失败：{error}")
                return
            parts = []
            danmaku = result.get("danmaku")
            if danmaku:
                parts.append(f"弹幕 {danmaku['merged']} 条（去重 {danmaku['duplicates']} 条）")
                self._log(f"已合并弹幕 → {os.path.basename(danmaku['out'])}")
                if danmaku.get("backup"):
                    self._log(f"原弹幕已备份：{os.path.basename(danmaku['backup'])}")
            timeline = result.get("timeline")
            if timeline:
                parts.append(f"时间轴 {timeline['entries']} 段 / {timeline['anchors']} 个锚点")
                self._log(f"已合并时间轴 → {os.path.basename(timeline['out'])}")
                if timeline.get("backup"):
                    self._log(f"原时间轴已备份：{os.path.basename(timeline['backup'])}")
                if timeline.get("estimated"):
                    self._log("部分源视频已不存在，时间轴偏移按相邻场次时间估算；"
                              "如对不上，请只勾选目标视频对应的那一段。")
            cover = result.get("cover")
            if cover:
                parts.append(f"封面 ← {os.path.basename(cover['source'])}")
                self._log(f"已整理封面 → {os.path.basename(cover['out'])}")
                if cover.get("backup"):
                    self._log(f"原封面已备份：{os.path.basename(cover['backup'])}")
            if parts:
                self.app.toast("合并完成：" + "，".join(parts), "success")

        run_bg(work, done, owner=self)

    def _migrate_sources(self):
        """只迁移来源视频：文件名无日期时按修改时间落到日期文件夹。"""
        if self.busy:
            return
        root = self.app.work_dir
        if not root or not os.path.isdir(root):
            self._log("工作目录不存在，请先在全局设置指定")
            return
        sources, recursive = self._source_paths(), self.recursive.isChecked()
        if not sources:
            self._log("请先添加 A 端来源文件夹")
            return
        self._set_busy(True)
        self._log("正在扫描来源文件夹…")

        def work():
            return scan_sources(sources, root, recursive)

        def done(result, error):
            if error:
                self._set_busy(False)
                self._log(f"扫描来源失败：{error}")
                return
            plan, skipped = result
            for item in plan:
                if not item.get("date"):
                    item["date"] = self._date_from_mtime(item["path"])
            if not plan:
                self._set_busy(False)
                self._log("来源里没有可迁移的视频（空文件 / 最近仍在写入 / 已在工作目录内）")
                if skipped:
                    self._log("跳过：" + "；".join(skipped[:5]))
                return
            dates = sorted({item["date"] for item in plan if item.get("date")})
            preview = "\n".join(
                f"{os.path.basename(item['path'])} → {item['date'] or '（根目录）'}"
                for item in plan[:10])
            more = f"\n…其余 {len(plan) - 10} 个视频" if len(plan) > 10 else ""
            skip_text = ""
            if skipped:
                skip_text = (f"\n\n本次不迁移 {len(skipped)} 个：\n"
                             + "\n".join(skipped[:5]))
                if len(skipped) > 5:
                    skip_text += f"\n…其余 {len(skipped) - 5} 个"
            ret = QMessageBox.question(
                self, "从来源迁移视频",
                f"将迁移 {len(plan)} 个视频到：\n{root}\\<日期>\\\n\n"
                f"{len(dates)} 个日期分组：{', '.join(dates[:8]) or '无'}\n\n"
                f"{preview}{more}{skip_text}\n\n"
                "同名自动编号，校验成功后删除来源。确认执行？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                self._set_busy(False)
                self._log("已取消来源迁移")
                return
            self.log_text.clear()

            def migrate():
                return execute_migration(root, plan, on_progress=lambda current, total, res:
                    self.progress.emit(f"{current}/{total} " + (
                        f"已迁移：{res['target']}" if res['ok']
                        else f"失败：{res['source']} · {res['error']}")))

            def migrated(results, err):
                self._set_busy(False)
                if err:
                    self._log(f"迁移中断：{err}，已迁移的视频保留在目标目录")
                    return
                ok = sum(1 for item in results if item["ok"])
                self._log(f"来源迁移完成：成功 {ok}，失败 {len(results) - ok}。"
                          "下一步请前往命名投稿页处理，再投稿。")
                if skipped:
                    self._log(f"另有 {len(skipped)} 个未迁移（仍留在来源）："
                              + "；".join(skipped[:5]))

            run_bg(migrate, migrated, owner=self)

        run_bg(work, done, owner=self)

    @staticmethod
    def _date_from_mtime(path: str) -> str:
        try:
            return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
        except OSError:
            return ""

    def _log(self, msg):
        self.log_text.append(str(msg))
