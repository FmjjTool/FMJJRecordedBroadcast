"""实用工具 · 图片筛选分类页（阶段二：三栏组件 + 懒加载 + 扫描/MD5/去重管线）。

整体规划见 image-sorter-migration-plan：
- 阶段一：骨架 + 源文件夹管理（拖放白名单）✅
- 阶段二（本文件）：左栏缩略图卡片流（懒加载）+ 中栏预览 + 右栏详情
  + 后台扫描线程 + MD5 过滤/重复检测管线 + 视图模式三态切换
- 阶段三：分类/删除/收藏键盘工作流 + 页内设置区
- 阶段四：视频/LIVP 播放 + 压缩 + 去重 UI

与源项目 MainWindow 的差异：
- QMainWindow+qfluentwidgets → BasePage（工具箱页面体系），反馈改 app.toast
- 扫描由同步 processEvents 改为 run_bg 后台线程（每次扫描用独立 FileScanner 实例，
  on_done 校验 scan_id 防串台；进度经 post_to_main 回主线程）
- 视频自动播放 / LIVP 自动播放 → 阶段四（MediaViewer 尚无播放器）
"""

import os
import time
from collections import deque
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QApplication, QSplitter, QVBoxLayout,
)

from gui_qt.pages.base import BasePage
from gui_qt.common import run_bg, post_to_main

from image_sorter import settings as cfg
from image_sorter.constants import (
    FileStatus,
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
from image_sorter.core.file_scanner import FileScanner
from image_sorter.core.folder_manager import FolderManager
from image_sorter.core.md5_filter_thread import Md5FilterThread
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter.core.thumbnail_generator import ThumbnailGenerator
from image_sorter._log import logger

from gui_qt.pages.image_sorter_widgets import FileListPanel, MediaViewer, FileInfoPanel

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

        # ---------- 顶栏 ----------
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

        # ---------- 管线状态栏 ----------
        self.pipeline_label = QLabel("")
        self.pipeline_label.setStyleSheet("color:#6b7280; padding: 1px 2px;")
        layout.addWidget(self.pipeline_label)

        # ---------- 三栏 ----------
        splitter = QSplitter(Qt.Horizontal)

        self.file_list_panel = FileListPanel(self)
        self.file_list_panel.set_grouping_mode(self._resolve_grouping_mode_for_view(self._view_mode))
        self.file_list_panel.set_grouping_datetime_resolver(self._get_grouping_datetime_fast)
        splitter.addWidget(self.file_list_panel)

        self.media_viewer = MediaViewer(self)
        splitter.addWidget(self.media_viewer)

        self.file_info_panel = FileInfoPanel(self)
        splitter.addWidget(self.file_info_panel)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([330, 440, 330])
        layout.addWidget(splitter, 1)

        self._render_pipeline_status()
        self._reload_sources_label()

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

    def _on_folders_dropped(self, folder_paths: List[str], persist_default: bool = True):
        """导入源文件夹：后台扫描 → 主线程接管懒加载流水线。"""
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
        filter_enabled = cfg.get_bool("filter_screened_files", True)
        if filter_enabled:
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
                        self.media_viewer.display_pil_image(live_cover, file_path)
                    else:
                        self.media_viewer.display_image(file_path)
                else:
                    self.media_viewer.display_image(file_path)

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
                    self.media_viewer.clear()

                info = self._video_info_cache.get(file_path)
                if info is None:
                    info = MediaInfoExtractor.extract_video_info(file_path)
                    self._video_info_cache[file_path] = info
                self.file_info_panel.display_video_info(file_path, info)

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
        """回车键：显示下一个文件（阶段三接入分类工作流前的占位导航）。"""
        if not self.file_list:
            return
        self._display_file(min(self.current_index + 1, len(self.file_list) - 1))

    def on_hide(self):
        # 页面隐藏时停止后台缩略图线程，避免占用资源
        self._retire_thumbnail_loader(timeout_ms=200)

    def shutdown(self):
        # 窗口关闭：停缩略图线程；MD5/去重线程不可中断，短暂等待兜底
        self._retire_thumbnail_loader(timeout_ms=300)
        for thread in (self.md5_filter_thread, self.duplicate_thread):
            if thread is not None and thread.isRunning():
                thread.wait(500)
