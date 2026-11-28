"""One client lifetime per summary/test, shared by desktop and WebUI."""

from __future__ import annotations

from contextlib import contextmanager

from . import config
from .client import ChatClient, is_local_base_url, list_models


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
                     allow_empty_key=allow_empty,
                     api_format=settings.api_format)


def fetch_models(base_url: str, api_format: str, api_key: str = "", cancel=None) -> dict:
    """Fetch provider model IDs using a typed or already-saved key, without saving it."""
    base_url = str(base_url or "").strip()
    selected_format = str(api_format or "").strip()
    key = str(api_key or "").strip()
    if not key:
        key = config.load_api_key()
    if not key and not is_local_base_url(base_url):
        raise ValueError("请先填写或保存模型接口的 API Key")
    return list_models(base_url, key, selected_format, cancel=cancel)
