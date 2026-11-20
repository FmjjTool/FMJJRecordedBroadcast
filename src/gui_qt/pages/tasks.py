"""定时任务管理页：集中查看 / 设置 / 删除本软件注册的 Windows 计划任务。

- 列表：腾讯文档监控 / 网媒账号追踪 / 音乐账号追踪 / 网盘备份 / 站点访问统计 /
  粉丝群快照 / 作品增量
- 每行：计划（本页统一维护）、安装状态、下次运行（schtasks）、设置 / 删除
- 顶部：刷新 / 全部删除
计划的模式、时间与间隔统一在本页「设置」对话框修改并安装，功能页不再单独提供定时设置；
时间用 24 小时制数字滚轮选择（时 / 分各一列，首尾无限循环）。
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from gui_qt import table_sort, theme
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.time_wheel import TimeWheel

_COLS = ["任务", "用途", "计划", "状态", "下次运行", "操作"]


class _ScheduleDialog(QDialog):
    """统一修改某个任务的计划模式、时间与间隔。"""

    def __init__(self, parent, task):
        super().__init__(parent)
        from toolbox import task_center
        self._task_id = task["id"]
        self._schedule = task_center.get_schedule(task["id"])
        self.setWindowTitle(f"计划设置 · {task['label']}")
        form = QFormLayout(self)
        form.setContentsMargins(16, 14, 16, 12)
        form.setSpacing(8)

        self.mode = QComboBox(self)
        self.mode.addItem("每天一次", "daily")
        self.mode.addItem("按间隔", "interval")
        if not self._schedule["interval_supported"]:
            self.mode.setEnabled(False)
            self.mode.setToolTip("该任务只支持每天执行")

        self.time_wheel = TimeWheel(self._schedule["time"], self)
        self.time_wheel.setToolTip("上下滚动鼠标滚轮 / 拖动数字选择时间，24 小时制，23 点后回到 0 点")

        self.interval_spin = QSpinBox(self)
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

        hint = QLabel("保存后立即注册 / 更新 Windows 计划任务；关闭软件也会按计划执行。", self)
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("保存并安装")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.mode.setCurrentIndex(max(0, self.mode.findData(self._schedule["mode"])))
        self.mode.currentIndexChanged.connect(self._sync_mode)
        self._sync_mode()
        self.resize(430, 430 if self.extra_wheels else 340)

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
        # 滚轮选择的值天然合法（00-23 / 00-59），只保存不再校验格式
        super().accept()


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
        dialog = _ScheduleDialog(self, task)
        if dialog.exec() != QDialog.Accepted:
            return
        mode, time_text, interval, extra_times = dialog.values()
        task_id = task["id"]

        def work():
            from toolbox import task_center
            return task_center.install(task_id, mode, time_text, interval, extra_times)

        def done(result, err):
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
