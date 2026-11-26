"""Desktop controls for the explicitly installed, optional local LLM runtime.

Only renders shared option metadata and invokes the local inference use cases.
Downloads and file operations remain in ``optional_runtime``.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import post_to_main, run_bg
from gui_qt.navigation import line_icon
from toolbox.livestream_summary.local_options import (
    FIELD_SPECS, defaults, normalize_options,
)


def _size(value) -> str:
    try:
        size = max(0, int(value))
    except (ValueError, TypeError):
        return ""
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    return f"{size / 1024 ** 2:.1f} MB" if size else ""


def _combo() -> QComboBox:
    combo = QComboBox()
    combo.setMinimumWidth(140)
    combo.setMinimumContentsLength(8)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setFocusPolicy(Qt.StrongFocus)
    combo.currentTextChanged.connect(combo.setToolTip)
    return combo


def _sync_model_search(combo: QComboBox) -> None:
    """Keep long model lists searchable without allowing arbitrary new IDs."""
    combo.setEditable(combo.count() > 12)
    combo.setInsertPolicy(QComboBox.NoInsert)
    if combo.isEditable():
        combo.lineEdit().setPlaceholderText("输入名称筛选模型")
        completer = combo.completer()
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)


class LocalLlmOptionsWidget(QWidget):
    """Eight model options generated from the shared, Qt-free schema."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controls = {}
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for spec in FIELD_SPECS:
            kind, key = spec["type"], spec["key"]
            if kind in ("select", "model"):
                control = _combo()
                if kind == "select":
                    for value, label in spec.get("choices", ()):
                        control.addItem(label, value)
                else:
                    control.addItem("选择已准备的 GGUF 模型", "")
                control.currentIndexChanged.connect(self._changed)
            else:
                control = QDoubleSpinBox() if kind == "float" else QSpinBox()
                control.setRange(spec["min"], spec["max"])
                control.setSingleStep(spec["step"])
                control.setFocusPolicy(Qt.StrongFocus)
                if kind == "float":
                    control.setDecimals(1)
                if key == "threads":
                    control.setSpecialValueText("自动")
                control.valueChanged.connect(self._changed)
            control.setObjectName("localLlmOption_" + key)
            self.controls[key] = control
            form.addRow(spec["label"], control)
        self.controls["backend"].currentIndexChanged.connect(self._sync_backend)
        self.set_values(defaults())

    def _changed(self, *_args):
        self.changed.emit()

    def _sync_backend(self, *_args):
        self.controls["gpu_layers"].setEnabled(
            self.controls["backend"].currentData() == "vulkan")

    def values(self) -> dict:
        values = {}
        for spec in FIELD_SPECS:
            control = self.controls[spec["key"]]
            values[spec["key"]] = (control.currentData()
                                   if spec["type"] in ("select", "model")
                                   else control.value())
        return normalize_options(values)

    def set_values(self, values):
        values = normalize_options(values, strict=False)
        for spec in FIELD_SPECS:
            key, control = spec["key"], self.controls[spec["key"]]
            control.blockSignals(True)
            if spec["type"] in ("select", "model"):
                index = control.findData(values[key])
                if index < 0 and spec["type"] == "model" and values[key]:
                    control.addItem("未找到模型：" + values[key], values[key])
                    index = control.count() - 1
                control.setCurrentIndex(max(0, index))
            else:
                control.setValue(values[key])
            control.blockSignals(False)
        self._sync_backend()

    def set_status(self, snapshot):
        control = self.controls["model"]
        selected = control.currentData() or ""
        control.blockSignals(True)
        control.clear()
        control.addItem("选择已准备的 GGUF 模型", "")
        for item in snapshot.get("models", ()):
            name = str(item.get("name") or item.get("id") or "")
            size = _size(item.get("size"))
            control.addItem(name + (" · " + size if size else ""), item["id"])
        index = control.findData(selected)
        if selected and index < 0:
            control.addItem("未找到模型：" + selected, selected)
            index = control.count() - 1
        control.setCurrentIndex(max(0, index))
        _sync_model_search(control)
        control.blockSignals(False)


class LocalLlmComponentsPanel(QWidget):
    """Global-settings-only entry for optional runtime/model file actions."""

    def __init__(self, app, *, busy_check=None, busy_changed=None,
                 on_complete=None, parent=None):
        super().__init__(parent)
        self.app = app
        self._busy_check = busy_check or (lambda: False)
        self._busy_changed = busy_changed or (lambda _busy: None)
        self._on_complete = on_complete or (lambda: None)
        self._cancel = None
        self._refreshing = False
        self._snapshot = {}
        self._operation_error = ""
        self._icons = []
        self._actions = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel("本地总结组件（可选）")
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)
        note = QLabel("CPU / GPU 模块与 GGUF 模型分开准备，标准版和 FULL 版均不内置；不安装也可使用接口或本地规则总结。")
        note.setWordWrap(True)
        layout.addWidget(note)

        runtime_row = QHBoxLayout()
        runtime_row.setSpacing(8)
        runtime_row.addWidget(QLabel("推理模块"))
        self.runtime_combo = _combo()
        for spec in FIELD_SPECS:
            if spec["key"] == "backend":
                for value, label in spec["choices"]:
                    self.runtime_combo.addItem(label, value)
        runtime_row.addWidget(self.runtime_combo, 1)
        self.install_button = self._button("下载", "download", self._install_runtime)
        runtime_row.addWidget(self.install_button)
        self.remove_runtime_button = self._button("卸载", "trash", self._remove_runtime)
        self.remove_runtime_button.setProperty("variant", "danger")
        runtime_row.addWidget(self.remove_runtime_button)
        self.refresh_button = self._button("刷新", "refresh", self.refresh)
        runtime_row.addWidget(self.refresh_button)
        layout.addLayout(runtime_row)

        download_row = QHBoxLayout()
        download_row.setSpacing(8)
        download_row.addWidget(QLabel("推荐模型"))
        self.catalog_combo = _combo()
        download_row.addWidget(self.catalog_combo, 1)
        self.download_button = self._button("下载模型", "download", self._download_model)
        download_row.addWidget(self.download_button)
        layout.addLayout(download_row)

        installed_row = QHBoxLayout()
        installed_row.setSpacing(8)
        installed_row.addWidget(QLabel("已准备模型"))
        self.model_combo = _combo()
        installed_row.addWidget(self.model_combo, 1)
        self.import_button = self._button("导入模型", "folder", self._import_model)
        installed_row.addWidget(self.import_button)
        self.remove_model_button = self._button("卸载", "trash", self._remove_model)
        self.remove_model_button.setProperty("variant", "danger")
        installed_row.addWidget(self.remove_model_button)
        layout.addLayout(installed_row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(16)
        progress_row.addWidget(self.progress, 1)
        self.stop_button = self._button("停止", "stop", self.cancel, action=False)
        self.stop_button.setEnabled(False)
        progress_row.addWidget(self.stop_button)
        layout.addLayout(progress_row)
        self.status_label = QLabel("正在检测本地总结组件…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.runtime_combo.currentIndexChanged.connect(self._sync_actions)
        self.catalog_combo.currentIndexChanged.connect(self._sync_actions)
        self.model_combo.currentIndexChanged.connect(self._sync_actions)
        self.on_theme_changed()
        self.refresh()

    def _button(self, text, icon, callback, *, action=True):
        button = QPushButton(text)
        button.setProperty("variant", "secondary")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _checked=False: callback())
        self._icons.append((button, icon))
        if action:
            self._actions.append(button)
        self.on_theme_changed()
        return button

    def on_theme_changed(self):
        for button, icon in self._icons:
            role = QPalette.BrightText if button.property("variant") == "danger" else QPalette.ButtonText
            color = theme.current_palette().color(role).name()
            button.setIcon(line_icon(icon, color, 16))
            button.setIconSize(QSize(16, 16))

    def is_busy(self):
        return self._cancel is not None

    def _sync_actions(self, *_args):
        active = self.is_busy()
        supported = bool(self._snapshot.get("supported", False))
        for button in self._actions:
            button.setEnabled(not active and supported)
        self.refresh_button.setEnabled(not active)
        self.stop_button.setEnabled(active and not self._cancel.is_set())
        for combo in (self.runtime_combo, self.catalog_combo, self.model_combo):
            combo.setEnabled(not active and supported)
        selected = self.runtime_combo.currentData()
        installed = any(item.get("id") == selected and item.get("installed")
                        for item in self._snapshot.get("runtimes", ()))
        self.remove_runtime_button.setEnabled(not active and supported and installed)
        self.remove_model_button.setEnabled(
            not active and supported and bool(self.model_combo.currentData()))
        self.download_button.setEnabled(
            not active and supported and bool(self.catalog_combo.currentData()))

    def refresh(self):
        if self._refreshing or self.is_busy():
            return
        self._refreshing = True

        def work():
            from toolbox.livestream_summary import optional_runtime
            return optional_runtime.status()

        def done(snapshot, error):
            self._refreshing = False
            if error:
                self.status_label.setText("检测本地总结组件失败，请刷新后重试。")
                self._snapshot = {}
                self._sync_actions()
                return
            self._snapshot = snapshot or {}
            for item in self._snapshot.get("runtimes", ()):
                index = self.runtime_combo.findData(item["id"])
                if index >= 0:
                    size = _size(item.get("download_size"))
                    self.runtime_combo.setItemText(
                        index, str(item.get("label") or item["id"])
                        + (" · 下载 " + size if size else ""))
            previous = self.model_combo.currentData()
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            for item in self._snapshot.get("models", ()):
                size = _size(item.get("size"))
                self.model_combo.addItem(
                    str(item.get("name") or item["id"]) + (" · " + size if size else ""),
                    item["id"])
            if not self.model_combo.count():
                self.model_combo.addItem("还没有模型，可下载或导入 GGUF", "")
            index = self.model_combo.findData(previous)
            self.model_combo.setCurrentIndex(max(0, index))
            _sync_model_search(self.model_combo)
            self.model_combo.blockSignals(False)
            previous = self.catalog_combo.currentData()
            self.catalog_combo.clear()
            for item in self._snapshot.get("model_catalog", ()):
                size = _size(item.get("size"))
                self.catalog_combo.addItem(
                    str(item.get("name") or item.get("label") or item["id"])
                    + (" · " + size if size else ""), item["id"])
            index = self.catalog_combo.findData(previous)
            self.catalog_combo.setCurrentIndex(max(0, index))
            states = [str(item.get("label") or item["id"]) + "："
                      + ("已安装" if item.get("installed") else "未安装")
                      for item in self._snapshot.get("runtimes", ())]
            status_text = ("；".join(states) if self._snapshot.get("supported")
                           else str(self._snapshot.get("note") or "仅支持 Windows x64"))
            self.status_label.setText(status_text + ("\n" + self._operation_error
                                                     if self._operation_error else ""))
            self._sync_actions()

        run_bg(work, done, owner=self)

    def _run(self, label, work):
        if self.is_busy() or self._busy_check():
            self.app.toast("已有组件操作在进行中，请结束后重试", "warning")
            return
        self._cancel = threading.Event()
        self._operation_error = ""
        cancel = self._cancel
        self._busy_changed(True)
        self.progress.setValue(0)
        self.status_label.setText(label + "中…")
        self._sync_actions()

        def progress(percent, message):
            post_to_main(self._set_progress, percent, message)

        def done(result, error):
            was_cancelled = cancel.is_set()
            self._cancel = None
            self._busy_changed(False)
            if error:
                message = "已取消" if was_cancelled else label + "失败：" + str(error)[:240]
                self._operation_error = message
                self.status_label.setText(message)
            else:
                self.progress.setValue(100)
                self.status_label.setText(label + "已完成")
                self.app.toast(label + "已完成", "success")
            self._sync_actions()
            self._on_complete()
            self.refresh()

        run_bg(lambda: work(progress, cancel), done, owner=self)

    def _set_progress(self, percent, message):
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status_label.setText(str(message or "进行中"))

    def cancel(self):
        if self._cancel is not None:
            self._cancel.set()
            self.status_label.setText("正在取消…")
            self.stop_button.setEnabled(False)

    def _install_runtime(self):
        from toolbox.livestream_summary import optional_runtime
        backend = self.runtime_combo.currentData()
        self._run("下载推理模块", lambda progress, cancel: optional_runtime.install_runtime(
            backend, progress=progress, cancel=cancel))

    def _download_model(self):
        from toolbox.livestream_summary import optional_runtime
        model = self.catalog_combo.currentData()
        if model:
            self._run("下载模型", lambda progress, cancel: optional_runtime.download_model(
                model, progress=progress, cancel=cancel))

    def _import_model(self):
        from toolbox.livestream_summary import optional_runtime
        path, _ = QFileDialog.getOpenFileName(self, "导入 GGUF 模型", "", "GGUF 模型 (*.gguf)")
        if path:
            self._run("导入模型", lambda progress, cancel: optional_runtime.import_model(
                path, progress=progress, cancel=cancel))

    def _confirm_remove(self, name, detail):
        box = QMessageBox(self)
        box.setWindowTitle("卸载组件")
        box.setIcon(QMessageBox.Warning)
        box.setText("卸载「" + name + "」？")
        box.setInformativeText(detail)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        remove = box.addButton("卸载", QMessageBox.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is remove

    def _remove_runtime(self):
        from toolbox.livestream_summary import optional_runtime
        selected = self.runtime_combo.currentData()
        name = self.runtime_combo.currentText()
        if selected and self._confirm_remove(name, "只删除所选推理模块，模型文件保留；卸载后可重新下载。"):
            self._run("卸载推理模块", lambda _progress, _cancel:
                      optional_runtime.remove_runtime(selected))

    def _remove_model(self):
        from toolbox.livestream_summary import optional_runtime
        selected = self.model_combo.currentData()
        name = self.model_combo.currentText()
        if selected and self._confirm_remove(name, "只删除程序管理目录内的所选模型，导入源文件与其他模型保留；卸载后可重新准备。"):
            self._run("卸载模型", lambda _progress, _cancel:
                      optional_runtime.remove_model(selected))
