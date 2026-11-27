"""Taobao CDN image adapter with bounded, cancellable streaming.

Only public image URLs are accepted. Platform credentials are never passed to
the HTTP client; redirects are disabled. Files belong to the caller's newly
allocated batch directory, and only files created by this call are removed.
"""

from __future__ import annotations

import os
import stat
import warnings
from pathlib import Path
from urllib.parse import urlsplit

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class TaobaoMediaError(ValueError):
    """A safe media-adapter failure, without source URLs or paths."""


class _CheckpointStop(Exception):
    def __init__(self, error):
        self.error = error


def cdn_url(value):
    if type(value) is not str or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise TaobaoMediaError("商品图片地址无效，本项已停止")
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        valid_host = any(host == suffix or host.endswith("." + suffix)
                         for suffix in ("alicdn.com", "taobaocdn.com", "tbcdn.cn"))
        if (parts.scheme not in {"https", "http"} or not valid_host
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443 if parts.scheme == "https" else 80)
                or parts.fragment):
            raise ValueError()
    except ValueError:
        raise TaobaoMediaError("商品图片来源不受支持，本项已停止") from None
    return value


def regular(path):
    state = Path(path).lstat()
    if stat.S_ISLNK(state.st_mode) or getattr(state, "st_file_attributes", 0) & _REPARSE:
        raise TaobaoMediaError("导出位置包含不支持的链接，请调整导出目录后重试")
    return state


def plain_chain(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise TaobaoMediaError("导出位置无效，请调整导出目录后重试")
    for ancestor in (*reversed(path.parents), path):
        try:
            regular(ancestor)
        except FileNotFoundError:
            continue


def ensure_directory(path):
    path = Path(path)
    plain_chain(path)
    path.mkdir(parents=True, exist_ok=True)
    plain_chain(path)
    if not path.is_dir():
        raise TaobaoMediaError("导出位置不可用，请调整导出目录后重试")
    return path


def below(path, root):
    path = Path(path)
    try:
        path.relative_to(root)
    except (TypeError, ValueError):
        raise TaobaoMediaError("商品文件位置校验失败，本项已停止") from None
    plain_chain(path)
    return path


def validate_picture(path, *, max_bytes, max_pixels):
    from PIL import Image

    state = regular(path)
    if not stat.S_ISREG(state.st_mode) or not 0 < state.st_size <= max_bytes:
        raise TaobaoMediaError("商品图片超过大小上限或格式无效，本项已停止")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as picture:
            if picture.width <= 0 or picture.height <= 0 or picture.width * picture.height > max_pixels:
                raise TaobaoMediaError("商品图片超过像素上限，本项已停止")
            if picture.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
                raise TaobaoMediaError("商品图片格式不受支持，本项已停止")
            picture.verify()


class TaobaoImageDownloader:
    """Callable matching shopping.download_item_images' injected boundary."""

    def __init__(self, root, *, checkpoint, max_file_bytes, max_total_bytes, max_pixels):
        self.root = Path(root)
        self.checkpoint = checkpoint
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.max_pixels = max_pixels
        self.downloaded = set()
        self.total_bytes = 0

    def _check(self):
        # Preserve the L3 caller's cancellation/auth exception verbatim without
        # mistaking InterruptedError (an OSError subclass) for an image failure.
        try:
            self.checkpoint()
        except Exception as error:
            raise _CheckpointStop(error) from None

    def __call__(self, urls, folder, prefix, log=None, threads=6):
        import httpx
        from shopping import UA_PC, _img_ext

        folder = ensure_directory(below(Path(folder), self.root))
        if (type(prefix) is not str or not prefix or Path(prefix).name != prefix
                or any(c in prefix for c in '/\\<>:"|?*')
                or any(ord(c) < 32 for c in prefix)):
            raise TaobaoMediaError("商品图片文件名无效，本项已停止")
        paths = []
        for number, raw_url in enumerate(urls, 1):
            try:
                self._check()
            except _CheckpointStop as error:
                raise error.error from None
            url = cdn_url(raw_url)
            path = folder / f"{prefix}{number:02d}{_img_ext(url)}"
            created, complete = False, False
            try:
                flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
                with httpx.stream("GET", url, timeout=30, follow_redirects=False,
                        headers={"User-Agent": UA_PC, "Referer": "https://item.taobao.com/"}) as response:
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if response.is_redirect or (length and (not length.isdecimal() or int(length) > self.max_file_bytes)):
                        continue
                    size = 0
                    descriptor = os.open(path, flags)
                    created = True
                    with os.fdopen(descriptor, "wb") as stream:
                        for chunk in response.iter_bytes(64 * 1024):
                            self._check()
                            size += len(chunk)
                            self.total_bytes += len(chunk)
                            if size > self.max_file_bytes or self.total_bytes > self.max_total_bytes:
                                raise TaobaoMediaError("商品图片超过大小上限，本项已停止")
                            stream.write(chunk)
                validate_picture(path, max_bytes=self.max_file_bytes, max_pixels=self.max_pixels)
                complete = True
                paths.append(str(path))
                self.downloaded.add(url)
            except _CheckpointStop as error:
                raise error.error from None
            except TaobaoMediaError:
                raise
            except Exception:
                # Missing/invalid image data is optional: the workbook keeps its
                # source URL. No raw URL, platform error or local path is logged.
                pass
            finally:
                if created and not complete:
                    path.unlink(missing_ok=True)
        return paths
