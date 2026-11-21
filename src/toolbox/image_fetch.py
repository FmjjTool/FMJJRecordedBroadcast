"""图片预览下载（L3）：统一超时、UA 与 Referer，不依赖 Qt。"""

from __future__ import annotations

import httpx

DEFAULT_UA = "Mozilla/5.0"


def fetch_image(url: str, *, user_agent: str = DEFAULT_UA,
                referer: str = "", timeout: float = 30.0,
                transport=None, max_bytes=None) -> bytes:
    """下载图片字节；失败抛 httpx 异常，由调用方决定提示文案。"""
    headers = {"User-Agent": user_agent or DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      transport=transport) as cli:
        if max_bytes is not None:
            content = bytearray()
            with cli.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(64 * 1024):
                    if len(content) + len(chunk) > max_bytes:
                        raise ValueError("预览图片超过大小限制")
                    content.extend(chunk)
            return bytes(content)
        resp = cli.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


def fetch_thumbnail(url: str, size=(44, 44), **kwargs) -> bytes:
    """仅供预览：下载限额后缩小首帧，缓存不保留原始大图/动图字节。"""
    from io import BytesIO
    from PIL import Image

    data = fetch_image(url, max_bytes=8 * 1024 * 1024, **kwargs)
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > 100_000_000:
            raise ValueError("预览图片像素过大")
        image.draft("RGB", size)
        image.thumbnail(size, Image.Resampling.LANCZOS)
        transparent = "A" in image.getbands() or "transparency" in image.info
        image = image.convert("RGBA" if transparent else "RGB")
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
