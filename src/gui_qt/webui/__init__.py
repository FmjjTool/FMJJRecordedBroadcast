"""WebUI 镜像：把当前页面控件树导出为浏览器可渲染的 JSON，并回传交互。

只镜像页面内部控件；独立窗口、原生文件对话框与自绘图表不在页面树内。
"""

from gui_qt.webui.bridge import WebUiBridge
from gui_qt.webui.server import WebUiServer

__all__ = ["WebUiBridge", "WebUiServer"]
