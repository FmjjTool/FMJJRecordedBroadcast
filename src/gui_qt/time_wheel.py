"""数字滚动时间选择：24 小时制，时 / 分两列，无限循环（23 → 00、59 → 00）。

用自绘实现「滚轮式」选择：上下滚动鼠标滚轮、拖动或按键盘上下键改变数值，
中间一行高亮，两侧数字渐隐。颜色全部取主题语义色，切主题后自动跟随。

用在「任务」页的计划设置对话框里替代原来的 HH:MM 文本框（约定 10：定时设置
只在任务页，本控件只是输入方式的变化）。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QRectF, Qt, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from gui_qt import theme

_ROW_H = 28
_VISIBLE_ROWS = 5
_COLUMN_W = 54


class _WheelColumn(QWidget):
    """单列数字滚轮：值为整数，显示时对 modulo 取模，因此可以无限滚动。"""

    valueChanged = Signal(int)

    def __init__(self, modulo: int, value: int = 0, parent=None):
        super().__init__(parent)
        self._modulo = max(1, int(modulo))
        self._target = int(value)
        self._pos = float(value)
        self._drag_y = None
        self._drag_accum = 0.0
        self._dragged = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setFixedSize(_COLUMN_W, _ROW_H * _VISIBLE_ROWS)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.SizeVerCursor)

    # ---------- 取值 ----------
    def value(self) -> int:
        return self._target % self._modulo

    def set_value(self, value: int) -> None:
        self._anim.stop()
        self._target = int(value)
        self._pos = float(self._target)
        self.update()

    def _step(self, steps: int) -> None:
        if steps == 0:
            return
        self._target += steps
        self._animate_to(self._target)
        self.valueChanged.emit(self.value())

    def _animate_to(self, target: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(float(self._pos))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._pos = float(value)
        self.update()

    # ---------- 交互 ----------
    def wheelEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        steps = int(delta / 120) or (1 if delta > 0 else -1)
        self._step(-steps)      # 滚轮向上 = 选更小的值（像列表向上翻）
        event.accept()

    def keyPressEvent(self, event):
        if not self.isEnabled():
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Left):
            self._step(-1)
        elif event.key() in (Qt.Key_Down, Qt.Key_Right):
            self._step(1)
        elif event.key() == Qt.Key_PageUp:
            self._step(-5)
        elif event.key() == Qt.Key_PageDown:
            self._step(5)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if not self.isEnabled() or event.button() != Qt.LeftButton:
            return
        self.setFocus(Qt.MouseFocusReason)
        self._drag_y = event.position().y()
        self._drag_accum = 0.0
        self._dragged = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_y is None:
            return
        y = event.position().y()
        self._drag_accum += self._drag_y - y
        self._drag_y = y
        steps = int(self._drag_accum / _ROW_H)
        if steps:
            self._drag_accum -= steps * _ROW_H
            self._target += steps
            self._pos = float(self._target)
            self._dragged = True
            self.update()
            self.valueChanged.emit(self.value())
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_y is None:
            return
        self._drag_y = None
        if not getattr(self, "_dragged", False):
            # 单击某一行 = 直接跳到该行
            offset = round((event.position().y() - self.height() / 2) / _ROW_H)
            if offset:
                self._step(offset)
        self._dragged = False
        event.accept()

    # ---------- 绘制 ----------
    def visible_rows(self) -> list[tuple[str, float, bool]]:
        """当前可见行：[(数字文本, 相对中心的偏移行数, 是否选中行)]。

        取模显示，所以 _pos 跨过 0 / 23（或 0 / 59）时首尾自然衔接，可无限滚动。
        """
        center = round(self._pos)
        start = int(self._pos) - 3
        rows = []
        for index in range(start, start + 7):
            distance = index - self._pos
            if abs(distance) > 2.4:
                continue
            rows.append((f"{index % self._modulo:02d}", distance, index == center))
        return rows

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        enabled = self.isEnabled()
        text_color = QColor(theme.color("text"))
        muted_color = QColor(theme.color("muted"))

        if enabled:
            band = QColor(theme.color("accent_soft"))
            painter.setPen(Qt.NoPen)
            painter.setBrush(band)
            painter.drawRoundedRect(
                QRectF(2, (self.height() - _ROW_H) / 2 + 2,
                       self.width() - 4, _ROW_H - 4), 8, 8)

        for text, distance, is_center in self.visible_rows():
            y = self.height() / 2 + distance * _ROW_H
            alpha = 255 if abs(distance) < 0.5 else max(60, int(255 - abs(distance) * 70))
            color = QColor(text_color if is_center else muted_color)
            color.setAlpha(alpha if enabled else min(alpha, 110))
            font = QFont(self.font())
            font.setPointSizeF(13.5 if is_center else 9.5)
            font.setBold(is_center)
            painter.setFont(font)
            painter.setPen(QPen(color))
            painter.drawText(
                QRectF(0, y - _ROW_H / 2, self.width(), _ROW_H),
                Qt.AlignCenter, text)

        if self.hasFocus() and enabled:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(theme.color("accent")), 1))
            painter.drawRoundedRect(QRectF(1, 1, self.width() - 2,
                                           self.height() - 2), 8, 8)


class TimeWheel(QWidget):
    """时 / 分两列组成的 24 小时时间滚轮，取值格式 `HH:MM`。"""

    timeChanged = Signal(str)

    def __init__(self, text: str = "09:00", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.hour = _WheelColumn(24, 9, self)
        self.minute = _WheelColumn(60, 0, self)
        self._colon = QLabel(":", self)
        self._colon.setProperty("role", "caption")
        self._colon.setAlignment(Qt.AlignCenter)
        self._colon.setFixedWidth(6)
        layout.addWidget(self.hour)
        layout.addWidget(self._colon)
        layout.addWidget(self.minute)
        layout.addStretch(1)
        self._style_colon()
        self.hour.valueChanged.connect(self._on_changed)
        self.minute.valueChanged.connect(self._on_changed)
        self.set_time(text)

    def _style_colon(self):
        self._colon.setStyleSheet(
            "color: palette(placeholder-text); font-size: 18px; font-weight: bold;")

    def _on_changed(self, _value):
        self.timeChanged.emit(self.time())

    # ---------- 取值 ----------
    def time(self) -> str:
        return f"{self.hour.value():02d}:{self.minute.value():02d}"

    def set_time(self, text: str) -> None:
        value = str(text or "").strip()
        hour, minute = 9, 0
        if ":" in value:
            left, _, right = value.partition(":")
            try:
                hour = max(0, min(23, int(left)))
            except ValueError:
                hour = 9
            try:
                minute = max(0, min(59, int(right)))
            except ValueError:
                minute = 0
        self.hour.set_value(hour)
        self.minute.set_value(minute)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt 命名
        super().setEnabled(enabled)
        self.hour.setEnabled(enabled)
        self.minute.setEnabled(enabled)
        self._colon.setEnabled(enabled)

    def on_theme_changed(self):
        self.hour.update()
        self.minute.update()
        self._style_colon()
        self.update()