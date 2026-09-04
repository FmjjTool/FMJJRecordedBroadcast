"""
MD5过滤线程
用于后台排除已分类媒体文件，避免阻塞UI

与源版本的差异：仅 PyQt6 → PySide6（pyqtSignal → Signal）。
"""

import os
from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import QThread, Signal
from image_sorter.core.media_hash_manager import MediaHashManager
from image_sorter.constants import SUPPORTED_FORMATS
from image_sorter._log import logger


class Md5FilterThread(QThread):
    """异步MD5过滤线程"""

    filtering_complete = Signal(int, list, int)  # scan_id, filtered_files(str), excluded_count
    filtering_failed = Signal(int, str)  # scan_id, error_message
    filtering_progress = Signal(int, float, str)  # scan_id, percent, stage_text

    def __init__(
        self,
        scan_id: int,
        scanned_files: List[Path],
        work_dir: Path,
        source_folders: List[Path],
        classified_folders: Optional[List[Path]] = None,
        cache_manager=None,
        sqlite_store=None,
    ):
        super().__init__()
        self.scan_id = scan_id
        self.scanned_files = list(scanned_files)
        self.work_dir = work_dir
        self.source_folders = list(source_folders)
        self.classified_folders = list(classified_folders or [])
        self.cache_manager = cache_manager
        self.sqlite_store = sqlite_store
        self._last_progress_bucket = -1

    def run(self):
        """执行后台MD5过滤"""
        try:
            self._emit_progress(0.0, "MD5解析准备中", force=True)
            if not self.scanned_files:
                self._emit_progress(100.0, "MD5解析完成", force=True)
                self.filtering_complete.emit(self.scan_id, [], 0)
                return

            hash_manager = MediaHashManager(sqlite_store=self.sqlite_store)

            valid_classified_folders: List[Path] = []
            seen_folders = set()
            for folder in self.classified_folders:
                if not folder.exists() or (not folder.is_dir()):
                    continue
                key = self._path_key(folder)
                if key in seen_folders:
                    continue
                seen_folders.add(key)
                valid_classified_folders.append(folder)

            classified_files: List[Path] = []
            total_folders = len(valid_classified_folders)
            if total_folders > 0:
                for folder_index, folder in enumerate(valid_classified_folders, start=1):
                    for item in folder.rglob("*"):
                        if item.is_file() and item.suffix.lower() in SUPPORTED_FORMATS:
                            classified_files.append(item)
                    self._emit_progress(
                        (folder_index / total_folders) * 25.0,
                        f"MD5解析中(扫描分类库 {folder_index}/{total_folders})",
                    )
            else:
                self._emit_progress(25.0, "MD5解析中(扫描分类库 0/0)", force=True)

            classified_hashes = set()

            # 合并持久化的已分类 MD5 集合（来自"清空文件夹内容"操作）
            if self.cache_manager and hasattr(self.cache_manager, 'load_classified_md5_set'):
                persistent_hashes = self.cache_manager.load_classified_md5_set()
                classified_hashes.update(persistent_hashes)

            if not classified_files and not classified_hashes:
                self._emit_progress(100.0, "MD5解析完成", force=True)
                self.filtering_complete.emit(self.scan_id, [str(p) for p in self.scanned_files], 0)
                return

            total_classified = len(classified_files)
            for index, file_path in enumerate(classified_files, start=1):
                md5_value = hash_manager.calculate_md5(file_path)
                if md5_value:
                    classified_hashes.add(md5_value)
                self._emit_progress(
                    25.0 + (index / total_classified) * 45.0,
                    f"MD5解析中(分类库哈希 {index}/{total_classified})",
                )

            if not classified_hashes:
                self._emit_progress(100.0, "MD5解析完成", force=True)
                self.filtering_complete.emit(self.scan_id, [str(p) for p in self.scanned_files], 0)
                return

            filtered_files: List[Path] = []
            excluded_count = 0
            total_scanned = len(self.scanned_files)
            for index, file_path in enumerate(self.scanned_files, start=1):
                file_md5 = hash_manager.calculate_md5(file_path)
                if file_md5 and file_md5 in classified_hashes:
                    excluded_count += 1
                else:
                    filtered_files.append(file_path)

                self._emit_progress(
                    70.0 + (index / total_scanned) * 30.0,
                    f"MD5解析中(待分类比对 {index}/{total_scanned})",
                )

            self._emit_progress(100.0, "MD5解析完成", force=True)
            self.filtering_complete.emit(self.scan_id, [str(p) for p in filtered_files], excluded_count)
        except Exception as e:
            logger.log_exception(e, "后台MD5过滤失败")
            self.filtering_failed.emit(self.scan_id, str(e))

    @staticmethod
    def _path_key(path: Path) -> str:
        """路径归一化键"""
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path.absolute()
        return os.path.normcase(os.path.normpath(str(resolved)))

    def _emit_progress(self, percent: float, text: str, force: bool = False):
        """发出进度（按0.1%做简单节流）"""
        normalized = max(0.0, min(100.0, float(percent)))
        bucket = int(normalized * 10)
        if not force and bucket == self._last_progress_bucket:
            return
        self._last_progress_bucket = bucket
        self.filtering_progress.emit(self.scan_id, normalized, text)
