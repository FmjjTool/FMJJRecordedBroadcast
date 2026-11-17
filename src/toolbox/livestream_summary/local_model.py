# -*- coding: utf-8 -*-
"""本机大模型（Ollama）检测与模型下载。

只使用 Ollama 的本地 HTTP API，不静默安装、不修改系统服务：
- `/api/tags` 检测服务与已安装模型；
- `/api/pull` 流式下载模型并回报进度，可取消。

未安装 Ollama 时由页面给出官网入口与安装命令，用户自行安装。
"""

from __future__ import annotations

import json
import threading
from typing import Callable

import requests

OLLAMA_BASE = "http://127.0.0.1:11434"
CHAT_BASE = OLLAMA_BASE + "/v1"
INSTALL_URL = "https://ollama.com/download"
INSTALL_COMMAND = "winget install Ollama.Ollama"

# 推荐模型：体积为约数，实际以下载页为准
RECOMMENDED_MODELS = (
    {"name": "qwen2.5:7b", "label": "通义千问 2.5 7B", "size": "约 4.7 GB",
     "note": "中文总结首选，建议 8GB 以上内存"},
    {"name": "qwen2.5:3b", "label": "通义千问 2.5 3B", "size": "约 1.9 GB",
     "note": "配置较低时使用"},
    {"name": "glm4:9b", "label": "智谱 GLM-4 9B", "size": "约 5.5 GB",
     "note": "中文长文理解"},
    {"name": "llama3.1:8b", "label": "Llama 3.1 8B", "size": "约 4.9 GB",
     "note": "通用模型，中文略弱于千问"},
)


class LocalModelError(RuntimeError):
    """本机模型服务错误。"""


class LocalModelCancelled(RuntimeError):
    """用户停止下载。"""


def chat_base_url() -> str:
    return CHAT_BASE


def format_size(num: int | float) -> str:
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    gb = value / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{value / (1024 ** 2):.0f} MB"


def is_running(timeout: float = 2.0) -> bool:
    try:
        response = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def list_models(timeout: float = 5.0) -> list[dict]:
    """返回已安装模型：[{name, size}]。"""
    try:
        response = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise LocalModelError(f"无法连接本机 Ollama：{exc}") from exc
    result = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        result.append({"name": name, "size": size})
    result.sort(key=lambda row: row["name"])
    return result


def parse_pull_event(line: str) -> dict:
    """解析 Ollama /api/pull 的一行 NDJSON，返回 percent / message / done / error。"""
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    error = payload.get("error")
    if error:
        return {"error": str(error)}
    status = str(payload.get("status") or "").strip()
    completed = payload.get("completed")
    total = payload.get("total")
    percent = 0
    try:
        if float(total or 0) > 0:
            percent = int(float(completed or 0) * 100 / float(total))
    except (TypeError, ValueError):
        percent = 0
    message = status
    if percent:
        message = f"{status} {format_size(completed or 0)} / {format_size(total or 0)}"
    return {
        "percent": max(0, min(100, percent)),
        "message": message,
        "done": status == "success",
    }


def pull_model(model: str, progress: Callable[[int, str], None] | None = None,
               cancel: threading.Event | None = None) -> None:
    """下载（拉取）模型；通过 progress(percent, message) 回报进度。"""
    model = (model or "").strip()
    if not model:
        raise LocalModelError("没有指定模型名称")
    progress = progress or (lambda _p, _m: None)
    last_percent = 0
    try:
        with requests.post(f"{OLLAMA_BASE}/api/pull",
                           json={"model": model, "stream": True},
                           stream=True, timeout=(15, 3600)) as response:
            if response.status_code >= 400:
                detail = (response.text or "").strip()[:200]
                raise LocalModelError(
                    f"下载失败：HTTP {response.status_code} {detail}")
            for raw in response.iter_lines(decode_unicode=True):
                if cancel is not None and cancel.is_set():
                    raise LocalModelCancelled("已停止下载")
                if not raw:
                    continue
                event = parse_pull_event(raw)
                if not event:
                    continue
                if event.get("error"):
                    raise LocalModelError(f"下载失败：{event['error']}")
                percent = max(last_percent, int(event.get("percent") or 0))
                last_percent = percent
                progress(percent, str(event.get("message") or ""))
                if event.get("done"):
                    return
    except requests.RequestException as exc:
        raise LocalModelError(f"下载请求失败：{exc}") from exc
