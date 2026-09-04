"""
媒体文件信息提取模块
提取图片和视频的元数据信息（格式、分辨率、大小、时长等）

与源版本的差异（去 OpenCV）：
- 视频元数据：cv2.VideoCapture → ffprobe JSON（走工具箱统一定位器
  toolbox.utils.find_ffprobe_exe：系统 PATH → 全局设置「FFmpeg 位置」）
- 视频首帧：ffmpeg 抽帧优先（源实现已有），cv2 回退删除——ffmpeg 不在场时返回 None
- LIVP 解包（zipfile）与 HEIC 解码（pillow-heif）路径原样保留
"""

import os
import io
import json
import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
from PIL import Image
from image_sorter.constants import SIZE_UNITS, SIZE_UNIT_STEP, BITRATE_UNITS, BITRATE_UNIT_STEP
from image_sorter.exceptions import CorruptedFileException
from image_sorter._log import logger

try:
    from toolbox.utils import find_ffmpeg_exe, find_ffprobe_exe
except Exception:  # 工具箱定位器不可用（独立测试场景）→ 回退系统 PATH
    find_ffmpeg_exe = None
    find_ffprobe_exe = None

# HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False
    logger.info("pillow-heif未安装，HEIC格式不受支持")


LIVE_PHOTO_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".bmp"}
LIVE_PHOTO_VIDEO_SUFFIXES = {".mov", ".mp4", ".avi", ".m4v", ".flv"}


class _ProbeUnavailable(Exception):
    """ffprobe 不在场（工具缺失），无法探测视频元数据。"""


class MediaInfoExtractor:
    """媒体文件信息提取器"""

    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        """
        格式化文件大小

        Args:
            size_bytes: 字节大小

        Returns:
            格式化后的大小字符串（如 "2.5MB"）
        """
        size = float(size_bytes)
        for unit in SIZE_UNITS:
            if size < SIZE_UNIT_STEP or unit == SIZE_UNITS[-1]:
                return f"{size:.2f}{unit}"
            size /= SIZE_UNIT_STEP
        return f"{size_bytes}B"

    @staticmethod
    def format_bitrate(bitrate_bps: int) -> str:
        """
        格式化码率

        Args:
            bitrate_bps: 比特率（bps）

        Returns:
            格式化后的码率字符串（如 "1.5Mbps"）
        """
        bitrate = float(bitrate_bps)
        for unit in BITRATE_UNITS:
            if bitrate < BITRATE_UNIT_STEP or unit == BITRATE_UNITS[-1]:
                return f"{bitrate:.0f}{unit}"
            bitrate /= BITRATE_UNIT_STEP
        return f"{bitrate_bps}bps"

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        格式化时长

        Args:
            seconds: 秒

        Returns:
            格式化后的时长字符串（如 "01:20:30"）
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def get_creation_time(file_path: Path) -> str:
        """
        获取文件创建时间

        Args:
            file_path: 文件路径

        Returns:
            格式化的创建时间字符串
        """
        try:
            create_timestamp = os.path.getctime(file_path)
            create_time = datetime.fromtimestamp(create_timestamp)
            return create_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return "未知"

    @staticmethod
    def get_modification_time(file_path: Path) -> str:
        """
        获取文件修改时间
        """
        try:
            mod_timestamp = os.path.getmtime(file_path)
            mod_time = datetime.fromtimestamp(mod_timestamp)
            return mod_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return "未知"

    @staticmethod
    def _extract_exif(img: Image.Image) -> Dict[str, str]:
        """提取图片的 Exif 详细信息"""
        exif_info = {}
        try:
            # 获取 Exif 数据
            exif_data = img.getexif()
            if not exif_data:
                return exif_info

            # 先拿基础项 (0x010f: Make, 0x0110: Model, 0x0131: Software)
            base_tags = {
                0x010f: "制造商",
                0x0110: "相机型号",
                0x0131: "软件",
            }
            for tag_id, label in base_tags.items():
                if tag_id in exif_data:
                    exif_info[label] = str(exif_data[tag_id]).strip()

            # 拿子项 (拍摄参数，通常在 0x8769 子分支)
            try:
                detailed_exif = exif_data.get_ifd(0x8769)
                detail_tags = {
                    0x9003: "拍摄时间",
                    0x829a: "曝光时间",
                    0x829d: "光圈",
                    0x8827: "ISO",
                    0x920a: "焦距",
                    0xa434: "镜头型号"
                }
                for tag_id, label in detail_tags.items():
                    if tag_id in detailed_exif:
                        val = detailed_exif[tag_id]
                        # 格式化处理
                        if label == "光圈":
                            exif_info[label] = f"f/{float(val):.1f}"
                        elif label == "拍摄时间":
                            exif_info[label] = MediaInfoExtractor._format_exif_datetime(str(val).strip())
                        elif label == "曝光时间":
                            f_val = float(val)
                            if f_val < 1.0 and f_val > 0:
                                exif_info[label] = f"1/{round(1/f_val)} s"
                            else:
                                exif_info[label] = f"{f_val} s"
                        elif label == "焦距":
                            exif_info[label] = f"{float(val):.1f} mm"
                        else:
                            exif_info[label] = str(val).strip()
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"解析Exif失败: {e}")

        return exif_info

    @staticmethod
    def _format_exif_datetime(raw_value: str) -> str:
        """将 Exif 时间格式 `YYYY:MM:DD HH:MM:SS` 标准化为 `YYYY-MM-DD HH:MM:SS`。"""
        raw = (raw_value or "").strip().replace("\x00", "")
        if not raw:
            return ""
        try:
            dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            # 兼容：仅替换日期部分的两个冒号
            if len(raw) >= 10 and raw[4] == ":" and raw[7] == ":":
                return f"{raw[:4]}-{raw[5:7]}-{raw[8:]}"
            return raw

    @staticmethod
    def get_grouping_datetime(file_path: Path) -> Optional[datetime]:
        """获取用于分组展示的时间。

        优先级：Exif 拍摄时间（可用时） > 文件创建时间。
        """

        try:
            suffix = file_path.suffix.lower()
        except Exception:
            suffix = ""

        # 仅对常见图片尝试 Exif
        if suffix in {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".bmp"}:
            try:
                with Image.open(file_path) as img:
                    exif = img.getexif()
                    if exif:
                        try:
                            detailed = exif.get_ifd(0x8769)
                        except Exception:
                            detailed = {}
                        raw = None
                        if isinstance(detailed, dict) and 0x9003 in detailed:
                            raw = str(detailed.get(0x9003) or "").strip()
                        if not raw and 0x9003 in exif:
                            raw = str(exif.get(0x9003) or "").strip()
                        if raw:
                            formatted = MediaInfoExtractor._format_exif_datetime(raw)
                            try:
                                return datetime.strptime(formatted, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
            except Exception:
                pass

        try:
            return datetime.fromtimestamp(os.path.getctime(file_path))
        except Exception:
            return None

    @staticmethod
    def resolve_ffmpeg_binary() -> Optional[str]:
        """查找可用 ffmpeg（工具箱统一定位器：PATH → 全局设置自定义位置）。

        定位器找不到真实文件时返回 None（其内部回退的裸命令名 "ffmpeg" 不返回，
        避免 subprocess FileNotFoundError）。
        """
        try:
            exe = find_ffmpeg_exe() if find_ffmpeg_exe else shutil.which("ffmpeg")
        except Exception:
            return None
        if not exe:
            return None
        if exe in ("ffmpeg", "ffmpeg.exe"):
            return None
        return exe

    @staticmethod
    def resolve_ffprobe_binary() -> Optional[str]:
        """查找可用 ffprobe（工具箱统一定位器；找不到返回 None）。"""
        try:
            exe = find_ffprobe_exe() if find_ffprobe_exe else shutil.which("ffprobe")
        except Exception:
            return None
        if not exe:
            return None
        if exe in ("ffprobe", "ffprobe.exe"):
            return None
        return exe

    @staticmethod
    def _extract_video_first_frame_by_ffmpeg(file_path: Path) -> Optional[Image.Image]:
        """
        使用 ffmpeg 抽关键帧，避免对超大视频走全链路解码。
        """
        ffmpeg_bin = MediaInfoExtractor.resolve_ffmpeg_binary()
        if not ffmpeg_bin:
            return None

        seek_positions = [0.1, 1.0]
        for seek in seek_positions:
            cmd = [
                ffmpeg_bin,
                "-v",
                "error",
                "-ss",
                f"{seek:.1f}",
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-2:flags=lanczos:force_original_aspect_ratio=decrease",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-",
            ]
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20,
                    check=False,
                )
                if result.returncode != 0 or (not result.stdout):
                    continue
                with Image.open(io.BytesIO(result.stdout)) as frame:
                    return frame.convert("RGB").copy()
            except Exception:
                continue
        return None

    @staticmethod
    def extract_image_info(file_path: Path) -> Dict[str, str]:
        """
        提取图片文件信息

        Args:
            file_path: 图片文件路径

        Returns:
            图片信息字典
        """
        try:
            suffix = file_path.suffix.lower()

            exif_data: Dict[str, str] = {}
            if suffix == ".livp":
                live_cover = MediaInfoExtractor.extract_live_photo_cover(file_path)
                if live_cover is None:
                    raise CorruptedFileException(str(file_path))
                width, height = live_cover.size
                format_ = "LIVP"
            else:
                with Image.open(file_path) as img:
                    width, height = img.size
                    format_ = img.format or file_path.suffix[1:].upper()
                    # 一次打开同时提取 Exif，避免二次解码
                    exif_data = MediaInfoExtractor._extract_exif(img)

            file_size = file_path.stat().st_size
            create_time = MediaInfoExtractor.get_creation_time(file_path)
            mod_time = MediaInfoExtractor.get_modification_time(file_path)

            info = {
                '格式': format_,
                '分辨率': f"{width}×{height}",
                '文件大小': MediaInfoExtractor.format_file_size(file_size),
                '创建时间': create_time,
                '修改时间': mod_time
            }

            # 注入额外信息（Exif等）
            if exif_data:
                info.update(exif_data)

            return info
        except Exception as e:
            logger.log_file_corrupted(str(file_path), str(e))
            raise CorruptedFileException(str(file_path))

    @staticmethod
    def extract_live_photo_cover(file_path: Path) -> Optional[Image.Image]:
        """
        提取 LIVP 实况图片封面（优先图片，其次视频首帧）

        Args:
            file_path: LIVP 文件路径

        Returns:
            PIL Image 对象，失败返回 None
        """
        try:
            if file_path.suffix.lower() != ".livp":
                return None

            with zipfile.ZipFile(file_path, "r") as archive:
                entries = [
                    name for name in archive.namelist()
                    if name and not name.endswith("/")
                ]
                image_entry = MediaInfoExtractor._find_archive_entry(entries, LIVE_PHOTO_IMAGE_SUFFIXES)
                if image_entry:
                    with archive.open(image_entry, "r") as f:
                        data = f.read()
                    with Image.open(io.BytesIO(data)) as img:
                        return img.convert("RGB").copy()

                video_entry = MediaInfoExtractor._find_archive_entry(entries, LIVE_PHOTO_VIDEO_SUFFIXES)
                if video_entry:
                    with archive.open(video_entry, "r") as f:
                        video_data = f.read()

                    temp_path = None
                    try:
                        suffix = Path(video_entry).suffix or ".mp4"
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                            temp_file.write(video_data)
                            temp_path = Path(temp_file.name)

                        frame = MediaInfoExtractor.extract_video_first_frame(temp_path)
                        if frame is not None:
                            return frame.convert("RGB")
                    finally:
                        if temp_path and temp_path.exists():
                            try:
                                temp_path.unlink()
                            except Exception:
                                pass
        except zipfile.BadZipFile:
            logger.error(f"LIVP文件损坏或格式不支持: {file_path}")
        except Exception as e:
            logger.log_exception(e, f"提取LIVP封面失败: {file_path}")

        return None

    @staticmethod
    def extract_live_photo_video(file_path: Path, output_path: Path) -> Optional[Path]:
        """从 LIVP 提取视频部分到指定路径。"""

        try:
            if file_path.suffix.lower() != ".livp":
                return None

            with zipfile.ZipFile(file_path, "r") as archive:
                entries = [
                    name for name in archive.namelist()
                    if name and not name.endswith("/")
                ]
                video_entry = MediaInfoExtractor._find_archive_entry(entries, LIVE_PHOTO_VIDEO_SUFFIXES)
                if not video_entry:
                    return None

                video_suffix = (Path(video_entry).suffix or output_path.suffix or ".mp4").lower()
                if output_path.suffix.lower() != video_suffix:
                    output_path = output_path.with_suffix(video_suffix)

                output_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(video_entry, "r") as src, open(output_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                return output_path
        except zipfile.BadZipFile:
            logger.error(f"LIVP文件损坏或格式不支持: {file_path}")
        except Exception as e:
            logger.log_exception(e, f"提取LIVP视频失败: {file_path}")

        return None

    @staticmethod
    def _find_archive_entry(entries: list, allowed_suffixes: set) -> Optional[str]:
        """在压缩包条目中查找首个指定后缀文件"""
        for name in entries:
            suffix = Path(name).suffix.lower()
            if suffix in allowed_suffixes:
                return name
        return None

    @staticmethod
    def _probe_video_meta(file_path: Path) -> Optional[dict]:
        """ffprobe 提取视频元数据。

        Returns:
            {codec, width, height, fps, duration, bitrate}；无视频流/解析失败返回 None

        Raises:
            _ProbeUnavailable: ffprobe 不在场
        """
        ffprobe_bin = MediaInfoExtractor.resolve_ffprobe_binary()
        if not ffprobe_bin:
            raise _ProbeUnavailable()

        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
        except Exception as e:
            logger.log_exception(e, f"ffprobe探测失败: {file_path}")
            return None

        if result.returncode != 0 or not result.stdout:
            return None

        try:
            data = json.loads(result.stdout.decode("utf-8", "ignore"))
        except Exception:
            return None

        streams = data.get("streams") or []
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if video_stream is None:
            return None

        fmt = data.get("format") or {}

        def _parse_fps(raw) -> float:
            try:
                text = str(raw or "").strip()
                if not text:
                    return 0.0
                if "/" in text:
                    num, den = text.split("/", 1)
                    num_f, den_f = float(num), float(den)
                    return num_f / den_f if den_f else 0.0
                return float(text)
            except Exception:
                return 0.0

        try:
            duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        try:
            bitrate = int(fmt.get("bit_rate") or 0)
        except (TypeError, ValueError):
            bitrate = 0

        return {
            "codec": str(video_stream.get("codec_name") or "未知"),
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "fps": _parse_fps(video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate")),
            "duration": duration,
            "bitrate": bitrate,
        }

    @staticmethod
    def extract_video_info(file_path: Path) -> Dict[str, str]:
        """
        提取视频文件信息（ffprobe）

        Args:
            file_path: 视频文件路径

        Returns:
            视频信息字典

        Raises:
            CorruptedFileException: ffprobe 在场但探测失败（视为损坏文件）
        """
        try:
            probe_available = True
            meta = None
            try:
                meta = MediaInfoExtractor._probe_video_meta(file_path)
            except _ProbeUnavailable:
                probe_available = False

            # ffprobe 在场但探测失败 / 无视频流 → 损坏
            if probe_available and meta is None:
                raise CorruptedFileException(str(file_path))

            file_size = file_path.stat().st_size
            info = {
                '文件大小': MediaInfoExtractor.format_file_size(file_size),
                '创建时间': MediaInfoExtractor.get_creation_time(file_path),
                '修改时间': MediaInfoExtractor.get_modification_time(file_path),
            }

            if meta is None:
                # ffprobe 不在场：降级返回基础信息（不判损坏，由 UI 提示补装）
                info['格式'] = file_path.suffix[1:].upper()
                return info

            duration = meta["duration"]
            bitrate_int = int(meta["bitrate"]) if meta["bitrate"] > 0 else 0
            if bitrate_int <= 0 and duration > 0:
                # 取不到码率：用文件大小估算
                bitrate_int = int(max(file_size * 8 / duration, 0))

            info['格式'] = file_path.suffix[1:].upper()
            info['编码'] = meta["codec"]
            info['分辨率'] = f"{meta['width']}×{meta['height']}"
            info['帧率'] = f"{meta['fps']:.2f} fps"
            info['时长'] = MediaInfoExtractor.format_duration(duration)
            if bitrate_int > 0:
                info['码率'] = MediaInfoExtractor.format_bitrate(bitrate_int)

            return info

        except CorruptedFileException:
            raise
        except Exception as e:
            logger.log_file_corrupted(str(file_path), str(e))
            raise CorruptedFileException(str(file_path))

    @staticmethod
    def extract_video_first_frame(file_path: Path) -> Optional[Image.Image]:
        """
        提取视频第一帧作为封面（ffmpeg 抽帧；不依赖 OpenCV）

        Args:
            file_path: 视频文件路径

        Returns:
            PIL Image对象，失败返回None
        """
        try:
            return MediaInfoExtractor._extract_video_first_frame_by_ffmpeg(file_path)
        except Exception as e:
            logger.log_exception(e, f"提取视频首帧失败: {file_path}")
            return None
