"""图片筛选分类页 · 文件夹管理面板（阶段三：分类目标文件夹卡片流）。

移植自源项目 ui/folder_panel.py（qfluentwidgets → 原生 QWidget+QSS）：
- DragSortFolderList  拖拽排序 + 外部文件拖入分类
- FolderPanel         内置 4（默认/收藏/删除/重复文件）+ 自定义文件夹卡片
  （QPainter 图标：emoji + 名称 + 统计 + 数字键角标）、面包屑多级导航、
  右键菜单（资源管理器/清空）、创建/重命名/删除、复检与返回源目录入口

与源版本的差异：
- CardWidget/PrimaryPushButton/ComboBox → QWidget/QPushButton/QComboBox + QSS
- 颜色一律浅色规范 hex，theme 自动重映射；QPainter 图标按 theme.is_dark() 现画
- folder_order.json 存 settings.get_data_root_dir()（config/image_sorter/）
- MessageBox → QMessageBox（轻提示由页面层 app.toast 承担）
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from gui_qt import theme

from image_sorter import settings as cfg
from image_sorter.core.folder_manager import FolderManager
from image_sorter._log import logger


# ==================== 可拖拽排序的文件夹列表 ====================

class DragSortFolderList(QListWidget):
    """自定义可拖拽排序的文件夹列表控件（内部排序 + 外部文件拖入）"""

    order_changed = Signal()

    def __init__(self, panel: "FolderPanel", parent=None):
        super().__init__(parent)
        self._panel = panel
        self._drag_row: int = -1
        self._drop_highlight_item: Optional[QListWidgetItem] = None
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setSortingEnabled(False)

    def _is_external_file_drag(self, event) -> bool:
        mime = event.mimeData()
        if mime is None:
            return False
        return event.source() != self and mime.hasUrls()

    def _clear_drop_highlight(self):
        if self._drop_highlight_item is not None:
            folder_name = self._drop_highlight_item.data(Qt.ItemDataRole.UserRole)
            self._drop_highlight_item.setToolTip(folder_name or "")
            self._drop_highlight_item = None
        QToolTip.hideText()

    def dragEnterEvent(self, event):
        if self._is_external_file_drag(event):
            event.acceptProposedAction()
            return
        if event.source() == self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._is_external_file_drag(event):
            event.acceptProposedAction()
            target_item = self.itemAt(event.position().toPoint())
            if target_item != self._drop_highlight_item:
                self._clear_drop_highlight()
                self._drop_highlight_item = target_item
            if target_item:
                folder_name = target_item.data(Qt.ItemDataRole.UserRole)
                tip_text = f'将放入 "{folder_name}" 文件夹'
                target_item.setToolTip(tip_text)
                QToolTip.showText(
                    self.viewport().mapToGlobal(event.position().toPoint()),
                    tip_text,
                    self,
                )
            return
        if event.source() == self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._clear_drop_highlight()
        super().dragLeaveEvent(event)

    def startDrag(self, supportedActions):
        # 记录拖拽起始行，避免 icon 模式下默认 drop 产生覆盖/跳位
        indexes = self.selectedIndexes()
        if not indexes:
            return

        self._drag_row = indexes[0].row()
        mime_data = self.model().mimeData(indexes)
        if mime_data is None:
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)

        current_item = self.currentItem()
        if current_item and not current_item.icon().isNull():
            drag.setPixmap(current_item.icon().pixmap(self.iconSize()))

        # 自定义拖拽启动，避免默认实现自动清理源项
        drag.exec(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        # --- 外部文件拖入 → 分类到目标文件夹 ---
        if self._is_external_file_drag(event):
            self._clear_drop_highlight()
            target_item = self.itemAt(event.position().toPoint())
            if target_item is None:
                event.ignore()
                return
            folder_path_data = target_item.data(Qt.ItemDataRole.UserRole + 3)
            if not folder_path_data:
                event.ignore()
                return
            file_paths = []
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local:
                    file_paths.append(local)
            if file_paths:
                event.acceptProposedAction()
                self._panel.file_dropped_to_folder.emit(file_paths, str(folder_path_data))
            else:
                event.ignore()
            return

        # --- 内部拖拽重排序 ---
        if event.source() != self:
            event.ignore()
            return

        source_row = self._drag_row
        self._drag_row = -1

        if source_row < 0 or source_row >= self.count():
            event.ignore()
            return

        drop_point = event.position().toPoint()
        target_item = self.itemAt(drop_point)

        if target_item is None:
            insert_row = self.count()
        else:
            target_row = self.row(target_item)
            target_rect = self.visualItemRect(target_item)
            insert_row = target_row + (1 if drop_point.x() > target_rect.center().x() else 0)

        if insert_row > source_row:
            insert_row -= 1

        if insert_row == source_row:
            event.acceptProposedAction()
            return

        item = self.takeItem(source_row)
        if item is None:
            event.ignore()
            return

        self.insertItem(insert_row, item)
        self.setCurrentItem(item)

        event.acceptProposedAction()
        self.order_changed.emit()

    def wheelEvent(self, event):
        # 强制将滚轮手势映射为横向滚动（单行图标列表）
        hbar = self.horizontalScrollBar()
        if hbar is None:
            super().wheelEvent(event)
            return

        angle = event.angleDelta()
        scroll_delta = angle.x() if angle.x() != 0 else angle.y()
        if scroll_delta == 0:
            super().wheelEvent(event)
            return

        step = int(scroll_delta / 3)
        if step == 0:
            step = 1 if scroll_delta > 0 else -1
        hbar.setValue(hbar.value() - step)

        vbar = self.verticalScrollBar()
        if vbar is not None and vbar.value() != 0:
            vbar.setValue(0)
        event.accept()


# ==================== 文件夹管理面板 ====================

class FolderPanel(QWidget):
    """文件夹管理面板（分类目标 + 多级导航 + 复检入口）"""

    folder_selected = Signal(str)            # 文件夹选中（相对工作目录路径）
    folder_created = Signal(str)             # 文件夹创建（相对路径）
    folder_renamed = Signal(str, str)        # 文件夹重命名（旧相对, 新相对）
    folder_deleted = Signal(str)             # 文件夹删除（相对路径）
    folder_preview_requested = Signal(str)   # 左侧复检请求（相对路径）
    restore_source_requested = Signal()      # 返回源目录请求
    folder_clear_requested = Signal(str)      # 清空文件夹内容请求（绝对路径）
    file_dropped_to_folder = Signal(list, str)  # 拖拽文件到文件夹（路径列表, 文件夹绝对路径）

    BUILTIN_ICONS = {"默认": "📁", "收藏": "⭐", "删除": "🗑️", "重复文件": "📋"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder_manager: Optional[FolderManager] = None
        self._selected_folder: Optional[str] = None  # 相对工作目录路径
        self._sort_by = "custom"  # custom / time / name
        self._current_path: List[str] = []  # 面包屑路径
        self._custom_order: Dict[str, list] = {}
        self._icon_theme_dark: Optional[bool] = None

        self._init_ui()
        self._load_custom_order()

    # ---------- UI ----------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 面包屑导航
        breadcrumb_layout = QHBoxLayout()
        self.back_btn = QPushButton("← 返回")
        self.back_btn.clicked.connect(self._on_go_back)
        self.back_btn.setEnabled(False)
        breadcrumb_layout.addWidget(self.back_btn)

        self.breadcrumb_label = QLabel("/")
        self.breadcrumb_label.setStyleSheet("font-size: 12px; color: #6b7280;")
        breadcrumb_layout.addWidget(self.breadcrumb_label)
        breadcrumb_layout.addStretch()
        layout.addLayout(breadcrumb_layout)

        # 标题和排序
        header_layout = QHBoxLayout()
        self.title_label = QLabel("📁 分类文件夹")
        self.title_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["自定义", "按时间", "按名称"])
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        header_layout.addWidget(self.sort_combo)
        layout.addLayout(header_layout)

        # 文件夹卡片列表（横向单行图标模式）
        self.folder_list = DragSortFolderList(self)
        self.folder_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.folder_list.setIconSize(QSize(120, 120))
        self.folder_list.setSpacing(10)
        self.folder_list.setFlow(QListWidget.Flow.LeftToRight)
        self.folder_list.setWrapping(False)
        self.folder_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.folder_list.setGridSize(QSize(130, 150))
        self.folder_list.setMovement(QListWidget.Movement.Snap)
        self.folder_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.folder_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        row_height = max(self.folder_list.gridSize().height() + 24, 180)
        self.folder_list.setFixedHeight(row_height)
        self.folder_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.folder_list.itemClicked.connect(self._on_folder_clicked)
        self.folder_list.itemDoubleClicked.connect(self._on_folder_double_clicked)
        self.folder_list.order_changed.connect(self._on_folder_order_changed)
        self._update_drag_mode()

        self.folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_list.customContextMenuRequested.connect(self._show_folder_context_menu)
        layout.addWidget(self.folder_list)

        # 未设置工作目录提示
        self.empty_hint_label = QLabel("尚未设置分类工作目录（分类时将自动弹出选择）")
        self.empty_hint_label.setStyleSheet("color: #6b7280; padding: 12px;")
        self.empty_hint_label.setWordWrap(True)
        self.empty_hint_label.hide()
        layout.addWidget(self.empty_hint_label)

        # 操作按钮
        button_layout = QHBoxLayout()
        self.create_btn = QPushButton("➕ 创建")
        self.create_btn.clicked.connect(self._on_create_folder)
        button_layout.addWidget(self.create_btn)

        self.rename_btn = QPushButton("✏️ 重命名")
        self.rename_btn.clicked.connect(self._on_rename_folder)
        button_layout.addWidget(self.rename_btn)

        self.delete_btn = QPushButton("🗑️ 删除")
        self.delete_btn.clicked.connect(self._on_delete_folder)
        button_layout.addWidget(self.delete_btn)

        self.preview_btn = QPushButton("👀 复检此夹")
        self.preview_btn.clicked.connect(self._on_preview_folder)
        button_layout.addWidget(self.preview_btn)

        self.restore_btn = QPushButton("↩️ 返回源目录")
        self.restore_btn.clicked.connect(self._on_restore_sources)
        button_layout.addWidget(self.restore_btn)

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(self.refresh_folders)
        button_layout.addWidget(self.refresh_btn)
        layout.addLayout(button_layout)

        self.folder_list.setStyleSheet(
            "QListWidget { background-color: #ffffff; border: 1px solid #dfe4ec;"
            " border-radius: 6px; }"
        )

    # ---------- 数据接入 ----------

    def set_folder_manager(self, folder_manager: FolderManager):
        self.folder_manager = folder_manager
        self.refresh_folders()

    def refresh_if_theme_changed(self):
        """主题切换后重画卡片图标（QPainter 图标不经过 QSS 重映射）。"""
        if self._icon_theme_dark != theme.is_dark():
            self.refresh_folders()

    # ---------- 列表刷新 ----------

    def refresh_folders(self):
        """刷新文件夹列表"""
        if not self.folder_manager or self.folder_manager._work_dir is None:
            self.folder_list.clear()
            self._selected_folder = None
            self.empty_hint_label.show()
            return
        self.empty_hint_label.hide()
        self._icon_theme_dark = theme.is_dark()

        self.folder_list.clear()
        self._selected_folder = None

        current_path = self.get_current_folder_path()

        if not self._current_path:
            if self._sort_by == "custom":
                folders = self.folder_manager.get_folders(sort_by="name")
                folders = self._apply_custom_order(folders)
            else:
                folders = self.folder_manager.get_folders(sort_by=self._sort_by)
        else:
            folders = []
            if current_path.exists():
                for item in current_path.iterdir():
                    if item.is_dir():
                        folders.append((item.name, False, item))
                if self._sort_by == "custom":
                    folders.sort(key=lambda x: x[0])
                    folders = self._apply_custom_order(folders)
                elif self._sort_by == "time":
                    folders.sort(key=lambda x: x[2].stat().st_ctime, reverse=True)
                elif self._sort_by == "name":
                    folders.sort(key=lambda x: x[0])

        for i, (folder_name, is_builtin, folder_path) in enumerate(folders):
            item = QListWidgetItem()
            folder_count, file_count = self._count_folder_stats(folder_path)
            item.setData(Qt.ItemDataRole.UserRole, folder_name)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_builtin)
            item.setData(Qt.ItemDataRole.UserRole + 2, self._build_relative_folder_path(folder_name))
            item.setData(Qt.ItemDataRole.UserRole + 3, str(folder_path))
            item.setData(Qt.ItemDataRole.UserRole + 4, int(folder_count))
            item.setData(Qt.ItemDataRole.UserRole + 5, int(file_count))
            quick_index = i + 1 if i < 9 else None
            self._update_folder_item_visuals(
                item, folder_name, is_builtin, folder_count, file_count, quick_index=quick_index
            )
            self.folder_list.addItem(item)

    def _on_folder_clicked(self, item: QListWidgetItem):
        folder_name = item.data(Qt.ItemDataRole.UserRole)
        folder_relative_path = item.data(Qt.ItemDataRole.UserRole + 2) or \
            self._build_relative_folder_path(folder_name)

        for i in range(self.folder_list.count()):
            self.folder_list.item(i).setBackground(Qt.GlobalColor.transparent)
        item.setBackground(QColor(76, 175, 80, 50))

        self._selected_folder = folder_relative_path
        self.folder_selected.emit(folder_relative_path)
        logger.log_operation("文件夹选中", folder_relative_path)

    @staticmethod
    def _count_folder_stats(folder_path: Path) -> Tuple[int, int]:
        """递归统计目录内的文件夹/文件数量。"""
        import os
        folder_count = 0
        file_count = 0
        try:
            for _, dirs, files in os.walk(folder_path):
                folder_count += len(dirs)
                file_count += len(files)
        except Exception:
            return (0, 0)
        return (folder_count, file_count)

    def _build_folder_card_icon(
        self,
        folder_name: str,
        is_builtin: bool,
        folder_count: int,
        file_count: int,
        quick_index: Optional[int] = None,
    ) -> QIcon:
        """构建文件夹卡片图标（emoji + 名称 + 统计 + 数字键角标）。"""
        dark = theme.is_dark()
        pixmap = QPixmap(120, 120)
        pixmap.fill(QColor(30, 30, 30) if dark else QColor(255, 255, 255))

        painter = QPainter(pixmap)

        try:
            if is_builtin:
                if folder_name == "收藏":
                    painter.fillRect(0, 0, 120, 120, QColor(255, 235, 59, 100))
                elif folder_name == "删除":
                    painter.fillRect(0, 0, 120, 120, QColor(244, 67, 54, 100))
                elif folder_name == "重复文件":
                    painter.fillRect(0, 0, 120, 120, QColor(33, 150, 243, 100))
                else:
                    painter.fillRect(0, 0, 120, 120, QColor(76, 175, 80, 100))
            else:
                painter.fillRect(0, 0, 120, 120, QColor(158, 158, 158, 100))

            # emoji 图标（Segoe UI Emoji 保证彩色字形）
            icon_font = painter.font()
            icon_font.setFamily("Segoe UI Emoji")
            icon_font.setPointSize(44)
            painter.setFont(icon_font)
            emoji = self.BUILTIN_ICONS.get(folder_name, "📂") if is_builtin else "📂"
            painter.drawText(0, 0, 120, 90, Qt.AlignmentFlag.AlignCenter, emoji)

            # 名称
            name_font = painter.font()
            name_font.setFamily("Microsoft YaHei")
            name_font.setPointSize(10)
            painter.setFont(name_font)
            painter.setPen(QColor(230, 230, 230) if dark else QColor(0, 0, 0))
            name_text = folder_name[:10] + "..." if len(folder_name) > 10 else folder_name
            painter.drawText(0, 86, 120, 18, Qt.AlignmentFlag.AlignCenter, name_text)

            # 统计
            stat_font = painter.font()
            stat_font.setPointSize(8)
            painter.setFont(stat_font)
            stat_text = f"(夹:{folder_count} 件:{file_count})"
            painter.drawText(0, 104, 120, 16, Qt.AlignmentFlag.AlignCenter, stat_text)

            # 前九个文件夹绘制快捷角标（数字键 1~9）
            if isinstance(quick_index, int) and 1 <= quick_index <= 9:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                badge_x, badge_y, badge_size = 88, 4, 28
                badge_bg = QColor(33, 150, 243, 220) if dark else QColor(25, 118, 210, 230)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(badge_bg)
                painter.drawEllipse(badge_x, badge_y, badge_size, badge_size)

                badge_font = painter.font()
                badge_font.setPointSize(11)
                badge_font.setBold(True)
                painter.setFont(badge_font)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(
                    badge_x, badge_y, badge_size, badge_size,
                    Qt.AlignmentFlag.AlignCenter, str(quick_index),
                )
        finally:
            painter.end()
        return QIcon(pixmap)

    def _update_folder_item_visuals(
        self,
        item: QListWidgetItem,
        folder_name: str,
        is_builtin: bool,
        folder_count: int,
        file_count: int,
        quick_index: Optional[int] = None,
    ):
        item.setIcon(
            self._build_folder_card_icon(
                folder_name, is_builtin, folder_count, file_count, quick_index=quick_index
            )
        )
        item.setText("")
        quick_tip = (
            f"\n数字键{quick_index}: 快速分类到此夹"
            if isinstance(quick_index, int) and 1 <= quick_index <= 9 else ""
        )
        item.setToolTip(f"{folder_name}\n文件夹: {folder_count} | 文件: {file_count}{quick_tip}")

    def refresh_visible_folder_counts(self):
        """仅刷新当前可见卡片的文件数量，避免全量重建列表。"""
        if not self.folder_manager or self.folder_manager._work_dir is None:
            return

        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            if item is None:
                continue

            folder_name = item.data(Qt.ItemDataRole.UserRole)
            if not folder_name:
                continue

            is_builtin = bool(item.data(Qt.ItemDataRole.UserRole + 1))
            relative_path = item.data(Qt.ItemDataRole.UserRole + 2) or \
                self._build_relative_folder_path(str(folder_name))
            folder_path = self.folder_manager.work_dir / Path(str(relative_path))
            folder_count, file_count = self._count_folder_stats(folder_path)
            item.setData(Qt.ItemDataRole.UserRole + 4, int(folder_count))
            item.setData(Qt.ItemDataRole.UserRole + 5, int(file_count))
            quick_index = i + 1 if i < 9 else None
            self._update_folder_item_visuals(
                item, str(folder_name), is_builtin, folder_count, file_count,
                quick_index=quick_index,
            )

    # ---------- 排序 ----------

    def _on_sort_changed(self, index: int):
        self._sort_by = ["custom", "time", "name"][index]
        self._update_drag_mode()
        self.refresh_folders()

    def _update_drag_mode(self):
        drag_enabled = self._sort_by == "custom"
        self.folder_list.setDragEnabled(drag_enabled)
        self.folder_list.setAcceptDrops(drag_enabled)
        if drag_enabled:
            self.folder_list.setMovement(QListWidget.Movement.Snap)
            self.folder_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        else:
            self.folder_list.setMovement(QListWidget.Movement.Static)
            self.folder_list.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)

    # ---------- 右键菜单 ----------

    def _show_folder_context_menu(self, pos):
        item = self.folder_list.itemAt(pos)
        if item is None or not self.folder_manager:
            return

        folder_name = item.data(Qt.ItemDataRole.UserRole)
        relative_path = item.data(Qt.ItemDataRole.UserRole + 2) or \
            self._build_relative_folder_path(folder_name)
        folder_path = self.folder_manager.work_dir / Path(str(relative_path))

        if not folder_path.exists():
            return

        menu = QMenu(self)
        explorer_action = menu.addAction("在资源管理器中查看")
        clear_action = menu.addAction("清空文件夹内容")
        chosen = menu.exec(self.folder_list.viewport().mapToGlobal(pos))
        if chosen == explorer_action:
            try:
                subprocess.Popen(["explorer", str(folder_path)])
            except Exception as e:
                logger.log_exception(e, f"打开资源管理器失败: {folder_path}")
        elif chosen == clear_action:
            reply = QMessageBox.question(
                self,
                "确认清空",
                f"确定要清空文件夹 '{folder_name}' 内的所有文件吗？\n"
                f"清空后这些文件的 MD5 将被记住，下次导入时自动跳过。",
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.folder_clear_requested.emit(str(folder_path))

    # ---------- 创建 / 重命名 / 删除 ----------

    def _on_create_folder(self):
        if not self.folder_manager:
            return

        folder_name, ok = QInputDialog.getText(self, "创建文件夹", "请输入文件夹名称:")
        if not (ok and folder_name):
            return

        folder_name = folder_name.strip()
        if not self._is_valid_folder_name(folder_name):
            QMessageBox.warning(self, "失败", "文件夹名称不能包含 / \\ .. 等字符")
            return
        parent_path = self.get_current_folder_path()
        folder_path = parent_path / folder_name

        if folder_path.exists():
            QMessageBox.warning(self, "失败", f"文件夹 '{folder_name}' 已存在")
            return

        try:
            folder_path.mkdir(parents=True, exist_ok=False)
            self.refresh_folders()
            self.folder_created.emit(self._build_relative_folder_path(folder_name))
        except Exception as e:
            logger.log_exception(e, f"创建文件夹失败: {folder_path}")
            QMessageBox.warning(self, "失败", f"文件夹 '{folder_name}' 创建失败")

    def _on_rename_folder(self):
        if not self.folder_manager:
            return

        selected_items = self.folder_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要重命名的文件夹")
            return

        item = selected_items[0]
        old_name = item.data(Qt.ItemDataRole.UserRole)
        is_builtin = item.data(Qt.ItemDataRole.UserRole + 1)

        if is_builtin:
            QMessageBox.information(self, "提示", "系统内置文件夹禁止重命名")
            return

        new_name, ok = QInputDialog.getText(self, "重命名文件夹", "请输入新的文件夹名称:", text=old_name)
        if not (ok and new_name and new_name != old_name):
            return

        new_name = new_name.strip()
        if not self._is_valid_folder_name(new_name):
            QMessageBox.warning(self, "失败", "文件夹名称不能包含 / \\ .. 等字符")
            return
        current_folder_path = self.get_current_folder_path()
        old_path = current_folder_path / old_name
        new_path = current_folder_path / new_name

        if not old_path.exists():
            QMessageBox.warning(self, "失败", f"文件夹 '{old_name}' 不存在")
            return
        if new_path.exists():
            QMessageBox.warning(self, "失败", f"文件夹 '{new_name}' 已存在")
            return

        try:
            old_path.rename(new_path)

            # 同步自定义排序配置：当前目录顺序表中替换名称
            current_key = self._get_current_relative_key()
            order_list = self._custom_order.get(current_key)
            changed = False
            if isinstance(order_list, list) and old_name in order_list:
                order_list = list(order_list)
                order_list[order_list.index(old_name)] = new_name
                self._custom_order[current_key] = order_list
                changed = True

            # 子目录顺序键整体迁移
            old_rel_key = self._build_relative_folder_path(old_name)
            new_rel_key = self._build_relative_folder_path(new_name)
            if old_rel_key != new_rel_key:
                for key in list(self._custom_order.keys()):
                    if key == old_rel_key or str(key).startswith(old_rel_key + "/"):
                        suffix = str(key)[len(old_rel_key):]
                        new_key = new_rel_key + suffix
                        if new_key not in self._custom_order:
                            self._custom_order[new_key] = self._custom_order.pop(key)
                        else:
                            merged = list(self._custom_order.get(new_key) or []) + \
                                list(self._custom_order.get(key) or [])
                            dedup = []
                            for name in merged:
                                if name not in dedup:
                                    dedup.append(name)
                            self._custom_order[new_key] = dedup
                            self._custom_order.pop(key, None)
                        changed = True

            if changed:
                self._save_custom_order()

            self.refresh_folders()
            logger.log_operation("重命名文件夹", f"{old_path} -> {new_path}")
            self.folder_renamed.emit(
                self._build_relative_folder_path(old_name),
                self._build_relative_folder_path(new_name),
            )
        except Exception as e:
            logger.log_exception(e, f"重命名文件夹失败: {old_path}")
            QMessageBox.warning(self, "失败", "文件夹重命名失败")

    def _on_delete_folder(self):
        if not self.folder_manager:
            return

        selected_items = self.folder_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要删除的文件夹")
            return

        item = selected_items[0]
        folder_name = item.data(Qt.ItemDataRole.UserRole)
        is_builtin = item.data(Qt.ItemDataRole.UserRole + 1)

        if is_builtin:
            QMessageBox.information(self, "提示", "系统内置文件夹禁止删除")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除文件夹 '{folder_name}' 吗？\n此操作将删除文件夹内的所有文件。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        current_folder_path = self.get_current_folder_path()
        folder_path = current_folder_path / folder_name

        if not folder_path.exists():
            QMessageBox.warning(self, "失败", f"文件夹 '{folder_name}' 不存在")
            return

        try:
            shutil.rmtree(folder_path)

            current_key = self._get_current_relative_key()
            changed = False
            order_list = self._custom_order.get(current_key)
            if isinstance(order_list, list) and folder_name in order_list:
                self._custom_order[current_key] = [n for n in order_list if n != folder_name]
                changed = True

            deleted_rel_key = self._build_relative_folder_path(folder_name)
            for key in list(self._custom_order.keys()):
                if key == deleted_rel_key or str(key).startswith(deleted_rel_key + "/"):
                    self._custom_order.pop(key, None)
                    changed = True

            if changed:
                self._save_custom_order()

            self.refresh_folders()
            logger.log_operation("删除文件夹", str(folder_path))
            self.folder_deleted.emit(self._build_relative_folder_path(folder_name))
        except Exception as e:
            logger.log_exception(e, f"删除文件夹失败: {folder_path}")
            QMessageBox.warning(self, "失败", "文件夹删除失败")

    # ---------- 复检 / 返回源目录 ----------

    def _resolve_target_relative_path(self) -> Optional[str]:
        selected_items = self.folder_list.selectedItems()
        if selected_items:
            item = selected_items[0]
            folder_name = item.data(Qt.ItemDataRole.UserRole)
            return item.data(Qt.ItemDataRole.UserRole + 2) or \
                self._build_relative_folder_path(folder_name)
        if self._current_path:
            return "/".join(self._current_path)
        return None

    def _on_preview_folder(self):
        """左侧复检：将文件夹媒体载入左侧列表继续分类。"""
        if not self.folder_manager:
            return
        relative_path = self._resolve_target_relative_path()
        if not relative_path:
            QMessageBox.information(self, "提示", "请先选择要复检的文件夹")
            return
        self.folder_preview_requested.emit(str(relative_path))
        logger.log_operation("请求左侧复检", str(relative_path))

    def _on_restore_sources(self):
        self.restore_source_requested.emit()

    # ---------- 选中 / 导航 ----------

    def get_selected_folder(self) -> Optional[str]:
        return self._selected_folder

    def get_visible_folders(self) -> List[Tuple[str, bool, Path, str]]:
        """当前面板可见文件夹（保持显示顺序）。"""
        if not self.folder_manager:
            return []

        folders: List[Tuple[str, bool, Path, str]] = []
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            folder_name = item.data(Qt.ItemDataRole.UserRole)
            is_builtin = bool(item.data(Qt.ItemDataRole.UserRole + 1))
            relative_path = item.data(Qt.ItemDataRole.UserRole + 2) or \
                self._build_relative_folder_path(folder_name)
            absolute_path = self.folder_manager.work_dir / Path(relative_path)
            folders.append((folder_name, is_builtin, absolute_path, relative_path))
        return folders

    def select_folder_by_index(self, index: int) -> bool:
        """根据显示索引选中文件夹（用于数字角标快速定位）。"""
        if index < 0 or index >= self.folder_list.count():
            return False
        item = self.folder_list.item(index)
        if item is None:
            return False
        self.folder_list.setCurrentItem(item)
        self._on_folder_clicked(item)
        return True

    def _on_folder_double_clicked(self, item: QListWidgetItem):
        self._enter_folder(item.data(Qt.ItemDataRole.UserRole))

    def enter_folder_by_index(self, index: int):
        """根据显示索引进入文件夹（用于小键盘导航）。"""
        visible_folders = self.get_visible_folders()
        if 0 <= index < len(visible_folders):
            self._enter_folder(visible_folders[index][0])

    def _enter_folder(self, folder_name: str):
        if not self.folder_manager:
            return

        self._current_path.append(folder_name)
        self._update_breadcrumb()
        self.refresh_folders()
        self.back_btn.setEnabled(True)

    def _on_go_back(self):
        if self._current_path:
            self._current_path.pop()
            self._update_breadcrumb()
            self.refresh_folders()
            if not self._current_path:
                self.back_btn.setEnabled(False)

    def can_go_back(self) -> bool:
        return bool(self._current_path)

    def go_back(self) -> bool:
        if not self.can_go_back():
            return False
        self._on_go_back()
        return True

    def _update_breadcrumb(self):
        self.breadcrumb_label.setText(
            " / " + " / ".join(self._current_path) if self._current_path else "/"
        )

    def get_current_folder_path(self) -> Path:
        if not self.folder_manager or self.folder_manager._work_dir is None:
            return Path()
        base_path = self.folder_manager.work_dir
        for folder_name in self._current_path:
            base_path = base_path / folder_name
        return base_path

    def _get_current_relative_key(self) -> str:
        return "/".join(self._current_path) if self._current_path else "/"

    def _build_relative_folder_path(self, folder_name: str) -> str:
        if not self._current_path:
            return folder_name
        return "/".join([*self._current_path, folder_name])

    @staticmethod
    def _is_valid_folder_name(name: str) -> bool:
        cleaned = str(name or "").strip()
        if not cleaned:
            return False
        if cleaned in (".", ".."):
            return False
        if "/" in cleaned or "\\" in cleaned:
            return False
        return True

    # ---------- 自定义排序持久化 ----------

    def _apply_custom_order(self, folders: list) -> list:
        current_key = self._get_current_relative_key()
        custom_order = self._custom_order.get(current_key, [])
        if not custom_order:
            return folders

        folder_dict = {f[0]: f for f in folders}

        ordered = []
        for name in custom_order:
            if name in folder_dict:
                ordered.append(folder_dict.pop(name))

        remaining = list(folder_dict.values())
        remaining.sort(key=lambda x: x[0])
        ordered.extend(remaining)
        return ordered

    def _on_folder_order_changed(self):
        if self._sort_by != "custom":
            return

        new_order = []
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            new_order.append(item.data(Qt.ItemDataRole.UserRole))

        current_key = self._get_current_relative_key()
        self._custom_order[current_key] = new_order
        self._save_custom_order()
        self._refresh_quick_badges_after_reorder()

    def _refresh_quick_badges_after_reorder(self):
        """拖拽重排后立即刷新 1~9 角标，保证角标始终与当前顺序一致。"""
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            if item is None:
                continue
            folder_name = item.data(Qt.ItemDataRole.UserRole)
            if not folder_name:
                continue
            is_builtin = bool(item.data(Qt.ItemDataRole.UserRole + 1))
            folder_count = int(item.data(Qt.ItemDataRole.UserRole + 4) or 0)
            file_count = int(item.data(Qt.ItemDataRole.UserRole + 5) or 0)
            quick_index = i + 1 if i < 9 else None
            self._update_folder_item_visuals(
                item, str(folder_name), is_builtin, folder_count, file_count,
                quick_index=quick_index,
            )

    def _load_custom_order(self):
        config_path = cfg.get_data_root_dir() / "folder_order.json"
        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)

                if isinstance(loaded, dict):
                    self._custom_order = {
                        str(key): value for key, value in loaded.items()
                        if isinstance(value, list)
                    }
                elif isinstance(loaded, list):
                    self._custom_order = {"/": loaded}
                else:
                    self._custom_order = {}
        except Exception as e:
            logger.error(f"加载文件夹顺序失败: {e}")
            self._custom_order = {}

    def _save_custom_order(self):
        config_dir = cfg.get_data_root_dir()
        config_path = config_dir / "folder_order.json"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self._custom_order, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存文件夹顺序失败: {e}")
