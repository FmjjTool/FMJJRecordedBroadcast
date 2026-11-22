"""程序内代理：内置 mihomo 内核 + 用户自备提供商。

对外只暴露 enable / disable / bootstrap / shutdown 与少量只读查询；
提供商内容、端口、密钥都落在 config/proxy/。

生效范围只是本程序：启用时设置进程级代理解释环境变量，退出时恢复；
不改 Windows 系统代理，也不启用 TUN。其他模块要判断是否需要显式代理时，
读 program_proxy_url() 即可（requests / httpx / git 默认会读环境变量）。
"""

from __future__ import annotations

import logging
import threading

from . import environ, install, provider, settings
from .core import ProxyError, core
from .install import download_core
from .provider import ProviderError

__all__ = [
    "ProxyError", "ProviderError", "core", "environ", "install", "provider",
    "settings", "bootstrap", "enable", "disable", "shutdown",
    "program_proxy_url", "download_core",
]

_log = logging.getLogger(__name__)


def program_proxy_url() -> str:
    """本程序当前使用的代理地址；未启用返回空串。"""
    return environ.url()


def enable() -> dict:
    """启动内核并让本程序请求走它；失败抛 ProxyError/ProviderError。"""
    cfg = settings.load()
    status = core.start()
    url = environ.enable(cfg["mixed_port"])
    settings.update(enabled=True)
    return {**status, "proxy_url": url}


def disable() -> dict:
    """关闭程序代理并停止内核（幂等）。"""
    environ.disable()
    core.stop()
    settings.update(enabled=False)
    return core.status()


def bootstrap() -> None:
    """启动时若上次是启用状态，在后台恢复内核与代理，不阻塞界面。"""
    try:
        cfg = settings.load()
    except Exception as exc:  # 配置损坏不应阻止主程序启动
        _log.warning("代理设置读取失败：%s", exc)
        return
    if not cfg.get("enabled"):
        return

    def work():
        try:
            enable()
            _log.info("程序代理已恢复：%s", environ.url())
        except Exception as exc:
            _log.warning("程序代理恢复失败：%s", exc)

    threading.Thread(target=work, name="proxy-bootstrap", daemon=True).start()


def shutdown() -> None:
    """退出收尾：停内核、恢复环境变量。"""
    try:
        environ.disable()
        core.stop()
    except Exception as exc:
        _log.warning("程序代理收尾失败：%s", exc)