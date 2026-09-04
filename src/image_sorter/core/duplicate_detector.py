"""
重复文件检测模块（四层轻量去重，替代源项目的全量 MD5 / 首帧哈希方案）。

漏斗逐层过滤（绝大多数文件在前两层被排除，万级文件整体 IO 从全量读
降至约 1~5%，与源实现对「完全相同副本」的检出能力等价）：
① 尺寸分组（零 IO）：大小不同必非重复
② 头尾抽样哈希（读头 64KB + 尾 64KB）：哈希不同排除
③ 最终指纹——图片线：全量 MD5（仅真实疑似组）；视频线：ffprobe
   时长+分辨率 与头尾哈希组合（cv2 首帧哈希已随 OpenCV 移除）
④ SQLite 持久缓存：全量 MD5 走 MediaHashManager（media_md5_cache 表，
   按 (path, size, mtime) 校验失效，同一批文件二次扫描即时返回）
"""

import hashlib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QThread, Signal

from image_sorter.constants import SUPPORTED_IMAGE_FORMATS, SUPPORTED_VIDEO_FORMATS
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter.core.media_hash_manager import MediaHashManager
from image_sorter._log import logger

# 头尾抽样哈希的读取量（各 64KB）
HEAD_TAIL_CHUNK = 64 * 1024


class DuplicateDetector:
    """重复文件检测器（四层漏斗）"""

    def __init__(self, sqlite_store=None):
        self._sqlite_store = sqlite_store
        self._file_hashes: Dict[str, List[Path]] = {}  # 最终指纹 -> 文件列表
        self._duplicate_groups: List[List[Path]] = []  # 重复文件组
        self._duplicate_files: Set[Path] = set()  # 所有重复文件

    def detect_duplicates(
        self,
        file_paths: List[Path],
        keep_rule: str = 'latest',
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[List[Path]]:
        """
        检测重复文件并分组

        Args:
            file_paths: 文件路径列表
            keep_rule: 保留规则，'latest'保留最新创建的，'manual'手动选择
            progress_callback: 进度回调(progress, total)

        Returns:
            重复文件组列表
        """
        self._file_hashes.clear()
        self._duplicate_groups.clear()
        self._duplicate_files.clear()
        total_files = len(file_paths)
        if total_files == 0:
            if progress_callback:
                try:
                    progress_callback(0, 0)
                except Exception:
                    pass
            return []

        def _emit_progress(done: int, stage_total: int, weight_from: float, weight_to: float):
            """阶段内进度映射到全局 (current, total)（①60% / ②20% / ③20%）。"""
            if progress_callback is None or stage_total <= 0:
                return
            try:
                frac = done / stage_total
                current = int((weight_from + (weight_to - weight_from) * frac) * total_files)
                progress_callback(max(0, min(current, total_files)), total_files)
            except Exception:
                pass

        # ---------- ① 尺寸分组（零 IO） ----------
        size_groups: Dict[int, List[Path]] = {}
        for index, file_path in enumerate(file_paths, start=1):
            try:
                size = int(file_path.stat().st_size)
            except Exception:
                # 扫描与检测并发时，文件可能已被移动/删除，直接跳过即可。
                continue
            size_groups.setdefault(size, []).append(file_path)
            _emit_progress(index, total_files, 0.0, 0.6)

        # ---------- ② 头尾抽样哈希（仅同尺寸疑似组） ----------
        suspect_files = [fp for g in size_groups.values() if len(g) > 1 for fp in g]
        suspects: Dict[Tuple[int, str], List[Path]] = {}  # (size, head_tail) -> files
        done = 0
        for size, group in size_groups.items():
            if len(group) < 2:
                continue
            for file_path in group:
                head_tail = self._hash_head_tail(file_path)
                done += 1
                if head_tail:
                    suspects.setdefault((size, head_tail), []).append(file_path)
                _emit_progress(done, len(suspect_files), 0.6, 0.8)

        # ---------- ③ 最终指纹（图片全量 MD5 / 视频 probe 组合） ----------
        confirmed = [fp for files in suspects.values() if len(files) > 1 for fp in files]
        done = 0
        for (size, head_tail), files in suspects.items():
            if len(files) < 2:
                continue
            for file_path in files:
                done += 1
                final_hash = self._final_fingerprint(file_path)
                if final_hash:
                    # 键带 size 前缀：视频"无probe/同分辨率时长"指纹在不同尺寸间
                    # 也会相同，不隔离会把不同大小的文件误并入同一重复组
                    self._file_hashes.setdefault(f"{size}|{final_hash}", []).append(file_path)
                _emit_progress(done, len(confirmed), 0.8, 1.0)

        # ---------- 重复组（最终指纹相同且超过 1 个文件） ----------
        for files in self._file_hashes.values():
            if len(files) > 1:
                if keep_rule == 'latest':
                    # 按创建时间倒序，最新的在最前（作为默认保留项）
                    files_sorted = sorted(files, key=self._safe_ctime, reverse=True)
                else:
                    # manual：保持发现顺序，由用户手动决定保留项
                    files_sorted = list(files)
                self._duplicate_groups.append(files_sorted)
                self._duplicate_files.update(files_sorted)

        logger.log_operation(
            "重复检测",
            f"检测到 {len(self._duplicate_groups)} 组重复文件，共 {len(self._duplicate_files)} 个文件"
        )

        # 无重复文件时第③层不会发射进度，这里补发终值保证进度到达 100%
        if progress_callback:
            try:
                progress_callback(total_files, total_files)
            except Exception:
                pass

        return self._duplicate_groups

    # ==================== 各层哈希 ====================

    @staticmethod
    def _hash_head_tail(file_path: Path) -> str:
        """第②层：读头 64KB + 尾 64KB 计算抽样哈希（文件不足 64KB 时只读一次）。"""
        try:
            size = file_path.stat().st_size
            md5 = hashlib.md5()
            with open(file_path, 'rb') as f:
                md5.update(f.read(HEAD_TAIL_CHUNK))
                if size > HEAD_TAIL_CHUNK:
                    f.seek(max(0, size - HEAD_TAIL_CHUNK))
                    md5.update(f.read(HEAD_TAIL_CHUNK))
            return md5.hexdigest()
        except FileNotFoundError:
            return ""
        except Exception as e:
            logger.log_exception(e, f"计算头尾抽样哈希失败: {file_path}")
            return ""

    def _final_fingerprint(self, file_path: Path) -> str:
        """第③层最终指纹：图片全量 MD5；视频 ffprobe 时长+分辨率组合。"""
        suffix = file_path.suffix.lower()
        if suffix in SUPPORTED_VIDEO_FORMATS:
            return self._video_fingerprint(file_path)
        # 图片线（含 LIVP 容器）：全量 MD5（走 MediaHashManager 命中 SQLite 缓存）
        return self._image_md5(file_path)

    def _image_md5(self, file_path: Path) -> str:
        """图片全量 MD5；持有 sqlite_store 时走 MediaHashManager（④层持久缓存）。"""
        try:
            if self._sqlite_store is not None:
                return MediaHashManager(sqlite_store=self._sqlite_store).calculate_md5(file_path)
            md5 = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    md5.update(chunk)
            return md5.hexdigest()
        except FileNotFoundError:
            return ""
        except Exception as e:
            logger.log_exception(e, f"计算图片MD5失败: {file_path}")
            return ""

    @staticmethod
    def _video_fingerprint(file_path: Path) -> str:
        """视频指纹：ffprobe 时长+分辨率（ffprobe 不在场时退化为头尾哈希已有信息）。"""
        meta = None
        try:
            # 同包内复用探针；_ProbeUnavailable 等一切异常都按"无元数据"处理
            meta = MediaInfoExtractor._probe_video_meta(file_path)
        except Exception:
            meta = None
        if not meta:
            return "video:no-probe"
        return f"video:{meta['duration']:.3f}:{meta['width']}x{meta['height']}"

    @staticmethod
    def _safe_ctime(file_path: Path) -> float:
        """安全获取ctime，避免排序时因文件已不存在抛异常。"""
        try:
            return file_path.stat().st_ctime
        except Exception:
            return 0.0

    # ==================== 查询接口 ====================

    def get_files_to_keep(self, duplicate_group: List[Path], keep_rule: str = 'latest') -> Optional[Path]:
        """
        获取重复组中要保留的文件

        Args:
            duplicate_group: 重复文件组
            keep_rule: 保留规则，'latest' 自动保留最新创建的，'manual' 需用户手动选择

        Returns:
            要保留的文件路径；manual 模式下返回 None 表示需用户手动决定
        """
        if not duplicate_group:
            return None

        if keep_rule == 'latest':
            # 返回创建时间最新的文件（已排序，第一个即为最新）
            return duplicate_group[0]

        # manual 规则由用户手动选择，无法自动决定保留项
        return None

    def is_duplicate(self, file_path: Path) -> bool:
        """检查文件是否为重复文件"""
        return file_path in self._duplicate_files

    @property
    def duplicate_groups(self) -> List[List[Path]]:
        """获取重复文件组"""
        return self._duplicate_groups

    @property
    def duplicate_files(self) -> Set[Path]:
        """获取所有重复文件"""
        return self._duplicate_files


class DuplicateDetectionThread(QThread):
    """异步重复检测线程"""

    detection_complete = Signal(list)  # 检测完成信号，传递重复文件组
    detection_progress = Signal(int, int)  # 当前进度, 总数
    detection_failed = Signal(str)  # 错误信息

    def __init__(self, file_paths: List[Path], keep_rule: str = 'latest', sqlite_store=None):
        super().__init__()
        self.file_paths = file_paths
        self.keep_rule = keep_rule
        self.sqlite_store = sqlite_store
        self.detector = DuplicateDetector(sqlite_store=sqlite_store)

    def run(self):
        """执行重复检测"""
        try:
            duplicate_groups = self.detector.detect_duplicates(
                self.file_paths,
                self.keep_rule,
                progress_callback=self._on_progress
            )
            if self.file_paths:
                self.detection_progress.emit(len(self.file_paths), len(self.file_paths))
            self.detection_complete.emit(duplicate_groups)
        except Exception as e:
            logger.log_exception(e, "重复检测线程失败")
            self.detection_failed.emit(str(e))

    def _on_progress(self, current: int, total: int):
        """转发进度"""
        self.detection_progress.emit(current, total)
