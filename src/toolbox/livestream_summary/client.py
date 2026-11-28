# -*- coding: utf-8 -*-
"""支持 OpenAI Chat Completions、Responses 与 Anthropic Messages 的远程客户端。

只负责一次对话请求与错误翻译；不记录、不打印 API Key。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable
from urllib.parse import urlparse

import requests

from .config import (
    API_FORMAT_ANTHROPIC_MESSAGES, API_FORMAT_CHAT_COMPLETIONS,
    API_FORMAT_RESPONSES, API_FORMATS,
)

CHAT_TIMEOUT = 600
CONNECT_TIMEOUT = 15
MODEL_CONNECT_TIMEOUT = 5
MODEL_READ_TIMEOUT = 10
MAX_MODEL_RESPONSE_BYTES = 1024 * 1024
MAX_MODEL_PAGES = 5
MAX_MODELS = 500

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


def _endpoint(base_url: str, api_format: str | None = None) -> tuple[str, str]:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ChatError("还没有填写模型接口地址")
    try:
        parsed = urlparse(base)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ChatError("模型接口地址必须是有效的 HTTP 或 HTTPS 地址") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ChatError("模型接口地址必须是有效的 HTTP 或 HTTPS 地址")
    selected = str(api_format or "").strip().lower()
    if not selected:
        # Keep direct ChatClient callers compatible with saved full endpoint URLs.
        normalized = base.lower()
        if normalized.endswith("/responses"):
            selected = API_FORMAT_RESPONSES
        elif normalized.endswith("/chat/completions"):
            selected = API_FORMAT_CHAT_COMPLETIONS
        elif normalized.endswith("/v1/messages"):
            selected = API_FORMAT_ANTHROPIC_MESSAGES
        else:
            selected = API_FORMAT_CHAT_COMPLETIONS
    elif selected not in API_FORMATS:
        raise ChatError("不支持的模型接口格式")
    # Strip only the operation path; preserve any version/prefix path (for
    # example /v1) so switching a full endpoint between formats stays stable.
    for suffix in ("/chat/completions", "/responses", "/messages"):
        if base.lower().endswith(suffix):
            base = base[:-len(suffix)].rstrip("/")
            break
    if selected == API_FORMAT_ANTHROPIC_MESSAGES:
        suffix = "/messages" if base.lower().endswith("/v1") else "/v1/messages"
        return base + suffix, selected
    suffix = ("/responses" if selected == API_FORMAT_RESPONSES
              else "/chat/completions")
    return base + suffix, selected


def _models_endpoint(base_url: str, api_format: str | None = None) -> tuple[str, str]:
    endpoint, api_mode = _endpoint(base_url, api_format)
    operation = {
        API_FORMAT_CHAT_COMPLETIONS: "/chat/completions",
        API_FORMAT_RESPONSES: "/responses",
        API_FORMAT_ANTHROPIC_MESSAGES: "/messages",
    }[api_mode]
    root = endpoint[:-len(operation)].rstrip("/")
    parsed = urlparse(root)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ChatError("接口地址不能包含用户名、密码、查询参数或片段")
    if api_mode == API_FORMAT_ANTHROPIC_MESSAGES:
        suffix = "/models" if parsed.path.lower().endswith("/v1") else "/v1/models"
    else:
        suffix = "/models"
    return root + suffix, api_mode


def _read_model_page(response, cancel=None) -> dict:
    if response.status_code in (401, 403):
        raise ChatError(
            f"模型列表请求未获授权（HTTP {response.status_code}），"
            "请检查接口地址、API Key 与接口格式")
    if response.status_code == 429:
        raise ChatError("模型列表请求过于频繁（HTTP 429），请稍后再试")
    if 300 <= response.status_code < 400:
        raise ChatError("模型列表接口发生重定向，请检查接口地址")
    if response.status_code >= 400:
        raise ChatError(
            f"模型列表请求失败（HTTP {response.status_code}），"
            "请确认服务商支持模型列表接口")
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if content_type and "json" not in content_type:
        raise ChatError("模型列表接口返回的不是 JSON")
    try:
        declared_size = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size > MAX_MODEL_RESPONSE_BYTES:
        raise ChatError("模型列表响应超过 1 MB 限制")
    chunks = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            _check_cancel(cancel)
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_MODEL_RESPONSE_BYTES:
                raise ChatError("模型列表响应超过 1 MB 限制")
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChatError("模型列表接口返回的 JSON 无法解析") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ChatError("模型列表响应格式无法解析（缺少 data 列表）")
    return payload


def list_models(base_url: str, api_key: str,
                api_format: str | None = None, cancel=None) -> dict:
    """Fetch a bounded list of model IDs without persisting or logging credentials."""
    url, api_mode = _models_endpoint(base_url, api_format)
    api_key = str(api_key or "").strip()
    headers = {"Accept": "application/json"}
    if api_mode == API_FORMAT_ANTHROPIC_MESSAGES:
        if api_key:
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = "Bearer local"
        headers["anthropic-version"] = "2026-10-21"
        params = {"limit": 100}
    else:
        headers["Authorization"] = f"Bearer {api_key or 'local'}"
        params = {}
    models = []
    seen = set()
    cursor = ""
    truncated = False
    for page_number in range(MAX_MODEL_PAGES):
        _check_cancel(cancel)
        page_params = dict(params)
        if cursor:
            page_params["after_id" if api_mode == API_FORMAT_ANTHROPIC_MESSAGES
                        else "after"] = cursor
        response = None
        try:
            response = requests.get(
                url, headers=headers, params=page_params or None,
                timeout=(MODEL_CONNECT_TIMEOUT, MODEL_READ_TIMEOUT),
                allow_redirects=False, stream=True)
            payload = _read_model_page(response, cancel=cancel)
        except requests.RequestException as exc:
            # requests exceptions can include a complete URL; never expose it.
            raise ChatError("获取模型列表时网络请求失败") from exc
        finally:
            if response is not None:
                response.close()
        page = payload["data"]
        for item_index, item in enumerate(page):
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                models.append(model_id)
                if len(models) >= MAX_MODELS:
                    truncated = (bool(payload.get("has_more"))
                                 or item_index < len(page) - 1)
                    break
        if truncated or not payload.get("has_more"):
            break
        next_cursor = str(
            payload.get("last_id") or
            (page[-1].get("id") if page and isinstance(page[-1], dict) else "") or
            "").strip()
        if not next_cursor or next_cursor == cursor:
            truncated = True
            break
        cursor = next_cursor
        if page_number == MAX_MODEL_PAGES - 1:
            truncated = True
    return {"models": models, "truncated": truncated}


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError("已停止")


def _clean_text(content) -> str:
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
    if not text:
        raise ChatError("模型返回了空内容")
    return text


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
    return _clean_text(content)


def _responses_content(payload: dict) -> str:
    """Extract assistant text from the raw Responses REST representation."""
    if not isinstance(payload, dict):
        raise ChatError("模型返回格式无法解析（缺少 output）")
    convenience_text = payload.get("output_text")
    if isinstance(convenience_text, str) and convenience_text.strip():
        return _clean_text(convenience_text)

    output = payload.get("output")
    if not isinstance(output, list):
        raise ChatError("模型返回格式无法解析（缺少 output 列表）")
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role") not in (None, "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "output_text":
                    text_parts.append(str(part.get("text") or ""))
                elif part_type == "refusal":
                    text_parts.append(str(part.get("refusal") or ""))
    return _clean_text("".join(text_parts))


def _anthropic_content(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ChatError("模型返回格式无法解析（缺少 content）")
    content = payload.get("content")
    if not isinstance(content, list):
        raise ChatError("模型返回格式无法解析（缺少 content 列表）")
    text_parts = [str(block.get("text") or "")
                  for block in content
                  if isinstance(block, dict) and block.get("type") == "text"]
    return _clean_text("".join(text_parts))


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = CHAT_TIMEOUT, allow_empty_key: bool = False,
                 api_format: str | None = None):
        self.base_url = base_url
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.timeout = timeout
        self.allow_empty_key = allow_empty_key
        self.api_format = api_format
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
        url, api_mode = _endpoint(self.base_url, self.api_format)
        if api_mode == "responses":
            body = {
                "model": self.model,
                "instructions": system,
                "input": user,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
        elif api_mode == API_FORMAT_ANTHROPIC_MESSAGES:
            body = {
                "model": self.model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": max_tokens,
            }
        else:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        headers = {"Content-Type": "application/json"}
        if api_mode == API_FORMAT_ANTHROPIC_MESSAGES:
            if self.api_key:
                headers["x-api-key"] = self.api_key
            else:
                headers["Authorization"] = "Bearer local"
            headers["anthropic-version"] = "2026-10-21"
        else:
            headers["Authorization"] = f"Bearer {self.api_key or 'local'}"
        last_error = ""
        for attempt in range(3):
            _check_cancel(cancel)
            try:
                self.requests_made += 1
                response = requests.post(url, json=body, headers=headers,
                                         timeout=(CONNECT_TIMEOUT, self.timeout),
                                         allow_redirects=False)
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
            if 300 <= response.status_code < 400:
                raise ChatError("模型接口发生重定向，请检查接口地址后重试")
            if response.status_code >= 400:
                detail = (response.text or "").strip()[:300]
                if self.api_key:
                    detail = detail.replace(self.api_key, "[已隐藏]")
                raise ChatError(f"模型接口返回 HTTP {response.status_code}：{detail}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ChatError("模型接口返回的不是 JSON") from exc
            if api_mode == API_FORMAT_RESPONSES:
                return _responses_content(payload)
            if api_mode == API_FORMAT_ANTHROPIC_MESSAGES:
                return _anthropic_content(payload)
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
