"""实用工具 · 文件时间修改页：批量改 创建/修改/访问 时间。

支持：指定时间、偏移量、按文件名提取时间、复制自另一文件。
Windows 下创建时间通过 win32file 或 ctypes 修改。
"""

import os
from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView, QDateTimeEdit, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from gui_qt import table_sort
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from toolbox.file_time_rules import (parse_offset, parse_time_from_name,
                                     set_file_times)


class FileTimePage(BasePage):
    def restore_session_data(self):
        self.files = [p for p in self.files if os.path.isfile(p)]
        self._render()

    def __init__(self, app):
        super().__init__(app)
        self.files: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 文件选择
        top = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_files.clicked.connect(lambda: self._pick_files(False))
        add_dir = QPushButton("添加文件夹")
        add_dir.clicked.connect(lambda: self._pick_files(True))
        clear = QPushButton("清空")
        clear.clicked.connect(self._clear)
        self.cnt = QLabel("0 个文件（可直接拖入）")
        self.cnt.setStyleSheet("color:#888;")
        self.cnt.setToolTip("在表格中选中行（可多选）后，预览/应用只作用于选中的文件；"
                            "不选中则为全部文件")
        top.addWidget(add_files)
        top.addWidget(add_dir)
        top.addWidget(clear)
        top.addWidget(self.cnt, 1)
        layout.addLayout(top)
        self.setAcceptDrops(True)

        # 文件表
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["文件", "修改时间", "创建时间", "新修改时间", "新创建时间", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # 行号 == self.files 下标：_scope() 用选中行反查文件，排序时必须同步重排列表
        self._sorter = table_sort.install(self.table, skip_columns=())
        layout.addWidget(self.table, 1)

        # 模式区
        mode = QHBoxLayout()
        mode.addWidget(QLabel("修改方式："))
        self.rb_set = QRadioButton("指定时间")
        self.rb_offset = QRadioButton("偏移量")
        self.rb_name = QRadioButton("按文件名时间")
        self.rb_set.setChecked(True)
        self.rb_set.toggled.connect(self._update_mode)
        mode.addWidget(self.rb_set)
        mode.addWidget(self.rb_offset)
        mode.addWidget(self.rb_name)
        mode.addStretch(1)
        layout.addLayout(mode)

        # 参数区
        pr = QHBoxLayout()
        self.time_dt = QDateTimeEdit(datetime.now())
        self.time_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.time_dt.setCalendarPopup(True)
        pr.addWidget(self.time_dt)
        self.offset_entry = QLineEdit()
        self.offset_entry.setPlaceholderText("偏移量，如 -210m / +2d / +90m / -30s")
        self.offset_entry.setText("+0h")
        self.offset_entry.setEnabled(False)
        pr.addWidget(self.offset_entry)
        preview = QPushButton("预览")
        preview.clicked.connect(self._preview)
        apply_btn = QPushButton("应用")
        apply_btn.setStyleSheet("background:#2e7d32;color:white;padding:5px 18px;")
        apply_btn.clicked.connect(self._apply)
        pr.addWidget(preview)
        pr.addWidget(apply_btn)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#888;")
        pr.addWidget(self.status, 1)
        layout.addLayout(pr)

    # ---------- 文件 ----------
    def _pick_files(self, from_dir: bool):
        if from_dir:
            d = QFileDialog.getExistingDirectory(self, "选择文件夹")
            if not d:
                return
            new = []
            for root, _dirs, names in os.walk(d):
                for n in names:
                    new.append(os.path.join(root, n))
        else:
            new, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        self.files.extend(new)
        self._render()

    def _clear(self):
        self.files.clear()
        self._render()

    def _render(self):
        self.cnt.setText(f"{len(self.files)} 个文件")
        self.table.setRowCount(0)
        shown = self.files[:1000]        # 表格只显示前 1000 个文件
        for i, f in enumerate(shown):
            self.table.insertRow(i)
            st = os.stat(f)
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(f)))
            self.table.setItem(i, 1, QTableWidgetItem(datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
            self.table.setItem(i, 2, QTableWidgetItem(datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")))
            self.table.setItem(i, 5, QTableWidgetItem(""))
        # 只重排显示段：排序后把这一段写回 self.files，选中行反查仍然对得上
        self._sorter.attach(shown).apply()
        self.files[:len(shown)] = shown

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        from PySide6.QtCore import QUrl
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                for root, _d, names in os.walk(p):
                    for n in names:
                        self.files.append(os.path.join(root, n))
            elif os.path.isfile(p):
                self.files.append(p)
        self._render()

    # ---------- 计算 ----------
    def _update_mode(self):
        self.time_dt.setEnabled(self.rb_set.isChecked())
        self.offset_entry.setEnabled(self.rb_offset.isChecked())

    def _scope(self) -> list[tuple[int, str]]:
        """操作范围：表格有选中行 → 仅选中文件；否则全部文件。"""
        sel = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if sel:
            return [(i, self.files[i]) for i in sel if i < len(self.files)]
        return list(enumerate(self.files))

    def _compute(self, f: str) -> tuple[float, float, float] | str:
        """返回 (atime, mtime, ctime) 或错误文本。"""
        st = os.stat(f)
        if self.rb_set.isChecked():
            ts = self.time_dt.dateTime().toSecsSinceEpoch()
            return (ts, ts, ts)
        if self.rb_offset.isChecked():
            delta = parse_offset(self.offset_entry.text())
            if isinstance(delta, str):
                return delta
            return (st.st_atime + delta, st.st_mtime + delta,
                    (st.st_ctime + delta) if os.name == "nt" else None)
        # 按文件名
        got = parse_time_from_name(os.path.basename(f))
        if not got:
            return "文件名无时间"
        return got

    def _preview(self):
        scope = self._scope()
        ok = 0
        for i, f in scope:
            r = self._compute(f)
            if i >= self.table.rowCount():
                continue
            if isinstance(r, str):
                self.table.setItem(i, 3, QTableWidgetItem(r))
                self.table.setItem(i, 4, QTableWidgetItem(""))
                self.table.setItem(i, 5, QTableWidgetItem(""))
            else:
                fmt = datetime.fromtimestamp(r[1]).strftime("%Y-%m-%d %H:%M:%S")
                self.table.setItem(i, 3, QTableWidgetItem(fmt))
                self.table.setItem(i, 4, QTableWidgetItem(datetime.fromtimestamp(r[2]).strftime("%Y-%m-%d %H:%M:%S") if r[2] else "（不变）"))
                self.table.setItem(i, 5, QTableWidgetItem("待应用"))
            if not isinstance(r, str):
                ok += 1
        only = len(scope) < len(self.files)
        tail = f"（仅选中 {len(scope)} 项）" if only else ""
        self.status.setText(f"预览：{ok}/{len(scope)} 可应用{tail}")

    def _apply(self):
        if not self.files:
            self.app.toast("请先添加文件", "warning")
            return
        scope = self._scope()
        # 主线程先把目标值全部算好：后台线程不再读控件（跨线程访问 QWidget 属未定义行为）
        plan = [(i, f, self._compute(f)) for i, f in scope]
        results = []

        def job():
            ok = 0
            for i, f, r in plan:
                if isinstance(r, str):
                    results.append((i, r))
                    continue
                try:
                    atime, mtime, ctime = r
                    set_file_times(f, atime, mtime, ctime)
                    results.append((i, ""))
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    results.append((i, str(e)))
            return ok

        def done(ok, err):
            if err:
                self.status.setText(f"应用出错：{err}")
                return
            fails = [(i, msg) for i, msg in results if msg]
            for i, _msg in fails:
                if i < self.table.rowCount():
                    self.table.setItem(i, 5, QTableWidgetItem("失败"))
            for i, msg in results:
                if i < self.table.rowCount() and not msg:
                    self.table.setItem(i, 5, QTableWidgetItem("成功"))
            only = len(plan) < len(self.files)
            tail = (f"（仅选中 {len(plan)} 项）" if only else "") + \
                (f"，失败 {len(fails)}" if fails else "")
            self.status.setText(f"完成 {ok}/{len(plan)}{tail}")
            self.app.toast(f"时间修改完成 {ok}/{len(plan)}", "success" if ok == len(plan) else "warning")

        run_bg(job, done)
