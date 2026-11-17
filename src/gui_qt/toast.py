"""右下角 toast 提示（带颜色和动效）。

toast 是宿主窗口的**子控件**（不是独立顶层窗口）：固定贴宿主窗口右下角，
随窗口移动/缩放，最小化时一起隐藏——始终属于窗口本身，不会飘出窗口外。
"""

from PySide6.QtCore import QEvent, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
)


class Toast(QFrame):
    COLORS = {
        "info": "#2a5a8c",
        "success": "#2e7d32",
        "warning": "#b8860b",
        "error": "#c62828",
    }

    def __init__(self, parent, text, kind="info", duration=2600):
        super().__init__(parent)
        if parent is None:
            # 无宿主窗口（极少）：退化为独立提示窗，贴屏幕右下角
            self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self._host = parent
        self.setStyleSheet(
            f"QFrame {{ background: {self.COLORS.get(kind, '#2a5a8c')}; border-radius: 8px; }}"
            f"QLabel {{ color: white; background: transparent; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        text_lbl = QLabel(text)
        text_font = QFont()
        text_font.setPointSize(12)
        text_lbl.setFont(text_font)
        text_lbl.setWordWrap(True)
        text_lbl.setMaximumWidth(420)
        layout.addWidget(text_lbl)
        self.adjustSize()
        self._place()
        if parent is not None:
            parent.installEventFilter(self)   # 宿主缩放时重新贴角
        # 显示 + 淡入（子控件不能用 windowOpacity，用透明度效果）
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0)
        self.setGraphicsEffect(self._fx)
        self.show()
        self.raise_()
        self._anim_in = QPropertyAnimation(self._fx, b"opacity")
        self._anim_in.setDuration(180)
        self._anim_in.setStartValue(0)
        self._anim_in.setEndValue(1)
        self._anim_in.start()
        # 定时淡出 + 关闭
        QTimer.singleShot(duration, self._fade_out)

    def _place(self):
        """贴宿主窗口内部右下角（窗口坐标系，随窗口移动/缩放）。"""
        host = self._host
        if host is not None:
            x = host.width() - self.width() - 24
            y = host.height() - self.height() - 60
            self.move(max(0, x), max(0, y))
            return
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if geo is not None:
            self.move(geo.right() - self.width() - 24,
                      geo.bottom() - self.height() - 60)

    def eventFilter(self, obj, ev):
        if obj is self._host and ev.type() == QEvent.Resize and self.isVisible():
            self._place()
        return super().eventFilter(obj, ev)

    def _fade_out(self):
        self._anim_out = QPropertyAnimation(self._fx, b"opacity")
        self._anim_out.setDuration(220)
        self._anim_out.setStartValue(1)
        self._anim_out.setEndValue(0)
        self._anim_out.finished.connect(self.deleteLater)
        self._anim_out.start()


def show_toast(parent, text, kind="info", duration=2600):
    """显示一个右下角 toast 提示（贴在宿主窗口内，跟随窗口移动/缩放）。"""
    t = Toast(parent, text, kind=kind, duration=duration)
    return t
