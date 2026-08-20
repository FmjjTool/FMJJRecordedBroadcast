"""PySide6 页面基类。所有页面继承此类：on_show/on_hide 在页面切换时调用。"""

from PySide6.QtWidgets import QWidget


class BasePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app  # MainWindow 实例（提供 work_dir / toast 等）

    def on_show(self):
        """每次切换到该页时调用（刷新数据用）。默认无操作。"""
        pass

    def on_hide(self):
        """每次从该页切走时调用（停后台任务用）。默认无操作。"""
        pass
