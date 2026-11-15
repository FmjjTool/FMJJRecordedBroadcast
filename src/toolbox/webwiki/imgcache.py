"""本地资料站 · 首屏封面本地缓存。

腾讯文档图床的缩略图由源站 COS 按需生成，冷启动每张要几秒。启动前把
首屏封面抓一遍存到本地，静态站直接命中缓存，浏览器打开就能看到图；
未预热的图片仍按原链路走 /api/img，并在成功取回后顺手落缓存。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def cache_dir(root: Path | str) -> Path:
    path = Path(root) / "webwiki-api" / "data" / "imgcache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(url: str, size: int) -> str:
    raw = f"{url}|{int(size or 0)}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:32]


def path_of(root: Path | str, url: str, size: int) -> Path:
    return cache_dir(root) / f"{_key(url, size)}.bin"


def sniff_type(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return "application/octet-stream"


def save(root: Path | str, url: str, size: int, data: bytes) -> None:
    if not data:
        return
    target = path_of(root, url, size)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def load(root: Path | str, url: str, size: int) -> tuple[bytes, str] | None:
    target = path_of(root, url, size)
    try:
        data = target.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return data, sniff_type(data)
