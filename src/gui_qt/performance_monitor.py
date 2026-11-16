"""主程序持有采样服务，诊断页面可以正常卸载。"""

import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QWidget, QTableView

from gui_qt import common
from toolbox.performance import PerformanceRecorder, child_process_memory, process_memory


class PerformanceMonitor(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.recorder = PerformanceRecorder()
        self._children = []
        self._child_busy = False
        self._lag = 0.0
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(100)
        self._heartbeat.timeout.connect(self._beat)
        self._sample_timer = QTimer(self)
        self._sample_timer.setInterval(1000)
        self._sample_timer.timeout.connect(self.sample)
        self._child_timer = QTimer(self)
        self._child_timer.setInterval(5000)
        self._child_timer.timeout.connect(self._read_children)

    def start(self, trace=False):
        if self.recorder.active:
            return
        self.recorder.start(trace)
        common.PERFORMANCE_RECORDER = self.recorder
        self._last_beat = time.perf_counter()
        self._lag = 0.0
        self._children = []
        for timer in (self._heartbeat, self._sample_timer, self._child_timer):
            timer.start()
        self.sample()
        self._read_children()

    def stop(self):
        for timer in (self._heartbeat, self._sample_timer, self._child_timer):
            timer.stop()
        if common.PERFORMANCE_RECORDER is self.recorder:
            common.PERFORMANCE_RECORDER = None
        self.recorder.stop()

    def _beat(self):
        now = time.perf_counter()
        delay = max(0, (now - self._last_beat) * 1000 - 100)
        self._last_beat = now
        self._lag = max(self._lag, delay)
        if delay > 100:
            self.recorder.event("ui_delay", self.app.current_page or "startup", lag_ms=round(delay, 1))

    def sample(self):
        if not self.recorder.active:
            return
        try:
            self._sample()
        except Exception:
            self.recorder.event("monitor_error", "采样失败")

    def _sample(self):
        pages = [(key, page, "当前页面" if key == self.app.current_page else
                  ("隐藏缓存" if page._ever_shown else "已预载"))
                 for key, page in self.app.pages.items()]
        pages += [(key, page, "后台保留") for key, page in self.app.page_lifecycle.retained.items()]
        inventory = []
        for key, page, state in pages:
            grids = page.findChildren(QTableView)
            inventory.append({"page": key, "state": state,
                              "widgets": len(page.findChildren(QWidget)) + 1,
                              "table_slots": sum(table.model().rowCount() * table.model().columnCount()
                                                 for table in grids if table.model() is not None)})
        self.recorder.sample(self.app.current_page, self._lag, inventory, list(self._children))
        self._lag = 0.0

    def _read_children(self):
        if self._child_busy or not self.recorder.active:
            return
        self._child_busy = True
        session = self.recorder.session

        def done(result, err):
            self._child_busy = False
            if session == self.recorder.session and self.recorder.active:
                self._children = result or []

        common.run_bg(child_process_memory, done)

    def page_started(self):
        if not self.recorder.active:
            return None
        return self.recorder.session, time.perf_counter(), process_memory()

    def page_finished(self, token, key, failed=False, kind="page_switch"):
        if token is None or token[0] != self.recorder.session or not self.recorder.active:
            return
        after = process_memory()
        metrics = {"duration_ms": round((time.perf_counter() - token[1]) * 1000, 2), "failed": failed}
        for field in ("rss", "private"):
            before = token[2][field]
            metrics[f"{field}_delta"] = after[field] - before if after[field] is not None and before is not None else None
        self.recorder.event(kind, key, **metrics)
