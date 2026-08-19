"""抖音弹幕实时数据页（PySide6 版，简化）：连接直播间 + 实时弹幕/礼物/进场。"""

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout,
)

from gui_qt.pages.base import BasePage


class TestPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.fetcher = None
        self.connected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🎬 抖音弹幕 · 实时数据接入")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("真实连接抖音直播间，实时抓取弹幕 / 礼物 / 进场 / 点赞 / 关注", styleSheet="color: #888;"))

        row = QHBoxLayout()
        self.room_entry = QLineEdit()
        self.room_entry.setPlaceholderText("直播间房间号")
        row.addWidget(self.room_entry, 1)
        self.connect_btn = QPushButton("▶ 连接")
        self.connect_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 16px; border-radius: 4px;")
        self.connect_btn.clicked.connect(self._connect)
        row.addWidget(self.connect_btn)
        self.disconnect_btn = QPushButton("■ 断开")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._disconnect)
        row.addWidget(self.disconnect_btn)
        layout.addLayout(row)

        self.status_label = QLabel("已断开")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        self.box = QTextEdit()
        self.box.setReadOnly(True)
        layout.addWidget(self.box, 1)

    def _append(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.box.append(f"{ts} {text}")
        sb = self.box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _connect(self):
        room = self.room_entry.text().strip()
        if not room:
            return
        from toolbox.douyin_live import DouyinLiveFetcher
        self.connected = True
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.status_label.setText(f"连接中 · 房间 {room}")
        self.status_label.setStyleSheet("color: #eab308;")
        self.fetcher = DouyinLiveFetcher(
            room, on_message=self._on_message, on_room_info=self._on_room_info, on_status=self._on_status)
        self.fetcher.start()

    def _disconnect(self):
        self.connected = False
        if self.fetcher:
            self.fetcher.stop()
            self.fetcher = None
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.status_label.setText("已断开")
        self.status_label.setStyleSheet("color: #888;")

    def _on_room_info(self, info):
        def apply():
            if info.get("anchor"):
                self._append(f"[房间] 主播 {info['anchor']} · 在线 {info.get('online', '?')}")
        QTimer.singleShot(0, apply)

    def _on_message(self, name, text):
        def apply():
            if name == "礼物":
                self._append(f"🎁 {text}")
            elif name == "进场":
                self._append(f"💚 {text}")
            elif name == "点赞":
                self._append(f"👍 {text}")
            elif name == "关注":
                self._append(f"⭐ {text}")
            else:
                self._append(text)
        QTimer.singleShot(0, apply)

    def _on_status(self, status, msg):
        def apply():
            if status == "connected":
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #4caf50;")
            elif status == "error":
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #ff5252;")
                self._disconnect()
            else:
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #eab308;")
        QTimer.singleShot(0, apply)
