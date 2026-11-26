"""定时任务管理页：集中查看 / 设置 / 删除本软件注册的 Windows 计划任务。

- 列表：腾讯文档监控 / 网媒账号追踪 / 音乐账号追踪 / 网盘备份 / 站点访问统计 /
  粉丝群快照 / 作品增量 / 微博超话采集 / 抖音指数快照
- 每行：计划（本页统一维护）、安装状态、下次运行（schtasks）、设置 / 删除
- 顶部：刷新 / 全部删除
计划的模式、时间与间隔统一在本页「设置」对话框修改并安装，功能页不再单独提供定时设置；
时间用 24 小时制数字滚轮选择（时 / 分各一列，首尾无限循环）。
"""

from copy import deepcopy
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from gui_qt import table_sort, theme
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.time_wheel import TimeWheel

_COLS = ["任务", "用途", "计划", "状态", "下次运行", "操作"]


class _ScheduleDialog(QDialog):
    """统一修改某个任务的计划模式、时间与间隔。"""

    def __init__(self, parent, task, schedule=None):
        super().__init__(parent)
        from toolbox import task_center
        self._task_id = task["id"]
        self._schedule = (schedule if schedule is not None
                          else task_center.get_schedule(task["id"]))
        self.setWindowTitle(f"计划设置 · {task['label']}")
        self.setProperty("windowSurface", True)
        self.setMinimumSize(420, 300)
        self.setStyleSheet(
            "QLabel#scheduleCollectionError { color: palette(bright-text); font-size: 11px; }"
            "QListWidget#scheduleTargets[invalid=\"true\"] { border: 1px solid palette(bright-text); }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(self.scroll)
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)

        self.mode = QComboBox(self)
        self.mode.setMinimumWidth(140)
        self.mode.setFocusPolicy(Qt.StrongFocus)
        self.mode.addItem("每天一次", "daily")
        self.mode.addItem("按间隔", "interval")
        if not self._schedule["interval_supported"]:
            self.mode.setEnabled(False)
            self.mode.setToolTip("该任务只支持每天执行")

        self.time_wheel = TimeWheel(self._schedule["time"], self)
        self.time_wheel.setToolTip("上下滚动鼠标滚轮 / 拖动数字选择时间，24 小时制，23 点后回到 0 点")

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setFocusPolicy(Qt.StrongFocus)
        self.interval_spin.setRange(1, 1439)
        self.interval_spin.setSuffix(" 分钟")
        self.interval_spin.setValue(int(self._schedule["interval"]))
        self.interval_spin.setToolTip("1 ~ 1439 分钟；24 小时请用每天模式")

        form.addRow("执行方式", self.mode)
        form.addRow("每天时间", self.time_wheel)
        self.extra_wheels: list[tuple[QCheckBox, TimeWheel]] = []
        if self._task_id == "webwiki":
            row = QHBoxLayout()
            row.setSpacing(10)
            for index, value in enumerate(self._schedule.get("extra_times") or ["", ""]):
                text = str(value or "").strip()
                column = QVBoxLayout()
                column.setSpacing(2)
                enable = QCheckBox(f"补采 {index + 1}", self)
                enable.setChecked(bool(text))
                enable.setToolTip(
                    "勾选后启用补采时间：到点若今天还没有采集记录才采集，已有记录则跳过")
                wheel = TimeWheel(text or "13:00", self)
                wheel.setEnabled(bool(text))
                enable.toggled.connect(wheel.setEnabled)
                column.addWidget(enable)
                column.addWidget(wheel)
                row.addLayout(column)
                self.extra_wheels.append((enable, wheel))
            row.addStretch(1)
            holder = QWidget(self)
            holder.setLayout(row)
            form.addRow("补采时间", holder)
        form.addRow("间隔", self.interval_spin)

        self._collection_fields = {}
        self._collection_required = set()
        collection = self._schedule.get("collection") or {}
        collection_values = collection.get("values") or {}
        for definition in collection.get("fields") or []:
            self._add_collection_field(form, definition, collection_values)
        self.collection_error = QLabel(self)
        self.collection_error.setObjectName("scheduleCollectionError")
        self.collection_error.setWordWrap(True)
        self.collection_error.hide()
        if self._collection_fields:
            form.addRow("", self.collection_error)
            required_hint = QLabel("* 为必填；仅保存勾选的采集对象。", self)
            required_hint.setProperty("role", "caption")
            required_hint.setWordWrap(True)
            form.addRow("", required_hint)

        hint = QLabel("保存后立即注册 / 更新 Windows 计划任务；关闭软件也会按计划执行。", self)
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        form.addRow(hint)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        self.buttons.setLayoutDirection(Qt.RightToLeft)
        self.buttons.button(QDialogButtonBox.Ok).setText("保存并安装")
        self.buttons.button(QDialogButtonBox.Ok).setProperty("variant", "primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons, 0, Qt.AlignRight)

        self.mode.setCurrentIndex(max(0, self.mode.findData(self._schedule["mode"])))
        self.mode.currentIndexChanged.connect(self._sync_mode)
        self._sync_mode()
        available = self.screen().availableGeometry()
        self.resize(min(720, available.width() - 32),
                    min(640 if self._collection_fields else 520,
                        available.height() - 32))
        self._initial_state = (self.values(), self.collection_values())

    def _add_collection_field(self, form, definition, values):
        """只渲染任务中心提供的字段表，不维护第二份默认值与采集规则。"""
        name = str(definition.get("name") or "")
        kind = definition.get("type")
        if not name or kind not in {"multiselect", "int", "select"}:
            return
        value = values.get(name, definition.get("default"))
        required = bool(definition.get("required", name == "targets"))
        label = str(definition.get("label") or name) + (" *" if required else "")
        if required:
            self._collection_required.add(name)
        if kind == "multiselect":
            control = QListWidget(self)
            control.setObjectName("scheduleTargets")
            control.setMinimumHeight(96)
            control.setMaximumHeight(176)
            control.setSelectionMode(QAbstractItemView.NoSelection)
            control.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            control.setTextElideMode(Qt.ElideRight)
            selected = {str(item) for item in value or []}
            choices = list(definition.get("choices") or [])
            known = {str(item.get("value")) for item in choices}
            choices.extend({"value": item, "label": f"{item}（已订阅）"}
                           for item in value or [] if str(item) not in known)
            for choice in choices:
                item_value = str(choice.get("value") or "")
                if not item_value:
                    continue
                text = str(choice.get("label") or item_value)
                item = QListWidgetItem(text, control)
                item.setData(Qt.UserRole, item_value)
                item.setToolTip(text)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if item_value in selected else Qt.Unchecked)
            control.itemChanged.connect(self._clear_collection_error)
            if not control.count():
                empty = QLabel(str(definition.get("empty_hint") or
                                   "尚无可选对象，请先在对应采集页保存一次结果。"), self)
                empty.setWordWrap(True)
                empty.setProperty("role", "caption")
                holder = QWidget(self)
                column = QVBoxLayout(holder)
                column.setContentsMargins(0, 0, 0, 0)
                column.setSpacing(4)
                column.addWidget(control)
                column.addWidget(empty)
                form.addRow(label, holder)
            else:
                form.addRow(label, control)
        elif kind == "int":
            control = QSpinBox(self)
            control.setFocusPolicy(Qt.StrongFocus)
            control.setRange(int(definition["min"]), int(definition["max"]))
            if value is not None:
                control.setValue(int(value))
            if definition.get("suffix"):
                control.setSuffix(str(definition["suffix"]))
            form.addRow(label, control)
        else:
            control = QComboBox(self)
            control.setMinimumWidth(140)
            control.setFocusPolicy(Qt.StrongFocus)
            control.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(8)
            for choice in definition.get("choices") or []:
                control.addItem(str(choice.get("label") or choice.get("value")),
                                choice.get("value"))
            control.setCurrentIndex(max(0, control.findData(value)))
            form.addRow(label, control)
        self._collection_fields[name] = (kind, control)

    def _clear_collection_error(self, *_args):
        self.collection_error.clear()
        self.collection_error.hide()
        for kind, control in self._collection_fields.values():
            if kind == "multiselect" and control.property("invalid"):
                control.setProperty("invalid", False)
                control.style().unpolish(control)
                control.style().polish(control)

    def collection_values(self):
        if not self._collection_fields:
            return None
        values = {}
        for name, (kind, control) in self._collection_fields.items():
            if kind == "multiselect":
                values[name] = [control.item(index).data(Qt.UserRole)
                                for index in range(control.count())
                                if control.item(index).checkState() == Qt.Checked]
            elif kind == "int":
                values[name] = control.value()
            else:
                values[name] = control.currentData()
        return values

    def _sync_mode(self):
        interval = self.mode.currentData() == "interval"
        self.interval_spin.setEnabled(interval)
        self.time_wheel.setEnabled(not interval)

    def values(self) -> tuple[str, str, int, list[str]]:
        return (self.mode.currentData() or "daily",
                self.time_wheel.time(), int(self.interval_spin.value()),
                [wheel.time() if enable.isChecked() else ""
                 for enable, wheel in self.extra_wheels])

    def accept(self):
        values = self.collection_values() or {}
        for name in self._collection_required:
            if not values.get(name):
                _kind, control = self._collection_fields[name]
                self.collection_error.setText("还差 1 项必填：请至少勾选一个采集对象。")
                self.collection_error.show()
                control.setProperty("invalid", True)
                control.style().unpolish(control)
                control.style().polish(control)
                control.setFocus()
                self.scroll.ensureWidgetVisible(control)
                return
        # 滚轮选择的值天然合法（00-23 / 00-59），只保存不再校验格式
        super().accept()

    def reject(self):
        if (self._collection_fields and
                (self.values(), self.collection_values()) != self._initial_state):
            box = QMessageBox(self)
            box.setWindowTitle("放弃修改")
            box.setText("放弃尚未保存的计划与采集对象修改？")
            box.addButton("放弃修改", QMessageBox.DestructiveRole)
            keep = box.addButton("继续编辑", QMessageBox.RejectRole)
            box.setDefaultButton(keep)
            box.setEscapeButton(keep)
            box.exec()
            if box.buttonRole(box.clickedButton()) != QMessageBox.DestructiveRole:
                return
        super().reject()


class TasksPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        card = QFrame()
        card.setProperty("variant", "card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(8, 8, 8, 8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(refresh_btn)
        del_all_btn = QPushButton("全部删除")
        del_all_btn.setProperty("variant", "danger")
        del_all_btn.setToolTip("删除本软件注册的全部 Windows 计划任务")
        del_all_btn.clicked.connect(self._uninstall_all)
        bar.addWidget(del_all_btn)
        bar.addStretch(1)
        cv.addLayout(bar)

        hint = QLabel("由 Windows 计划任务执行，关闭软件也会按时运行；"
                      "计划统一在本页「设置」修改。")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        cv.addWidget(hint)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        # 表头排序：数据只在单元格 / 按钮闭包里，最后一列是操作控件 → 跳过
        self._sorter = table_sort.install(self.table, skip_columns=(5,))
        cv.addWidget(self.table)
        v.addWidget(card, 1)

        self._tasks: list[dict] = []
        self.refresh()

    def on_show(self):
        if time.monotonic() - getattr(self, "_refreshed_at", 0) > 8:
            self.refresh()

    def on_theme_changed(self):
        self._render()

    # ---------- 数据刷新 ----------
    def refresh(self):
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        def work():
            from toolbox.task_center import list_tasks
            return list_tasks()

        def done(tasks, err):
            self._refreshing = False
            if err:
                self.app.toast(f"读取定时任务失败：{err}", "error")
                return
            self._tasks = tasks or []
            self._refreshed_at = time.monotonic()
            self._render()

        run_bg(work, done)

    def _render(self):
        self.table.setRowCount(len(self._tasks))
        for i, t in enumerate(self._tasks):
            name_item = QTableWidgetItem(t["label"])
            name_item.setToolTip(f"Windows 任务名：{t['task_name']}")
            self.table.setItem(i, 0, name_item)
            self.table.setItem(i, 1, QTableWidgetItem(t["usage"]))
            self.table.setItem(i, 2, QTableWidgetItem(t["schedule"]))
            status = QTableWidgetItem("● 已安装" if t["installed"] else "○ 未安装")
            status.setForeground(QColor(theme.color("success" if t["installed"] else "muted")))
            self.table.setItem(i, 3, status)
            self.table.setItem(i, 4, QTableWidgetItem(t["next_run"]))
            self.table.setCellWidget(i, 5, self._ops_widget(t))
        self.table.resizeRowsToContents()

    def _ops_widget(self, t) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(4)
        set_btn = QPushButton("设置")
        set_btn.setFixedHeight(24)
        set_btn.setToolTip("修改该任务的计划模式、时间与间隔，并注册 / 更新计划任务")
        set_btn.clicked.connect(lambda _=False, task=t: self._open_schedule(task))
        h.addWidget(set_btn)
        del_btn = QPushButton("删除")
        del_btn.setFixedHeight(24)
        del_btn.setProperty("variant", "danger")
        del_btn.setEnabled(t["installed"])
        del_btn.setToolTip("删除该 Windows 计划任务（删除后关闭软件不会自动采集）")
        del_btn.clicked.connect(lambda _=False, task=t: self._uninstall_one(task))
        h.addWidget(del_btn)
        h.addStretch(1)
        return w

    # ---------- 计划设置 ----------
    def _open_schedule(self, task):
        if getattr(self, "_loading_schedule", False):
            return
        self._loading_schedule = True

        def work():
            from toolbox import task_center
            return task_center.get_schedule(task["id"])

        def done(schedule, err):
            self._loading_schedule = False
            if err:
                self.app.toast("读取计划设置失败，请重试。", "error")
                return
            self._edit_schedule(task, schedule)

        run_bg(work, done)

    def _edit_schedule(self, task, schedule, error_text=""):
        dialog = _ScheduleDialog(self, task, schedule)
        if error_text and dialog.collection_values() is not None:
            dialog.collection_error.setText(error_text)
            dialog.collection_error.show()
            QTimer.singleShot(0, lambda: dialog.scroll.ensureWidgetVisible(
                dialog.collection_error))
        if dialog.exec() != QDialog.Accepted:
            return
        mode, time_text, interval, extra_times = dialog.values()
        collection = dialog.collection_values()
        task_id = task["id"]
        draft = None
        if collection is not None:
            draft = deepcopy(schedule)
            draft.update(mode=mode, time=time_text, interval=interval,
                         extra_times=extra_times)
            draft["collection"]["values"] = collection

        def work():
            from toolbox import task_center
            if collection is not None:
                return task_center.install(task_id, mode, time_text, interval,
                                           extra_times=extra_times, collection=collection)
            return task_center.install(task_id, mode, time_text, interval, extra_times)

        def done(result, err):
            if draft is not None and (err or not result or not result[0]):
                self._edit_schedule(
                    task, draft,
                    "保存计划失败，请确认采集对象有效且有系统计划任务权限后重试。"
                    "本次填写内容已保留。")
                return
            if err:
                self.app.toast(f"安装定时任务失败：{err}", "error")
                return
            ok, message = result
            if not ok:
                QMessageBox.warning(self, "安装定时任务", f"安装失败：{message}")
                return
            self.app.toast(f"已保存并安装：{message}", "success")
            self.refresh()

        run_bg(work, done)

    # ---------- 删除 ----------
    def _uninstall_one(self, task):
        if QMessageBox.question(
                self, "删除定时任务",
                f"删除「{task['label']}」的 Windows 计划任务？\n\n"
                "删除后关闭软件也不会再自动采集（可随时在对应页面重新注册）。"
        ) != QMessageBox.Yes:
            return
        self._uninstall_many([task])

    def _uninstall_all(self):
        targets = [t for t in self._tasks if t["installed"]]
        if not targets:
            self.app.toast("当前没有已安装的定时任务", "info")
            return
        names = "\n".join(f"· {t['label']}" for t in targets)
        if QMessageBox.question(
                self, "删除全部定时任务",
                f"要删除以下 {len(targets)} 个定时任务吗？\n\n{names}\n\n"
                "删除后关闭软件不会再自动采集。"
        ) != QMessageBox.Yes:
            return
        self._uninstall_many(targets)

    def _uninstall_many(self, tasks):
        def work():
            from toolbox.task_center import uninstall
            return [(t["label"],) + uninstall(t["id"]) for t in tasks]

        def done(results, err):
            if err:
                self.app.toast(f"删除失败：{err}", "error")
                return
            failed = [f"{label}：{msg}" for label, ok, msg in results if not ok]
            if failed:
                QMessageBox.warning(self, "部分任务删除失败", "\n".join(failed))
            ok_n = len(results) - len(failed)
            if ok_n:
                self.app.toast(f"已删除 {ok_n} 个定时任务", "info")
            self.refresh()

        run_bg(work, done)
