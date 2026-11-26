"""可预览文案、选择分P与封面、查看失败记录的 B站投稿入口。"""
import threading
import time
from pathlib import Path

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QInputDialog,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QScrollArea, QWidget)

from gui_qt.common import run_bg
from toolbox import bili_upload as BU
from toolbox.utils import extract_date_from_filename
from gui_qt.video_selection_dialog import VideoSelectionDialog, video_item, checked_paths


def _fmt_size(num):
    """字节数 → 人读文本（B/KB/MB/GB）。"""
    value = float(max(0, num))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} 小时 {seconds % 3600 // 60} 分"
    if seconds >= 60:
        return f"{seconds // 60} 分 {seconds % 60} 秒"
    return f"{seconds} 秒"


class BiliUploadDialog(QDialog):
    # 已传字节, 总字节, 说明（分片进度）
    # 必须用 object 传字节数：Qt 的 int 是 32 位，超过 2 GB 的总量会溢出成负数。
    progress = Signal(object, object, str)

    def __init__(self, parent=None, paths=None, *, work_dir=None, blocked_paths=()):
        super().__init__(parent)
        self.setWindowTitle("B站视频上传 · 文案与分P预览")
        self.resize(820, 890)
        # 独立窗口（和直播录制二级界面一样），开着也能继续操作主界面
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.paths = BU.unique_video_paths(paths or [])
        self.work_dir = str(work_dir or getattr(getattr(parent, "app", None), "work_dir", ""))
        self.blocked_paths = set(blocked_paths)
        self._selected_day = BU.today_compact()
        self._records = []
        self._closed = False
        self._entered = False
        self._checking_account = False
        self._verified_identity = ""
        self._scan_generation = 0
        self._selection_modified = False
        self._source_platform_paths = None
        self.account_cancel = threading.Event()
        self._season_titles = set()
        self.settings = BU.load_settings()
        self.config = {**BU.DEFAULTS, **self.settings.get("upload", {})}
        self.busy = False
        self.cancel = threading.Event()
        self.uploader = None
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(content)
        hint = QLabel("没有视频时也可先配置并保存投稿模板；视频完成命名后再选择上传。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        templates = QHBoxLayout()
        templates.addWidget(QLabel("投稿模板"))
        self.templates = QComboBox()
        templates.addWidget(self.templates, 1)
        self.save_as_btn = QPushButton("另存为模板")
        self.save_as_btn.clicked.connect(self.save_as_template)
        templates.addWidget(self.save_as_btn)
        self.delete_template_btn = QPushButton("删除模板")
        self.delete_template_btn.clicked.connect(self.delete_template)
        templates.addWidget(self.delete_template_btn)
        layout.addLayout(templates)
        self.variables = QLabel()
        self.variables.setWordWrap(True)
        layout.addWidget(self.variables)
        self.multipart_hint = QLabel()
        self.multipart_hint.setWordWrap(True)
        layout.addWidget(self.multipart_hint)
        self.workspace_label = QLabel("当前工作目录：" + (self.work_dir or "未设置"))
        self.workspace_label.setWordWrap(True)
        layout.addWidget(self.workspace_label)
        self.files = QListWidget()
        self.files.setMaximumHeight(100)
        self.files.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        layout.addWidget(self.files)
        row = QHBoxLayout()
        for text, slot in (("选择工作目录视频", self.choose_files), ("刷新视频", self.load_workspace),
                           ("移除所选", self.remove_files), ("上传历史", self.show_history)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        form = QFormLayout()
        self.title = QLineEdit(self.config["title"])
        form.addRow("标题模板", self.title)
        self.date = QLineEdit(self._selected_day)
        self.date.setPlaceholderText("留空从视频文件名识别；可填 20261002 或 20261002~20261003")
        self.date.setToolTip("跨天录制填 开始~结束；只填单日时按视频文件名里的结束日期补全")
        form.addRow("本次直播日期", self.date)
        self.platform = QComboBox()
        self.platform.addItems(BU.SOURCE_PLATFORMS)
        self.platform.setCurrentText(BU.normalize_platform(self.config.get("platform")))
        self.platform.setToolTip("根据勾选视频的文件名自动识别，可手动调整")
        form.addRow("直播来源平台", self.platform)
        self.desc = QPlainTextEdit(self.config["desc"])
        self.desc.setMaximumHeight(70)
        form.addRow("简介模板", self.desc)
        self.tags = QLineEdit(self.config["tags"])
        form.addRow("标签（逗号分隔）", self.tags)
        self.tid = QSpinBox()
        self.tid.setRange(0, 99999)
        self.tid.setSpecialValueText("请选择分区")
        self.tid.setValue(int(self.config["tid"] or 0))
        form.addRow("分区 ID", self.tid)
        self.categories = QComboBox()
        self.categories.addItem("进入窗口后自动读取可用分区", 0)
        self.categories.currentIndexChanged.connect(lambda: self.tid.setValue(int(self.categories.currentData() or 0)))
        form.addRow("账号可用分区", self.categories)
        self.statement = QComboBox()
        for code, label, *_ in BU.CREATION_STATEMENTS:
            self.statement.addItem(label, code)
        self.statement.setToolTip("B站创作声明只能选一条：选“内容为转载”时须填写视频来源")
        form.addRow("创作声明", self.statement)
        self.source = QLineEdit(self.config["source"])
        self.source.setPlaceholderText("创作声明选“内容为转载”时填写原直播间链接")
        form.addRow("视频来源", self.source)
        self.season = QComboBox()
        self.season.setEditable(True)
        self.season.addItem("不加入合集", "")
        self.season.setToolTip("投稿成功后把稿件放进该合集；验证账号后会列出账号可用合集")
        form.addRow("加入合集", self.season)
        self._select_statement(self.config.get("creation_statement"))
        self.season.setCurrentText(str(self.config.get("season") or ""))
        cover_row = QHBoxLayout()
        self.cover = QLineEdit(self.config["cover"])
        self.cover.setPlaceholderText("留空自动提取首帧封面")
        cover_row.addWidget(self.cover)
        button = QPushButton("选择封面")
        button.clicked.connect(self.choose_cover)
        cover_row.addWidget(button)
        form.addRow("封面", cover_row)
        self.threads = QSpinBox()
        self.threads.setRange(1, BU.MAX_UPLOAD_THREADS)
        self.threads.setValue(BU.upload_threads(self.config.get("threads")))
        self.threads.setToolTip("同时上传几个分片：1 最稳、3 默认、调高更快但更吃带宽，也更容易触发风控")
        form.addRow("并行上传线程", self.threads)
        self.publish = QCheckBox("上传成功后自动投稿（进入 B站审核）")
        self.publish.setChecked(bool(self.config.get("auto_publish", True)))
        form.addRow(self.publish)
        layout.addLayout(form)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(110)
        layout.addWidget(self.preview)
        # 进度区固定在窗口底部（不随表单滚动），上传时始终可见
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")
        self.bar.setMinimumHeight(24)
        self.bar.setToolTip("已上传字节 / 总字节、实时速度与预计剩余时间")
        outer.addWidget(self.bar)
        self.progress_detail = QLabel("尚未开始上传")
        self.progress_detail.setWordWrap(True)
        self.progress_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.progress_detail)
        self.account_label = QLabel("进入后自动验证当前 B站账号")
        self.account_label.setWordWrap(True)
        outer.addWidget(self.account_label)
        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        actions = QHBoxLayout()
        self.preview_btn = QPushButton("更新预览")
        self.preview_btn.clicked.connect(self.refresh_preview)
        actions.addWidget(self.preview_btn)
        self.save_btn = QPushButton("保存模板")
        self.save_btn.clicked.connect(self.save_config)
        actions.addWidget(self.save_btn)
        self.start_btn = QPushButton("开始上传")
        self.start_btn.setToolTip("选择已重命名视频并完成账号验证后开始上传；没有视频时可先保存模板")
        self.start_btn.clicked.connect(self.start_upload)
        actions.addWidget(self.start_btn)
        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.clicked.connect(self.reject)
        actions.addWidget(self.cancel_btn)
        outer.addLayout(actions)
        self.progress.connect(self.on_progress)
        self.files.itemChanged.connect(self.on_files_changed)
        self.files.model().rowsMoved.connect(self.refresh_preview)
        self._reload_templates(self.settings.get("active_upload_template"))
        self.templates.currentTextChanged.connect(self.load_template)
        records = [{"path": str(path), "date": extract_date_from_filename(Path(path).name) or "",
                    "renamed": BU.is_renamed_video(path)} for path in self.paths]
        self._records = records
        self.set_video_list(records, {record["path"] for record in records if record["date"] == self._selected_day})
        value = BU.date_value_for_paths(self.selected_paths())
        if value:
            self._selected_day = value.split("~", 1)[0]
            self.date.setText(value)
        for widget in (self.title, self.date, self.tags, self.source, self.cover):
            widget.textChanged.connect(self.refresh_preview)
        self.desc.textChanged.connect(self.refresh_preview)
        self.platform.currentTextChanged.connect(self.refresh_preview)
        self.tid.valueChanged.connect(self.refresh_preview)
        self.statement.currentIndexChanged.connect(self.refresh_preview)
        self.season.currentTextChanged.connect(self.refresh_preview)
        self.load_template(self.templates.currentText())
        self.refresh_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._entered:
            self._entered = True
            QTimer.singleShot(0, self.check_account)
            QTimer.singleShot(0, self.load_workspace)

    def is_closed(self):
        return self._closed

    def reopen(self, paths=(), blocked_paths=()):
        """关掉后再从投稿入口打开：沿用同一窗口，只补新视频并重新验证登录。"""
        self._closed = False
        self.account_cancel = threading.Event()
        self.merge_paths(paths, blocked_paths)
        QTimer.singleShot(0, self.check_account)
        QTimer.singleShot(0, self.load_workspace)

    def merge_paths(self, paths=(), blocked_paths=()):
        """窗口已开着时再点投稿入口：把新视频并进来，不打断正在进行的上传。"""
        self.blocked_paths.update(blocked_paths)
        if self.busy or not paths:
            return
        known = {BU.video_path_key(item) for item in self.paths}
        added = [item for item in BU.unique_video_paths(list(paths))
                 if BU.video_path_key(item) not in known]
        if not added:
            return
        added_keys = {BU.video_path_key(item) for item in added}
        self.paths.extend(added)
        records = [{"path": str(item), "date": extract_date_from_filename(Path(item).name) or "",
                    "renamed": BU.is_renamed_video(item)} for item in self.paths]
        self._records = records
        days = {record["date"] for record in records
                if record["date"] and BU.video_path_key(record["path"]) in added_keys}
        if len(days) == 1:
            self._selected_day = days.pop()
            self.date.setText(self._selected_day)
        checked = set(self.selected_paths())
        checked |= {record["path"] for record in records
                    if record["renamed"] and BU.video_path_key(record["path"]) in added_keys}
        self._selection_modified = True
        self.set_video_list([record for record in records if record["date"] == self._selected_day], checked)
        value = BU.date_value_for_paths(self.selected_paths())
        if value:
            self._selected_day = value.split("~", 1)[0]
            self.date.setText(value)
        self.status.setText(f"已加入 {len(added)} 个视频，当前勾选 {len(self.selected_paths())} 个")

    def set_video_list(self, records, checked):
        records = BU.unique_video_records(records)
        checked = {BU.video_path_key(path) for path in checked}
        self.files.blockSignals(True)
        self.files.clear()
        for record in records:
            self.files.addItem(video_item(record, self.work_dir, BU.video_path_key(record["path"]) in checked, self.blocked_paths))
        self.files.blockSignals(False)
        self.refresh_preview()

    def on_files_changed(self, *_):
        self._selection_modified = True
        selected = self.selected_paths()
        dates = {extract_date_from_filename(Path(path).name) for path in selected}
        value = BU.date_value_for_paths(selected)
        if value:
            self._selected_day = value.split("~", 1)[0]
            self.date.setText(value)
        elif len(dates) > 1:
            self.date.clear()
        elif not selected:
            self.date.setText(self._selected_day)
        self.refresh_preview()

    def load_workspace(self):
        if self._closed or self.busy:
            return
        root = str(getattr(getattr(self.parentWidget(), "app", None), "work_dir", self.work_dir))
        if root != self.work_dir:
            self.paths = []
            self._records = []
            self._selection_modified = False
        self.work_dir = root
        self.workspace_label.setText("当前工作目录：" + (root or "未设置"))
        self._scan_generation += 1
        generation = self._scan_generation
        def done(records, error):
            if self._closed or generation != self._scan_generation:
                return
            current = str(getattr(getattr(self.parentWidget(), "app", None), "work_dir", self.work_dir))
            if current != root:
                self._selection_modified = False
                self.load_workspace()
                return
            if error:
                self.status.setText("读取工作目录失败，请检查目录权限")
                return
            supplied = {BU.video_path_key(path) for path in self.paths}
            retained = [record for record in self._records if
                        BU.video_path_key(record["path"]) in supplied and Path(record["path"]).is_file()]
            # 扫描用Path生成反斜杠路径，重命名页可能传入正斜杠路径；按文件标识合并。
            records = BU.unique_video_records([*records, *retained])
            self._records = records
            if self.busy:
                return
            visible = [record for record in records if record["date"] == self._selected_day]
            checked = (set(self.selected_paths()) if self._selection_modified else
                       {record["path"] for record in visible if record["renamed"]})
            self.set_video_list(visible, checked)
            self.status.setText(f"已读取工作目录：{self._selected_day} 共 {len(visible)} 个视频，勾选 {len(self.selected_paths())} 个")
        run_bg(lambda: BU.scan_workspace_videos(root), done, owner=self.parentWidget())

    def selected_paths(self):
        return checked_paths(self.files)

    def current_config(self):
        return {"title": self.title.text(), "date": self.date.text().strip(),
                "desc": self.desc.toPlainText(), "tags": self.tags.text(), "tid": self.tid.value(),
                "creation_statement": self.statement.currentData() or "self",
                "season": self.season_name(), "source": self.source.text().strip(),
                "cover": self.cover.text().strip(), "auto_publish": self.publish.isChecked(),
                "platform": self.platform.currentText(), "threads": self.threads.value()}

    def statement_label(self):
        return self.statement.currentText()

    def season_name(self):
        name = self.season.currentText().strip()
        return "" if name == "不加入合集" else name

    def _select_statement(self, value):
        """按保存的键或名称选中创作声明；未知值回到自制声明。"""
        index = self.statement.findText(BU.creation_statement(value)[0])
        self.statement.setCurrentIndex(max(0, index))

    def _fill_seasons(self, seasons):
        """把账号可用合集填进下拉框，保留用户已填的名称。"""
        self._season_titles = {season["title"] for season in seasons}
        current = self.season_name()
        self.season.blockSignals(True)
        self.season.clear()
        self.season.addItem("不加入合集", "")
        for season in seasons:
            self.season.addItem(season["title"], season["title"])
        self.season.setCurrentText(current)
        self.season.blockSignals(False)
        self.refresh_preview()

    def choose_files(self):
        if self.busy:
            return
        picker = VideoSelectionDialog(self, self.work_dir, self._records, self.selected_paths(),
                                      self._selected_day, self.blocked_paths)
        if picker.exec() == QDialog.Accepted:
            self._selected_day = picker.date.date().toString("yyyyMMdd")
            self.set_video_list(picker.visible_records(), set(picker.selected_paths()))
            self.on_files_changed()

    def remove_files(self):
        if not self.busy:
            for item in self.files.selectedItems():
                self.files.takeItem(self.files.row(item))
            self.on_files_changed()

    def choose_cover(self):
        if not self.busy:
            path, _ = QFileDialog.getOpenFileName(self, "选择封面", "", "图片 (*.png *.jpg *.jpeg *.webp)")
            if path:
                self.cover.setText(path)

    def _sync_source_platform(self, paths, *, force=False):
        selection = frozenset(BU.video_path_key(path) for path in paths)
        if not force and selection == self._source_platform_paths:
            return
        self._source_platform_paths = selection
        platform = BU.detect_source_platform(paths)
        if platform:
            blocked = self.platform.blockSignals(True)
            self.platform.setCurrentText(platform)
            self.platform.blockSignals(blocked)

    def refresh_preview(self):
        paths = self.selected_paths()
        self.start_btn.setEnabled(bool(paths) and bool(self._verified_identity)
                                  and not self.busy and not self._checking_account)
        self._sync_source_platform(paths)
        parts = BU.multipart_count(paths, self.date.text().strip())
        self.setWindowTitle("B站视频上传 · 文案" + ("与分P预览" if parts else "预览"))
        self.variables.setText("模板变量：{日期}、{平台}、{文件名}" +
                               ("、{P数}、{分P说明}" if parts else "") + "；模板不保存本次日期和视频。")
        self.multipart_hint.setText(f"今天的 {parts} 个录播可组成同一条视频的 {parts} P；拖动列表调整顺序。")
        self.multipart_hint.setVisible(bool(parts))
        self.files.setDragDropMode(QListWidget.InternalMove if parts else QListWidget.NoDragDrop)
        if not paths:
            self.preview.setPlainText("尚未选择视频，可先配置并保存投稿模板；选择已重命名视频后再预览和上传。")
            return
        try:
            metadata = BU.build_metadata(paths, self.current_config())
            self.preview.setPlainText(f"标题：{metadata['title']}\n简介：{metadata['desc']}\n"
                f"标签：{metadata['tag']}\n分区：{metadata['tid']} · " +
                (f"{parts} P" if parts else f"视频：{len(paths)} 个") +
                f"\n平台：{self.platform.currentText()}\n创作声明：{self.statement_label()}" +
                f"\n合集：{self.season_name() or '不加入合集'}\n来源：{metadata['source']}")
        except (ValueError, TypeError) as error:
            self.preview.setPlainText(str(error))

    def save_config(self):
        return self.save_template(self.templates.currentText() or "默认模板")

    def _reload_templates(self, selected=None):
        saved = BU.load_settings().get("upload_templates", {})
        self._templates = saved if isinstance(saved, dict) else {}
        self.templates.blockSignals(True)
        self.templates.clear()
        self.templates.addItems(list(self._templates) or ["默认模板"])
        if selected in self._templates:
            self.templates.setCurrentText(selected)
        self.templates.blockSignals(False)

    def load_template(self, name):
        saved = self._templates.get(name)
        if not isinstance(saved, dict):
            return
        config = {**BU.DEFAULTS, **saved}
        self.title.setText(config["title"])
        self.desc.setPlainText(config["desc"])
        self.tags.setText(config["tags"])
        self.tid.setValue(int(config["tid"] or 0))
        self._select_statement(config.get("creation_statement"))
        self.season.setCurrentText(str(config.get("season") or ""))
        self.source.setText(config["source"])
        self.cover.setText(config["cover"])
        self.publish.setChecked(bool(config.get("auto_publish", True)))
        self.threads.setValue(BU.upload_threads(config.get("threads")))
        self.platform.setCurrentText(BU.normalize_platform(config.get("platform")))
        self._sync_source_platform(self.selected_paths(), force=True)
        self.refresh_preview()

    def save_template(self, name):
        config = {**self.current_config(), "date": ""}
        settings = BU.load_settings()
        templates = settings.get("upload_templates", {})
        templates = dict(templates) if isinstance(templates, dict) else {}
        templates[name] = config
        settings["upload_templates"] = templates
        settings["active_upload_template"] = name
        settings["upload"] = config
        try:
            BU.save_settings(settings)
            self._reload_templates(name)
            self.status.setText(f"模板“{name}”已保存；日期与视频每次重新选择")
            return True
        except OSError:
            self.status.setText("模板保存失败，请检查写入权限")
            return False

    def save_as_template(self):
        if self.busy:
            return
        name, ok = QInputDialog.getText(self, "另存为投稿模板", "模板名称")
        name = name.strip()
        if ok and name:
            if name in self._templates and QMessageBox.question(self, "更新已有模板",
                    f"模板“{name}”已存在，是否更新？", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            self.save_template(name)

    def delete_template(self):
        if self.busy:
            return
        name = self.templates.currentText()
        settings = BU.load_settings()
        templates = settings.get("upload_templates", {})
        if name not in templates:
            return
        if QMessageBox.question(self, "删除模板", f"删除模板“{name}”？",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        del templates[name]
        settings["upload_templates"] = templates
        settings["active_upload_template"] = next(iter(templates), "")
        try:
            BU.save_settings(settings)
            self._reload_templates(settings["active_upload_template"])
            self.load_template(self.templates.currentText())
            self.status.setText(f"模板“{name}”已删除")
        except OSError:
            self.status.setText("模板删除失败，请检查目录权限")

    def check_account(self):
        if self.busy or self._checking_account or self._closed:
            return
        self._checking_account = True
        self._verified_identity = ""
        self.start_btn.setEnabled(False)
        self.account_label.setText("正在自动验证登录与可用分区…")
        started_identity = BU.credential_identity()
        def work():
            uploader = BU.BiliUploader(cancel=self.account_cancel)
            try:
                account = uploader.check_account()
                try:
                    data, category_error = uploader.creator_info(), ""
                except BU.UploadError as error:
                    data, category_error = {}, str(error)
                try:
                    seasons = uploader.list_seasons()
                except (BU.UploadError, AttributeError):
                    seasons = []
                return account, data, uploader.auth_identity, category_error, seasons
            finally:
                uploader.close()
        def done(result, error):
            self._checking_account = False
            if self._closed:
                return
            if BU.credential_identity() != started_identity:
                self.account_label.setText("登录状态已变化，正在重新验证…")
                QTimer.singleShot(0, self.check_account)
                return
            if error:
                self.account_label.setText(str(error) + "；请检查当前登录状态")
                return
            account, data, identity, category_error, seasons = result
            if not identity or identity != BU.credential_identity():
                self.account_label.setText("登录状态已变化，正在重新验证…")
                QTimer.singleShot(0, self.check_account)
                return
            self._verified_identity = identity
            self.start_btn.setEnabled(bool(self.selected_paths()))
            self._fill_seasons(seasons)
            self.account_label.setText(f"当前投稿账号：{account['name']}（UID {account['mid']}）" +
                                      ("；分区读取失败，可手工填写分区 ID" if category_error else ""))
            current = self.tid.value()
            self.categories.blockSignals(True)
            self.categories.clear()
            self.categories.addItem("选择分区", 0)
            def visit(items, prefix=""):
                if isinstance(items, dict):
                    items = list(items.values())
                for item in items if isinstance(items, list) else []:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("typename") or "")
                    children = item.get("children") or item.get("sub")
                    if children:
                        visit(children, prefix + name + "/")
                    else:
                        value = item.get("id") or item.get("tid") or item.get("typeid")
                        if value:
                            self.categories.addItem(prefix + name + f"（{value}）", int(value))
            visit(data.get("typelist") or data.get("type_list") or [])
            self.categories.setCurrentIndex(max(0, self.categories.findData(current)))
            self.categories.blockSignals(False)
        run_bg(work, done, owner=self.parentWidget())

    def show_history(self):
        if self.busy:
            return
        records = BU.UploadHistory().recent()
        dialog = QDialog(self)
        dialog.setWindowTitle("上传历史 · 异常投稿须先核对")
        dialog.resize(680, 420)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        states = {"published": "已投稿", "uploaded": "已上传未投稿", "failed": "失败/取消",
                  "uncertain": "投稿结果不明", "submitting": "提交中/中断", "uploading": "上传中/中断"}
        for key, state, data in records:
            item = f"{states.get(state, state)} | {data.get('title')} | {data.get('account', {}).get('name', '')}"
            listing.addItem(item)
            listing.item(listing.count() - 1).setData(Qt.ItemDataRole.UserRole, (key, state, data))
        layout.addWidget(listing)
        link = QLabel('<a href="https://member.bilibili.com/platform/upload-manager/article">打开官方投稿管理核对稿件</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        retry = QPushButton("已核对没有稿件，允许所选任务重试")
        layout.addWidget(retry)
        def allow_retry():
            selected = listing.currentItem()
            if not selected:
                return
            key, state, data = selected.data(Qt.ItemDataRole.UserRole)
            if state == "published":
                QMessageBox.information(dialog, "已投稿", "已成功投稿的任务不能从这里重复发布")
                return
            if QMessageBox.question(dialog, "核对后重试", "请先确认没有正在上传的任务，且官方投稿管理没有相同稿件。确认允许重试？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                BU.UploadHistory().save(key, "failed", data)
                selected.setText("已允许重试 | " + data.get("title", ""))
        retry.clicked.connect(allow_retry)
        dialog.exec()

    def start_upload(self):
        if self.busy:
            return
        paths, config = self.selected_paths(), self.current_config()
        try:
            BU.validate_renamed_videos(paths)
            metadata = BU.build_metadata(paths, config)
        except (ValueError, TypeError) as error:
            self.status.setText(str(error))
            return
        if not self._verified_identity or self._verified_identity != BU.credential_identity():
            self.status.setText("正在自动验证登录，请稍候再开始上传")
            self.check_account()
            return
        if config["season"] and self._season_titles and config["season"] not in self._season_titles:
            self.status.setText(f"当前账号没有合集“{config['season']}”，请在“加入合集”里重新选择")
            return
        action = "上传并自动投稿" if config["auto_publish"] else "仅上传，不投稿"
        count_text = f"{len(paths)} P" if len(paths) > 1 else "1 个视频"
        if QMessageBox.question(self, "投稿预览", f"{action}\n\n{metadata['title']}\n{count_text} · 分区 {metadata['tid']}\n"
                f"创作声明：{self.statement_label()}\n合集：{config['season'] or '不加入合集'}\n"
                f"简介：{metadata['desc']}\n标签：{metadata['tag']}\n\n使用当前 B站账号，确认开始？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if not self.save_config():
            return
        self.busy = True
        self.cancel.clear()
        self._progress_started = time.monotonic()
        self.bar.setValue(0)
        self.progress_detail.setText("准备上传…")
        self.start_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.templates.setEnabled(False)
        self.save_as_btn.setEnabled(False)
        self.delete_template_btn.setEnabled(False)
        self.files.setEnabled(False)
        self.cancel_btn.setText("取消任务")
        self.status.setText("开始上传…")
        def work():
            self.uploader = BU.BiliUploader(cancel=self.cancel, on_progress=lambda done, total, text:
                self.progress.emit(int(done), int(total), text))
            try:
                return self.uploader.run(paths, config)
            finally:
                self.uploader.close()
        def done(result, error):
            self.busy = False
            self.start_btn.setEnabled(bool(self.selected_paths()) and bool(self._verified_identity))
            self.save_btn.setEnabled(True)
            self.templates.setEnabled(True)
            self.save_as_btn.setEnabled(True)
            self.delete_template_btn.setEnabled(True)
            self.files.setEnabled(True)
            self.cancel_btn.setText("关闭")
            if error:
                self.status.setText(str(error) + "；可到上传历史核对任务状态")
            else:
                self.bar.setValue(100)
                if result["state"] == "published":
                    text = "已投稿，等待 B站审核：" + str(result.get("bvid") or result.get("aid") or result["task"])
                    if result.get("season"):
                        text += f"；已加入合集“{result['season']}”"
                else:
                    text = "已上传；可用同一批视频再次进入投稿流程"
                if result.get("season_note"):
                    text += "；" + result["season_note"]
                self.status.setText(text)
        run_bg(work, done, owner=self.parentWidget())

    def on_progress(self, done, total, text):
        done, total = max(0, int(done or 0)), int(total or 0)
        if total > 0:
            self.bar.setValue(min(100, int(done * 100 / total)))
        else:
            self.bar.setValue(0)
        elapsed = max(0.001, time.monotonic() - getattr(self, "_progress_started", time.monotonic()))
        speed = done / elapsed
        parts = [f"{_fmt_size(done)} / {_fmt_size(total)}" if total else _fmt_size(done)]
        if speed > 0:
            parts.append(f"{_fmt_size(speed)}/s")
        if speed > 0 and total > done:
            parts.append("剩余 " + _fmt_duration((total - done) / speed))
        self.progress_detail.setText(" · ".join(parts))
        self.status.setText(text)

    def reject(self):
        if self.busy:
            self.cancel.set()
            self.status.setText("正在取消，等待当前网络请求结束；提交阶段请先核对稿件状态")
            return
        self._closed = True
        self.account_cancel.set()
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            self._closed = True
            self.account_cancel.set()
            super().closeEvent(event)


def open_upload_window(parent, paths=None, *, work_dir=None, blocked_paths=()):
    """打开（或复用）非模态投稿窗口：和直播录制二级界面一样，开着也能操作主界面。"""
    window = getattr(parent, "_bili_upload_window", None)
    try:
        if window is None:
            window = BiliUploadDialog(parent, paths, work_dir=work_dir, blocked_paths=blocked_paths)
            parent._bili_upload_window = window
        elif window.is_closed():
            window.reopen(paths or (), blocked_paths)
        else:
            window.merge_paths(paths or (), blocked_paths)
    except RuntimeError:      # 旧窗口的 Qt 对象已被回收，重建一个
        window = BiliUploadDialog(parent, paths, work_dir=work_dir, blocked_paths=blocked_paths)
        parent._bili_upload_window = window
    window.setModal(False)
    window.setWindowModality(Qt.WindowModality.NonModal)
    window.show()
    window.raise_()
    window.activateWindow()
    return window
