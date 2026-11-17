"""无边框窗口外壳：顶部栏自绘窗口按钮 + 四边/四角缩放热区。

隐藏系统标题栏后，最小化 / 最大化 / 关闭收进顶部栏右侧；拖动顶部栏空白处
移动窗口（startSystemMove 保留 Windows 贴边分屏），双击在最大化与还原间
切换；窗口四边与四角由透明热区触发系统缩放（startSystemResize）。

页面区、侧栏与状态栏的布局完全不变，这里只接管「窗口外壳」这一层。
"""

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

import sys

from gui_qt import theme
from gui_qt.navigation import line_icon

_CONTROL_WIDTH = 40
_CONTROL_HEIGHT = 30
_ICON_SIZE = 16

_CLOSE_HOVER = "#e81123"
_CLOSE_PRESSED = "#c42b1c"

# Windows 11 DWM 窗口圆角（DWMWA_WINDOW_CORNER_PREFERENCE）
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2


def apply_round_corners(window, radius=_DWMWCP_ROUND):
    """Win11 系统圆角：DWM 裁剪整窗（含全部子控件），最大化时自动取消。

    QSS 的 border-radius 只能裁自身绘制，顶部栏/侧栏/页面各自的矩形背景会在
    四角画出直角；交给 DWM 后不需要逐层配圆角，也不会影响布局与滚动区。
    Win10 或调用失败返回 False，窗口保持直角但不报错。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        value = ctypes.c_int(int(radius))
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(window.winId())),
            _DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0
    except (AttributeError, OSError, ValueError):
        return False


def _color(role):
    return theme.current_palette().color(role).name()


class WindowControlButton(QPushButton):
    """顶部栏窗口按钮：纯图标，悬停底色由 QSS 提供，图标随主题/悬停重绘。"""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._icon_name = {"min": "win_min", "max": "win_max", "close": "win_close"}[kind]
        self._hover = False
        self.setObjectName("windowControlClose" if kind == "close" else "windowControl")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(_CONTROL_WIDTH, _CONTROL_HEIGHT)
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self.setToolTip({"min": "最小化", "max": "最大化", "close": "关闭"}[kind])
        self.refresh_icon()

    def set_maximized(self, maximized):
        """最大化状态切换时同步图标与提示（还原 / 最大化）。"""
        if self._kind != "max":
            return
        self._icon_name = "win_restore" if maximized else "win_max"
        self.setToolTip("还原" if maximized else "最大化")
        self.refresh_icon()

    def refresh_icon(self):
        if self._hover:
            color = "#ffffff" if self._kind == "close" else _color(QPalette.Text)
        else:
            color = _color(QPalette.PlaceholderText)
        self.setIcon(line_icon(self._icon_name, color, _ICON_SIZE))

    def enterEvent(self, event):
        self._hover = True
        self.refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.refresh_icon()
        super().leaveEvent(event)


class WindowControlBar(QWidget):
    """最小化 / 最大化 / 关闭三连按钮。"""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.min_button = WindowControlButton("min", self)
        self.max_button = WindowControlButton("max", self)
        self.close_button = WindowControlButton("close", self)
        self.min_button.clicked.connect(lambda *_: self.minimize_requested.emit())
        self.max_button.clicked.connect(lambda *_: self.maximize_requested.emit())
        self.close_button.clicked.connect(lambda *_: self.close_requested.emit())
        for button in (self.min_button, self.max_button, self.close_button):
            row.addWidget(button)

    def set_maximized(self, maximized):
        self.max_button.set_maximized(maximized)

    def refresh_icons(self):
        for button in (self.min_button, self.max_button, self.close_button):
            button.refresh_icon()


class TitleBar(QFrame):
    """顶部栏：空白处拖动窗口，双击最大化 / 还原；按钮区域事件照常。"""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            toggle = getattr(self.window(), "toggle_max_restore", None)
            if callable(toggle):
                toggle()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


def _edge_cursor(edges):
    left = bool(edges & Qt.LeftEdge)
    right = bool(edges & Qt.RightEdge)
    top = bool(edges & Qt.TopEdge)
    bottom = bool(edges & Qt.BottomEdge)
    if (left and top) or (right and bottom):
        return Qt.SizeFDiagCursor
    if (right and top) or (left and bottom):
        return Qt.SizeBDiagCursor
    if left or right:
        return Qt.SizeHorCursor
    return Qt.SizeVerCursor


class EdgeResizeHandle(QWidget):
    """边缘透明热区：按下即交给系统做原生缩放，不自己算尺寸。"""

    def __init__(self, parent, edges):
        super().__init__(parent)
        self._edges = edges
        self.setCursor(_edge_cursor(edges))
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)
            event.accept()
            return
        super().mousePressEvent(event)


class WindowResizeHandles:
    """窗口四边 + 四角热区集合；最大化 / 全屏时整体隐藏。"""

    ORDER = (
        Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge,
        Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.TopEdge,
        Qt.LeftEdge | Qt.BottomEdge, Qt.RightEdge | Qt.BottomEdge,
    )

    def __init__(self, window, border=6, corner=14):
        self.window = window
        self.border = border
        self.corner = corner
        self._handles = [(EdgeResizeHandle(window, edges), edges) for edges in self.ORDER]
        self.reposition()
        self.set_enabled(True)

    def set_enabled(self, enabled):
        for handle, _edges in self._handles:
            handle.setVisible(bool(enabled))
        if enabled:
            self.reposition()

    def raise_all(self):
        """压到最上层：状态栏等后创建的兄弟控件会盖住边缘热区。"""
        for handle, _edges in self._handles:
            handle.raise_()
            handle.setVisible(not (self.window.isMaximized() or self.window.isFullScreen()))

    def reposition(self):
        w = max(self.window.width(), 1)
        h = max(self.window.height(), 1)
        b = self.border
        c = self.corner
        geometry = {
            Qt.LeftEdge: QRect(0, c, b, max(h - 2 * c, 0)),
            Qt.RightEdge: QRect(w - b, c, b, max(h - 2 * c, 0)),
            Qt.TopEdge: QRect(c, 0, max(w - 2 * c, 0), b),
            Qt.BottomEdge: QRect(c, h - b, max(w - 2 * c, 0), b),
            Qt.LeftEdge | Qt.TopEdge: QRect(0, 0, c, c),
            Qt.RightEdge | Qt.TopEdge: QRect(w - c, 0, c, c),
            Qt.LeftEdge | Qt.BottomEdge: QRect(0, h - c, c, c),
            Qt.RightEdge | Qt.BottomEdge: QRect(w - c, h - c, c, c),
        }
        for handle, edges in self._handles:
            handle.setGeometry(geometry[edges])


def install_resize_handles(window, border=6, corner=14):
    return WindowResizeHandles(window, border=border, corner=corner)
