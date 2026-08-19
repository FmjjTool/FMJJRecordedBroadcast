"""抖音弹幕姬页（PySide6 版）：启动 dycast 前端。"""

import os

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.config_manager import get_app_root

from gui_qt.pages.base import BasePage


class DycastPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📺 抖音弹幕姬")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("实时弹幕 / 礼物 / 进场 / 点赞 / 关注（前端 dycast-main）", styleSheet="color: #888;"))

        btn = QPushButton("▶️ 启动弹幕姬")
        btn.setStyleSheet("background: #2e7d32; color: white; padding: 8px 20px; border-radius: 6px; font-size: 14px;")
        btn.clicked.connect(self._launch)
        layout.addWidget(btn)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _launch(self):
        try:
            import webview
            root = get_app_root()
            dycast = os.path.join(root, "dycast-main")
            index = os.path.join(dycast, "index.html")
            if not os.path.isfile(index):
                self.log.append("⚠️ 未找到 dycast-main 前端，请先启动一次原版弹幕姬自动安装依赖")
                return
            self.log.append("⏳ 正在启动弹幕姬...")
            webview.create_window("抖音弹幕姬", index, width=1100, height=720)
            webview.start(gui="edgechromium")
        except ImportError:
            self.log.append("⚠️ 未安装 pywebview，无法内嵌弹幕姬\n   请运行: pip install pywebview")
        except Exception as e:
            self.log.append(f"❌ 启动失败: {e}")
