"""图片筛选分类页 · 面板组件（源项目 ui 层按工具箱 QSS 体系重写）。

- FileListPanel  左栏：缩略图卡片流 + 目录/日期分组头（QListWidget IconMode + setItemWidget）
- MediaViewer    中栏：图片缩放预览（QGraphicsView 0.25%~2000%；视频播放为阶段四）
- FileInfoPanel  右栏：媒体信息展示（Exif 优先级排序；压缩/重命名标签页为阶段三/四）

与源版本差异：
- qfluentwidgets（CardWidget/BodyLabel/IndeterminateProgressRing）→ QWidget+QSS / QLabel / QProgressBar busy
- 颜色一律浅色规范 hex，经 theme 自动重映射补丁 / adapt() 适配深色（无 isDarkTheme 分支）
- InfoBar 提示 → copy_info_requested 信号由页面层转 app.toast
"""

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image as PILImage
from PySide6.QtCore import Qt, QPoint, QSize, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPixmap, QImage
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QStandardPaths

from gui_qt.common import icon_btn_style
from gui_qt import theme

from image_sorter import settings as cfg
from image_sorter.constants import (
    FileStatus,
    STATUS_COLORS,
    SUPPORTED_VIDEO_FORMATS,
)
from image_sorter.core.media_info_extractor import MediaInfoExtractor
from image_sorter._log import logger


# ==================== 左栏：文件列表（卡片布局 + 多级分组） ====================

class FileListPanel(QWidget):
    """文件列表面板（卡片布局 + 多级分组）"""

    folders_dropped = Signal(list)  # 文件夹拖入信号
    file_selected = Signal(Path)  # 文件选中信号
    files_batch_selected = Signal(list)  # 批量文件选中信号
    source_folder_remove_requested = Signal(str)  # 请求移除源文件夹
    folder_header_clicked = Signal(str)  # 目录头点击信号（folder_key）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_items: Dict[Path, QListWidgetItem] = {}
        self._file_name_labels: Dict[Path, QLabel] = {}
        self._file_preview_labels: Dict[Path, QLabel] = {}
        self._file_preview_cards: Dict[Path, QFrame] = {}
        self._file_status: Dict[Path, FileStatus] = {}
        self._file_thumbnail_loaded: Dict[Path, bool] = {}

        # 多级目录头部管理
        self._folder_items: Dict[str, QListWidgetItem] = {}
        self._folder_title_labels: Dict[str, QLabel] = {}
        self._folder_file_items: Dict[str, List[QListWidgetItem]] = {}  # 仅直属文件项
        self._folder_collapsed: Dict[str, bool] = {}
        self._folder_force_hidden: set = set()
        self._folder_parent: Dict[str, Optional[str]] = {}
        self._folder_children: Dict[str, List[str]] = {}
        self._folder_depth: Dict[str, int] = {}
        self._folder_display_name: Dict[str, str] = {}
        self._folder_total_file_count: Dict[str, int] = {}

        self._source_folders: List[Path] = []
        self._folder_labels: Dict[str, str] = {}  # 根目录展示名
        self._source_folder_map: Dict[str, Path] = {}  # 根目录key -> 源目录
        self._folder_path_map: Dict[str, object] = {}  # 目录key -> 实际路径（日期分组为 None）

        # 分组模式：default(按实际目录层级) / date(按年月日虚拟分组) / flat(不分组)
        self._grouping_mode: str = "default"
        self._grouping_datetime_resolver = None
        self._layout_metrics = {
            "item_width": 200,
            "item_height": 236,
            "preview_card": 180,
            "preview": 176,
            "header_width": 408,
            "text_width": 176,
        }

        self._init_ui()

    def set_grouping_mode(self, mode: str):
        """设置分组模式（default/date/flat）。仅影响后续 add_file() 的分组。"""
        normalized = str(mode or "default").strip().lower()
        self._grouping_mode = (
            normalized if normalized in ("default", "date", "flat") else "default"
        )

    def set_grouping_datetime_resolver(self, resolver):
        """设置日期分组时间解析器（复用外部缓存，避免重复EXIF读取）。"""
        self._grouping_datetime_resolver = resolver

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setAcceptDrops(True)

        title_label = QLabel("文件列表", self)
        title_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #2f3440; padding: 2px;")
        layout.addWidget(title_label)

        self.file_list = QListWidget(self)
        self.file_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.file_list.setFlow(QListWidget.Flow.LeftToRight)
        self.file_list.setWrapping(True)
        self.file_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.file_list.setMovement(QListWidget.Movement.Static)
        self.file_list.setSpacing(4)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.file_list.setMinimumWidth(0)
        self.file_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.file_list.setAcceptDrops(False)
        self.file_list.setDragEnabled(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.file_list.setStyleSheet(
            """
            QListWidget {
                background-color: #ffffff;
                border: 1px solid #d0d6df;
                border-radius: 6px;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 2px;
            }
            QListWidget::item:selected {
                background: #d8e7f6;
                border-radius: 8px;
            }
            """
        )

        self.file_list.itemClicked.connect(self._on_item_clicked)
        self.file_list.itemDoubleClicked.connect(self._on_item_clicked)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_context_menu)

        # Override startDrag to provide file URIs for external drag targets
        _file_list_ref = self.file_list

        def _custom_start_drag(supportedActions):
            from PySide6.QtCore import QMimeData, QUrl
            from PySide6.QtGui import QDrag
            selected = _file_list_ref.selectedItems()
            urls = []
            for item in selected:
                fp = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(fp, Path) and item.data(Qt.ItemDataRole.UserRole + 1) == "file":
                    urls.append(QUrl.fromLocalFile(str(fp)))
            if not urls:
                return
            mime = QMimeData()
            mime.setUrls(urls)
            drag = QDrag(_file_list_ref)
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction)

        self.file_list.startDrag = _custom_start_drag
        layout.addWidget(self.file_list)

        self.loading_widget = QWidget(self)
        loading_layout = QVBoxLayout(self.loading_widget)
        loading_layout.setContentsMargins(0, 20, 0, 20)
        self.loading_bar = QProgressBar(self.loading_widget)
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedWidth(180)
        self.loading_label = QLabel("加载中...", self.loading_widget)
        self.loading_label.setStyleSheet("color: #5a6474;")
        loading_layout.addWidget(self.loading_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self.loading_label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.loading_widget.hide()
        layout.addWidget(self.loading_widget)

        QTimer.singleShot(0, lambda: self._apply_responsive_layout(force=True))

    @staticmethod
    def _build_preview_card_stylesheet() -> str:
        return """
            QFrame {
                background-color: #ffffff;
                border: 2px solid #d0d6df;
                border-radius: 8px;
            }
            """

    def _compute_layout_metrics(self) -> Dict[str, int]:
        """基于当前可视区宽度计算卡片尺寸，避免固定宽度导致溢出留白。"""
        viewport_width = max(self.file_list.viewport().width(), self.file_list.width(), 1)
        available_width = max(viewport_width - 12, 180)
        spacing = max(self.file_list.spacing(), 4)

        # 文件卡片始终按两列计算，避免小屏下退化成单列造成空间浪费
        two_col_width = int((available_width - spacing * 3) / 2)
        item_width = min(200, max(92, two_col_width))
        preview_card = max(84, item_width - 20)
        preview = max(80, preview_card - 4)
        item_height = preview_card + 56
        header_width = max(120, viewport_width - 16)

        return {
            "item_width": int(item_width),
            "item_height": int(item_height),
            "preview_card": int(preview_card),
            "preview": int(preview),
            "header_width": int(header_width),
            "text_width": int(preview),
        }

    def _apply_responsive_layout(self, force: bool = False):
        """同步头部与卡片尺寸到当前视口宽度。"""
        if not hasattr(self, "file_list"):
            return

        metrics = self._compute_layout_metrics()
        if (not force) and metrics == self._layout_metrics:
            return
        self._layout_metrics = metrics

        header_size = QSize(metrics["header_width"], 34)
        for header_item in self._folder_items.values():
            header_item.setSizeHint(header_size)

        for file_path, file_item in self._file_items.items():
            file_item.setSizeHint(QSize(metrics["item_width"], metrics["item_height"]))

            item_widget = self.file_list.itemWidget(file_item)
            if item_widget is not None:
                item_widget.setFixedWidth(metrics["item_width"])

            preview_card = self._file_preview_cards.get(file_path)
            if preview_card is not None:
                preview_card.setFixedSize(metrics["preview_card"], metrics["preview_card"])

            preview_label = self._file_preview_labels.get(file_path)
            if preview_label is not None:
                preview_label.setFixedSize(metrics["preview"], metrics["preview"])
                current_pixmap = preview_label.pixmap()
                if current_pixmap is not None and not current_pixmap.isNull():
                    preview_label.setPixmap(self._to_square_pixmap(current_pixmap))

            self._update_item_appearance(file_item, self._file_status.get(file_path, FileStatus.NORMAL))

        self.file_list.doItemsLayout()
        self.file_list.viewport().update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        folder_paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                folder_paths.append(str(path))

        if folder_paths:
            self.folders_dropped.emit(folder_paths)
            logger.log_operation("文件夹拖入", f"拖入 {len(folder_paths)} 个文件夹")

    def set_source_folders(self, folder_paths: List[str]):
        """设置当前批次的源文件夹（用于分级显示）"""
        unique_folders: List[Path] = []
        seen = set()
        for folder_path in folder_paths:
            folder = Path(folder_path)
            key = self._path_key(folder)
            if key in seen:
                continue
            seen.add(key)
            unique_folders.append(folder)

        self._source_folders = unique_folders
        self._source_folder_map = {self._path_key(folder): folder for folder in unique_folders}
        self._build_folder_labels()

        # 若列表已存在根头部，更新显示名
        for folder_key, depth in self._folder_depth.items():
            if depth == 0:
                self._folder_display_name[folder_key] = self._folder_labels.get(
                    folder_key, self._folder_display_name.get(folder_key, "文件夹")
                )
                self._update_folder_title(folder_key)

    def add_file(
        self,
        file_path: Path,
        thumbnail: Optional[QPixmap],
        status: FileStatus = FileStatus.NORMAL,
        index_in_folder: Optional[int] = None,
        refresh: bool = True,
    ):
        """添加文件卡片"""
        if file_path in self._file_items:
            preview_label = self._file_preview_labels.get(file_path)
            if preview_label and thumbnail:
                preview_label.setPixmap(self._to_square_pixmap(thumbnail))
                self._file_thumbnail_loaded[file_path] = True
            self.update_file_status(file_path, status)
            return

        folder_key = self.ensure_folder_chain_for_file(file_path)
        insert_row = self._find_insert_row_for_file(folder_key, index_in_folder)

        metrics = self._layout_metrics

        file_item = QListWidgetItem()
        file_item.setData(Qt.ItemDataRole.UserRole, file_path)
        file_item.setData(Qt.ItemDataRole.UserRole + 1, "file")
        file_item.setData(Qt.ItemDataRole.UserRole + 2, folder_key)
        file_item.setSizeHint(QSize(metrics["item_width"], metrics["item_height"]))
        self.file_list.insertItem(insert_row, file_item)

        item_widget = QWidget(self.file_list)
        item_widget.setFixedWidth(metrics["item_width"])
        item_widget.setStyleSheet("background: transparent; border: none;")
        item_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        item_layout = QVBoxLayout(item_widget)
        item_layout.setContentsMargins(6, 6, 6, 6)
        item_layout.setSpacing(6)

        preview_card = QFrame(item_widget)
        preview_card.setFixedSize(metrics["preview_card"], metrics["preview_card"])
        preview_card.setStyleSheet(self._build_preview_card_stylesheet())
        preview_card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        preview_label = QLabel(preview_card)
        preview_label.setFixedSize(metrics["preview"], metrics["preview"])
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setScaledContents(True)
        # 空位底色即占位（浅灰 #eceff3，theme 自动映射深色）
        preview_label.setStyleSheet("background: #eceff3; border: none; border-radius: 6px;")
        preview_label.setPixmap(self._to_square_pixmap(thumbnail))
        preview_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        preview_layout.addWidget(preview_label, alignment=Qt.AlignmentFlag.AlignCenter)
        item_layout.addWidget(preview_card, alignment=Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel(file_path.name, item_widget)
        name_label.setWordWrap(False)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setFixedHeight(20)
        name_label.setStyleSheet("font-size: 12px;")
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        item_layout.addWidget(name_label)

        self.file_list.setItemWidget(file_item, item_widget)

        self._file_items[file_path] = file_item
        self._file_name_labels[file_path] = name_label
        self._file_preview_labels[file_path] = preview_label
        self._file_preview_cards[file_path] = preview_card
        self._file_status[file_path] = status
        self._file_thumbnail_loaded[file_path] = bool(thumbnail and (not thumbnail.isNull()))

        if index_in_folder is not None:
            self._folder_file_items[folder_key].insert(index_in_folder, file_item)
        else:
            self._folder_file_items[folder_key].append(file_item)

        # 级联更新文件计数
        current = folder_key
        while current is not None:
            self._folder_total_file_count[current] = self._folder_total_file_count.get(current, 0) + 1
            current = self._folder_parent.get(current)

        self._update_item_appearance(file_item, status)
        if refresh:
            self._update_folder_titles_upward(folder_key)
            self._refresh_visibility()
            self._apply_responsive_layout()

    def select_file_item(self, file_path: Path):
        """选中并激活指定文件的卡片（视觉选中）"""
        item = self._file_items.get(file_path)
        if not item:
            return

        self.file_list.blockSignals(True)
        try:
            self.file_list.clearSelection()
            item.setSelected(True)
            self.file_list.setCurrentItem(item)
        finally:
            self.file_list.blockSignals(False)

    def expand_related_folder(self, file_path: Path, force_scroll: bool = False):
        """展开指定文件所属目录链；滚动到该文件"""
        file_item = self._file_items.get(file_path)
        if file_item is None:
            return

        folder_key = file_item.data(Qt.ItemDataRole.UserRole + 2)
        if not isinstance(folder_key, str):
            return

        changed = False
        current = folder_key
        while current is not None:
            if self._folder_collapsed.get(current, False):
                self._folder_collapsed[current] = False
                self._update_folder_title(current)
                changed = True
            current = self._folder_parent.get(current)

        if changed:
            self._refresh_visibility()

        if file_item.isHidden():
            return

        # 避免每次显示都强制居中导致视图“往下跳”
        item_rect = self.file_list.visualItemRect(file_item)
        viewport_rect = self.file_list.viewport().rect()
        fully_visible = (
            item_rect.isValid()
            and item_rect.top() >= viewport_rect.top()
            and item_rect.bottom() <= viewport_rect.bottom()
        )

        if force_scroll or not fully_visible:
            self.file_list.scrollToItem(
                file_item,
                QAbstractItemView.ScrollHint.PositionAtTop,
            )

    def update_file_status(self, file_path: Path, status: FileStatus):
        """更新文件状态"""
        if file_path not in self._file_items:
            return

        self._file_status[file_path] = status
        self._update_item_appearance(self._file_items[file_path], status)

    def _update_item_appearance(self, item: QListWidgetItem, status: FileStatus):
        """更新卡片外观（仅文件名颜色）"""
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(file_path, Path):
            return

        color_hex = "#2f3440"
        if status in STATUS_COLORS:
            color_hex = STATUS_COLORS[status]

        name_label = self._file_name_labels.get(file_path)
        if name_label:
            name_label.setText(self._elide_file_name(file_path.name))
            name_label.setStyleSheet(f"font-size: 12px; color: {color_hex};")
            name_label.setToolTip(file_path.name)

    def get_selected_files(self) -> List[Path]:
        """获取选中的文件"""
        files: List[Path] = []
        for item in self.file_list.selectedItems():
            if item.data(Qt.ItemDataRole.UserRole + 1) != "file":
                continue
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(file_path, Path):
                files.append(file_path)
        return files

    def remove_file(self, file_path: Path):
        """移除单个文件"""
        if file_path not in self._file_items:
            return

        item = self._file_items.pop(file_path)
        folder_key = item.data(Qt.ItemDataRole.UserRole + 2)
        row = self.file_list.row(item)
        if row >= 0:
            self.file_list.takeItem(row)

        if folder_key in self._folder_file_items:
            self._folder_file_items[folder_key] = [
                it for it in self._folder_file_items[folder_key] if it is not item
            ]

        self._file_status.pop(file_path, None)
        self._file_name_labels.pop(file_path, None)
        self._file_preview_labels.pop(file_path, None)
        self._file_preview_cards.pop(file_path, None)
        self._file_thumbnail_loaded.pop(file_path, None)

        # 级联减少文件计数
        current = folder_key
        while current is not None:
            if current in self._folder_total_file_count:
                self._folder_total_file_count[current] = max(
                    0, self._folder_total_file_count[current] - 1
                )
            current = self._folder_parent.get(current)

        self._update_folder_titles_upward(folder_key)
        self._cleanup_empty_folders(folder_key)
        self._refresh_visibility()

    def rename_file(self, old_path: Path, new_path: Path):
        """更新列表中单个文件的路径和显示名称"""
        if old_path not in self._file_items:
            return

        item = self._file_items.pop(old_path)
        item.setData(Qt.ItemDataRole.UserRole, new_path)
        self._file_items[new_path] = item

        status = self._file_status.pop(old_path, FileStatus.NORMAL)
        self._file_status[new_path] = status

        if old_path in self._file_name_labels:
            self._file_name_labels[new_path] = self._file_name_labels.pop(old_path)
        if old_path in self._file_preview_labels:
            self._file_preview_labels[new_path] = self._file_preview_labels.pop(old_path)
        if old_path in self._file_preview_cards:
            self._file_preview_cards[new_path] = self._file_preview_cards.pop(old_path)
        if old_path in self._file_thumbnail_loaded:
            self._file_thumbnail_loaded[new_path] = self._file_thumbnail_loaded.pop(old_path)

        self._update_item_appearance(item, status)

    def remove_files(self, file_paths: List[Path]):
        """移除多个文件（批量优化）"""
        self.remove_files_bulk(file_paths)

    def remove_files_bulk(self, file_paths: List[Path]):
        """高效批量移除文件，避免逐条刷新造成卡顿"""
        if not file_paths:
            return

        existing_paths = [file_path for file_path in file_paths if file_path in self._file_items]
        if not existing_paths:
            return

        rows_to_remove: List[int] = []
        removed_items_by_folder: Dict[str, List[QListWidgetItem]] = {}
        decrements: Dict[str, int] = {}
        affected_folders = set()

        previous_signal_state = self.file_list.blockSignals(True)
        self.file_list.setUpdatesEnabled(False)
        try:
            for file_path in existing_paths:
                item = self._file_items.pop(file_path, None)
                if item is None:
                    continue

                folder_key = item.data(Qt.ItemDataRole.UserRole + 2)
                if not isinstance(folder_key, str):
                    folder_key = ""

                row = self.file_list.row(item)
                if row >= 0:
                    rows_to_remove.append(row)

                removed_items_by_folder.setdefault(folder_key, []).append(item)
                self._file_status.pop(file_path, None)
                self._file_name_labels.pop(file_path, None)
                self._file_preview_labels.pop(file_path, None)
                self._file_preview_cards.pop(file_path, None)
                self._file_thumbnail_loaded.pop(file_path, None)

                current = folder_key
                while current is not None:
                    decrements[current] = decrements.get(current, 0) + 1
                    affected_folders.add(current)
                    current = self._folder_parent.get(current)

            for folder_key, removed_items in removed_items_by_folder.items():
                existing_items = self._folder_file_items.get(folder_key)
                if existing_items is None:
                    continue
                removed_ids = {id(item) for item in removed_items}
                self._folder_file_items[folder_key] = [
                    item for item in existing_items
                    if id(item) not in removed_ids
                ]

            for folder_key, dec in decrements.items():
                if folder_key in self._folder_total_file_count:
                    self._folder_total_file_count[folder_key] = max(
                        0, self._folder_total_file_count[folder_key] - dec
                    )

            for row in sorted(set(rows_to_remove), reverse=True):
                self.file_list.takeItem(row)

            for folder_key in sorted(
                affected_folders,
                key=lambda key: self._folder_depth.get(key, 0),
                reverse=True,
            ):
                if folder_key in self._folder_items:
                    self._cleanup_empty_folders(folder_key)

            for folder_key in affected_folders:
                if folder_key in self._folder_items:
                    self._update_folder_title(folder_key)

            self._refresh_visibility()
        finally:
            self.file_list.setUpdatesEnabled(True)
            self.file_list.blockSignals(previous_signal_state)

    def refresh_after_batch(self):
        """批量操作后统一刷新UI状态"""
        self.file_list.setUpdatesEnabled(False)
        try:
            affected_folders = set()
            for it in self._file_items.values():
                folder_key = it.data(Qt.ItemDataRole.UserRole + 2)
                current = folder_key
                while current is not None:
                    affected_folders.add(current)
                    current = self._folder_parent.get(current)

            for f_key in affected_folders:
                self._update_folder_title(f_key)

            self._refresh_visibility()
        finally:
            self.file_list.setUpdatesEnabled(True)
            self._apply_responsive_layout(force=True)
            self.file_list.viewport().update()

    def refresh_after_incremental(self, file_paths: List[Path]):
        """增量插入后的轻量刷新：仅刷新受影响目录与新文件可见性。"""
        if not file_paths:
            return
        self.file_list.setUpdatesEnabled(False)
        try:
            affected_folders = set()
            for file_path in file_paths:
                item = self._file_items.get(file_path)
                if item is None:
                    continue
                folder_key = item.data(Qt.ItemDataRole.UserRole + 2)
                if not isinstance(folder_key, str):
                    continue
                item.setHidden(self._has_collapsed_ancestor(folder_key, include_self=True))
                current = folder_key
                while current is not None:
                    affected_folders.add(current)
                    current = self._folder_parent.get(current)

            for folder_key in affected_folders:
                self._update_folder_title(folder_key)
                header_item = self._folder_items.get(folder_key)
                if header_item is None:
                    continue
                forced_hidden = folder_key in self._folder_force_hidden
                header_item.setHidden(
                    forced_hidden
                    or self._has_collapsed_ancestor(folder_key, include_self=False)
                )
        finally:
            self.file_list.setUpdatesEnabled(True)
            self._apply_responsive_layout(force=False)
            self.file_list.viewport().update()

    def is_folder_collapsed(self, folder_key: str) -> bool:
        if not isinstance(folder_key, str):
            return False
        return bool(self._folder_collapsed.get(folder_key, False))

    def clear_files(self):
        """清空列表"""
        self.file_list.clear()
        self._file_items.clear()
        self._file_name_labels.clear()
        self._file_preview_labels.clear()
        self._file_preview_cards.clear()
        self._file_status.clear()
        self._file_thumbnail_loaded.clear()

        self._folder_items.clear()
        self._folder_title_labels.clear()
        self._folder_file_items.clear()
        self._folder_collapsed.clear()
        self._folder_force_hidden.clear()
        self._folder_parent.clear()
        self._folder_children.clear()
        self._folder_depth.clear()
        self._folder_display_name.clear()
        self._folder_total_file_count.clear()
        self._source_folder_map.clear()
        self._folder_path_map.clear()

    def clear(self):
        """兼容旧调用"""
        self.clear_files()

    def refresh_list(self):
        """兼容旧调用：刷新列表显示"""
        self.file_list.viewport().update()

    def show_loading(self, show: bool = True):
        """显示/隐藏加载动画"""
        self.loading_widget.setVisible(show)
        self.file_list.setVisible(not show)

    def _on_item_clicked(self, item: QListWidgetItem):
        item_type = item.data(Qt.ItemDataRole.UserRole + 1)

        if item_type == "folder_header":
            folder_key = item.data(Qt.ItemDataRole.UserRole)
            collapsed = not self._folder_collapsed.get(folder_key, False)
            self._folder_collapsed[folder_key] = collapsed
            self._update_folder_title(folder_key)
            self._refresh_visibility()
            item.setSelected(False)
            if isinstance(folder_key, str):
                self.folder_header_clicked.emit(folder_key)
            return

        if item_type == "file":
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(file_path, Path):
                self.file_selected.emit(file_path)

    def _on_selection_changed(self):
        selected_files = self.get_selected_files()
        # 始终同步选择状态，避免主窗口保留过期多选导致“处理一张却跳多张”
        self.files_batch_selected.emit(selected_files)

    def get_adjacent_visible_file(self, file_path: Path, forward: bool = True) -> Optional[Path]:
        """获取指定文件在当前可见列表中的相邻文件（跳过目录头部与隐藏项）"""
        start_row = -1
        total = self.file_list.count()
        for row in range(total):
            item = self.file_list.item(row)
            if item is None:
                continue
            if item.data(Qt.ItemDataRole.UserRole + 1) != "file":
                continue
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate, Path) and candidate == file_path:
                start_row = row
                break

        if start_row < 0:
            return None

        step = 1 if forward else -1
        row = start_row + step
        while 0 <= row < total:
            item = self.file_list.item(row)
            row += step
            if item is None or item.isHidden():
                continue
            if item.data(Qt.ItemDataRole.UserRole + 1) != "file":
                continue
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate, Path):
                return candidate
        return None

    def _show_context_menu(self, pos):
        """右键菜单：支持移除此源文件夹、在资源管理器中查看"""
        item = self.file_list.itemAt(pos)
        if item is None:
            return

        menu = QMenu(self)
        item_type = item.data(Qt.ItemDataRole.UserRole + 1)

        explorer_action = None
        explorer_path = None
        if item_type == "file":
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(file_path, Path) and file_path.exists():
                explorer_action = menu.addAction("在资源管理器中查看")
                explorer_path = file_path
        elif item_type == "folder_header":
            folder_key = item.data(Qt.ItemDataRole.UserRole)
            folder_path = self._folder_path_map.get(folder_key)
            if isinstance(folder_path, Path) and folder_path.exists():
                explorer_action = menu.addAction("在资源管理器中查看")
                explorer_path = folder_path

        remove_action = None
        source_folder = self._resolve_source_folder_for_item(item)
        if source_folder is not None:
            remove_action = menu.addAction("移除此源文件夹")

        if menu.isEmpty():
            return

        chosen = menu.exec(self.file_list.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == explorer_action and explorer_path:
            self._open_in_explorer(explorer_path, select_file=(item_type == "file"))
        elif chosen == remove_action and source_folder:
            self.source_folder_remove_requested.emit(str(source_folder))

    @staticmethod
    def _open_in_explorer(path: Path, select_file: bool = False):
        try:
            if select_file:
                subprocess.Popen(['explorer', f'/select,{path}'])
            else:
                subprocess.Popen(['explorer', str(path)])
        except Exception as e:
            logger.log_exception(e, f"打开资源管理器失败: {path}")

    def ensure_folder_chain_for_file(self, file_path: Path) -> str:
        """为文件确保多级目录头部存在，返回叶子目录key"""
        if self._grouping_mode == "date":
            return self._ensure_date_group_chain_for_file(file_path)
        if self._grouping_mode == "flat":
            return self._ensure_flat_group_for_file()

        source_root = self._resolve_source_folder(file_path)
        chain: List[Path] = [source_root]

        try:
            rel_parent = file_path.parent.resolve().relative_to(source_root.resolve())
            current = source_root
            for part in rel_parent.parts:
                if part in ("", "."):
                    continue
                current = current / part
                chain.append(current)
        except Exception:
            if file_path.parent != source_root:
                chain.append(file_path.parent)

        for folder_path in chain:
            self._ensure_folder_item(folder_path, source_root)

        return self._path_key(chain[-1])

    def _ensure_flat_group_for_file(self) -> str:
        """扁平模式：不显示目录分组头部，全部文件直接平铺显示。"""
        folder_key = "flat"
        if folder_key in self._folder_items:
            return folder_key

        self._ensure_virtual_folder_item(folder_key, "", None, 0)
        self._folder_force_hidden.add(folder_key)
        self._folder_collapsed[folder_key] = False
        self._update_folder_title(folder_key)
        self._refresh_visibility()
        return folder_key

    def _ensure_date_group_chain_for_file(self, file_path: Path) -> str:
        """按 YYYY/MM/DD 虚拟分组创建头部，返回 DD 分组 key。"""
        dt = None
        if callable(self._grouping_datetime_resolver):
            try:
                dt = self._grouping_datetime_resolver(file_path)
            except Exception:
                dt = None
        if dt is None:
            dt = MediaInfoExtractor.get_grouping_datetime(file_path)
        if dt is None:
            year, month, day = "未知", "??", "??"
        else:
            year = dt.strftime("%Y")
            month = dt.strftime("%m")
            day = dt.strftime("%d")

        year_key = f"date/{year}"
        month_key = f"date/{year}/{month}"
        day_key = f"date/{year}/{month}/{day}"

        self._ensure_virtual_folder_item(year_key, year, None, 0)
        self._ensure_virtual_folder_item(month_key, month, year_key, 1)
        self._ensure_virtual_folder_item(day_key, day, month_key, 2)

        return day_key

    def _ensure_virtual_folder_item(
        self,
        folder_key: str,
        display_name: str,
        parent_key: Optional[str],
        depth: int,
    ):
        """确保一个虚拟分组头部存在（用于日期分组）。"""
        if folder_key in self._folder_items:
            return

        header_item = QListWidgetItem()
        header_item.setData(Qt.ItemDataRole.UserRole, folder_key)
        header_item.setData(Qt.ItemDataRole.UserRole + 1, "folder_header")
        header_item.setSizeHint(QSize(self._layout_metrics["header_width"], 34))

        insert_row = self._find_insert_row_for_folder(parent_key)
        self.file_list.insertItem(insert_row, header_item)

        header_widget = QWidget(self.file_list)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(6)

        title_label = QLabel("", header_widget)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.file_list.setItemWidget(header_item, header_widget)

        self._folder_items[folder_key] = header_item
        self._folder_path_map[folder_key] = None
        self._folder_title_labels[folder_key] = title_label
        self._folder_file_items[folder_key] = []
        self._folder_collapsed[folder_key] = True
        self._folder_parent[folder_key] = parent_key
        self._folder_children[folder_key] = self._folder_children.get(folder_key, [])
        self._folder_depth[folder_key] = depth
        self._folder_display_name[folder_key] = display_name
        self._folder_total_file_count[folder_key] = self._folder_total_file_count.get(folder_key, 0)

        if parent_key is not None:
            children = self._folder_children.setdefault(parent_key, [])
            if folder_key not in children:
                children.append(folder_key)

        self._update_folder_title(folder_key)
        self._refresh_visibility()

    def _ensure_folder_item(self, folder_path: Path, source_root: Path):
        """确保单个目录头部存在"""
        folder_key = self._path_key(folder_path)
        if folder_key in self._folder_items:
            return

        source_root_key = self._path_key(source_root)
        parent_key: Optional[str] = None
        if folder_key != source_root_key:
            parent_path = folder_path.parent
            self._ensure_folder_item(parent_path, source_root)
            parent_key = self._path_key(parent_path)

        depth = 0 if parent_key is None else self._folder_depth.get(parent_key, 0) + 1
        display_name = self._folder_labels.get(folder_key, folder_path.name or str(folder_path))
        if depth > 0:
            display_name = folder_path.name or display_name

        header_item = QListWidgetItem()
        header_item.setData(Qt.ItemDataRole.UserRole, folder_key)
        header_item.setData(Qt.ItemDataRole.UserRole + 1, "folder_header")
        header_item.setSizeHint(QSize(self._layout_metrics["header_width"], 34))

        insert_row = self._find_insert_row_for_folder(parent_key)
        self.file_list.insertItem(insert_row, header_item)

        header_widget = QWidget(self.file_list)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(6)

        title_label = QLabel("", header_widget)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.file_list.setItemWidget(header_item, header_widget)

        self._folder_items[folder_key] = header_item
        self._folder_path_map[folder_key] = folder_path
        self._folder_title_labels[folder_key] = title_label
        self._folder_file_items[folder_key] = []
        # 默认折叠，按“当前文件定位/筛选命中”再自动展开
        self._folder_collapsed[folder_key] = True
        self._folder_parent[folder_key] = parent_key
        self._folder_children[folder_key] = self._folder_children.get(folder_key, [])
        self._folder_depth[folder_key] = depth
        self._folder_display_name[folder_key] = display_name
        self._folder_total_file_count[folder_key] = self._folder_total_file_count.get(folder_key, 0)

        if parent_key is not None:
            children = self._folder_children.setdefault(parent_key, [])
            if folder_key not in children:
                children.append(folder_key)

        self._update_folder_title(folder_key)
        self._refresh_visibility()

    def _find_insert_row_for_folder(self, parent_key: Optional[str]) -> int:
        if parent_key is None:
            return self.file_list.count()
        return self._get_subtree_end_row(parent_key) + 1

    def _find_insert_row_for_file(self, folder_key: str, index_in_folder: Optional[int] = None) -> int:
        """计算文件卡片插入位置（插在目录头后，子目录前）"""
        header_item = self._folder_items[folder_key]
        header_row = self.file_list.row(header_item)

        if index_in_folder is None:
            index_in_folder = len(self._folder_file_items.get(folder_key, []))

        return header_row + 1 + index_in_folder

    def _get_subtree_end_row(self, folder_key: str) -> int:
        """获取目录子树在列表中的末尾行号"""
        header_item = self._folder_items.get(folder_key)
        if header_item is None:
            return self.file_list.count() - 1

        start_row = self.file_list.row(header_item)
        end_row = start_row
        for row in range(start_row + 1, self.file_list.count()):
            item = self.file_list.item(row)
            item_type = item.data(Qt.ItemDataRole.UserRole + 1)
            if item_type == "folder_header":
                current_key = item.data(Qt.ItemDataRole.UserRole)
                if self._is_descendant_folder(current_key, folder_key):
                    end_row = row
                    continue
            elif item_type == "file":
                owner_key = item.data(Qt.ItemDataRole.UserRole + 2)
                if self._is_descendant_folder(owner_key, folder_key):
                    end_row = row
                    continue
            break

        return end_row

    def _is_descendant_folder(self, folder_key: str, ancestor_key: str) -> bool:
        current = folder_key
        while current is not None:
            if current == ancestor_key:
                return True
            current = self._folder_parent.get(current)
        return False

    def _refresh_visibility(self):
        """根据折叠状态刷新目录和文件可见性"""
        for folder_key, header_item in self._folder_items.items():
            forced_hidden = folder_key in self._folder_force_hidden
            header_item.setHidden(
                forced_hidden
                or self._has_collapsed_ancestor(folder_key, include_self=False)
            )

        for file_path, file_item in self._file_items.items():
            folder_key = file_item.data(Qt.ItemDataRole.UserRole + 2)
            file_item.setHidden(self._has_collapsed_ancestor(folder_key, include_self=True))

    def _has_collapsed_ancestor(self, folder_key: str, include_self: bool) -> bool:
        current = folder_key if include_self else self._folder_parent.get(folder_key)
        while current is not None:
            if self._folder_collapsed.get(current, False):
                return True
            current = self._folder_parent.get(current)
        return False

    def _update_folder_titles_upward(self, folder_key: str):
        current = folder_key
        while current is not None:
            self._update_folder_title(current)
            current = self._folder_parent.get(current)

    def _update_folder_title(self, folder_key: str):
        """更新目录标题文本"""
        title = self._folder_title_labels.get(folder_key)
        if title is None:
            return

        label = self._folder_display_name.get(folder_key, "文件夹")
        depth = self._folder_depth.get(folder_key, 0)
        count = self._folder_total_file_count.get(folder_key, 0)
        collapsed = self._folder_collapsed.get(folder_key, False)
        arrow = "▶" if collapsed else "▼"
        if str(folder_key).startswith("date/"):
            icon = "📅"
        else:
            icon = "🗂" if depth == 0 else "📂"
        indent = "  " * depth
        title.setText(f"{indent}{arrow} {icon} {label} ({count})")
        title.setStyleSheet("font-size: 12px; color: #2f3440; font-weight: 600;")

    def _cleanup_empty_folders(self, start_folder_key: str):
        """清理空目录头部（从叶子向上回收）"""
        current = start_folder_key
        while current is not None:
            if current not in self._folder_items:
                current = self._folder_parent.get(current)
                continue

            has_files = self._folder_total_file_count.get(current, 0) > 0
            existing_children = [
                child for child in self._folder_children.get(current, [])
                if child in self._folder_items
            ]
            has_children = len(existing_children) > 0
            if has_files or has_children:
                break

            header_item = self._folder_items.pop(current, None)
            if header_item is not None:
                row = self.file_list.row(header_item)
                if row >= 0:
                    self.file_list.takeItem(row)

            parent_key = self._folder_parent.get(current)
            if parent_key is not None and parent_key in self._folder_children:
                self._folder_children[parent_key] = [
                    child for child in self._folder_children[parent_key] if child != current
                ]

            self._folder_title_labels.pop(current, None)
            self._folder_file_items.pop(current, None)
            self._folder_collapsed.pop(current, None)
            self._folder_parent.pop(current, None)
            self._folder_children.pop(current, None)
            self._folder_depth.pop(current, None)
            self._folder_display_name.pop(current, None)
            self._folder_total_file_count.pop(current, None)
            self._folder_path_map.pop(current, None)

            current = parent_key

    def _build_folder_labels(self):
        """构建根目录展示名（处理同名目录冲突）"""
        self._folder_labels.clear()
        if not self._source_folders:
            return

        name_counts = Counter(folder.name for folder in self._source_folders)
        for folder in self._source_folders:
            key = self._path_key(folder)
            if name_counts[folder.name] > 1:
                label = f"{folder.name} ({folder.parent.name})"
            else:
                label = folder.name
            self._folder_labels[key] = label

    def _resolve_source_folder(self, file_path: Path) -> Path:
        """解析文件归属的源文件夹（最长匹配）"""
        if not self._source_folders:
            return file_path.parent

        matches = [
            folder for folder in self._source_folders
            if self._is_path_in_folder(file_path, folder)
        ]
        if not matches:
            return file_path.parent

        return max(matches, key=lambda p: len(self._path_key(p)))

    def _resolve_source_folder_for_item(self, item: QListWidgetItem) -> Optional[Path]:
        item_type = item.data(Qt.ItemDataRole.UserRole + 1)
        if item_type == "file":
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(file_path, Path):
                return self._resolve_source_folder(file_path)
            return None

        if item_type == "folder_header":
            folder_key = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(folder_key, str):
                return None
            root_key = self._get_root_folder_key(folder_key)
            source_folder = self._source_folder_map.get(root_key)
            if source_folder:
                return source_folder
            root_path = self._folder_path_map.get(root_key)
            return root_path if isinstance(root_path, Path) else None

        return None

    def _get_root_folder_key(self, folder_key: str) -> str:
        current = folder_key
        while self._folder_parent.get(current) is not None:
            parent = self._folder_parent.get(current)
            if parent is None:
                break
            current = parent
        return current

    def get_root_folder_key(self, folder_key: str) -> str:
        """公开接口：获取目录所在树根 key。"""
        if not isinstance(folder_key, str):
            return ""
        return self._get_root_folder_key(folder_key)

    def has_file(self, file_path: Path) -> bool:
        return file_path in self._file_items

    def get_visible_file_paths(self, lookaround: int = 20) -> List[Path]:
        """获取当前可视区文件，并附带前后缓冲行。"""
        total = self.file_list.count()
        if total <= 0:
            return []

        viewport = self.file_list.viewport()
        viewport_rect = viewport.rect()
        width = max(viewport_rect.width(), 1)
        x_candidates = [4, width // 2, max(width - 6, 4)]

        def _find_row_from_top() -> int:
            y = max(viewport_rect.top(), 0)
            y_end = max(viewport_rect.bottom(), y)
            while y <= y_end:
                for x in x_candidates:
                    idx = self.file_list.indexAt(QPoint(x, y))
                    if idx.isValid():
                        return idx.row()
                y += 6
            return -1

        def _find_row_from_bottom() -> int:
            y = max(viewport_rect.bottom(), 0)
            y_start = max(viewport_rect.top(), 0)
            while y >= y_start:
                for x in x_candidates:
                    idx = self.file_list.indexAt(QPoint(x, y))
                    if idx.isValid():
                        return idx.row()
                y -= 6
            return -1

        first_row = _find_row_from_top()
        last_row = _find_row_from_bottom()
        if first_row < 0 and last_row < 0:
            current = self.file_list.currentRow()
            if current < 0:
                current = 0
            first_row = current
            last_row = min(total - 1, current + 12)
        elif first_row < 0:
            first_row = max(0, last_row - 12)
        elif last_row < 0:
            last_row = min(total - 1, first_row + 12)

        if first_row > last_row:
            first_row, last_row = last_row, first_row

        pad = max(int(lookaround), 0)
        start = max(0, first_row - pad)
        end = min(total - 1, last_row + pad)

        results: List[Path] = []
        seen = set()
        for row in range(start, end + 1):
            item = self.file_list.item(row)
            if item is None or item.isHidden():
                continue
            if item.data(Qt.ItemDataRole.UserRole + 1) != "file":
                continue
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(file_path, Path):
                continue
            key = self._path_key(file_path)
            if key in seen:
                continue
            seen.add(key)
            results.append(file_path)
        return results

    def has_thumbnail(self, file_path: Path) -> bool:
        return bool(self._file_thumbnail_loaded.get(file_path, False))

    def release_thumbnail(self, file_path: Path):
        """释放文件缩略图（保留条目，仅回退为占位底色）。"""
        if file_path not in self._file_items:
            return
        preview_label = self._file_preview_labels.get(file_path)
        if preview_label is not None:
            preview_label.setPixmap(QPixmap())
        self._file_thumbnail_loaded[file_path] = False

    def _elide_file_name(self, file_name: str) -> str:
        """将文件名压缩为单行显示"""
        metrics = QFontMetrics(QLabel().font())
        width = max(int(self._layout_metrics.get("text_width", 176)), 88)
        return metrics.elidedText(file_name, Qt.TextElideMode.ElideMiddle, width)

    def _to_square_pixmap(self, pixmap: Optional[QPixmap]) -> QPixmap:
        """缩略图转换为正方形显示；空图返回空 QPixmap（label 底色即占位）。"""
        side = max(int(self._layout_metrics.get("preview", 176)), 80)
        if pixmap is None or pixmap.isNull():
            return QPixmap()
        return pixmap.scaled(
            side, side,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    @staticmethod
    def _path_key(path: Path) -> str:
        """路径归一化键（用于大小写不敏感平台）"""
        try:
            absolute = path.resolve()
        except Exception:
            absolute = path.absolute()
        return os.path.normcase(os.path.normpath(str(absolute)))

    def _is_path_in_folder(self, file_path: Path, folder_path: Path) -> bool:
        file_key = self._path_key(file_path)
        folder_key = self._path_key(folder_path)
        try:
            common = os.path.commonpath([file_key, folder_key])
        except ValueError:
            return False
        return common == folder_key


# ==================== 中栏：媒体预览（阶段二：图片缩放；阶段四：视频播放） ====================

class MediaViewer(QWidget):
    """媒体查看器"""

    MIN_ZOOM_LEVEL = 0.0025  # 0.25%
    MAX_ZOOM_LEVEL = 20.0    # 2000%

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_file: Optional[Path] = None
        self._zoom_level = 1.0

        self._init_ui()

    def _init_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # 图片显示区（QGraphicsView 支持缩放）
        self.graphics_view = QGraphicsView(self)
        self.graphics_scene = QGraphicsScene(self)
        self.graphics_view.setScene(self.graphics_scene)
        self.graphics_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.graphics_view.setRenderHint(self.graphics_view.renderHints())
        self.graphics_view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.graphics_view.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.graphics_view.setStyleSheet("QGraphicsView { background-color: #15171d; border: none; }")
        self.graphics_view.wheelEvent = self._on_wheel_event
        self.graphics_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.graphics_view.customContextMenuRequested.connect(self._show_viewer_context_menu)

        # Ctrl+左键拖动 = 文件拖拽到外部；普通左键拖动 = 平移缩放
        _original_mouse_press = self.graphics_view.mousePressEvent
        _original_mouse_move = self.graphics_view.mouseMoveEvent
        _original_mouse_release = self.graphics_view.mouseReleaseEvent
        _viewer_ref = self

        def _preview_mouse_press(event):
            if (event.button() == Qt.MouseButton.LeftButton
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    and _viewer_ref._current_file):
                _viewer_ref._ctrl_drag_start = event.pos()
                event.accept()
                return
            _viewer_ref._ctrl_drag_start = None
            _original_mouse_press(event)

        def _preview_mouse_move(event):
            if (hasattr(_viewer_ref, '_ctrl_drag_start')
                    and _viewer_ref._ctrl_drag_start is not None
                    and (event.buttons() & Qt.MouseButton.LeftButton)):
                distance = (event.pos() - _viewer_ref._ctrl_drag_start).manhattanLength()
                if distance > 15 and _viewer_ref._current_file:
                    from PySide6.QtCore import QMimeData, QUrl
                    from PySide6.QtGui import QDrag
                    mime = QMimeData()
                    mime.setUrls([QUrl.fromLocalFile(str(_viewer_ref._current_file))])
                    drag = QDrag(self.graphics_view)
                    drag.setMimeData(mime)
                    drag.exec(Qt.DropAction.CopyAction)
                    _viewer_ref._ctrl_drag_start = None
                    return
            _original_mouse_move(event)

        def _preview_mouse_release(event):
            _viewer_ref._ctrl_drag_start = None
            _original_mouse_release(event)

        self.graphics_view.mousePressEvent = _preview_mouse_press
        self.graphics_view.mouseMoveEvent = _preview_mouse_move
        self.graphics_view.mouseReleaseEvent = _preview_mouse_release
        self._ctrl_drag_start = None

        self.pixmap_item: Optional[QGraphicsPixmapItem] = None
        self.original_pixmap: Optional[QPixmap] = None

        self.layout.addWidget(self.graphics_view)

        # 进度标签（右下角）
        self.progress_label = QLabel("0/0", self)
        self.progress_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(0, 0, 0, 150);
                color: white;
                padding: 5px 10px;
                border-radius: 5px;
            }
            """
        )
        self.progress_label.move(self.width() - 80, self.height() - 40)

        # 缩放比例标签（左上角）
        self.zoom_label = QLabel("100%", self)
        self.zoom_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(0, 0, 0, 150);
                color: white;
                padding: 5px 10px;
                border-radius: 5px;
            }
            """
        )
        self.zoom_label.move(10, 10)

        # 加载动画（busy 进度条）
        self.loading_widget = QWidget(self)
        loading_layout = QVBoxLayout(self.loading_widget)
        self.loading_bar = QProgressBar(self.loading_widget)
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setFixedWidth(180)
        loading_layout.addWidget(self.loading_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        self.loading_widget.setStyleSheet("background: transparent;")
        self.loading_widget.hide()

    def display_image(self, image_path: Path):
        """显示图片并自动缩放适应窗口"""
        try:
            self.show_loading(True)
            pixmap = QPixmap(str(image_path))
            if pixmap.isNull():
                # QPixmap 对 HEIC/LIVP 等兼容较差，回退到 Pillow（HEIC 需 pillow-heif）
                with PILImage.open(image_path) as pil_image:
                    self.display_pil_image(pil_image, image_path)
                    return

            self.display_pixmap(pixmap, image_path)
            self.show_loading(False)
        except Exception as e:
            logger.log_exception(e, f"显示图片失败: {image_path}")
            self.show_loading(False)

    def display_pil_image(self, image: PILImage.Image, source_path: Optional[Path] = None):
        """显示 Pillow 图片对象"""
        try:
            rgb = image.convert("RGB")
            data = rgb.tobytes("raw", "RGB")
            qimage = QImage(
                data,
                rgb.width,
                rgb.height,
                rgb.width * 3,
                QImage.Format.Format_RGB888,
            )
            pixmap = QPixmap.fromImage(qimage.copy())
            self.display_pixmap(pixmap, source_path)
            self.show_loading(False)
        except Exception as e:
            logger.log_exception(e, "显示Pillow图片失败")
            self.show_loading(False)

    def display_pixmap(self, pixmap: QPixmap, source_path: Optional[Path] = None, show_play_button: bool = False):
        """显示QPixmap"""
        if pixmap.isNull():
            return

        self.graphics_view.show()
        self.original_pixmap = pixmap
        self.graphics_scene.clear()
        self.pixmap_item = self.graphics_scene.addPixmap(pixmap)
        self.graphics_scene.setSceneRect(pixmap.rect().toRectF())
        self.graphics_view.resetTransform()

        QTimer.singleShot(30, self._apply_fit_to_view)
        self._current_file = source_path
        # show_play_button 暂存字段：阶段四视频封面播放按钮启用
        self._show_play_button = bool(show_play_button)

    def _apply_fit_to_view(self):
        """应用适应窗口的缩放"""
        if not self.pixmap_item:
            return

        self.graphics_view.fitInView(
            self.graphics_scene.sceneRect(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.graphics_view.setAlignment(Qt.AlignmentFlag.AlignCenter)

        view_rect = self.graphics_view.viewport().rect()
        scene_rect = self.graphics_scene.sceneRect()

        if scene_rect.width() > 0 and scene_rect.height() > 0:
            x_ratio = view_rect.width() / scene_rect.width()
            y_ratio = view_rect.height() / scene_rect.height()
            self._zoom_level = max(
                self.MIN_ZOOM_LEVEL,
                min(self.MAX_ZOOM_LEVEL, min(x_ratio, y_ratio)),
            )
            self._update_zoom_label()

    def display_video_cover(self, video_path: Path, cover_pixmap: QPixmap):
        """显示视频封面（居中；播放为阶段四）"""
        self.display_pixmap(cover_pixmap, video_path, show_play_button=True)

    def clear(self):
        """清空显示内容"""
        self.graphics_scene.clear()
        self.pixmap_item = None
        self.original_pixmap = None
        self._current_file = None
        self.progress_label.setText("0/0")

    def stop_video(self):
        """阶段四接入 QMediaPlayer 后实现；保留接口供页面调用。"""

    def zoom_in(self):
        self._zoom_level = min(self.MAX_ZOOM_LEVEL, self._zoom_level * 1.2)
        self._apply_zoom()

    def zoom_out(self):
        self._zoom_level = max(self.MIN_ZOOM_LEVEL, self._zoom_level / 1.2)
        self._apply_zoom()

    def _apply_zoom(self):
        if self.pixmap_item:
            self._zoom_level = max(
                self.MIN_ZOOM_LEVEL, min(self.MAX_ZOOM_LEVEL, self._zoom_level)
            )
            self.graphics_view.resetTransform()
            self.graphics_view.scale(self._zoom_level, self._zoom_level)
            self._update_zoom_label()

    def _update_zoom_label(self):
        percent = self._zoom_level * 100.0
        if percent < 10:
            text = f"{percent:.2f}%"
        elif percent < 100:
            text = f"{percent:.1f}%"
        else:
            text = f"{int(percent)}%"
        self.zoom_label.setText(text)

    def _on_wheel_event(self, event):
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        event.accept()

    def update_progress(self, current: int, total: int):
        self.progress_label.setText(f"{current}/{total}")

    def _show_viewer_context_menu(self, pos):
        """右键菜单：在资源管理器中查看当前文件"""
        if not self._current_file or not self._current_file.exists():
            return

        menu = QMenu(self)
        explorer_action = menu.addAction("在资源管理器中查看")
        chosen = menu.exec(self.graphics_view.viewport().mapToGlobal(pos))
        if chosen == explorer_action:
            try:
                subprocess.Popen(['explorer', f'/select,{self._current_file}'])
            except Exception as e:
                logger.log_exception(e, f"打开资源管理器失败: {self._current_file}")

    def show_loading(self, show: bool = True):
        if show:
            self.loading_widget.setGeometry(
                (self.width() - 200) // 2,
                (self.height() - 60) // 2,
                200,
                60,
            )
        self.loading_widget.setVisible(show)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.progress_label.move(self.width() - 100, self.height() - 50)


# ==================== 右栏：媒体信息面板（阶段二：信息展示） ====================

class FileInfoPanel(QWidget):
    """文件信息面板（信息 + 重命名双标签页；压缩标签页为阶段四）。

    重命名标签页：手动重命名 + 模板管理 + 自动重命名 token + 分类词，
    模板/分类词持久化到 config.json「image_sorter」节（settings.set_dict）。
    """

    copy_info_requested = Signal(str)  # 信息提示文本（页面层转 toast）
    rename_requested = Signal(str)     # 请求重命名当前文件（新文件名）
    auto_rename_config_changed = Signal(bool, str)  # 自动重命名配置更新
    notify_requested = Signal(str, str)  # 通用提示（文本, 级别 success/warning/error）

    PRIORITY_FIELDS = [
        "格式", "编码", "分辨率", "时长", "帧率", "文件大小",
        "创建时间", "修改时间", "拍摄时间",
        "制造商", "相机型号", "镜头型号", "ISO", "光圈", "曝光时间", "焦距",
    ]

    AUTO_RENAME_TOKEN_HELP = [
        ("source_name", "源文件名（不含扩展名）"),
        ("format", "文件格式，如 JPG/MP4"),
        ("resolution", "分辨率，如 1920x1080"),
        ("file_size", "文件大小，如 2.3MB"),
        ("created_time", "文件创建时间"),
        ("modified_time", "文件修改时间"),
        ("captured_time", "拍摄时间（有 Exif 时）"),
        ("codec", "编码格式（视频常见）"),
        ("fps", "帧率（视频）"),
        ("duration", "时长（视频）"),
        ("bitrate", "码率（视频）"),
        ("maker", "设备制造商"),
        ("camera_model", "相机型号"),
        ("lens_model", "镜头型号"),
        ("iso", "ISO 感光度"),
        ("aperture", "光圈值"),
        ("exposure_time", "曝光时间"),
        ("focal_length", "焦距"),
        ("date", "当前日期（YYYYMMDD）"),
        ("time", "当前时间（HHMMSS）"),
        ("datetime", "当前日期时间（YYYYMMDD_HHMMSS）"),
    ]
    AUTO_RENAME_AVAILABLE_TOKENS = [token for token, _ in AUTO_RENAME_TOKEN_HELP]

    DEFAULT_RENAME_TEMPLATES = {
        "原名": "{source_name}",
        "日期_原名": "{date}_{source_name}",
        "时间戳_原名": "{datetime}_{source_name}",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_file: Optional[Path] = None
        self._current_info: Dict[str, str] = {}
        self._current_fields: List[str] = []
        self.info_labels: Dict[str, QLabel] = {}

        self._rename_templates: Dict[str, str] = {}
        self._rename_tags: List[str] = []
        self._auto_rename_enabled = False
        self._auto_rename_pattern = "{source_name}"
        self._pattern_renderer = None  # 页面注入：fn(pattern, target_path) -> str

        self._load_rename_templates()
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # 信息卡（白底+边框容器，深浅色主题均正常）
        card = QFrame(self)
        card.setStyleSheet(
            "QFrame { background-color: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }"
        )
        outer.addWidget(card, 1)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)

        self.tab_widget = QTabWidget(card)
        self.tab_widget.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #d0d6df; border-radius: 4px; top: -1px; }"
            " QTabBar::tab { padding: 5px 12px; background: #f7f8fa;"
            " border: 1px solid #d0d6df; border-bottom: none;"
            " border-top-left-radius: 4px; border-top-right-radius: 4px; }"
            " QTabBar::tab:selected { background: #ffffff; color: #2563eb;"
            " font-weight: 600; }"
        )
        card_layout.addWidget(self.tab_widget, 1)

        self._init_info_tab()
        self._init_rename_tab()

    def _init_info_tab(self):
        self.info_tab = QWidget(self.tab_widget)
        self.info_tab.setStyleSheet("background: transparent;")
        info_layout = QVBoxLayout(self.info_tab)
        info_layout.setContentsMargins(6, 8, 6, 6)

        header = QHBoxLayout()
        title = QLabel("媒体信息")
        title.setStyleSheet("font-size: 13px; font-weight: 600; color: #2f3440;")
        header.addWidget(title)
        header.addStretch(1)
        self.copy_all_btn = QPushButton("📋 复制全部")
        self.copy_all_btn.clicked.connect(self._on_copy_all_clicked)
        header.addWidget(self.copy_all_btn)
        info_layout.addLayout(header)

        self.export_txt_btn = QPushButton("📄 导出 TXT")
        self.export_txt_btn.clicked.connect(lambda: self._export_current_info("txt"))
        self.export_json_btn = QPushButton("🧾 导出 JSON")
        self.export_json_btn.clicked.connect(lambda: self._export_current_info("json"))

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.export_txt_btn)
        btn_row.addWidget(self.export_json_btn)
        btn_row.addStretch(1)
        info_layout.addLayout(btn_row)

        self.scroll = QScrollArea(self.info_tab)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea { background-color: #ffffff; border: 1px solid #d0d6df; border-radius: 4px; }"
        )
        self.scroll_container = QWidget()
        self.scroll_container.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.scroll_container)
        info_layout.addWidget(self.scroll, 1)

        self.info_grid = QVBoxLayout(self.scroll_container)
        self.info_grid.setContentsMargins(6, 6, 6, 6)
        self.info_grid.setSpacing(2)
        self.info_grid.addStretch(1)

        self.tab_widget.addTab(self.info_tab, "文件信息")

    def _init_rename_tab(self):
        """重命名标签页：手动重命名 + 模板 + 自动重命名 + 分类词。"""
        self.rename_tab = QWidget(self.tab_widget)
        self.rename_tab.setStyleSheet("background: transparent;")

        rename_scroll = QScrollArea(self.rename_tab)
        rename_scroll.setWidgetResizable(True)
        rename_scroll.setStyleSheet(
            "QScrollArea { background-color: #ffffff; border: none; }"
        )
        rename_tab_layout = QVBoxLayout(self.rename_tab)
        rename_tab_layout.setContentsMargins(0, 0, 0, 0)
        rename_tab_layout.addWidget(rename_scroll)

        body = QWidget(rename_scroll)
        body.setStyleSheet("background: transparent;")
        rename_layout = QVBoxLayout(body)
        rename_layout.setContentsMargins(6, 8, 6, 8)
        rename_layout.setSpacing(6)
        rename_scroll.setWidget(body)

        self.current_name_label = QLabel("当前文件: -", body)
        self.current_name_label.setWordWrap(True)
        self.current_name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.current_name_label.setStyleSheet("color: #6b7280;")
        rename_layout.addWidget(self.current_name_label)

        self.rename_input = QLineEdit(body)
        self.rename_input.setPlaceholderText("输入新文件名（可不带扩展名）")
        rename_layout.addWidget(self.rename_input)

        template_use_layout = QHBoxLayout()
        self.template_combo = QComboBox(body)
        self.template_combo.addItem("选择模板")
        template_use_layout.addWidget(self.template_combo, 1)

        apply_template_btn = QPushButton("套用", body)
        apply_template_btn.clicked.connect(self._on_apply_template)
        template_use_layout.addWidget(apply_template_btn)
        rename_layout.addLayout(template_use_layout)

        self.template_name_input = QLineEdit(body)
        self.template_name_input.setPlaceholderText("新模板名称")
        rename_layout.addWidget(self.template_name_input)

        self.template_pattern_input = QLineEdit(body)
        self.template_pattern_input.setPlaceholderText(
            "模板内容，如 {source_name}_{captured_time}_精选"
        )
        rename_layout.addWidget(self.template_pattern_input)

        template_edit_layout = QHBoxLayout()
        save_template_btn = QPushButton("保存模板", body)
        save_template_btn.clicked.connect(self._on_save_template)
        template_edit_layout.addWidget(save_template_btn)

        delete_template_btn = QPushButton("删除模板", body)
        delete_template_btn.clicked.connect(self._on_delete_template)
        template_edit_layout.addWidget(delete_template_btn)
        rename_layout.addLayout(template_edit_layout)

        template_hint = QLabel("模板与自动重命名使用同一套占位符（见下方可复制列表）", body)
        template_hint.setWordWrap(True)
        template_hint.setStyleSheet("font-size: 11px; color: #888;")
        rename_layout.addWidget(template_hint)

        auto_title = QLabel("自动重命名（分类后）", body)
        auto_title.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #2f3440; margin-top: 6px;"
        )
        rename_layout.addWidget(auto_title)

        auto_toggle_layout = QHBoxLayout()
        self.auto_rename_checkbox = QCheckBox("启用自动重命名", body)
        auto_toggle_layout.addWidget(self.auto_rename_checkbox)
        auto_toggle_layout.addStretch()
        rename_layout.addLayout(auto_toggle_layout)

        self.auto_rename_fields_input = QLineEdit(body)
        self.auto_rename_fields_input.setPlaceholderText(
            "模板，如 {source_name}_{captured_time}_精选"
        )
        rename_layout.addWidget(self.auto_rename_fields_input)

        auto_hint = QLabel(
            "占位符必须是英文并用 {} 包裹；其余内容按普通文本原样保留", body
        )
        auto_hint.setWordWrap(True)
        auto_hint.setStyleSheet("font-size: 11px; color: #888;")
        rename_layout.addWidget(auto_hint)

        fields_hint = QLabel("点击下方占位符可直接复制：", body)
        fields_hint.setWordWrap(True)
        fields_hint.setStyleSheet("font-size: 11px; color: #6b7280;")
        rename_layout.addWidget(fields_hint)

        self.auto_token_search_input = QLineEdit(body)
        self.auto_token_search_input.setPlaceholderText(
            "搜索占位符，如 time / source / 分辨率"
        )
        self.auto_token_search_input.textChanged.connect(
            self._on_auto_token_search_changed
        )
        rename_layout.addWidget(self.auto_token_search_input)

        self.auto_token_panel = QWidget(body)
        self.auto_token_panel.setStyleSheet("background: transparent;")
        self.auto_token_layout = QGridLayout(self.auto_token_panel)
        self.auto_token_layout.setContentsMargins(0, 0, 0, 0)
        self.auto_token_layout.setHorizontalSpacing(8)
        self.auto_token_layout.setVerticalSpacing(6)
        self._auto_token_items: List[tuple] = []

        token_cols = 3
        for i, (token, desc) in enumerate(self.AUTO_RENAME_TOKEN_HELP):
            row = i // token_cols
            col = i % token_cols
            token_btn = QPushButton(f"{{{token}}}", self.auto_token_panel)
            token_btn.setToolTip(f"{desc}\n点击可复制")
            token_btn.clicked.connect(
                lambda _, t=token: self._copy_auto_rename_token(t)
            )
            self.auto_token_layout.addWidget(token_btn, row, col)
            self._auto_token_items.append((token, desc, token_btn))

        rename_layout.addWidget(self.auto_token_panel)

        invalid_hint = QLabel(
            "示例: {source_name}_{captured_time}_raw；重复文件名会自动加 (1)/(2) 后缀", body
        )
        invalid_hint.setWordWrap(True)
        invalid_hint.setStyleSheet("font-size: 11px; color: #888;")
        rename_layout.addWidget(invalid_hint)

        save_auto_btn = QPushButton("保存自动重命名配置", body)
        save_auto_btn.clicked.connect(self._on_save_auto_rename_config)
        rename_layout.addWidget(save_auto_btn)

        tag_title = QLabel("分类词", body)
        tag_title.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #2f3440; margin-top: 6px;"
        )
        rename_layout.addWidget(tag_title)

        tag_edit_layout = QHBoxLayout()
        self.tag_input = QLineEdit(body)
        self.tag_input.setPlaceholderText(
            "新增分类词（自动加前缀_），如 风景 / 人物 / 封茗囧菌"
        )
        self.tag_input.returnPressed.connect(self._on_add_tag)
        tag_edit_layout.addWidget(self.tag_input, 1)

        add_tag_btn = QPushButton("新增词", body)
        add_tag_btn.clicked.connect(self._on_add_tag)
        tag_edit_layout.addWidget(add_tag_btn)
        rename_layout.addLayout(tag_edit_layout)

        self.tag_scroll = QScrollArea(body)
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setFixedHeight(130)
        self.tag_scroll.setStyleSheet(
            "QScrollArea { background-color: #ffffff; border: 1px solid #d0d6df; border-radius: 4px; }"
        )
        self.tag_panel = QWidget(self.tag_scroll)
        self.tag_panel.setStyleSheet("background: transparent;")
        self.tag_layout = QGridLayout(self.tag_panel)
        self.tag_layout.setContentsMargins(4, 4, 4, 4)
        self.tag_layout.setHorizontalSpacing(6)
        self.tag_layout.setVerticalSpacing(6)
        self.tag_scroll.setWidget(self.tag_panel)
        rename_layout.addWidget(self.tag_scroll)

        self.rename_btn = QPushButton("重命名当前文件", body)
        self.rename_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; border: none;"
            " border-radius: 4px; padding: 6px 16px; font-weight: 600; }"
        )
        self.rename_btn.clicked.connect(self._on_rename_clicked)
        self.rename_btn.setEnabled(False)
        rename_layout.addWidget(self.rename_btn)

        rename_layout.addStretch()
        self.tab_widget.addTab(self.rename_tab, "重命名")

        self._refresh_template_combo()
        self._refresh_tag_buttons()

    # ---------- 展示 ----------

    def display_image_info(self, file_path: Path, info: Dict[str, str]):
        """显示图片信息"""
        self._current_file = file_path
        self._clear_info()
        self._display_info(info)
        self._sync_rename_fields(file_path)

    def display_video_info(self, file_path: Path, info: Dict[str, str]):
        """显示视频信息"""
        self._current_file = file_path
        self._clear_info()
        self._display_info(info)
        self._sync_rename_fields(file_path)

    def _display_info(self, info: Dict[str, str]):
        """动态显示信息（有多少显多少，按逻辑优先级排列）"""
        self._current_info = info

        all_fields = list(info.keys())
        ordered_fields = []
        for pf in self.PRIORITY_FIELDS:
            if pf in all_fields:
                ordered_fields.append(pf)
                all_fields.remove(pf)
        ordered_fields.extend(sorted(all_fields))  # 其余按字母排

        self._current_fields = ordered_fields
        self.copy_all_btn.setEnabled(len(ordered_fields) > 0)
        self.export_txt_btn.setEnabled(len(ordered_fields) > 0 and self._current_file is not None)
        self.export_json_btn.setEnabled(len(ordered_fields) > 0 and self._current_file is not None)

        # 移除末尾 stretch，逐行插入后再补回
        while self.info_grid.count():
            item = self.info_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for field in ordered_fields:
            val = str(info[field]).strip()
            if not val or val == "未知":
                continue

            field_label = QLabel(f"{field}:")
            field_label.setStyleSheet("font-weight: 600; color: #5a6474;")
            self.info_grid.addWidget(field_label)

            value_label = QLabel(val)
            value_label.setWordWrap(True)
            value_label.setMinimumWidth(200)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_label.setStyleSheet("margin-left: 10px; margin-bottom: 8px; color: #2f3440;")
            self.info_grid.addWidget(value_label)
            self.info_labels[field] = value_label

        self.info_grid.addStretch(1)

    def _clear_info(self):
        self._current_info = {}
        self._current_fields = []
        self.copy_all_btn.setEnabled(False)
        self.export_txt_btn.setEnabled(False)
        self.export_json_btn.setEnabled(False)
        while self.info_grid.count():
            item = self.info_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.info_labels.clear()
        self.info_grid.addStretch(1)

    # ---------- 复制 / 导出 ----------

    def _on_copy_all_clicked(self):
        if not self._current_info or not self._current_fields:
            return

        lines = []
        for field in self._current_fields:
            val = self._current_info.get(field)
            if val and val != "未知":
                lines.append(f"{field}：{val}")

        if not lines:
            return

        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(lines))
        self.copy_info_requested.emit("已成功将所有信息复制到剪贴板")

    def _export_current_info(self, fmt: str):
        """导出当前文件信息到系统桌面"""
        if not self._current_info or not self._current_fields or self._current_file is None:
            return

        fmt_normalized = (fmt or "").strip().lower()
        if fmt_normalized not in {"txt", "json"}:
            return

        desktop_dir = self._resolve_desktop_dir()
        desktop_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_stem = re.sub(r"[<>:\"/\\|?*]+", "_", self._current_file.stem).strip(" ._") or "media"
        export_path = desktop_dir / f"{safe_stem}_info_{timestamp}.{fmt_normalized}"
        if export_path.exists():
            suffix = 1
            while True:
                candidate = desktop_dir / f"{safe_stem}_info_{timestamp}_{suffix}.{fmt_normalized}"
                if not candidate.exists():
                    export_path = candidate
                    break
                suffix += 1

        ordered_info = {}
        for field in self._current_fields:
            value = str(self._current_info.get(field, "")).strip()
            if value and value != "未知":
                ordered_info[field] = value

        if not ordered_info:
            return

        try:
            if fmt_normalized == "txt":
                lines = [
                    f"文件名: {self._current_file.name}",
                    f"文件路径: {self._current_file}",
                    f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "",
                ]
                lines.extend([f"{k}: {v}" for k, v in ordered_info.items()])
                export_path.write_text("\n".join(lines), encoding="utf-8")
            else:
                payload = {
                    "file_name": self._current_file.name,
                    "file_path": str(self._current_file),
                    "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "info": ordered_info,
                }
                export_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            self.copy_info_requested.emit(f"已导出 {fmt_normalized.upper()} 到桌面: {export_path.name}")
        except Exception as e:
            logger.log_exception(e, f"导出文件信息失败: {export_path}")
            QMessageBox.warning(self, "导出失败", f"写入失败：{e}")

    @staticmethod
    def _resolve_desktop_dir() -> Path:
        desktop = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation
        )
        if desktop:
            return Path(desktop)
        return Path.home() / "Desktop"

    # ---------- 状态同步 ----------

    def get_current_file(self) -> Optional[Path]:
        return self._current_file

    def update_current_file_path(self, file_path: Optional[Path]):
        """更新当前文件路径（清空信息显示）"""
        self._current_file = file_path
        if file_path is None:
            self._clear_info()
        self._sync_rename_fields(file_path)

    # ---------- 重命名（阶段三） ----------

    def set_pattern_renderer(self, renderer):
        """注入页面级模板渲染器：renderer(pattern: str, target_path: Path) -> str。

        模板占位符渲染依赖页面的媒体信息提取与字符清洗逻辑（与自动重命名同一套），
        面板自身不重复实现；未注入时「套用」原样返回模板文本。
        """
        self._pattern_renderer = renderer

    def set_auto_rename_config(self, enabled: bool, pattern: str):
        """设置自动重命名配置（由页面加载持久化值）"""
        self._auto_rename_enabled = bool(enabled)
        self._auto_rename_pattern = str(pattern or "{source_name}").strip() or "{source_name}"

        self.auto_rename_checkbox.setChecked(self._auto_rename_enabled)
        self.auto_rename_fields_input.setText(self._auto_rename_pattern)

    def get_auto_rename_config(self) -> tuple:
        """读取面板当前自动重命名配置（未保存状态）。"""
        return self.auto_rename_checkbox.isChecked(), self.auto_rename_fields_input.text().strip()

    def _sync_rename_fields(self, file_path: Optional[Path]):
        """同步重命名区域显示"""
        if not file_path:
            self.current_name_label.setText("当前文件: -")
            self.rename_input.clear()
            self.rename_btn.setEnabled(False)
            return

        self.current_name_label.setText(f"当前文件: {file_path.name}")
        self.rename_input.setText(file_path.stem)
        self.rename_btn.setEnabled(True)

    def _on_rename_clicked(self):
        """点击重命名：发信号由页面执行（含扩展名补全与列表联动）"""
        new_name = self.rename_input.text().strip()
        if not new_name:
            QMessageBox.information(self, "提示", "请输入新文件名")
            return
        self.rename_requested.emit(new_name)

    def _on_save_auto_rename_config(self):
        """保存自动重命名配置"""
        enabled = self.auto_rename_checkbox.isChecked()
        pattern = self.auto_rename_fields_input.text().strip() or "{source_name}"

        invalid_placeholders = self._find_invalid_auto_rename_placeholders(pattern)
        if invalid_placeholders:
            QMessageBox.warning(
                self,
                "提示",
                "以下占位符不可用:\n"
                + "\n".join(invalid_placeholders)
                + "\n\n占位符规则：仅英文/下划线，必须写成 {token}。",
            )
            return

        self._auto_rename_enabled = enabled
        self._auto_rename_pattern = pattern
        self.auto_rename_fields_input.setText(pattern)
        self.auto_rename_config_changed.emit(enabled, pattern)
        self.notify_requested.emit("自动重命名配置已保存", "success")

    def _copy_auto_rename_token(self, token: str):
        """复制自动重命名占位符到剪贴板"""
        placeholder = "{" + token + "}"
        QApplication.clipboard().setText(placeholder)
        self.notify_requested.emit(f"已复制占位符 {placeholder}", "success")

    def _on_auto_token_search_changed(self, text: str):
        """按关键字筛选可用占位符（token/中文说明均可匹配）"""
        keyword = (text or "").strip().lower()
        for token, desc, token_btn in self._auto_token_items:
            if not keyword:
                visible = True
            else:
                visible = (keyword in token.lower()) or (keyword in desc.lower())
            token_btn.setVisible(visible)

    def _on_apply_template(self):
        """套用模板到重命名输入框"""
        template_name = self.template_combo.currentText().strip()
        if not template_name or template_name == "选择模板":
            return

        pattern = self._rename_templates.get(template_name)
        if not pattern:
            return
        self.rename_input.setText(self._render_template(pattern))

    def _on_save_template(self):
        """保存新模板"""
        template_name = self.template_name_input.text().strip()
        pattern = self.template_pattern_input.text().strip()

        if not template_name:
            QMessageBox.information(self, "提示", "请输入模板名称")
            return
        if not pattern:
            QMessageBox.information(self, "提示", "请输入模板内容")
            return

        self._rename_templates[template_name] = pattern
        self._save_rename_templates()
        self._refresh_template_combo(select_name=template_name)

        self.template_name_input.clear()
        self.template_pattern_input.clear()
        self.notify_requested.emit(f"模板「{template_name}」已保存", "success")

    def _on_delete_template(self):
        """删除当前选择模板"""
        template_name = self.template_combo.currentText().strip()
        if not template_name or template_name == "选择模板":
            return

        if template_name in self._rename_templates:
            del self._rename_templates[template_name]
            self._save_rename_templates()
            self._refresh_template_combo()
            self.notify_requested.emit(f"模板「{template_name}」已删除", "success")

    def _on_add_tag(self):
        """新增分类词"""
        tag = self._normalize_tag(self.tag_input.text())
        if not tag:
            QMessageBox.information(self, "提示", "请输入分类词")
            return
        if tag in self._rename_tags:
            self.notify_requested.emit(f"分类词「{tag}」已存在", "warning")
            return

        self._rename_tags.append(tag)
        self._save_rename_templates()
        self._refresh_tag_buttons()
        self.tag_input.clear()

    def _refresh_tag_buttons(self):
        """刷新分类词按钮列表（右键删除 + 点击追加到文件名）"""
        normalized_tags: List[str] = []
        for tag in self._rename_tags:
            normalized = self._normalize_tag(tag)
            if normalized and normalized not in normalized_tags:
                normalized_tags.append(normalized)
        self._rename_tags = normalized_tags

        while self.tag_layout.count():
            item = self.tag_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._rename_tags:
            hint = QLabel("暂无分类词，可在上方新增", self.tag_panel)
            hint.setStyleSheet("font-size: 11px; color: #888;")
            self.tag_layout.addWidget(hint, 0, 0)
            return

        max_cols = 3
        for i, tag in enumerate(self._rename_tags):
            row = i // max_cols
            col = i % max_cols
            btn = QPushButton(tag, self.tag_panel)
            btn.clicked.connect(lambda _, t=tag: self._append_tag_to_name(t))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, t=tag, b=btn: self._show_tag_context_menu(b, t)
            )
            self.tag_layout.addWidget(btn, row, col)

    def _show_tag_context_menu(self, btn, tag: str):
        menu = QMenu(self)
        delete_action = menu.addAction("删除")
        action = menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))
        if action is delete_action:
            self._delete_tag_by_value(tag)

    def _delete_tag_by_value(self, tag: str):
        """删除指定分类词（右键菜单）"""
        normalized = self._normalize_tag(tag)
        if not normalized or normalized not in self._rename_tags:
            return
        self._rename_tags.remove(normalized)
        self._save_rename_templates()
        self._refresh_tag_buttons()
        self.notify_requested.emit(f"分类词「{normalized}」已删除", "success")

    def _append_tag_to_name(self, tag: str):
        """将分类词追加到重命名输入框尾部"""
        normalized_tag = self._normalize_tag(tag)
        if not normalized_tag:
            return

        current_name = self.rename_input.text().strip()
        if not current_name:
            self.rename_input.setText(normalized_tag.lstrip("_"))
            return

        if current_name.endswith(normalized_tag) or current_name.endswith(
            normalized_tag.lstrip("_")
        ):
            return

        self.rename_input.setText(f"{current_name}{normalized_tag}")

    def _render_template(self, pattern: str) -> str:
        """渲染模板字符串（与自动重命名同一套占位符，渲染器由页面注入）。"""
        template = (pattern or "").strip()
        if not template:
            return ""

        current_file = self._current_file
        if current_file is None:
            return template

        if self._pattern_renderer is None:
            return template

        try:
            return self._pattern_renderer(template, current_file).strip()
        except Exception as e:
            logger.log_exception(e, "渲染重命名模板失败")
            return template

    def _find_invalid_auto_rename_placeholders(self, pattern: str) -> List[str]:
        """检查模板占位符合法性：{token} 形式 + token 在允许列表内。"""
        text = pattern or ""
        invalid: List[str] = []
        allowed = set(self.AUTO_RENAME_AVAILABLE_TOKENS)

        for token in re.findall(r"\{([^{}]+)\}", text):
            normalized = token.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
                invalid.append("{" + token + "}")
                continue
            if normalized not in allowed:
                invalid.append("{" + token + "}")

        if text.count("{") != text.count("}"):
            invalid.append("大括号未成对")

        broken_left = re.findall(r"\{[^{}]*$", text)
        if broken_left:
            invalid.append("存在未闭合的 {token}")

        unique_invalid: List[str] = []
        for item in invalid:
            if item not in unique_invalid:
                unique_invalid.append(item)
        return unique_invalid

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        """标准化分类词：统一为 `_词` 格式"""
        raw = (tag or "").strip()
        if not raw:
            return ""

        core = raw.lstrip("_").strip()
        if not core:
            return ""

        return f"_{core}"

    def _load_rename_templates(self):
        """加载重命名模板与分类词（config.json「image_sorter」节）"""
        try:
            templates = cfg.get_dict("rename_templates")
            if isinstance(templates, dict):
                self._rename_templates = {
                    str(k): str(v) for k, v in templates.items() if isinstance(v, str)
                }
            self._rename_tags = [
                str(t) for t in cfg.get_list("rename_tags") if str(t).strip()
            ]

            if not self._rename_templates:
                self._rename_templates = dict(self.DEFAULT_RENAME_TEMPLATES)
                self._save_rename_templates()
        except Exception as e:
            logger.log_exception(e, "加载重命名模板失败")
            self._rename_templates = dict(self.DEFAULT_RENAME_TEMPLATES)
            self._rename_tags = []

    def _save_rename_templates(self):
        """保存重命名模板与分类词"""
        try:
            cfg.set_dict("rename_templates", dict(self._rename_templates))
            cfg.set_list("rename_tags", list(self._rename_tags))
        except Exception as e:
            logger.log_exception(e, "保存重命名模板失败")

    def _refresh_template_combo(self, select_name: Optional[str] = None):
        """刷新模板下拉列表"""
        self.template_combo.clear()
        self.template_combo.addItem("选择模板")

        template_names = sorted(self._rename_templates.keys())
        for name in template_names:
            self.template_combo.addItem(name)

        if select_name and select_name in self._rename_templates:
            index = template_names.index(select_name) + 1
            self.template_combo.setCurrentIndex(index)
        else:
            self.template_combo.setCurrentIndex(0)
