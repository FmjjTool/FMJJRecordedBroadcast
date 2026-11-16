"""可预览文案、选择分P与封面、查看失败记录的 B站投稿入口。"""
import threading

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout)

from gui_qt.common import run_bg
from toolbox import bili_upload as BU


class BiliUploadDialog(QDialog):
    progress = Signal(int, str)

    def __init__(self, parent=None, paths=None):
        super().__init__(parent)
        self.setWindowTitle("B站视频上传 · 文案与分P预览")
        self.resize(760, 760)
        self.paths = list(paths or [])
        self.settings = BU.load_settings()
        self.config = {**BU.DEFAULTS, **self.settings.get("upload", {})}
        self.busy = False
        self.cancel = threading.Event()
        self.uploader = None
        layout = QVBoxLayout(self)
        hint = QLabel("复用 Cookie 管理的 B站账号；多个视频按列表顺序组成分P。图片示例未提供分区、简介和来源，请自行填写。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.files = QListWidget()
        self.files.setMaximumHeight(100)
        self.files.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        layout.addWidget(self.files)
        row = QHBoxLayout()
        for text, slot in (("选择视频", self.choose_files), ("移除所选", self.remove_files),
                           ("检查登录/分区", self.check_account), ("上传历史", self.show_history)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        form = QFormLayout()
        self.title = QLineEdit(self.config["title"])
        form.addRow("标题模板", self.title)
        self.date = QLineEdit(self.config["date"])
        self.date.setPlaceholderText("留空从视频文件名识别；可填 20261002")
        form.addRow("本次直播日期", self.date)
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
        self.categories.addItem("检查登录后可读取账号可用分区", 0)
        self.categories.currentIndexChanged.connect(lambda: self.tid.setValue(int(self.categories.currentData() or 0)))
        form.addRow("账号可用分区", self.categories)
        self.copyright = QComboBox()
        self.copyright.addItem("转载（原直播录播）", 2)
        self.copyright.addItem("自制", 1)
        self.copyright.setCurrentIndex(0 if self.config["copyright"] == 2 else 1)
        form.addRow("投稿类型", self.copyright)
        self.source = QLineEdit(self.config["source"])
        self.source.setPlaceholderText("转载稿件填写原直播间链接")
        form.addRow("视频来源", self.source)
        cover_row = QHBoxLayout()
        self.cover = QLineEdit(self.config["cover"])
        self.cover.setPlaceholderText("留空自动提取首帧封面")
        cover_row.addWidget(self.cover)
        button = QPushButton("选择封面")
        button.clicked.connect(self.choose_cover)
        cover_row.addWidget(button)
        form.addRow("封面", cover_row)
        self.publish = QCheckBox("上传成功后自动投稿（进入 B站审核）")
        self.publish.setChecked(bool(self.config.get("auto_publish", True)))
        form.addRow(self.publish)
        layout.addLayout(form)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(110)
        layout.addWidget(self.preview)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)
        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.preview_btn = QPushButton("更新预览")
        self.preview_btn.clicked.connect(self.refresh_preview)
        actions.addWidget(self.preview_btn)
        self.save_btn = QPushButton("保存配置")
        self.save_btn.clicked.connect(self.save_config)
        actions.addWidget(self.save_btn)
        self.start_btn = QPushButton("开始上传")
        self.start_btn.clicked.connect(self.start_upload)
        actions.addWidget(self.start_btn)
        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.clicked.connect(self.reject)
        actions.addWidget(self.cancel_btn)
        layout.addLayout(actions)
        self.progress.connect(self.on_progress)
        self.files.addItems(self.paths)
        self.refresh_preview()

    def selected_paths(self):
        return [self.files.item(index).text() for index in range(self.files.count())]

    def current_config(self):
        return {"title": self.title.text(), "date": self.date.text().strip(),
                "desc": self.desc.toPlainText(), "tags": self.tags.text(), "tid": self.tid.value(),
                "copyright": self.copyright.currentData(), "source": self.source.text().strip(),
                "cover": self.cover.text().strip(), "auto_publish": self.publish.isChecked()}

    def choose_files(self):
        if self.busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择投稿视频（可多选、拖动调整分P顺序）", "",
             "视频 (*.mp4 *.flv *.mkv *.ts *.mov *.avi *.webm *.m4v *.mpeg *.mpg *.mts *.m2ts *.wmv *.ogv)")
        known = set(self.selected_paths())
        self.files.addItems([path for path in paths if path not in known])
        self.refresh_preview()

    def remove_files(self):
        if not self.busy:
            for item in self.files.selectedItems():
                self.files.takeItem(self.files.row(item))
            self.refresh_preview()

    def choose_cover(self):
        if not self.busy:
            path, _ = QFileDialog.getOpenFileName(self, "选择封面", "", "图片 (*.png *.jpg *.jpeg *.webp)")
            if path:
                self.cover.setText(path)

    def refresh_preview(self):
        try:
            metadata = BU.build_metadata(self.selected_paths(), self.current_config())
            self.preview.setPlainText(f"标题：{metadata['title']}\n简介：{metadata['desc']}\n"
                f"标签：{metadata['tag']}\n分区：{metadata['tid']} · {self.files.count()} P\n来源：{metadata['source']}")
        except (ValueError, TypeError) as error:
            self.preview.setPlainText(str(error))

    def save_config(self):
        config = self.current_config()
        # 日期仅用于本次手工覆盖，不能把上一场日期固化到自动投稿。
        config["date"] = ""
        settings = BU.load_settings()
        settings["upload"] = config
        try:
            BU.save_settings(settings)
            self.status.setText("配置已保存，直播日期每次从文件名识别")
        except OSError:
            self.status.setText("配置保存失败，请检查写入权限")

    def check_account(self):
        if self.busy:
            return
        self.busy = True
        self.start_btn.setEnabled(False)
        self.status.setText("正在检查登录与可用分区…")
        def work():
            uploader = BU.BiliUploader()
            try:
                return uploader.check_account(), uploader.creator_info()
            finally:
                uploader.close()
        def done(result, error):
            self.busy = False
            self.start_btn.setEnabled(True)
            if error:
                self.status.setText(str(error))
                return
            account, data = result
            self.status.setText(f"当前投稿账号：{account['name']}（UID {account['mid']}）")
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
        run_bg(work, done)

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
            metadata = BU.build_metadata(paths, config)
        except (ValueError, TypeError) as error:
            self.status.setText(str(error))
            return
        action = "上传并自动投稿" if config["auto_publish"] else "仅上传，不投稿"
        if QMessageBox.question(self, "投稿预览", f"{action}\n\n{metadata['title']}\n{len(paths)} P · 分区 {metadata['tid']}\n"
                f"简介：{metadata['desc']}\n标签：{metadata['tag']}\n\n使用 Cookie 管理当前的 B站账号，确认开始？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.save_config()
        self.busy = True
        self.cancel.clear()
        self.start_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.cancel_btn.setText("取消任务")
        self.status.setText("开始上传…")
        def work():
            self.uploader = BU.BiliUploader(cancel=self.cancel, on_progress=lambda done, total, text:
                self.progress.emit(int(done * 100 / max(1, total)), text))
            try:
                return self.uploader.run(paths, config)
            finally:
                self.uploader.close()
        def done(result, error):
            self.busy = False
            self.start_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.cancel_btn.setText("关闭")
            if error:
                self.status.setText(str(error) + "；可到上传历史核对任务状态")
            else:
                self.bar.setValue(100)
                self.status.setText("已投稿，等待 B站审核：" + str(result.get("bvid") or result.get("aid") or result["task"])
                                    if result["state"] == "published" else "已上传；可用同一批视频再次进入投稿流程")
        run_bg(work, done)

    def on_progress(self, percent, text):
        self.bar.setValue(percent)
        self.status.setText(text)

    def reject(self):
        if self.busy:
            self.cancel.set()
            self.status.setText("正在取消，等待当前网络请求结束；提交阶段请先核对稿件状态")
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
