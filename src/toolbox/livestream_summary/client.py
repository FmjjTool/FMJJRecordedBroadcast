# -*- coding: utf-8 -*-
"""OpenAI 兼容 /chat/completions 客户端。

只负责一次对话请求与错误翻译；不记录、不打印 API Key。
"""

from __future__ import annotations

import threading
import time
from typing import Callable
from urllib.parse import urlparse

import requests

CHAT_TIMEOUT = 600
CONNECT_TIMEOUT = 15

# 本机模型服务（Ollama / LM Studio / llama.cpp）通常不校验 Key
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_local_base_url(base_url: str) -> bool:
    """接口地址是否指向本机；用于允许不填 API Key 的本地模型。"""
    try:
        host = urlparse(str(base_url or "").strip()).hostname
    except ValueError:
        return False
    return bool(host) and host.lower() in LOCAL_HOSTS


class ChatError(RuntimeError):
    """模型接口错误（可直接显示给用户）。"""


class CancelledError(RuntimeError):
    """用户主动停止。"""


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ChatError("还没有填写模型接口地址")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError("已停止")


def _content(payload: dict) -> str:
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChatError("模型返回格式无法解析（缺少 choices[0].message）") from exc
    content = message.get("content") if isinstance(message, dict) else message
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content)
    text = str(content or "").strip()
    if not text:
        raise ChatError("模型返回了空内容")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = CHAT_TIMEOUT, allow_empty_key: bool = False):
        self.base_url = base_url
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.timeout = timeout
        self.allow_empty_key = allow_empty_key
        self.requests_made = 0

    def _validate(self) -> None:
        if not self.api_key and not self.allow_empty_key:
            raise ChatError("还没有保存 API Key")
        if not self.model:
            raise ChatError("还没有填写模型名称")

    def chat(self, system: str, user: str, *,
             temperature: float = 0.3, max_tokens: int = 8192,
             cancel: threading.Event | None = None,
             on_wait: Callable[[str], None] | None = None) -> str:
        self._validate()
        url = _endpoint(self.base_url)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key or 'local'}",
            "Content-Type": "application/json",
        }
        last_error = ""
        for attempt in range(3):
            _check_cancel(cancel)
            try:
                self.requests_made += 1
                response = requests.post(url, json=body, headers=headers,
                                         timeout=(CONNECT_TIMEOUT, self.timeout))
            except requests.RequestException as exc:
                last_error = f"网络请求失败：{exc}"
                if attempt < 2:
                    self._wait(2.0 * (attempt + 1), cancel, on_wait)
                    continue
                raise ChatError(last_error) from exc

            if response.status_code in (401, 403):
                raise ChatError(
                    f"API Key 无效或无权限（HTTP {response.status_code}），"
                    "请检查接口地址、Key 与模型名是否匹配")
            if response.status_code == 429:
                wait = self._retry_after(response)
                last_error = "请求被限流（HTTP 429）"
                if attempt < 2:
                    self._wait(wait, cancel, on_wait)
                    continue
                raise ChatError(last_error + "，请稍后再试")
            if response.status_code >= 500:
                last_error = f"模型服务暂时不可用（HTTP {response.status_code}）"
                if attempt < 2:
                    self._wait(2.0 * (attempt + 1), cancel, on_wait)
                    continue
                raise ChatError(last_error)
            if response.status_code >= 400:
                detail = (response.text or "").strip()[:300]
                raise ChatError(f"模型接口返回 HTTP {response.status_code}：{detail}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ChatError("模型接口返回的不是 JSON") from exc
            return _content(payload)
        raise ChatError(last_error or "模型请求失败")

    @staticmethod
    def _retry_after(response: requests.Response) -> float:
        try:
            return min(10.0, max(1.0, float(response.headers.get("Retry-After", "3"))))
        except (TypeError, ValueError):
            return 3.0

    @staticmethod
    def _wait(seconds: float, cancel: threading.Event | None,
              on_wait: Callable[[str], None] | None) -> None:
        if on_wait:
            on_wait(f"等待 {seconds:.0f} 秒后重试…")
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            _check_cancel(cancel)
            time.sleep(0.2)

    def test_connection(self, cancel: threading.Event | None = None) -> str:
        return self.chat("你是连通性测试助手。", "只回复 OK", temperature=0,
                         max_tokens=8, cancel=cancel)
