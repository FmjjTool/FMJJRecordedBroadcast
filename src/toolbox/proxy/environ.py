"""程序级代理环境变量：只影响本进程与它启动的子进程。

启用时写 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY，并把回环地址加进 NO_PROXY
（否则内核对自身的接口调用也会被代理）。禁用或退出时原样恢复，不动系统
代理设置，也不改其他程序的网络。
"""

from __future__ import annotations

import os
import threading

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
_NO_PROXY = "NO_PROXY"
_ALL_KEYS = _PROXY_KEYS + (_NO_PROXY,)
_LOOPBACK = "127.0.0.1,localhost,::1"

_lock = threading.RLock()
_saved: dict[str, str | None] | None = None
_port = 0


def _merge_no_proxy(existing: str) -> str:
    items = [part.strip() for part in str(existing or "").split(",") if part.strip()]
    for host in _LOOPBACK.split(","):
        if host not in items:
            items.append(host)
    return ",".join(items)


def enable(port: int) -> str:
    """开启程序代理，返回代理地址。重复调用是幂等的。"""
    global _saved, _port
    port = int(port)
    with _lock:
        if _saved is None:
            _saved = {key: os.environ.get(key) for key in _ALL_KEYS}
        url = f"http://127.0.0.1:{port}"
        for key in _PROXY_KEYS:
            os.environ[key] = url
        os.environ[_NO_PROXY] = _merge_no_proxy(_saved.get(_NO_PROXY) or "")
        _port = port
        return url


def disable() -> None:
    """恢复启用前的环境变量；没启用过则什么也不做。"""
    global _saved, _port
    with _lock:
        if _saved is None:
            return
        for key, value in _saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _saved = None
        _port = 0


def url() -> str:
    """当前程序代理地址；未启用返回空串。"""
    with _lock:
        return f"http://127.0.0.1:{_port}" if _port else ""


def is_enabled() -> bool:
    return bool(url())


def describe() -> str:
    current = url()
    if not current:
        return "未启用（本程序请求直连）"
    return f"已启用：{current}（仅本程序请求）"