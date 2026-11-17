# -*- coding: utf-8 -*-
"""把页面历史遗留的前导 emoji 结构图标统一转换为 line_icon 线形图标。

安装后自动作用于运行期创建的所有控件，页面源码无需逐个改写：

- `QPushButton` / `QToolButton`：去掉文案前导 emoji，改为 `setIcon`；
- `QLabel`：去掉前导 emoji，在文字左侧绘制小图标（保留原文字颜色与字体）；
- `QCheckBox` / `QRadioButton`：只去掉前导 emoji（Qt 复选框不支持图标）；
- `QComboBox` / `QTabWidget`：去掉条目前导 emoji，改为条目图标。

颜色始终取自主题角色（标题/条目 link，普通按钮 button-text，主按钮
highlighted-text，弱化标签 placeholder-text）；切主题后由 `refresh_all()` 重新生成。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QLabel, QPushButton,
    QRadioButton, QTabWidget, QToolButton, QWidget,
)

from gui_qt import theme
from gui_qt.navigation import line_icon

_ICON_SIZE_BUTTON = 16
_ICON_SIZE_LABEL = 14
_LABEL_GAP = 4

# emoji → line_icon 名称；变体选择符/零宽连接符在匹配前统一丢弃。
_EMOJI_ICONS = {
    "📁": "folder", "📂": "folder", "🗂": "folder",
    "📄": "document", "📃": "document", "📑": "document", "📇": "document",
    "🧾": "document", "📰": "document",
    "📋": "clipboard",
    "💾": "save",
    "🔄": "refresh", "♻": "refresh",
    "🔍": "search", "🔎": "search",
    "🗑": "trash",
    "⬇": "download", "📥": "download",
    "⬆": "upload", "📤": "upload",
    "📊": "chart", "📈": "chart",
    "✅": "check", "✔": "check", "☑": "check",
    "➕": "plus",
    "✕": "close", "✖": "close", "❌": "close",
    "✏": "edit", "📝": "edit", "✍": "edit",
    "📷": "image", "📸": "image", "🖼": "image", "🎞": "image",
    "🎬": "video", "🎥": "video", "📹": "video", "📺": "video",
    "🌐": "globe",
    "⚙": "settings", "🛠": "settings",
    "🎯": "target",
    "🚀": "rocket",
    "🔒": "lock",
    "🔓": "unlock",
    "❓": "question",
    "👤": "users", "👥": "users",
    "🔑": "key",
    "🛒": "cart", "🛍": "cart", "🏬": "cart",
    "📦": "package", "🎁": "package",
    "🕓": "clock",
    "💬": "message",
    "🎫": "ticket",
    "🎵": "music", "🎼": "music",
    "♥": "heart",
    "😀": "smile",
    "🎨": "palette",
    "🎲": "dice",
    "🗜": "compress",
    "✂": "scissors",
    "⭐": "star",
    "📅": "calendar",
    "🔗": "link",
    "🚚": "truck",
    "👁": "eye", "👀": "eye",
    "🧩": "chip",
    "🧹": "broom",
    "📡": "antenna",
    "🏷": "tag",
    "☀": "sun",
    "🌙": "moon",
    "🩺": "pulse",
    "🔴": "dot",
    "⬅": "arrow_left",
    "➡": "arrow",
    "⚠": "alert",
    "🤖": "robot",
    "⏸": "pause",
    "▶": "play", "⏹": "stop",
    "⏭": "skip_next", "⏮": "skip_prev",
    "⏺": "dot",
    "⏰": "clock", "⏳": "clock", "⏱": "clock", "🕘": "clock",
    "🌗": "moon", "🔁": "refresh", "↺": "refresh",
    "🔐": "lock", "🆑": "trash", "⛶": "expand", "⌨": "keyboard",
    "🏗": "settings", "🗄": "package", "📜": "document",
    "🎧": "music", "📱": "phone", "☁": "cloud", "🗓": "calendar",
    "🎤": "mic", "📕": "document", "📚": "document", "📌": "pin",
    "🔤": "list", "‹": "arrow_left", "←": "arrow_left",
    "↩": "arrow_left", "↪": "arrow", "➖": "minus", "⇄": "swap",
    "→": "arrow", "＋": "plus", "×": "close", "□": "close",
}

_VARIATION = dict.fromkeys(map(ord, "\ufe0f\ufe0e\u200d\u20e3"), None)
_EMOJI_ICONS = {key.translate(_VARIATION): value for key, value in _EMOJI_ICONS.items()}

_TOKEN_RE = re.compile(r"^(?P<token>[^\w\s\u4e00-\u9fff]+)(?P<rest>.*)$", re.S)

_installed = False
_orig_abstract_set_text = None
_orig_label_set_text = None
_orig_label_set_pixmap = None
_orig_push_set_icon = None
_orig_tool_set_icon = None


def _norm(text: str) -> str:
    return text.translate(_VARIATION)


def split_leading_emoji(text: str) -> tuple[str | None, str]:
    """拆分前导 emoji；返回 (line_icon 名称, 去 emoji 文本)，不认识则名称 None。"""
    if not text:
        return None, text
    stripped = text.lstrip()
    lead = text[: len(text) - len(stripped)]
    match = _TOKEN_RE.match(stripped)
    if match:
        name = _EMOJI_ICONS.get(_norm(match.group("token")))
        if name:
            rest = match.group("rest").lstrip()
            return name, lead + rest if rest else lead
    for size in (3, 2, 1):
        if size <= len(stripped):
            candidate = _norm(stripped[:size])
            if candidate in _EMOJI_ICONS:
                rest = stripped[size:].lstrip()
                if rest:
                    return _EMOJI_ICONS[candidate], lead + rest
    return None, text


def _color(role: str) -> str:
    pal = theme.current_palette()
    if role == "primary":
        return pal.color(QPalette.HighlightedText).name()
    if role == "muted":
        return pal.color(QPalette.PlaceholderText).name()
    if role == "button":
        return pal.color(QPalette.ButtonText).name()
    return pal.color(QPalette.Link).name()


def _button_role(button) -> str:
    return "primary" if button.property("variant") == "primary" else "button"


def _button_icon_size() -> QSize:
    return QSize(_ICON_SIZE_BUTTON, _ICON_SIZE_BUTTON)


def _paint_button_icon(button):
    name = getattr(button, "_line_icon_name", None)
    if not name:
        return
    button._line_busy = True
    try:
        button.setIcon(line_icon(name, _color(_button_role(button)), _ICON_SIZE_BUTTON))
        button.setIconSize(_button_icon_size())
    finally:
        button._line_busy = False


def _apply_button(button):
    if getattr(button, "_line_busy", False):
        return
    name, clean = split_leading_emoji(button.text())
    if name is None:
        if getattr(button, "_line_auto", False):
            button._line_auto = False
            button._line_icon_name = None
        return
    if clean != button.text():
        button._line_busy = True
        try:
            _orig_abstract_set_text(button, clean)
        finally:
            button._line_busy = False
    button._line_icon_name = name
    button._line_auto = True
    _paint_button_icon(button)


def _apply_check(button):
    if getattr(button, "_line_busy", False):
        return
    name, clean = split_leading_emoji(button.text())
    if name is not None and clean != button.text():
        button._line_busy = True
        try:
            _orig_abstract_set_text(button, clean)
        finally:
            button._line_busy = False


class _LabelKeeper(QObject):
    """标签尺寸变化时重新摆放左侧图标。"""

    def __init__(self, label: QLabel):
        super().__init__(label)
        self._label = label

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            _place_label_icon(self._label)
        return False


def _place_label_icon(label):
    child = getattr(label, "_line_icon_label", None)
    if child is None:
        return
    try:
        if label.text().strip():
            x = 0
        else:
            x = max(0, (label.width() - child.width()) // 2)
        child.move(x, max(0, (label.height() - child.height()) // 2))
    except RuntimeError:
        pass


def _clear_label_icon(label):
    child = getattr(label, "_line_icon_label", None)
    if child is None:
        label._line_icon_name = None
        return
    label._line_icon_label = None
    child.deleteLater()
    base = getattr(label, "_line_base_margins", None)
    if base is not None:
        label.setContentsMargins(*base)
    label.updateGeometry()
    label._line_icon_name = None


def _label_role(label) -> str:
    if label.objectName() == "navigationGroup" or label.property("role") in (
            "caption", "muted", "subtitle"):
        return "muted"
    return "title"


def _ensure_label_icon(label):
    child = getattr(label, "_line_icon_label", None)
    if child is None:
        child = QLabel(label)
        child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        child.setFixedSize(_ICON_SIZE_LABEL, _ICON_SIZE_LABEL)
        child.show()
        label._line_icon_label = child
        label._line_base_margins = label.contentsMargins()
        label._line_keeper = _LabelKeeper(label)
        label.installEventFilter(label._line_keeper)
    base = label._line_base_margins
    target_left = base.left() + (
        _ICON_SIZE_LABEL + _LABEL_GAP if label.text().strip() else 0)
    margins = label.contentsMargins()
    if margins.left() != target_left:
        label.setContentsMargins(
            target_left, margins.top(), margins.right(), margins.bottom())
        label.updateGeometry()
    label._line_icon_role = _label_role(label)
    child.setPixmap(line_icon(
        label._line_icon_name, _color(label._line_icon_role), _ICON_SIZE_LABEL
    ).pixmap(_ICON_SIZE_LABEL, _ICON_SIZE_LABEL))
    _place_label_icon(label)
    QTimer.singleShot(0, lambda target=label: _place_label_icon(target))


def _apply_label(label):
    if getattr(label, "_line_busy", False):
        return
    label._line_skip = False
    name, clean = split_leading_emoji(label.text())
    if name is None:
        _clear_label_icon(label)
        return
    if clean != label.text():
        label._line_busy = True
        try:
            _orig_label_set_text(label, clean)
        finally:
            label._line_busy = False
    label._line_icon_name = name
    _ensure_label_icon(label)


def _patch_buttons():
    global _orig_push_set_icon, _orig_tool_set_icon

    _orig_push_init = QPushButton.__init__
    _orig_tool_init = QToolButton.__init__

    def push_init(self, *args, **kwargs):
        _orig_push_init(self, *args, **kwargs)
        _apply_button(self)

    def tool_init(self, *args, **kwargs):
        _orig_tool_init(self, *args, **kwargs)
        _apply_button(self)

    QPushButton.__init__ = push_init
    QToolButton.__init__ = tool_init

    _orig_push_set_icon = QPushButton.setIcon
    _orig_tool_set_icon = QToolButton.setIcon

    def push_set_icon(self, icon):
        if not getattr(self, "_line_busy", False):
            self._line_auto = False
            self._line_icon_name = None
        _orig_push_set_icon(self, icon)

    def tool_set_icon(self, icon):
        if not getattr(self, "_line_busy", False):
            self._line_auto = False
            self._line_icon_name = None
        _orig_tool_set_icon(self, icon)

    QPushButton.setIcon = push_set_icon
    QToolButton.setIcon = tool_set_icon

    for cls in (QCheckBox, QRadioButton):
        original = cls.__init__

        def check_init(self, *args, _original=original, **kwargs):
            _original(self, *args, **kwargs)
            _apply_check(self)

        cls.__init__ = check_init


def _patch_abstract_text():
    original = QAbstractButton.setText

    def set_text(self, text):
        original(self, text)
        if isinstance(self, (QCheckBox, QRadioButton)):
            _apply_check(self)
        elif isinstance(self, (QPushButton, QToolButton)):
            _apply_button(self)

    QAbstractButton.setText = set_text


def _patch_labels():
    global _orig_label_set_text, _orig_label_set_pixmap

    _orig_label_init = QLabel.__init__
    _orig_label_set_text = QLabel.setText
    _orig_label_set_pixmap = QLabel.setPixmap

    def label_init(self, *args, **kwargs):
        _orig_label_init(self, *args, **kwargs)
        _apply_label(self)

    def label_set_text(self, text):
        if getattr(self, "_line_busy", False):
            _orig_label_set_text(self, text)
            return
        _orig_label_set_text(self, text)
        _apply_label(self)

    def label_set_pixmap(self, pixmap):
        _clear_label_icon(self)
        _orig_label_set_pixmap(self, pixmap)

    QLabel.__init__ = label_init
    QLabel.setText = label_set_text
    QLabel.setPixmap = label_set_pixmap


def _patch_combos():
    original_add = QComboBox.addItem
    original_insert = QComboBox.insertItem
    original_clear = QComboBox.clear

    def add_item(self, *args):
        name = None
        if args and isinstance(args[0], str):
            name, clean = split_leading_emoji(args[0])
            if name is not None:
                args = (clean,) + args[1:]
        original_add(self, *args)
        if name is not None:
            index = self.count() - 1
            icons = dict(getattr(self, "_line_item_icons", {}))
            icons[index] = name
            self._line_item_icons = icons
            self.setItemIcon(index, line_icon(name, _color("title"), 16))

    def insert_item(self, index, *args):
        name = None
        if args and isinstance(args[0], str):
            name, clean = split_leading_emoji(args[0])
            if name is not None:
                args = (clean,) + args[1:]
        original_insert(self, index, *args)
        if name is not None:
            icons = {i + (1 if i >= index else 0): n
                     for i, n in getattr(self, "_line_item_icons", {}).items()}
            icons[index] = name
            self._line_item_icons = icons
            self.setItemIcon(index, line_icon(name, _color("title"), 16))

    def add_items(self, texts):
        for text in texts:
            add_item(self, text)

    def insert_items(self, index, texts):
        for offset, text in enumerate(texts):
            insert_item(self, index + offset, text)

    def clear(self):
        self._line_item_icons = {}
        original_clear(self)

    QComboBox.addItem = add_item
    QComboBox.insertItem = insert_item
    QComboBox.addItems = add_items
    QComboBox.insertItems = insert_items
    QComboBox.clear = clear


def _patch_tabs():
    original_add = QTabWidget.addTab
    original_insert = QTabWidget.insertTab
    original_remove = QTabWidget.removeTab

    def add_tab(self, widget, label):
        name, clean = split_leading_emoji(label)
        index = original_add(self, widget, clean if name else label)
        if name:
            icons = dict(getattr(self, "_line_tab_icons", {}))
            icons[index] = name
            self._line_tab_icons = icons
            self.setTabIcon(index, line_icon(name, _color("title"), 16))
        return index

    def insert_tab(self, index, widget, label):
        name, clean = split_leading_emoji(label)
        original_insert(self, index, widget, clean if name else label)
        if name:
            icons = {i + (1 if i >= index else 0): n
                     for i, n in getattr(self, "_line_tab_icons", {}).items()}
            icons[index] = name
            self._line_tab_icons = icons
            self.setTabIcon(index, line_icon(name, _color("title"), 16))

    def remove_tab(self, index):
        original_remove(self, index)
        icons = {i - (1 if i > index else 0): n
                 for i, n in getattr(self, "_line_tab_icons", {}).items() if i != index}
        self._line_tab_icons = icons

    QTabWidget.addTab = add_tab
    QTabWidget.insertTab = insert_tab
    QTabWidget.removeTab = remove_tab


def _patch_set_property():
    original = QWidget.setProperty

    def set_property(self, name, value):
        result = original(self, name, value)
        if name in ("variant", "role"):
            _refresh(self)
        return result

    QWidget.setProperty = set_property


def _refresh(widget):
    try:
        if isinstance(widget, (QPushButton, QToolButton)):
            if getattr(widget, "_line_auto", False):
                _paint_button_icon(widget)
        elif isinstance(widget, QLabel):
            if getattr(widget, "_line_icon_name", None) and getattr(
                    widget, "_line_icon_label", None) is not None:
                _ensure_label_icon(widget)
        elif isinstance(widget, QComboBox):
            for index, name in getattr(widget, "_line_item_icons", {}).items():
                if 0 <= index < widget.count():
                    widget.setItemIcon(index, line_icon(name, _color("title"), 16))
        elif isinstance(widget, QTabWidget):
            for index, name in getattr(widget, "_line_tab_icons", {}).items():
                if 0 <= index < widget.count():
                    widget.setTabIcon(index, line_icon(name, _color("title"), 16))
    except RuntimeError:
        pass


def refresh_all():
    """主题切换后调用：按当前调色板重生成所有自动图标。"""
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        _refresh(widget)


def install():
    global _installed, _orig_abstract_set_text
    if _installed:
        return
    _installed = True
    _orig_abstract_set_text = QAbstractButton.setText
    _patch_buttons()
    _patch_abstract_text()
    _patch_labels()
    _patch_combos()
    _patch_tabs()
    _patch_set_property()