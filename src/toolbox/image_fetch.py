"""图片预览下载（L3）：统一超时、UA 与 Referer，不依赖 Qt。"""

from __future__ import annotations

import httpx

DEFAULT_UA = "Mozilla/5.0"


def fetch_image(url: str, *, user_agent: str = DEFAULT_UA,
                referer: str = "", timeout: float = 30.0,
                transport=None) -> bytes:
    """下载图片字节；失败抛 httpx 异常，由调用方决定提示文案。"""
    headers = {"User-Agent": user_agent or DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      transport=transport) as cli:
        resp = cli.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content
