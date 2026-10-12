"""
Media compression worker thread based on ffmpeg.

与源版本的差异：
- PyQt6 → PySide6（pyqtSignal → Signal）
- ffmpeg 查找走 MediaInfoExtractor.resolve_ffmpeg_binary（内部接工具箱统一
  定位器 toolbox.utils.find_ffmpeg_exe：系统 PATH → 全局设置「FFmpeg 位置」）
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter.constants import SUPPORTED_VIDEO_FORMATS
from image_sorter._log import logger


class MediaCompressThread(QThread):
    """后台媒体压缩线程（图片/视频统一 ffmpeg）。"""

    progress = Signal(str)
    progress_detail = Signal(float, str)  # percent(0~100，-1=时长未知)，speed 文本（如 "1.25x"）
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

            returncode, stderr_text = self._execute_ffmpeg(cmd, is_video, before_info)
            if returncode != 0:
                message = (stderr_text or "").strip()[-2000:] or "ffmpeg 执行失败"
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

    # ==================== ffmpeg 执行与进度 ====================

    def _execute_ffmpeg(self, cmd, is_video: bool, before_info: Optional[Dict]) -> Tuple[int, str]:
        """执行 ffmpeg 命令，返回 (returncode, stderr 文本)。

        视频走 `-progress pipe:1` 流式解析（约每 0.5s 一个进度块），
        逐块发射 progress_detail(percent, speed)；图片转码瞬间完成，直接阻塞执行。
        """
        if not is_video:
            return self._run_plain(cmd)

        total_seconds = self._duration_of(before_info)
        progress_cmd = [cmd[0], "-y", "-progress", "pipe:1", "-nostats"] + cmd[2:]
        returncode, stderr_text = self._run_with_progress(progress_cmd, total_seconds)
        if returncode != 0 and "failed to open progress url" in (stderr_text or "").lower():
            # 精简构建 ffmpeg 不含 pipe 协议：去掉进度流重跑（压缩本身不受影响）
            self.progress.emit("当前 ffmpeg 不支持进度输出，已切换普通模式...")
            return self._run_plain(cmd)
        return returncode, stderr_text

    @staticmethod
    def _run_plain(cmd) -> Tuple[int, str]:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        return completed.returncode, (completed.stderr or "") + (completed.stdout or "")

    def _run_with_progress(self, cmd, total_seconds: float) -> Tuple[int, str]:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=creationflags,
        )

        # stderr 由独立线程持续排空，防止管道写满导致 ffmpeg 卡死
        stderr_lines: list = []

        def _drain_stderr():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except Exception:
                pass

        drain = threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()

        try:
            out_time_us = 0
            speed_text = ""
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("out_time_us="):
                    value = self._to_int(line.split("=", 1)[1])
                    if value is not None:
                        out_time_us = value
                elif line.startswith("out_time="):
                    # 兜底：旧版 ffmpeg 无 out_time_us，解析 HH:MM:SS.micro 文本
                    seconds = self._parse_hms(line.split("=", 1)[1])
                    if seconds >= 0:
                        out_time_us = int(seconds * 1_000_000)
                elif line.startswith("speed="):
                    candidate = line.split("=", 1)[1].strip()
                    if candidate and candidate != "N/A":
                        speed_text = candidate
                elif line.startswith("progress="):
                    percent = -1.0
                    if total_seconds > 0 and out_time_us > 0:
                        is_end = line.split("=", 1)[1].strip() == "end"
                        ratio = (out_time_us / 1_000_000.0) / total_seconds
                        percent = min(ratio * 100.0, 100.0 if is_end else 99.9)
                    self.progress_detail.emit(percent, speed_text)
            returncode = proc.wait()
        finally:
            drain.join(timeout=2)
        return returncode, "".join(stderr_lines)

    @staticmethod
    def _duration_of(before_info: Optional[Dict]) -> float:
        if not isinstance(before_info, dict):
            return 0.0
        return max(MediaCompressThread._parse_hms(str(before_info.get("时长", ""))), 0.0)

    @staticmethod
    def _parse_hms(text: str) -> float:
        """解析 HH:MM:SS(.frac) / MM:SS / 纯秒文本，失败返回 -1。"""
        parts = str(text or "").strip().split(":")
        if not parts or len(parts) > 3:
            return -1.0
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(parts[0])
        except ValueError:
            return -1.0

    @staticmethod
    def _to_int(text: str) -> Optional[int]:
        try:
            return int(str(text).strip())
        except (TypeError, ValueError):
            return None

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

        # CPU 占用限制：按逻辑核心百分比换算编码线程数（copy 纯封装不吃 CPU，跳过）
        cpu_limit = int(options.get("cpu_limit_percent", 0) or 0)
        if 0 < cpu_limit < 100 and (not is_video or target_codec != "copy"):
            cores = os.cpu_count() or 4
            threads = max(1, round(cores * cpu_limit / 100.0))
            cmd += ["-threads", str(threads), "-filter_threads", str(threads)]

        cmd.append(str(out))
        return cmd
