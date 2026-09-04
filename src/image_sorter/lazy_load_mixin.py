"""懒加载与缩略图管理 Mixin（从源项目 ui/lazy_load_mixin.py 移植）。

与源版本的差异：
- PyQt6 → PySide6（QTimer/QPixmap import 路径）
- app_config.config → image_sorter.settings（cfg）
- 移除忙等浮层 hold（_hold_busy_overlay_until_media_ready / _release_overlay_hold_if_needed）
  与启动进度回调——工具箱页面无对应机制
- 宿主（页面类）需提供：file_list / current_index / selected_files / _source_folders /
  file_list_panel / media_viewer / file_info_panel / cache_manager / file_scanner /
  thumbnail_loader / md5_filter_thread / duplicate_thread / _set_pipeline_stage_status /
  _start_md5_filter / _start_auto_tasks_for_scan / _wait_thread_responsive /
  _path_key / _is_path_in_folder 等状态与方法
"""

from collections import deque
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Deque, List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap

from image_sorter.core.thumbnail_generator import ThumbnailLoader
from image_sorter import settings as cfg
from image_sorter.constants import FileStatus, BUILTIN_FOLDERS
from image_sorter._log import logger


class LazyLoadMixin:
    """文件列表懒加载、缩略图、日期分组与模式切换管理。"""

    def _disconnect_thumbnail_loader_signals(self, loader: Optional[ThumbnailLoader]):
        """断开缩略图线程信号，避免旧线程回写当前会话。"""
        if loader is None:
            return
        try:
            loader.thumbnail_loaded.disconnect(self._on_thumbnail_loaded)
        except Exception:
            pass
        try:
            loader.load_failed.disconnect(self._on_thumbnail_failed)
        except Exception:
            pass
        try:
            loader.progress_changed.disconnect(self._on_thumbnail_progress)
        except Exception:
            pass
        try:
            loader.finished.disconnect(self._on_thumbnail_loading_finished)
        except Exception:
            pass

    def _retire_thumbnail_loader(self, timeout_ms: int = 0):
        """停止并退役当前缩略图线程（即使线程未立刻结束，也不再接收其回调）。"""
        loader = self.thumbnail_loader
        if loader is None:
            return

        self._disconnect_thumbnail_loader_signals(loader)
        try:
            if loader.isRunning():
                loader.stop()
                if timeout_ms > 0:
                    self._wait_thread_responsive(loader, timeout_ms=timeout_ms)
        except Exception:
            pass
        finally:
            if self.thumbnail_loader is loader:
                self.thumbnail_loader = None

    def _is_active_thumbnail_loader_signal(self) -> bool:
        """当前缩略图回调是否来自活跃线程。"""
        sender = self.sender()
        if sender is None:
            return False
        if sender is not self.thumbnail_loader:
            return False
        sender_scan_id = getattr(sender, "_scan_id", None)
        return (
            sender_scan_id == self._active_scan_id
            and self._thumbnail_scan_id == self._active_scan_id
        )

    def _get_grouping_timestamp_fast(self, file_path: Path, persist_missing: bool = True) -> float:
        """
        日期分组快速时间戳：
        - 优先 SQLite 缓存
        - 未命中时退化为文件 mtime（不走 EXIF，避免排序卡顿）
        """
        try:
            stat = file_path.stat()
        except Exception:
            return 0.0

        cached_ts = self.cache_manager.get_cached_grouping_timestamp(file_path, stat_result=stat)
        if cached_ts is not None:
            try:
                return float(cached_ts)
            except Exception:
                pass

        fallback_ts = float(stat.st_mtime)
        if persist_missing:
            self.cache_manager.save_grouping_timestamp(file_path, fallback_ts, stat_result=stat)
        return fallback_ts

    def _get_grouping_datetime_fast(self, file_path: Path):
        """供左侧日期分组使用的快速时间解析。"""
        ts = self._get_grouping_timestamp_fast(file_path)
        if ts <= 0:
            return None
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None

    def _set_mode_switch_base_files(self, file_paths: List[Path]):
        """记录模式切换时可复用的全量文件基准（去重保序）。"""
        previous_keys = [self._path_key(p) for p in self._mode_switch_base_files if isinstance(p, Path)]
        deduped: List[Path] = []
        seen = set()
        for file_path in file_paths:
            if not isinstance(file_path, Path):
                continue
            key = self._path_key(file_path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(file_path)
        new_keys = [self._path_key(p) for p in deduped]
        changed = (len(previous_keys) != len(new_keys)) or any(
            old_key != new_key for old_key, new_key in zip(previous_keys, new_keys)
        )
        self._mode_switch_base_files = deduped
        if changed:
            self._mode_switch_sorted_cache.clear()

    def _add_mode_switch_base_files(self, file_paths: List[Path]):
        """向模式切换基准列表追加文件（去重）。"""
        if not file_paths:
            return
        existing_keys = {self._path_key(p) for p in self._mode_switch_base_files}
        for file_path in file_paths:
            if not isinstance(file_path, Path):
                continue
            key = self._path_key(file_path)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            self._mode_switch_base_files.append(file_path)
        self._mode_switch_sorted_cache.clear()

    def _remove_mode_switch_base_files(self, file_paths: List[Path]):
        """同步移除基准列表中的文件，保持模式切换一致性。"""
        if not self._mode_switch_base_files or not file_paths:
            return
        remove_keys = {self._path_key(p) for p in file_paths if isinstance(p, Path)}
        if not remove_keys:
            return
        self._mode_switch_base_files = [
            p for p in self._mode_switch_base_files
            if self._path_key(p) not in remove_keys
        ]
        self._mode_switch_sorted_cache.clear()

    def _rename_lazy_state_file(self, old_path: Path, new_path: Path):
        """重命名后同步懒加载内部路径状态（桶/已加载键/LRU/待补图/基准列表）。

        手动重命名只换路径不改集合成员，逐容器原地替换可避免全量重扫。
        """
        if old_path == new_path:
            return
        old_key = self._path_key(old_path)
        new_key = self._path_key(new_path)

        for bucket in self._lazy_bucket_files.values():
            for i, p in enumerate(bucket):
                if self._path_key(p) == old_key:
                    bucket[i] = new_path

        if old_key in self._lazy_loaded_keys:
            self._lazy_loaded_keys.discard(old_key)
            self._lazy_loaded_keys.add(new_key)

        if any(p == old_path for p in self._lazy_thumbnail_lru):
            self._lazy_thumbnail_lru = [
                new_path if p == old_path else p for p in self._lazy_thumbnail_lru
            ]
        if old_key in self._lazy_thumbnail_lru_keys:
            self._lazy_thumbnail_lru_keys.discard(old_key)
            self._lazy_thumbnail_lru_keys.add(new_key)

        if any(p == old_path for p in self._pending_thumb_paths):
            self._pending_thumb_paths = deque(
                new_path if p == old_path else p for p in self._pending_thumb_paths
            )
        if old_key in self._pending_thumb_keys:
            self._pending_thumb_keys.discard(old_key)
            self._pending_thumb_keys.add(new_key)

        for i, p in enumerate(self._mode_switch_base_files):
            if self._path_key(p) == old_key:
                self._mode_switch_base_files[i] = new_path
        self._mode_switch_sorted_cache.clear()

    def _resolve_grouping_mode_for_view(self, view_mode: str) -> str:
        mode = str(view_mode or "default").strip().lower()
        if mode == "date":
            return "date"
        if mode == "random":
            return "flat"
        return "default"

    def _get_cached_sorted_files_for_mode(self, target_mode: str, candidate_base: List[Path]) -> List[Path]:
        """获取模式排序结果（含内存缓存），避免重复全量重排。"""
        mode = str(target_mode or "default").strip().lower()
        if mode not in ("default", "date"):
            return list(candidate_base)

        def _fast_key(path: Path) -> str:
            return os.path.normcase(os.path.normpath(str(path)))

        expected_keys = {_fast_key(p) for p in candidate_base}
        cached = self._mode_switch_sorted_cache.get(mode, [])
        if cached and len(cached) == len(candidate_base):
            cached_keys = {_fast_key(p) for p in cached}
            if cached_keys == expected_keys:
                return list(cached)

        rebuilt = list(candidate_base)
        if mode == "date":
            rebuilt.sort(key=lambda p: (-self._get_grouping_timestamp_fast(p, persist_missing=False), str(p).lower()))
        else:
            rebuilt.sort(key=lambda p: str(p).lower())
        self._mode_switch_sorted_cache[mode] = list(rebuilt)
        return rebuilt

    def _switch_view_mode_without_rescan(self, target_mode: str) -> bool:
        """仅基于当前缓存文件列表切换模式，不触发文件系统重扫。"""
        if not self._source_folders:
            return False

        candidate_base = list(self._mode_switch_base_files) if self._mode_switch_base_files else list(self.file_list)
        candidate_base = [p for p in candidate_base if isinstance(p, Path)]
        if not candidate_base:
            return False

        self._retire_thumbnail_loader(timeout_ms=220)
        if self.duplicate_thread and self.duplicate_thread.isRunning():
            self._wait_thread_responsive(self.duplicate_thread, timeout_ms=160)
        if self.md5_filter_thread and self.md5_filter_thread.isRunning():
            self._wait_thread_responsive(self.md5_filter_thread, timeout_ms=160)

        self._active_scan_id += 1
        scan_id = self._active_scan_id
        self._thumbnail_scan_id = scan_id
        self._auto_tasks_pending_scan_id = scan_id
        self._random_prefilter_scan_ids.clear()
        self._md5_done_before_thumbnail_scan_ids.clear()
        self.file_list_panel.show_loading(True)

        if hasattr(self.file_list_panel, "set_grouping_mode"):
            self.file_list_panel.set_grouping_mode(self._resolve_grouping_mode_for_view(target_mode))

        self._set_pipeline_status_for_fast_mode_switch(target_mode)
        self._mode_switch_base_files = list(candidate_base)
        self._clear_loaded_content_before_mode_switch()
        self._skip_post_thumbnail_tasks_scan_ids = {scan_id}

        if target_mode == "random":
            self.file_list = self._sample_random_files(candidate_base)
            self._md5_filter_enabled_for_active_scan = False
            self._set_pipeline_stage_status("md5", "模式切换已跳过", 100.0)
            self._set_pipeline_stage_status("duplicate", "模式切换已跳过", 100.0)
        else:
            rebuilt = self._get_cached_sorted_files_for_mode(target_mode, candidate_base)
            self.file_list = rebuilt
            self._mode_switch_base_files = list(rebuilt)
            self._md5_filter_enabled_for_active_scan = False
            self._set_pipeline_stage_status("md5", "模式切换已跳过", 100.0)
            self._set_pipeline_stage_status("duplicate", "模式切换已跳过", 100.0)

        if not self.file_list:
            self._set_pipeline_stage_status("thumbnail", "已跳过")
            self._set_pipeline_stage_status("duplicate", "已跳过")
            self.file_list_panel.show_loading(False)
            self.media_viewer.clear()
            self.file_info_panel.update_current_file_path(None)
            self._skip_post_thumbnail_tasks_scan_ids.discard(scan_id)
            return True

        self.current_index = 0
        self.selected_files = []
        self._reset_file_list_panel_state()
        self._prepare_lazy_loading()
        self._thumbnail_pipeline_completed_scan_ids.discard(scan_id)
        self._load_initial_lazy_files()
        QTimer.singleShot(120, lambda: self._display_file(0, force_scroll=True, prefetch_focus=False))
        return True

    def _set_pipeline_status_for_fast_mode_switch(self, target_mode: str):
        """模式切换复用状态栏，不展示“导入中”误导信息。"""
        mode_label = {"default": "默认", "date": "日期", "random": "随机"}.get(target_mode, "默认")
        self._set_pipeline_stage_status("import", f"模式切换({mode_label})", 100.0)
        self._set_pipeline_stage_status("thumbnail", "等待列表刷新")
        self._set_pipeline_stage_status("md5", "模式切换已跳过", 100.0)
        self._set_pipeline_stage_status("duplicate", "模式切换已跳过", 100.0)

    def _sample_random_files(self, file_paths: List[Path]) -> List[Path]:
        """随机抽样（在MD5过滤之后调用）。"""
        n = int(cfg.get_setting("random_sample_size") or 50)
        n = max(n, 1)
        if len(file_paths) <= n:
            return list(file_paths)
        return random.sample(list(file_paths), n)

    def _reset_file_list_panel_state(self):
        """重置左侧列表和选择状态。"""
        self.file_list_panel.clear_files()
        self.current_index = 0
        self.selected_files = []

    def _clear_loaded_content_before_mode_switch(self):
        """模式切换前先清空旧内容，避免旧列表/缩略图残留。"""
        before_rows = 0
        try:
            before_rows = int(self.file_list_panel.file_list.count())
        except Exception:
            before_rows = 0
        self._reset_file_list_panel_state()
        self._reset_lazy_loading_state()
        self._scroll_autoload_armed = False
        try:
            self.media_viewer.clear()
        except Exception:
            pass
        try:
            self.file_info_panel.update_current_file_path(None)
        except Exception:
            pass
        if before_rows > 0:
            logger.log_operation("模式切换清理", f"list -{before_rows}")

    def _populate_file_placeholders(self, file_paths: Optional[List[Path]] = None):
        """按给定列表填充占位项，等待缩略图异步回填。"""
        targets = list(file_paths) if file_paths is not None else list(self.file_list)
        if not targets:
            return
        for file_path in targets:
            self.file_list_panel.add_file(
                file_path,
                None,
                FileStatus.NORMAL,
                refresh=False
            )
        if hasattr(self.file_list_panel, "refresh_after_incremental"):
            self.file_list_panel.refresh_after_incremental(targets)
        else:
            self.file_list_panel.refresh_after_batch()

    def _reset_lazy_loading_state(self):
        """清理增量加载状态。"""
        self._lazy_bucket_files = {}
        self._lazy_bucket_offsets = {}
        self._lazy_bucket_order = []
        self._lazy_loaded_keys.clear()
        self._lazy_active_bucket_key = None
        self._lazy_thumbnail_lru = []
        self._lazy_thumbnail_lru_keys.clear()
        self._pending_thumb_paths.clear()
        self._pending_thumb_keys.clear()
        self._visible_prefetch_last_ts = 0.0
        self._scroll_autoload_armed = False

    def _arm_scroll_autoload(self):
        """由用户滚动行为触发：允许下一次到底部时增量加载一批。"""
        self._scroll_autoload_armed = True

    def _remember_lazy_thumbnail_loaded(self, file_path: Path):
        """记录已加载缩略图的文件，用于后续按批回收。"""
        if not isinstance(file_path, Path):
            return
        file_key = self._path_key(file_path)
        if file_key in self._lazy_thumbnail_lru_keys:
            return
        self._lazy_thumbnail_lru.append(file_path)
        self._lazy_thumbnail_lru_keys.add(file_key)

    def _build_lazy_unload_protect_keys(self) -> set:
        """构建缩略图回收保护窗口（当前聚焦附近不回收）。"""
        protect: set = set()
        if hasattr(self.file_list_panel, "get_visible_file_paths"):
            try:
                visible = self.file_list_panel.get_visible_file_paths(
                    lookaround=self._lazy_unload_visible_lookaround
                )
                protect.update(self._path_key(p) for p in visible if isinstance(p, Path))
            except Exception:
                pass
        if not self.file_list:
            return protect
        center = min(max(self.current_index, 0), len(self.file_list) - 1)
        radius = max(int(self._lazy_unload_focus_radius), 0)
        left = max(0, center - radius)
        right = min(len(self.file_list) - 1, center + radius)
        protect.update(
            self._path_key(self.file_list[idx])
            for idx in range(left, right + 1)
        )
        return protect

    def _enqueue_pending_thumb_paths(self, file_paths: List[Path], priority: bool = False):
        """将待补图文件加入队列，等待当前加载线程空闲后执行。"""
        for file_path in file_paths:
            if not isinstance(file_path, Path):
                continue
            file_key = self._path_key(file_path)
            if file_key in self._pending_thumb_keys:
                if not priority:
                    continue
                # 已在队列中时，提升到队首，保证“当前选中文件”优先回填
                kept: Deque[Path] = deque()
                while self._pending_thumb_paths:
                    candidate = self._pending_thumb_paths.popleft()
                    if self._path_key(candidate) == file_key:
                        continue
                    kept.append(candidate)
                self._pending_thumb_paths = kept
                self._pending_thumb_paths.appendleft(file_path)
                continue

            if len(self._pending_thumb_paths) >= self._pending_thumb_max:
                if not priority:
                    continue
                # 高优先级任务挤掉队尾低优先级任务
                while len(self._pending_thumb_paths) >= self._pending_thumb_max:
                    dropped = self._pending_thumb_paths.pop()
                    self._pending_thumb_keys.discard(self._path_key(dropped))

            self._pending_thumb_keys.add(file_key)
            if priority:
                self._pending_thumb_paths.appendleft(file_path)
            else:
                self._pending_thumb_paths.append(file_path)

    def _take_pending_thumb_batch(self, limit: int) -> List[Path]:
        """从待补图队列取一批可用文件。

        遍历整个队列；已加载（has_thumb=True）的从队列移除；
        不在 UI 中（has_file=False）的保留在队列尾部，等 caller populate
        后再处理；不在 file_list 的移除。
        """
        target = max(1, int(limit))
        results: List[Path] = []
        if not self._pending_thumb_paths:
            return results

        new_pending: Deque[Path] = deque()
        for file_path in self._pending_thumb_paths:
            if len(results) >= target:
                # 已取够，剩余全部保留在队列
                new_pending.append(file_path)
                continue
            file_key = self._path_key(file_path)
            if file_path not in self.file_list:
                self._pending_thumb_keys.discard(file_key)
                continue
            has_file = bool(
                hasattr(self.file_list_panel, "has_file")
                and self.file_list_panel.has_file(file_path)
            )
            if not has_file:
                # 文件不在 UI 中，保留在队列尾部（等 caller populate 后再处理）
                new_pending.append(file_path)
                continue
            has_thumb = bool(
                hasattr(self.file_list_panel, "has_thumbnail")
                and self.file_list_panel.has_thumbnail(file_path)
            )
            if has_thumb:
                # 已加载，从队列移除
                self._pending_thumb_keys.discard(file_key)
                continue
            # 需要加载
            results.append(file_path)
            self._pending_thumb_keys.discard(file_key)

        self._pending_thumb_paths = new_pending
        return results

    def _auto_load_more_if_idle(self):
        """缩略图线程空闲后主动增量加载未进入 UI 的文件。

        解决懒加载下"不滚动就不加载，大量文件停留在 file_list"的问题：
        只要线程空闲、没有排队任务、且还有未进入 UI 的文件，就主动从桶
        中取下一批加载；忙时通过 _schedule_auto_load_more 延迟重试。
        """
        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            self._schedule_auto_load_more()
            return
        if self.md5_filter_thread and self.md5_filter_thread.isRunning():
            self._schedule_auto_load_more()
            return
        if self.duplicate_thread and self.duplicate_thread.isRunning():
            self._schedule_auto_load_more()
            return
        if not self.file_list or not self._lazy_bucket_order:
            return
        # 模式切换跳过阶段不主动增量
        if self._thumbnail_scan_id in self._skip_post_thumbnail_tasks_scan_ids:
            return
        # 有排队任务时，等 flush 完再增量
        if self._pending_thumb_paths:
            self._schedule_auto_load_more()
            return
        # 全部已加载则停止
        if len(self._lazy_loaded_keys) >= len(self.file_list):
            return
        # 主动增量一批
        self._load_more_lazy_files(from_scroll=False)

    def _schedule_auto_load_more(self):
        """稍后再尝试主动增量（忙时延迟重试，避免高频空转）"""
        if self._auto_load_more_scheduled:
            return
        self._auto_load_more_scheduled = True
        QTimer.singleShot(180, self._on_auto_load_more_tick)

    def _on_auto_load_more_tick(self):
        """定时重试主动增量"""
        self._auto_load_more_scheduled = False
        self._auto_load_more_if_idle()

    def _prefetch_visible_missing_thumbnails(self, force: bool = False):
        """优先补齐可视区及前后缓冲的缩略图。"""
        if not self.file_list:
            return
        now = time.monotonic()
        if (not force) and (now - self._visible_prefetch_last_ts) < 0.18:
            return
        self._visible_prefetch_last_ts = now

        if not hasattr(self.file_list_panel, "get_visible_file_paths"):
            return
        visible_paths = self.file_list_panel.get_visible_file_paths(lookaround=20)
        if not visible_paths:
            return

        missing: List[Path] = []
        for file_path in visible_paths:
            has_file = bool(
                hasattr(self.file_list_panel, "has_file")
                and self.file_list_panel.has_file(file_path)
            )
            if not has_file:
                continue
            has_thumb = bool(
                hasattr(self.file_list_panel, "has_thumbnail")
                and self.file_list_panel.has_thumbnail(file_path)
            )
            if has_thumb:
                continue
            missing.append(file_path)
            if len(missing) >= self._lazy_step_batch_size:
                break

        if not missing:
            return

        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            self._enqueue_pending_thumb_paths(missing)
            return
        self._load_thumbnails(self._active_scan_id, file_paths=missing)

    def _prefetch_focus_window_thumbnails(self, center_index: int, radius: int = 30):
        """聚焦到某张文件时，优先补齐其上下窗口（默认±30）。"""
        if not self.file_list:
            return
        if center_index < 0 or center_index >= len(self.file_list):
            return

        span = max(int(radius), 0)
        left = max(0, center_index - span)
        right = min(len(self.file_list) - 1, center_index + span)
        window_paths = list(self.file_list[left:right + 1])
        if not window_paths:
            return

        missing_entries: List[Path] = []
        missing_thumbs: List[Path] = []
        for file_path in window_paths:
            has_file = bool(
                hasattr(self.file_list_panel, "has_file")
                and self.file_list_panel.has_file(file_path)
            )
            if not has_file:
                missing_entries.append(file_path)
                continue
            has_thumb = bool(
                hasattr(self.file_list_panel, "has_thumbnail")
                and self.file_list_panel.has_thumbnail(file_path)
            )
            if not has_thumb:
                missing_thumbs.append(file_path)

        if missing_entries:
            self._populate_file_placeholders(missing_entries)
            missing_thumbs.extend(missing_entries)

        if not missing_thumbs:
            return

        # 去重保序
        deduped: List[Path] = []
        seen = set()
        for file_path in missing_thumbs:
            key = self._path_key(file_path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(file_path)

        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            self._enqueue_pending_thumb_paths(deduped)
            return
        self._load_thumbnails(self._active_scan_id, file_paths=deduped)

    def _flush_pending_thumb_prefetch(self):
        """当缩略图线程空闲时，继续处理可视区待补图队列。"""
        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            return
        if not self._pending_thumb_paths:
            return
        batch = self._take_pending_thumb_batch(self._lazy_step_batch_size)
        if not batch:
            return
        self._load_thumbnails(self._active_scan_id, file_paths=batch)

    def _unload_old_lazy_thumbnails(self, count: Optional[int] = None) -> int:
        """每次增量后回收最旧的缩略图，降低内存占用。"""
        target = max(0, int(count if count is not None else self._lazy_unload_batch_size))
        if target <= 0:
            return 0
        if not self._lazy_thumbnail_lru:
            return 0
        if not hasattr(self.file_list_panel, "release_thumbnail"):
            return 0
        resident_limit = max(int(self._lazy_thumbnail_resident_limit), 0)
        overflow = len(self._lazy_thumbnail_lru) - resident_limit
        if overflow <= 0:
            return 0
        target = min(target, overflow)
        if target <= 0:
            return 0

        protect_keys = self._build_lazy_unload_protect_keys()
        released = 0
        attempts = len(self._lazy_thumbnail_lru)

        while released < target and attempts > 0 and self._lazy_thumbnail_lru:
            candidate = self._lazy_thumbnail_lru.pop(0)
            attempts -= 1
            candidate_key = self._path_key(candidate)

            # 当前窗口附近文件保留，放回队尾等待后续回收
            if candidate_key in protect_keys:
                self._lazy_thumbnail_lru.append(candidate)
                continue

            has_file = bool(
                hasattr(self.file_list_panel, "has_file")
                and self.file_list_panel.has_file(candidate)
            )
            if not has_file:
                self._lazy_thumbnail_lru_keys.discard(candidate_key)
                continue

            has_thumb = bool(
                hasattr(self.file_list_panel, "has_thumbnail")
                and self.file_list_panel.has_thumbnail(candidate)
            )
            if not has_thumb:
                self._lazy_thumbnail_lru_keys.discard(candidate_key)
                continue

            self.file_list_panel.release_thumbnail(candidate)
            self._lazy_thumbnail_lru_keys.discard(candidate_key)
            released += 1
        return released

    def _resolve_source_folder_for_file(self, file_path: Path) -> Optional[Path]:
        """按最长匹配解析文件归属源目录。"""
        if not self._source_folders:
            return None
        candidates = [
            folder for folder in self._source_folders
            if self._is_path_in_folder(file_path, folder)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: len(self._path_key(p)))

    def _prepare_lazy_loading(self):
        """按当前 file_list 构建增量加载桶。"""
        self._reset_lazy_loading_state()
        if not self.file_list:
            return

        mode = str(getattr(self, "_view_mode", "default") or "default").strip().lower()
        if mode == "default" and self._source_folders:
            for file_path in self.file_list:
                src = self._resolve_source_folder_for_file(file_path)
                key = self._path_key(src) if src else "__all__"
                self._lazy_bucket_files.setdefault(key, []).append(file_path)
            for folder in self._source_folders:
                key = self._path_key(folder)
                if key in self._lazy_bucket_files:
                    self._lazy_bucket_order.append(key)
        else:
            self._lazy_bucket_files["__all__"] = list(self.file_list)
            self._lazy_bucket_order = ["__all__"]

        if not self._lazy_bucket_order:
            self._lazy_bucket_order = list(self._lazy_bucket_files.keys())

        for key in self._lazy_bucket_order:
            self._lazy_bucket_offsets[key] = 0
        if self._lazy_bucket_order:
            self._lazy_active_bucket_key = self._lazy_bucket_order[0]

    def _take_lazy_files_from_bucket(self, bucket_key: str, count: int) -> List[Path]:
        """从指定桶按顺序取下一批文件。"""
        if bucket_key not in self._lazy_bucket_files:
            return []
        bucket = self._lazy_bucket_files[bucket_key]
        offset = int(self._lazy_bucket_offsets.get(bucket_key, 0))
        max_count = max(int(count), 0)
        if max_count <= 0:
            return []

        result: List[Path] = []
        while offset < len(bucket) and len(result) < max_count:
            file_path = bucket[offset]
            offset += 1
            file_key = self._path_key(file_path)
            if file_key in self._lazy_loaded_keys:
                continue
            self._lazy_loaded_keys.add(file_key)
            result.append(file_path)
        self._lazy_bucket_offsets[bucket_key] = offset
        return result

    def _load_initial_lazy_files(self) -> List[Path]:
        """首屏加载：先让每个目录至少出现 1 条，再补足首批数量。"""
        if not self.file_list:
            return []
        if not self._lazy_bucket_order:
            self._prepare_lazy_loading()
            if not self._lazy_bucket_order:
                return []

        total_limit = max(1, int(self._lazy_initial_batch_size))
        loaded_files: List[Path] = []

        # 第一轮：每个桶先取 1 条，确保目录头可见，点击可触发继续加载
        for key in self._lazy_bucket_order:
            if len(loaded_files) >= total_limit:
                break
            chunk = self._take_lazy_files_from_bucket(key, 1)
            if chunk:
                loaded_files.extend(chunk)

        # 第二轮：轮询补足到首批上限
        while len(loaded_files) < total_limit:
            added = False
            for key in self._lazy_bucket_order:
                if len(loaded_files) >= total_limit:
                    break
                chunk = self._take_lazy_files_from_bucket(key, 1)
                if chunk:
                    loaded_files.extend(chunk)
                    self._lazy_active_bucket_key = key
                    added = True
            if not added:
                break

        if not loaded_files:
            return []
        self._populate_file_placeholders(loaded_files)
        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            self._enqueue_pending_thumb_paths(loaded_files)
        else:
            self._load_thumbnails(self._active_scan_id, file_paths=loaded_files)
        return loaded_files

    def _load_more_lazy_files(
        self,
        preferred_bucket: Optional[str] = None,
        batch_size: Optional[int] = None,
        from_scroll: bool = False,
    ) -> List[Path]:
        """加载下一批占位项+缩略图。"""
        if not self.file_list:
            return []

        if not self._lazy_bucket_order:
            self._prepare_lazy_loading()
            if not self._lazy_bucket_order:
                return []

        count = int(batch_size or self._lazy_step_batch_size)
        count = max(1, count)

        candidates: List[str] = []
        if preferred_bucket:
            candidates.append(preferred_bucket)
        if self._lazy_active_bucket_key:
            candidates.append(self._lazy_active_bucket_key)
        candidates.extend(self._lazy_bucket_order)

        unique_candidates: List[str] = []
        seen = set()
        for key in candidates:
            if key and key not in seen:
                seen.add(key)
                unique_candidates.append(key)

        loaded_files: List[Path] = []
        for key in unique_candidates:
            loaded_files = self._take_lazy_files_from_bucket(key, count)
            if loaded_files:
                self._lazy_active_bucket_key = key
                break

        if not loaded_files:
            return []

        self._populate_file_placeholders(loaded_files)
        queued = 0
        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            before = len(self._pending_thumb_paths)
            self._enqueue_pending_thumb_paths(loaded_files)
            queued = max(0, len(self._pending_thumb_paths) - before)
        else:
            self._load_thumbnails(self._active_scan_id, file_paths=loaded_files)
        released = self._unload_old_lazy_thumbnails(self._lazy_unload_batch_size)

        trigger = "scroll" if from_scroll else ("folder" if preferred_bucket else "lazy")
        logger.log_operation(
            "增量加载",
            f"{trigger} +{len(loaded_files)} / -{released} / q+{queued}"
        )
        return loaded_files

    def _on_file_list_scroll_changed(self, _value: int):
        """滚动到底部时触发增量加载。"""
        if not self.file_list:
            return
        try:
            bar = self.file_list_panel.file_list.verticalScrollBar()
        except Exception:
            return
        if bar is None:
            return
        # 仅在用户真实滚动过程中触发可视区补图，避免程序内部刷新引起连续后台预取
        if self._scroll_autoload_armed or bar.isSliderDown():
            self._prefetch_visible_missing_thumbnails()
        if not self._scroll_autoload_armed:
            return
        if bar.maximum() <= 0:
            return
        near_bottom = (bar.maximum() - bar.value()) <= 40
        if near_bottom:
            # 一次用户滚动动作最多触发一批，避免程序内部刷新导致“自动全量加载”。
            self._scroll_autoload_armed = False
            self._load_more_lazy_files(from_scroll=True)

    def _on_file_list_folder_header_clicked(self, folder_key: str):
        """点击目录头时，优先加载该目录所属源桶。"""
        if not folder_key:
            return
        if (
            hasattr(self.file_list_panel, "is_folder_collapsed")
            and self.file_list_panel.is_folder_collapsed(folder_key)
        ):
            return
        bucket_key = folder_key
        if hasattr(self.file_list_panel, "get_root_folder_key"):
            root_key = self.file_list_panel.get_root_folder_key(folder_key)
            if root_key:
                bucket_key = root_key
        self._lazy_active_bucket_key = bucket_key
        current_scan_id = self._active_scan_id

        def _deferred_folder_increment():
            if current_scan_id != self._active_scan_id:
                return
            self._load_more_lazy_files(
                preferred_bucket=bucket_key,
                batch_size=self._lazy_step_batch_size,
            )

        QTimer.singleShot(0, _deferred_folder_increment)

    def _restore_thumbnail_from_cache_for_file(self, file_path: Path) -> bool:
        """优先从磁盘缩略图缓存快速恢复单文件封面。"""
        if self.cache_manager is None:
            return False
        try:
            cached_thumbnail = self.cache_manager.load_thumbnail(file_path)
        except Exception:
            return False
        if cached_thumbnail is None or cached_thumbnail.isNull():
            return False

        status = FileStatus.NORMAL
        if file_path in self.file_scanner.corrupted_files:
            status = FileStatus.CORRUPTED
        self.file_list_panel.add_file(file_path, cached_thumbnail, status, refresh=False)
        self._remember_lazy_thumbnail_loaded(file_path)
        logger.log_operation("焦点补图缓存命中", file_path.name)
        return True

    def _ensure_file_loaded_for_display(self, file_path: Path) -> bool:
        """确保目标文件在左侧列表中可见（按需增量补载）。"""
        requested = False
        has_file = bool(
            hasattr(self.file_list_panel, "has_file")
            and self.file_list_panel.has_file(file_path)
        )
        if not has_file:
            self._populate_file_placeholders([file_path])
            if self._restore_thumbnail_from_cache_for_file(file_path):
                return True
            if self.thumbnail_loader and self.thumbnail_loader.isRunning():
                self._enqueue_pending_thumb_paths([file_path], priority=True)
                logger.log_operation("焦点补图排队", file_path.name)
            else:
                self._load_thumbnails(self._active_scan_id, file_paths=[file_path])
            return True

        has_thumb = bool(
            hasattr(self.file_list_panel, "has_thumbnail")
            and self.file_list_panel.has_thumbnail(file_path)
        )
        if has_thumb:
            return False
        if self._restore_thumbnail_from_cache_for_file(file_path):
            return True
        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            self._enqueue_pending_thumb_paths([file_path], priority=True)
            logger.log_operation("焦点补图排队", file_path.name)
            return True
        self._load_thumbnails(self._active_scan_id, file_paths=[file_path])
        requested = True
        return requested

    def _remember_classified_target_folder(self, target_folder: Path):
        """记录已用于分类的目标目录（用于重启后MD5过滤）"""
        if not self.folder_manager or not self.folder_manager._work_dir:
            return

        work_dir = self.folder_manager.work_dir
        try:
            resolved = target_folder.resolve()
        except Exception:
            resolved = target_folder.absolute()

        if not self._is_path_in_folder(resolved, work_dir):
            return
        if not resolved.exists() or not resolved.is_dir():
            return

        existing = self._normalize_folder_paths(cfg.get_list("classified_target_folders"))
        existing_keys = {self._path_key(Path(p)) for p in existing}
        resolved_key = self._path_key(resolved)
        if resolved_key in existing_keys:
            return

        existing.append(str(resolved))
        cfg.set_list("classified_target_folders", existing)

    def _build_md5_classified_folders(self) -> List[Path]:
        """构建MD5过滤的分类库目录列表"""
        if not self.folder_manager or not self.folder_manager._work_dir:
            return []

        work_dir = self.folder_manager.work_dir
        candidates: List[Path] = []

        # 内置分类目录始终作为分类库
        for folder_name in BUILTIN_FOLDERS.values():
            folder_path = work_dir / folder_name
            if folder_path.exists() and folder_path.is_dir():
                candidates.append(folder_path)

        # 叠加历史使用过的分类目标目录
        for raw_path in self._normalize_folder_paths(cfg.get_list("classified_target_folders")):
            folder_path = Path(raw_path)
            if self._is_path_in_folder(folder_path, work_dir):
                candidates.append(folder_path)

        deduped: List[Path] = []
        seen = set()
        for path in candidates:
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    def _load_thumbnails(self, scan_id: int, file_paths: Optional[List[Path]] = None):
        """异步加载缩略图（支持局部增量加载）。"""
        targets = list(file_paths) if file_paths is not None else list(self.file_list)
        if not targets:
            return
        if self.thumbnail_loader and self.thumbnail_loader.isRunning():
            return
        if self.thumbnail_loader is not None:
            # 清理已结束线程引用，避免 sender 校验误判
            self._disconnect_thumbnail_loader_signals(self.thumbnail_loader)
            self.thumbnail_loader = None

        self._set_pipeline_stage_status("thumbnail", "缩略图加载中", 0.0)
        self._thumbnail_scan_id = scan_id
        self.thumbnail_loader = ThumbnailLoader(targets, cache_manager=self.cache_manager)
        self.thumbnail_loader._scan_id = scan_id
        self.thumbnail_loader.thumbnail_loaded.connect(self._on_thumbnail_loaded)
        self.thumbnail_loader.load_failed.connect(self._on_thumbnail_failed)
        self.thumbnail_loader.progress_changed.connect(self._on_thumbnail_progress)
        self.thumbnail_loader.finished.connect(self._on_thumbnail_loading_finished)
        self.thumbnail_loader.start()

    def _on_thumbnail_loaded(self, file_path: Path, thumbnail: QPixmap):
        """缩略图加载完成"""
        if not self._is_active_thumbnail_loader_signal():
            return
        if file_path not in self.file_list:
            return

        # 检查文件状态
        status = FileStatus.NORMAL
        if file_path in self.file_scanner.corrupted_files:
            status = FileStatus.CORRUPTED

        self.file_list_panel.add_file(file_path, thumbnail, status, refresh=False)
        self._remember_lazy_thumbnail_loaded(file_path)
        if self.file_list and 0 <= self.current_index < len(self.file_list):
            current_file = self.file_list[self.current_index]
            if current_file == file_path and hasattr(self.file_list_panel, "expand_related_folder"):
                self.file_list_panel.expand_related_folder(file_path)
        self.file_list_panel.show_loading(False)

    def _on_undo_thumbnail_loaded(self, file_path: Path, thumbnail: QPixmap):
        """撤回操作的缩略图加载完成"""
        if file_path not in self.file_list:
            return
        self.file_list_panel.add_file(file_path, thumbnail, FileStatus.NORMAL)
        self._remember_lazy_thumbnail_loaded(file_path)

    def _on_thumbnail_failed(self, file_path: Path):
        """缩略图加载失败"""
        if not self._is_active_thumbnail_loader_signal():
            return
        logger.error(f"缩略图加载失败: {file_path}")

    def _on_thumbnail_progress(self, current: int, total: int):
        """缩略图加载进度"""
        if not self._is_active_thumbnail_loader_signal():
            return
        if total <= 0:
            self._set_pipeline_stage_status("thumbnail", "缩略图加载完成", 100.0)
            return
        percent = current * 100.0 / total
        self._set_pipeline_stage_status("thumbnail", f"缩略图加载中({current}/{total})", percent)

    def _on_thumbnail_loading_finished(self):
        """缩略图加载完成后再后台计算MD5排除"""
        if not self._is_active_thumbnail_loader_signal():
            return
        finished_loader = self.thumbnail_loader
        if finished_loader is not None:
            self._disconnect_thumbnail_loader_signals(finished_loader)
            self.thumbnail_loader = None
        # 主动增量加载未进入 UI 的文件（解决懒加载"不滚动就不加载"的问题）
        self._auto_load_more_if_idle()
        self._set_pipeline_stage_status("thumbnail", "缩略图加载完成", 100.0)
        self.file_list_panel.show_loading(False)
        released = self._unload_old_lazy_thumbnails(self._lazy_unload_batch_size)
        if released > 0:
            logger.log_operation("减量回收", f"thumb -{released}")

        if self._thumbnail_scan_id in self._skip_post_thumbnail_tasks_scan_ids:
            self._set_pipeline_stage_status("md5", "模式切换已跳过", 100.0)
            self._set_pipeline_stage_status("duplicate", "模式切换已跳过", 100.0)
            self._flush_pending_thumb_prefetch()
            self._prefetch_visible_missing_thumbnails(force=False)
            return

        # 增量加载场景：仅首批缩略图完成后触发一次后续流水线
        if self._thumbnail_scan_id in self._thumbnail_pipeline_completed_scan_ids:
            self._flush_pending_thumb_prefetch()
            return
        self._thumbnail_pipeline_completed_scan_ids.add(self._thumbnail_scan_id)

        # 随机模式预过滤路径：MD5已在缩略图前完成，这里直接进入后续任务
        if self._thumbnail_scan_id in self._md5_done_before_thumbnail_scan_ids:
            self._md5_done_before_thumbnail_scan_ids.discard(self._thumbnail_scan_id)
            self._start_auto_tasks_for_scan(self._thumbnail_scan_id)
            self._flush_pending_thumb_prefetch()
            self._prefetch_visible_missing_thumbnails(force=False)
            return

        if not self._md5_filter_enabled_for_active_scan:
            if cfg.get_bool("filter_screened_files", True):
                self._set_pipeline_stage_status("md5", "复检模式已跳过", 100.0)
            else:
                self._set_pipeline_stage_status("md5", "过滤已关闭", 100.0)
            self._start_auto_tasks_for_scan(self._thumbnail_scan_id)
            self._flush_pending_thumb_prefetch()
            self._prefetch_visible_missing_thumbnails(force=False)
            return
        self._start_md5_filter(self._thumbnail_scan_id)
        self._flush_pending_thumb_prefetch()
        self._prefetch_visible_missing_thumbnails(force=False)
