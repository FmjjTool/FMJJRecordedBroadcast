"""实用工具 · 媒体压缩页：图片/视频批量压缩（原媒体筛选页压缩标签页独立为工具）。

左：待压缩文件列表（复选框 + 浏览/拖放导入，不改全局工作目录）
右：图片/视频两套压缩参数（多档预设 + 九项参数 + 大小预估）+ 结果区
压缩经后台 MediaCompressThread 逐个执行（ffmpeg 统一定位器），输出到源文件同目录。
"""

import os
import re
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

from image_sorter import settings as cfg
from image_sorter.constants import SUPPORTED_IMAGE_FORMATS, SUPPORTED_VIDEO_FORMATS
from image_sorter.core.cache_manager import CacheManager
from image_sorter.core.media_compress_thread import MediaCompressThread
from image_sorter.core.media_hash_manager import MediaHashManager
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter._log import logger

PRESETS_IMAGE: Dict[str, Dict[str, Any]] = {
    "高质量": {
        "compression_ratio": 20,
        "quality": 90,
        "resolution": "保持原始",
        "codec": "libwebp",
        "target_type": "webp",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": False,
    },
    "均衡(推荐)": {
        "compression_ratio": 38,
        "quality": 78,
        "resolution": "1920x1080",
        "codec": "libwebp",
        "target_type": "webp",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": True,
    },
    "极限压缩": {
        "compression_ratio": 70,
        "quality": 48,
        "resolution": "1280x720",
        "codec": "libaom-av1",
        "target_type": "avif",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": True,
    },
    "无损优先": {
        "compression_ratio": 5,
        "quality": 98,
        "resolution": "保持原始",
        "codec": "png",
        "target_type": "png",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": False,
    },
    "自定义": {},
}

PRESETS_VIDEO: Dict[str, Dict[str, Any]] = {
    "高质量": {
        "compression_ratio": 20,
        "quality": 90,
        "resolution": "保持原始",
        "codec": "libx265",
        "target_type": "keep",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": False,
    },
    "均衡(推荐)": {
        "compression_ratio": 35,
        "quality": 75,
        "resolution": "1920x1080",
        "codec": "libx264",
        "target_type": "mp4",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": True,
    },
    "极限压缩": {
        "compression_ratio": 65,
        "quality": 45,
        "resolution": "1280x720",
        "codec": "libx265",
        "target_type": "mp4",
        "fps": 24,
        "bitrate_kbps": 1200,
        "clear_metadata": True,
    },
    "无损优先": {
        "compression_ratio": 5,
        "quality": 98,
        "resolution": "保持原始",
        "codec": "copy",
        "target_type": "keep",
        "fps": 0,
        "bitrate_kbps": 0,
        "clear_metadata": False,
    },
    "自定义": {},
}

CODECS_IMAGE = ["auto", "libwebp", "libaom-av1", "mjpeg", "png"]
CODECS_VIDEO = ["auto", "libx264", "libx265", "libvpx-vp9", "mpeg4", "copy"]
TYPES_IMAGE = ["keep", "jpg", "png", "webp", "avif"]
TYPES_VIDEO = ["keep", "mp4", "mov", "mkv", "webm", "avi", "flv"]
RESOLUTION_ITEMS = [
    "保持原始", "3840x2160", "2560x1440", "1920x1080",
    "1280x720", "854x480", "640x360",
]


def _format_bytes(size_value: int) -> str:
    size = float(max(int(size_value), 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    return f"{size:.2f}{units[unit_index]}"


def _parse_resolution_text(value: str) -> Optional[Tuple[int, int]]:
    text = str(value or "").strip().lower()
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(re.sub(r"[^\d]", "", left))
        height = int(re.sub(r"[^\d]", "", right))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _parse_duration_seconds(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if ":" not in text:
        try:
            return max(float(text), 0.0)
        except Exception:
            return 0.0
    parts = text.split(":")
    try:
        if len(parts) == 2:
            mm, ss = int(parts[0]), float(parts[1])
            return max(mm * 60 + ss, 0.0)
        if len(parts) == 3:
            hh, mm, ss = int(parts[0]), int(parts[1]), float(parts[2])
            return max(hh * 3600 + mm * 60 + ss, 0.0)
    except Exception:
        return 0.0
    return 0.0


def _parse_fps_value(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return 0.0
    try:
        return max(float(m.group(1)), 0.0)
    except Exception:
        return 0.0


def _estimate_size_bytes(
    media_type: str, src_size: int, options: Dict[str, Any], info: Dict[str, str]
) -> Optional[int]:
    """按参数与源文件信息预估压缩后大小（仅参考，实际以 ffmpeg 输出为准）。"""
    if src_size <= 0:
        return None

    compression_ratio = max(0, min(int(options.get("compression_ratio", 35)), 90))
    quality = max(1, min(int(options.get("quality", 75)), 100))
    resolution = str(options.get("resolution", "保持原始")).strip()
    codec = str(options.get("codec", "auto")).strip().lower()
    target_type = str(options.get("target_type", "keep")).strip().lower()
    fps = max(0, int(options.get("fps", 0) or 0))
    bitrate_kbps = max(0, int(options.get("bitrate_kbps", 0) or 0))
    clear_metadata = bool(options.get("clear_metadata", False))

    source_resolution = _parse_resolution_text(info.get("分辨率", ""))
    target_resolution = _parse_resolution_text(resolution)
    area_scale = 1.0
    if source_resolution and target_resolution:
        src_area = max(source_resolution[0] * source_resolution[1], 1)
        tgt_area = max(
            min(target_resolution[0], source_resolution[0]) *
            min(target_resolution[1], source_resolution[1]),
            1,
        )
        area_scale = min(1.0, tgt_area / float(src_area))

    estimate = float(src_size)
    if media_type != "video":
        ratio_factor = 1.0 - (compression_ratio / 100.0) * 0.75
        quality_factor = 0.55 + (quality / 100.0) * 0.55
        area_factor = 0.35 + (area_scale ** 0.9) * 0.65
        type_factor_map = {
            "keep": 1.0, "jpg": 0.95, "jpeg": 0.95,
            "png": 1.20, "webp": 0.76, "avif": 0.62,
        }
        codec_factor_map = {
            "auto": 1.0, "mjpeg": 0.96, "png": 1.15,
            "libwebp": 0.78, "libaom-av1": 0.70,
        }
        estimate *= ratio_factor * quality_factor * area_factor
        estimate *= type_factor_map.get(target_type, 1.0)
        estimate *= codec_factor_map.get(codec, 1.0)
        if clear_metadata:
            estimate *= 0.985
        min_size = int(src_size * 0.02)
        max_size = int(src_size * 1.4)
    else:
        ratio_factor = 1.0 - (compression_ratio / 100.0) * 0.70
        quality_factor = 0.50 + (quality / 100.0) * 0.60
        area_factor = 0.40 + (area_scale ** 0.7) * 0.60
        fps_factor = 1.0
        source_fps = _parse_fps_value(info.get("帧率", ""))
        if fps > 0 and source_fps > 0:
            fps_factor = max(0.35, min(float(fps) / source_fps, 1.25))

        codec_factor_map = {
            "auto": 0.95, "libx264": 0.95, "libx265": 0.80,
            "libvpx-vp9": 0.76, "mpeg4": 1.15, "copy": 1.0,
        }
        type_factor_map = {
            "keep": 1.0, "mp4": 0.97, "mov": 1.03, "mkv": 0.96,
            "webm": 0.90, "avi": 1.12, "flv": 1.08,
        }
        estimate *= ratio_factor * quality_factor * area_factor * fps_factor
        estimate *= codec_factor_map.get(codec, 1.0)
        estimate *= type_factor_map.get(target_type, 1.0)

        duration_seconds = _parse_duration_seconds(info.get("时长", ""))
        if bitrate_kbps > 0 and duration_seconds > 0:
            bitrate_estimate = (bitrate_kbps * 1000.0 / 8.0) * duration_seconds * 1.12
            estimate = min(estimate, bitrate_estimate)

        if clear_metadata:
            estimate *= 0.997
        min_size = int(src_size * 0.05)
        max_size = int(src_size * 1.6)

    return int(max(min_size, min(max_size, estimate)))


def _media_type_of(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_VIDEO_FORMATS:
        return "video"
    if suffix in SUPPORTED_IMAGE_FORMATS:
        return "image"
    return None


class _ParamGroup(QWidget):
    """一套媒体压缩参数组（预设 + 参数控件 + 大小预估）。"""

    def __init__(self, media_type: str, on_params_changed=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.media_type = media_type
        self.is_video = media_type == "video"
        self.presets = PRESETS_VIDEO if self.is_video else PRESETS_IMAGE
        self._applying = False
        self._on_params_changed = on_params_changed

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        box = QGroupBox("🎬 视频压缩参数" if self.is_video else "🖼 图片压缩参数", self)
        outer.addWidget(box)

        body = QVBoxLayout(box)
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(8)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        def _field_label(text: str, tip: str = "") -> QLabel:
            lab = QLabel(text)
            lab.setStyleSheet("font-weight: 600; color: #5a6474;")
            if tip:
                lab.setToolTip(tip)
            return lab

        row = 0
        preset_label = "视频压缩预设" if self.is_video else "图片压缩预设"
        form.addWidget(_field_label(preset_label, "切换预设自动应用参数"), row, 0)
        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(self.presets.keys()))
        preset_row.addWidget(self.preset_combo, 1)
        apply_btn = QPushButton("应用预设")
        apply_btn.clicked.connect(lambda: self.apply_preset(self.preset_combo.currentText()))
        preset_row.addWidget(apply_btn)
        form.addLayout(preset_row, row, 1)
        row += 1

        form.addWidget(_field_label("压缩率(%)", "目标压缩率越高，体积越小，画质通常越低"), row, 0)
        self.ratio_spin = QSpinBox()
        self.ratio_spin.setRange(0, 90)
        self.ratio_spin.setValue(35)
        form.addWidget(self.ratio_spin, row, 1)
        row += 1

        form.addWidget(_field_label("清晰度", "值越高画质越好，体积通常越大"), row, 0)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(75)
        form.addWidget(self.quality_slider, row, 1)
        row += 1

        form.addWidget(_field_label("分辨率"), row, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTION_ITEMS)
        form.addWidget(self.resolution_combo, row, 1)
        row += 1

        form.addWidget(_field_label("编码"), row, 0)
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(CODECS_VIDEO if self.is_video else CODECS_IMAGE)
        form.addWidget(self.codec_combo, row, 1)
        row += 1

        form.addWidget(_field_label("文件类型"), row, 0)
        self.type_combo = QComboBox()
        self.type_combo.addItems(TYPES_VIDEO if self.is_video else TYPES_IMAGE)
        form.addWidget(self.type_combo, row, 1)
        row += 1

        if self.is_video:
            form.addWidget(_field_label("帧率(FPS,0=不改)"), row, 0)
            self.fps_spin = QSpinBox()
            self.fps_spin.setRange(0, 240)
            self.fps_spin.setValue(0)
            form.addWidget(self.fps_spin, row, 1)
            row += 1

            form.addWidget(_field_label("视频码率(kbps,0=自动)"), row, 0)
            self.bitrate_spin = QSpinBox()
            self.bitrate_spin.setRange(0, 300000)
            self.bitrate_spin.setValue(0)
            form.addWidget(self.bitrate_spin, row, 1)
            row += 1

        self.strip_metadata_cb = QCheckBox("清除元数据")
        self.strip_metadata_cb.setChecked(True)
        form.addWidget(self.strip_metadata_cb, row, 0, 1, 2)

        body.addLayout(form)

        self.preview_label = QLabel("大小预估: -")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("color: #6b7280;")
        body.addWidget(self.preview_label)

        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        self.ratio_spin.valueChanged.connect(self._on_manual_changed)
        self.quality_slider.valueChanged.connect(self._on_manual_changed)
        self.resolution_combo.currentTextChanged.connect(self._on_manual_changed)
        self.codec_combo.currentTextChanged.connect(self._on_manual_changed)
        self.type_combo.currentTextChanged.connect(self._on_manual_changed)
        if self.is_video:
            self.fps_spin.valueChanged.connect(self._on_manual_changed)
            self.bitrate_spin.valueChanged.connect(self._on_manual_changed)
        self.strip_metadata_cb.toggled.connect(self._on_manual_changed)

    # ---------- 预设与参数 ----------

    def _set_combo_text_if_exists(self, combo: QComboBox, text: str):
        target = str(text or "").strip()
        if not target:
            return
        index = combo.findText(target)
        if index >= 0 and index != combo.currentIndex():
            combo.setCurrentIndex(index)

    def _on_preset_changed(self, preset_name: str):
        if str(preset_name or "").strip() == "自定义":
            return
        self.apply_preset(preset_name)
        self._notify_params_changed()

    def _on_manual_changed(self, *_args):
        """用户手动改参数后，将预设标记为自定义。"""
        if self._applying:
            return
        if str(self.preset_combo.currentText()).strip() != "自定义":
            self._set_combo_text_if_exists(self.preset_combo, "自定义")
        self._notify_params_changed()

    def _notify_params_changed(self):
        if self._on_params_changed:
            try:
                self._on_params_changed()
            except Exception:
                pass

    def apply_preset(self, preset_name: str = ""):
        """应用压缩预设到参数控件。"""
        preset = str(preset_name or self.preset_combo.currentText() or "").strip()
        values = dict(self.presets.get(preset, {}))
        if not values:
            return
        self._applying = True
        try:
            self.ratio_spin.setValue(int(values.get("compression_ratio", self.ratio_spin.value())))
            self.quality_slider.setValue(int(values.get("quality", self.quality_slider.value())))
            self._set_combo_text_if_exists(self.resolution_combo, str(values.get("resolution", "")))
            self._set_combo_text_if_exists(self.codec_combo, str(values.get("codec", "")))
            self._set_combo_text_if_exists(self.type_combo, str(values.get("target_type", "")))
            if self.is_video:
                self.fps_spin.setValue(int(values.get("fps", self.fps_spin.value())))
                self.bitrate_spin.setValue(int(values.get("bitrate_kbps", self.bitrate_spin.value())))
            self.strip_metadata_cb.setChecked(bool(values.get("clear_metadata", self.strip_metadata_cb.isChecked())))
        finally:
            self._applying = False

    def restore(self, preset_name: str, options: Dict[str, Any]):
        """恢复持久化的预设和参数（启动时调用）。"""
        target_preset = str(preset_name or "").strip()
        if target_preset not in self.presets:
            target_preset = "均衡(推荐)" if "均衡(推荐)" in self.presets else list(self.presets.keys())[0]
        self._applying = True
        try:
            index = self.preset_combo.findText(target_preset)
            self.preset_combo.setCurrentIndex(max(index, 0))
        finally:
            self._applying = False

        self.apply_preset(self.preset_combo.currentText())
        if not isinstance(options, dict):
            return

        self._applying = True
        try:
            if "compression_ratio" in options:
                self.ratio_spin.setValue(int(options.get("compression_ratio") or self.ratio_spin.value()))
            if "quality" in options:
                self.quality_slider.setValue(int(options.get("quality") or self.quality_slider.value()))
            if "resolution" in options:
                self._set_combo_text_if_exists(self.resolution_combo, str(options.get("resolution", "")))
            if "codec" in options:
                self._set_combo_text_if_exists(self.codec_combo, str(options.get("codec", "")))
            if "target_type" in options:
                self._set_combo_text_if_exists(self.type_combo, str(options.get("target_type", "")))
            if self.is_video:
                if "fps" in options:
                    self.fps_spin.setValue(int(options.get("fps") or 0))
                if "bitrate_kbps" in options:
                    self.bitrate_spin.setValue(int(options.get("bitrate_kbps") or 0))
            if "clear_metadata" in options:
                self.strip_metadata_cb.setChecked(bool(options.get("clear_metadata")))
        finally:
            self._applying = False

    def current_preset(self) -> str:
        return str(self.preset_combo.currentText()).strip() or "自定义"

    def collect_options(self) -> Dict[str, Any]:
        return {
            "compression_ratio": int(self.ratio_spin.value()),
            "quality": int(self.quality_slider.value()),
            "resolution": str(self.resolution_combo.currentText()).strip(),
            "codec": str(self.codec_combo.currentText()).strip(),
            "target_type": str(self.type_combo.currentText()).strip(),
            "fps": int(self.fps_spin.value()) if self.is_video else 0,
            "bitrate_kbps": int(self.bitrate_spin.value()) if self.is_video else 0,
            "clear_metadata": bool(self.strip_metadata_cb.isChecked()),
        }

    def update_preview(self, src_size: int, info: Dict[str, str]):
        """刷新大小预估（info 为当前选中文件的信息，可为空）。"""
        if src_size <= 0:
            self.preview_label.setText("大小预估: -")
            return
        estimate = _estimate_size_bytes(self.media_type, src_size, self.collect_options(), info)
        if estimate is None:
            self.preview_label.setText(f"大小预估: {_format_bytes(src_size)} -> -")
            return
        delta = estimate - src_size
        delta_ratio = (abs(delta) * 100.0 / src_size) if src_size > 0 else 0.0
        if delta < 0:
            trend = f"预计减少 {delta_ratio:.1f}%"
        elif delta > 0:
            trend = f"预计增加 {delta_ratio:.1f}%"
        else:
            trend = "预计基本不变"
        self.preview_label.setText(
            f"大小预估: {_format_bytes(src_size)} -> {_format_bytes(estimate)} ({trend})"
        )

    def clear_preview(self):
        self.preview_label.setText("大小预估: -")


class MediaCompressPage(BasePage):
    """实用工具 · 媒体压缩页。"""

    def __init__(self, app):
        super().__init__(app)
        self._files: List[Path] = []
        self._queue: Deque[Path] = deque()
        self._thread: Optional[MediaCompressThread] = None
        self._running = False
        self._stop_requested = False
        self._done_ok = 0
        self._done_fail = 0
        self._total_files = 0
        self._done_count = 0
        self._current_task_label = ""
        self._total_before = 0
        self._total_after = 0
        self._info_token = 0
        self.cache_manager = CacheManager()

        self._init_ui()
        self._restore_profiles()

    # ==================== UI ====================

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶栏
        top = QHBoxLayout()
        self.add_files_btn = QPushButton("📁 添加文件")
        self.add_files_btn.clicked.connect(self._on_add_files_clicked)
        top.addWidget(self.add_files_btn)

        self.add_folder_btn = QPushButton("📂 添加文件夹")
        self.add_folder_btn.clicked.connect(self._on_add_folder_clicked)
        top.addWidget(self.add_folder_btn)

        self.select_all_btn = QPushButton("☑️ 全选")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        top.addWidget(self.select_all_btn)

        self.invert_btn = QPushButton("🔄 反选")
        self.invert_btn.clicked.connect(self._invert_checked)
        top.addWidget(self.invert_btn)

        self.clear_btn = QPushButton("🗑️ 清空列表")
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        top.addWidget(self.clear_btn)

        top.addStretch(1)

        self.count_label = QLabel("共 0 个文件")
        self.count_label.setStyleSheet("color: #6b7280;")
        top.addWidget(self.count_label)

        self.start_btn = QPushButton("▶ 压缩勾选")
        self.start_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; border: none;"
            " border-radius: 4px; padding: 6px 16px; font-weight: 600; }"
        )
        self.start_btn.clicked.connect(self._on_start_clicked)
        top.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setToolTip("停止后续任务（正在压缩的文件会完成后结束）")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        top.addWidget(self.stop_btn)
        layout.addLayout(top)

        # 主体：左文件列表 + 右参数区
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self.file_list = QListWidget(self.splitter)
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.file_list.setStyleSheet(
            "QListWidget { background-color: #ffffff; border: 1px solid #d0d6df;"
            " border-radius: 4px; }"
        )
        self.file_list.itemChanged.connect(self._on_item_changed)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)

        right_panel = QWidget(self.splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(right_panel)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { background-color: #ffffff; border: 1px solid #d0d6df; border-radius: 4px; }"
        )
        scroll_body = QWidget(scroll)
        scroll_body.setStyleSheet("background: transparent;")
        scroll_layout = QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(10)
        scroll.setWidget(scroll_body)
        right_layout.addWidget(scroll, 1)

        self.selected_info_label = QLabel("选中文件: -")
        self.selected_info_label.setWordWrap(True)
        self.selected_info_label.setStyleSheet("color: #6b7280;")
        scroll_layout.addWidget(self.selected_info_label)

        self.image_group = _ParamGroup("image", scroll_body)
        scroll_layout.addWidget(self.image_group)

        self.video_group = _ParamGroup("video", scroll_body)
        scroll_layout.addWidget(self.video_group)

        # CPU 占用限制（图片/视频共用，压缩是逐文件串行，一个全局值即可）
        cpu_row = QHBoxLayout()
        cpu_label = QLabel("⚙ CPU 占用限制")
        cpu_label.setStyleSheet("font-weight: 600; color: #5a6474;")
        cpu_label.setToolTip(
            "按逻辑核心百分比限制 ffmpeg 编码线程数，降低压缩时的 CPU 占用。\n"
            "100% = 不限制（默认全速）；建议 50~60% 保持电脑流畅"
        )
        cpu_row.addWidget(cpu_label)

        self.cpu_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_slider.setRange(20, 100)
        self.cpu_slider.setValue(55)
        self.cpu_slider.setToolTip(cpu_label.toolTip())
        self.cpu_slider.valueChanged.connect(self._on_cpu_value_changed)
        self.cpu_slider.sliderReleased.connect(self._on_cpu_released)
        cpu_row.addWidget(self.cpu_slider, 1)

        self.cpu_value_label = QLabel("")
        self.cpu_value_label.setStyleSheet("color: #6b7280;")
        cpu_row.addWidget(self.cpu_value_label)
        scroll_layout.addLayout(cpu_row)

        hint = QLabel("勾选左侧文件后点击「压缩勾选」批量执行；图片/视频分别使用各自参数。预估仅供参考，实际以 ffmpeg 输出为准。")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #8a93a5;")
        scroll_layout.addWidget(hint)

        self.result_edit = QTextEdit(scroll_body)
        self.result_edit.setReadOnly(True)
        self.result_edit.setMinimumHeight(180)
        self.result_edit.setPlaceholderText("压缩结果将在这里显示（每个文件的体积变化与输出文件名）")
        self.result_edit.setStyleSheet(
            "QTextEdit { background-color: #f4f5f7; border: 1px solid #d0d6df; border-radius: 4px;"
            " color: #2f3440; font-size: 12px; }"
        )
        scroll_layout.addWidget(self.result_edit, 1)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)

        # 底部状态
        bottom = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        bottom.addWidget(self.progress_bar, 1)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #6b7280;")
        bottom.addWidget(self.status_label)
        layout.addLayout(bottom)

    # ==================== 配置持久化 ====================

    def _restore_profiles(self):
        try:
            self.image_group.restore(
                cfg.get_str("compress_image_preset", "均衡(推荐)"),
                cfg.get_dict("compress_image_options"),
            )
            self.video_group.restore(
                cfg.get_str("compress_video_preset", "均衡(推荐)"),
                cfg.get_dict("compress_video_options"),
            )
            self.cpu_slider.setValue(max(20, min(cfg.get_int("compress_cpu_limit_percent", 55), 100)))
        except Exception as e:
            logger.log_exception(e, "恢复媒体压缩配置失败")

    def _persist_profiles(self):
        try:
            cfg.set_setting("compress_image_preset", self.image_group.current_preset())
            cfg.set_dict("compress_image_options", self.image_group.collect_options())
            cfg.set_setting("compress_video_preset", self.video_group.current_preset())
            cfg.set_dict("compress_video_options", self.video_group.collect_options())
            cfg.set_setting("compress_cpu_limit_percent", int(self.cpu_slider.value()))
        except Exception as e:
            logger.log_exception(e, "保存媒体压缩配置失败")

    def _on_cpu_value_changed(self, value: int):
        if value >= 100:
            self.cpu_value_label.setText("100%（不限制）")
            return
        cores = os.cpu_count() or 4
        threads = max(1, round(cores * value / 100.0))
        self.cpu_value_label.setText(f"{value}%（约 {threads}/{cores} 线程）")

    def _on_cpu_released(self):
        try:
            cfg.set_setting("compress_cpu_limit_percent", int(self.cpu_slider.value()))
        except Exception:
            pass

    # ==================== 文件列表 ====================

    def _on_add_files_clicked(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要压缩的文件", "", "媒体文件 (*.jpg *.jpeg *.png *.webp *.avif *.bmp *.gif *.heic *.heif *.tif *.tiff *.mp4 *.mov *.mkv *.webm *.avi *.flv *.m4v *.ts)"
        )
        if files:
            self._add_files([Path(f) for f in files])

    def _on_add_folder_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "选择要压缩的文件夹")
        if folder:
            self._add_folder(Path(folder))

    def _on_clear_clicked(self):
        if not self._files:
            return
        if self._running:
            self.app.toast("请先停止压缩任务", "warning")
            return
        self._files = []
        self.file_list.clear()
        self._refresh_count()
        self.selected_info_label.setText("选中文件: -")
        self.image_group.clear_preview()
        self.video_group.clear_preview()

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.file_list.count()):
            self.file_list.blockSignals(True)
            self.file_list.item(row).setCheckState(state)
            self.file_list.blockSignals(False)
        self._refresh_count()

    def _invert_checked(self):
        for row in range(self.file_list.count()):
            self.file_list.blockSignals(True)
            item = self.file_list.item(row)
            item.setCheckState(
                Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            self.file_list.blockSignals(False)
        self._refresh_count()

    def _on_item_changed(self, _item):
        self._refresh_count()

    def _refresh_count(self):
        total = self.file_list.count()
        checked = sum(
            1 for row in range(total)
            if self.file_list.item(row).checkState() == Qt.CheckState.Checked
        )
        self.count_label.setText(f"共 {total} 个文件 · 勾选 {checked} 个")

    def _add_files(self, paths: List[Path]):
        added = 0
        skipped = 0
        known = {str(p.resolve()) for p in self._files}
        for path in paths:
            try:
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key in known or _media_type_of(path) is None:
                    skipped += 1 if key not in known else 0
                    continue
                known.add(key)
            except Exception:
                continue
            self._files.append(path)
            self._append_list_item(path)
            added += 1
        if added:
            self._refresh_count()
        if skipped:
            self.app.toast(f"已跳过 {skipped} 个不支持或重复的文件", "warning")

    def _add_folder(self, folder: Path):
        if not folder.is_dir():
            return
        paths: List[Path] = []
        for root, _dirs, names in os.walk(folder):
            for name in names:
                candidate = Path(root) / name
                if _media_type_of(candidate) is not None:
                    paths.append(candidate)
        if not paths:
            self.app.toast("该文件夹中没有可压缩的媒体文件", "warning")
            return
        self._add_files(paths)

    def _append_list_item(self, path: Path):
        try:
            size_text = _format_bytes(path.stat().st_size)
        except Exception:
            size_text = "?"
        item = QListWidgetItem(f"{path.name}  ({size_text})")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setToolTip(str(path))
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        self.file_list.addItem(item)

    def _item_path(self, item: QListWidgetItem) -> Optional[Path]:
        raw = item.data(Qt.ItemDataRole.UserRole)
        return Path(raw) if raw else None

    # ==================== 选中信息与预估 ====================

    def _on_selection_changed(self):
        items = self.file_list.selectedItems()
        if len(items) != 1:
            self.selected_info_label.setText("选中文件: -（点选单个文件查看信息与预估）")
            self.image_group.clear_preview()
            self.video_group.clear_preview()
            return

        path = self._item_path(items[0])
        if path is None:
            return
        try:
            size = int(path.stat().st_size)
        except Exception:
            size = 0
        self.selected_info_label.setText(f"选中文件: {path.name}（{_format_bytes(size)}）")

        self._info_token += 1
        token = self._info_token

        # 先按文件大小即时给出预估，信息提取完成后在 _on_info_ready 里细化
        media_type = _media_type_of(path)
        if media_type == "image":
            self.image_group.update_preview(size, {})
            self.video_group.clear_preview()
        elif media_type == "video":
            self.video_group.update_preview(size, {})
            self.image_group.clear_preview()

        def _job():
            try:
                media_type = _media_type_of(path)
                if media_type == "video":
                    info = MediaInfoExtractor.extract_video_info(path)
                elif media_type == "image":
                    info = MediaInfoExtractor.extract_image_info(path)
                else:
                    info = {}
                return str(path), dict(info or {})
            except Exception:
                return str(path), {}

        run_bg(_job, lambda result, err: self._on_info_ready(token, result, err, size))

    def _on_info_ready(self, token: int, result, err, size: int):
        if token != self._info_token:
            return
        try:
            path_text, info = result if isinstance(result, tuple) else ("", {})
        except Exception:
            path_text, info = "", {}
        info = info if isinstance(info, dict) else {}

        # 选中项已被切换则忽略
        items = self.file_list.selectedItems()
        if len(items) != 1:
            return
        path = self._item_path(items[0])
        if path is None or str(path) != path_text:
            return

        media_type = _media_type_of(path)
        parts = [f"选中文件: {path.name}（{_format_bytes(size)}）"]
        for key in ("分辨率", "时长", "帧率", "格式"):
            value = str(info.get(key, "")).strip()
            if value and value != "未知":
                parts.append(value)
        self.selected_info_label.setText(" · ".join(parts))

        if media_type == "image":
            self.image_group.update_preview(size, info)
            self.video_group.clear_preview()
        elif media_type == "video":
            self.video_group.update_preview(size, info)
            self.image_group.clear_preview()

    # ==================== 批量压缩 ====================

    def _on_start_clicked(self):
        if self._running:
            return
        checked: List[Path] = []
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                path = self._item_path(item)
                if path is not None and path.exists():
                    checked.append(path)
        if not checked:
            self.app.toast("请先勾选要压缩的文件", "warning")
            return

        self._persist_profiles()
        self._queue = deque(checked)
        self._running = True
        self._stop_requested = False
        self._done_ok = 0
        self._done_fail = 0
        self._total_files = len(checked)
        self._done_count = 0
        self._current_task_label = ""
        self._total_before = 0
        self._total_after = 0
        # 进度条细分到单文件：每个文件占 100 格，压缩中实时推进
        self.progress_bar.setRange(0, len(checked) * 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.clear_btn.setEnabled(False)
        self.add_files_btn.setEnabled(False)
        self.add_folder_btn.setEnabled(False)
        self.result_edit.clear()
        self.result_edit.append(f"[开始] 共 {len(checked)} 个文件待压缩\n")
        self._compress_next()

    def _on_stop_clicked(self):
        if not self._running:
            return
        self._stop_requested = True
        self._queue.clear()
        self.status_label.setText("已请求停止（当前文件完成后结束）")

    def _compress_next(self):
        if self._stop_requested or not self._queue:
            self._finish_batch()
            return

        path = self._queue.popleft()
        media_type = _media_type_of(path)
        if media_type is None:
            self._done_fail += 1
            self.result_edit.append(f"[失败] {path.name}: 不支持的文件类型\n")
            self._done_count += 1
            self.progress_bar.setValue(self._done_count * 100)
            self._compress_next()
            return

        group = self.video_group if media_type == "video" else self.image_group
        options = group.collect_options()
        try:
            src_size = int(path.stat().st_size)
        except Exception:
            src_size = 0
        payload = {
            "file_path": str(path),
            "media_type": media_type,
            "preset": group.current_preset(),
            "cpu_limit_percent": int(self.cpu_slider.value()),
            **options,
            "estimated_after_size": int(
                _estimate_size_bytes(media_type, src_size, options, {}) or 0
            ),
        }

        self._current_task_label = (
            f"正在压缩 {self._done_count + 1}/{self._total_files}: {path.name}"
        )
        self.status_label.setText(self._current_task_label)

        thread = MediaCompressThread(file_path=path, options=payload, parent=self)
        self._thread = thread
        thread.progress.connect(self._on_compress_progress)
        thread.progress_detail.connect(self._on_compress_progress_detail)
        thread.finished_ok.connect(self._on_compress_finished)
        thread.failed.connect(lambda msg, p=path: self._on_compress_failed(p, msg))
        thread.start()

    def _on_compress_progress(self, message: str):
        # 通用阶段消息不覆盖带文件名/百分比的实时状态（视频压缩中由 progress_detail 维护）
        if not message or message == "正在执行压缩...":
            return
        self.status_label.setText(message)

    def _on_compress_progress_detail(self, percent: float, speed: str):
        parts = [self._current_task_label or "正在压缩"]
        if percent >= 0:
            parts.append(f"{percent:.0f}%")
        if speed:
            parts.append(f"速度 {speed}")
        self.status_label.setText(" · ".join(parts))
        if percent >= 0:
            value = int(self._done_count * 100 + percent)
            self.progress_bar.setValue(min(value, self._total_files * 100))

    def _on_compress_finished(self, result: dict):
        try:
            before = int(result.get("before_size", 0) or 0)
            after = int(result.get("after_size", 0) or 0)
            out_path = Path(str(result.get("output_path", "")))
            src_name = Path(str(result.get("source_path", ""))).name
            saved = max(before - after, 0)
            saved_ratio = (saved * 100.0 / before) if before > 0 else 0.0
            self._total_before += before
            self._total_after += after
            self._done_ok += 1
            self.result_edit.append(
                f"[完成] {src_name}: {_format_bytes(before)} -> {_format_bytes(after)}"
                f"（节省 {saved_ratio:.1f}%）  →  {out_path.name}\n"
            )
            self._mark_item(src_name, "✅")
            self._mark_output_classified(out_path)
        finally:
            self._done_count += 1
            self.progress_bar.setValue(self._done_count * 100)
            self._thread = None
            self._compress_next()

    def _on_compress_failed(self, path: Path, error_message: str):
        try:
            self._done_fail += 1
            self.result_edit.append(f"[失败] {path.name}: {error_message}\n")
            self._mark_item(path.name, "❌")
        finally:
            self._done_count += 1
            self.progress_bar.setValue(self._done_count * 100)
            self._thread = None
            self._compress_next()

    def _mark_output_classified(self, output_path: Path):
        """压缩产物写入 MD5 缓存并标记已分类（媒体筛选页不再重复列出）。"""
        try:
            hash_manager = MediaHashManager(sqlite_store=self.cache_manager.sqlite_store)
            md5_value = hash_manager.calculate_md5(output_path)
            if md5_value:
                self.cache_manager.mark_md5_classified_batch({md5_value})
        except Exception as e:
            logger.log_exception(e, f"压缩结果写入MD5缓存失败: {output_path}")
        self._remember_classified_target_folder(output_path.parent)

    def _remember_classified_target_folder(self, target_folder: Path):
        """压缩输出目录位于筛选工具工作目录内时，记为分类库目录（MD5 过滤重启后使用）。"""
        work_dir = cfg.get_str("work_dir", "").strip()
        if not work_dir:
            return
        try:
            resolved = target_folder.resolve()
        except Exception:
            resolved = target_folder.absolute()
        if not self._is_path_in_folder(resolved, Path(work_dir)):
            return
        if not resolved.exists() or not resolved.is_dir():
            return

        existing: List[str] = []
        seen = set()
        for folder_path in cfg.get_list("classified_target_folders"):
            path = Path(folder_path)
            if not path.exists() or not path.is_dir():
                continue
            try:
                p_resolved = path.resolve()
            except Exception:
                p_resolved = path.absolute()
            key = os.path.normcase(os.path.normpath(str(p_resolved)))
            if key in seen:
                continue
            seen.add(key)
            existing.append(str(p_resolved))
        if os.path.normcase(os.path.normpath(str(resolved))) in seen:
            return
        existing.append(str(resolved))
        cfg.set_list("classified_target_folders", existing)

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path.absolute()
        return os.path.normcase(os.path.normpath(str(resolved)))

    @classmethod
    def _is_path_in_folder(cls, file_path: Path, folder_path: Path) -> bool:
        file_key = cls._path_key(file_path)
        folder_key = cls._path_key(folder_path)
        try:
            common = os.path.commonpath([file_key, folder_key])
        except ValueError:
            return False
        return common == folder_key

    def _mark_item(self, file_name: str, prefix: str):
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            text = item.text()
            if text.startswith(("✅ ", "❌ ")):
                text = text[2:]
            if text.split("  (")[0] == file_name:
                item.setText(f"{prefix} {text}")

    def _finish_batch(self):
        self._running = False
        total = self._done_ok + self._done_fail
        if self._total_before > 0:
            saved = max(self._total_before - self._total_after, 0)
            saved_ratio = saved * 100.0 / self._total_before
            summary = (
                f"[汇总] 共 {total} 个：成功 {self._done_ok} · 失败 {self._done_fail} · "
                f"总体积 {_format_bytes(self._total_before)} -> {_format_bytes(self._total_after)}"
                f"（节省 {saved_ratio:.1f}%）"
            )
        else:
            summary = f"[汇总] 共 {total} 个：成功 {self._done_ok} · 失败 {self._done_fail}"
        self.result_edit.append(summary + "\n")
        self.status_label.setText(
            f"完成：成功 {self._done_ok} · 失败 {self._done_fail}"
            if self._done_ok or self._done_fail else "就绪"
        )
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.clear_btn.setEnabled(True)
        self.add_files_btn.setEnabled(True)
        self.add_folder_btn.setEnabled(True)
        if total > 0:
            level = "success" if self._done_fail == 0 else "warning"
            self.app.toast(f"压缩完成：成功 {self._done_ok}，失败 {self._done_fail}", level)

    # ==================== 拖放白名单接口（main_window dropEvent 调用） ====================

    def add_dropped_folder(self, dir_path: str):
        self._add_folder(Path(dir_path))

    def add_dropped_files(self, file_paths: list):
        self._add_files([Path(fp) for fp in file_paths])

    # ==================== 生命周期 ====================

    def shutdown(self):
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(500)
