"""
文件扫描模块
负责递归扫描文件夹，过滤支持的媒体格式，检测损坏文件
"""

import random
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Set
from image_sorter.constants import SUPPORTED_FORMATS, SUPPORTED_IMAGE_FORMATS, SUPPORTED_VIDEO_FORMATS
from image_sorter.exceptions import CorruptedFileException
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter._log import logger

if TYPE_CHECKING:
    from image_sorter.core.cache_manager import CacheManager


class FileScanner:
    """文件扫描器"""

    def __init__(self):
        self._scanned_files: List[Path] = []
        self._corrupted_files: Set[Path] = set()
        self._filtered_count = 0
        self._total_files = 0
        self._total_supported_files = 0
        self._processed_files = 0
        self._cache_hit = False
        self._progress_callback: Optional[Callable[[float, int, int], None]] = None

    def scan_folders(
        self,
        folder_paths: List[str],
        sort_order: str = 'create_time',
        progress_callback: Optional[Callable[[float, int, int], None]] = None,
        cache_manager: Optional['CacheManager'] = None,
        use_cache: bool = True,
        verify_integrity: bool = True,
        pre_count: bool = True,
    ) -> List[Path]:
        """
        扫描文件夹并返回支持的媒体文件列表

        Args:
            folder_paths: 要扫描的文件夹路径列表
            sort_order: 排序方式，'create_time'按创建时间，'path'按路径，'random'随机

        Returns:
            扫描到的媒体文件路径列表
        """
        self._scanned_files = []
        self._corrupted_files = set()
        self._filtered_count = 0
        self._cache_hit = False
        self._progress_callback = progress_callback
        self._processed_files = 0
        if pre_count:
            self._total_files, self._total_supported_files = self._count_total_and_supported_files(folder_paths)
        else:
            self._total_files = 0
            self._total_supported_files = 0
        self._emit_progress()

        # 优先尝试读取扫描缓存（随机排序不使用缓存，保留每次随机性）
        if use_cache and cache_manager and sort_order != 'random':
            cached = cache_manager.load_scan_cache(folder_paths, sort_order)
            if cached is not None:
                (
                    cached_files,
                    cached_corrupted_files,
                    cached_filtered_count,
                    cached_total_files,
                    cached_total_supported_files,
                ) = cached
                if pre_count and (
                    cached_total_files != self._total_files
                    or cached_total_supported_files != self._total_supported_files
                ):
                    logger.info("扫描缓存失效：文件总量或支持格式数量发生变化")
                else:
                    self._scanned_files = list(cached_files)
                    self._corrupted_files = set(cached_corrupted_files)
                    self._filtered_count = int(max(cached_filtered_count, 0))
                    self._processed_files = len(self._scanned_files) + len(self._corrupted_files) + self._filtered_count
                    self._total_files = max(self._processed_files, 1)
                    self._emit_progress(force_complete=True)
                    self._cache_hit = True
                    logger.log_operation(
                        "文件扫描缓存命中",
                        f"命中 {len(self._scanned_files)} 个文件，损坏 {len(self._corrupted_files)} 个，"
                        f"过滤 {self._filtered_count} 个"
                    )
                    return list(self._scanned_files)

        # 递归扫描所有文件夹
        for folder_path in folder_paths:
            folder = Path(folder_path)
            if not folder.exists() or not folder.is_dir():
                logger.error(f"文件夹不存在或不是目录: {folder_path}")
                continue

            self._scan_directory_recursive(folder, verify_integrity=verify_integrity)

        if not pre_count:
            self._total_files = max(self._processed_files, 1)

        # 去重（同一文件仅保留一次）
        unique_files = list(set(self._scanned_files))

        # 排序
        if sort_order == 'create_time':
            unique_files.sort(key=self._safe_ctime)
        elif sort_order == 'path':
            unique_files.sort(key=lambda f: str(f).lower())
        elif sort_order == 'random':
            random.shuffle(unique_files)

        logger.log_operation(
            "文件扫描完成",
            f"共扫描 {len(unique_files)} 个文件，过滤 {self._filtered_count} 个非媒体文件，"
            f"检测到 {len(self._corrupted_files)} 个损坏文件"
        )

        if use_cache and cache_manager and sort_order != 'random':
            cache_manager.save_scan_cache(
                folder_paths=folder_paths,
                sort_order=sort_order,
                scanned_files=unique_files,
                corrupted_files=self._corrupted_files,
                filtered_count=self._filtered_count,
                total_files=self._total_files,
                total_supported_files=self._total_supported_files,
            )

        self._emit_progress(force_complete=True)

        return unique_files

    def _scan_directory_recursive(self, directory: Path, verify_integrity: bool = True):
        """
        递归扫描目录

        Args:
            directory: 目录路径
        """
        try:
            for item in directory.iterdir():
                if item.is_dir():
                    # 递归扫描子文件夹
                    self._scan_directory_recursive(item, verify_integrity=verify_integrity)
                elif item.is_file():
                    self._processed_files += 1
                    # iPhone Live Photo：同名图片 + .mov（sidecar）只保留图片本体
                    try:
                        suffix = item.suffix.lower()
                    except Exception:
                        suffix = ""
                    if suffix == ".mov" and any(
                        item.with_suffix(ext).exists()
                        for ext in (".jpg", ".jpeg", ".heic", ".heif")
                    ):
                        continue

                    # 检查文件格式
                    if self._is_supported_format(item):
                        # 可选完整性检查（关闭时显著加快导入）
                        if (not verify_integrity) or self._check_file_integrity(item):
                            self._scanned_files.append(item)
                        else:
                            self._corrupted_files.add(item)
                    else:
                        self._filtered_count += 1
                    self._emit_progress()
        except PermissionError:
            logger.log_permission_error(str(directory), "扫描目录")
        except Exception as e:
            logger.log_exception(e, f"扫描目录失败: {directory}")

    def _count_total_and_supported_files(self, folder_paths: List[str]) -> tuple[int, int]:
        """统计待扫描总文件数和支持格式文件数（用于进度及缓存校验）"""
        total = 0
        supported_total = 0
        for folder_path in folder_paths:
            folder = Path(folder_path)
            if not folder.exists() or not folder.is_dir():
                continue
            for _, _, files in os.walk(folder):
                total += len(files)
                for file_name in files:
                    if Path(file_name).suffix.lower() in SUPPORTED_FORMATS:
                        supported_total += 1
        return total, supported_total

    def _emit_progress(self, force_complete: bool = False):
        """发出扫描进度"""
        if not self._progress_callback:
            return

        if self._total_files <= 0:
            processed = max(self._processed_files, 0)
            if force_complete:
                total = max(processed, 1)
                percent = 100.0
                try:
                    self._progress_callback(percent, total, total)
                except Exception:
                    pass
            else:
                try:
                    self._progress_callback(0.0, processed, 0)
                except Exception:
                    pass
            return

        total = max(self._total_files, 1)
        processed = min(self._processed_files, total)
        if force_complete:
            processed = total

        percent = processed * 100.0 / total
        try:
            self._progress_callback(percent, processed, total)
        except Exception:
            pass

    def _is_supported_format(self, file_path: Path) -> bool:
        """
        检查文件格式是否支持

        Args:
            file_path: 文件路径

        Returns:
            是否为支持的格式
        """
        suffix = file_path.suffix.lower()
        return suffix in SUPPORTED_FORMATS

    @staticmethod
    def _safe_ctime(file_path: Path) -> float:
        """安全获取文件创建时间，排序时文件可能已被移动/删除。"""
        try:
            return file_path.stat().st_ctime
        except Exception:
            return 0.0

    def _check_file_integrity(self, file_path: Path) -> bool:
        """
        检查文件完整性（是否损坏）

        Args:
            file_path: 文件路径

        Returns:
            文件是否完整
        """
        try:
            suffix = file_path.suffix.lower()

            if suffix in SUPPORTED_IMAGE_FORMATS:
                # 尝试提取图片信息
                MediaInfoExtractor.extract_image_info(file_path)
            elif suffix in SUPPORTED_VIDEO_FORMATS:
                # 尝试提取视频信息
                MediaInfoExtractor.extract_video_info(file_path)

            return True
        except CorruptedFileException:
            logger.log_file_corrupted(str(file_path))
            return False
        except Exception as e:
            logger.log_exception(e, f"检查文件完整性失败: {file_path}")
            return False

    @property
    def corrupted_files(self) -> Set[Path]:
        """获取损坏文件集合"""
        return self._corrupted_files

    @property
    def filtered_count(self) -> int:
        """获取过滤的文件数量"""
        return self._filtered_count

    @property
    def cache_hit(self) -> bool:
        """是否命中扫描缓存"""
        return self._cache_hit
