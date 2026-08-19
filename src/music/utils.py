"""
工具函数 - HTTP 请求、加密、通用辅助
"""

from __future__ import annotations

import json
import re
import random
from typing import Any, Optional

import httpx

# 常见浏览器 UA 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def random_ua() -> str:
    return random.choice(USER_AGENTS)


def make_headers(referer: str = "", extra: Optional[dict] = None) -> dict:
    """构建请求头"""
    h = {**DEFAULT_HEADERS, "User-Agent": random_ua()}
    if referer:
        h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def get_client(timeout: float = 15.0) -> httpx.Client:
    """获取 httpx Client"""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        http2=False,
        headers={"User-Agent": random_ua()},
    )


def safe_json(text: str) -> Any:
    """安全解析 JSON，处理各种格式"""
    text = text.strip()
    # 移除 BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    # 处理 JSONP callback
    cb_match = re.match(r'^[\w.]+\((.*)\)[\s;]*$', text, re.DOTALL)
    if cb_match:
        text = cb_match.group(1)
    # 处理单引号 JSON
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试替换单引号
    try:
        return json.loads(text.replace("'", '"'))
    except json.JSONDecodeError:
        pass
    return None








def parse_duration(raw) -> int:
    """将各种时长格式转为秒数"""
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw if raw < 10000 else raw // 1000  # 可能是毫秒
    if isinstance(raw, str):
        raw = raw.strip()
        # mm:ss
        m = re.match(r'^(\d+):(\d{2})$', raw)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        # 纯数字（可能是秒或毫秒）
        try:
            v = int(raw)
            return v if v < 10000 else v // 1000
        except ValueError:
            pass
    return 0
