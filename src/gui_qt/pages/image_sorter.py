"""实用工具 · 图片筛选分类页（阶段四：视频/LIVP 播放 + 压缩 + 自动播放设置）。

整体规划见 image-sorter-migration-plan：
- 阶段一：骨架 + 源文件夹管理（拖放白名单）✅
- 阶段二：左栏缩略图卡片流（懒加载）+ 中栏预览 + 右栏详情
  + 后台扫描线程 + MD5 过滤/重复检测管线 + 视图模式三态切换 ✅
- 阶段三：文件夹面板（分类目标卡片）+ 分类/删除/收藏/快速分类
  + 复制/移动模式 + 撤销栈 + 复检模式 + 页面级快捷键分发 + 页内设置区 ✅
- 阶段四（本次）：QMediaPlayer 视频播放（Enter/中央播放键）+ LIVP/sidecar
  实况播放 + 自动播放设置开关；压缩标签页已独立为「实用工具·媒体压缩」页

与源项目 MainWindow 的差异：
- QMainWindow+qfluentwidgets → BasePage（工具箱页面体系），反馈改 app.toast
- 扫描由同步 processEvents 改为 run_bg 后台线程（每次扫描用独立 FileScanner 实例，
  on_done 校验 scan_id 防串台；进度经 post_to_main 回主线程）
- 快捷键：源项目重写 keyPressEvent → 本页 on_show 时向 QApplication 装 eventFilter、
  on_hide 时卸载（页面切换后按键不泄漏到其他页面）
- 视频播放失败不做 OpenCV 回退（工具箱不带 cv2 依赖）：
  健康检查 900ms 未起播即判失败，toast 提示用外部播放器打开
"""

import os
import re
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)

from gui_qt.pages.base import BasePage
from gui_qt.common import run_bg, post_to_main

from image_sorter import settings as cfg
from image_sorter.constants import (
    BUILTIN_FOLDERS,
    FileStatus,
    SUPPORTED_FORMATS,
    SUPPORTED_IMAGE_FORMATS,
    SUPPORTED_VIDEO_FORMATS,
    LAZY_INITIAL_BATCH_SIZE,
    LAZY_STEP_BATCH_SIZE,
    LAZY_UNLOAD_BATCH_SIZE,
    LAZY_THUMBNAIL_RESIDENT_LIMIT,
    LAZY_UNLOAD_VISIBLE_LOOKAROUND,
    LAZY_UNLOAD_FOCUS_RADIUS,
    PENDING_THUMB_MAX,
)
from image_sorter.lazy_load_mixin import LazyLoadMixin
from image_sorter.core.cache_manager import CacheManager
from image_sorter.core.duplicate_detector import DuplicateDetectionThread
from image_sorter.core.file_processor import FileProcessor
from image_sorter.core.file_scanner import FileScanner
from image_sorter.core.folder_manager import FolderManager
from image_sorter.core.media_hash_manager import MediaHashManager
from image_sorter.core.md5_filter_thread import Md5FilterThread
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter.core.operation_history import OperationHistory, OperationType
from image_sorter.core.shortcut_manager import ShortcutManager
from image_sorter.core.thumbnail_generator import ThumbnailGenerator, ThumbnailLoader
from image_sorter._log import logger

from gui_qt.pages.image_sorter_widgets import FileListPanel, MediaViewer, FileInfoPanel
from gui_qt.pages.image_sorter_folder_panel import FolderPanel
from gui_qt.pages.image_sorter_settings import ShortcutMappingDialog

VIEW_MODES = [
    ("date", "📅 日期分组"),
    ("default", "📋 默认列表"),
    ("random", "🎲 随机抽样"),
]

STAGE_TITLES = {"import": "导入", "thumbnail": "缩略图", "md5": "MD5", "duplicate": "去重"}


class ImageSorterPage(LazyLoadMixin, BasePage):
    """图片筛选分类页。"""

    def __init__(self, app):
        super().__init__(app)

        # ---------- 核心管理器 ----------
        self.folder_manager = FolderManager()
        self.file_scanner = FileScanner()
        self.cache_manager = CacheManager()

        # 恢复持久化的工作目录（分类目标根；无则 MD5 过滤自动跳过）
        saved_work_dir = cfg.get_str("work_dir", "")
        if saved_work_dir and Path(saved_work_dir).is_dir():
            try:
                self.folder_manager.set_work_dir(saved_work_dir)
            except Exception as e:
                logger.log_exception(e, "恢复工作目录失败")

        # ---------- 文件状态 ----------
        self.file_list: List[Path] = []
        self.current_index = 0
        self.selected_files: List[Path] = []
        self._source_folders: List[Path] = []
        self._view_mode = self._normalize_view_mode(cfg.get_str("view_mode", "date"))
        self._file_list_already_md5_filtered = False
        self._video_cover_cache = {}
        self._video_info_cache = {}
        self._review_source_snapshot: List[str] = []  # 复检前的源目录快照

        # ---------- 操作体系（阶段三） ----------
        self.operation_history = OperationHistory()
        self.shortcut_manager = ShortcutManager()
        self._file_operation_mode = self._normalize_file_operation_mode(
            cfg.get_str("file_operation_mode", "copy")
        )
        self._auto_rename_enabled = cfg.get_bool("auto_rename_enabled", False)
        self._auto_rename_pattern = cfg.get_str("auto_rename_pattern", "{source_name}")
        self._undo_thumbnail_loader: Optional[ThumbnailLoader] = None
        self._shortcut_mapping_dialog_open = False
        self._shortcuts_installed = False

        # ---------- 线程 ----------
        self.thumbnail_loader = None
        self.duplicate_thread: Optional[DuplicateDetectionThread] = None
        self.md5_filter_thread: Optional[Md5FilterThread] = None
        self._scan_job_running = False

        # ---------- 扫描会话 ----------
        self._active_scan_id = 0
        self._thumbnail_scan_id = 0
        self._auto_tasks_pending_scan_id = 0
        self._duplicate_scan_id = 0
        self._thumbnail_pipeline_completed_scan_ids: set = set()
        self._skip_post_thumbnail_tasks_scan_ids: set = set()
        self._random_prefilter_scan_ids: set = set()
        self._md5_done_before_thumbnail_scan_ids: set = set()
        self._md5_filter_enabled_for_active_scan = True
        self._mode_switch_base_files: List[Path] = []
        self._mode_switch_sorted_cache = {}

        # ---------- 懒加载参数 ----------
        self._lazy_initial_batch_size = LAZY_INITIAL_BATCH_SIZE
        self._lazy_step_batch_size = LAZY_STEP_BATCH_SIZE
        self._lazy_unload_batch_size = LAZY_UNLOAD_BATCH_SIZE
        self._lazy_thumbnail_resident_limit = LAZY_THUMBNAIL_RESIDENT_LIMIT
        self._lazy_unload_visible_lookaround = LAZY_UNLOAD_VISIBLE_LOOKAROUND
        self._lazy_unload_focus_radius = LAZY_UNLOAD_FOCUS_RADIUS
        self._lazy_bucket_files = {}
        self._lazy_bucket_offsets = {}
        self._lazy_bucket_order: List[str] = []
        self._lazy_loaded_keys: set = set()
        self._lazy_active_bucket_key: Optional[str] = None
        self._lazy_thumbnail_lru: List[Path] = []
        self._lazy_thumbnail_lru_keys: set = set()
        self._scroll_autoload_armed = False
        self._pending_thumb_paths: deque = deque()
        self._pending_thumb_keys: set = set()
        self._pending_thumb_max = PENDING_THUMB_MAX
        self._visible_prefetch_last_ts = 0.0
        self._auto_load_more_scheduled = False

        # ---------- 管线状态 ----------
        self._status_stage_order = ["import", "thumbnail", "md5", "duplicate"]
        self._status_stage_values = {key: "待开始" for key in self._status_stage_order}

        # ---------- 启动恢复 ----------
        self._startup_restore_done = False

        self._init_ui()
        self._connect_signals()

    # ==================== UI ====================

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # ---------- 顶栏：源文件夹管理 ----------
        top = QHBoxLayout()
        add_btn = QPushButton("📂 添加源文件夹")
        add_btn.clicked.connect(self._pick_source_folder)
        clear_btn = QPushButton("🧹 清空源")
        clear_btn.clicked.connect(self._clear_sources)
        self.view_combo = QComboBox()
        for key, text in VIEW_MODES:
            self.view_combo.addItem(text, key)
            if key == self._view_mode:
                self.view_combo.setCurrentIndex(self.view_combo.count() - 1)
        self.view_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        top.addWidget(add_btn)
        top.addWidget(clear_btn)
        top.addWidget(QLabel("视图:"))
        top.addWidget(self.view_combo)
        top.addStretch(1)
        self.sources_label = QLabel("尚未添加源文件夹")
        self.sources_label.setStyleSheet("color:#888;")
        top.addWidget(self.sources_label)
        layout.addLayout(top)

        # ---------- 顶栏二：分类工作流控制 ----------
        classify_bar = QHBoxLayout()
        self.work_dir_btn = QPushButton("📁 分类目录")
        self.work_dir_btn.clicked.connect(self._on_change_work_dir)
        classify_bar.addWidget(self.work_dir_btn)
        self.work_dir_label = QLabel("")
        self.work_dir_label.setStyleSheet("color:#6b7280;")
        classify_bar.addWidget(self.work_dir_label)
        classify_bar.addStretch(1)

        self.op_mode_combo = QComboBox()
        self.op_mode_combo.addItem("复制模式", "copy")
        self.op_mode_combo.addItem("移动模式", "move")
        if self._file_operation_mode == "move":
            self.op_mode_combo.setCurrentIndex(1)
        self.op_mode_combo.currentIndexChanged.connect(self._on_op_mode_changed)
        classify_bar.addWidget(QLabel("分类方式:"))
        classify_bar.addWidget(self.op_mode_combo)

        keep_btn = QPushButton("⬇️ 保留(S)")
        keep_btn.clicked.connect(self._on_keep_file)
        classify_bar.addWidget(keep_btn)
        fav_btn = QPushButton("⭐ 收藏(F)")
        fav_btn.clicked.connect(self._on_favorite_file)
        classify_bar.addWidget(fav_btn)
        del_btn = QPushButton("🗑️ 删除(W)")
        del_btn.clicked.connect(self._on_delete_file)
        classify_bar.addWidget(del_btn)
        undo_btn = QPushButton("↩️ 撤销(Ctrl+Z)")
        undo_btn.clicked.connect(self._on_undo)
        classify_bar.addWidget(undo_btn)

        self.settings_btn = QPushButton("⚙️ 设置")
        self.settings_btn.clicked.connect(self._toggle_settings_area)
        classify_bar.addWidget(self.settings_btn)
        layout.addLayout(classify_bar)

        # ---------- 页内设置区（默认收起） ----------
        self._init_settings_area()

        # ---------- 管线状态栏 ----------
        self.pipeline_label = QLabel("")
        self.pipeline_label.setStyleSheet("color:#6b7280; padding: 1px 2px;")
        layout.addWidget(self.pipeline_label)

        # ---------- 三栏（中栏 = 预览 + 文件夹面板 垂直分割） ----------
        splitter = QSplitter(Qt.Horizontal)

        self.file_list_panel = FileListPanel(self)
        self.file_list_panel.set_grouping_mode(self._resolve_grouping_mode_for_view(self._view_mode))
        self.file_list_panel.set_grouping_datetime_resolver(self._get_grouping_datetime_fast)
        splitter.addWidget(self.file_list_panel)

        middle_splitter = QSplitter(Qt.Vertical)
        self.media_viewer = MediaViewer(self)
        middle_splitter.addWidget(self.media_viewer)

        self.folder_panel = FolderPanel(self)
        self.folder_panel.set_folder_manager(self.folder_manager)
        middle_splitter.addWidget(self.folder_panel)
        middle_splitter.setStretchFactor(0, 3)
        middle_splitter.setStretchFactor(1, 2)
        middle_splitter.setSizes([420, 230])
        splitter.addWidget(middle_splitter)

        self.file_info_panel = FileInfoPanel(self)
        self.file_info_panel.set_auto_rename_config(
            self._auto_rename_enabled, self._auto_rename_pattern
        )
        self.file_info_panel.set_pattern_renderer(self._render_pattern_for_panel)
        splitter.addWidget(self.file_info_panel)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([330, 440, 330])
        layout.addWidget(splitter, 1)

        self._render_pipeline_status()
        self._reload_sources_label()
        self._reload_work_dir_label()

    # ==================== 页内设置区 ====================

    def _init_settings_area(self):
        self.settings_area = QWidget()
        self.settings_area.setStyleSheet(
            "QWidget { background-color: #f4f5f7; border: 1px solid #dfe4ec;"
            " border-radius: 6px; }"
        )
        area_layout = QHBoxLayout(self.settings_area)
        area_layout.setContentsMargins(8, 6, 8, 6)
        area_layout.setSpacing(10)

        self.filter_screened_cb = QCheckBox("过滤已筛选文件(MD5)")
        self.filter_screened_cb.setChecked(cfg.get_bool("filter_screened_files", True))
        self.filter_screened_cb.stateChanged.connect(self._on_filter_screened_toggled)
        area_layout.addWidget(self.filter_screened_cb)

        area_layout.addWidget(QLabel("重复保留:"))
        self.duplicate_keep_combo = QComboBox()
        self.duplicate_keep_combo.addItem("保留最新", "latest")
        self.duplicate_keep_combo.addItem("手动处理", "manual")
        saved_keep = cfg.get_str("duplicate_keep", "latest")
        self.duplicate_keep_combo.setCurrentIndex(1 if saved_keep == "manual" else 0)
        self.duplicate_keep_combo.currentIndexChanged.connect(self._on_duplicate_keep_changed)
        area_layout.addWidget(self.duplicate_keep_combo)

        area_layout.addWidget(QLabel("随机抽样数:"))
        self.random_size_spin = QSpinBox()
        self.random_size_spin.setRange(5, 500)
        self.random_size_spin.setValue(cfg.get_int("random_sample_size", 50))
        self.random_size_spin.valueChanged.connect(self._on_random_size_changed)
        area_layout.addWidget(self.random_size_spin)

        self.autoplay_video_cb = QCheckBox("自动播放视频")
        self.autoplay_video_cb.setChecked(cfg.get_bool("autoplay_video", False))
        self.autoplay_video_cb.stateChanged.connect(self._on_autoplay_video_toggled)
        area_layout.addWidget(self.autoplay_video_cb)

        self.autoplay_live_cb = QCheckBox("自动播放实况(LIVP)")
        self.autoplay_live_cb.setChecked(cfg.get_bool("autoplay_live_photo", False))
        self.autoplay_live_cb.stateChanged.connect(self._on_autoplay_live_toggled)
        area_layout.addWidget(self.autoplay_live_cb)

        shortcut_btn = QPushButton("⌨️ 快捷键映射")
        shortcut_btn.clicked.connect(self._on_shortcut_mapping)
        area_layout.addWidget(shortcut_btn)

        clear_cache_btn = QPushButton("🧹 清理缓存")
        clear_cache_btn.clicked.connect(self._on_clear_cache)
        area_layout.addWidget(clear_cache_btn)

        open_cache_btn = QPushButton("📂 打开缓存目录")
        open_cache_btn.clicked.connect(self._on_open_cache_folder)
        area_layout.addWidget(open_cache_btn)

        area_layout.addStretch(1)
        self.settings_area.hide()
        self.layout().addWidget(self.settings_area)

    def _toggle_settings_area(self):
        self.settings_area.setVisible(not self.settings_area.isVisible())

    def _on_filter_screened_toggled(self, state: int):
        enabled = self.filter_screened_cb.isChecked()
        cfg.set_setting("filter_screened_files", enabled)
        if not enabled:
            self._set_pipeline_stage_status("md5", "过滤已关闭")

    def _on_duplicate_keep_changed(self, index: int):
        cfg.set_setting("duplicate_keep", self.duplicate_keep_combo.itemData(index))

    def _on_random_size_changed(self, value: int):
        cfg.set_setting("random_sample_size", int(value))

    def _on_autoplay_video_toggled(self, state: int):
        cfg.set_setting("autoplay_video", self.autoplay_video_cb.isChecked())

    def _on_autoplay_live_toggled(self, state: int):
        cfg.set_setting("autoplay_live_photo", self.autoplay_live_cb.isChecked())

    def _on_shortcut_mapping(self):
        self._shortcut_mapping_dialog_open = True
        try:
            dlg = ShortcutMappingDialog(self.shortcut_manager, self)
            dlg.exec()
        finally:
            self._shortcut_mapping_dialog_open = False

    def _on_clear_cache(self):
        options = self._show_cache_clear_options_dialog()
        if options is None:
            return
        if not any(options.values()):
            self.app.toast("未勾选任何缓存项", "warning")
            return

        self._retire_thumbnail_loader(timeout_ms=2500)
        if self.md5_filter_thread and self.md5_filter_thread.isRunning():
            self._wait_thread_responsive(self.md5_filter_thread, timeout_ms=2500)

        cleared_items: List[str] = []
        try:
            if options.get("scan"):
                self.cache_manager.clear_scan_cache()
                cleared_items.append("导入扫描缓存")
            if options.get("thumbnail"):
                self.cache_manager.clear_thumbnail_cache()
                cleared_items.append("缩略图缓存")
            if options.get("md5"):
                self.cache_manager.clear_md5_cache()
                cleared_items.append("MD5缓存")
            if options.get("classified_md5"):
                self.cache_manager.clear_classified_md5_cache()
                cleared_items.append("已分类MD5记录")
            if options.get("memory"):
                self._video_cover_cache.clear()
                self._video_info_cache.clear()
                cleared_items.append("内存预览缓存")
            elif options.get("thumbnail"):
                self._video_cover_cache.clear()
                self._video_info_cache.clear()
                cleared_items.append("内存预览缓存(联动)")

            self.app.toast(f"已清理: {'、'.join(cleared_items)}", "success")
            logger.log_operation("清除缓存", f"{self.cache_manager.base_dir} -> {cleared_items}")
        except Exception as e:
            logger.log_exception(e, "清除缓存失败")
            self.app.toast(f"清除缓存失败: {e}", "error")

    def _show_cache_clear_options_dialog(self) -> Optional[Dict[str, bool]]:
        dialog = QDialog(self)
        dialog.setWindowTitle("选择清理项")
        dialog.setModal(True)
        dialog.resize(460, 320)

        layout = QVBoxLayout(dialog)
        tip = QLabel("请选择要清理的缓存项（仅清理勾选内容）：", dialog)
        tip.setWordWrap(True)
        layout.addWidget(tip)

        scan_cb = QCheckBox("导入扫描缓存（文件列表缓存）", dialog)
        scan_cb.setChecked(True)
        layout.addWidget(scan_cb)

        thumbnail_cb = QCheckBox("缩略图缓存（磁盘）", dialog)
        thumbnail_cb.setChecked(True)
        layout.addWidget(thumbnail_cb)

        md5_cb = QCheckBox("MD5缓存（媒体哈希计算结果）", dialog)
        md5_cb.setChecked(True)
        layout.addWidget(md5_cb)

        classified_md5_cb = QCheckBox("已分类MD5记录（影响「过滤已筛选文件」）", dialog)
        classified_md5_cb.setChecked(False)
        layout.addWidget(classified_md5_cb)

        memory_cb = QCheckBox("内存预览缓存（当前会话视频封面/信息）", dialog)
        memory_cb.setChecked(True)
        layout.addWidget(memory_cb)

        caution = QLabel(
            "提示：清理「已分类MD5记录」后，历史已分类文件可能重新出现在待筛选列表中。",
            dialog,
        )
        caution.setWordWrap(True)
        caution.setStyleSheet("color: #d32f2f; font-size: 12px;")
        layout.addWidget(caution)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return {
            "scan": scan_cb.isChecked(),
            "thumbnail": thumbnail_cb.isChecked(),
            "md5": md5_cb.isChecked(),
            "classified_md5": classified_md5_cb.isChecked(),
            "memory": memory_cb.isChecked(),
        }

    def _on_open_cache_folder(self):
        cache_path = self.cache_manager.base_dir
        cache_path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(cache_path)))
        self.app.toast(f"缓存目录: {cache_path}", "info")

    def _connect_signals(self):
        self.file_list_panel.folders_dropped.connect(self._on_folders_dropped)
        self.file_list_panel.file_selected.connect(self._on_file_selected)
        self.file_list_panel.files_batch_selected.connect(self._on_files_batch_selected)
        self.file_list_panel.folder_header_clicked.connect(self._on_file_list_folder_header_clicked)
        bar = self.file_list_panel.file_list.verticalScrollBar()
        bar.valueChanged.connect(self._on_file_list_scroll_changed)
        bar.sliderPressed.connect(self._arm_scroll_autoload)
        self.file_info_panel.copy_info_requested.connect(
            lambda msg: self.app.toast(msg, "success")
        )
        self.file_info_panel.notify_requested.connect(
            lambda msg, level: self.app.toast(msg, level if level else "success")
        )
        self.file_info_panel.rename_requested.connect(self._on_rename_requested)
        self.file_info_panel.auto_rename_config_changed.connect(
            self._on_auto_rename_config_changed
        )
        # 文件夹面板（阶段三）
        self.folder_panel.folder_selected.connect(self._on_folder_selected)
        self.folder_panel.folder_created.connect(self._on_folder_created)
        self.folder_panel.folder_renamed.connect(self._on_folder_renamed)
        self.folder_panel.folder_deleted.connect(self._on_folder_deleted)
        self.folder_panel.folder_preview_requested.connect(self._on_folder_preview_requested)
        self.folder_panel.restore_source_requested.connect(self._on_restore_source_requested)
        self.folder_panel.folder_clear_requested.connect(self._on_folder_clear_requested)
        self.folder_panel.file_dropped_to_folder.connect(self._on_file_dropped_to_folder)

        # 媒体查看器（阶段四：播放失败提示 + LIVP 中央播放键）
        self.media_viewer.playback_failed.connect(self._on_playback_failed)
        self.media_viewer.livp_play_requested.connect(self._play_livp_video)

    # ==================== 源文件夹管理 ====================

    def _pick_source_folder(self):
        picked = QFileDialog.getExistingDirectory(
            self, "选择要筛选的源文件夹（可添加多个）",
            str(self._source_folders[0]) if self._source_folders else "",
        )
        if picked:
            self._add_source_folders([picked])

    def _add_source_folders(self, folders: list):
        merged = self._normalize_folder_paths([*self._source_folders_as_str(), *folders])
        old = set(self._source_folders_as_str())
        added = [f for f in merged if f not in old]
        if not added:
            return  # 无新增（重复拖入）不触发重扫
        cfg.set_list("source_folders", merged)
        self._reload_sources_label()
        self.app.toast(f"✅ 已添加 {len(added)} 个源文件夹", "success")
        self._on_folders_dropped(merged, persist_default=False)

    def _clear_sources(self):
        if not self._source_folders:
            return
        ret = QMessageBox.question(
            self, "清空源文件夹",
            f"确定移除全部 {len(self._source_folders)} 个源文件夹吗？\n（仅移除列表记录，不删除任何文件）",
        )
        if ret != QMessageBox.Yes:
            return
        cfg.set_list("source_folders", [])
        self._source_folders = []
        self._mode_switch_base_files = []
        self._mode_switch_sorted_cache.clear()
        self._reset_file_list_panel_state()
        self._reset_lazy_loading_state()
        self.media_viewer.clear()
        self.file_info_panel.update_current_file_path(None)
        self._set_pipeline_idle_status("就绪")
        self._reload_sources_label()
        self.app.toast("已清空源文件夹列表", "info")

    def _source_folders_as_str(self) -> List[str]:
        return [str(p) for p in self._source_folders]

    def _reload_sources_label(self):
        if not self._source_folders:
            self.sources_label.setText("尚未添加源文件夹（可拖入文件夹）")
            return
        if len(self._source_folders) == 1:
            self.sources_label.setText(f"源文件夹: {self._source_folders[0]}")
        else:
            self.sources_label.setText(f"源文件夹: {len(self._source_folders)} 个")

    # ==================== 视图模式 ====================

    @staticmethod
    def _normalize_view_mode(mode) -> str:
        normalized = str(mode or "default").strip().lower()
        return normalized if normalized in ("default", "date", "random") else "default"

    def _on_view_mode_changed(self, index: int):
        key = self.view_combo.itemData(index)
        self._set_view_mode(key)

    def _set_view_mode(self, mode: str):
        normalized = self._normalize_view_mode(mode)
        if normalized == self._view_mode:
            return
        self._view_mode = normalized
        cfg.set_setting("view_mode", normalized)

        if self._source_folders:
            if self._switch_view_mode_without_rescan(normalized):
                return
            self._clear_loaded_content_before_mode_switch()
            self._on_folders_dropped(self._source_folders_as_str(), persist_default=False)

    # ==================== 管线状态 ====================

    def _set_pipeline_stage_status(self, stage: str, text: str, percent: Optional[float] = None):
        if stage not in self._status_stage_values:
            return
        if percent is None:
            self._status_stage_values[stage] = text
        else:
            normalized = max(0.0, min(100.0, float(percent)))
            self._status_stage_values[stage] = f"{text}-{normalized:.1f}%"
        self._render_pipeline_status()

    def _render_pipeline_status(self):
        parts = [
            f"{STAGE_TITLES[stage]}:{self._status_stage_values[stage]}"
            for stage in self._status_stage_order
        ]
        summary = " | ".join(parts)
        self.pipeline_label.setText(summary)
        self.pipeline_label.setToolTip("\n".join(parts))

    def _reset_pipeline_status(self):
        for stage in self._status_stage_order:
            self._status_stage_values[stage] = "待开始"
        self._render_pipeline_status()

    def _set_pipeline_idle_status(self, import_text: str = "就绪"):
        self._set_pipeline_stage_status("import", import_text)
        self._set_pipeline_stage_status("thumbnail", "就绪")
        if cfg.get_bool("filter_screened_files", True):
            self._set_pipeline_stage_status("md5", "就绪")
        else:
            self._set_pipeline_stage_status("md5", "过滤已关闭")
        self._set_pipeline_stage_status("duplicate", "就绪")

    # ==================== 扫描（后台线程） ====================

    def _on_folders_dropped(
        self,
        folder_paths: List[str],
        persist_default: bool = True,
        enable_md5_filter: bool = True,
    ):
        """导入源文件夹：后台扫描 → 主线程接管懒加载流水线。

        enable_md5_filter=False 时跳过 MD5 过滤（复检模式：看的就是已分类文件）。
        """
        self._scroll_autoload_armed = False
        self._skip_post_thumbnail_tasks_scan_ids.clear()
        self.file_list_panel.show_loading(True)
        self._retire_thumbnail_loader(timeout_ms=2500)
        if self.duplicate_thread and self.duplicate_thread.isRunning():
            self._wait_thread_responsive(self.duplicate_thread, timeout_ms=600)
        if self.md5_filter_thread and self.md5_filter_thread.isRunning():
            self._wait_thread_responsive(self.md5_filter_thread, timeout_ms=600)

        self._reset_pipeline_status()
        self._set_pipeline_stage_status("import", "文件导入中", 0.0)
        self._set_pipeline_stage_status("thumbnail", "等待导入完成")
        filter_enabled = enable_md5_filter and cfg.get_bool("filter_screened_files", True)
        if not enable_md5_filter:
            self._set_pipeline_stage_status("md5", "复检模式已跳过")
        elif filter_enabled:
            self._set_pipeline_stage_status("md5", "等待缩略图完成")
        else:
            self._set_pipeline_stage_status("md5", "过滤已关闭")
        self._set_pipeline_stage_status(
            "duplicate", "等待MD5完成" if filter_enabled else "等待缩略图完成"
        )

        self._active_scan_id += 1
        scan_id = self._active_scan_id
        self._md5_filter_enabled_for_active_scan = filter_enabled
        self._file_list_already_md5_filtered = bool(not filter_enabled)
        self._random_prefilter_scan_ids.clear()
        self._md5_done_before_thumbnail_scan_ids.clear()

        normalized_folders = self._normalize_folder_paths(folder_paths)
        if not normalized_folders:
            self._set_pipeline_stage_status("import", "导入失败")
            self._set_pipeline_stage_status("thumbnail", "已跳过")
            self._set_pipeline_stage_status("md5", "已跳过")
            self.app.toast("未找到可用文件夹，请拖入存在且可访问的目录", "warning")
            self.file_list_panel.show_loading(False)
            return

        self._source_folders = [Path(p) for p in normalized_folders]
        if persist_default:
            cfg.set_list("source_folders", normalized_folders)
        self._reload_sources_label()

        self.file_list_panel.set_grouping_mode(
            self._resolve_grouping_mode_for_view(self._view_mode)
        )
        self.file_list_panel.set_source_folders(normalized_folders)
        self._video_cover_cache.clear()
        self._video_info_cache.clear()

        # 独立 FileScanner 实例跑后台线程，避免并发扫描串台
        scanner = FileScanner()
        sort_order = cfg.get_str("sort_order", "path")
        if self._view_mode == "random":
            sort_order = "path"

        def _scan_progress(percent: float, processed: int, total: int):
            if scan_id != self._active_scan_id:
                return
            post_to_main(self._on_scan_progress, percent, processed, total)

        def _scan_job():
            return scanner.scan_folders(
                normalized_folders,
                sort_order,
                progress_callback=_scan_progress,
                cache_manager=self.cache_manager,
                use_cache=True,
                verify_integrity=False,
                pre_count=False,
            )

        self._scan_job_running = True
        run_bg(_scan_job, lambda result, err: self._on_scan_done(scan_id, scanner, result, err))

    def _on_scan_progress(self, percent: float, processed: int, total: int):
        if total > 0:
            self._set_pipeline_stage_status("import", f"文件导入中({processed}/{total})", percent)
        else:
            self._set_pipeline_stage_status("import", f"文件导入中({processed})")

    def _on_scan_done(self, scan_id: int, scanner: FileScanner, result, err):
        self._scan_job_running = False
        if scan_id != self._active_scan_id:
            return  # 已被新扫描会话取代，丢弃
        if err is not None:
            self._set_pipeline_stage_status("import", "导入失败")
            logger.log_exception(Exception(err), "文件扫描失败")
            self.app.toast(f"文件扫描失败: {err}", "error")
            self.file_list_panel.show_loading(False)
            return

        self.file_scanner = scanner
        scanned_files = list(result or [])

        try:
            # 日期模式：优先 SQLite 分组时间缓存，未命中退化为文件时间
            if self._view_mode == "date":
                scanned_files.sort(
                    key=lambda p: (-self._get_grouping_timestamp_fast(p), str(p).lower())
                )

            full_scanned_files = list(scanned_files)
            self._set_mode_switch_base_files(full_scanned_files)

            if self._view_mode == "random":
                scanned_files = self._sample_random_files(full_scanned_files)

            self._set_pipeline_stage_status("import", "文件导入完成", 100.0)
            self.file_list = list(scanned_files)

            if scanner.cache_hit:
                self.app.toast(f"已从缓存快速恢复 {len(self.file_list)} 个文件", "info")
            if scanner.filtered_count > 0:
                self.app.toast(f"已过滤 {scanner.filtered_count} 个非图片/视频文件", "info")

            if not self.file_list:
                self._set_pipeline_stage_status("thumbnail", "已跳过")
                self._set_pipeline_stage_status("md5", "已跳过")
                self._set_pipeline_stage_status("duplicate", "已跳过")
                self.app.toast("未找到可用媒体文件，请检查文件夹内容", "warning")
                self.file_list_panel.show_loading(False)
                return

            self._auto_tasks_pending_scan_id = scan_id
            self._thumbnail_pipeline_completed_scan_ids.discard(scan_id)

            self._reset_file_list_panel_state()
            self._prepare_lazy_loading()
            self._load_initial_lazy_files()

            QTimer.singleShot(
                150,
                lambda: self._display_file(0, force_scroll=True, prefetch_focus=False),
            )
        except Exception as e:
            self._set_pipeline_stage_status("import", "导入失败")
            logger.log_exception(e, "扫描结果处理失败")
            self.app.toast(f"文件处理失败: {e}", "error")
            self.file_list_panel.show_loading(False)

    # ==================== MD5 过滤（已分类排除） ====================

    def _start_md5_filter(self, scan_id: int, trigger: str = "post_thumbnail"):
        if not cfg.get_bool("filter_screened_files", True):
            self._set_pipeline_stage_status("md5", "过滤已关闭", 100.0)
            self._start_auto_tasks_for_scan(scan_id)
            return
        if not self.file_list:
            self._set_pipeline_stage_status("md5", "已跳过")
            self._start_auto_tasks_for_scan(scan_id)
            return
        if not self.folder_manager._work_dir:
            self._set_pipeline_stage_status("md5", "已跳过")
            self._start_auto_tasks_for_scan(scan_id)
            return
        if self.md5_filter_thread and self.md5_filter_thread.isRunning():
            self.md5_filter_thread.wait()

        self._set_pipeline_stage_status("md5", "MD5解析中", 0.0)
        self._file_list_already_md5_filtered = False
        self.app.toast("正在后台计算MD5排除已分类文件...", "info")

        self.md5_filter_thread = Md5FilterThread(
            scan_id=scan_id,
            scanned_files=list(self.file_list),
            work_dir=self.folder_manager.work_dir,
            source_folders=list(self._source_folders),
            classified_folders=self._build_md5_classified_folders(),
            cache_manager=self.cache_manager,
            sqlite_store=self.cache_manager.sqlite_store,
        )
        self.md5_filter_thread.filtering_progress.connect(self._on_md5_filter_progress)
        self.md5_filter_thread.filtering_complete.connect(self._on_md5_filter_complete)
        self.md5_filter_thread.filtering_failed.connect(self._on_md5_filter_failed)
        self.md5_filter_thread.start()

    def _on_md5_filter_progress(self, scan_id: int, percent: float, stage_text: str):
        if scan_id != self._active_scan_id:
            return
        self._set_pipeline_stage_status("md5", stage_text, percent)

    def _on_md5_filter_complete(self, scan_id: int, filtered_files: list, excluded_count: int):
        if scan_id != self._active_scan_id:
            return
        self._set_pipeline_stage_status("md5", "MD5解析完成", 100.0)
        self._file_list_already_md5_filtered = True

        new_file_list = [Path(p) for p in filtered_files]
        self._set_mode_switch_base_files(new_file_list)

        if scan_id in self._random_prefilter_scan_ids:
            self._random_prefilter_scan_ids.discard(scan_id)
            self.file_list = self._sample_random_files(new_file_list)

            if excluded_count > 0:
                self.app.toast(f"基于MD5自动排除 {excluded_count} 个已分类媒体文件", "info")

            if not self.file_list:
                self._set_pipeline_stage_status("thumbnail", "已跳过")
                self._set_pipeline_stage_status("duplicate", "已跳过")
                self.file_list_panel.show_loading(False)
                self.app.toast("本次文件均已分类或抽样后无可用文件", "info")
                self._start_auto_tasks_for_scan(scan_id)
                return

            self._reset_file_list_panel_state()
            self._prepare_lazy_loading()
            self._md5_done_before_thumbnail_scan_ids.add(scan_id)
            self._thumbnail_pipeline_completed_scan_ids.discard(scan_id)
            self._load_initial_lazy_files()
            QTimer.singleShot(
                150,
                lambda: self._display_file(0, force_scroll=True, prefetch_focus=False),
            )
            return

        old_file_list = list(self.file_list)
        new_file_set = set(new_file_list)
        removed_files = [p for p in old_file_list if p not in new_file_set]

        if removed_files:
            current_file = None
            if 0 <= self.current_index < len(old_file_list):
                current_file = old_file_list[self.current_index]

            self.file_list = new_file_list
            self.file_list_panel.remove_files(removed_files)
            self.selected_files = [f for f in self.selected_files if f in new_file_set]

            if not self.file_list:
                self.current_index = 0
                self.media_viewer.clear()
                self.file_info_panel.update_current_file_path(None)
                self.app.toast(f"本次文件均已分类（MD5排除 {excluded_count} 个）", "info")
                self._start_auto_tasks_for_scan(scan_id)
                return

            if current_file and current_file in new_file_set:
                self.current_index = self.file_list.index(current_file)
            else:
                self.current_index = min(self.current_index, len(self.file_list) - 1)

            self._display_file(self.current_index)

        if excluded_count > 0:
            self.app.toast(f"基于MD5自动排除 {excluded_count} 个已分类媒体文件", "info")

        self._start_auto_tasks_for_scan(scan_id)

    def _on_md5_filter_failed(self, scan_id: int, error_message: str):
        if scan_id != self._active_scan_id:
            return
        self._file_list_already_md5_filtered = False
        if scan_id in self._random_prefilter_scan_ids:
            self._random_prefilter_scan_ids.discard(scan_id)
            self.file_list = self._sample_random_files(list(self.file_list))
            if not self.file_list:
                self._set_pipeline_stage_status("thumbnail", "已跳过")
                self._set_pipeline_stage_status("md5", "MD5解析失败")
                self._set_pipeline_stage_status("duplicate", "已跳过")
                self.file_list_panel.show_loading(False)
                self.app.toast("MD5过滤失败且无可用文件", "warning")
                self._start_auto_tasks_for_scan(scan_id)
                return

            self._reset_file_list_panel_state()
            self._prepare_lazy_loading()
            self._md5_done_before_thumbnail_scan_ids.add(scan_id)
            self._thumbnail_pipeline_completed_scan_ids.discard(scan_id)
            self._load_initial_lazy_files()
            self.app.toast("MD5过滤失败，已回退为直接随机抽样", "warning")
            logger.error(f"后台MD5过滤失败[{scan_id}]: {error_message}")
            return

        self._set_pipeline_stage_status("md5", "MD5解析失败")
        logger.error(f"后台MD5过滤失败[{scan_id}]: {error_message}")
        self._start_auto_tasks_for_scan(scan_id)

    # ==================== 重复检测 ====================

    def _start_auto_tasks_for_scan(self, scan_id: int):
        if self._auto_tasks_pending_scan_id != scan_id:
            return
        self._auto_tasks_pending_scan_id = 0
        if self.file_list:
            self._detect_duplicates()
        else:
            self._set_pipeline_stage_status("duplicate", "已跳过")

    def _detect_duplicates(self):
        if not self.file_list:
            self._set_pipeline_stage_status("duplicate", "已跳过")
            return
        if self.duplicate_thread and self.duplicate_thread.isRunning():
            self.duplicate_thread.wait()

        self._set_pipeline_stage_status("duplicate", "重复检测中", 0.0)
        self._duplicate_scan_id = self._active_scan_id
        self.duplicate_thread = DuplicateDetectionThread(
            self.file_list,
            cfg.get_str("duplicate_keep", "latest"),
            sqlite_store=self.cache_manager.sqlite_store,
        )
        self.duplicate_thread.detection_progress.connect(self._on_duplicate_progress)
        self.duplicate_thread.detection_failed.connect(self._on_duplicates_failed)
        self.duplicate_thread.detection_complete.connect(self._on_duplicates_detected)
        self.duplicate_thread.start()

    def _on_duplicate_progress(self, current: int, total: int):
        if self._duplicate_scan_id != self._active_scan_id:
            return
        if total <= 0:
            self._set_pipeline_stage_status("duplicate", "重复检测完成", 100.0)
            return
        percent = current * 100.0 / total
        self._set_pipeline_stage_status("duplicate", f"重复检测中({current}/{total})", percent)

    def _on_duplicates_failed(self, error_message: str):
        if self._duplicate_scan_id != self._active_scan_id:
            return
        self._set_pipeline_stage_status("duplicate", "重复检测失败")
        logger.error(f"重复检测失败: {error_message}")

    def _on_duplicates_detected(self, duplicate_groups: list):
        if self._duplicate_scan_id != self._active_scan_id:
            return
        self._set_pipeline_stage_status("duplicate", "重复检测完成", 100.0)
        if duplicate_groups:
            total_duplicates = sum(len(group) for group in duplicate_groups)
            self.app.toast(
                f"检测到 {len(duplicate_groups)} 组重复文件，共 {total_duplicates} 个", "info"
            )
            for group in duplicate_groups:
                for file_path in group:
                    self.file_list_panel.update_file_status(file_path, FileStatus.DUPLICATE)

    # ==================== 文件显示 ====================

    def _display_file(self, index: int, force_scroll: bool = False, prefetch_focus: bool = True):
        if not self.file_list or index < 0 or index >= len(self.file_list):
            return

        file_path = self.file_list[index]
        self.current_index = index
        requested_thumb = self._ensure_file_loaded_for_display(file_path)
        if prefetch_focus and requested_thumb:
            self._prefetch_focus_window_thumbnails(index, radius=30)
        self._prefetch_visible_missing_thumbnails(force=(force_scroll or requested_thumb))

        self.file_list_panel.expand_related_folder(file_path, force_scroll=force_scroll)
        self.file_list_panel.select_file_item(file_path)

        self.media_viewer.show_loading(True)
        try:
            suffix = file_path.suffix.lower()

            if suffix in SUPPORTED_IMAGE_FORMATS:
                if suffix == ".livp":
                    live_cover = MediaInfoExtractor.extract_live_photo_cover(file_path)
                    if live_cover is not None:
                        self.media_viewer.display_pil_image(
                            live_cover, file_path, show_play_button=True
                        )
                        if cfg.get_bool("autoplay_live_photo", False):
                            self._play_livp_video(file_path)
                    else:
                        self.media_viewer.display_image(file_path)
                else:
                    self.media_viewer.display_image(file_path)
                    if cfg.get_bool("autoplay_live_photo", False):
                        self._play_sidecar_live_photo_video(file_path)

                info = MediaInfoExtractor.extract_image_info(file_path)
                self.file_info_panel.display_image_info(file_path, info)

            elif suffix in SUPPORTED_VIDEO_FORMATS:
                cover_pixmap = self._video_cover_cache.get(file_path)
                if cover_pixmap is None:
                    first_frame = MediaInfoExtractor.extract_video_first_frame(file_path)
                    if first_frame:
                        cover_pixmap = ThumbnailGenerator._pil_to_qpixmap(first_frame)
                        self._video_cover_cache[file_path] = cover_pixmap

                if cover_pixmap:
                    self.media_viewer.display_video_cover(file_path, cover_pixmap)
                else:
                    # 若无法提取封面，仍显示视频信息并允许回车播放
                    self.media_viewer.clear()

                info = self._video_info_cache.get(file_path)
                if info is None:
                    info = MediaInfoExtractor.extract_video_info(file_path)
                    self._video_info_cache[file_path] = info
                self.file_info_panel.display_video_info(file_path, info)

                if cfg.get_bool("autoplay_video", False):
                    self.media_viewer.play_video(file_path)

            self.media_viewer.update_progress(index + 1, len(self.file_list))
            logger.log_operation("显示文件", file_path.name)

        except Exception as e:
            logger.log_exception(e, f"显示文件失败: {file_path}")
            self.app.toast(f"文件预览失败，已跳过异常文件: {file_path.name}", "warning")
            self.media_viewer.clear()
            self.file_info_panel.update_current_file_path(file_path)
        finally:
            self.media_viewer.show_loading(False)

    def _on_file_selected(self, file_path: Path):
        try:
            src_folder = self._resolve_source_folder_for_file(file_path)
            if src_folder is not None:
                self._lazy_active_bucket_key = self._path_key(src_folder)
            index = self.file_list.index(file_path)
            self._display_file(index)
        except ValueError:
            target_key = self._path_key(file_path)
            for idx, candidate in enumerate(self.file_list):
                if self._path_key(candidate) == target_key:
                    self._display_file(idx)
                    return
            self._ensure_file_loaded_for_display(file_path)
            logger.log_operation("选择项未命中", file_path.name)

    def _on_files_batch_selected(self, files: List[Path]):
        self.selected_files = files

    # ==================== 快捷键分发（阶段三） ====================

    def _install_shortcut_filter(self):
        if not self._shortcuts_installed:
            QApplication.instance().installEventFilter(self)
            self._shortcuts_installed = True

    def _remove_shortcut_filter(self):
        if self._shortcuts_installed:
            QApplication.instance().removeEventFilter(self)
            self._shortcuts_installed = False

    def eventFilter(self, obj, event):
        """页面级快捷键：本页可见期接管按键（on_hide 卸载，防止泄漏到其他页面）。"""
        if event.type() == QEvent.KeyPress:
            if not self.isVisible():
                return False
            # 仅接管发往本页控件树（主窗口）的按键；弹窗/独立对话框/QWindow 不拦截
            if not isinstance(obj, QWidget) or not (self.isAncestorOf(obj) or obj is self):
                return False
            if self._handle_global_shortcuts(event):
                return True
        return False

    def _handle_global_shortcuts(self, event) -> bool:
        """处理页面快捷键，返回是否已消费。"""
        if self._shortcut_mapping_dialog_open:
            return False

        focus_widget = QApplication.focusWidget()
        is_text_input = isinstance(focus_widget, (QLineEdit,))
        focus_in_folder_panel = bool(
            focus_widget is not None
            and (
                focus_widget is self.folder_panel
                or self.folder_panel.isAncestorOf(focus_widget)
            )
        )

        # 撤回：输入框内先让输入法处理（撤销输入）
        if self.shortcut_manager.matches(event, "undo"):
            if is_text_input:
                return False
            self._on_undo()
            return True

        # 输入框聚焦时禁用所有单键快捷键
        if is_text_input:
            return False

        # 小键盘数字 1-9：进入对应的子文件夹（多级分类）
        for folder_index in range(9):
            action = f"enter_subfolder_{folder_index + 1}"
            if self.shortcut_manager.matches(event, action):
                self.folder_panel.enter_folder_by_index(folder_index)
                return True

        # 小键盘数字 0：返回上一级文件夹
        if self.shortcut_manager.matches(event, "enter_parent_folder_keypad"):
            return self._on_navigate_parent_folder()

        # 主键盘数字 1-9：快速分类到对应文件夹
        for folder_index in range(9):
            action = f"quick_classify_{folder_index + 1}"
            if self.shortcut_manager.matches(event, action):
                if focus_in_folder_panel:
                    if self.folder_panel.select_folder_by_index(folder_index):
                        return True
                self._move_to_folder_by_index(folder_index)
                return True

        # 主键盘数字 0：快速移入「删除」文件夹
        if self.shortcut_manager.matches(event, "quick_delete_0"):
            self._on_delete_file()
            return True

        if self.shortcut_manager.matches(event, "go_parent_folder"):
            return self._on_navigate_parent_folder()

        if self.shortcut_manager.matches(event, "prev_file"):
            self._on_prev_file()
            return True
        if self.shortcut_manager.matches(event, "next_file"):
            self._on_next_file()
            return True

        if self.shortcut_manager.matches(event, "delete_file"):
            self._on_delete_file()
            return True

        if self.shortcut_manager.matches(event, "keep_file"):
            self._on_keep_file()
            return True

        # Enter/Space：视频播放（阶段四）/ 图片保留
        if self.shortcut_manager.matches(event, "play_or_keep"):
            self._on_play_or_keep()
            return True

        if self.shortcut_manager.matches(event, "favorite_file"):
            self._on_favorite_file()
            return True

        return False

    def _on_navigate_parent_folder(self) -> bool:
        """退格键导航：优先返回文件夹管理上一级，再恢复源目录复检。"""
        if self.folder_panel.go_back():
            return True
        if self._can_restore_source_from_review():
            self._on_restore_source_requested()
            return True
        return False

    def _can_restore_source_from_review(self) -> bool:
        restore_folders = self._normalize_folder_paths(self._review_source_snapshot)
        if not restore_folders or not self._source_folders:
            return False
        current_sources = [self._path_key(p) for p in self._source_folders]
        target_sources = [self._path_key(Path(p)) for p in restore_folders]
        return current_sources != target_sources

    def _on_play_or_keep(self):
        if self._is_current_video():
            self._play_current_video()
            return
        self._on_keep_file()

    def _play_current_video(self):
        """播放/暂停当前视频（同一视频在播则切换暂停）"""
        if not self.file_list or not (0 <= self.current_index < len(self.file_list)):
            return
        current_file = self.file_list[self.current_index]
        if current_file.suffix.lower() in SUPPORTED_VIDEO_FORMATS:
            self.media_viewer.toggle_video_play_pause(current_file)

    def _play_livp_video(self, file_path: Path):
        """实况(LIVP)播放：提取压缩包内视频到缓存后调用播放器。"""
        try:
            if file_path.suffix.lower() != ".livp":
                return

            live_cache_dir = self.cache_manager.base_dir / "live"
            live_cache_dir.mkdir(parents=True, exist_ok=True)

            key_raw = str(file_path)
            safe = "".join([c if c.isalnum() else "_" for c in key_raw])
            if len(safe) > 120:
                safe = safe[-120:]
            out_path = live_cache_dir / f"{safe}.mp4"

            extracted = MediaInfoExtractor.extract_live_photo_video(file_path, out_path)
            if extracted and extracted.exists():
                self.media_viewer.play_video(extracted)
        except Exception as e:
            logger.log_exception(e, f"LIVP 播放失败: {file_path}")

    def _play_sidecar_live_photo_video(self, image_path: Path):
        """iPhone Live Photo（JPG/HEIC + 同名 MOV）播放。"""
        try:
            if not image_path or not image_path.exists():
                return
            sidecar = image_path.with_suffix(".mov")
            if not sidecar.exists():
                sidecar = image_path.with_suffix(".MOV")
            if sidecar.exists() and sidecar.suffix.lower() in SUPPORTED_VIDEO_FORMATS:
                self.media_viewer.play_video(sidecar)
        except Exception as e:
            logger.log_exception(e, f"Live Photo sidecar 播放失败: {image_path}")

    def _on_playback_failed(self, file_name: str):
        self.app.toast(
            f"视频播放失败（可能缺少解码器）：{file_name}，"
            f"可右键文件列表选择在资源管理器中查看后用本地播放器打开",
            "warning",
        )

    def _is_current_video(self) -> bool:
        if not self.file_list or not (0 <= self.current_index < len(self.file_list)):
            return False
        return self.file_list[self.current_index].suffix.lower() in SUPPORTED_VIDEO_FORMATS

    # ==================== 工作目录管理 ====================

    def _on_change_work_dir(self):
        picked = QFileDialog.getExistingDirectory(
            self,
            "选择分类工作目录（将自动创建 默认/收藏/删除/重复文件 四个内置文件夹）",
        )
        if not picked:
            return
        try:
            self.folder_manager.set_work_dir(picked)
        except Exception as e:
            logger.log_exception(e, "设置工作目录失败")
            self.app.toast(f"设置工作目录失败: {e}", "error")
            return
        cfg.set_setting("work_dir", picked)
        self.folder_panel.refresh_folders()
        self._reload_work_dir_label()
        self.app.toast("分类目录已更新", "success")

    def _ensure_work_dir(self) -> bool:
        """确保工作目录可用；无则弹窗选择。返回 False=用户取消。"""
        if self.folder_manager._work_dir is not None:
            if self.folder_manager.work_dir.exists():
                return True
        picked = QFileDialog.getExistingDirectory(
            self,
            "首次分类需要选择工作目录（将自动创建 默认/收藏/删除/重复文件 四个内置文件夹）",
        )
        if not picked:
            return False
        try:
            self.folder_manager.set_work_dir(picked)
        except Exception as e:
            logger.log_exception(e, "设置工作目录失败")
            self.app.toast(f"设置工作目录失败: {e}", "error")
            return False
        cfg.set_setting("work_dir", picked)
        self.folder_panel.refresh_folders()
        self._reload_work_dir_label()
        return True

    def _reload_work_dir_label(self):
        if self.folder_manager._work_dir is None:
            self.work_dir_label.setText("未设置（分类时自动选择）")
            return
        work_dir = self.folder_manager.work_dir
        self.work_dir_label.setText(f"{work_dir}")

    @staticmethod
    def _normalize_file_operation_mode(mode) -> str:
        return "move" if str(mode or "").strip().lower() == "move" else "copy"

    def _on_op_mode_changed(self, index: int):
        self._file_operation_mode = self.op_mode_combo.itemData(index) or "copy"
        cfg.set_setting("file_operation_mode", self._file_operation_mode)

    def _is_move_mode(self) -> bool:
        return self._file_operation_mode == "move"

    def _operation_action_verb(self) -> str:
        return "移动" if self._is_move_mode() else "复制"

    # ==================== 分类 / 删除 / 收藏（阶段三核心） ====================

    def _transfer_files_to_folder(
        self, source_files: List[Path], target_folder: Path
    ) -> List[Dict[str, Path]]:
        transfer_records = FileProcessor.transfer_files_batch(
            source_files,
            target_folder,
            mode=self._file_operation_mode,
        )
        self._apply_auto_rename_to_transfer_records(transfer_records)
        self._persist_transferred_md5_hashes(transfer_records)
        return transfer_records

    def _persist_transferred_md5_hashes(self, transfer_records: List[Dict[str, Path]]):
        """分类时即时持久化 MD5，避免后续再次全量扫描分类库。"""
        if not transfer_records:
            return

        md5_hashes = set()
        hash_manager = MediaHashManager(sqlite_store=self.cache_manager.sqlite_store)
        for record in transfer_records:
            target_path = Path(record.get("target", ""))
            source_path = Path(record.get("source", ""))
            candidate = target_path if target_path.exists() else source_path
            if not candidate.exists() or candidate.suffix.lower() not in SUPPORTED_FORMATS:
                continue
            md5_value = hash_manager.calculate_md5(candidate)
            if md5_value:
                md5_hashes.add(md5_value)

        if md5_hashes:
            self.cache_manager.mark_md5_classified_batch(md5_hashes)

    def _apply_auto_rename_to_transfer_records(self, transfer_records: List[Dict[str, Path]]):
        """分类后按配置自动重命名目标文件。"""
        if not transfer_records or not self._auto_rename_enabled:
            return
        renamed_count = 0
        for record in transfer_records:
            target_path = Path(record.get("target", ""))
            if not target_path.exists():
                continue
            source_path = Path(record.get("source", ""))
            renamed_target = self._auto_rename_target_file(target_path, source_path=source_path)
            if renamed_target != target_path:
                record["target"] = renamed_target
                renamed_count += 1
        if renamed_count > 0:
            logger.log_operation(
                "自动重命名", f"{renamed_count}/{len(transfer_records)} 个文件已按字段追加命名"
            )

    def _auto_rename_target_file(self, target_path: Path, source_path: Optional[Path] = None) -> Path:
        info = self._extract_media_info_for_file(target_path)
        if not info and (source_path is None or not source_path):
            return target_path

        rendered_stem = self._render_auto_rename_stem(
            self._auto_rename_pattern,
            target_path=target_path,
            source_path=source_path,
            info=info,
        )
        if not rendered_stem:
            return target_path

        max_stem_len = 150
        if len(rendered_stem) > max_stem_len:
            rendered_stem = rendered_stem[:max_stem_len].rstrip(" ._")
        if not rendered_stem:
            return target_path

        desired_path = target_path.with_name(f"{rendered_stem}{target_path.suffix}")
        final_path = self._ensure_unique_file_path(desired_path)
        if final_path == target_path:
            return target_path

        try:
            target_path.rename(final_path)
            if target_path in self._video_info_cache:
                self._video_info_cache[final_path] = self._video_info_cache.pop(target_path)
            if target_path in self._video_cover_cache:
                self._video_cover_cache[final_path] = self._video_cover_cache.pop(target_path)
            return final_path
        except Exception as e:
            logger.log_exception(e, f"自动重命名失败: {target_path}")
            return target_path

    def _render_auto_rename_stem(
        self,
        pattern: str,
        target_path: Path,
        source_path: Optional[Path],
        info: Dict[str, str],
    ) -> str:
        """渲染自动重命名模板（英文占位符 + 普通文本）。"""
        template = (pattern or "").strip()
        if not template:
            return ""

        now = time.localtime()
        source_name = (
            source_path.stem if isinstance(source_path, Path) and source_path else target_path.stem
        )

        replacements = {
            "source_name": source_name,
            "format": info.get("格式", ""),
            "resolution": info.get("分辨率", ""),
            "file_size": info.get("文件大小", ""),
            "created_time": info.get("创建时间", ""),
            "modified_time": info.get("修改时间", ""),
            "captured_time": info.get("拍摄时间", ""),
            "codec": info.get("编码", ""),
            "fps": info.get("帧率", ""),
            "duration": info.get("时长", ""),
            "bitrate": info.get("码率", ""),
            "maker": info.get("制造商", ""),
            "camera_model": info.get("相机型号", ""),
            "lens_model": info.get("镜头型号", ""),
            "iso": info.get("ISO", ""),
            "aperture": info.get("光圈", ""),
            "exposure_time": info.get("曝光时间", ""),
            "focal_length": info.get("焦距", ""),
            "date": time.strftime("%Y%m%d", now),
            "time": time.strftime("%H%M%S", now),
            "datetime": time.strftime("%Y%m%d_%H%M%S", now),
        }

        def _replace_token(match):
            token = match.group(1).strip()
            value = str(replacements.get(token, "")).strip()
            if not value or value == "未知":
                return ""
            return self._sanitize_filename_token(value)

        rendered = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", _replace_token, template)
        rendered = self._sanitize_filename_token(rendered, preserve_delimiter=True, max_len=None)
        return rendered

    def _extract_media_info_for_file(self, file_path: Path) -> Dict[str, str]:
        suffix = file_path.suffix.lower()
        if suffix in SUPPORTED_IMAGE_FORMATS:
            return MediaInfoExtractor.extract_image_info(file_path)
        if suffix in SUPPORTED_VIDEO_FORMATS:
            cached = self._video_info_cache.get(file_path)
            if cached:
                return cached
            info = MediaInfoExtractor.extract_video_info(file_path)
            self._video_info_cache[file_path] = info
            return info
        return {}

    @staticmethod
    def _sanitize_filename_token(
        value: str,
        preserve_delimiter: bool = False,
        max_len: Optional[int] = 48,
    ) -> str:
        """清洗文件名 token：去非法字符，压缩空白。"""
        import unicodedata

        cleaned = str(value or "")
        cleaned = cleaned.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        cleaned = "".join(ch for ch in cleaned if not (0x200B <= ord(ch) <= 0x200F))
        cleaned = unicodedata.normalize("NFKC", cleaned)

        illegal = '<>:"|?*'
        if preserve_delimiter:
            illegal = '<>:"|?*'  # 保留 / 供分隔
            cleaned = "".join(ch if (ch not in illegal or ch == "/") else "_" for ch in cleaned)
        else:
            cleaned = "".join(ch if ch not in illegal else "_" for ch in cleaned)

        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if max_len is not None and len(cleaned) > max_len:
            cleaned = cleaned[:max_len].rstrip(" ._")
        return cleaned

    @staticmethod
    def _ensure_unique_file_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        for counter in range(1, 10001):
            candidate = path.with_name(f"{stem}({counter}){suffix}")
            if not candidate.exists():
                return candidate
        return path

    def _render_pattern_for_panel(self, pattern: str, target_path: Path) -> str:
        """右栏模板套用渲染入口（与自动重命名同一套占位符逻辑）。"""
        info = self._extract_media_info_for_file(target_path)
        return self._render_auto_rename_stem(
            pattern, target_path=target_path, source_path=target_path, info=info
        )

    def _on_auto_rename_config_changed(self, enabled: bool, pattern: str):
        """右栏保存自动重命名配置 → 页面状态 + 持久化。"""
        self._auto_rename_enabled = bool(enabled)
        self._auto_rename_pattern = (
            str(pattern or "{source_name}").strip() or "{source_name}"
        )
        cfg.set_setting("auto_rename_enabled", self._auto_rename_enabled)
        cfg.set_setting("auto_rename_pattern", self._auto_rename_pattern)
        logger.log_operation(
            "自动重命名配置",
            f"enabled={self._auto_rename_enabled}, pattern={self._auto_rename_pattern}",
        )

    def _on_rename_requested(self, new_name: str):
        """重命名当前文件（扩展名补全 + 列表/缓存/懒加载联动）。"""
        if not self.file_list or not (0 <= self.current_index < len(self.file_list)):
            self.app.toast("当前没有可重命名的文件", "warning")
            return

        current_file = self.file_list[self.current_index]
        target_name = (new_name or "").strip()

        if not target_name:
            self.app.toast("文件名不能为空", "warning")
            return
        if Path(target_name).name != target_name or target_name in (".", ".."):
            self.app.toast("文件名不合法", "error")
            return
        if not Path(target_name).suffix:
            target_name = f"{target_name}{current_file.suffix}"

        target_path = current_file.with_name(target_name)

        if target_path == current_file:
            self.app.toast("文件名未变化", "info")
            return
        if target_path.exists():
            self.app.toast(f"目标文件已存在: {target_path.name}", "warning")
            return

        try:
            current_file.rename(target_path)

            self.file_list[self.current_index] = target_path

            if current_file in self._video_cover_cache:
                self._video_cover_cache[target_path] = (
                    self._video_cover_cache.pop(current_file)
                )
            if current_file in self._video_info_cache:
                self._video_info_cache[target_path] = (
                    self._video_info_cache.pop(current_file)
                )

            corrupted = self.file_scanner.corrupted_files
            if current_file in corrupted:
                corrupted.discard(current_file)
                corrupted.add(target_path)

            self.selected_files = [
                target_path if f == current_file else f for f in self.selected_files
            ]

            self._rename_lazy_state_file(current_file, target_path)
            self.file_list_panel.rename_file(current_file, target_path)

            self.file_info_panel.update_current_file_path(target_path)
            self._display_file(self.current_index)

            self.app.toast(
                f"重命名成功: {current_file.name} → {target_path.name}", "success"
            )
            logger.log_operation("重命名文件", f"{current_file} -> {target_path}")
        except Exception as e:
            logger.log_exception(e, f"重命名文件失败: {current_file}")
            self.app.toast(f"重命名失败: {e}", "error")

    def _collect_operation_files(self, action_name: str) -> Optional[List[Path]]:
        """收集操作目标文件：多选→批量；否则当前文件。"""
        if len(self.selected_files) > 1:
            return list(self.selected_files)
        if not self.file_list or not (0 <= self.current_index < len(self.file_list)):
            self.app.toast("当前没有可操作的文件", "warning")
            return None
        return [self.file_list[self.current_index]]

    def _classify_current_files(
        self,
        op_type: OperationType,
        default_folder_key: str,
        action_label: str,
    ):
        """分类统一入口：删除/保留/收藏共用（转移→记录→从列表移除→自动前进）。"""
        files_to_process = self._collect_operation_files(action_label)
        if not files_to_process:
            return

        if not self._ensure_work_dir():
            return

        try:
            if self.file_list and 0 <= self.current_index < len(self.file_list):
                current_anchor = self.file_list[self.current_index]
            else:
                current_anchor = files_to_process[0]

            # 目标文件夹：面板选中项优先，否则内置默认/收藏/删除
            selected_folder = self.folder_panel.get_selected_folder()
            if op_type == OperationType.KEEP and selected_folder:
                target_folder = self.folder_manager.work_dir / Path(selected_folder)
            else:
                target_folder = self.folder_manager.work_dir / BUILTIN_FOLDERS[default_folder_key]

            transfer_records = self._transfer_files_to_folder(files_to_process, target_folder)
            if not transfer_records:
                self.app.toast("没有文件被处理，请检查权限或文件状态", "warning")
                return

            transferred_files = [item["source"] for item in transfer_records]
            removed_set = set(transferred_files)
            preferred_next = self._pick_next_file_after_removal(current_anchor, removed_set)
            original_positions = [
                self.file_list.index(file_path)
                for file_path in transferred_files
                if file_path in self.file_list
            ]
            restore_index = min(original_positions) if original_positions else max(0, self.current_index)

            self._remember_classified_target_folder(target_folder)

            self.operation_history.add_operation(
                op_type,
                {
                    "files": [str(f) for f in transferred_files],
                    "target_folder": str(target_folder),
                    "restore_index": int(restore_index),
                    "mode": self._file_operation_mode,
                    "transfers": [
                        {"source": str(item["source"]), "target": str(item["target"])}
                        for item in transfer_records
                    ],
                },
            )

            for file_path in transferred_files:
                if file_path in self.file_list:
                    self.file_list.remove(file_path)
            self._remove_mode_switch_base_files(transferred_files)

            self.file_list_panel.remove_files(transferred_files)
            self.folder_panel.refresh_visible_folder_counts()

            folder_name = target_folder.name
            self.app.toast(
                f"{action_label}成功：已{self._operation_action_verb()} "
                f"{len(transferred_files)} 个文件到「{folder_name}」",
                "success",
            )
            self.selected_files = [f for f in self.selected_files if f not in removed_set]

            self._auto_advance(preferred_next)
        except Exception as e:
            logger.log_exception(e, f"{action_label}文件失败")
            self.app.toast(f"{action_label}失败: {e}", "error")

    def _on_delete_file(self):
        self._classify_current_files(OperationType.DELETE, "deleted", "删除")

    def _on_keep_file(self):
        self._classify_current_files(OperationType.KEEP, "default", "保留")

    def _on_favorite_file(self):
        self._classify_current_files(OperationType.FAVORITE, "favorite", "收藏")

    def _move_to_folder_by_index(self, folder_index: int):
        """数字键 1-9 快速分类：移动/复制到面板第 N 个文件夹。"""
        visible_folders = self.folder_panel.get_visible_folders()
        if not (0 <= folder_index < len(visible_folders)):
            return
        folder_name, _is_builtin, folder_path, _rel = visible_folders[folder_index]
        self._classify_to_folder(folder_path, folder_name)

    def _classify_to_folder(self, target_folder: Path, folder_name: str):
        files_to_process = self._collect_operation_files("分类")
        if not files_to_process:
            return
        if not self._ensure_work_dir():
            return

        try:
            if self.file_list and 0 <= self.current_index < len(self.file_list):
                current_anchor = self.file_list[self.current_index]
            else:
                current_anchor = files_to_process[0]

            transfer_records = self._transfer_files_to_folder(files_to_process, target_folder)
            if not transfer_records:
                self.app.toast("没有文件被处理，请检查权限或文件状态", "warning")
                return

            transferred_files = [item["source"] for item in transfer_records]
            removed_set = set(transferred_files)
            preferred_next = self._pick_next_file_after_removal(current_anchor, removed_set)
            original_positions = [
                self.file_list.index(file_path)
                for file_path in transferred_files
                if file_path in self.file_list
            ]
            restore_index = min(original_positions) if original_positions else max(0, self.current_index)

            self._remember_classified_target_folder(target_folder)

            self.operation_history.add_operation(
                OperationType.KEEP,
                {
                    "files": [str(f) for f in transferred_files],
                    "target_folder": str(target_folder),
                    "restore_index": int(restore_index),
                    "mode": self._file_operation_mode,
                    "transfers": [
                        {"source": str(item["source"]), "target": str(item["target"])}
                        for item in transfer_records
                    ],
                },
            )

            for file_path in transferred_files:
                if file_path in self.file_list:
                    self.file_list.remove(file_path)
            self._remove_mode_switch_base_files(transferred_files)

            self.file_list_panel.remove_files(transferred_files)
            self.folder_panel.refresh_visible_folder_counts()

            self.app.toast(
                f"已{self._operation_action_verb()} {len(transferred_files)} 个文件到「{folder_name}」",
                "success",
            )
            self.selected_files = [f for f in self.selected_files if f not in removed_set]

            self._auto_advance(preferred_next)
        except Exception as e:
            logger.log_exception(e, f"分类到 {folder_name} 失败")
            self.app.toast(f"分类失败: {e}", "error")

    def _on_prev_file(self):
        """上一张文件。"""
        if self.current_index > 0:
            self.current_index -= 1
            self._display_file(self.current_index)
        else:
            self.app.toast("已是第一张文件", "info")

    def _on_next_file(self):
        """下一张文件（自动保留当前）。"""
        self._on_keep_file()

    def _pick_next_file_after_removal(self, anchor_file: Path, removed_files: set) -> Optional[Path]:
        """按左侧可见顺序选择移除后的下一张（优先后一个，兜底前一个）。"""
        if not self.file_list:
            return None

        if hasattr(self.file_list_panel, "get_adjacent_visible_file"):
            cursor = anchor_file
            for _ in range(len(self.file_list) + 2):
                candidate = self.file_list_panel.get_adjacent_visible_file(cursor, forward=True)
                if candidate is None:
                    break
                if candidate not in removed_files:
                    return candidate
                cursor = candidate

            cursor = anchor_file
            for _ in range(len(self.file_list) + 2):
                candidate = self.file_list_panel.get_adjacent_visible_file(cursor, forward=False)
                if candidate is None:
                    break
                if candidate not in removed_files:
                    return candidate
                cursor = candidate

        try:
            anchor_index = self.file_list.index(anchor_file)
        except ValueError:
            anchor_index = min(max(self.current_index, 0), max(0, len(self.file_list) - 1))

        for i in range(anchor_index + 1, len(self.file_list)):
            if self.file_list[i] not in removed_files:
                return self.file_list[i]
        for i in range(anchor_index - 1, -1, -1):
            if self.file_list[i] not in removed_files:
                return self.file_list[i]
        return None

    def _auto_advance(self, preferred_next: Optional[Path] = None):
        """分类后自动切换到下一张文件。"""
        if not self.file_list:
            self.app.toast("所有文件已处理完毕", "success")
            self.media_viewer.update_progress(0, 0)
            return

        if preferred_next is not None and preferred_next in self.file_list:
            self.current_index = self.file_list.index(preferred_next)
            self._display_file(self.current_index)
            return

        if self.current_index >= len(self.file_list):
            self.current_index = len(self.file_list) - 1
        if self.current_index >= 0:
            self._display_file(self.current_index)

    # ==================== 撤销 ====================

    def _on_undo(self):
        """撤回上一步操作（Ctrl+Z）。"""
        try:
            operation = self.operation_history.undo()
            if operation is None:
                self.app.toast("没有可撤回的操作", "info")
                return

            if operation.op_type in (OperationType.KEEP, OperationType.DELETE, OperationType.FAVORITE):
                files = operation.data.get("files", [])
                try:
                    restore_index = int(operation.data.get("restore_index", self.current_index))
                except Exception:
                    restore_index = self.current_index
                restore_index = max(0, min(restore_index, len(self.file_list)))

                pending_restore_files = []
                missing_files = []
                for offset, file_path_str in enumerate(files):
                    file_path = Path(file_path_str)
                    if not file_path.exists():
                        missing_files.append(file_path)
                        continue
                    if file_path not in self.file_list:
                        insert_at = min(restore_index + offset, len(self.file_list))
                        self.file_list.insert(insert_at, file_path)
                        pending_restore_files.append(file_path)

                        folder_key = self.file_list_panel.ensure_folder_chain_for_file(file_path)
                        index_in_folder = sum(
                            1 for i in range(insert_at)
                            if self.file_list_panel.ensure_folder_chain_for_file(self.file_list[i]) == folder_key
                        )
                        self.file_list_panel.add_file(
                            file_path,
                            None,  # 占位图，缩略图异步补
                            FileStatus.NORMAL,
                            index_in_folder=index_in_folder,
                            refresh=False,
                        )

                self.file_list_panel.refresh_after_batch()
                self.folder_panel.refresh_visible_folder_counts()
                self._add_mode_switch_base_files(pending_restore_files)

                if pending_restore_files:
                    if self._undo_thumbnail_loader is not None and \
                            self._undo_thumbnail_loader.isRunning():
                        self._undo_thumbnail_loader.stop()
                        self._undo_thumbnail_loader.wait(200)
                    self._undo_thumbnail_loader = ThumbnailLoader(
                        pending_restore_files, cache_manager=self.cache_manager
                    )
                    self._undo_thumbnail_loader.thumbnail_loaded.connect(
                        self._on_undo_thumbnail_loaded
                    )
                    self._undo_thumbnail_loader.start()

                if missing_files:
                    logger.warning(f"撤回时跳过不存在文件: {len(missing_files)}")
                    self.app.toast(
                        f"已撤回 {operation.op_type.value}，但有 {len(missing_files)} 个文件不存在，未恢复到列表",
                        "warning",
                    )
                else:
                    self.app.toast(f"已撤回 {operation.op_type.value} 操作", "success")

                if files:
                    first_restored = Path(files[0])
                    if first_restored in self.file_list:
                        self.current_index = self.file_list.index(first_restored)
                        self._display_file(self.current_index)
                    elif self.file_list:
                        restore_index = max(0, min(restore_index, len(self.file_list) - 1))
                        self.current_index = restore_index
                        self._display_file(self.current_index)
                elif self.file_list:
                    self.current_index = min(max(restore_index, 0), len(self.file_list) - 1)
                    self._display_file(self.current_index)
            else:
                self.app.toast(f"已撤回 {operation.op_type.value} 操作", "success")
        except Exception as e:
            logger.log_exception(e, "撤回操作失败")
            self.app.toast(f"撤回失败: {e}", "error")

    def _on_undo_thumbnail_loaded(self, file_path: Path, thumbnail):
        if thumbnail is None or thumbnail.isNull():
            return
        if not self.file_list_panel.has_file(file_path):
            return
        self.file_list_panel.add_file(file_path, thumbnail, FileStatus.NORMAL, refresh=False)

    # ==================== 文件夹面板信号处理 ====================

    def _on_folder_selected(self, folder_relative_path: str):
        # 选中文件夹只影响「保留」目标，无需额外动作（面包屑在面板内维护）
        pass

    def _on_folder_created(self, folder_relative_path: str):
        self._remember_classified_target_folder(
            self.folder_manager.work_dir / Path(folder_relative_path)
        )
        self.folder_panel.refresh_visible_folder_counts()

    def _on_folder_renamed(self, old_relative_path: str, new_relative_path: str):
        self._sync_classified_target_folders_after_folder_change(
            renamed_pairs=[(old_relative_path, new_relative_path)]
        )

    def _on_folder_deleted(self, relative_path: str):
        self._sync_classified_target_folders_after_folder_change(
            deleted_relatives=[relative_path]
        )

    def _sync_classified_target_folders_after_folder_change(
        self,
        renamed_pairs=None,
        deleted_relatives=None,
    ):
        """文件夹改名/删除后同步 classified_target_folders 配置。"""
        if not self.folder_manager or self.folder_manager._work_dir is None:
            return

        renamed_pairs = renamed_pairs or []
        deleted_relatives = deleted_relatives or []

        existing = self._normalize_folder_paths(cfg.get_list("classified_target_folders"))
        if not existing:
            return

        work_dir = self.folder_manager.work_dir

        def _rel_norm(raw: str) -> str:
            return str(raw or "").replace("\\", "/").strip("/")

        renamed_abs_pairs = []
        for old_rel, new_rel in renamed_pairs:
            try:
                old_abs = str((work_dir / Path(_rel_norm(old_rel))).resolve())
                new_abs = str((work_dir / Path(_rel_norm(new_rel))).resolve())
            except Exception:
                continue
            renamed_abs_pairs.append((old_abs, new_abs))

        deleted_abs_keys = set()
        for rel in deleted_relatives:
            try:
                deleted_abs = (work_dir / Path(_rel_norm(rel))).resolve()
            except Exception:
                continue
            deleted_abs_keys.add(self._path_key(deleted_abs))

        updated_paths = []
        for p in existing:
            replaced = p
            for old_abs, new_abs in renamed_abs_pairs:
                if self._path_key(Path(replaced)) == self._path_key(Path(old_abs)):
                    replaced = new_abs
                    break

            if deleted_abs_keys and self._path_key(Path(replaced)) in deleted_abs_keys:
                continue
            if not Path(replaced).exists():
                continue
            if replaced not in updated_paths:
                updated_paths.append(replaced)

        if updated_paths != existing:
            cfg.set_list("classified_target_folders", updated_paths)

    # ==================== 复检模式 ====================

    def _resolve_work_subfolder(self, folder_relative_path: str) -> Optional[Path]:
        """解析并校验工作目录下的相对目录路径。"""
        if not self.folder_manager or self.folder_manager._work_dir is None:
            self.app.toast("请先设置分类工作目录", "warning")
            return None

        work_dir = self.folder_manager.work_dir
        target = work_dir / Path(folder_relative_path)
        try:
            target = target.resolve()
        except Exception:
            target = target.absolute()

        if not target.exists() or not target.is_dir():
            self.app.toast(f"目录不可用: {target}", "warning")
            return None

        try:
            if os.path.commonpath([self._path_key(target), self._path_key(work_dir)]) != \
                    self._path_key(work_dir):
                self.app.toast("仅支持工作目录内的文件夹", "warning")
                return None
        except ValueError:
            self.app.toast("目录路径无效", "warning")
            return None

        return target

    def _on_folder_preview_requested(self, folder_relative_path: str):
        """左侧复检：加载指定分类目录到左侧列表继续分类。"""
        target = self._resolve_work_subfolder(folder_relative_path)
        if target is None:
            return

        try:
            current_sources = self._normalize_folder_paths(
                [str(p) for p in self._source_folders]
            )
            if current_sources:
                self._review_source_snapshot = list(current_sources)
            self._on_folders_dropped(
                [str(target)],
                persist_default=False,
                enable_md5_filter=False,
            )
        except Exception as e:
            logger.log_exception(e, "进入左侧复检失败")
            self.app.toast(f"复检失败: {e}", "error")
            return

        if self.file_list:
            self.app.toast(
                f"已进入复检模式（{folder_relative_path}），完成后可点「返回源目录」", "info"
            )
        else:
            self.app.toast(f"{folder_relative_path} 中未找到可预览的图片/视频", "warning")

    def _on_restore_source_requested(self):
        """从复检视图返回默认源目录。"""
        restore_folders = self._normalize_folder_paths(
            self._review_source_snapshot or cfg.get_list("source_folders")
        )
        if not restore_folders:
            self.app.toast("没有可恢复的源目录", "warning")
            return

        try:
            self._on_folders_dropped(
                restore_folders,
                persist_default=False,
                enable_md5_filter=True,
            )
            self.app.toast(f"已恢复源目录（共 {len(restore_folders)} 个）", "success")
        except Exception as e:
            logger.log_exception(e, "恢复源目录失败")
            self.app.toast(f"恢复失败: {e}", "error")

    def _on_folder_clear_requested(self, folder_path_str: str):
        """清空文件夹内容并将其中文件的 MD5 标记为已分类。"""
        folder_path = Path(folder_path_str)
        if not folder_path.exists() or not folder_path.is_dir():
            self.app.toast("文件夹不存在", "warning")
            return

        try:
            media_files = []
            for item in folder_path.rglob("*"):
                if item.is_file() and item.suffix.lower() in SUPPORTED_FORMATS:
                    media_files.append(item)

            md5_hashes = set()
            if media_files:
                hash_manager = MediaHashManager(sqlite_store=self.cache_manager.sqlite_store)
                for mf in media_files:
                    md5_value = hash_manager.calculate_md5(mf)
                    if md5_value:
                        md5_hashes.add(md5_value)

            if md5_hashes:
                self.cache_manager.mark_md5_classified_batch(md5_hashes)

            for child in folder_path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except Exception:
                        pass

            self._remember_classified_target_folder(folder_path)
            self.folder_panel.refresh_visible_folder_counts()

            self.app.toast(
                f"已清空 {len(media_files)} 个文件，记录 {len(md5_hashes)} 条 MD5", "success"
            )
            logger.log_operation(
                "清空文件夹", f"{folder_path} ({len(media_files)} files, {len(md5_hashes)} MD5)"
            )
        except Exception as e:
            logger.log_exception(e, f"清空文件夹失败: {folder_path}")
            self.app.toast(f"清空失败: {e}", "error")

    def _on_file_dropped_to_folder(self, file_path_strs: list, folder_path_str: str):
        """拖拽文件到文件夹卡片：按当前复制/移动模式分类。"""
        folder_path = Path(folder_path_str)
        try:
            folder_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.log_exception(e, f"创建目标文件夹失败: {folder_path}")
            self.app.toast(f"目标文件夹不可用: {folder_path}", "error")
            return

        source_files = []
        for raw in file_path_strs:
            file_path = Path(raw)
            if file_path.exists() and file_path.is_file() and \
                    file_path.suffix.lower() in SUPPORTED_FORMATS:
                source_files.append(file_path)

        if not source_files:
            self.app.toast("没有可分类的媒体文件", "warning")
            return

        try:
            transfer_records = self._transfer_files_to_folder(source_files, folder_path)
            if not transfer_records:
                self.app.toast("没有文件被处理，请检查权限或文件状态", "warning")
                return

            transferred_files = [item["source"] for item in transfer_records]
            self._remember_classified_target_folder(folder_path)

            # 拖入的文件若在当前列表中，同步移除
            removed = [f for f in transferred_files if f in self.file_list]
            if removed:
                removed_set = set(removed)
                current_anchor = None
                if 0 <= self.current_index < len(self.file_list):
                    current_anchor = self.file_list[self.current_index]
                original_positions = [
                    self.file_list.index(f) for f in removed if f in self.file_list
                ]
                restore_index = (
                    min(original_positions) if original_positions else max(0, self.current_index)
                )
                self.operation_history.add_operation(
                    OperationType.KEEP,
                    {
                        "files": [str(f) for f in transferred_files],
                        "target_folder": str(folder_path),
                        "restore_index": int(restore_index),
                        "mode": self._file_operation_mode,
                        "transfers": [
                            {"source": str(item["source"]), "target": str(item["target"])}
                            for item in transfer_records
                        ],
                    },
                )
                for file_path in removed:
                    self.file_list.remove(file_path)
                self._remove_mode_switch_base_files(removed)
                self.file_list_panel.remove_files(removed)
                self.selected_files = [f for f in self.selected_files if f not in removed_set]
                preferred_next = (
                    self._pick_next_file_after_removal(current_anchor, removed_set)
                    if current_anchor else None
                )
                self._auto_advance(preferred_next)
            else:
                self.app.toast(
                    f"已{self._operation_action_verb()} {len(transferred_files)} 个文件到「{folder_path.name}」",
                    "success",
                )

            self.folder_panel.refresh_visible_folder_counts()
        except Exception as e:
            logger.log_exception(e, "拖拽分类失败")
            self.app.toast(f"拖拽分类失败: {e}", "error")

    # ==================== 路径工具 ====================

    def _normalize_folder_paths(self, folder_paths: List[str]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for folder_path in folder_paths:
            path = Path(folder_path)
            if not path.exists() or not path.is_dir():
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path.absolute()

            key = os.path.normcase(os.path.normpath(str(resolved)))
            if key in seen:
                continue
            seen.add(key)
            normalized.append(str(resolved))
        return normalized

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

    def _wait_thread_responsive(self, thread, timeout_ms: int = 4000) -> bool:
        if thread is None or (not thread.isRunning()):
            return True

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while thread.isRunning() and time.monotonic() < deadline:
            thread.wait(80)
            QApplication.processEvents()
        return not thread.isRunning()

    # ==================== 拖放白名单接口（main_window dropEvent 调用） ====================

    def add_dropped_folder(self, dir_path: str):
        self._add_source_folders([dir_path])

    def add_dropped_files(self, file_paths: list):
        parents = []
        for fp in file_paths:
            parent = str(Path(fp).parent)
            if parent not in parents:
                parents.append(parent)
        self._add_source_folders(parents)

    # ==================== 生命周期 ====================

    def on_show(self):
        # 页面级快捷键：装在 QApplication 上，全页可见期生效
        self._install_shortcut_filter()
        self.folder_panel.refresh_if_theme_changed()
        if not self._startup_restore_done:
            self._startup_restore_done = True
            saved = self._normalize_folder_paths(cfg.get_list("source_folders"))
            if saved:
                # 页面首次显示时恢复上次的源文件夹（后台扫描自动接管线）
                self._on_folders_dropped(saved, persist_default=False)
            else:
                self._set_pipeline_idle_status()
            return
        # 切回页面：恢复懒加载（on_hide 已停缩略图线程）
        if self.file_list:
            self._prefetch_visible_missing_thumbnails(force=True)
            self._auto_load_more_if_idle()

    def on_return_press(self):
        """回车键（主窗口 eventFilter 兜底分发）：与 play_or_keep 快捷键同逻辑。"""
        if self._shortcuts_installed and self._shortcut_mapping_dialog_open is False:
            pass  # 页面过滤器已处理时不会走到这里；此入口仅为过滤器未装时兜底
        self._on_play_or_keep()

    def on_hide(self):
        # 页面隐藏时卸载快捷键过滤器（防止 1-9 等按键泄漏到其他页面）
        self._remove_shortcut_filter()
        # 切页停止视频播放（声音不应跨页残留）
        self.media_viewer.stop_video()
        # 停止后台缩略图线程，避免占用资源
        self._retire_thumbnail_loader(timeout_ms=200)

    def shutdown(self):
        # 窗口关闭：卸载过滤器；停播放器与缩略图线程；MD5/去重线程不可中断，短暂等待兜底
        self._remove_shortcut_filter()
        self.media_viewer.stop_video()
        self._retire_thumbnail_loader(timeout_ms=300)
        if self._undo_thumbnail_loader is not None and self._undo_thumbnail_loader.isRunning():
            self._undo_thumbnail_loader.stop()
            self._undo_thumbnail_loader.wait(300)
        for thread in (self.md5_filter_thread, self.duplicate_thread):
            if thread is not None and thread.isRunning():
                thread.wait(500)
