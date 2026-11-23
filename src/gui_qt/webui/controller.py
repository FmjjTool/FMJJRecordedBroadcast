"""WebUI 镜像生命周期：启动 / 停止本地服务，令牌随每次启动重新生成。"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication

from gui_qt.webui.bridge import WebUiBridge
from gui_qt.webui.server import WebUiServer
from toolbox.config_manager import load_convert_config

_log = logging.getLogger(__name__)


class WebUiController:
    def __init__(self):
        self._server: WebUiServer | None = None
        self._bridge: WebUiBridge | None = None
        self._quit_hooked = False

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        return self._server.url if self._server is not None else ""

    @property
    def port(self) -> int:
        return self._server.port if self._server is not None else 0

    def start(self, window, port: int) -> str:
        if self._server is not None:
            return self._server.url
        bridge = WebUiBridge(window)
        server = WebUiServer(bridge, port=int(port))
        try:
            server.start()
        except Exception:
            bridge.close()
            bridge.deleteLater()
            raise
        self._server = server
        self._bridge = bridge
        app = QApplication.instance()
        if app is not None and not self._quit_hooked:
            app.aboutToQuit.connect(self.stop)
            self._quit_hooked = True
        return server.url

    def stop(self) -> None:
        server = self._server
        bridge = self._bridge
        self._server = None
        self._bridge = None
        if server is not None:
            server.stop()
        if bridge is not None:
            try:
                bridge.close()
                bridge.deleteLater()
            except Exception:
                pass


_controller = WebUiController()


def controller() -> WebUiController:
    return _controller


def maybe_autostart(window) -> str:
    """按配置在启动后拉起本地服务；失败只记日志，不影响主程序。"""
    cfg = load_convert_config()
    if not cfg.get("webui_auto_start"):
        return ""
    try:
        port = int(cfg.get("webui_port", 8765) or 8765)
    except (TypeError, ValueError):
        port = 8765
    try:
        return _controller.start(window, port)
    except Exception:
        _log.exception("WebUI 自动启动失败")
        return ""
