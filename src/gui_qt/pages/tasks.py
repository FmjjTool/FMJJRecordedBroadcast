"""定时任务管理页：集中查看 / 删除本软件注册的 Windows 计划任务。

- 列表：腾讯文档监控 / 网媒账号追踪 / 音乐账号追踪
- 每行：计划（各页保存的配置）、安装状态、下次运行（schtasks）、⚙ 设置 / 🗑 删除
- 顶部：🔄 刷新 / 🗑 全部删除
任务由对应页面的「定时采集」弹窗注册，本页只做统一查看与删除。
"""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

_COLS = ["任务", "用途", "计划", "状态", "下次运行", "操作"]


class TasksPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("⏰ 定时任务管理")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self.refresh)
        head.addWidget(refresh_btn)
        del_all_btn = QPushButton("🗑 全部删除")
        del_all_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c62828; color: #c62828;")
        del_all_btn.setToolTip("删除本软件注册的全部 Windows 计划任务")
        del_all_btn.clicked.connect(self._uninstall_all)
        head.addWidget(del_all_btn)
        v.addLayout(head)

        hint = QLabel("这些任务由 Windows 计划任务执行：关闭软件后也会按计划自动采集；"
                      "删除任务后不再自动采集，可到对应页面的「⏰ 定时采集」重新注册")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        card = QFrame()
        card.setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        cv.addWidget(self.table)
        v.addWidget(card, 1)

        self._tasks: list[dict] = []
        self.refresh()

    def on_show(self):
        self.refresh()

    # ---------- 数据刷新 ----------
    def refresh(self):
        def work():
            from toolbox.task_center import list_tasks
            return list_tasks()

        def done(tasks, err):
            if err:
                self.app.toast(f"读取定时任务失败：{err}", "error")
                return
            self._tasks = tasks or []
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
            status.setForeground(QColor("#4caf50" if t["installed"] else "#888888"))
            self.table.setItem(i, 3, status)
            self.table.setItem(i, 4, QTableWidgetItem(t["next_run"]))
            self.table.setCellWidget(i, 5, self._ops_widget(t))
        self.table.resizeRowsToContents()

    def _ops_widget(self, t) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(4)
        set_btn = QPushButton("⚙ 设置")
        set_btn.setFixedHeight(24)
        set_btn.setToolTip("跳到对应页面，注册 / 修改定时采集")
        set_btn.clicked.connect(lambda _=False, p=t["page"]: self.app.show_page(p))
        h.addWidget(set_btn)
        del_btn = QPushButton("🗑 删除")
        del_btn.setFixedHeight(24)
        del_btn.setStyleSheet(
            "QPushButton { background: #c62828; color: white; font-size: 12px; padding: 2px 10px; }"
            "QPushButton:disabled { background: #e3e7ee; color: #9aa3b2; }")
        del_btn.setEnabled(t["installed"])
        del_btn.setToolTip("删除该 Windows 计划任务（删除后关闭软件不会自动采集）")
        del_btn.clicked.connect(lambda _=False, task=t: self._uninstall_one(task))
        h.addWidget(del_btn)
        h.addStretch(1)
        return w

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
                self.app.toast(f"🗑 已删除 {ok_n} 个定时任务", "info")
            self.refresh()

        run_bg(work, done)
