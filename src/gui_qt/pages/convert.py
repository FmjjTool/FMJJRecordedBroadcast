"""旧视频转换页面类的兼容入口；界面与功能统一由视频操作页提供。"""

from gui_qt.pages.editor import EditorPage


class ConvertPage(EditorPage):
    def __init__(self, app):
        super().__init__(app)
        self.open_convert_tab()
