"""One client lifetime per summary/test, shared by desktop and WebUI."""

from __future__ import annotations

from contextlib import contextmanager

from . import config
from .client import ChatClient, is_local_base_url


@contextmanager
def client_session(settings: config.Settings, cancel=None, progress=None):
    """Open the explicitly selected backend; never install or silently fall back."""
    if settings.mode == "local":
        from .optional_runtime import LocalSession

        with LocalSession(settings.local, cancel=cancel, progress=progress) as client:
            yield client
        return
    if settings.mode != "ai":
        raise ValueError("本地规则模式不需要连接模型")
    allow_empty = is_local_base_url(settings.base_url)
    api_key = config.load_api_key()
    if not api_key and not allow_empty:
        raise ValueError("请先保存模型接口的 API Key")
    if not settings.model:
        raise ValueError("请填写模型名称")
    yield ChatClient(settings.base_url, api_key, settings.model,
                     allow_empty_key=allow_empty)
