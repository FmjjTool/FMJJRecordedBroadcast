"""抖音风格实时弹幕窗口：透明底、彩色昵称、消息由底部向上排列。"""
import html
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QTextDocument
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QSlider


class ChatCanvas(QWidget):
    COLORS = ("#7dd3fc", "#c4b5fd", "#f9a8d4", "#86efac", "#fde68a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages = deque(maxlen=80)
        self.font_size = 20
        self.rows = 8
        self.background_alpha = 110
        self.setMinimumSize(300, 180)

    def add_message(self, name, text, message=None):
        message = message or {}
        if name not in ("弹幕", "表情弹幕", "礼物", "SC", "上舰") and not message.get("giftName"):
            return
        nick = str(message.get("nickname") or "观众")[:80]
        body = str(message.get("content") or text or "")[:600]
        if message.get("giftName"):
            body = f"送出 {message['giftName']} ×{message.get('giftCount') or 1}"
        if not body:
            return
        self.messages.append((time.monotonic(), nick, body))
        self.update()

    def expire(self):
        now = time.monotonic()
        changed = False
        while self.messages and now - self.messages[0][0] > 30:
            self.messages.popleft()
            changed = True
        if changed:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 20, 29, self.background_alpha))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)
        bottom = self.height() - 12
        for _, nick, body in reversed(list(self.messages)[-self.rows:]):
            color = self.COLORS[sum(map(ord, nick)) % len(self.COLORS)]
            document = QTextDocument()
            document.setDocumentMargin(0)
            document.setHtml(
                f'<div style="font-family:Microsoft YaHei;font-size:{self.font_size}px;'
                f'color:#ffffff"><b style="color:{color}">{html.escape(nick)}：</b>'
                f'{html.escape(body)}</div>')
            document.setTextWidth(max(30, self.width() - 24))
            height = document.size().height()
            bottom -= height
            if bottom < 0:
                break
            painter.save()
            painter.translate(12, bottom)
            document.drawContents(painter)
            painter.restore()
            bottom -= 10
        painter.end()


class DanmakuOverlay(QWidget):
    def __init__(self, title="实时弹幕", parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(title + " · 弹幕渲染")
        self.resize(440, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        controls = QWidget()
        controls.setStyleSheet("background:#20232d;color:#ffffff;border-radius:6px;")
        row = QHBoxLayout(controls)
        self.status = QLabel("等待弹幕")
        row.addWidget(self.status, 1)
        row.addWidget(QLabel("字号"))
        size = QSpinBox()
        size.setRange(12, 40)
        size.setValue(20)
        row.addWidget(size)
        row.addWidget(QLabel("条数"))
        rows = QSpinBox()
        rows.setRange(2, 20)
        rows.setValue(8)
        row.addWidget(rows)
        layout.addWidget(controls)
        self.canvas = ChatCanvas()
        layout.addWidget(self.canvas, 1)
        opacity = QSlider(Qt.Orientation.Horizontal)
        opacity.setRange(0, 255)
        opacity.setValue(110)
        opacity.setToolTip("弹幕背景透明度（0 为全透明）")
        layout.addWidget(opacity)
        def set_option(key, value):
            setattr(self.canvas, key, value)
            self.canvas.update()
        size.valueChanged.connect(lambda value: set_option("font_size", value))
        rows.valueChanged.connect(lambda value: set_option("rows", value))
        opacity.valueChanged.connect(lambda value: set_option("background_alpha", value))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.canvas.expire)

    def set_active(self, active):
        self.status.setText("● 接收中" if active else "○ 已停止")

    def add_message(self, name, text, message=None):
        if self.isVisible():
            self.canvas.add_message(name, text, message)

    def showEvent(self, event):
        self.canvas.expire()
        self.timer.start(500)
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
