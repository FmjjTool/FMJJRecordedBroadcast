"""首页显示后分步创建页面；每步让出事件循环，控件始终在主线程创建。"""

from collections import deque
import logging

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication


class PagePreloader(QObject):
    def __init__(self, app, keys):
        super().__init__(app)
        self.app = app
        self.queue = deque((key, None) for key in keys)
        self.total = len(self.queue)
        self.completed = 0
        self.errors = []
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)

    def start(self):
        if not self.closed and self.queue:
            self.timer.start(500)

    def defer_for_navigation(self):
        if self.timer.isActive():
            self.timer.start(500)

    def step(self):
        if self.closed:
            return
        if not self.queue:
            self._update_status()
            return
        # 对话框操作期间避免插入耗时构造。
        if QApplication.activeModalWidget() is not None:
            self.timer.start(200)
            return
        key, task = self.queue.popleft()
        monitor = self.app.performance_monitor
        token = None
        failed = False
        try:
            if monitor is not None:
                try:
                    token = monitor.page_started()
                except Exception:
                    pass
            if task is None:
                page = self.app._ensure_page(key)
                tasks = tuple(page.preload_tasks())
                self.queue.extend((key, callback) for callback in tasks)
                self.total += len(tasks)
            else:
                task()
        except Exception:
            failed = True
            self.errors.append(key)
            logging.getLogger(__name__).exception("页面预载失败: %s", key)
        finally:
            if monitor is not None:
                try:
                    monitor.page_finished(token, key, failed, kind="page_preload")
                except Exception:
                    pass
            self.completed += 1
        self._update_status()
        if not self.closed and self.queue:
            self.timer.start(100)

    def _update_status(self):
        label = self.app.preload_status
        if self.queue:
            label.setText(f"页面预载 {self.completed}/{self.total}")
        elif any(page.preload_is_pending() for page in self.app.pages.values()):
            label.setText("预载：正在准备本地数据…")
            if not self.closed:
                self.timer.start(100)
        else:
            label.setText("页面预载完成" if not self.errors else f"预载完成 · {len(self.errors)} 项失败")

    def stop(self):
        self.closed = True
        self.timer.stop()
        self.queue.clear()
