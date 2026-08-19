"""PySide6 页面基类。所有页面继承此类：持有 app 引用，on_show 在每次切换到该页时调用。"""

from PySide6.QtWidgets import QWidget


class BasePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app  # MainWindow 实例（提供 work_dir / toast 等）

    def on_show(self):
        """每次切换到该页时调用（刷新数据用）。默认无操作。"""
        pass
