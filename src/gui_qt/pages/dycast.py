"""抖音弹幕姬页（PySide6 版）：启动 dycast-main 前端。"""

import os
import subprocess
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.config_manager import get_app_root

from gui_qt.pages.base import BasePage


class DycastPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.dev_process = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📺 抖音弹幕姬")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("实时弹幕 / 礼物 / 进场 / 点赞 / 关注（前端 dycast-main）",
                                styleSheet="color: #888;"))

        bar = QHBoxLayout()
        self.start_btn = QPushButton("▶️ 启动弹幕姬")
        self.start_btn.setStyleSheet("background: #2e7d32; color: white; padding: 8px 20px; border-radius: 6px; font-size: 14px;")
        self.start_btn.clicked.connect(self._launch)
        bar.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        bar.addWidget(self.stop_btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _log(self, msg):
        self.log.append(msg)

    def _launch(self):
        root = get_app_root()
        dycast = os.path.join(root, "dycast-main")
        if not os.path.isdir(dycast):
            self.app.toast("未找到 dycast-main 前端", "error")
            return
        nm = os.path.join(dycast, "node_modules")
        if not os.path.isdir(nm):
            self._log("⚠️ dycast-main 依赖未安装，正在尝试自动安装（请确保已装 pnpm/node）...")
            QTimer.singleShot(50, lambda: self._install_and_run(dycast))
            return
        self._run_dev(dycast)

    def _install_and_run(self, dycast):
        try:
            self._log("⏳ pnpm install ...")
            r = subprocess.run(["pnpm", "install"], cwd=dycast, capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                self._log(f"❌ pnpm install 失败: {r.stderr[-300:]}")
                self.app.toast("dycast 依赖安装失败，请手动在 dycast-main 跑 pnpm install", "error")
                return
        except Exception as e:
            self._log(f"❌ {e}")
            self.app.toast("请先装 pnpm 和 node，再点启动", "error")
            return
        self._log("✅ 依赖安装完成")
        self._run_dev(dycast)

    def _run_dev(self, dycast):
        try:
            self._log("⏳ 启动 vite dev server ...")
            self.dev_process = subprocess.Popen(
                ["pnpm", "dev"], cwd=dycast,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._log(f"❌ 启动失败: {e}")
            self.app.toast("启动失败，请检查 pnpm/node 环境", "error")
            return
        # 等 vite 起来（一般 3-8 秒）
        QTimer.singleShot(5000, self._open_webview)

    def _open_webview(self):
        try:
            import webview
            self._log("🌐 打开弹幕姬窗口（localhost:5173）...")
            webview.create_window("抖音弹幕姬", "http://localhost:5173", width=1100, height=720)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            webview.start(gui="edgechromium")
        except Exception as e:
            self._log(f"❌ 打开失败: {e}")
            self.app.toast(f"弹幕姬窗口打开失败: {e}", "error")

    def _stop(self):
        if self.dev_process:
            try:
                self.dev_process.terminate()
            except Exception:
                pass
            self.dev_process = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log("⏹ 已停止")
