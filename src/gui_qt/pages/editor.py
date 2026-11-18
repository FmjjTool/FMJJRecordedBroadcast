"""视频操作页：批量转换 + 预览 / 合并 / 切割 / 压缩 / 轨道提取 / 截图。

布局：
- 左侧：文件队列（合并顺序，可拖拽/上移下移）
- 右上：内嵌播放器 + 时间轴 + 起止点
- 右下：转换 / 合并 / 切割 / 压缩 / 轨道 / 截图 / 元数据页签

合并逻辑与 LC 对齐：concat demuxer + 参数兼容性检查；切割默认无损（关键帧边界），
需要精确时可选重编码。压缩复用 image_sorter 的 MediaCompressThread。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStyle,
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
from toolbox.edit_engine import (
    add_external_audio, add_external_subtitle, complement_segments,
    cut_points_from_ranges, cut_segments, detect_black, detect_scenes,
    detect_silence, export_chapters, export_edl, export_segments_csv,
    extract_all_tracks, extract_track, frame_bytes, import_segments_csv,
    probe_tracks, read_metadata, remove_tracks, segments_from_points, snapshot,
    snapshot_every, write_chapters, write_metadata,
)
from toolbox.utils import format_size, is_video_file

from gui_qt.common import post_to_main, run_bg
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


class _SeekSlider(QSlider):
    """可点即跳的进度条：点击轨道任意位置直接跳到该处。

    QSlider 默认只有拖手柄才改值，点击轨道走 pageStep（一次 1/10 时长），
    用起来像「点不动」。这里把点击位置直接换算成时间点再发 clickedAt。
    """

    clickedAt = Signal(int)

    def mousePressEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(
                QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                span = max(1, self.width() - handle.width())
                pos = int(event.position().x() - handle.width() / 2)
                value = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), pos, span,
                    option.upsideDown)
                self.setValue(value)
                self.clickedAt.emit(value)
                event.accept()
                return
        super().mousePressEvent(event)


class EditorPage(BasePage):
    SESSION_FIELDS = ("files", "current_path", "segments", "detect_points")

    def restore_session_data(self):
        segments = self.segments
        points = self.detect_points
        current = self.current_path
        self._refresh_files(self.files.index(current) if current in self.files else 0)
        self.segments = segments
        self.detect_points = points
        self._refresh_segments()

    def has_active_tasks(self):
        return self.running

    def release_view_resources(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.hide()

    def on_show(self):
        if self.current_path:
            self.video.show()
            source = QUrl.fromLocalFile(self.current_path)
            if self.player.source() != source:
                self.player.setSource(source)
            self.player.pause()

    def on_hide(self):
        self.player.pause()

    def __init__(self, app):
        super().__init__(app)
        self.files: List[str] = []
        self.current_path: Optional[str] = None
        self.segments: List[Tuple[int, int]] = []
        self.detect_points: List[int] = []
        self.ext_audio_path: Optional[str] = None
        self.ext_sub_path: Optional[str] = None
        self._pending_start: Optional[int] = None
        self._dragging_slider = False
        self._last_seek_at = 0.0
        self._duration_ms = 0
        self.running = False
        self._cancel: Optional[threading.Event] = None
        self._compress_queue: List[str] = []
        self._compress_thread = None

        cfg = load_convert_config()
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        body = QHBoxLayout()
        body.setSpacing(8)
        root.addLayout(body, 1)

        # ==================== 左：文件队列 ====================
        left = QVBoxLayout()
        left.setSpacing(4)
        row1 = QHBoxLayout()
        row1.addWidget(self._btn("扫描工作目录", self._scan_work_dir))
        row1.addWidget(self._btn("添加文件", self._add_files))
        row1.addWidget(self._btn("添加文件夹", self._add_folder))
        left.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(self._btn("按名排序", self._sort_by_name, small=True))
        row2.addWidget(self._btn("↑", lambda: self._move(-1), small=True))
        row2.addWidget(self._btn("↓", lambda: self._move(1), small=True))
        row2.addWidget(self._btn("×", self._delete_selected, small=True, danger=True))
        row2.addWidget(self._btn("清空", self._clear, small=True))
        left.addLayout(row2)
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
        self.left_wrap.setFixedWidth(330)
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
        preview_area.setMinimumHeight(260)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(100)
        self.video.setStyleSheet("background: #000;")
        preview_layout.addWidget(self.video, 3)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.errorOccurred.connect(self._on_player_error)

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
            " background: palette(highlight); border-radius: 4px; }"
            " QSlider::handle:horizontal { width: 16px; height: 16px; margin: -4px 0;"
            " border-radius: 8px; background: palette(base);"
            " border: 2px solid palette(highlight); }"
            " QSlider::handle:horizontal:hover { background: palette(midlight); }"
            " QSlider::groove:horizontal:disabled { background: palette(alternate-base); }"
        )
        self.slider.clickedAt.connect(self._seek_to)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)

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

        ctrl = QHBoxLayout()
        self.play_btn = QPushButton("▶ 播放")
        self.play_btn.setFixedWidth(80)
        self.play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self.play_btn)
        self.set_start_btn = self._btn("起点", self._set_start, small=True)
        self.set_end_btn = self._btn("终点", self._set_end, small=True)
        self.remove_seg_btn = self._btn("删除选中片段", self._remove_segment, small=True)
        self.clear_seg_btn = self._btn("清空片段", self._clear_segments, small=True)
        self.shot_current_btn = self._btn("截图当前帧", self._shot_current, small=True)
        for widget in (self.set_start_btn, self.set_end_btn, self.remove_seg_btn,
                       self.clear_seg_btn, self.shot_current_btn):
            ctrl.addWidget(widget)
        ctrl.addStretch(1)
        preview_layout.addLayout(ctrl)

        # 快捷转跳
        jump_row = QHBoxLayout()
        jump_row.addWidget(QLabel("转跳:"))
        self.jump_buttons = []
        for text, delta in (("⏮", None), ("-10s", -10000), ("-1s", -1000),
                            ("+1s", 1000), ("+10s", 10000), ("⏭", "end")):
            btn = QPushButton(text)
            btn.setFixedWidth(44)
            btn.setStyleSheet("padding: 2px 4px;")
            if delta is None:
                btn.clicked.connect(lambda: self._seek_to(0))
            elif delta == "end":
                btn.clicked.connect(lambda: self._seek_to(self._duration_ms))
            else:
                btn.clicked.connect(lambda _c=False, d=delta: self._jump(d))
            self.jump_buttons.append(btn)
            jump_row.addWidget(btn)
        self.jump_input = QLineEdit()
        self.jump_input.setPlaceholderText("00:30 跳转")
        self.jump_input.setFixedWidth(110)
        self.jump_input.returnPressed.connect(self._jump_to_text)
        jump_row.addWidget(self.jump_input)
        self.jump_btn = QPushButton("跳转")
        self.jump_btn.setStyleSheet("padding: 2px 8px;")
        self.jump_btn.clicked.connect(self._jump_to_text)
        jump_row.addWidget(self.jump_btn)
        jump_row.addStretch(1)
        preview_layout.addLayout(jump_row)

        self.seg_list = QListWidget()
        self.seg_list.setMinimumHeight(40)
        self.seg_list.setMaximumHeight(64)
        # Ignored：竖直方向不参与「最小高度」争夺，窗口变矮时先压它和日志，
        # 而不是把视频和进度条一起挤成一条缝。
        self.seg_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        preview_layout.addWidget(self.seg_list)
        self.operation_splitter.addWidget(preview_area)

        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        operation_scroll = QScrollArea()
        operation_scroll.setWidgetResizable(True)
        operation_scroll.setMinimumHeight(210)
        operation_scroll.setWidget(self.tabs)
        self.operation_splitter.addWidget(operation_scroll)
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
        self.log_text.setMinimumHeight(60)
        self.log_text.setMaximumHeight(90)
        self.log_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right.addWidget(self.log_text)
        body.addLayout(right, 1)

    # ==================== 小工具 ====================

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

    # ==================== 文件队列 ====================

    def add_dropped_files(self, files):
        added = []
        for f in files:
            if is_video_file(f) and f not in self.files:
                self.files.append(f)
                added.append(f)
        if added:
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
        """直接扫描全局工作目录，免去手动选目录。"""
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
        if row < 0 or row >= len(self.files):
            self.current_path = None
            self._duration_ms = 0
            self.player.stop()
            self.player.setSource(QUrl())
            self.play_btn.setText("▶ 播放")
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
        self.segments = []
        self.detect_points = []
        self.points_list.clear()
        self._refresh_segments()
        self._load_preview(self.current_path)
        self._refresh_tracks()
        self._refresh_meta()
        self._set_preview_enabled(True)

    # ==================== 预览 ====================

    def _load_preview(self, path: str):
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()
        self.slider.setValue(0)
        self.play_btn.setText("▶ 播放")
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
            self.play_btn.setText("▶ 播放")
        else:
            self.player.play()
            self.play_btn.setText("暂停")

    def _seek_to(self, position_ms: int):
        if not self.current_path:
            return
        self.player.setPosition(max(0, int(position_ms)))
        self._on_position(max(0, int(position_ms)))

    def _jump(self, delta_ms: int):
        if not self.current_path:
            return
        current = self.player.position() or self.slider.value()
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
                       self.remove_seg_btn, self.clear_seg_btn,
                       self.shot_current_btn, self.jump_input, self.jump_btn,
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

    # ==================== 转换页签 ====================

    def _build_convert_tab(self, cfg):
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        content = QWidget()
        tab.setWidget(content)
        v = QVBoxLayout(content)
        v.setContentsMargins(8, 8, 8, 8)
        hint = QLabel("转换左侧整个文件队列，优先无损封装。成功后队列切换为输出文件，可继续剪辑。")
        hint.setWordWrap(True)
        v.addWidget(hint)
        row = QHBoxLayout()
        self.convert_scan_btn = self._btn("扫描待转换视频", self._scan_convert)
        row.addWidget(self.convert_scan_btn)
        self.convert_btn = self._btn("开始转换", self._start_convert)
        self.convert_btn.setProperty("variant", "primary")
        row.addWidget(self.convert_btn)
        self.convert_clear_btn = self._btn("清除转换缓存", self._clear_convert_cache)
        self.convert_clear_btn.setToolTip("只清除转换记录，不删除视频文件。")
        row.addWidget(self.convert_clear_btn)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("输出格式:"))
        self.convert_ext = QComboBox()
        self.convert_ext.addItems([".mp4", ".mkv", ".mov"])
        self.convert_ext.setCurrentText(cfg.get("output_extension", ".mp4"))
        self.convert_ext.currentTextChanged.connect(lambda t: self._save(output_extension=t))
        row.addWidget(self.convert_ext)
        row.addStretch(1)
        v.addLayout(row)
        options = QGridLayout()
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
            options.addWidget(checkbox, index // 3, index % 3)
            self.convert_options[key] = checkbox
        self.convert_options["delete_source_after_success"].setToolTip("转换成功后删除原始视频。")
        v.addLayout(options)
        self.convert_hint = QLabel()
        self.convert_hint.setWordWrap(True)
        self.convert_hint.setMinimumWidth(0)
        self.convert_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.convert_hint)
        v.addStretch(1)
        self.convert_tab_index = self.tabs.addTab(tab, "转换")
        self._refresh_convert_hint()

    def open_convert_tab(self):
        self.tabs.setCurrentIndex(self.convert_tab_index)

    def on_dir_changed(self, _path):
        self._refresh_convert_hint()

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
        self.player.stop()
        self.player.setSource(QUrl())

    def _on_player_error(self, *_args):
        self._log("该格式无法内嵌预览（不影响切割/合并/提取）")

    # ==================== 片段 ====================

    def _set_start(self):
        self._pending_start = self.player.position() or self.slider.value()
        self._log(f"起点: {_fmt_ms(self._pending_start)}")

    def _set_end(self):
        end = self.player.position() or self.slider.value()
        start = self._pending_start
        if start is None:
            start = 0
        if end <= start:
            self.app.toast("终点需要大于起点", "warning")
            return
        self.segments.append((int(start), int(end)))
        self.segments.sort()
        self._pending_start = None
        self._refresh_segments()
        self._log(f"已添加片段 {_fmt_ms(start)} → {_fmt_ms(end)}")

    def _refresh_segments(self):
        self.seg_list.clear()
        for idx, (start, end) in enumerate(self.segments, 1):
            item = QListWidgetItem(
                f"{idx}. {_fmt_ms(start)} → {_fmt_ms(end)}  ({_fmt_ms(end - start)})")
            item.setData(Qt.UserRole, (start, end))
            self.seg_list.addItem(item)
        self._refresh_action_buttons()

    def _remove_segment(self):
        row = self.seg_list.currentRow()
        if 0 <= row < len(self.segments):
            self.segments.pop(row)
            self._refresh_segments()

    def _clear_segments(self):
        self.segments = []
        self._pending_start = None
        self._refresh_segments()

    # ==================== 合并页签 ====================

    def _build_merge_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("合并方式:"))
        self.merge_mode = QComboBox()
        self.merge_mode.addItem("无损拼接（推荐）", MODE_COPY)
        self.merge_mode.addItem("兼容转码（参数不一致时）", MODE_ENCODE)
        idx = self.merge_mode.findData(cfg.get("merge_mode", MODE_COPY))
        self.merge_mode.setCurrentIndex(max(0, idx))
        self.merge_mode.currentIndexChanged.connect(
            lambda _i: self._save(merge_mode=self.merge_mode.currentData()))
        row.addWidget(self.merge_mode)
        row.addWidget(QLabel("输出:"))
        self.merge_ext = QComboBox()
        self.merge_ext.addItems([".mp4", ".mkv"])
        self.merge_ext.setCurrentText(cfg.get("merge_out_ext", ".mp4"))
        self.merge_ext.currentTextChanged.connect(lambda t: self._save(merge_out_ext=t))
        row.addWidget(self.merge_ext)
        row.addStretch(1)
        v.addLayout(row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("文件名:"))
        self.merge_name = QLineEdit()
        self.merge_name.setPlaceholderText("留空 = 合并_日期_时间")
        name_row.addWidget(self.merge_name, 1)
        self.merge_btn = QPushButton("开始合并")
        self.merge_btn.setProperty("variant", "primary")
        self.merge_btn.clicked.connect(self._start_merge)
        name_row.addWidget(self.merge_btn)
        v.addLayout(name_row)
        v.addWidget(QLabel("合并按左侧列表顺序执行；参数不一致会提示，可改用「兼容转码」。"))
        v.addStretch(1)
        self.tabs.addTab(tab, "合并")

    # ==================== 切割页签 ====================

    def _build_cut_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("导出方式:"))
        self.cut_export = QComboBox()
        self.cut_export.addItem("逐段导出", "each")
        self.cut_export.addItem("拼接成一个文件", "merge")
        self.cut_export.addItem("删除选中片段（导出剩余）", "complement")
        idx = self.cut_export.findData(cfg.get("editor_cut_export", "each"))
        self.cut_export.setCurrentIndex(max(0, idx))
        self.cut_export.currentIndexChanged.connect(
            lambda _i: self._save(editor_cut_export=self.cut_export.currentData()))
        row.addWidget(self.cut_export)
        row.addWidget(QLabel("方式:"))
        self.cut_mode = QComboBox()
        self.cut_mode.addItem("无损（关键帧边界，快）", "copy")
        self.cut_mode.addItem("精确（重编码，准）", "precise")
        idx = self.cut_mode.findData(cfg.get("editor_cut_mode", "copy"))
        self.cut_mode.setCurrentIndex(max(0, idx))
        self.cut_mode.currentIndexChanged.connect(
            lambda _i: self._save(editor_cut_mode=self.cut_mode.currentData()))
        row.addWidget(self.cut_mode)
        row.addStretch(1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("合并文件名:"))
        self.cut_name = QLineEdit()
        self.cut_name.setPlaceholderText("留空 = 原文件名_片段合并")
        row2.addWidget(self.cut_name, 1)
        self.cut_btn = QPushButton("开始切割")
        self.cut_btn.setProperty("variant", "primary")
        self.cut_btn.clicked.connect(self._start_cut)
        row2.addWidget(self.cut_btn)
        v.addLayout(row2)

        hint = QLabel("在预览区拖时间轴，点「起点」「终点」添加片段。无损切割以关键帧为界，"
                      "实际起止可能前后偏移；要精确请选「精确」（会转码）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        v.addWidget(hint)

        # 自动检测（静音/黑帧/场景）→ 切点 → 片段
        det_row = QHBoxLayout()
        det_row.addWidget(QLabel("自动检测:"))
        self.det_type = QComboBox()
        self.det_type.addItem("静音", "silence")
        self.det_type.addItem("黑帧", "black")
        self.det_type.addItem("场景切换", "scene")
        self.det_type.currentIndexChanged.connect(self._on_detect_type_changed)
        det_row.addWidget(self.det_type)
        det_row.addWidget(QLabel("阈值:"))
        self.det_thresh = QDoubleSpinBox()
        self.det_thresh.setDecimals(2)
        self.det_thresh.setRange(-60, 1)
        self.det_thresh.setValue(-30)
        self.det_thresh.setSuffix(" dB")
        det_row.addWidget(self.det_thresh)
        det_row.addWidget(QLabel("最短:"))
        self.det_min = QDoubleSpinBox()
        self.det_min.setRange(0.1, 10.0)
        self.det_min.setSingleStep(0.1)
        self.det_min.setValue(0.5)
        self.det_min.setSuffix(" 秒")
        det_row.addWidget(self.det_min)
        det_row.addWidget(self._btn("检测", self._start_detect, small=True))
        det_row.addWidget(self._btn("生成片段", self._segments_from_points, small=True))
        det_row.addWidget(self._btn("清空切点", self._clear_points, small=True))
        det_row.addStretch(1)
        v.addLayout(det_row)

        self.points_list = QListWidget()
        self.points_list.setMaximumHeight(56)
        v.addWidget(self.points_list)

        exp_row = QHBoxLayout()
        exp_row.addWidget(QLabel("片段导出:"))
        exp_row.addWidget(self._btn("CSV", self._export_csv, small=True))
        exp_row.addWidget(self._btn("EDL", self._export_edl, small=True))
        exp_row.addWidget(self._btn("章节文件", self._export_chapters, small=True))
        exp_row.addWidget(self._btn("写入章节到视频", self._write_chapters, small=True))
        exp_row.addWidget(self._btn("导入 CSV", self._import_csv, small=True))
        exp_row.addStretch(1)
        v.addLayout(exp_row)
        v.addStretch(1)
        self.tabs.addTab(tab, "切割")

    # ==================== 压缩页签 ====================

    def _build_compress_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("预设:"))
        self.cmp_preset = QComboBox()
        for key, (label, _q, _r) in COMPRESS_PRESETS.items():
            self.cmp_preset.addItem(label, key)
        idx = self.cmp_preset.findData(cfg.get("editor_compress_preset", "normal"))
        self.cmp_preset.setCurrentIndex(max(0, idx))
        self.cmp_preset.currentIndexChanged.connect(
            lambda _i: self._save(editor_compress_preset=self.cmp_preset.currentData()))
        row.addWidget(self.cmp_preset)
        row.addWidget(QLabel("分辨率:"))
        self.cmp_res = QComboBox()
        self.cmp_res.addItems(["保持原始", "1920x1080", "1280x720", "854x480"])
        self.cmp_res.setCurrentText(cfg.get("editor_compress_res", "保持原始"))
        self.cmp_res.currentTextChanged.connect(lambda t: self._save(editor_compress_res=t))
        row.addWidget(self.cmp_res)
        self.cmp_clean = QCheckBox("清除元数据")
        self.cmp_clean.setChecked(bool(cfg.get("editor_compress_clean", True)))
        self.cmp_clean.toggled.connect(lambda c: self._save(editor_compress_clean=c))
        row.addWidget(self.cmp_clean)
        row.addStretch(1)
        v.addLayout(row)

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
        v.addWidget(hint)
        v.addStretch(1)
        self.tabs.addTab(tab, "压缩")

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
        v.addWidget(self.track_table, 1)

        row = QHBoxLayout()
        row.addWidget(self._btn("重新读取", self._refresh_tracks, small=True))
        row.addWidget(self._btn("提取选中", self._extract_selected_tracks, small=True))
        row.addWidget(self._btn("提取全部", self._extract_all_tracks, small=True))
        row.addWidget(self._btn("删除选中轨道", self._remove_tracks, small=True, danger=True))
        row.addStretch(1)
        v.addLayout(row)

        ext_row = QHBoxLayout()
        ext_row.addWidget(self._btn("选择外部音轨", self._pick_ext_audio, small=True))
        self.ext_audio_label = QLabel("未选择")
        self.ext_audio_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        ext_row.addWidget(self.ext_audio_label, 1)
        ext_row.addWidget(self._btn("追加音轨", lambda: self._apply_ext_audio(False), small=True))
        ext_row.addWidget(self._btn("替换全部音轨", lambda: self._apply_ext_audio(True), small=True))
        v.addLayout(ext_row)

        sub_row = QHBoxLayout()
        sub_row.addWidget(self._btn("选择字幕", self._pick_ext_sub, small=True))
        self.ext_sub_label = QLabel("未选择")
        self.ext_sub_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        sub_row.addWidget(self.ext_sub_label, 1)
        sub_row.addWidget(self._btn("添加字幕", self._apply_ext_sub, small=True))
        sub_row.addStretch(1)
        v.addLayout(sub_row)
        hint = QLabel("提取不重新编码：音轨按编码落成 m4a/mp3/flac 等，字幕转 SRT（位图字幕保留原格式）。")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.tabs.addTab(tab, "轨道")

    # ==================== 元数据页签 ====================

    def _build_meta_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)

        grid = QHBoxLayout()
        col1 = QVBoxLayout()
        self.meta_title = QLineEdit()
        self.meta_artist = QLineEdit()
        col1.addWidget(QLabel("标题"))
        col1.addWidget(self.meta_title)
        col1.addWidget(QLabel("作者"))
        col1.addWidget(self.meta_artist)
        grid.addLayout(col1, 1)
        col2 = QVBoxLayout()
        self.meta_comment = QLineEdit()
        self.meta_date = QLineEdit()
        col2.addWidget(QLabel("备注"))
        col2.addWidget(self.meta_comment)
        col2.addWidget(QLabel("日期"))
        col2.addWidget(self.meta_date)
        grid.addLayout(col2, 1)
        v.addLayout(grid)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("旋转:"))
        self.meta_rotation = QComboBox()
        self.meta_rotation.addItem("保持原样", None)
        self.meta_rotation.addItem("0°", 0)
        self.meta_rotation.addItem("90°", 90)
        self.meta_rotation.addItem("180°", 180)
        self.meta_rotation.addItem("270°", 270)
        rot_row.addWidget(self.meta_rotation)
        rot_row.addStretch(1)
        rot_row.addWidget(self._btn("读取当前", self._refresh_meta, small=True))
        rot_row.addWidget(self._btn("写入新文件", self._write_meta, small=True))
        v.addLayout(rot_row)

        hint = QLabel("写入采用重新封装（-c copy），不改画质；mp4/mkv 支持元数据，"
                      "旋转对 mp4 的显示矩阵生效，mkv 可能被忽略。")
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        self.tabs.addTab(tab, "元数据")

    # ==================== 截图页签 ====================

    def _build_shot_tab(self, cfg):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("帧格式:"))
        self.shot_fmt = QComboBox()
        self.shot_fmt.addItems(["JPG", "PNG"])
        row.addWidget(self.shot_fmt)
        row.addWidget(QLabel("抽帧间隔(秒):"))
        self.shot_interval = QSpinBox()
        self.shot_interval.setRange(1, 3600)
        self.shot_interval.setValue(int(cfg.get("editor_shot_interval", 10)))
        self.shot_interval.valueChanged.connect(
            lambda val: self._save(editor_shot_interval=val))
        row.addWidget(self.shot_interval)
        self.shot_copy_cb = QCheckBox("截图后自动复制")
        self.shot_copy_cb.setChecked(bool(cfg.get("editor_shot_copy", True)))
        self.shot_copy_cb.setToolTip("截完图顺手进剪贴板，可直接粘到微信 / PS / PR")
        self.shot_copy_cb.toggled.connect(lambda on: self._save(editor_shot_copy=bool(on)))
        row.addWidget(self.shot_copy_cb)
        row.addStretch(1)
        v.addLayout(row)

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
        v.addWidget(hint)
        hint2 = QLabel("「复制当前帧」不写文件：PNG 无损进剪贴板，可直接粘贴到 PS / PR / 微信。")
        hint2.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        v.addWidget(hint2)
        v.addStretch(1)
        self.tabs.addTab(tab, "截图")

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
        rotation = self.meta_rotation.currentData()
        self._begin("写入元数据", f"输出: {out}")
        self._run(lambda: write_metadata(src, out, tags, rotation),
                  lambda r: f"元数据已写入: {Path(r).name}")

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
