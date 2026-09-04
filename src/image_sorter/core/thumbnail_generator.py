"""
缩略图生成模块
为图片和视频生成缩略图，支持异步加载

与源版本的差异：仅 PyQt6 → PySide6（pyqtSignal → Signal）；
视频首帧由 media_info_extractor 的 ffmpeg 抽帧提供（去 OpenCV 在该层已完成）。
"""

from pathlib import Path
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Optional, TYPE_CHECKING
from PIL import Image
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from image_sorter.constants import THUMBNAIL_SIZE, SUPPORTED_IMAGE_FORMATS, SUPPORTED_VIDEO_FORMATS
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter._log import logger

if TYPE_CHECKING:
    from image_sorter.core.cache_manager import CacheManager


class ThumbnailGenerator:
    """缩略图生成器"""

    @staticmethod
    def generate_thumbnail(file_path: Path) -> Optional[QPixmap]:
        """
        生成文件缩略图

        Args:
            file_path: 文件路径

        Returns:
            QPixmap缩略图对象，失败返回None
        """
        try:
            suffix = file_path.suffix.lower()

            if suffix in SUPPORTED_IMAGE_FORMATS:
                return ThumbnailGenerator._generate_image_thumbnail(file_path)
            elif suffix in SUPPORTED_VIDEO_FORMATS:
                return ThumbnailGenerator._generate_video_thumbnail(file_path)

            return None
        except Exception as e:
            logger.log_exception(e, f"生成缩略图失败: {file_path}")
            return None

    @staticmethod
    def _generate_image_thumbnail(file_path: Path) -> Optional[QPixmap]:
        """生成图片缩略图（1:1正方形）"""
        try:
            suffix = file_path.suffix.lower()
            if suffix == ".livp":
                live_cover = MediaInfoExtractor.extract_live_photo_cover(file_path)
                if live_cover is None:
                    return None
                img = live_cover
            else:
                with Image.open(file_path) as opened:
                    img = opened.copy()

            # 转换为RGB模式（处理RGBA等）
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # 生成1:1正方形缩略图（居中裁剪）
            square_img = ThumbnailGenerator._make_square(img, THUMBNAIL_SIZE[0])

            # 转换为QPixmap
            return ThumbnailGenerator._pil_to_qpixmap(square_img)
        except Exception as e:
            logger.log_exception(e, f"生成图片缩略图失败: {file_path}")
            return None

    @staticmethod
    def _generate_video_thumbnail(file_path: Path) -> Optional[QPixmap]:
        """生成视频缩略图（第一帧，1:1正方形）"""
        try:
            # 提取视频第一帧
            first_frame = MediaInfoExtractor.extract_video_first_frame(file_path)

            if first_frame is None:
                return None

            # 生成1:1正方形缩略图（居中裁剪）
            square_img = ThumbnailGenerator._make_square(first_frame, THUMBNAIL_SIZE[0])

            # 转换为QPixmap
            return ThumbnailGenerator._pil_to_qpixmap(square_img)
        except Exception as e:
            logger.log_exception(e, f"生成视频缩略图失败: {file_path}")
            return None

    @staticmethod
    def _make_square(img: Image.Image, size: int) -> Image.Image:
        """
        将图片裁剪为正方形

        Args:
            img: PIL Image对象
            size: 目标正方形边长

        Returns:
            裁剪后的正方形图片
        """
        # 获取原始尺寸
        width, height = img.size

        # 计算裁剪区域（居中裁剪）
        if width > height:
            # 宽图，裁剪左右
            left = (width - height) // 2
            top = 0
            right = left + height
            bottom = height
        else:
            # 高图，裁剪上下
            left = 0
            top = (height - width) // 2
            right = width
            bottom = top + width

        # 裁剪为正方形
        square_img = img.crop((left, top, right, bottom))

        # 缩放到目标大小
        square_img = square_img.resize((size, size), Image.Resampling.LANCZOS)

        return square_img

    @staticmethod
    def _pil_to_qpixmap(pil_image: Image.Image) -> QPixmap:
        """
        将PIL Image转换为QPixmap

        Args:
            pil_image: PIL Image对象

        Returns:
            QPixmap对象
        """
        # PIL Image -> bytes
        img_bytes = pil_image.tobytes('raw', 'RGB')

        # 创建QImage
        qimage = QImage(
            img_bytes,
            pil_image.width,
            pil_image.height,
            pil_image.width * 3,  # bytesPerLine
            QImage.Format_RGB888
        )

        # QImage -> QPixmap
        return QPixmap.fromImage(qimage.copy())


class ThumbnailLoader(QThread):
    """异步缩略图加载器"""

    thumbnail_loaded = Signal(Path, QPixmap)  # 加载完成信号
    load_failed = Signal(Path)  # 加载失败信号
    progress_changed = Signal(int, int)  # 当前进度, 总数

    def __init__(self, file_paths: list, cache_manager: Optional['CacheManager'] = None):
        super().__init__()
        self.file_paths = file_paths
        self._is_running = True
        self.cache_manager = cache_manager
        self._executor: Optional[ThreadPoolExecutor] = None

    def run(self):
        """异步加载缩略图"""
        total = len(self.file_paths)
        if total == 0:
            self.progress_changed.emit(0, 0)
            return

        cpu_count = os.cpu_count() or 4
        # 缩略图解码属于重 I/O + 重 CPU，过高并发会导致主线程交互卡顿
        max_workers = max(2, min(6, cpu_count))
        max_in_flight = max_workers * 4
        completed = 0

        try:
            self._executor = ThreadPoolExecutor(max_workers=max_workers)
            file_iter = iter(self.file_paths)
            futures = {}

            # 先填满在途任务池，避免一次性创建海量Future导致开关卡顿
            while len(futures) < max_in_flight and self._is_running:
                try:
                    file_path = next(file_iter)
                except StopIteration:
                    break
                future = self._executor.submit(self._load_single_thumbnail, file_path)
                futures[future] = file_path

            while futures and self._is_running:
                done, _ = wait(
                    futures.keys(),
                    timeout=0.25,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue

                for future in done:
                    if not self._is_running:
                        break
                    file_path = futures.pop(future, None)
                    if file_path is None:
                        continue

                    thumbnail = None
                    try:
                        thumbnail = future.result()
                    except Exception as e:
                        logger.log_exception(e, f"异步缩略图任务失败: {file_path}")

                    if thumbnail:
                        self.thumbnail_loaded.emit(file_path, thumbnail)
                    else:
                        self.load_failed.emit(file_path)

                    completed += 1
                    self.progress_changed.emit(completed, total)

                    if self._is_running:
                        try:
                            next_file = next(file_iter)
                        except StopIteration:
                            continue
                        next_future = self._executor.submit(self._load_single_thumbnail, next_file)
                        futures[next_future] = next_file
        finally:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    def stop(self):
        """停止加载"""
        self._is_running = False
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _load_single_thumbnail(self, file_path: Path) -> Optional[QPixmap]:
        """单文件缩略图加载任务（线程池执行）"""
        if not self._is_running:
            return None
        cached_thumbnail = None
        if self.cache_manager is not None:
            cached_thumbnail = self.cache_manager.load_thumbnail(file_path)
            if cached_thumbnail:
                return cached_thumbnail

        if not self._is_running:
            return None
        thumbnail = ThumbnailGenerator.generate_thumbnail(file_path)
        if not self._is_running:
            return None
        if thumbnail and self.cache_manager is not None:
            self.cache_manager.save_thumbnail(file_path, thumbnail)
        return thumbnail
