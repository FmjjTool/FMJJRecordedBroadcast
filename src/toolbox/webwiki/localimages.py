"""本地资料站 · 优先使用腾讯文档监控已下载的本地图片。

工具箱「腾讯文档监控 → 采集图片」会把智能表格图片字段下载到
`download/腾讯文档数据/图片/<文档>/<工作表>/<字段>/`，并在 tables.db 的
images 表里记录 url → local_path。本地站点请求 /api/img 时先查这张表：
命中就直接读本地文件（按 320/960 生成缩略图并缓存），不再走网络；
未命中再回退到本地缩略图缓存 / 远端图床。
"""

from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
import threading
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from toolbox.config_manager import get_export_subdir

from . import imgcache

_URL_CACHE: dict = {"key": None, "by_url": {}, "by_key": {}}
_CACHE_LOCK = threading.Lock()
_ALLOWED_SIZES = (320, 960)


def _data_dir() -> Path:
    return get_export_subdir("tcdoc")


def _db_path() -> Path:
    return _data_dir() / "tables.db"


def _db_stamp(db: Path) -> tuple:
    try:
        stat = db.stat()
        wal = Path(str(db) + "-wal")
        wal_stat = wal.stat() if wal.is_file() else None
        return (stat.st_mtime_ns, stat.st_size,
                wal_stat.st_mtime_ns if wal_stat else 0,
                wal_stat.st_size if wal_stat else 0)
    except OSError:
        return (0, 0, 0, 0)


def _load_map() -> dict:
    db = _db_path()
    if not db.is_file():
        return {"by_url": {}, "by_key": {}}
    stamp = _db_stamp(db)
    with _CACHE_LOCK:
        if _URL_CACHE["key"] == stamp:
            return _URL_CACHE
    by_url: dict[str, str] = {}
    by_key: dict[str, str] = {}
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        rows = conn.execute(
            "SELECT url, image_key, local_path FROM images "
            "WHERE local_path IS NOT NULL AND local_path != '' AND status='done'"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    for url, image_key, local_path in rows:
        url = str(url or "").strip()
        local_path = str(local_path or "").strip()
        if not url or not local_path:
            continue
        by_url.setdefault(url, local_path)
        if image_key:
            by_key.setdefault(str(image_key), local_path)
    data = {"by_url": by_url, "by_key": by_key}
    with _CACHE_LOCK:
        _URL_CACHE.clear()
        _URL_CACHE.update({"key": stamp, **data})
    return data


def _image_key(url: str) -> str:
    try:
        path = urlsplit(url).path or url
    except ValueError:
        path = url
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


def find(url: str) -> Path | None:
    """按图片 URL 找本地已下载文件；没有返回 None。"""
    first = str(url or "").strip().split(" ")[0]
    if not first:
        return None
    mapping = _load_map()
    local_path = mapping["by_url"].get(first) or mapping["by_key"].get(_image_key(first))
    if not local_path:
        return None
    path = Path(local_path)
    if not path.is_absolute():
        path = _data_dir() / path
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path
    except OSError:
        pass
    return None


def available_count() -> int:
    mapping = _load_map()
    return len(mapping["by_url"])


def _content_type(path: Path, data: bytes) -> str:
    ctype = imgcache.sniff_type(data)
    if ctype != "application/octet-stream":
        return ctype
    guessed = mimetypes.guess_type(str(path))[0]
    return guessed or "application/octet-stream"


def _thumbnail(path: Path, size: int) -> tuple[bytes, str] | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((size, size), Image.LANCZOS)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            buffer = BytesIO()
            try:
                image.save(buffer, format="WEBP", quality=82, method=4)
                return buffer.getvalue(), "image/webp"
            except (OSError, ValueError):
                buffer = BytesIO()
                if image.mode == "RGBA":
                    image = image.convert("RGB")
                image.save(buffer, format="JPEG", quality=86)
                return buffer.getvalue(), "image/jpeg"
    except (OSError, ValueError):
        return None


def serve(cache_root: Path | str, url: str, size: int) -> tuple[bytes, str] | None:
    """读取本地图片；size 为 320/960 时生成并缓存缩略图。

    cache_root 是本地资料站根目录，缩略图复用 imgcache（按原 URL + size 存）。
    """
    first = str(url or "").strip().split(" ")[0]
    if not first:
        return None
    path = find(first)
    if path is None:
        return None
    if size in _ALLOWED_SIZES:
        cached = imgcache.load(cache_root, first, size)
        if cached:
            return cached
        thumb = _thumbnail(path, size)
        if thumb:
            imgcache.save(cache_root, first, size, thumb[0])
            return thumb
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return data, _content_type(path, data)
