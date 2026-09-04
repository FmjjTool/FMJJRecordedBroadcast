"""图片筛选分类页 · 设置组件（阶段三）。

ShortcutMappingDialog：快捷键映射编辑对话框。
- 源项目 ui/shortcut_mapping_dialog.py 的 PySide6 移植
- qfluentwidgets（BodyLabel/PushButton/PrimaryPushButton/isDarkTheme）→ 原生
  QLabel/QPushButton + 浅色 hex QSS（theme 自动重映射深色）
- 持久化走 ShortcutManager（config.json「image_sorter」节 shortcuts 键）
"""

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from image_sorter.core.shortcut_manager import ShortcutManager


class ShortcutMappingDialog(QDialog):
    """快捷键映射设置对话框（单序列编辑 + 恢复默认 + 保存）。"""

    def __init__(self, shortcut_manager: ShortcutManager, parent=None):
        super().__init__(parent)
        self.shortcut_manager = shortcut_manager
        self._editors: Dict[str, QKeySequenceEdit] = {}
        self._initial_mapping: Dict[str, List[str]] = {}

        self._init_ui()
        self._load_values()

    def _init_ui(self):
        self.setWindowTitle("快捷键映射")
        self.setModal(True)
        self.resize(680, 520)

        layout = QVBoxLayout(self)

        tips = QLabel(
            "点击快捷键框后，直接按键录入（支持 Ctrl/Alt/Shift/Num 组合），无需手动输入",
            self,
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("color: #6b7280;")
        layout.addWidget(tips)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea { background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 4px; }"
        )
        self.container = QWidget(self.scroll)
        self.container.setStyleSheet("background: transparent;")
        form_layout = QFormLayout(self.container)
        form_layout.setContentsMargins(8, 8, 8, 8)
        form_layout.setSpacing(10)
        form_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        for action in ShortcutManager.DEFAULT_SHORTCUTS.keys():
            label_text = ShortcutManager.ACTION_LABELS.get(action, action)
            label = QLabel(label_text, self.container)
            row_widget = QWidget(self.container)
            row_widget.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            editor = QKeySequenceEdit(row_widget)
            editor.setMaximumSequenceLength(1)
            row_layout.addWidget(editor, 1)

            clear_btn = QPushButton("清空", row_widget)
            clear_btn.setFixedWidth(64)
            clear_btn.clicked.connect(editor.clear)
            row_layout.addWidget(clear_btn)

            form_layout.addRow(label, row_widget)
            self._editors[action] = editor

        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)

        button_layout = QHBoxLayout()
        reset_btn = QPushButton("恢复默认", self)
        reset_btn.clicked.connect(self._on_reset_default)
        button_layout.addWidget(reset_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("取消", self)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存", self)
        save_btn.clicked.connect(self._on_save)
        save_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: #ffffff; border: none;"
            " border-radius: 4px; padding: 6px 16px; font-weight: 600; }"
        )
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def _load_values(self):
        mapping = self.shortcut_manager.get_shortcuts()
        self._initial_mapping = {k: list(v) for k, v in mapping.items()}
        for action, editor in self._editors.items():
            shortcuts = mapping.get(action, [])
            if shortcuts:
                editor.setKeySequence(QKeySequence(shortcuts[0]))
            else:
                editor.clear()

    def _collect_mapping(self) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        invalid_entries: List[str] = []

        for action, editor in self._editors.items():
            shortcut = editor.keySequence().toString(
                QKeySequence.SequenceFormat.PortableText
            ).strip()
            if not shortcut:
                mapping[action] = list(ShortcutManager.DEFAULT_SHORTCUTS.get(action, []))
                continue

            if self.shortcut_manager.validate_shortcut(shortcut):
                original = self._initial_mapping.get(action, [])
                original_first = original[0] if original else ""
                if shortcut == original_first and len(original) > 1:
                    mapping[action] = list(original)
                else:
                    mapping[action] = [shortcut]
            else:
                label = ShortcutManager.ACTION_LABELS.get(action, action)
                invalid_entries.append(f"{label}: {shortcut}")
                mapping[action] = []

        if invalid_entries:
            preview = "\n".join(invalid_entries[:12])
            if len(invalid_entries) > 12:
                preview += f"\n... 另有 {len(invalid_entries) - 12} 项"
            raise ValueError(preview)

        return mapping

    def _on_reset_default(self):
        reply = QMessageBox.question(
            self,
            "恢复默认",
            "确认将所有快捷键恢复为默认配置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.shortcut_manager.reset_to_default()
            self._load_values()
        except Exception as e:
            QMessageBox.critical(self, "恢复失败", str(e))

    def _on_save(self):
        try:
            mapping = self._collect_mapping()
            self.shortcut_manager.save_shortcuts(mapping)
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "快捷键格式错误", str(e))
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
