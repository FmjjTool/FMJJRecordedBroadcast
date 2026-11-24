"""性能诊断入口：用户手动开启，切走后由主程序继续采样。"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from gui_qt import table_sort
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.performance_monitor import PerformanceMonitor


def _mb(value):
    return "—" if value is None else f"{value / 1048576:.1f}"


class _MemoryChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.setMaximumHeight(160)
        self.samples = []

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        series = (("rss", "工作集", "#3b82f6"), ("private", "私有提交", "#e69c24"),
                  ("python_bytes", "Python 分配", "#37b56b"))
        for i, (_, name, color) in enumerate(series):
            painter.setPen(QColor(color))
            painter.drawText(12 + i * 135, 18, name)
        peak = max((sample.get(field) or 0 for sample in self.samples for field, _, _ in series), default=1)
        peak = max(peak, 1)
        painter.setPen(self.palette().text().color())
        painter.drawText(12, 40, f"{_mb(peak)} MB")
        painter.drawText(12, self.height() - 8, "0 MB")
        if len(self.samples) < 2:
            painter.drawText(110, 60, "开始检测后显示最近 10 分钟的采样趋势")
            return
        start, end = self.samples[0]["seconds"], self.samples[-1]["seconds"]
        for field, _, color in series:
            painter.setPen(QPen(QColor(color), 1.8))
            previous = None
            for sample in self.samples:
                value = sample.get(field)
                if value is None:
                    previous = None
                    continue
                point = QPointF(90 + (self.width() - 105) * (sample["seconds"] - start) / max(end - start, .001),
                                self.height() - 20 - (self.height() - 60) * value / peak)
                if previous is not None:
                    painter.drawLine(previous, point)
                previous = point


class PerformancePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        if app.performance_monitor is None:
            app.performance_monitor = PerformanceMonitor(app)
        self.monitor = app.performance_monitor
        self._visible = False
        self._snapshot_busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        bar = QHBoxLayout()
        self.trace_cb = QCheckBox("追踪 Python 分配（较慢）")
        self.trace_cb.setToolTip("仅追踪开启后的 Python 分配；Qt、解码图片及子进程内存不在其覆盖范围内")
        bar.addWidget(self.trace_cb)
        self.start_btn = QPushButton("开始检测")
        self.start_btn.clicked.connect(self._start)
        bar.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止检测")
        self.stop_btn.clicked.connect(self._stop)
        bar.addWidget(self.stop_btn)
        self.mark_btn = QPushButton("记录标记")
        self.mark_btn.clicked.connect(self._mark)
        bar.addWidget(self.mark_btn)
        self.snapshot_btn = QPushButton("Python 快照")
        self.snapshot_btn.clicked.connect(self._snapshot)
        bar.addWidget(self.snapshot_btn)
        self.export_btn = QPushButton("导出报告")
        self.export_btn.clicked.connect(self._export)
        bar.addWidget(self.export_btn)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        top = QFrame()
        top.setProperty("variant", "toolbar")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(12, 10, 12, 10)
        tv.setSpacing(6)
        tv.addLayout(bar)
        tv.addWidget(self.status)
        layout.addWidget(top)
        self.chart = _MemoryChart()
        chart_card = QFrame()
        chart_card.setProperty("variant", "card")
        cv = QVBoxLayout(chart_card)
        cv.setContentsMargins(10, 10, 10, 10)
        cv.addWidget(self.chart)
        layout.addWidget(chart_card)
        self.tabs = QTabWidget()
        self.sample_table = self._table(("秒", "页面", "工作集 MB", "私有提交 MB", "Python MB",
                                         "CPU % / 单核", "界面延迟 ms", "线程 / 任务"))
        self.page_table = self._table(("页面", "状态", "控件数", "表格格数（非内存）"))
        self.event_table = self._table(("秒", "阶段 / 标记", "页面 / 函数", "耗时 ms", "工作集变化 MB", "状态"))
        self.memory_table = self._table(("分配位置", "当前 KB", "块数"))
        self.growth_table = self._table(("相对上次快照的新增位置", "新增 KB", "新增块数"))
        self.child_table = self._table(("子进程", "PID", "工作集 MB", "私有提交 MB"))
        for table, label in ((self.sample_table, "采样"), (self.page_table, "页面控件"),
                             (self.event_table, "页面 / 任务耗时"), (self.memory_table, "Python 分配"),
                             (self.growth_table, "分配增长"), (self.child_table, "子进程")):
            self.tabs.addTab(table, label)
        layout.addWidget(self.tabs, 1)
        note = QLabel("工作集与私有提交是不同指标，不能相加；Python 分配不等于进程总内存。"
                      "页面切换增量可能包含同期后台任务。CPU 100% 表示用满一个逻辑核。"
                      "每秒自动采样，最多保留 3600 次采样和 2000 个事件。")
        note.setProperty("role", "caption")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._render)
        self.tabs.currentChanged.connect(self._render)

    def _table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 110 if len(headers) > 4 else 300)
        # 表头排序：数据只在单元格里（无控件列），每秒刷新后由 _fill 重新套用
        table_sort.install(table, skip_columns=())
        return table

    def _fill(self, table, rows):
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    table.setItem(r, c, item)
        finally:
            table.setUpdatesEnabled(True)
        sorter = getattr(table, "_table_sorter", None)
        if sorter is not None:
            sorter.reapply()

    def on_show(self):
        self._visible = True
        self._timer.start()
        self._render()

    def on_hide(self):
        self._visible = False
        self._timer.stop()

    def _start(self):
        try:
            self.monitor.start(self.trace_cb.isChecked())
        except Exception as exc:  # noqa: BLE001 - 诊断失败不影响功能页面
            self.app.toast(str(exc), "error")
            return
        self._render()

    def _stop(self):
        self.monitor.sample()
        self.monitor.stop()
        self._render()

    def _mark(self):
        from toolbox.performance import process_memory
        self.monitor.recorder.event("mark", self.app.current_page, **process_memory())
        self.monitor.sample()
        self._render()

    def _snapshot(self):
        if self._snapshot_busy:
            return
        self._snapshot_busy = True
        self.snapshot_btn.setEnabled(False)
        session = self.monitor.recorder.session

        def done(result, err):
            self._snapshot_busy = False
            if err:
                self.app.toast(f"快照失败：{err}"[:160], "error")
            elif result is not None and session == self.monitor.recorder.session:
                self.monitor.sample()
            if self._visible:
                self._render()

        run_bg(lambda: self.monitor.recorder.memory_snapshot(session), done, owner=self)

    def _render(self, *_args):
        if not self._visible:
            return
        recorder = self.monitor.recorder
        report = recorder.report()
        samples = report["samples"]
        latest = samples[-1] if samples else {}
        self.start_btn.setEnabled(not recorder.active)
        self.trace_cb.setEnabled(not recorder.active)
        if recorder.active:
            self.trace_cb.blockSignals(True)
            self.trace_cb.setChecked(recorder._trace_owned)
            self.trace_cb.blockSignals(False)
        self.stop_btn.setEnabled(recorder.active)
        self.mark_btn.setEnabled(recorder.active)
        self.snapshot_btn.setEnabled(recorder.active and recorder._trace_owned and not self._snapshot_busy)
        self.export_btn.setEnabled(bool(samples))
        if recorder.active:
            self.status.setText(f"检测中 · {len(samples)} 次采样 · 工作集 {_mb(latest.get('rss'))} MB"
                                f" · 私有提交 {_mb(latest.get('private'))} MB · 可切走继续检测")
        else:
            self.status.setText(f"已停止 · 保留 {len(samples)} 次采样，可导出；开始检测将建立新记录" if samples
                                else "未开始；默认不运行采样或 Python 分配追踪")
        self.chart.samples = [sample for sample in samples if sample["seconds"] >= latest.get("seconds", 0) - 600]
        self.chart.update()
        current = self.tabs.currentWidget()
        if current is self.sample_table:
            self._fill(current, [(s["seconds"], s["page"], _mb(s["rss"]), _mb(s["private"]),
                                  _mb(s["python_bytes"]), s["cpu_one_core_percent"], s["ui_lag_ms"],
                                  f"{s['threads']} / {s['tasks']}") for s in reversed(samples[-120:])])
        elif current is self.page_table:
            self._fill(current, [(p["page"], p["state"], p["widgets"], p["table_slots"])
                                  for p in latest.get("pages", [])])
        elif current is self.event_table:
            names = {"page_switch": "切换页面", "page_preload": "页面预载", "worker": "后台执行", "callback": "主线程回调",
                     "ui_delay": "界面延迟", "mark": "手动标记"}
            self._fill(current, [(e["seconds"], names.get(e["kind"], e["kind"]), e["label"],
                                  e.get("duration_ms", e.get("lag_ms", "—")), _mb(e.get("rss_delta")),
                                  "失败" if e.get("failed") else "—") for e in reversed(report["events"][-200:])])
        elif current in (self.memory_table, self.growth_table):
            snapshots = report["memory_snapshots"]
            entries = snapshots[-1]["top" if current is self.memory_table else "growth"] if snapshots else []
            self._fill(current, [(e["location"], f"{e['bytes'] / 1024:.1f}", e["blocks"]) for e in entries])
        elif current is self.child_table:
            self._fill(current, [(p["name"], p["pid"], _mb(p["rss"]), _mb(p["private"]))
                                  for p in latest.get("children", [])])

    def _export(self):
        from toolbox.config_manager import get_export_subdir
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        target = get_export_subdir("性能诊断") / f"性能诊断_{stamp}.json"
        path, _ = QFileDialog.getSaveFileName(self, "导出性能诊断", str(target), "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(self.monitor.recorder.report(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.app.toast("性能诊断报告已导出", "success")
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"导出失败：{exc}"[:160], "error")
