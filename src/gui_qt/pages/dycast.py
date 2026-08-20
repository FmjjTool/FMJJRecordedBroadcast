"""抖音弹幕姬入口页（PySide6 版，对应原版 gui/dycast_page.py）。

- 定位 dycast-main 目录（项目内优先，回退上级目录）
- 环境检查（依赖是否已安装）
- 启动弹幕姬（start.bat，自动检测 pnpm/npm 并 install）
- 内嵌预览（本地 HTTP 服务器托管 dist + WebView，回退浏览器）
- 打开项目目录 + 使用说明
"""

import functools
import http.server
import os
import socket
import subprocess
import sys
import threading

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)

from toolbox.config_manager import get_app_root

from gui_qt.pages.base import BasePage


def _find_dycast_dir():
    """定位 dycast-main 目录（优先主项目内，回退到外部 all/dycast-main）。"""
    root = str(get_app_root())            # .../PySide6
    inner = os.path.join(root, "dycast-main")
    if os.path.isdir(inner):
        return inner
    parent = os.path.dirname(root)        # .../all
    candidate = os.path.join(parent, "dycast-main")
    if os.path.isdir(candidate):
        return candidate
    # 再回退一层：.../all/平台信息获取/dycast-main
    sibling = os.path.join(parent, "平台信息获取", "dycast-main")
    if os.path.isdir(sibling):
        return sibling
    return None


class DycastPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.dycast_dir = _find_dycast_dir()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📺 抖音弹幕姬")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("抖音直播间弹幕 / 礼物 / 点赞实时抓取、录制与转发（Vue 前端应用）",
                                styleSheet="color: #888;"))

        info = QLabel()
        info.setStyleSheet(
            "background: #1a1d27; border-radius: 6px; padding: 8px; color: #c6cbd6;")
        info.setWordWrap(True)
        info.setText(
            "<b>弹幕姬前端</b><br>"
            + ("<span style='color:#64b5f6;'>内置 dycast-main（已集成到本软件）</span>"
               if self.dycast_dir else "<span style='color:#64b5f6;'>（未找到 dycast-main 目录）</span>"))
        layout.addWidget(info)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; padding-left: 4px;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.launch_btn = QPushButton("🚀 启动弹幕姬")
        self.launch_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.launch_btn.clicked.connect(self._launch)
        btn_row.addWidget(self.launch_btn)
        self.embed_btn = QPushButton("🖥 内嵌预览")
        self.embed_btn.setStyleSheet("background: #2a5a8c; color: white; padding: 6px 16px; border-radius: 4px;")
        self.embed_btn.clicked.connect(self._embedded_preview)
        btn_row.addWidget(self.embed_btn)
        self.open_btn = QPushButton("📂 打开项目目录")
        self.open_btn.clicked.connect(self._open_dir)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        tip = QTextBrowser()
        tip.setOpenExternalLinks(False)
        tip.setStyleSheet("color: #888; font-size: 12px; background: #1a1d27; border-radius: 6px; padding: 10px;")
        tip.setPlainText(
            "使用说明\n\n"
            "1. 依赖缺失时启动会自动安装（start.bat 自动检测 pnpm/npm 并 install）\n"
            "2. 点击「启动弹幕姬」后，会弹出命令行窗口并自动打开浏览器\n"
            "3. 在浏览器里输入直播间房间号，即可实时抓取弹幕 / 礼物 / 点赞\n"
            "4. 支持直播录制、回放、打赏榜、多房间监控等功能\n\n"
            "依赖：Node.js（建议 v18+）与 pnpm\n"
            "房间号 / Cookie 等配置在 dycast-main/dycast.config.json 中修改")
        layout.addWidget(tip, 1)

        self._check_env()

    def _check_env(self):
        if not self.dycast_dir:
            self.status_label.setText("⚠️ 未找到 dycast-main，请确认目录位置")
            self.status_label.setStyleSheet("color: #ff9800;")
            self.launch_btn.setEnabled(False)
            return
        node_modules = os.path.isdir(os.path.join(self.dycast_dir, "node_modules"))
        if node_modules:
            self.status_label.setText("✅ 依赖已安装")
            self.status_label.setStyleSheet("color: #4caf50;")
        else:
            self.status_label.setText("⚠️ 尚未安装依赖，首次启动会自动安装（较慢）")
            self.status_label.setStyleSheet("color: #ff9800;")

    def _launch(self):
        if not self.dycast_dir:
            self.app.toast("未找到 dycast-main 目录", "error")
            return
        bat = os.path.join(self.dycast_dir, "start.bat")
        if not os.path.isfile(bat):
            self.app.toast(f"未找到启动脚本: {bat}", "error")
            return
        try:
            if sys.platform == "win32":
                os.startfile(bat)  # 弹出命令行窗口运行
            else:
                subprocess.Popen(["bash", bat], cwd=self.dycast_dir)
        except Exception as e:
            self.app.toast(f"启动失败: {e}", "error")
            return
        self.app.toast("弹幕姬启动中，稍后会自动打开浏览器窗口", "success")

    def _open_dir(self):
        if self.dycast_dir and os.path.isdir(self.dycast_dir):
            os.startfile(self.dycast_dir)

    def _embedded_preview(self):
        """内嵌预览：起本地 HTTP 服务器托管 dist，再用系统 WebView 加载，实现真正接入。"""
        if not self.dycast_dir:
            self.app.toast("未找到 dycast-main 目录", "error")
            return
        dist = os.path.join(self.dycast_dir, "dist")
        index = os.path.join(dist, "index.html")
        if not os.path.isfile(index):
            self.app.toast("未找到构建产物 dist/index.html，请先 build", "error")
            return
        self.app.toast("正在启动弹幕姬…", "info")

        def run():
            try:
                # 起一个本地静态服务器托管 dist（index.html 资源是绝对路径，必须走 http）
                handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=dist)
                with socket.socket() as s:
                    s.bind(("127.0.0.1", 0))
                    port = s.getsockname()[1]
                httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                url = f"http://127.0.0.1:{port}/index.html"
                try:
                    import webview
                    webview.create_window("抖音弹幕姬", url, width=1100, height=720)
                    webview.start(gui="edgechromium")
                except Exception:
                    # pywebview 不可用（无 Edge WebView2 运行时等），回退到默认浏览器
                    import webbrowser
                    webbrowser.open(url)
            except Exception as e:
                from gui_qt.common import post_to_main
                post_to_main(self.app.toast, f"内嵌预览失败: {e}", "error")

        threading.Thread(target=run, daemon=True).start()
