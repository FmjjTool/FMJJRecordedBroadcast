"""实用工具 · 文件时间修改页：批量改 创建/修改/访问 时间。

支持：指定时间、偏移量、按文件名提取时间、复制自另一文件。
Windows 下创建时间通过 win32file 或 ctypes 修改。
"""

import os
import re
import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage


def set_file_times(path: str, atime: float, mtime: float, ctime: float | None):
    """设置文件时间。ctime 仅 Windows 生效（pywin32 优先，无则 ctypes）。"""
    os.utime(path, (atime, mtime))
    if ctime is None or os.name != "nt":
        return
    try:
        import pywintypes
        import win32file
        h = win32file.CreateFileW(path, win32file.GENERIC_WRITE,
                                  0, None, win32file.OPEN_EXISTING, 0, None)
        try:
            win32file.SetFileTime(h, pywintypes.Time(ctime), pywintypes.Time(atime), pywintypes.Time(mtime))
        finally:
            win32file.CloseHandle(h)
    except ImportError:
        import ctypes
        from ctypes import wintypes

        def _ft(ts: float) -> wintypes.FILETIME:
            v = int(ts * 10_000_000) + 116444736000000000
            ft = wintypes.FILETIME()
            ft.dwLowDateTime = v & 0xFFFFFFFF
            ft.dwHighDateTime = v >> 32
            return ft

        k32 = ctypes.windll.kernel32
        CreateTime = _ft(ctime)
        AccessTime = _ft(atime)
        WriteTime = _ft(mtime)
        h = k32.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x80, None)
        if h != -1:
            try:
                k32.SetFileTime(h, ctypes.byref(CreateTime),
                                ctypes.byref(AccessTime), ctypes.byref(WriteTime))
            finally:
                k32.CloseHandle(h)


def parse_time_from_name(name: str) -> tuple[float, float, float] | None:
    """从文件名提取时间（2024-01-02、20240102、2024-01-02 15-04-05 等常见格式）。"""
    m = re.search(r"(20\d{2})[-_.年/](\d{1,2})[-_.月/](\d{1,2})日?"
                  r"(?:[-_ T](\d{1,2})[-_:T时](\d{1,2})(?:[-_:分](\d{1,2}))?)?", name)
    if not m:
        m = re.search(r"(20\d{2})(\d{2})(\d{2})[-_ ]?(\d{2})?(\d{2})?(\d{2})?", name)
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 12)
        mi = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
    else:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 12)
        mi = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
    try:
        dt = datetime(y, mo, d, hh, mi, ss)
        ts = dt.timestamp()
        return (ts, ts, ts)
    except ValueError:
        return None


class FileTimePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.files: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 文件选择
        top = QHBoxLayout()
        add_files = QPushButton("📄 添加文件")
        add_files.clicked.connect(lambda: self._pick_files(False))
        add_dir = QPushButton("📂 添加文件夹")
        add_dir.clicked.connect(lambda: self._pick_files(True))
        clear = QPushButton("🧹 清空")
        clear.clicked.connect(self._clear)
        self.cnt = QLabel("0 个文件（可直接拖入）")
        self.cnt.setStyleSheet("color:#888;")
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
        self.time_entry = QLineEdit()
        self.time_entry.setPlaceholderText("2024-01-02 15:04:05（也可只到分钟）")
        self.time_entry.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        pr.addWidget(self.time_entry)
        self.offset_entry = QLineEdit()
        self.offset_entry.setPlaceholderText("偏移量，如 -3h30m / +2d / +90m / -30s")
        self.offset_entry.setText("+0h")
        self.offset_entry.setEnabled(False)
        pr.addWidget(self.offset_entry)
        preview = QPushButton("👁 预览")
        preview.clicked.connect(self._preview)
        apply_btn = QPushButton("✔ 应用")
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
        for i, f in enumerate(self.files[:1000]):
            self.table.insertRow(i)
            st = os.stat(f)
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(f)))
            self.table.setItem(i, 1, QTableWidgetItem(datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
            self.table.setItem(i, 2, QTableWidgetItem(datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")))
            self.table.setItem(i, 5, QTableWidgetItem(""))

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
        self.time_entry.setEnabled(self.rb_set.isChecked())
        self.offset_entry.setEnabled(self.rb_offset.isChecked())

    def _compute(self, f: str) -> tuple[float, float, float] | str:
        """返回 (atime, mtime, ctime) 或错误文本。"""
        st = os.stat(f)
        if self.rb_set.isChecked():
            txt = self.time_entry.text().strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
                try:
                    ts = datetime.strptime(txt, fmt).timestamp()
                    return (ts, ts, ts)
                except ValueError:
                    continue
            return "时间格式错误"
        if self.rb_offset.isChecked():
            txt = self.offset_entry.text().strip().lower()
            m = re.fullmatch(r"([+-])(\d+(?:\.\d+)?)([smhd])", txt)
            if not m:
                return "偏移格式错误（如 -3h30m 不支持混合，请用 -210m）"
            sign = 1 if m.group(1) == "+" else -1
            unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(3)]
            delta = sign * float(m.group(2)) * unit
            return (st.st_atime + delta, st.st_mtime + delta, (st.st_ctime + delta) if os.name == "nt" else None)
        # 按文件名
        got = parse_time_from_name(os.path.basename(f))
        if not got:
            return "文件名无时间"
        return got

    def _preview(self):
        for i, f in enumerate(self.files):
            r = self._compute(f)
            if i >= self.table.rowCount():
                break
            if isinstance(r, str):
                self.table.setItem(i, 3, QTableWidgetItem(r))
                self.table.setItem(i, 4, QTableWidgetItem(""))
                self.table.setItem(i, 5, QTableWidgetItem("✗"))
            else:
                fmt = datetime.fromtimestamp(r[1]).strftime("%Y-%m-%d %H:%M:%S")
                self.table.setItem(i, 3, QTableWidgetItem(fmt))
                self.table.setItem(i, 4, QTableWidgetItem(datetime.fromtimestamp(r[2]).strftime("%Y-%m-%d %H:%M:%S") if r[2] else "（不变）"))
                self.table.setItem(i, 5, QTableWidgetItem("待应用"))
        ok = sum(1 for f in self.files if not isinstance(self._compute(f), str))
        self.status.setText(f"预览：{ok}/{len(self.files)} 可应用")

    def _apply(self):
        if not self.files:
            QMessageBox.information(self, "提示", "请先添加文件")
            return
        files = list(self.files)
        results = []

        def job():
            ok = 0
            for i, f in enumerate(files):
                r = self._compute(f)
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
                    self.table.setItem(i, 5, QTableWidgetItem("✗"))
            for i, msg in results:
                if i < self.table.rowCount() and not msg:
                    self.table.setItem(i, 5, QTableWidgetItem("✔"))
            self.status.setText(f"完成 {ok}/{len(files)}" + (f"，失败 {len(fails)}" if fails else ""))
            self.app.toast(f"时间修改完成 {ok}/{len(files)}", "success" if ok == len(files) else "warning")

        run_bg(job, done)
