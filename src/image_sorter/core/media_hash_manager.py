"""
媒体哈希管理模块
负责计算媒体文件 MD5，并基于 SQLite 缓存（按大小/修改时间校验失效）
"""

import hashlib
from pathlib import Path
from typing import Iterable, Set
from image_sorter.core.sqlite_cache import SQLiteCacheStore
from image_sorter._log import logger


class MediaHashManager:
    """媒体文件 MD5 管理器（基于 SQLite 缓存）"""

    def __init__(self, sqlite_store: SQLiteCacheStore):
        self.sqlite_store = sqlite_store

    def calculate_md5(self, file_path: Path) -> str:
        """
        计算文件 MD5（命中 SQLite 缓存则直接返回）

        Args:
            file_path: 文件路径

        Returns:
            MD5 字符串，失败返回空字符串
        """
        try:
            stat = file_path.stat()
        except Exception:
            return ""

        cached = self.sqlite_store.get_cached_md5(
            file_path=file_path,
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
        )
        if cached:
            return cached

        md5_value = self._compute_md5_stream(file_path)
        if not md5_value:
            return ""

        self.sqlite_store.upsert_md5(
            file_path=file_path,
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
            md5_value=md5_value,
        )
        return md5_value

    @staticmethod
    def _compute_md5_stream(file_path: Path) -> str:
        """流式读取计算 MD5"""
        try:
            md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    md5.update(chunk)
            return md5.hexdigest()
        except Exception as e:
            logger.log_exception(e, f"计算MD5失败: {file_path}")
            return ""
