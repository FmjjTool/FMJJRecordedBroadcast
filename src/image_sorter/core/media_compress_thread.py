"""
Media compression worker thread based on ffmpeg.

与源版本的差异：
- PyQt6 → PySide6（pyqtSignal → Signal）
- ffmpeg 查找走 MediaInfoExtractor.resolve_ffmpeg_binary（内部接工具箱统一
  定位器 toolbox.utils.find_ffmpeg_exe：系统 PATH → 全局设置「FFmpeg 位置」）
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict

from PySide6.QtCore import QThread, Signal

from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter.constants import SUPPORTED_VIDEO_FORMATS
from image_sorter._log import logger


class MediaCompressThread(QThread):
    """后台媒体压缩线程（图片/视频统一 ffmpeg）。"""

    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)
    IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    VIDEO_TYPES = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm"}

    def __init__(self, file_path: Path, options: Dict, parent=None):
        super().__init__(parent)
        self.file_path = Path(file_path)
        self.options = dict(options or {})

    def run(self):
        try:
            if not self.file_path.exists() or not self.file_path.is_file():
                raise RuntimeError("源文件不存在")

            ffmpeg_bin = MediaInfoExtractor.resolve_ffmpeg_binary()
            if not ffmpeg_bin:
                raise RuntimeError("未找到 ffmpeg：可将其加入 PATH，或在全局设置中配置 FFmpeg 位置")

            is_video = self.file_path.suffix.lower() in SUPPORTED_VIDEO_FORMATS
            before_info = (
                MediaInfoExtractor.extract_video_info(self.file_path)
                if is_video else MediaInfoExtractor.extract_image_info(self.file_path)
            )
            before_size = int(self.file_path.stat().st_size)
            self.progress.emit("正在执行压缩...")

            output_path = self._build_output_path(self.file_path, self.options, is_video)
            cmd = self._build_ffmpeg_command(ffmpeg_bin, self.file_path, output_path, is_video, self.options)

            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip() or "ffmpeg 执行失败"
                raise RuntimeError(message)

            if not output_path.exists():
                raise RuntimeError("压缩输出文件不存在")

            after_size = int(output_path.stat().st_size)
            after_info = (
                MediaInfoExtractor.extract_video_info(output_path)
                if is_video else MediaInfoExtractor.extract_image_info(output_path)
            )

            result = {
                "source_path": str(self.file_path),
                "output_path": str(output_path),
                "before_size": before_size,
                "after_size": after_size,
                "before_info": before_info or {},
                "after_info": after_info or {},
                "is_video": bool(is_video),
                "options": self.options,
            }
            self.finished_ok.emit(result)
        except Exception as e:
            logger.log_exception(e, f"媒体压缩失败: {self.file_path}")
            self.failed.emit(str(e))

    @staticmethod
    def _build_output_path(src: Path, options: Dict, is_video: bool) -> Path:
        target_type = str(options.get("target_type", "keep")).strip().lower()
        if target_type in ("keep", "", "auto"):
            ext = src.suffix.lower()
        else:
            ext = target_type if target_type.startswith(".") else f".{target_type}"

        # 跨媒体类型时自动回退到兼容扩展名，避免 ffmpeg 参数冲突
        if is_video and ext in MediaCompressThread.IMAGE_TYPES:
            ext = ".mp4"
        if (not is_video) and ext in MediaCompressThread.VIDEO_TYPES:
            ext = src.suffix.lower()

        out_dir = src.parent
        stem = src.stem
        suffix = "_compressed_video" if is_video else "_compressed_image"
        candidate = out_dir / f"{stem}{suffix}{ext}"
        if not candidate.exists():
            return candidate

        idx = 1
        while True:
            trial = out_dir / f"{stem}{suffix}_{idx}{ext}"
            if not trial.exists():
                return trial
            idx += 1

    @staticmethod
    def _quality_to_crf(quality: int, compression_ratio: int) -> int:
        """将清晰度/压缩率转换为 CRF。"""
        q = max(1, min(int(quality), 100))
        ratio = max(0, min(int(compression_ratio), 90))
        base_crf = 35 - int(q * 0.2)  # q高 -> crf低
        base_crf += int(ratio * 0.15)  # ratio高 -> crf高
        return max(16, min(base_crf, 42))

    @staticmethod
    def _build_scale_filter(resolution_text: str) -> str:
        text = str(resolution_text or "").strip().lower()
        if text in ("", "keep", "保持原始"):
            return ""
        if "x" not in text:
            return ""
        w, h = text.split("x", 1)
        try:
            iw = max(int(w), 2)
            ih = max(int(h), 2)
        except ValueError:
            return ""
        return f"scale={iw}:{ih}:force_original_aspect_ratio=decrease"

    def _build_ffmpeg_command(
        self,
        ffmpeg_bin: str,
        src: Path,
        out: Path,
        is_video: bool,
        options: Dict,
    ):
        codec = str(options.get("codec", "auto")).strip().lower()
        quality = int(options.get("quality", 75) or 75)
        compression_ratio = int(options.get("compression_ratio", 35) or 35)
        resolution = str(options.get("resolution", "keep")).strip()
        fps = int(options.get("fps", 0) or 0)
        clear_metadata = bool(options.get("clear_metadata", True))
        bitrate_kbps = int(options.get("bitrate_kbps", 0) or 0)

        cmd = [ffmpeg_bin, "-y", "-i", str(src)]

        if clear_metadata:
            cmd += ["-map_metadata", "-1"]

        vf = self._build_scale_filter(resolution)
        if is_video:
            target_codec = codec
            if target_codec in ("auto", "", "default"):
                if out.suffix.lower() == ".webm":
                    target_codec = "libvpx-vp9"
                else:
                    target_codec = "libx264"
            if target_codec not in {"libx264", "libx265", "libvpx-vp9", "mpeg4", "copy"}:
                target_codec = "libx264"

            if target_codec != "copy":
                cmd += ["-c:v", target_codec]
                cmd += ["-crf", str(self._quality_to_crf(quality, compression_ratio))]
                if bitrate_kbps > 0:
                    cmd += ["-b:v", f"{bitrate_kbps}k"]
                if fps > 0:
                    cmd += ["-r", str(fps)]
                if vf:
                    cmd += ["-vf", vf]
            else:
                # copy 模式不允许转码参数
                pass

            if out.suffix.lower() == ".webm":
                cmd += ["-c:a", "libopus", "-b:a", "96k"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "128k"]
            if out.suffix.lower() in (".mp4", ".mov"):
                cmd += ["-movflags", "+faststart"]
        else:
            target_codec = codec
            if target_codec in ("auto", "", "default"):
                if out.suffix.lower() in (".jpg", ".jpeg"):
                    target_codec = "mjpeg"
                elif out.suffix.lower() == ".png":
                    target_codec = "png"
                elif out.suffix.lower() == ".avif":
                    target_codec = "libaom-av1"
                else:
                    target_codec = "libwebp"
            if target_codec not in {"mjpeg", "png", "libaom-av1", "libwebp"}:
                target_codec = "libwebp"

            cmd += ["-frames:v", "1", "-c:v", target_codec]
            if target_codec == "mjpeg":
                qv = max(2, min(31, int(33 - (quality * 0.3))))
                cmd += ["-q:v", str(qv)]
            else:
                cmd += ["-q:v", str(max(1, min(quality, 100)))]
            if vf:
                cmd += ["-vf", vf]

        cmd.append(str(out))
        return cmd
