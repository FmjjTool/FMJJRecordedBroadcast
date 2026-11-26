"""视频操作页：批量转换 + 预览 / 合并 / 切割 / 压缩 / 轨道提取 / 截图。

布局：
- 左侧：文件队列（合并顺序，可拖拽/上移下移）
- 右上：内嵌播放器 + 彩色片段时间轴 + 播放指针标记
- 右下：固定页签头，各页独立纵向滚动；旋转在转换页独立导出
- 切割页：片段选择列表、检测与导入导出；元数据字段使用框内提示

合并逻辑与 LC 对齐：concat demuxer + 参数兼容性检查；切割默认无损（关键帧边界），
需要精确时可选重编码。压缩复用 image_sorter 的 MediaCompressThread。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStyle,
    QStyleOptionSlider, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from image_sorter.core.media_compress_thread import MediaCompressThread
from toolbox.concat_engine import (
    MODE_COPY, MODE_ENCODE, check_compatibility, concat_media, find_videos_in,
    probe_media, unique_output_path,
)
from toolbox.config_manager import (
    load_convert_cache, load_convert_config, save_convert_cache, save_convert_config,
)
from toolbox.convert_engine import (
    convert_one_file, find_videos, get_output_path, is_convert_cached, update_convert_cache,
)
from toolbox.date_folders import scan_date_folders
from toolbox.edit_engine import (
    add_external_audio, add_external_subtitle, complement_segments,
    cut_points_from_ranges, cut_segments, detect_black, detect_scenes,
    detect_silence, export_chapters, export_edl, export_segments_csv,
    extract_all_tracks, extract_track, frame_bytes, import_segments_csv,
    probe_tracks, read_metadata, remove_tracks, segments_from_points, snapshot,
    snapshot_every, write_chapters, write_metadata,
)
from toolbox.utils import format_size, is_video_file

from gui_qt import table_sort, theme
from gui_qt.common import FlowLayout, post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage

COMPRESS_PRESETS = {
    "light": ("轻度（画质优先）", 88, 12),
    "normal": ("标准", 75, 35),
    "strong": ("高强度（体积优先）", 60, 62),
}


def _fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    total = ms // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    m, rem = divmod(ms, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{m:02d}:{s:02d}.{milli // 100}"


class _OptionGrid(QWidget):
    """参数组响应式排布；短控件可按内容宽度流式换行。"""

    def __init__(self, widgets, columns=2, cell_width=210, *, compact=False):
        super().__init__()
        self._widgets = widgets
        self._compact = compact
        self._max_columns = columns
        self._cell_width = cell_width
        self._columns = 0
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if compact:
            self._grid = FlowLayout(self, spacing=8, center_items=True)
            for widget in widgets:
                self._grid.addWidget(widget)
            return
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        self._grid.setSizeConstraint(QLayout.SetNoConstraint)
        self._arrange(1)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._compact:
            return
        cell = max(self._cell_width,
                   max((w.minimumSizeHint().width() for w in self._widgets), default=0))
        self._arrange(min(self._max_columns, max(1, (self.width() + 8) // (cell + 8))))

    def _arrange(self, columns):
        if columns == self._columns:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        for column in range(self._max_columns):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, widget in enumerate(self._widgets):
            self._grid.addWidget(widget, index // columns, index % columns)
        self._columns = columns
        self.updateGeometry()


class _SeekSlider(QSlider):
    """点击/拖动播放指针，并在同一时间轴显示片段和待定起点。"""

    clickedAt = Signal(int)
    markerDeleteRequested = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._segments = ()
        self._partial_segments = ()
        self._pending_start = None
        self._selected_index = -1
        self._pointer_dragging = False
        self.valueChanged.connect(lambda _value: self.update())

    def set_segments(self, segments, pending_start=None, selected_index=-1,
                     partial_segments=()):
        self._segments = tuple(segments)
        self._partial_segments = tuple(dict(row) for row in partial_segments)
        self._pending_start = pending_start
        self._selected_index = selected_index
        self.update()

    def _geometry(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        return option, groove, handle

    def _x_at(self, value, option=None, groove=None, handle=None):
        if option is None:
            option, groove, handle = self._geometry()
        span = max(1, groove.width() - handle.width())
        value = max(self.minimum(), min(self.maximum(), int(value)))
        return (groove.left() + handle.width() / 2
                + QStyle.sliderPositionFromValue(
                    self.minimum(), self.maximum(), value, span, option.upsideDown))

    def _value_at(self, x):
        option, groove, handle = self._geometry()
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            int(x - groove.left() - handle.width() / 2),
            max(1, groove.width() - handle.width()), option.upsideDown)

    def mousePressEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            self._pointer_dragging = True
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            self.sliderMoved.emit(self.value())
            self.clickedAt.emit(self.value())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._pointer_dragging:
            self.setValue(self._value_at(event.position().x()))
            self.sliderMoved.emit(self.value())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._pointer_dragging and event.button() == Qt.LeftButton:
            self.setValue(self._value_at(event.position().x()))
            self._pointer_dragging = False
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self.maximum() <= self.minimum():
            return
        option, groove, handle = self._geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(self.rect())
        y = groove.center().y()
        for index, (start, end) in enumerate(self._segments):
            x1, x2 = sorted((self._x_at(start, option, groove, handle),
                             self._x_at(end, option, groove, handle)))
            selected = index == self._selected_index
            fill = QColor(theme.color("accent" if selected else "success"))
            fill.setAlpha(180 if selected else 110)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(x1, y - 4, max(2, x2 - x1), 8), 2, 2)
            if selected:
                for point, role in ((start, "success"), (end, "danger")):
                    painter.setPen(QPen(QColor(theme.color(role)), 1))
                    x = int(self._x_at(point, option, groove, handle))
                    painter.drawLine(x, 2, x, self.height() - 2)
        for row in self._partial_segments:
            for endpoint, role in (("start", "success"), ("end", "danger")):
                point = row.get(endpoint)
                if point is None:
                    continue
                x = int(self._x_at(point, option, groove, handle))
                painter.setPen(QPen(QColor(theme.color(role)), 2))
                painter.drawLine(x, 3, x, self.height() - 3)
                painter.setBrush(QColor(theme.color(role)))
                painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
        if self._pending_start is not None:
            x1, x2 = sorted((self._x_at(self._pending_start, option, groove, handle),
                             self._x_at(self.value(), option, groove, handle)))
            fill = QColor(theme.color("warning"))
            fill.setAlpha(70)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(x1, y - 4, max(2, x2 - x1), 8), 2, 2)
            painter.setPen(QPen(QColor(theme.color("warning")), 1, Qt.DashLine))
            x = int(self._x_at(self._pending_start, option, groove, handle))
            painter.drawLine(x, 2, x, self.height() - 2)
        # 片段底色在手柄下，当前位置始终清楚可见。
        option.subControls = QStyle.SC_SliderHandle
        self.style().drawComplexControl(QStyle.CC_Slider, option, painter, self)

    def contextMenuEvent(self, event):  # noqa: N802
        if self.maximum() <= self.minimum():
            return
        x = event.pos().x()
        hits = []
        for index, (start, end) in enumerate(self._segments):
            for endpoint, point in (("start", start), ("end", end)):
                if abs(self._x_at(point) - x) <= 8:
                    hits.append({"kind": "segment", "index": index,
                                 "endpoint": endpoint,
                                 "label": f"片段 {index + 1} · 删除{'起点' if endpoint == 'start' else '终点'} {_fmt_ms(point)}"})
        for row in self._partial_segments:
            for endpoint in ("start", "end"):
                point = row.get(endpoint)
                if point is not None and abs(self._x_at(point) - x) <= 8:
                    hits.append({"kind": "partial", "id": row["id"],
                                 "endpoint": endpoint,
                                 "label": f"待补标记 · 删除{'起点' if endpoint == 'start' else '终点'} {_fmt_ms(point)}"})
        if self._pending_start is not None and abs(self._x_at(self._pending_start) - x) <= 8:
            hits.append({"kind": "pending", "endpoint": "start",
                         "label": f"当前起点 · 删除 {_fmt_ms(self._pending_start)}"})
        if not hits:
            event.ignore()
            return
        menu = QMenu(self)
        if len(hits) <= 8:
            for hit in hits:
                action = menu.addAction(hit["label"])
                action.triggered.connect(
                    lambda _checked=False, value=hit: self.markerDeleteRequested.emit(value))
        else:
            groups = {}
            for hit in hits:
                key = (("片段 " + str(hit["index"] + 1)) if hit["kind"] == "segment"
                       else ("待补标记" if hit["kind"] == "partial" else "当前起点"))
                groups.setdefault(key, []).append(hit)
            for name, group in groups.items():
                submenu = menu.addMenu(name)
                for hit in group:
                    action = submenu.addAction(hit["label"])
                    action.triggered.connect(
                        lambda _checked=False, value=hit: self.markerDeleteRequested.emit(value))
        menu.exec(event.globalPos())


class EditorPage(BasePage):
    SESSION_FIELDS = ("files", "current_path", "segments", "partial_segments",
                     "_pending_start", "detect_points")

    def restore_session_data(self):
        segments = self.segments
        partial_segments = self.partial_segments
        pending_start = self._pending_start
        points = self.detect_points
        current = self.current_path
        self._refresh_files(self.files.index(current) if current in self.files else 0)
        self.segments = segments
        self.partial_segments = partial_segments
        self._pending_start = pending_start
        self.detect_points = points
        self._refresh_segments()

    def has_active_tasks(self):
        return self.running

    def release_view_resources(self):
        self._release_preview()
        self.video.hide()

    def release_media_file(self, path):
        """其它页面要改名/移动视频前调用：预览占用同一文件时立即松开句柄。"""
        current = self.current_path
        if not current or not path:
            return
        try:
            same = (os.path.normcase(os.path.abspath(current))
                    == os.path.normcase(os.path.abspath(path)))
        except (OSError, ValueError):
            same = current == path
        if same:
            self._release_preview()

    def _release_preview(self):
        """停播并清空媒体源：QMediaPlayer 暂停时仍持有文件句柄，Windows 上会挡住改名。"""
        position = self.player.position()
        if position > 0:
            self._resume_ms = position  # 回来时接着看，不因松开句柄丢进度
        self.player.stop()
        self.player.setSource(QUrl())
        self.play_btn.setText("播放")

    def on_show(self):
        self._sync_folders()
        if self.current_path:
            self.video.show()
            source = QUrl.fromLocalFile(self.current_path)
            if self.player.source() != source:
                self.player.setSource(source)
                if self._resume_ms > 0:
                    self.player.setPosition(self._resume_ms)
                    self.slider.setValue(self._resume_ms)
                    self._update_time_label(self._resume_ms)
                else:
                    self.slider.setValue(0)
                    self._update_time_label(0)
            self.player.pause()

    def on_dir_changed(self, _path):
        """工作目录切换：刷新转换提示，并重新识别日期文件夹（不用手动扫描）。"""
        self._refresh_convert_hint()
        replace = bool(self._auto_folder) or not self.files
        self._auto_folder = None
        if replace:
            self.files = []
            self.current_path = None
            self._refresh_files(select=-1)
        self._sync_folders(reload_files=replace)

    def on_hide(self):
        # 切页必须松开文件：暂停状态的 QMediaPlayer 仍占着句柄，
        # 会让命名投稿无法改名、删除或写元数据。
        self._release_preview()

    def __init__(self, app):
        super().__init__(app)
        self.files: List[str] = []
        self.current_path: Optional[str] = None
        self.segments: List[Tuple[int, int]] = []
        self.partial_segments: List[dict] = []
        self._next_partial_id = 1
        self.detect_points: List[int] = []
        self.ext_audio_path: Optional[str] = None
        self.ext_sub_path: Optional[str] = None
        self._pending_start: Optional[int] = None
        self._dragging_slider = False
        self._last_seek_at = 0.0
        self._duration_ms = 0
        self._resume_ms = 0  # 切页松开句柄时记住播放位置
        self.running = False
        self._cancel: Optional[threading.Event] = None
        self._compress_queue: List[str] = []
        self._compress_thread = None
        # 当前自动点入的日期文件夹；None 表示队列是手动添加/扫描得到的，自动识别不再覆盖
        self._auto_folder: Optional[str] = None

        cfg = load_convert_config()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(8)
        root.addLayout(body, 1)

        # ==================== 左：文件队列 ====================
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        left.addWidget(_OptionGrid([
            self._btn("刷新", self._refresh_folders),
            self._btn("添加文件", self._add_files),
            self._btn("添加文件夹", self._add_folder)], columns=3, cell_width=88))
        row2 = QHBoxLayout()
        row2.addWidget(self._btn("按名排序", self._sort_by_name, small=True))
        queue_more = QPushButton("更多")
        queue_more.setProperty("menuButton", True)
        queue_menu = QMenu(queue_more)
        for label, slot in (("上移", lambda: self._move(-1)),
                            ("下移", lambda: self._move(1)),
                            ("删除选中文件", self._delete_selected), ("清空队列", self._clear)):
            queue_menu.addAction(label).triggered.connect(slot)
        queue_more.setMenu(queue_menu)
        row2.addWidget(queue_more)
        left.addLayout(row2)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        folder_row.addWidget(QLabel("日期文件夹"))
        self.folder_combo = QComboBox()
        self.folder_combo.setToolTip(
            "工作目录下的日期文件夹：进入页面自动点入最新一个并载入其中的视频，"
            "选中其它文件夹即载入该文件夹的视频")
        self.folder_combo.setMinimumContentsLength(12)
        self.folder_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        folder_row.addWidget(self.folder_combo, 1)
        left.addLayout(folder_row)
        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.model().rowsMoved.connect(lambda *_a: self._sync_order_from_view())
        self.file_list.currentRowChanged.connect(self._on_current_changed)
        left.addWidget(self.file_list, 1)
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet(
            "color: palette(placeholder-text); font-size: 11px;")
        self.file_label.setWordWrap(True)
        left.addWidget(self.file_label)
        self.left_wrap = QWidget()
        self.left_wrap.setLayout(left)
        self.left_wrap.setFixedWidth(280)
        body.addWidget(self.left_wrap)

        # ==================== 右：预览 + 操作 ====================
        right = QVBoxLayout()
        right.setSpacing(4)
        self.operation_splitter = QSplitter(Qt.Vertical)
        self.operation_splitter.setChildrenCollapsible(False)
        preview_area = QWidget()
        preview_layout = QVBoxLayout(preview_area)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        preview_area.setMinimumHeight(170)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(60)
        self.video.setStyleSheet("background: #000;")
        preview_layout.addWidget(self.video, 3)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.playbackStateChanged.connect(self._sync_play_button)

        # 进度条独立一行并锁死高度：竖直空间不足时普通布局会把它压成不可见，
        # 固定高度能保证它永远在（拖不动进度条=这条被挤掉）。
        self.slider = _SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setFixedHeight(20)
        self.slider.setMinimumWidth(140)
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider.setToolTip("点击或拖动进度条定位；拖动时会实时预览画面")
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 8px;"
            " background: palette(mid); border-radius: 4px; }"
            " QSlider::sub-page:horizontal { height: 8px;"
            " background: palette(mid); border-radius: 4px; }"
            " QSlider::handle:horizontal { width: 16px; height: 16px; margin: -4px 0;"
            " border-radius: 8px; background: palette(base);"
            " border: 1px solid palette(highlight); }"
            " QSlider::handle:horizontal:hover { background: palette(midlight); }"
            " QSlider::groove:horizontal:disabled { background: palette(alternate-base); }"
        )
        self.slider.clickedAt.connect(self._seek_to)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.actionTriggered.connect(
            lambda _action: self._seek_to(self.slider.sliderPosition()))

        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(
            "font-family: Consolas, monospace; color: palette(link);")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setFixedWidth(58)
        self.time_label.setToolTip("当前位置")
        self.dur_label = QLabel("00:00")
        self.dur_label.setStyleSheet(
            "font-family: Consolas, monospace; color: palette(placeholder-text);")
        self.dur_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.dur_label.setFixedWidth(58)
        self.dur_label.setToolTip("总时长")

        seek_row = QHBoxLayout()
        seek_row.setContentsMargins(0, 0, 0, 0)
        seek_row.setSpacing(6)
        seek_row.addWidget(self.time_label)
        seek_row.addWidget(self.slider, 1)
        seek_row.addWidget(self.dur_label)
        seek_wrap = QWidget()
        seek_wrap.setLayout(seek_row)
        seek_wrap.setFixedHeight(32)
        seek_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        preview_layout.addWidget(seek_wrap)

        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self._toggle_play)
        self.set_start_btn = self._btn("标记开始", self._set_start, small=True)
        self.set_start_btn.setToolTip("将当前播放指针标为起点（I），再标记结束生成片段")
        self.set_end_btn = self._btn("标记结束", self._set_end, small=True)
        self.set_end_btn.setToolTip("将当前播放指针标为终点（O）；未标记开始时从 00:00 开始")
        self.preview_more_btn = QPushButton("更多")
        self.preview_more_btn.setProperty("menuButton", True)
        preview_menu = QMenu(self.preview_more_btn)
        self.preview_more_btn.setMenu(preview_menu)
        self.shot_current_btn = preview_menu.addAction("截图当前帧")
        self.shot_current_btn.triggered.connect(self._shot_current)
        preview_layout.addWidget(_OptionGrid([
            self.play_btn, self.set_start_btn, self.set_end_btn, self.preview_more_btn],
            columns=4, cell_width=80))
        self.remove_start_btn = self._btn("删起点", self._remove_start_mark, small=True)
        self.remove_start_btn.setToolTip("删除选中片段的起点；保留终点待重新标记")
        self.remove_end_btn = self._btn("删终点", self._remove_end_mark, small=True)
        self.remove_end_btn.setToolTip("删除选中片段的终点；保留起点待重新标记")
        self.remove_seg_btn = self._btn("删选中", self._remove_segment, small=True)
        self.remove_seg_btn.setToolTip("删除选中的整段或单个待补标记")
        self.clear_seg_btn = self._btn("清除标记", self._clear_segments, small=True, danger=True)
        self.clear_seg_btn.setToolTip("清除当前视频全部片段、待补标记与未完成起点")
        preview_layout.addWidget(_OptionGrid([
            self.remove_start_btn, self.remove_end_btn,
            self.remove_seg_btn, self.clear_seg_btn], columns=4, cell_width=70))
        self.slider.markerDeleteRequested.connect(self._delete_timeline_marker)

        self.segment_status = QLabel("未标记片段：移动播放指针后标记开始 / 结束")
        self.segment_status.setProperty("role", "caption")
        self.segment_status.setWordWrap(True)

        # 高频步进直接显示，其余定位操作收进菜单，避免预览区挤占参数高度。
        jump_row = QHBoxLayout()
        jump_menu_btn = QPushButton("定位")
        jump_menu_btn.setProperty("menuButton", True)
        jump_menu = QMenu(jump_menu_btn)
        jump_menu_btn.setMenu(jump_menu)
        jump_row.addWidget(jump_menu_btn)
        self.jump_buttons = []
        for text, delta in (("片头", None), ("-10s", -10000), ("-1s", -1000),
                            ("+1s", 1000), ("+10s", 10000), ("片尾", "end")):
            if delta in (-1000, 1000):
                btn = QPushButton(text)
                btn.setStyleSheet("padding: 2px 4px;")
                jump_row.addWidget(btn)
                signal = btn.clicked
            else:
                btn = jump_menu.addAction(text)
                signal = btn.triggered
            if delta is None:
                signal.connect(lambda: self._seek_to(0))
            elif delta == "end":
                signal.connect(lambda: self._seek_to(self._duration_ms))
            else:
                signal.connect(lambda _c=False, d=delta: self._jump(d))
            self.jump_buttons.append(btn)
        self.jump_buttons.append(jump_menu_btn)
        self.jump_input = QLineEdit()
        self.jump_input.setPlaceholderText("00:30 跳转")
        self.jump_input.setAccessibleName("跳转时间")
        self.jump_input.returnPressed.connect(self._jump_to_text)
        jump_row.addWidget(self.jump_input, 1)
        self.jump_btn = QPushButton("跳转")
        self.jump_btn.setStyleSheet("padding: 2px 8px;")
        self.jump_btn.clicked.connect(self._jump_to_text)
        jump_row.addWidget(self.jump_btn)
        preview_layout.addLayout(jump_row)

        self.seg_list = QListWidget()
        self.seg_list.setMinimumHeight(64)
        self.seg_list.setMaximumHeight(100)
        self.seg_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.seg_list.currentRowChanged.connect(self._sync_timeline)
        self.seg_list.itemDoubleClicked.connect(
            self._seek_to_segment_item)
        self.operation_splitter.addWidget(preview_area)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("editorOperations")
        self.tabs.tabBar().setObjectName("editorOperationTabs")
        self.tabs.setStyleSheet(
            "QTabWidget#editorOperations::pane { border: 1px solid palette(shadow); }"
            "QTabBar#editorOperationTabs::tab { background: transparent; border: none;"
            " border-bottom: 2px solid transparent; padding: 6px 10px; }"
            "QTabBar#editorOperationTabs::tab:selected { color: palette(link);"
            " border-bottom-color: palette(highlight); }")
        self.tabs.setMinimumHeight(180)
        self.tabs.setMinimumWidth(0)
        self.tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.operation_splitter.addWidget(self.tabs)
        self.operation_splitter.setStretchFactor(0, 3)
        self.operation_splitter.setStretchFactor(1, 2)
        self.operation_splitter.setSizes([300, 250])
        right.addWidget(self.operation_splitter, 1)
        self._build_convert_tab(cfg)
        self._build_merge_tab(cfg)
        self._build_cut_tab(cfg)
        self._build_compress_tab(cfg)
        self._build_track_tab()
        self._build_shot_tab(cfg)
        self._build_meta_tab()
        self._refresh_files(select=-1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        prog_row = QHBoxLayout()
        prog_row.addWidget(self.progress, 1)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("variant", "danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_all)
        prog_row.addWidget(self.stop_btn)
        right.addLayout(prog_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(24)
        self.log_text.setMaximumHeight(64)
        self.log_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right.addWidget(self.log_text)
        body.addLayout(right, 1)
        self.setFocusPolicy(Qt.StrongFocus)
        self.on_theme_changed()

    # ==================== 小工具 ====================

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "left_wrap"):
            self.left_wrap.setFixedWidth(220 if self.width() < 900 else 280)

    def keyPressEvent(self, event):  # noqa: N802
        # 文本输入框会先消费字母，不抢占元数据/文件名的正常输入。
        if event.modifiers() == Qt.NoModifier and self.slider.isEnabled():
            if event.key() == Qt.Key_I:
                self._set_start()
                event.accept()
                return
            if event.key() == Qt.Key_O:
                self._set_end()
                event.accept()
                return
        super().keyPressEvent(event)

    def on_theme_changed(self):
        self.slider.update()
        self._sync_play_button()
        for widget, name in ((self.set_start_btn, "skip_prev"),
                             (self.set_end_btn, "skip_next")):
            widget.setIcon(line_icon(name, theme.color("text"), 16))

    def _sync_play_button(self, _state=None):
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_btn.setText("暂停" if playing else "播放")
        self.play_btn.setIcon(line_icon("pause" if playing else "play", theme.color("text"), 16))

    @staticmethod
    def _field(label, control):
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        text = QLabel(label)
        text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(text)
        control.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(control)
        control.setAccessibleName(label.rstrip(":"))
        return group

    def _add_tab(self, content, title):
        """固定页签头；每页只有一个纵向滚动容器。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(0)
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        for combo in content.findChildren(QComboBox):
            combo.setMinimumWidth(160)
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setToolTip(combo.currentText())
            combo.currentTextChanged.connect(combo.setToolTip)
        for label in content.findChildren(QLabel):
            if label.wordWrap():
                label.setMinimumWidth(0)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll.setWidget(content)
        return self.tabs.addTab(scroll, title)

    def _btn(self, text, slot, small=False, danger=False):
        b = QPushButton(text)
        b.clicked.connect(slot)
        if danger:
            b.setProperty("variant", "danger")
        if small:
            b.setStyleSheet("padding: 2px 8px;")
        return b

    def _log(self, msg):
        self.log_text.append(str(msg))

    def _cfg(self):
        return load_convert_config()

    def _save(self, **kwargs):
        cfg = load_convert_config()
        cfg.update(kwargs)
        save_convert_config(cfg)

    # ==================== 输入/输出目录（统一取全局设置） ====================

    def _output_dir(self) -> Path:
        """输入与输出同一个目录：始终取全局设置里的工作目录。"""
        work = getattr(self.app, "work_dir", "") or ""
        if work and Path(work).is_dir():
            return Path(work)
        if self.current_path:
            return Path(self.current_path).parent
        return Path.home()

    def _work_dir_start(self) -> str:
        work = getattr(self.app, "work_dir", "") or ""
        return work if work and Path(work).is_dir() else str(Path.home())

    # ==================== 日期文件夹（自动识别，和命名投稿页一致） ====================

    def _folder_choices(self):
        """工作目录下的全部日期文件夹 [(名称, 绝对路径)]，按名称升序。"""
        root = self._work_dir_start()
        return root, [(name, os.path.join(root, name))
                      for name, _label in scan_date_folders(root)]

    def _sync_folders(self, reload_files: bool = True):
        """扫描日期文件夹下拉，自动点入最新一个并载入其中视频（免手动扫描）。"""
        if self.running:
            return
        _root, choices = self._folder_choices()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for name, path in choices:
            self.folder_combo.addItem(name, path)
            self.folder_combo.setItemData(
                self.folder_combo.count() - 1, path, Qt.ToolTipRole)
        self.folder_combo.blockSignals(False)
        self.folder_combo.setEnabled(bool(choices))
        if not choices:
            # 没有日期文件夹：按工作目录递归兜底，且只在队列为空时做，不冲掉手动队列
            work = getattr(self.app, "work_dir", "") or ""
            if reload_files and not self.files and work and Path(work).is_dir():
                self._scan_work_dir()
            return
        if self.files and not self._auto_folder:
            # 手动添加/扫描得到的队列保持不动，下拉只跟随显示当前文件所在文件夹
            self._select_folder(self._folder_of_path(self.current_path, choices))
            return
        keep = (self._auto_folder
                if self._auto_folder and os.path.isdir(self._auto_folder) else "")
        target = keep or choices[-1][1]   # 升序 → 最后一个是最新日期文件夹
        self._select_folder(target)
        if reload_files:
            self._load_folder(target)

    def _refresh_folders(self):
        """刷新：重扫日期文件夹并重新载入当前文件夹的视频。"""
        if self.running:
            return
        _root, choices = self._folder_choices()
        if not choices:
            self._scan_work_dir()   # 没有日期文件夹：按工作目录递归刷新
            return
        self._sync_folders()
        self._log("已刷新日期文件夹与视频列表")

    def _folder_of_path(self, path, choices):
        """当前文件属于哪个日期文件夹；不在日期文件夹内返回空串。"""
        if not path:
            return ""
        target = os.path.abspath(path)
        for _name, folder in choices:
            base = os.path.abspath(folder)
            try:
                if os.path.commonpath([base, target]) == base:
                    return folder
            except ValueError:
                continue
        return ""

    def _select_folder(self, folder: str):
        """只把下拉切到指定文件夹，不触发载入。"""
        if not folder:
            return
        for idx in range(self.folder_combo.count()):
            if self.folder_combo.itemData(idx) == folder:
                self.folder_combo.blockSignals(True)
                self.folder_combo.setCurrentIndex(idx)
                self.folder_combo.blockSignals(False)
                return

    def _load_folder(self, folder: str):
        """点入日期文件夹：队列换成该文件夹内的视频，并载入第一个预览。"""
        found = [str(p) for p in find_videos_in(folder)]
        self._auto_folder = folder
        if found and self.files == found and self.current_path in found:
            return   # 内容没变：保留当前进度与已标记片段，不重置预览
        if not found:
            self.files = []
            self._refresh_files(select=-1)
            self._log(f"{Path(folder).name} 内没有视频")
            return
        keep = self.current_path if self.current_path in found else found[0]
        self.files = found
        self._refresh_files(select=found.index(keep))
        self._log(f"载入日期文件夹 {Path(folder).name} → {len(found)} 个视频")

    def _on_folder_changed(self, index: int):
        """下拉选中某个日期文件夹：载入该文件夹的视频。"""
        folder = self.folder_combo.itemData(index)
        if not folder or not os.path.isdir(folder):
            return
        if folder == self._auto_folder and self.files:
            return
        if self.files and not self._auto_folder:
            self._log("手动加入的文件队列已被日期文件夹替换")
        self._load_folder(folder)

    # ==================== 文件队列 ====================

    def add_dropped_files(self, files):
        added = []
        for f in files:
            if is_video_file(f) and f not in self.files:
                self.files.append(f)
                added.append(f)
        if added:
            self._auto_folder = None   # 手动加入后队列归手动，自动识别不再覆盖
            self._log(f"添加 {len(added)} 个视频")
            self._refresh_files(select=self.files.index(added[0]))
        else:
            self._log("无新增视频（重复或非视频文件已忽略）")

    def add_dropped_folder(self, folder):
        found = [str(p) for p in find_videos_in(folder)]
        if found:
            self._log(f"从文件夹添加: {folder}")
            self.add_dropped_files(found)
        else:
            self._log(f"文件夹内没有视频: {folder}")

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", self._work_dir_start(),
            "视频文件 (*.mp4 *.mkv *.mov *.flv *.ts *.avi *.wmv *.webm);;所有文件 (*.*)")
        if files:
            self.add_dropped_files(files)

    def _scan_work_dir(self):
        """兜底：递归扫描全局工作目录（无日期文件夹时由「刷新」和进入页面调用）。"""
        root = self._work_dir_start()
        found = [str(p) for p in find_videos_in(root)]
        if not found:
            self._log(f"工作目录内没有视频: {root}")
            return
        self._log(f"扫描工作目录: {root} → {len(found)} 个视频")
        self.add_dropped_files(found)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择文件夹（递归收集视频）", self._work_dir_start())
        if folder:
            self.add_dropped_folder(folder)

    def _sort_by_name(self):
        if not self.files:
            return
        current = self.current_path
        self.files.sort(key=lambda p: Path(p).name.lower())
        row = self.files.index(current) if current in self.files else 0
        self._refresh_files(select=row)
        self._log("已按文件名排序")

    def _delete_selected(self):
        rows = sorted({self.file_list.row(i) for i in self.file_list.selectedItems()},
                      reverse=True)
        for row in rows:
            self.files.pop(row)
        self._refresh_files(select=min(rows[-1], len(self.files) - 1) if self.files and rows else 0)

    def _clear(self):
        self.files = []
        self._refresh_files(select=-1)

    def _move(self, delta):
        rows = sorted(self.file_list.row(i) for i in self.file_list.selectedItems())
        if not rows:
            return
        if delta < 0:
            if rows[0] == 0:
                return
            for row in rows:
                self.files[row - 1], self.files[row] = self.files[row], self.files[row - 1]
            new_rows = [r - 1 for r in rows]
        else:
            if rows[-1] >= len(self.files) - 1:
                return
            for row in reversed(rows):
                self.files[row + 1], self.files[row] = self.files[row], self.files[row + 1]
            new_rows = [r + 1 for r in rows]
        self._refresh_files(select=new_rows[0])
        for row in new_rows:
            if self.file_list.item(row):
                self.file_list.item(row).setSelected(True)

    def _sync_order_from_view(self):
        ordered = [self.file_list.item(i).data(Qt.UserRole)
                   for i in range(self.file_list.count())]
        if ordered and ordered != self.files:
            self.files = ordered
        self._refresh_files(select=self.file_list.currentRow())

    def _refresh_files(self, select: int = 0):
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for idx, fp in enumerate(self.files, 1):
            try:
                size = format_size(Path(fp).stat().st_size)
            except OSError:
                size = "?"
            item = QListWidgetItem(f"{idx}. {Path(fp).name}  ({size})")
            item.setData(Qt.UserRole, fp)
            item.setToolTip(fp)
            self.file_list.addItem(item)
        if self.files:
            row = max(0, min(select, len(self.files) - 1))
            self.file_list.setCurrentRow(row)
        else:
            row = -1
        self.file_list.blockSignals(False)
        self._on_current_changed(row)

    def _on_current_changed(self, row: int):
        self._pending_start = None
        self.segments = []
        self.detect_points = []
        self.points_list.clear()
        if row < 0 or row >= len(self.files):
            self.current_path = None
            self._duration_ms = 0
            self.player.stop()
            self.player.setSource(QUrl())
            self.slider.setRange(0, 0)
            self._update_time_label(0)
            self.partial_segments = []
            self._refresh_segments()
            self.play_btn.setText("播放")
            self.file_label.setText("未选择文件")
            self.track_table.setRowCount(0)
            for edit in (self.meta_title, self.meta_artist,
                         self.meta_comment, self.meta_date):
                edit.clear()
            self._set_preview_enabled(False)
            self._refresh_action_buttons()
            return
        self.current_path = self.files[row]
        self.file_label.setText(self.current_path)
        self.partial_segments = []
        self._refresh_segments()
        self._load_preview(self.current_path)
        self._refresh_tracks()
        self._refresh_meta()
        self._set_preview_enabled(True)

    # ==================== 预览 ====================

    def _load_preview(self, path: str):
        self._resume_ms = 0
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()
        self.slider.setValue(0)
        self.play_btn.setText("播放")
        try:
            self._duration_ms = int(probe_media(Path(path)).duration * 1000)
        except Exception:
            self._duration_ms = 0
        self.slider.setRange(0, max(0, self._duration_ms))
        self._update_time_label(0)

    def _on_duration(self, duration: int):
        if duration > 0:
            self._duration_ms = duration
            self.slider.setRange(0, duration)
            self._update_time_label(self.player.position())
            self._sync_timeline()

    def _on_position(self, position: int):
        # 拖动中不让播放位置反压滑块与时间标签，否则手感是「拖了又弹回去」
        if self._dragging_slider:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(position)
        self.slider.blockSignals(False)
        self._update_time_label(position)

    def _update_time_label(self, position: int):
        self.time_label.setText(_fmt_time(position))
        self.dur_label.setText(_fmt_time(self._duration_ms))

    def _on_slider_pressed(self):
        self._dragging_slider = True
        self._last_seek_at = 0.0

    def _on_slider_released(self):
        self._dragging_slider = False
        self._last_seek_at = time.monotonic()
        self.player.setPosition(self.slider.value())
        self._update_time_label(self.slider.value())

    def _on_slider_moved(self, value: int):
        self._update_time_label(value)
        # 拖动过程中实时预览画面，节流 120ms 防止高频 seek 卡住解码器
        now = time.monotonic()
        if now - self._last_seek_at >= 0.12:
            self._last_seek_at = now
            self.player.setPosition(int(value))

    def _toggle_play(self):
        if not self.current_path or self._duration_ms <= 0:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("播放")
        else:
            self.player.play()
            self.play_btn.setText("暂停")

    def _seek_to(self, position_ms: int):
        if not self.current_path:
            return
        position = max(0, min(self._duration_ms, int(position_ms)))
        self.slider.setValue(position)
        self.player.setPosition(position)
        self._update_time_label(position)

    def _jump(self, delta_ms: int):
        if not self.current_path:
            return
        current = self.slider.value()
        self._seek_to(current + delta_ms)

    def _jump_to_text(self):
        if not self.current_path:
            return
        text = self.jump_input.text().strip()
        if not text:
            return
        try:
            if ":" in text:
                parts = [float(p) for p in text.split(":")]
                seconds = 0.0
                for part in parts:
                    seconds = seconds * 60 + part
            else:
                seconds = float(text)
        except ValueError:
            self.app.toast("时间格式示例：30 或 01:30 或 1:02:03", "warning")
            return
        self._seek_to(int(seconds * 1000))

    def _set_preview_enabled(self, enabled: bool):
        for widget in (self.play_btn, self.set_start_btn, self.set_end_btn,
                       self.remove_start_btn, self.remove_end_btn,
                       self.remove_seg_btn, self.clear_seg_btn,
                       self.shot_current_btn, self.preview_more_btn, self.jump_input, self.jump_btn,
                       *self.jump_buttons):
            widget.setEnabled(enabled)
        self.slider.setEnabled(enabled)
        for index in range(2, self.tabs.count()):
            self.tabs.setTabEnabled(index, enabled)

    def _refresh_action_buttons(self):
        has_file = bool(self.current_path)
        idle = not self.running
        self.left_wrap.setEnabled(idle)
        self.tabs.setEnabled(idle)
        self.merge_btn.setEnabled(len(self.files) >= 2 and idle)
        self.cut_btn.setEnabled(has_file and bool(self.segments) and idle)
        self.cmp_btn.setEnabled(bool(self.files) and idle)
        self.convert_btn.setEnabled(bool(self.files) and idle)
        self.convert_scan_btn.setEnabled(idle)
        self.convert_clear_btn.setEnabled(idle)
        self.rotate_btn.setEnabled(has_file and idle)
        self.meta_rotation.setEnabled(has_file and idle)
        data = self._selected_segment_data() if hasattr(self, "seg_list") else None
        segment = self._selected_complete_segment(data)
        partial = self._partial_by_id(data.get("id")) if data and data.get("kind") == "partial" else None
        pending = bool(data and data.get("kind") == "pending" and self._pending_start is not None)
        self.remove_start_btn.setEnabled(idle and bool(segment or (partial and partial.get("start") is not None) or pending))
        self.remove_end_btn.setEnabled(idle and bool(segment or (partial and partial.get("end") is not None)))
        self.remove_seg_btn.setEnabled(idle and bool(segment or partial or pending))
        self.clear_seg_btn.setEnabled(idle and bool(
            self.segments or self.partial_segments or self._pending_start is not None))

    # ==================== 转换页签 ====================

    def _build_convert_tab(self, cfg):
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(12, 12, 12, 12)
        hint = QLabel("转换左侧整个文件队列，优先无损封装。成功后队列切换为输出文件，可继续剪辑。")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.convert_scan_btn = self._btn("扫描待转换视频", self._scan_convert)
        self.convert_btn = self._btn("开始转换", self._start_convert)
        self.convert_btn.setProperty("variant", "primary")
        self.convert_clear_btn = self._btn("清除转换缓存", self._clear_convert_cache)
        self.convert_clear_btn.setToolTip("只清除转换记录，不删除视频文件。")
        v.addWidget(_OptionGrid([
            self.convert_scan_btn, self.convert_clear_btn, self.convert_btn],
            columns=3, cell_width=128))
        row = QHBoxLayout()
        row.addWidget(QLabel("输出格式:"))
        self.convert_ext = QComboBox()
        self.convert_ext.addItems([".mp4", ".mkv", ".mov"])
        self.convert_ext.setCurrentText(cfg.get("output_extension", ".mp4"))
        self.convert_ext.currentTextChanged.connect(lambda t: self._save(output_extension=t))
        row.addWidget(self.convert_ext)
        row.addStretch(1)
        v.addLayout(row)
        self.convert_options = {}
        for index, (key, label, default) in enumerate((
            ("preserve_metadata", "保留元数据", True),
            ("preserve_timestamps", "保留时间戳", True),
            ("enable_cache", "启用缓存", True),
            ("enable_encode_fallback", "无损失败转码", False),
            ("delete_source_after_success", "成功后删源", False),
        )):
            checkbox = QCheckBox(label)
            checkbox.setChecked(cfg.get(key, default))
            checkbox.toggled.connect(lambda checked, k=key: self._save(**{k: checked}))
            self.convert_options[key] = checkbox
        self.convert_options["delete_source_after_success"].setToolTip("转换成功后删除原始视频。")
        v.addWidget(_OptionGrid(list(self.convert_options.values()), compact=True))
        self.convert_hint = QLabel()
        self.convert_hint.setWordWrap(True)
        self.convert_hint.setMinimumWidth(0)
        self.convert_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.convert_hint)
        self.meta_rotation = QComboBox()
        for label, value in (("保持原样", None), ("0°", 0), ("90°", 90),
                             ("180°", 180), ("270°", 270)):
            self.meta_rotation.addItem(label, value)
        self.rotate_btn = self._btn("导出旋转", self._write_rotation, small=True)
        self.rotate_btn.setToolTip("只旋转当前视频，独立导出新文件；不修改画质与已有元数据")
        v.addWidget(_OptionGrid([
            self._field("旋转:", self.meta_rotation), self.rotate_btn], compact=True))
        rotation_hint = QLabel("旋转仅处理当前视频的显示方向，不参与批量转换；MP4 支持显示矩阵，MKV 可能忽略。")
        rotation_hint.setWordWrap(True)
        rotation_hint.setProperty("role", "caption")
        v.addWidget(rotation_hint)
        v.addStretch(1)
        self.convert_tab_index = self._add_tab(content, "转换")
        self._refresh_convert_hint()

    def open_convert_tab(self):
        self.tabs.setCurrentIndex(self.convert_tab_index)

    def _refresh_convert_hint(self):
        cfg = self._cfg()
        extensions = " / ".join(cfg.get("input_extensions", [".flv", ".mkv", ".ts"]))
        output = cfg.get("output_dir") or "与源文件相同目录"
        self.convert_hint.setText(f"扫描格式: {extensions}    输出位置: {output}")

    def _scan_convert(self):
        if self.running:
            return
        root = Path(getattr(self.app, "work_dir", "") or "")
        if not getattr(self.app, "work_dir", "") or not root.is_dir():
            self.app.toast("请先设置有效的工作目录", "warning")
            return
        self._refresh_convert_hint()
        found = find_videos(root, self._cfg())
        self._log(f"扫描完成，共找到 {len(found)} 个待转换视频")
        self.add_dropped_files([str(p) for p in found])

    def _clear_convert_cache(self):
        if self.running:
            return
        if QMessageBox.question(self, "清除转换缓存", "确定清除转换记录吗？不会删除视频文件。",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            save_convert_cache({})
            self._log("转换缓存已清除")

    def _start_convert(self):
        if self.running or not self.files:
            return
        cfg = self._cfg()
        cfg["output_extension"] = self.convert_ext.currentText()
        cfg.update({key: widget.isChecked() for key, widget in self.convert_options.items()})
        paths = [Path(path).resolve() for path in self.files]
        work_root = Path(self.app.work_dir).resolve() if self.app.work_dir else None
        self._begin(f"开始转换 {len(paths)} 个视频", "停止将在当前 FFmpeg 操作结束后生效。")
        cancel = self._cancel
        # 释放播放器占用，允许 Windows 上转换成功后删除源文件。
        self.player.stop()
        self.player.setSource(QUrl())

        def work():
            cache = load_convert_cache() if cfg.get("enable_cache") else {}
            stats = {"success": 0, "skipped": 0, "failed": 0}
            outputs = {}
            try:
                for index, src in enumerate(paths):
                    if cancel.is_set():
                        break
                    # 手动添加的文件可在工作目录外；其输出使用文件名而非非法相对路径。
                    root = work_root if work_root and src.is_relative_to(work_root) else src.parent
                    try:
                        dst = get_output_path(src, root, cfg)
                        if cfg.get("enable_cache") and is_convert_cached(cache, src) and dst.exists():
                            stats["skipped"] += 1
                            outputs[str(src)] = str(dst)
                            post_to_main(self._log, f"缓存命中: {src.name}")
                        else:
                            result = convert_one_file(
                                src, root, cfg,
                                progress_callback=lambda msg: post_to_main(self._log, msg),
                                should_stop=cancel.is_set)
                            if result.interrupted:
                                break
                            key = "skipped" if result.mode == "skip" else "success" if result.success else "failed"
                            stats[key] += 1
                            if result.success:
                                outputs[str(src)] = str(result.dst)
                            if cfg.get("enable_cache"):
                                update_convert_cache(cache, src, result.success, result.dst)
                    except Exception as exc:
                        stats["failed"] += 1
                        post_to_main(self._log, f"{src.name}: {exc}")
                    post_to_main(self.progress.setValue, int((index + 1) / len(paths) * 100))
            finally:
                if cfg.get("enable_cache"):
                    save_convert_cache(cache)
            return stats, outputs

        def done(result, err):
            stopped = cancel.is_set()
            self.running = False
            self._cancel = None
            self.stop_btn.setEnabled(False)
            self._refresh_action_buttons()
            if err:
                if self.current_path:
                    self._load_preview(self.current_path)
                self._log(f"转换失败: {err}")
                self.app.toast("转换失败，详情见日志", "error")
                return
            stats, outputs = result
            selected = outputs.get(str(Path(self.current_path).resolve()), self.current_path) if self.current_path else None
            remaining = [outputs.get(str(Path(path).resolve()), path) for path in self.files]
            self.files = list(dict.fromkeys(path for path in remaining if Path(path).exists()))
            self._refresh_files(select=self.files.index(selected) if selected in self.files else 0)
            self._log(f"{'已停止' if stopped else '转换完成'}: 成功 {stats['success']} / "
                      f"跳过 {stats['skipped']} / 失败 {stats['failed']}")
            if not stopped:
                self.progress.setValue(100)

        run_bg(work, done)

    def shutdown(self):
        if self._cancel is not None:
            self._cancel.set()
        self._release_preview()

    def _on_player_error(self, *_args):
        self._log("该格式无法内嵌预览（不影响切割/合并/提取）")

    # ==================== 片段 ====================

    def _selected_segment_data(self):
        item = self.seg_list.currentItem()
        value = item.data(Qt.UserRole) if item is not None else None
        return value if isinstance(value, dict) else None

    def _partial_by_id(self, partial_id):
        if partial_id is None:
            return None
        return next((row for row in self.partial_segments
                     if row.get("id") == partial_id), None)

    def _selected_complete_segment(self, data=None):
        data = self._selected_segment_data() if data is None else data
        if not data or data.get("kind") != "segment":
            return None
        index = data.get("index")
        if isinstance(index, int) and 0 <= index < len(self.segments):
            return self.segments[index]
        return None

    def _new_partial(self, start=None, end=None):
        row = {"id": self._next_partial_id, "start": start, "end": end}
        self._next_partial_id += 1
        self.partial_segments.append(row)
        return row

    def _promote_partial(self, row):
        start, end = row.get("start"), row.get("end")
        if start is None or end is None:
            return False
        if end <= start:
            self.app.toast("终点需要大于起点", "warning")
            return False
        interval = (int(start), int(end))
        self.segments.append(interval)
        self.segments.sort()
        self.partial_segments.remove(row)
        self._refresh_segments({"kind": "segment", "range": interval})
        self._log(f"已补齐片段 {_fmt_ms(start)} → {_fmt_ms(end)}")
        return True

    def _set_start(self):
        if not self.current_path or self._duration_ms <= 0:
            return
        position = self.slider.value()
        data = self._selected_segment_data()
        if data and data.get("kind") == "partial":
            row = self._partial_by_id(data.get("id"))
            if row is None:
                return
            end = row.get("end")
            if end is not None and position >= end:
                self.app.toast("开始时间需要早于保留的终点", "warning")
                return
            row["start"] = position
            if row.get("end") is not None:
                self._promote_partial(row)
            else:
                self._refresh_segments({"kind": "partial", "id": row["id"]})
                self._log(f"起点已标记: {_fmt_ms(position)} · 待标终点")
            return
        self._pending_start = position
        self._refresh_segments({"kind": "pending"})
        self._log(f"起点: {_fmt_ms(self._pending_start)}")

    def _set_end(self):
        if not self.current_path or self._duration_ms <= 0:
            return
        end = self.slider.value()
        data = self._selected_segment_data()
        if data and data.get("kind") == "partial":
            row = self._partial_by_id(data.get("id"))
            if row is None:
                return
            start = row.get("start")
            if start is not None and end <= start:
                self.app.toast("终点需要晚于保留的起点", "warning")
                return
            row["end"] = end
            if row.get("start") is not None:
                self._promote_partial(row)
            else:
                self._refresh_segments({"kind": "partial", "id": row["id"]})
                self._log(f"终点已标记: {_fmt_ms(end)} · 待标起点")
            return
        start = self._pending_start
        if start is None and self.partial_segments:
            self.app.toast("先在片段列表选中待补标记，再补另一端", "warning")
            return
        if start is None:
            start = 0
        if end <= start:
            self.app.toast("终点需要大于起点", "warning")
            return
        interval = (int(start), int(end))
        self.segments.append(interval)
        self.segments.sort()
        self._pending_start = None
        self._refresh_segments({"kind": "segment", "range": interval})
        self._log(f"已添加片段 {_fmt_ms(start)} → {_fmt_ms(end)}")

    @staticmethod
    def _same_segment_item(actual, wanted):
        if not isinstance(actual, dict) or not isinstance(wanted, dict):
            return False
        if actual.get("kind") != wanted.get("kind"):
            return False
        if actual["kind"] == "segment":
            if "index" in wanted:
                return actual.get("index") == wanted.get("index")
            return actual.get("range") == wanted.get("range")
        if actual["kind"] == "partial":
            return actual.get("id") == wanted.get("id")
        return actual["kind"] == "pending"

    def _refresh_segments(self, selected=None):
        if selected is None:
            selected = self._selected_segment_data()
        self.seg_list.blockSignals(True)
        self.seg_list.clear()
        entries = []
        for index, (start, end) in enumerate(self.segments):
            entries.append((min(start, end), 1, {
                "kind": "segment", "index": index, "range": (start, end),
                "text": f"{_fmt_ms(start)} → {_fmt_ms(end)}  ({_fmt_ms(end - start)})",
                "tip": "双击定位到片段起点"}))
        for row in self.partial_segments:
            if row.get("start") is not None:
                order_time = row["start"]
                label = f"起点 {_fmt_ms(row['start'])} · 待补终点"
                tip = "选中后用「标记结束」或 O 补齐"
            else:
                order_time = row["end"]
                label = f"待补起点 · 终点 {_fmt_ms(row['end'])}"
                tip = "选中后用「标记开始」或 I 补齐"
            entries.append((order_time, 0, {
                "kind": "partial", "id": row["id"], "text": label, "tip": tip}))
        if self._pending_start is not None:
            entries.append((self._pending_start, -1, {
                "kind": "pending", "text": f"待完成片段 · 起点 {_fmt_ms(self._pending_start)}",
                "tip": "移动播放指针后标记结束；或选中后删除此起点"}))
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        selected_row = -1
        for order_time, order_type, data in entries:
            item = QListWidgetItem(data["text"])
            item.setData(Qt.UserRole, data)
            item.setToolTip(data["tip"])
            self.seg_list.addItem(item)
            if selected and self._same_segment_item(data, selected):
                selected_row = self.seg_list.count() - 1
        if selected_row >= 0:
            self.seg_list.setCurrentRow(selected_row)
        elif self.seg_list.count():
            self.seg_list.setCurrentRow(0)
        self.seg_list.blockSignals(False)
        self._sync_timeline()
        self._refresh_action_buttons()

    def _sync_timeline(self, _row=None):
        data = self._selected_segment_data()
        selected = data.get("index", -1) if data and data.get("kind") == "segment" else -1
        self.slider.set_segments(self.segments, self._pending_start, selected,
                                 self.partial_segments)
        if data and data.get("kind") == "pending" and self._pending_start is not None:
            self.segment_status.setText(
                f"待完成起点 {_fmt_ms(self._pending_start)} · 移动播放指针后标记结束")
        elif data and data.get("kind") == "partial":
            row = self._partial_by_id(data.get("id"))
            if row and row.get("start") is not None:
                self.segment_status.setText(
                    f"待补终点 · 保留起点 {_fmt_ms(row['start'])} · 选中后按 O 补齐")
            elif row:
                self.segment_status.setText(
                    f"待补起点 · 保留终点 {_fmt_ms(row['end'])} · 选中后按 I 补齐")
            else:
                self.segment_status.setText("待补标记已移除")
        elif data and data.get("kind") == "segment":
            start, end = data["range"]
            self.segment_status.setText(
                f"片段 {selected + 1} / {len(self.segments)} · 起 {_fmt_ms(start)} → 终 {_fmt_ms(end)}")
        else:
            self.segment_status.setText("移动播放指针并标记开始 / 结束；未选中待补标记时结束从 00:00 开始")

    def _seek_to_segment_item(self, item):
        data = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(data, dict):
            return
        if data.get("kind") == "segment":
            self._seek_to(data["range"][0])
        elif data.get("kind") == "partial":
            row = self._partial_by_id(data.get("id"))
            if row:
                point = row.get("start") if row.get("start") is not None else row.get("end")
                self._seek_to(point)
        elif data.get("kind") == "pending":
            self._seek_to(self._pending_start or 0)

    def _remove_start_mark(self):
        self._remove_mark("start")

    def _remove_end_mark(self):
        self._remove_mark("end")

    def _remove_mark(self, endpoint, data=None):
        data = self._selected_segment_data() if data is None else data
        if not data:
            return
        kind = data.get("kind")
        if kind == "segment":
            index = data.get("index")
            if not isinstance(index, int) or not 0 <= index < len(self.segments):
                return
            start, end = self.segments.pop(index)
            partial = self._new_partial(
                None if endpoint == "start" else start,
                None if endpoint == "end" else end)
            self._refresh_segments({"kind": "partial", "id": partial["id"]})
            self._log(f"已删除所选片段的{'起点' if endpoint == 'start' else '终点'}，保留另一端")
            return
        if kind == "partial":
            row = self._partial_by_id(data.get("id"))
            if row is None or row.get(endpoint) is None:
                return
            self.partial_segments.remove(row)
            self._refresh_segments()
            self._log(f"已删除待补标记的{'起点' if endpoint == 'start' else '终点'}")
            return
        if kind == "pending" and endpoint == "start" and self._pending_start is not None:
            self._pending_start = None
            self._refresh_segments()
            self._log("已删除未完成起点")

    def _remove_segment(self):
        data = self._selected_segment_data()
        if not data:
            return
        kind = data.get("kind")
        if kind == "segment":
            index = data.get("index")
            if isinstance(index, int) and 0 <= index < len(self.segments):
                self.segments.pop(index)
        elif kind == "partial":
            row = self._partial_by_id(data.get("id"))
            if row is not None:
                self.partial_segments.remove(row)
        elif kind == "pending":
            self._pending_start = None
        self._refresh_segments()

    def _delete_timeline_marker(self, marker):
        kind = marker.get("kind")
        if kind == "segment":
            index = marker.get("index")
            if not isinstance(index, int) or not 0 <= index < len(self.segments):
                return
            start, end = self.segments[index]
            data = {"kind": "segment", "index": index, "range": (start, end)}
        elif kind == "partial":
            data = {"kind": "partial", "id": marker.get("id")}
        elif kind == "pending":
            data = {"kind": "pending"}
        else:
            return
        self._remove_mark(marker.get("endpoint", "start"), data)

    def _clear_segments(self):
        self.segments = []
        self.partial_segments = []
        self._pending_start = None
        self._refresh_segments()

    # ==================== 合并页签 ====================

    def _build_merge_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        self.merge_mode = QComboBox()
        self.merge_mode.addItem("无损拼接（推荐）", MODE_COPY)
        self.merge_mode.addItem("兼容转码（参数不一致时）", MODE_ENCODE)
        idx = self.merge_mode.findData(cfg.get("merge_mode", MODE_COPY))
        self.merge_mode.setCurrentIndex(max(0, idx))
        self.merge_mode.currentIndexChanged.connect(
            lambda _i: self._save(merge_mode=self.merge_mode.currentData()))
        self.merge_ext = QComboBox()
        self.merge_ext.addItems([".mp4", ".mkv"])
        self.merge_ext.setCurrentText(cfg.get("merge_out_ext", ".mp4"))
        self.merge_ext.currentTextChanged.connect(lambda t: self._save(merge_out_ext=t))
        v.addWidget(_OptionGrid([
            self._field("合并方式:", self.merge_mode), self._field("输出:", self.merge_ext)], compact=True))

        name_row = QHBoxLayout()
        self.merge_name = QLineEdit()
        self.merge_name.setPlaceholderText("文件名（留空自动命名）")
        self.merge_name.setAccessibleName("合并文件名")
        self.merge_name.setToolTip("合并文件名；留空使用 合并_日期_时间")
        name_row.addWidget(self.merge_name, 1)
        self.merge_btn = QPushButton("开始合并")
        self.merge_btn.setProperty("variant", "primary")
        self.merge_btn.clicked.connect(self._start_merge)
        name_row.addWidget(self.merge_btn)
        v.addLayout(name_row)
        hint = QLabel("合并按左侧列表顺序执行；参数不一致会提示，可改用「兼容转码」。")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        self._add_tab(tab, "合并")

    # ==================== 切割页签 ====================

    def _build_cut_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        self.cut_export = QComboBox()
        self.cut_export.addItem("逐段导出", "each")
        self.cut_export.addItem("拼接成一个文件", "merge")
        self.cut_export.addItem("删除选中片段（导出剩余）", "complement")
        idx = self.cut_export.findData(cfg.get("editor_cut_export", "each"))
        self.cut_export.setCurrentIndex(max(0, idx))
        self.cut_export.currentIndexChanged.connect(
            lambda _i: self._save(editor_cut_export=self.cut_export.currentData()))
        self.cut_mode = QComboBox()
        self.cut_mode.addItem("无损（关键帧边界，快）", "copy")
        self.cut_mode.addItem("精确（重编码，准）", "precise")
        idx = self.cut_mode.findData(cfg.get("editor_cut_mode", "copy"))
        self.cut_mode.setCurrentIndex(max(0, idx))
        self.cut_mode.currentIndexChanged.connect(
            lambda _i: self._save(editor_cut_mode=self.cut_mode.currentData()))
        v.addWidget(_OptionGrid([
            self._field("导出方式:", self.cut_export), self._field("切割方式:", self.cut_mode)], compact=True))

        row2 = QHBoxLayout()
        self.cut_name = QLineEdit()
        self.cut_name.setPlaceholderText("合并文件名（留空自动命名）")
        self.cut_name.setAccessibleName("切割后合并文件名")
        self.cut_name.setToolTip("切割后合并文件名；留空使用 原文件名_片段合并")
        row2.addWidget(self.cut_name, 1)
        self.cut_btn = QPushButton("开始切割")
        self.cut_btn.setProperty("variant", "primary")
        self.cut_btn.clicked.connect(self._start_cut)
        row2.addWidget(self.cut_btn)
        v.addLayout(row2)

        hint = QLabel("移动播放指针，用「标记开始 / 结束」或 I / O 添加片段，时间轴同步显示颜色。无损切割以关键帧为界，"
                      "实际起止可能前后偏移；要精确请选「精确」（会转码）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        v.addWidget(hint)

        v.addWidget(self.segment_status)
        v.addWidget(self.seg_list)

        # 自动检测（静音/黑帧/场景）→ 切点 → 片段
        self.det_type = QComboBox()
        self.det_type.addItem("静音", "silence")
        self.det_type.addItem("黑帧", "black")
        self.det_type.addItem("场景切换", "scene")
        self.det_type.currentIndexChanged.connect(self._on_detect_type_changed)
        self.det_thresh = QDoubleSpinBox()
        self.det_thresh.setDecimals(2)
        self.det_thresh.setRange(-60, 1)
        self.det_thresh.setValue(-30)
        self.det_thresh.setSuffix(" dB")
        self.det_min = QDoubleSpinBox()
        self.det_min.setRange(0.1, 10.0)
        self.det_min.setSingleStep(0.1)
        self.det_min.setValue(0.5)
        self.det_min.setSuffix(" 秒")
        v.addWidget(_OptionGrid([
            self._field("自动检测:", self.det_type), self._field("阈值:", self.det_thresh),
            self._field("最短:", self.det_min)], compact=True))
        v.addWidget(_OptionGrid([
            self._btn("清空切点", self._clear_points, small=True),
            self._btn("生成片段", self._segments_from_points, small=True),
            self._btn("检测", self._start_detect, small=True)], columns=3, cell_width=88))

        self.points_list = QListWidget()
        self.points_list.setMaximumHeight(56)
        v.addWidget(self.points_list)

        v.addWidget(_OptionGrid([
            self._btn("导出 CSV", self._export_csv, small=True),
            self._btn("导出 EDL", self._export_edl, small=True),
            self._btn("导出章节", self._export_chapters, small=True),
            self._btn("写入章节", self._write_chapters, small=True),
            self._btn("导入 CSV", self._import_csv, small=True)], columns=3, cell_width=100))
        v.addStretch(1)
        self._add_tab(tab, "切割")

    # ==================== 压缩页签 ====================

    def _build_compress_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        self.cmp_preset = QComboBox()
        for key, (label, _q, _r) in COMPRESS_PRESETS.items():
            self.cmp_preset.addItem(label, key)
        idx = self.cmp_preset.findData(cfg.get("editor_compress_preset", "normal"))
        self.cmp_preset.setCurrentIndex(max(0, idx))
        self.cmp_preset.currentIndexChanged.connect(
            lambda _i: self._save(editor_compress_preset=self.cmp_preset.currentData()))
        self.cmp_res = QComboBox()
        self.cmp_res.addItems(["保持原始", "1920x1080", "1280x720", "854x480"])
        self.cmp_res.setCurrentText(cfg.get("editor_compress_res", "保持原始"))
        self.cmp_res.currentTextChanged.connect(lambda t: self._save(editor_compress_res=t))
        v.addWidget(_OptionGrid([
            self._field("预设:", self.cmp_preset), self._field("分辨率:", self.cmp_res)], compact=True))
        self.cmp_clean = QCheckBox("清除元数据")
        self.cmp_clean.setChecked(bool(cfg.get("editor_compress_clean", True)))
        self.cmp_clean.toggled.connect(lambda c: self._save(editor_compress_clean=c))
        v.addWidget(self.cmp_clean)

        row2 = QHBoxLayout()
        self.cmp_btn = QPushButton("开始压缩")
        self.cmp_btn.setProperty("variant", "primary")
        self.cmp_btn.clicked.connect(self._start_compress)
        row2.addWidget(self.cmp_btn)
        self.cmp_stop = QPushButton("停止（当前文件完成后）")
        self.cmp_stop.setProperty("variant", "danger")
        self.cmp_stop.setEnabled(False)
        self.cmp_stop.clicked.connect(lambda: self._compress_queue.clear())
        row2.addWidget(self.cmp_stop)
        row2.addStretch(1)
        v.addLayout(row2)
        hint = QLabel("压缩会重新编码，输出到源目录：<原名>_compressed_video.mp4。")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        self._add_tab(tab, "压缩")

    # ==================== 轨道页签 ====================

    def _build_track_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        self.track_table = QTableWidget(0, 4)
        self.track_table.setHorizontalHeaderLabels(["轨道", "类型", "编码", "语言"])
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.track_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.track_table.horizontalHeader().setStretchLastSection(True)
        self.track_table.setColumnWidth(0, 60)
        self.track_table.setColumnWidth(1, 80)
        self.track_table.setColumnWidth(2, 120)
        # 流信息存在第 1 列的 UserRole 里，随 item 一起移动，排序后仍能取到
        self._track_sorter = table_sort.install(self.track_table, skip_columns=())
        v.addWidget(self.track_table, 1)

        v.addWidget(_OptionGrid([
            self._btn("重新读取", self._refresh_tracks, small=True),
            self._btn("提取选中", self._extract_selected_tracks, small=True),
            self._btn("提取全部", self._extract_all_tracks, small=True),
            self._btn("删除选中轨道", self._remove_tracks, small=True, danger=True)],
            columns=4, cell_width=112))

        ext_row = QHBoxLayout()
        ext_row.addWidget(self._btn("选择外部音轨", self._pick_ext_audio, small=True))
        self.ext_audio_label = QLabel("未选择")
        self.ext_audio_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.ext_audio_label.setMinimumWidth(0)
        self.ext_audio_label.setWordWrap(True)
        self.ext_audio_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ext_row.addWidget(self.ext_audio_label, 1)
        v.addLayout(ext_row)
        v.addWidget(_OptionGrid([
            self._btn("追加音轨", lambda: self._apply_ext_audio(False), small=True),
            self._btn("替换全部音轨", lambda: self._apply_ext_audio(True), small=True)],
            columns=2, cell_width=120))

        sub_row = QHBoxLayout()
        sub_row.addWidget(self._btn("选择字幕", self._pick_ext_sub, small=True))
        self.ext_sub_label = QLabel("未选择")
        self.ext_sub_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.ext_sub_label.setMinimumWidth(0)
        self.ext_sub_label.setWordWrap(True)
        self.ext_sub_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sub_row.addWidget(self.ext_sub_label, 1)
        sub_row.addWidget(self._btn("添加字幕", self._apply_ext_sub, small=True))
        sub_row.addStretch(1)
        v.addLayout(sub_row)
        hint = QLabel("提取不重新编码：音轨按编码落成 m4a/mp3/flac 等，字幕转 SRT（位图字幕保留原格式）。")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self._add_tab(tab, "轨道")

    # ==================== 元数据页签 ====================

    def _build_meta_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)

        self.meta_title = QLineEdit()
        self.meta_artist = QLineEdit()
        self.meta_comment = QLineEdit()
        self.meta_date = QLineEdit()
        edits = []
        for label, edit in (("标题", self.meta_title), ("备注", self.meta_comment),
                            ("作者", self.meta_artist), ("日期", self.meta_date)):
            edit.setPlaceholderText(label)
            edit.setAccessibleName(label)
            edit.setToolTip(label)
            edits.append(edit)
        v.addWidget(_OptionGrid(edits))
        v.addWidget(_OptionGrid([
            self._btn("读取当前", self._refresh_meta, small=True),
            self._btn("写入新文件", self._write_meta, small=True)], columns=2, cell_width=120))

        hint = QLabel("写入新文件，不改画质与旋转方向；MP4 / MKV 支持元数据。")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        self._add_tab(tab, "元数据")

    # ==================== 截图页签 ====================

    def _build_shot_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        self.shot_fmt = QComboBox()
        self.shot_fmt.addItems(["JPG", "PNG"])
        self.shot_interval = QSpinBox()
        self.shot_interval.setRange(1, 3600)
        self.shot_interval.setValue(int(cfg.get("editor_shot_interval", 10)))
        self.shot_interval.valueChanged.connect(
            lambda val: self._save(editor_shot_interval=val))
        self.shot_interval.setSuffix(" 秒")
        v.addWidget(_OptionGrid([
            self._field("帧格式:", self.shot_fmt), self._field("抽帧间隔:", self.shot_interval)], compact=True))
        self.shot_copy_cb = QCheckBox("截图后自动复制")
        self.shot_copy_cb.setChecked(bool(cfg.get("editor_shot_copy", True)))
        self.shot_copy_cb.setToolTip("截完图顺手进剪贴板，可直接粘到微信 / PS / PR")
        self.shot_copy_cb.toggled.connect(lambda on: self._save(editor_shot_copy=bool(on)))
        v.addWidget(self.shot_copy_cb)

        row2 = QHBoxLayout()
        self.shot_btn = QPushButton("当前帧")
        self.shot_btn.clicked.connect(self._shot_current)
        row2.addWidget(self.shot_btn)
        self.shot_copy_btn = QPushButton("复制当前帧")
        self.shot_copy_btn.setToolTip("不落盘，直接把当前画面以 PNG 复制到剪贴板")
        self.shot_copy_btn.clicked.connect(self._copy_current_frame)
        row2.addWidget(self.shot_copy_btn)
        self.shot_all_btn = QPushButton("按间隔抽帧")
        self.shot_all_btn.setProperty("variant", "primary")
        self.shot_all_btn.clicked.connect(self._shot_every)
        row2.addWidget(self.shot_all_btn)
        row2.addStretch(1)
        v.addLayout(row2)
        hint = QLabel("截图输出到源文件同目录；按间隔抽帧输出到「<文件名>_frames」文件夹。")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        hint2 = QLabel("「复制当前帧」不写文件：PNG 无损进剪贴板，可直接粘贴到 PS / PR / 微信。")
        hint2.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint2.setWordWrap(True)
        v.addWidget(hint2)
        v.addStretch(1)
        self._add_tab(tab, "截图")

    # ==================== 合并执行 ====================

    def _merge_output(self) -> Path:
        name = self.merge_name.text().strip() or f"合并_{datetime.now():%Y%m%d_%H%M%S}"
        name = name.replace("|", " ").replace("/", " ").replace("\\", " ")
        ext = self.merge_ext.currentText()
        if Path(name).suffix.lower() != ext:
            name += ext
        return unique_output_path(self._output_dir() / name)

    def _start_merge(self):
        if self.running:
            return
        if len(self.files) < 2:
            self.app.toast("至少添加 2 个视频才能合并", "warning")
            return
        missing = [f for f in self.files if not Path(f).exists()]
        if missing:
            self.app.toast(f"有 {len(missing)} 个文件不存在", "warning")
            return
        paths = [Path(f) for f in self.files]
        mode = self.merge_mode.currentData() or MODE_COPY
        out_path = self._merge_output()
        self._begin(f"开始合并 {len(paths)} 个文件（{'无损' if mode == MODE_COPY else '转码'}）",
                    f"输出: {out_path}")

        def work():
            infos = [probe_media(p) for p in paths]
            problems = check_compatibility(infos)
            if problems:
                post_to_main(self._log, f"检测到 {len(problems)} 项参数差异：")
                for p in problems[:10]:
                    post_to_main(self._log, f"   · {p}")
                if mode == MODE_COPY:
                    post_to_main(self._log, "无损拼接可能失败/花屏，建议改用「兼容转码」")
            else:
                post_to_main(self._log, "编码参数一致")
            return concat_media(paths, out_path, mode,
                                progress=lambda p, m: post_to_main(self._on_progress, p, m),
                                cancel=self._cancel.is_set)

        self._run(work, lambda r: f"合并完成: {Path(r).name}")

    # ==================== 切割执行 ====================

    def _start_cut(self):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择要切割的文件", "warning")
            return
        if not self.segments:
            self.app.toast("请先在预览区添加片段", "warning")
            return
        src = Path(self.current_path)
        export = self.cut_export.currentData()
        mode = self.cut_mode.currentData() or "copy"
        segments = list(self.segments)
        if export == "complement":
            total_ms = int(probe_media(src).duration * 1000)
            segments = complement_segments(segments, total_ms)
            if not segments:
                self.app.toast("选中的片段覆盖了整段视频", "warning")
                return
        out_dir = self._output_dir()
        merge = export in ("merge", "complement")
        merged_name = (self.cut_name.text().strip() or f"{src.stem}_片段合并")
        if not Path(merged_name).suffix:
            merged_name += ".mp4"

        self._begin(f"开始切割（{len(segments)} 段，{'精确' if mode == 'precise' else '无损'}）",
                    f"输出: {out_dir}")

        def work():
            return cut_segments(src, segments, out_dir, mode, merge=merge,
                                merged_name=merged_name,
                                progress=lambda p, m: post_to_main(self._on_progress, p, m),
                                cancel=self._cancel.is_set)

        self._run(work, lambda r: f"切割完成: " + ", ".join(p.name for p in r))

    # ==================== 自动检测 / 片段导出 ====================

    def _on_detect_type_changed(self, _index):
        kind = self.det_type.currentData()
        if kind == "silence":
            self.det_thresh.setRange(-60, -5)
            self.det_thresh.setValue(-30)
            self.det_thresh.setSuffix(" dB")
            self.det_min.setEnabled(True)
        elif kind == "black":
            self.det_thresh.setRange(0.02, 0.5)
            self.det_thresh.setValue(0.10)
            self.det_thresh.setSuffix("")
            self.det_min.setEnabled(True)
        else:
            self.det_thresh.setRange(0.1, 0.95)
            self.det_thresh.setValue(0.40)
            self.det_thresh.setSuffix("")
            self.det_min.setEnabled(False)

    def _start_detect(self):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        src = Path(self.current_path)
        kind = self.det_type.currentData()
        threshold = self.det_thresh.value()
        min_sec = self.det_min.value()
        label = self.det_type.currentText()
        self._begin(f"检测{label}（阈值 {threshold:g}）", "")

        def work():
            if kind == "silence":
                ranges = detect_silence(
                    src, threshold, min_sec,
                    progress=lambda p, m: post_to_main(self._on_progress, p, m),
                    cancel=self._cancel.is_set)
                return cut_points_from_ranges(ranges)
            if kind == "black":
                ranges = detect_black(
                    src, min_sec, threshold,
                    progress=lambda p, m: post_to_main(self._on_progress, p, m),
                    cancel=self._cancel.is_set)
                return cut_points_from_ranges(ranges)
            return detect_scenes(
                src, threshold,
                progress=lambda p, m: post_to_main(self._on_progress, p, m),
                cancel=self._cancel.is_set)

        self._run(work, self._apply_points)

    def _apply_points(self, points: List[int]) -> str:
        self.detect_points = sorted(set(int(p) for p in points))
        self.points_list.clear()
        for idx, point in enumerate(self.detect_points, 1):
            self.points_list.addItem(f"{idx}. {_fmt_ms(point)}")
        return f"检测完成：{len(self.detect_points)} 个切点"

    def _segments_from_points(self):
        if not self.detect_points:
            self.app.toast("先执行检测", "warning")
            return
        duration = self._duration_ms
        if duration <= 0 and self.current_path:
            try:
                duration = int(probe_media(Path(self.current_path)).duration * 1000)
            except Exception:
                duration = 0
        if duration <= 0:
            self.app.toast("无法读取视频时长", "warning")
            return
        self.segments = segments_from_points(self.detect_points, duration)
        self._refresh_segments()
        self._log(f"已按切点生成 {len(self.segments)} 个片段")

    def _clear_points(self):
        self.detect_points = []
        self.points_list.clear()

    def _export_target(self, suffix: str) -> Optional[Path]:
        if not self.segments:
            self.app.toast("还没有片段", "warning")
            return None
        base = Path(self.current_path).parent / f"{Path(self.current_path).stem}_片段{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, "导出片段", str(base))
        return Path(path) if path else None

    def _export_csv(self):
        path = self._export_target(".csv")
        if not path:
            return
        export_segments_csv(self.segments, path)
        self._log(f"已导出 CSV: {path.name}")
        self.app.toast(f"已导出: {path.name}", "success")

    def _export_edl(self):
        path = self._export_target(".edl")
        if not path:
            return
        export_edl(self.segments, path)
        self._log(f"已导出 EDL: {path.name}")
        self.app.toast(f"已导出: {path.name}", "success")

    def _export_chapters(self):
        path = self._export_target(".chapters.txt")
        if not path:
            return
        export_chapters(self.segments, path)
        self._log(f"已导出章节文件: {path.name}")
        self.app.toast(f"已导出: {path.name}", "success")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入片段 CSV", "", "CSV (*.csv);;所有文件 (*.*)")
        if not path:
            return
        try:
            self.segments = import_segments_csv(Path(path))
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"导入失败: {exc}", "error")
            return
        self._refresh_segments()
        self._log(f"已导入 {len(self.segments)} 个片段: {Path(path).name}")

    def _write_chapters(self):
        if self.running:
            return
        if not self.current_path or not self.segments:
            self.app.toast("先添加片段", "warning")
            return
        src = Path(self.current_path)
        ext = src.suffix if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
        out = unique_output_path(self._output_dir() / f"{src.stem}_章节{ext}")
        segments = list(self.segments)
        self._begin(f"写入章节到视频（{len(segments)} 个片段）", f"输出: {out}")
        self._run(lambda: write_chapters(src, segments, out),
                  lambda r: f"章节已写入: {Path(r).name}")

    # ==================== 压缩执行 ====================

    def _start_compress(self):
        if self.running:
            return
        if not self.files:
            self.app.toast("先添加要压缩的视频", "warning")
            return
        preset = COMPRESS_PRESETS.get(self.cmp_preset.currentData(),
                                      COMPRESS_PRESETS["normal"])
        _label, quality, ratio = preset
        resolution = self.cmp_res.currentText()
        options = {
            "codec": "auto",
            "quality": quality,
            "compression_ratio": ratio,
            "resolution": "keep" if resolution == "保持原始" else resolution,
            "fps": 0,
            "clear_metadata": self.cmp_clean.isChecked(),
            "bitrate_kbps": 0,
            "target_type": "keep",
            "output_dir": str(self._output_dir()),
        }
        self._compress_options = options
        self._compress_queue = list(self.files)
        self.running = True
        self._cancel = threading.Event()
        self.progress.setValue(0)
        self.cmp_stop.setEnabled(True)
        self.cmp_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._refresh_action_buttons()
        self._log(f"开始压缩 {len(self._compress_queue)} 个文件（{preset[0]}）")
        self._compress_next()

    def _compress_next(self):
        if not self._compress_queue or (self._cancel and self._cancel.is_set()):
            self._finish_run("压缩结束")
            return
        path = self._compress_queue.pop(0)
        self._log(f"压缩: {Path(path).name}")
        thread = MediaCompressThread(Path(path), self._compress_options, self)
        self._compress_thread = thread
        thread.progress.connect(self._log)
        thread.progress_detail.connect(self._on_compress_detail)
        thread.finished_ok.connect(lambda _r: self._compress_next())
        thread.failed.connect(self._on_compress_failed)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_compress_detail(self, percent: float, speed: str):
        if percent >= 0:
            done = len(self.files) - len(self._compress_queue)
            overall = int((done + percent / 100) / max(1, len(self.files)) * 100)
            self.progress.setValue(min(99, overall))
        if speed:
            self.progress.setFormat(f"{percent:.0f}% {speed}")

    def _on_compress_failed(self, message: str):
        self._log(f"压缩失败: {message[:160]}")
        self._compress_next()

    # ==================== 轨道 ====================

    def _refresh_tracks(self):
        self.track_table.setRowCount(0)
        if not self.current_path:
            return
        try:
            tracks = probe_tracks(Path(self.current_path))
        except Exception as exc:  # noqa: BLE001
            self._log(f"读取轨道失败: {exc}")
            return
        self.track_table.setRowCount(len(tracks))
        for row, stream in enumerate(tracks):
            kind = "音轨" if stream.get("codec_type") == "audio" else "字幕"
            language = ((stream.get("tags") or {}).get("language") or "-")
            self.track_table.setItem(row, 0, QTableWidgetItem(str(stream.get("index"))))
            self.track_table.setItem(row, 1, QTableWidgetItem(kind))
            self.track_table.setItem(row, 2, QTableWidgetItem(str(stream.get("codec_name"))))
            self.track_table.setItem(row, 3, QTableWidgetItem(str(language)))
            self.track_table.item(row, 0).setData(Qt.UserRole, stream)
        self._track_sorter.reapply()

    def _selected_track_streams(self):
        rows = sorted({i.row() for i in self.track_table.selectedItems()})
        if not rows:
            rows = list(range(self.track_table.rowCount()))
        return [self.track_table.item(r, 0).data(Qt.UserRole) for r in rows
                if self.track_table.item(r, 0) is not None]

    def _extract_selected_tracks(self):
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        streams = self._selected_track_streams()
        if not streams:
            self.app.toast("没有可提取的轨道", "warning")
            return
        src = Path(self.current_path)
        out_dir = self._output_dir()

        def work():
            outputs = []
            for idx, stream in enumerate(streams):
                post_to_main(self._on_progress, int(100 * idx / len(streams)),
                             f"提取轨道 {idx + 1}/{len(streams)}")
                outputs.append(extract_track(src, stream, out_dir))
            return outputs

        self._begin(f"提取 {len(streams)} 条轨道", "")
        self._run(work, lambda r: "已提取: " + ", ".join(p.name for p in r))

    def _extract_all_tracks(self):
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        src = Path(self.current_path)
        out_dir = self._output_dir()
        self._begin("提取全部轨道", "")
        self._run(
            lambda: extract_all_tracks(
                src, out_dir,
                progress=lambda p, m: post_to_main(self._on_progress, p, m)),
            lambda r: "已提取: " + ", ".join(p.name for p in r))

    # ==================== 轨道增删替换 ====================

    def _pick_ext_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择外部音轨", "",
            "音频文件 (*.m4a *.mp3 *.aac *.flac *.wav *.opus *.ogg *.ac3);;所有文件 (*.*)")
        if path:
            self.ext_audio_path = path
            self.ext_audio_label.setText(Path(path).name)
            self.ext_audio_label.setStyleSheet(
                "color: palette(link-visited); font-size: 11px;")

    def _pick_ext_sub(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", "",
            "字幕文件 (*.srt *.ass *.ssa *.vtt);;所有文件 (*.*)")
        if path:
            self.ext_sub_path = path
            self.ext_sub_label.setText(Path(path).name)
            self.ext_sub_label.setStyleSheet(
                "color: palette(link-visited); font-size: 11px;")

    def _apply_ext_audio(self, replace: bool):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        if not self.ext_audio_path or not Path(self.ext_audio_path).exists():
            self.app.toast("先选择外部音轨文件", "warning")
            return
        src = Path(self.current_path)
        ext = src.suffix if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
        tag = "替换音轨" if replace else "追加音轨"
        out = unique_output_path(self._output_dir() / f"{src.stem}_{tag}{ext}")
        audio_file = Path(self.ext_audio_path)
        self._begin(f"{tag}: {audio_file.name}", f"输出: {out}")
        self._run(
            lambda: add_external_audio(src, audio_file, out, replace=replace),
            lambda r: f"{tag}完成: {Path(r).name}")

    def _apply_ext_sub(self):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        if not self.ext_sub_path or not Path(self.ext_sub_path).exists():
            self.app.toast("先选择字幕文件", "warning")
            return
        src = Path(self.current_path)
        ext = src.suffix if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
        out = unique_output_path(self._output_dir() / f"{src.stem}_加字幕{ext}")
        sub_file = Path(self.ext_sub_path)
        self._begin(f"添加字幕: {sub_file.name}", f"输出: {out}")
        self._run(lambda: add_external_subtitle(src, sub_file, out),
                  lambda r: f"字幕已添加: {Path(r).name}")

    def _remove_tracks(self):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        streams = self._selected_track_streams()
        if not streams:
            self.app.toast("先在轨道表里选中要删除的轨道", "warning")
            return
        src = Path(self.current_path)
        total_streams = len(probe_media(src).streams)
        if total_streams - len(streams) <= 0:
            self.app.toast("不能删除全部轨道", "warning")
            return
        ext = src.suffix if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
        out = unique_output_path(self._output_dir() / f"{src.stem}_删轨{ext}")
        indices = [int(s.get("index")) for s in streams]
        self._begin(f"删除 {len(indices)} 条轨道", f"输出: {out}")
        self._run(lambda: remove_tracks(src, indices, out),
                  lambda r: f"已删除轨道: {Path(r).name}")

    # ==================== 元数据 ====================

    def _refresh_meta(self):
        if not self.current_path:
            return
        try:
            meta = read_metadata(Path(self.current_path))
        except Exception as exc:  # noqa: BLE001
            self._log(f"读取元数据失败: {exc}")
            return
        self.meta_title.setText(meta.get("title", ""))
        self.meta_artist.setText(meta.get("artist", ""))
        self.meta_comment.setText(meta.get("comment", ""))
        self.meta_date.setText(meta.get("date", ""))
        idx = self.meta_rotation.findData(meta.get("rotation", 0) or 0)
        self.meta_rotation.setCurrentIndex(idx if idx >= 0 else 0)

    def _write_meta(self):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        src = Path(self.current_path)
        ext = src.suffix if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
        out = unique_output_path(self._output_dir() / f"{src.stem}_meta{ext}")
        tags = {
            "title": self.meta_title.text().strip(),
            "artist": self.meta_artist.text().strip(),
            "comment": self.meta_comment.text().strip(),
            "date": self.meta_date.text().strip(),
        }
        self._begin("写入元数据", f"输出: {out}")
        self._run(lambda: write_metadata(src, out, tags, None),
                  lambda r: f"元数据已写入: {Path(r).name}")

    def _write_rotation(self):
        if self.running or not self.current_path:
            return
        rotation = self.meta_rotation.currentData()
        if rotation is None:
            self.app.toast("先选择旋转角度", "warning")
            return
        src = Path(self.current_path)
        ext = src.suffix if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
        out = unique_output_path(self._output_dir() / f"{src.stem}_rotate{ext}")
        self._begin("导出旋转视频", f"输出: {out}")
        self._run(lambda: write_metadata(src, out, {}, rotation),
                  lambda r: f"旋转已导出: {Path(r).name}")

    # ==================== 截图 ====================

    def _shot_current(self):
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        src = Path(self.current_path)
        position = self.player.position() or self.slider.value()
        fmt = "png" if self.shot_fmt.currentText() == "PNG" else "jpg"
        try:
            out = snapshot(src, position, self._output_dir(), image_format=fmt)
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"截图失败: {exc}", "error")
            return
        self._log(f"已截图: {out.name}")
        if self.shot_copy_cb.isChecked():
            self._copy_frame(src, position, saved_name=out.name)
        else:
            self.app.toast(f"截图已保存: {out.name}", "success")

    # ---------- 复制到剪贴板 ----------

    def _copy_current_frame(self):
        """把当前播放位置的一帧以 PNG 直接复制到剪贴板（不落盘）。"""
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        self._copy_frame(Path(self.current_path),
                         self.player.position() or self.slider.value())

    def _copy_frame(self, src: Path, position: int, saved_name: str = ""):
        def job():
            return frame_bytes(src, position, "png")

        def done(data, err):
            if err:
                self.app.toast(f"复制失败: {err}", "error")
                return
            image = QImage()
            if not image.loadFromData(data or b""):
                self.app.toast("复制失败：帧数据无法解码", "error")
                return
            QApplication.clipboard().setImage(image)
            size = f"{image.width()}×{image.height()}"
            if saved_name:
                self.app.toast(f"截图已保存并复制: {saved_name}", "success")
                self._log(f"同时复制到剪贴板（{size}）")
            else:
                self.app.toast(f"已复制当前帧到剪贴板（{size}）", "success")
                self._log(f"复制当前帧 {_fmt_time(position)} → 剪贴板（{size}）")

        run_bg(job, done)

    def _shot_every(self):
        if self.running:
            return
        if not self.current_path:
            self.app.toast("先在左侧选择文件", "warning")
            return
        src = Path(self.current_path)
        interval = self.shot_interval.value()
        out_dir = self._output_dir() / f"{src.stem}_frames"
        self._begin(f"按 {interval}s 间隔抽帧", f"输出: {out_dir}")
        self._run(
            lambda: snapshot_every(
                src, interval, out_dir,
                progress=lambda p, m: post_to_main(self._on_progress, p, m),
                cancel=self._cancel.is_set),
            lambda r: f"已导出 {len(r)} 帧")

    # ==================== 运行框架 ====================

    def _begin(self, log_line: str, extra: str):
        self.running = True
        self._cancel = threading.Event()
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.stop_btn.setEnabled(True)
        self.log_text.clear()
        self._log(log_line)
        if extra:
            self._log(extra)
        self._refresh_action_buttons()

    def _stop_all(self):
        if self._cancel is not None:
            self._cancel.set()
        self._compress_queue.clear()
        self.stop_btn.setEnabled(False)
        self._log("已请求停止...")

    def _on_progress(self, percent: int, message: str):
        self.progress.setValue(max(0, min(100, int(percent))))
        if message and percent - getattr(self, "_last_log_pct", -100) >= 10:
            self._last_log_pct = percent
            self._log(message)

    def _run(self, work, success_text):
        self._last_log_pct = -100

        def done(result, err):
            self.running = False
            self.cmp_stop.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._refresh_action_buttons()
            if err:
                if "已停止" in str(err):
                    self._log("已停止")
                else:
                    self._log(f"失败: {str(err)[:200]}")
                    self.app.toast("操作失败，详情见日志", "error")
                return
            self.progress.setValue(100)
            self._log(success_text(result))
            self.app.toast(success_text(result), "success")

        run_bg(work, done)

    def _finish_run(self, message: str):
        self.running = False
        self._cancel = None
        self.progress.setValue(100)
        self.cmp_stop.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self._log(message)
        self._refresh_action_buttons()
