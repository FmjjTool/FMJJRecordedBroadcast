"""PySide6 页面基类。所有页面继承此类：on_show/on_hide 在页面切换时调用。"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget


class BasePage(QWidget):
    SESSION_FIELDS = ("files", "input_files")

    def __init__(self, app):
        super().__init__()
        self.setProperty("pageSurface", True)
        self.app = app  # MainWindow 实例（提供 work_dir / toast 等）
        self._disposed = False

    def has_active_tasks(self):
        """业务进程仍依赖该宿主时，禁止销毁它。"""
        return False

    def has_critical_services(self):
        return False

    def release_view_resources(self):
        """切走释放预览等资源，不取消后台业务。"""
        pass

    def restore_session_data(self):
        """显式业务数据恢复后重建列表。"""
        pass

    def prepare_session_state(self):
        pass

    def session_restored(self):
        pass

    def saved_child_states(self):
        return {}

    def restore_child_state(self, path, state):
        pass

    def on_unload(self):
        """真正回收时停止页面计时器。"""
        for timer in self.findChildren(QTimer):
            timer.stop()

    def on_show(self):
        """每次切换到该页时调用（刷新数据用）。默认无操作。"""
        pass

    def on_hide(self):
        """每次从该页切走时调用（停后台任务用）。默认无操作。"""
        pass

    def shutdown(self):
        """软件退出时调用（全停：子进程树 / 录制 / 抓取等）。默认等同 on_hide。"""
        self.on_hide()
