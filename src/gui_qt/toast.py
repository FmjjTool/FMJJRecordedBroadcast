"""右下角 toast 提示（带颜色和动效）。"""

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel


class Toast(QFrame):
    COLORS = {
        "info": "#2a5a8c",
        "success": "#2e7d32",
        "warning": "#b8860b",
        "error": "#c62828",
    }

    def __init__(self, parent, text, kind="info", duration=2600):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(
            f"QFrame {{ background: {self.COLORS.get(kind, '#2a5a8c')}; border-radius: 8px; }}"
            f"QLabel {{ color: white; background: transparent; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        icon_lbl = QLabel(self._icon(kind))
        icon_lbl.setFont(QFont(size=14))
        layout.addWidget(icon_lbl)
        text_lbl = QLabel(text)
        text_lbl.setFont(QFont(size=12))
        text_lbl.setWordWrap(True)
        text_lbl.setMaximumWidth(420)
        layout.addWidget(text_lbl)
        self.adjustSize()
        # 定位：父窗口右下角
        self._place(parent)
        # 显示 + 淡入
        self.setWindowOpacity(0)
        self.show()
        self._anim_in = QPropertyAnimation(self, b"windowOpacity")
        self._anim_in.setDuration(180)
        self._anim_in.setStartValue(0)
        self._anim_in.setEndValue(1)
        self._anim_in.start()
        # 定时淡出 + 关闭
        QTimer.singleShot(duration, self._fade_out)

    def _icon(self, kind):
        return {"info": "ℹ", "success": "✅", "warning": "⚠", "error": "❌"}.get(kind, "ℹ")

    def _place(self, parent):
        if parent:
            geo = parent.geometry()
            x = geo.x() + geo.width() - self.width() - 24
            y = geo.y() + geo.height() - self.height() - 60
            self.move(QPoint(max(0, x), max(0, y)))
        else:
            self.move(QPoint(800, 600))

    def _fade_out(self):
        self._anim_out = QPropertyAnimation(self, b"windowOpacity")
        self._anim_out.setDuration(220)
        self._anim_out.setStartValue(1)
        self._anim_out.setEndValue(0)
        self._anim_out.finished.connect(self.deleteLater)
        self._anim_out.start()


def show_toast(parent, text, kind="info", duration=2600):
    """显示一个右下角 toast 提示。"""
    t = Toast(parent, text, kind=kind, duration=duration)
    return t
