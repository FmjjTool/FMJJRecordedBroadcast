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
import threading

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)

from toolbox.config_manager import get_app_root

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage
from gui_qt.process_tree import assign_job, create_kill_job, terminate_job


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
        # 进程树管理：链接进程树（Job Object 父关子关）时的启动实例
        self._proc = None
        self._job = None

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
        self.stop_btn = QPushButton("⏹ 停止弹幕姬")
        self.stop_btn.setStyleSheet("background: #c62828; color: white; padding: 6px 16px; border-radius: 4px;")
        self.stop_btn.clicked.connect(self._stop_linked)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        self.embed_btn = QPushButton("🖥 内嵌预览")
        self.embed_btn.setStyleSheet("background: #2a5a8c; color: white; padding: 6px 16px; border-radius: 4px;")
        self.embed_btn.clicked.connect(self._embedded_preview)
        btn_row.addWidget(self.embed_btn)
        self.open_btn = QPushButton("📂 打开项目目录")
        self.open_btn.clicked.connect(self._open_dir)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 进程树链接：勾选后弹幕姬进程（cmd/node/vite）纳入 Job Object，
        # 弹幕姬退出或本软件退出时整棵进程树一并结束（父关子关）。
        self.link_tree_var = QCheckBox(
            "链接进程树（弹幕姬与本软件退出时，一并结束全部子进程；同一时间仅允许一个实例）")
        self.link_tree_var.setChecked(True)
        layout.addWidget(self.link_tree_var)

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
        linked = self.link_tree_var.isChecked()
        if linked and self._proc is not None and self._proc.poll() is None:
            # 单实例：进程树已存在则不再启动第二个
            self.app.toast("弹幕姬已在运行（已链接进程树，同一时间仅一个实例）", "warning")
            return
        try:
            if linked:
                # Job Object 进程树：cmd → node/vite 全部纳入，父关子关
                job = create_kill_job()
                proc = subprocess.Popen(
                    ["cmd", "/c", bat], cwd=self.dycast_dir,
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
                if job and assign_job(job, proc):
                    self._job, self._proc = job, proc
                else:
                    # Job 绑定失败：退化为普通跟踪（关闭时 taskkill /T 兜底）
                    if job:
                        terminate_job(job)
                    self._job, self._proc = None, proc
                self.stop_btn.setEnabled(True)
                self.launch_btn.setEnabled(False)
                threading.Thread(target=self._watch_proc, daemon=True).start()
            else:
                os.startfile(bat)  # 独立运行（不纳入进程树，软件退出不影响）
        except Exception as e:
            self.app.toast(f"启动失败: {e}", "error")
            return
        if linked:
            self.app.toast("弹幕姬启动中（已链接进程树：本软件退出时一并结束）", "success")
        else:
            self.app.toast("弹幕姬启动中，稍后会自动打开浏览器窗口", "success")

    def _watch_proc(self):
        """后台监视：宿主 cmd 退出 → 清理进程树残留（父关子关）。"""
        proc = self._proc
        if proc is None:
            return
        proc.wait()
        if self._proc is not proc:
            return  # 已被停止按钮处理
        self._proc = None
        post_to_main(self._on_linked_exit)

    def _on_linked_exit(self):
        """弹幕姬宿主进程退出（用户关窗口等）→ 终止 Job 清残留子进程。"""
        self._cleanup_linked()
        self.app.toast("弹幕姬已退出（进程树已全部结束）", "info")

    def _stop_linked(self):
        """手动停止：终止整棵进程树。"""
        if self._proc is None and self._job is None:
            return
        self._cleanup_linked()
        self.app.toast("弹幕姬进程树已终止", "success")

    def _cleanup_linked(self):
        if self._job is not None:
            terminate_job(self._job)
            self._job = None
        elif self._proc is not None:
            # 无 Job（绑定失败兜底）：taskkill /T 杀整树
            try:
                subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            except Exception:
                pass
        self._proc = None
        self.stop_btn.setEnabled(False)
        self.launch_btn.setEnabled(bool(self.dycast_dir))

    def shutdown(self):
        """软件退出：链接进程树时一并结束弹幕姬全部子进程（父关子关）。"""
        self._cleanup_linked()

    def on_show(self):
        # 切回页面时同步进程树状态（宿主可能已被用户手动关闭）
        if self._proc is not None and self._proc.poll() is not None:
            self._cleanup_linked()
        linked_running = self._proc is not None
        self.stop_btn.setEnabled(linked_running)
        self.launch_btn.setEnabled(bool(self.dycast_dir) and not linked_running)

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
                post_to_main(self.app.toast, f"内嵌预览失败: {e}", "error")

        threading.Thread(target=run, daemon=True).start()
