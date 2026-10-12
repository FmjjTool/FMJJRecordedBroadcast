"""
缓存管理模块
统一管理导入扫描缓存、缩略图缓存、MD5缓存文件路径

与源版本的差异：
- PyQt6 → PySide6（QBuffer/QIODevice/QPixmap 等价 API）
- 缓存根目录从源项目 cache/ 迁至工具箱 config/image_sorter/
  （scan/ + thumbnails/ + cache.db，见 settings.get_cache_root_dir）
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QPixmap

from image_sorter.constants import THUMBNAIL_SIZE
from image_sorter.settings import get_cache_root_dir
from image_sorter.core.sqlite_cache import SQLiteCacheStore
from image_sorter._log import logger


class CacheManager:
    """缓存管理器"""

    SCAN_CACHE_VERSION = 1

    def __init__(self, base_dir: Optional[Path] = None):
        default_base = get_cache_root_dir()
        self.base_dir = base_dir or default_base
        self.scan_cache_dir: Path = Path()
        self.thumbnail_cache_dir: Path = Path()
        self.sqlite_cache_file: Path = Path()
        self.sqlite_store: Optional[SQLiteCacheStore] = None
        self._refresh_paths()

    def clear_scan_cache(self):
        """清空导入扫描缓存"""
        try:
            if self.scan_cache_dir.exists():
                shutil.rmtree(self.scan_cache_dir, ignore_errors=True)
        finally:
            self.scan_cache_dir.mkdir(parents=True, exist_ok=True)

    def clear_thumbnail_cache(self):
        """清空缩略图缓存"""
        try:
            if self.thumbnail_cache_dir.exists():
                shutil.rmtree(self.thumbnail_cache_dir, ignore_errors=True)
        finally:
            self.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)

    def clear_md5_cache(self):
        """清空媒体 MD5 计算缓存"""
        try:
            if self.sqlite_store is not None:
                self.sqlite_store.clear_md5_cache()
        except Exception as e:
            logger.log_exception(e, "清理MD5缓存失败")

    def load_scan_cache(
        self,
        folder_paths: List[str],
        sort_order: str,
    ) -> Optional[Tuple[List[Path], Set[Path], int, int, int]]:
        """读取扫描缓存（完全命中才返回）"""
        cache_file = self.scan_cache_dir / f"{self._build_scan_key(folder_paths, sort_order)}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)

            if not isinstance(payload, dict):
                return None
            if int(payload.get("version", 0)) != self.SCAN_CACHE_VERSION:
                return None

            file_records = payload.get("files", [])
            corrupted_records = payload.get("corrupted_files", [])
            filtered_count = int(payload.get("filtered_count", 0))

            scanned_files = self._validate_records(file_records)
            corrupted_files = set(self._validate_records(corrupted_records))
            if scanned_files is None or corrupted_files is None:
                return None

            total_files = int(payload.get("total_files", -1))
            total_supported_files = int(payload.get("total_supported_files", -1))
            return (
                scanned_files,
                corrupted_files,
                max(filtered_count, 0),
                max(total_files, -1),
                max(total_supported_files, -1),
            )
        except Exception as e:
            logger.log_exception(e, f"读取扫描缓存失败: {cache_file}")
            return None

    def save_scan_cache(
        self,
        folder_paths: List[str],
        sort_order: str,
        scanned_files: List[Path],
        corrupted_files: Set[Path],
        filtered_count: int,
        total_files: int,
        total_supported_files: int,
    ):
        """保存扫描缓存"""
        cache_file = self.scan_cache_dir / f"{self._build_scan_key(folder_paths, sort_order)}.json"

        file_records = self._build_records(scanned_files)
        corrupted_records = self._build_records(list(corrupted_files))

        payload = {
            "version": self.SCAN_CACHE_VERSION,
            "sort_order": sort_order,
            "source_folders": [self._normalize_path_str(Path(p)) for p in folder_paths],
            "files": file_records,
            "corrupted_files": corrupted_records,
            "filtered_count": int(max(filtered_count, 0)),
            "total_files": int(max(total_files, 0)),
            "total_supported_files": int(max(total_supported_files, 0)),
            "saved_at": int(time.time()),
        }

        try:
            self.scan_cache_dir.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.log_exception(e, f"写入扫描缓存失败: {cache_file}")

    def load_thumbnail(self, file_path: Path) -> Optional[QPixmap]:
        """读取缩略图缓存"""
        cache_stem = self._get_thumbnail_cache_stem(file_path)
        if cache_stem is None:
            return None

        for ext in (".avif", ".webp", ".jpg", ".jpeg"):
            cache_file = cache_stem.with_suffix(ext)
            if not cache_file.exists():
                continue
            pixmap = QPixmap(str(cache_file))
            if not pixmap.isNull():
                return pixmap
        return None

    def save_thumbnail(self, file_path: Path, pixmap: QPixmap):
        """写入缩略图缓存"""
        cache_stem = self._get_thumbnail_cache_stem(file_path)
        if cache_stem is None or pixmap.isNull():
            return
        try:
            cache_stem.parent.mkdir(parents=True, exist_ok=True)
            encoded, ext = self._encode_micro_thumbnail(pixmap)
            if not encoded:
                return

            target_file = cache_stem.with_suffix(ext)
            with open(target_file, "wb") as f:
                f.write(encoded)

            for old_ext in (".avif", ".webp", ".jpg", ".jpeg"):
                stale = cache_stem.with_suffix(old_ext)
                if stale == target_file:
                    continue
                if stale.exists():
                    try:
                        stale.unlink()
                    except Exception:
                        pass
        except Exception as e:
            logger.log_exception(e, f"保存缩略图缓存失败: {file_path}")

    def _refresh_paths(self):
        """刷新并确保缓存目录存在"""
        self.scan_cache_dir = self.base_dir / "scan"
        self.thumbnail_cache_dir = self.base_dir / "thumbnails"
        self.sqlite_cache_file = self.base_dir / "cache.db"
        self.scan_cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_store = SQLiteCacheStore(self.sqlite_cache_file)

    def mark_md5_classified_batch(self, md5_hashes):
        """批量标记 MD5 为已分类"""
        incoming = {h for h in md5_hashes if isinstance(h, str) and h}
        if not incoming:
            return
        try:
            if self.sqlite_store is not None:
                self.sqlite_store.mark_classified_md5_batch(incoming)
        except Exception as e:
            logger.log_exception(e, "保存已分类MD5集合失败")

    def load_classified_md5_set(self) -> set:
        """加载已分类的 MD5 集合"""
        try:
            if self.sqlite_store is not None:
                return self.sqlite_store.load_classified_md5_set()
            return set()
        except Exception as e:
            logger.log_exception(e, "加载已分类MD5集合失败")
            return set()

    def get_cached_grouping_timestamp(self, file_path: Path, stat_result=None) -> Optional[float]:
        """从SQLite读取分组时间缓存（命中需校验size/mtime/ctime）。"""
        if self.sqlite_store is None:
            return None
        try:
            stat = stat_result if stat_result is not None else file_path.stat()
            return self.sqlite_store.get_grouping_timestamp(
                file_path=file_path,
                size=int(stat.st_size),
                mtime_ns=int(stat.st_mtime_ns),
                ctime_ns=int(stat.st_ctime_ns),
            )
        except Exception:
            return None

    def save_grouping_timestamp(self, file_path: Path, grouping_timestamp: Optional[float], stat_result=None):
        """写入分组时间缓存到SQLite。"""
        if self.sqlite_store is None:
            return
        try:
            stat = stat_result if stat_result is not None else file_path.stat()
            self.sqlite_store.upsert_grouping_timestamp(
                file_path=file_path,
                size=int(stat.st_size),
                mtime_ns=int(stat.st_mtime_ns),
                ctime_ns=int(stat.st_ctime_ns),
                group_ts=grouping_timestamp,
            )
        except Exception:
            return

    def _build_scan_key(self, folder_paths: List[str], sort_order: str) -> str:
        """构建扫描缓存键"""
        normalized = [self._normalize_path_str(Path(p)) for p in folder_paths]
        payload = {
            "folders": normalized,
            "sort_order": sort_order,
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(content.encode("utf-8")).hexdigest()

    def _validate_records(self, records: list) -> Optional[List[Path]]:
        """校验缓存记录有效性（抽样校验，避免大规模全量stat卡顿）"""
        if not isinstance(records, list):
            return None

        parsed: List[Tuple[Path, int, float]] = []
        for record in records:
            if not isinstance(record, dict):
                return None
            path_value = record.get("path")
            size_value = record.get("size")
            mtime_value = record.get("mtime")
            if not isinstance(path_value, str):
                return None
            if not isinstance(size_value, int):
                return None
            if not isinstance(mtime_value, (int, float)):
                return None

            parsed.append((Path(path_value), int(size_value), float(mtime_value)))

        if not parsed:
            return []

        sample_count = min(160, len(parsed))
        if sample_count <= 0:
            return [item[0] for item in parsed]

        step = max(len(parsed) // sample_count, 1)
        sample_indexes = list(range(0, len(parsed), step))[:sample_count]
        if (len(parsed) - 1) not in sample_indexes:
            sample_indexes.append(len(parsed) - 1)

        for idx in sample_indexes:
            file_path, expected_size, expected_mtime = parsed[idx]
            if not file_path.exists() or (not file_path.is_file()):
                return None
            try:
                stat = file_path.stat()
            except Exception:
                return None

            if int(stat.st_size) != expected_size:
                return None
            if abs(float(stat.st_mtime) - expected_mtime) > 1e-6:
                return None

        return [item[0] for item in parsed]

    def _build_records(self, file_paths: List[Path]) -> List[dict]:
        """构建缓存记录"""
        records: List[dict] = []
        for file_path in file_paths:
            try:
                stat = file_path.stat()
            except Exception:
                continue
            records.append(
                {
                    "path": self._normalize_path_str(file_path),
                    "size": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                }
            )
        return records

    def _get_thumbnail_cache_stem(self, file_path: Path) -> Optional[Path]:
        """获取缩略图缓存文件路径前缀（不含扩展名）"""
        try:
            stat = file_path.stat()
        except Exception:
            return None

        payload = (
            f"{self._normalize_path_str(file_path)}|{int(stat.st_size)}|"
            f"{float(stat.st_mtime)}|{THUMBNAIL_SIZE[0]}x{THUMBNAIL_SIZE[1]}|micro_v2"
        )
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return self.thumbnail_cache_dir / digest[:2] / digest

    @staticmethod
    def _encode_qpixmap(pixmap: QPixmap, image_format: str, quality: int) -> bytes:
        buffer = QBuffer()
        if not buffer.open(QIODevice.WriteOnly):
            return b""
        ok = pixmap.save(buffer, image_format, int(quality))
        raw = bytes(buffer.data()) if ok else b""
        buffer.close()
        return raw

    def _encode_micro_thumbnail(self, pixmap: QPixmap) -> tuple[bytes, str]:
        """
        生成列表微型缩略图：
        - 优先 AVIF/WEBP
        - 尽量控制在 5KB~50KB
        """
        min_bytes = 5 * 1024
        max_bytes = 50 * 1024
        qualities = [92, 86, 80, 74, 68, 62, 56, 50, 44, 38]

        best_data = b""
        best_ext = ".webp"
        best_distance = float("inf")

        for image_format, ext in (("AVIF", ".avif"), ("WEBP", ".webp")):
            for quality in qualities:
                data = self._encode_qpixmap(pixmap, image_format, quality)
                if not data:
                    # 当前格式不受支持时直接切到下一种格式
                    break
                size = len(data)
                if min_bytes <= size <= max_bytes:
                    return data, ext

                distance = min(abs(size - min_bytes), abs(size - max_bytes))
                if distance < best_distance:
                    best_distance = distance
                    best_data = data
                    best_ext = ext

        if best_data:
            return best_data, best_ext

        # 最后回退 JPG，保证兼容性
        jpg_data = self._encode_qpixmap(pixmap, "JPG", 82)
        return jpg_data, ".jpg"

    @staticmethod
    def _normalize_path_str(path: Path) -> str:
        """路径标准化字符串"""
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path.absolute()
        return os.path.normcase(os.path.normpath(str(resolved)))
