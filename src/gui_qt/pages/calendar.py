"""日历浏览页（PySide6 版）：三级视图 年 → 月 → 日。"""

import os
from calendar import monthrange
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from toolbox.rename_engine import scan_date_folders

from gui_qt.pages.base import BasePage


class DayCell(QFrame):
    """日期格：内容从顶部开始往下排（日期在上、文件夹信息在下）。"""

    clicked = Signal()

    def __init__(self, day, file_count=None, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor if file_count is not None
                       else Qt.ArrowCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(2)
        v.setAlignment(Qt.AlignTop)
        num = QLabel(str(day))
        num.setStyleSheet(
            "font-size: 14px; font-weight: bold; background: transparent; border: none;")
        v.addWidget(num)
        if file_count is not None:
            info = QLabel(f"📁 {file_count}")
            info.setStyleSheet("font-size: 11px; background: transparent; border: none;")
            v.addWidget(info)
            v.addStretch(1)
            num.setStyleSheet(
                "font-size: 14px; font-weight: bold; background: transparent; "
                "border: none; color: #ffffff;")
            info.setStyleSheet(
                "font-size: 11px; background: transparent; border: none; color: #ffffff;")
            self.setStyleSheet(
                "DayCell { background: #1a5c2a; border-radius: 4px; border: none; }"
                "DayCell:hover { background: #2a8c3a; }")
        else:
            v.addStretch(1)
            self.setStyleSheet(
                "DayCell { background: #e8ebf0; border-radius: 4px; border: none; }")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.cursor().shape() == Qt.PointingHandCursor:
            self.clicked.emit()
            return
        super().mousePressEvent(event)


class CalendarPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.date_map = {}
        self.current_level = "year"
        self.current_year = None
        self.current_month = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QPushButton("🔄 刷新", clicked=self._scan))
        self.breadcrumb = QLabel("年视图")
        self.breadcrumb.setStyleSheet("font-weight: bold; font-size: 14px;")
        top.addWidget(self.breadcrumb)
        top.addStretch(1)
        self.back_btn = QPushButton("⬅ 返回")
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setEnabled(False)
        top.addWidget(self.back_btn)
        layout.addLayout(top)

        self.grid_area = QScrollArea()
        self.grid_area.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid_area.setWidget(self.grid_widget)
        layout.addWidget(self.grid_area, 1)

        self._scan()

    def _clear_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _scan(self):
        """扫描日期文件夹"""
        self.date_map = {}
        if not os.path.isdir(self.app.work_dir):
            self._clear_grid()
            lbl = QLabel("（工作目录不存在，请先设置）")
            lbl.setStyleSheet("color: #ff9800;")
            self.grid.addWidget(lbl, 0, 0)
            return
        for name, _ in scan_date_folders(self.app.work_dir):
            try:
                dt = date(int(name[:4]), int(name[4:6]), int(name[6:8]))
                self.date_map[dt] = name
            except Exception:
                continue
        if not self.date_map:
            self._clear_grid()
            lbl = QLabel(f"（未找到日期文件夹）\n当前目录: {self.app.work_dir}")
            lbl.setStyleSheet("color: #ff9800;")
            self.grid.addWidget(lbl, 0, 0)
            self.breadcrumb.setText("📅 无数据")
            return
        self.current_level = "year"
        self.current_year = None
        self.current_month = None
        self._show_year_view()

    def _go_back(self):
        if self.current_level == "month":
            self.current_level = "year"
            self._show_year_view()
        elif self.current_level == "day":
            self.current_level = "month"
            self._show_month_view()

    def _show_year_view(self):
        self._clear_grid()
        self.breadcrumb.setText("📅 年视图 — 点击年份进入月份")
        self.back_btn.setEnabled(False)
        years = sorted({d.year for d in self.date_map}, reverse=True)
        if not years:
            lbl = QLabel("（无日期文件夹，请检查工作目录）")
            lbl.setStyleSheet("color: #888;")
            self.grid.addWidget(lbl, 0, 0)
            return
        for i, y in enumerate(years):
            days = sum(1 for d in self.date_map if d.year == y)
            months = len({d.month for d in self.date_map if d.year == y})
            btn = QPushButton(f"{y} 年\n📁 {days} 天 / {months} 个月")
            btn.setMinimumHeight(70)
            btn.setStyleSheet(
                "QPushButton { background: #d8e7f6; font-size: 15px; font-weight: bold; }"
                "QPushButton:hover { background: #2a5a8c; }")
            btn.clicked.connect(lambda checked, yr=y: self._open_year(yr))
            self.grid.addWidget(btn, i // 4, i % 4)

    def _open_year(self, year):
        self.current_year = year
        self.current_level = "month"
        self._show_month_view()

    def _show_month_view(self):
        self._clear_grid()
        y = self.current_year
        self.breadcrumb.setText(f"📅 {y} 年 — 点击月份进入日期")
        self.back_btn.setEnabled(True)
        names = ["一月", "二月", "三月", "四月", "五月", "六月",
                 "七月", "八月", "九月", "十月", "十一月", "十二月"]
        for m in range(1, 13):
            days = sum(1 for d in self.date_map if d.year == y and d.month == m)
            if days > 0:
                btn = QPushButton(f"{m}月\n{names[m - 1]}\n📁 {days} 天")
                btn.setStyleSheet(
                    "QPushButton { background: #d8e7f6; font-size: 14px; font-weight: bold; }"
                    "QPushButton:hover { background: #2a5a8c; }")
                btn.clicked.connect(lambda checked, mo=m: self._open_month(mo))
            else:
                btn = QPushButton(f"{m}月\n{names[m - 1]}\n无数据")
                btn.setStyleSheet("QPushButton { background: #e8ebf0; font-size: 14px; }")
                btn.setEnabled(False)
            btn.setMinimumHeight(70)
            self.grid.addWidget(btn, (m - 1) // 4, (m - 1) % 4)

    def _open_month(self, month):
        self.current_month = month
        self.current_level = "day"
        self._show_day_view()

    def _show_day_view(self):
        self._clear_grid()
        y, m = self.current_year, self.current_month
        self.breadcrumb.setText(f"📅 {y}年{m}月 — 双击日期打开文件夹")
        self.back_btn.setEnabled(True)
        for col, wd in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            self.grid.addWidget(QLabel(wd), 0, col)
        _, days_in_month = monthrange(y, m)
        first_weekday = date(y, m, 1).weekday()
        day = 1
        for week in range(6):
            for col in range(7):
                if week == 0 and col < first_weekday:
                    continue
                if day > days_in_month:
                    break
                d = date(y, m, day)
                has = d in self.date_map
                if has:
                    folder = self.date_map[d]
                    # 统计文件数
                    fpath = os.path.join(self.app.work_dir, folder)
                    file_count = 0
                    if os.path.isdir(fpath):
                        file_count = len([f for f in os.listdir(fpath)
                                          if os.path.isfile(os.path.join(fpath, f))])
                    cell = DayCell(day, file_count)
                    cell.setMinimumHeight(52)
                    cell.clicked.connect(
                        lambda fd=folder: os.startfile(os.path.join(self.app.work_dir, fd)))
                else:
                    cell = DayCell(day)
                    cell.setMinimumHeight(52)
                self.grid.addWidget(cell, week + 1, col)
                day += 1
