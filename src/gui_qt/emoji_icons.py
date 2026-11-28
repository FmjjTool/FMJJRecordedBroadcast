# -*- coding: utf-8 -*-
"""把页面历史遗留的前导 emoji 结构图标统一转换为 line_icon 线形图标。

安装后自动作用于运行期创建的所有控件，页面源码无需逐个改写：

- `QPushButton` / `QToolButton`：去掉文案前导 emoji，改为 `setIcon`；
- `QLabel`：去掉前导 emoji，在文字左侧绘制小图标（保留原文字颜色与字体）；
- `QCheckBox` / `QRadioButton`：只去掉前导 emoji（Qt 复选框不支持图标）；
- `QComboBox` / `QTabWidget`：去掉条目前导 emoji，改为条目图标；
- 只读 `QPlainTextEdit` / `QTextEdit` 日志与 `QStatusBar`：统一删除 emoji；
  可编辑输入区、`QTextBrowser` 用户正文不改。

颜色始终取自主题角色（标题/条目 link，普通按钮 button-text，主按钮
highlighted-text，弱化标签 placeholder-text）；切主题后由 `refresh_all()` 重新生成。
"""

from __future__ import annotations

import html as html_lib
import re
import weakref
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QErrorMessage, QGroupBox, QLabel,
    QInputDialog,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
    QProgressDialog, QRadioButton, QMessageBox, QStatusBar, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit,
    QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
    QWidget,
)

from gui_qt import theme
from gui_qt.navigation import line_icon

_ICON_SIZE_BUTTON = 16
_ICON_SIZE_LABEL = 14
_ICON_SIZE_ITEM = 16
_LABEL_GAP = 4
_ITEM_ICON_ROLE = Qt.UserRole + 977

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
# Unicode 17.0 Emoji_Presentation code points. Keeping exact ranges avoids
# deleting neighboring keyboard, technical, and layout symbols.
_EMOJI_PRESENTATION_RANGES = (
    "231A-231B 23E9-23EC 23F0 23F3 25FD-25FE 2614-2615 2648-2653 267F"
    " 2693 26A1 26AA-26AB 26BD-26BE 26C4-26C5 26CE 26D4 26EA 26F2-26F3"
    " 26F5 26FA 26FD 2705 270A-270B 2728 274C 274E 2753-2755 2757"
    " 2795-2797 27B0 27BF 2B1B-2B1C 2B50 2B55 1F004 1F0CF 1F18E"
    " 1F191-1F19A 1F1E6-1F1FF 1F201 1F21A 1F22F 1F232-1F236 1F238-1F23A"
    " 1F250-1F251 1F300-1F320 1F32D-1F335 1F337-1F37C 1F37E-1F393"
    " 1F3A0-1F3CA 1F3CF-1F3D3 1F3E0-1F3F0 1F3F4 1F3F8-1F43E 1F440"
    " 1F442-1F4FC 1F4FF-1F53D 1F54B-1F54E 1F550-1F567 1F57A 1F595-1F596"
    " 1F5A4 1F5FB-1F64F 1F680-1F6C5 1F6CC 1F6D0-1F6D2 1F6D5-1F6D8"
    " 1F6DC-1F6DF 1F6EB-1F6EC 1F6F4-1F6FC 1F7E0-1F7EB 1F7F0"
    " 1F90C-1F93A 1F93C-1F945 1F947-1F9FF 1FA70-1FA7C 1FA80-1FA8A"
    " 1FA8E-1FAC6 1FAC8 1FACD-1FADC 1FADF-1FAEA 1FAEF-1FAF8"
).split()
# Emoji=Yes but Emoji_Presentation=No: remove these only with VS16, so plain
# text such as ©, arrows, and keyboard glyphs remains readable.
_TEXT_PRESENTATION_EMOJI_RANGES = (
    "A9 AE 203C 2049 2122 2139 2194-2199 21A9-21AA 2328 23CF 23ED-23EF"
    " 23F1-23F2 23F8-23FA 24C2 25AA-25AB 25B6 25C0 25FB-25FC 2600-2604"
    " 260E 2611 2618 261D 2620 2622-2623 2626 262A 262E-262F 2638-263A"
    " 2640 2642 265F-2660 2663 2665-2666 2668 267B 267E 2692 2694-2697"
    " 2699 269B-269C 26A0 26A7 26B0-26B1 26C8 26CF 26D1 26D3 26E9"
    " 26F0-26F1 26F4 26F7-26F9 2702 2708-2709 270C-270D 270F 2712"
    " 2714 2716 271D 2721 2733-2734 2744 2747 2763-2764 27A1 2934-2935"
    " 2B05-2B07 3030 303D 3297 3299 1F170-1F171 1F17E-1F17F 1F202"
    " 1F237 1F321 1F324-1F32C 1F336 1F37D 1F396-1F397 1F399-1F39B"
    " 1F39E-1F39F 1F3CB-1F3CE 1F3D4-1F3DF 1F3F3 1F3F5 1F3F7 1F43F"
    " 1F441 1F4FD 1F549-1F54A 1F56F-1F570 1F573-1F579 1F587"
    " 1F58A-1F58D 1F590 1F5A5 1F5A8 1F5B1-1F5B2 1F5BC 1F5C2-1F5C4"
    " 1F5D1-1F5D3 1F5DC-1F5DE 1F5E1 1F5E3 1F5E8 1F5EF 1F5F3 1F5FA"
    " 1F6CB 1F6CD-1F6CF 1F6E0-1F6E5 1F6E9 1F6F0 1F6F3"
).split()


def _unicode_class(ranges):
    parts = []
    for value in ranges:
        bounds = value.split("-")
        start = chr(int(bounds[0], 16))
        end = chr(int(bounds[-1], 16))
        parts.append(start if start == end else start + "-" + end)
    return "[" + "".join(parts) + "]"


_EMOJI_BASE = _unicode_class(_EMOJI_PRESENTATION_RANGES)
_TEXT_EMOJI_BASE = _unicode_class(_TEXT_PRESENTATION_EMOJI_RANGES)
_EMOJI_MODIFIER = "[\U0001F3FB-\U0001F3FF]"
_EMOJI_UNIT = (
    rf"(?:{_EMOJI_BASE}[\ufe0e\ufe0f]?|{_TEXT_EMOJI_BASE}\ufe0f|"
    rf"{_EMOJI_MODIFIER}){_EMOJI_MODIFIER}?"
)
# Remove complete emoji sequences only; never globally discard VS, ZWJ, or tag
# characters from unrelated text. Regional indicators are handled as flags.
_INLINE_EMOJI_RE = re.compile(
    rf"(?:[#*0-9]\ufe0f?\u20e3|[\U0001F1E6-\U0001F1FF]{{1,2}}|"
    rf"{_EMOJI_UNIT}(?:\u200d{_EMOJI_UNIT})*"
    rf"(?:[\U000E0020-\U000E007E]+\U000E007F)?)")
_EMOJI_CHARREF_RE = re.compile(r"&#(?:[xX]([0-9a-fA-F]{1,8})|([0-9]{1,10}));")
_RICH_TEXT_RE = re.compile(
    r"</?[A-Za-z][^>]*>|&(?:#(?:x[0-9a-f]+|[0-9]+)|[A-Za-z][A-Za-z0-9]+);",
    re.I,
)
_EMOJI_IMAGE_MARKERS = frozenset(("emoji", "emote", "emoticon", "sticker", "stickers"))

_installed = False
_orig_abstract_set_text = None
_orig_label_set_text = None
_orig_label_set_pixmap = None
_orig_push_set_icon = None
_orig_tool_set_icon = None
_orig_list_set_text = None
_orig_table_set_text = None
_orig_tree_set_text = None
_orig_group_set_title = None
_auto_actions = weakref.WeakSet()


def _norm(text: str) -> str:
    return text.translate(_VARIATION)


def strip_inline_emoji(text: str) -> str:
    """删除句中的 emoji（保留“→”“×”等普通排版符号），并收拢残留空白。"""
    if not text:
        return text
    cleaned = _INLINE_EMOJI_RE.sub("", text)
    if cleaned == text:
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)
    cleaned = re.sub(r"([「（(【\[])[ \t]+", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+([」）)】\]])", r"\1", cleaned)
    return cleaned.strip()


def strip_emoji(text: str) -> str:
    """只删除 emoji，不改空白与缩进（供日志等保留排版的场景使用）。"""
    if not text:
        return text
    return _INLINE_EMOJI_RE.sub("", str(text))


def _is_emoji_image(attrs) -> bool:
    values = {name.lower(): value or "" for name, value in attrs}
    for name in ("class", "id", "data-type", "data-kind", "data-emoji",
                 "data-emote", "data-sticker"):
        value = str(values.get(name, "")).lower()
        tokens = re.findall(r"[a-z]+", value)
        if any(token in _EMOJI_IMAGE_MARKERS for token in tokens):
            return True
    src = str(values.get("src", ""))
    try:
        path = unquote(urlsplit(src).path)
    except ValueError:
        path = src
    for segment in path.replace("\\", "/").split("/"):
        stem = segment.rsplit(".", 1)[0].lower()
        tokens = re.findall(r"[a-z]+", stem)
        if any(token in _EMOJI_IMAGE_MARKERS for token in tokens):
            return True
    return False


class _EmojiHTMLParser(HTMLParser):
    """保留 HTML 结构，只清理可见文字与可识别的表情图片节点。"""

    _VISIBLE_ATTRIBUTES = frozenset(("alt", "title", "aria-label"))

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self._raw_text_depth = 0

    def _start_tag(self, tag, attrs, self_closing=False):
        if tag.lower() == "img" and _is_emoji_image(attrs):
            return
        changed = False
        clean_attrs = []
        for name, value in attrs:
            if value is not None and name.lower() in self._VISIBLE_ATTRIBUTES:
                clean = strip_inline_emoji(value)
                changed = changed or clean != value
                value = clean
            clean_attrs.append((name, value))
        if not changed:
            self.parts.append(self.get_starttag_text())
            if tag.lower() == "script":
                self._raw_text_depth += 1
            return
        suffix = " /" if self_closing else ""
        rendered = "".join(
            f" {name}=\"{html_lib.escape(value, quote=True)}\""
            if value is not None else f" {name}"
            for name, value in clean_attrs
        )
        self.parts.append(f"<{tag}{rendered}{suffix}>")
        if tag.lower() == "script":
            self._raw_text_depth += 1

    def handle_starttag(self, tag, attrs):
        self._start_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._start_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")
        if tag.lower() == "script" and self._raw_text_depth:
            self._raw_text_depth -= 1

    def handle_data(self, data):
        self.parts.append(data if self._raw_text_depth else strip_inline_emoji(data))

    def handle_entityref(self, name):
        raw = f"&{name};"
        value = html_lib.unescape(raw)
        clean = strip_emoji(value)
        self.parts.append(html_lib.escape(clean, quote=False) if clean != value else raw)

    def handle_charref(self, name):
        raw = f"&#{name};"
        try:
            value = chr(int(name[1:], 16) if name.lower().startswith("x") else int(name))
        except (ValueError, OverflowError):
            self.parts.append(raw)
            return
        clean = strip_emoji(value)
        self.parts.append(html_lib.escape(clean, quote=False) if clean != value else raw)

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.parts.append(f"<!{decl}>")

    def unknown_decl(self, data):
        self.parts.append(f"<![{data}]>")


def strip_html_emoji(text: str) -> str:
    """清理富文本中的 emoji 文本、实体及带有明确表情标记的图片节点。"""
    if not text:
        return text
    parser = _EmojiHTMLParser()
    try:
        parser.feed(str(text))
        parser.close()
    except Exception:  # noqa: BLE001 - malformed rich text should still render
        source = str(text)

        def remove_emoji_ref(match):
            try:
                value = chr(int(match.group(1), 16) if match.group(1) else int(match.group(2)))
            except (ValueError, OverflowError):
                return match.group(0)
            return "" if strip_emoji(value) != value else match.group(0)

        source = _EMOJI_CHARREF_RE.sub(remove_emoji_ref, source)
        return strip_inline_emoji(source)
    return "".join(parser.parts)


def _clean_display_text(text) -> str:
    value = str(text or "")
    return strip_html_emoji(value) if _RICH_TEXT_RE.search(value) else strip_inline_emoji(value)


def clean_text(text: str) -> tuple[str | None, str]:
    """返回 (前导 emoji 对应的图标名, 去 emoji 文本)；句中 emoji 一并删除。"""
    name, clean = split_leading_emoji(text)
    return name, strip_inline_emoji(clean if name is not None else text)


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
    try:
        _apply_button_impl(button)
    except Exception:  # noqa: BLE001 - 图标装饰失败不能影响按钮文字与点击
        return


def _apply_button_impl(button):
    original = button.text()
    name, clean = split_leading_emoji(original)
    clean = strip_inline_emoji(clean if name is not None else original)
    if name is None:
        if clean != original:
            button._line_busy = True
            try:
                _orig_abstract_set_text(button, clean)
            finally:
                button._line_busy = False
        if getattr(button, "_line_auto", False):
            button._line_auto = False
            button._line_icon_name = None
            button.setIcon(QIcon())
        return
    if clean != original:
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
    try:
        _apply_check_impl(button)
    except Exception:  # noqa: BLE001 - 同上，勾选控件的文字优先
        return


def _apply_check_impl(button):
    original = button.text()
    name, clean = split_leading_emoji(original)
    clean = strip_inline_emoji(clean if name is not None else original)
    if clean != original:
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
        # QMargins 不可迭代，必须逐个取；这里抛异常会从 QLabel.setText 冒到业务代码
        label.setContentsMargins(base.left(), base.top(), base.right(), base.bottom())
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
    try:
        _apply_label_impl(label)
    except Exception:  # noqa: BLE001 - 图标装饰失败不能影响标签文字与业务回调
        return


def _apply_label_impl(label):
    label._line_skip = False
    original = label.text()
    if _RICH_TEXT_RE.search(original):
        name, clean = None, strip_html_emoji(original)
    else:
        name, clean = split_leading_emoji(original)
        clean = strip_inline_emoji(clean if name is not None else original)
    if clean != original:
        label._line_busy = True
        try:
            _orig_label_set_text(label, clean)
        finally:
            label._line_busy = False
    if name is None:
        _clear_label_icon(label)
        return
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
    original_set_item_text = QComboBox.setItemText

    def add_item(self, *args):
        name = None
        if args and isinstance(args[0], str):
            name, clean = clean_text(args[0])
            if clean != args[0]:
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
            name, clean = clean_text(args[0])
            if clean != args[0]:
                args = (clean,) + args[1:]
        original_insert(self, index, *args)
        if name is not None:
            icons = {i + (1 if i >= index else 0): n
                     for i, n in getattr(self, "_line_item_icons", {}).items()}
            icons[index] = name
            self._line_item_icons = icons
            self.setItemIcon(index, line_icon(name, _color("title"), 16))

    def set_item_text(self, index, text):
        name, clean = clean_text(text)
        original_set_item_text(self, index, clean)
        icons = dict(getattr(self, "_line_item_icons", {}))
        if name is not None:
            icons[index] = name
            self.setItemIcon(index, line_icon(name, _color("title"), 16))
        elif index in icons:
            icons.pop(index, None)
            self.setItemIcon(index, QIcon())
        self._line_item_icons = icons

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
    QComboBox.setItemText = set_item_text
    QComboBox.addItems = add_items
    QComboBox.insertItems = insert_items
    QComboBox.clear = clear


def _patch_tabs():
    original_add = QTabWidget.addTab
    original_insert = QTabWidget.insertTab
    original_remove = QTabWidget.removeTab
    original_set_text = QTabWidget.setTabText

    def add_tab(self, widget, label):
        name, clean = clean_text(label)
        index = original_add(self, widget, clean)
        if name:
            icons = dict(getattr(self, "_line_tab_icons", {}))
            icons[index] = name
            self._line_tab_icons = icons
            self.setTabIcon(index, line_icon(name, _color("title"), 16))
        return index

    def insert_tab(self, index, widget, label):
        name, clean = clean_text(label)
        original_insert(self, index, widget, clean)
        if name:
            icons = {i + (1 if i >= index else 0): n
                     for i, n in getattr(self, "_line_tab_icons", {}).items()}
            icons[index] = name
            self._line_tab_icons = icons
            self.setTabIcon(index, line_icon(name, _color("title"), 16))

    def set_tab_text(self, index, label):
        name, clean = clean_text(label)
        original_set_text(self, index, clean)
        icons = dict(getattr(self, "_line_tab_icons", {}))
        if name:
            icons[index] = name
            self.setTabIcon(index, line_icon(name, _color("title"), 16))
        elif index in icons:
            icons.pop(index, None)
            self.setTabIcon(index, QIcon())
        self._line_tab_icons = icons

    def remove_tab(self, index):
        original_remove(self, index)
        icons = {i - (1 if i > index else 0): n
                 for i, n in getattr(self, "_line_tab_icons", {}).items() if i != index}
        self._line_tab_icons = icons

    QTabWidget.addTab = add_tab
    QTabWidget.insertTab = insert_tab
    QTabWidget.setTabText = set_tab_text
    QTabWidget.removeTab = remove_tab


def _apply_flat_item(item):
    original = item.text()
    name, clean = clean_text(original)
    if clean != original:
        setter = (_orig_list_set_text if isinstance(item, QListWidgetItem)
                  else _orig_table_set_text)
        setter(item, clean)
    if name is None:
        if item.data(_ITEM_ICON_ROLE) is not None:
            item.setData(_ITEM_ICON_ROLE, None)
            item.setIcon(QIcon())
        return
    if not item.icon().isNull() and item.data(_ITEM_ICON_ROLE) is None:
        return
    item.setData(_ITEM_ICON_ROLE, name)
    item.setIcon(line_icon(name, _color("title"), _ICON_SIZE_ITEM))


def _apply_tree_item(item):
    original = item.text(0)
    name, clean = clean_text(original)
    if clean != original:
        _orig_tree_set_text(item, 0, clean)
    if name is None:
        if item.data(0, _ITEM_ICON_ROLE) is not None:
            item.setData(0, _ITEM_ICON_ROLE, None)
            item.setIcon(0, QIcon())
        return
    if not item.icon(0).isNull() and item.data(0, _ITEM_ICON_ROLE) is None:
        return
    item.setData(0, _ITEM_ICON_ROLE, name)
    item.setIcon(0, line_icon(name, _color("title"), _ICON_SIZE_ITEM))


def _patch_item_types():
    global _orig_list_set_text, _orig_table_set_text, _orig_tree_set_text

    for cls in (QListWidgetItem, QTableWidgetItem):
        original_init = cls.__init__
        original_set = cls.setText
        if cls is QListWidgetItem:
            _orig_list_set_text = original_set
        else:
            _orig_table_set_text = original_set

        def item_init(self, *args, _original=original_init, **kwargs):
            _original(self, *args, **kwargs)
            try:
                _apply_flat_item(self)
            except Exception:  # noqa: BLE001 - 图标装饰失败不能影响条目文本
                pass

        def item_set_text(self, text, _original=original_set):
            _original(self, text)
            try:
                _apply_flat_item(self)
            except Exception:  # noqa: BLE001
                pass

        cls.__init__ = item_init
        cls.setText = item_set_text

    _orig_tree_set_text = QTreeWidgetItem.setText
    original_tree_init = QTreeWidgetItem.__init__

    def tree_init(self, *args, **kwargs):
        original_tree_init(self, *args, **kwargs)
        try:
            _apply_tree_item(self)
        except Exception:  # noqa: BLE001
            pass

    def tree_set_text(self, column, text):
        _orig_tree_set_text(self, column, text)
        try:
            if column == 0:
                _apply_tree_item(self)
            else:
                clean = strip_inline_emoji(text)
                if clean != self.text(column):
                    _orig_tree_set_text(self, column, clean)
        except Exception:  # noqa: BLE001
            pass

    QTreeWidgetItem.__init__ = tree_init
    QTreeWidgetItem.setText = tree_set_text


def _patch_item_widgets():
    original_add = QListWidget.addItem
    original_insert = QListWidget.insertItem

    def add_item(self, item):
        if isinstance(item, str):
            item = QListWidgetItem(item)
        original_add(self, item)

    def add_items(self, texts):
        for text in texts:
            add_item(self, text)

    def insert_item(self, row, item):
        if isinstance(item, str):
            item = QListWidgetItem(item)
        original_insert(self, row, item)

    def insert_items(self, row, texts):
        for offset, text in enumerate(texts):
            insert_item(self, row + offset, text)

    QListWidget.addItem = add_item
    QListWidget.addItems = add_items
    QListWidget.insertItem = insert_item
    QListWidget.insertItems = insert_items


def _apply_group_title(box):
    original = box.title()
    _name, clean = clean_text(original)
    if clean != original:
        _orig_group_set_title(box, clean)


def _patch_group_boxes():
    global _orig_group_set_title
    _orig_group_set_title = QGroupBox.setTitle
    original_init = QGroupBox.__init__

    def group_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            _apply_group_title(self)
        except Exception:  # noqa: BLE001
            pass

    def set_title(self, title):
        _name, clean = clean_text(title)
        _orig_group_set_title(self, clean)

    QGroupBox.__init__ = group_init
    QGroupBox.setTitle = set_title


def _apply_action(action):
    original = action.text()
    name, clean = clean_text(original)
    if clean != original:
        action._line_busy = True
        try:
            _orig_action_set_text(action, clean)
        finally:
            action._line_busy = False
    if name:
        action._line_icon_name = name
        action._line_auto = True
        action.setIcon(line_icon(name, _color("button"), _ICON_SIZE_BUTTON))
        _auto_actions.add(action)


def _patch_actions():
    global _orig_action_set_text
    _orig_action_set_text = QAction.setText
    original_init = QAction.__init__

    def action_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            _apply_action(self)
        except Exception:  # noqa: BLE001
            pass

    def action_set_text(self, text):
        if getattr(self, "_line_busy", False):
            _orig_action_set_text(self, text)
            return
        _orig_action_set_text(self, text)
        try:
            _apply_action(self)
        except Exception:  # noqa: BLE001
            pass

    QAction.__init__ = action_init
    QAction.setText = action_set_text

    original_add_action = QMenu.addAction

    def add_action(self, *args):
        result = original_add_action(self, *args)
        if isinstance(result, QAction):
            try:
                _apply_action(result)
            except Exception:  # noqa: BLE001
                pass
        return result

    original_add_menu = QMenu.addMenu

    def add_menu(self, *args):
        if args and isinstance(args[0], str):
            name, clean = clean_text(args[0])
            menu = original_add_menu(self, *((clean,) + args[1:]))
            if name:
                menu.menuAction().setIcon(
                    line_icon(name, _color("button"), _ICON_SIZE_BUTTON))
            return menu
        return original_add_menu(self, *args)

    original_set_title = QMenu.setTitle

    def set_title(self, title):
        _name, clean = clean_text(title)
        original_set_title(self, clean)

    QMenu.addAction = add_action
    QMenu.addMenu = add_menu
    QMenu.setTitle = set_title


def _patch_window_titles():
    original = QWidget.setWindowTitle

    def set_window_title(self, title):
        original(self, strip_inline_emoji(title))

    QWidget.setWindowTitle = set_window_title


def _patch_set_property():
    original = QWidget.setProperty

    def set_property(self, name, value):
        result = original(self, name, value)
        if name in ("variant", "role"):
            _refresh(self)
        return result

    QWidget.setProperty = set_property


def _refresh_item_icon(item):
    name = item.data(_ITEM_ICON_ROLE)
    if name:
        item.setIcon(line_icon(name, _color("title"), _ICON_SIZE_ITEM))


def _refresh_tree_icon(item):
    name = item.data(0, _ITEM_ICON_ROLE)
    if name:
        item.setIcon(0, line_icon(name, _color("title"), _ICON_SIZE_ITEM))


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
        elif isinstance(widget, QListWidget):
            for i in range(widget.count()):
                item = widget.item(i)
                if item is not None:
                    _refresh_item_icon(item)
        elif isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None:
                        _refresh_item_icon(item)
        elif isinstance(widget, QTreeWidget):
            iterator = QTreeWidgetItemIterator(widget)
            while iterator.value() is not None:
                _refresh_tree_icon(iterator.value())
                iterator += 1
    except RuntimeError:
        pass


def refresh_all():
    """主题切换后调用：按当前调色板重生成所有自动图标。"""
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        _refresh(widget)
    for action in list(_auto_actions):
        try:
            name = getattr(action, "_line_icon_name", None)
            if name and getattr(action, "_line_auto", False):
                action.setIcon(line_icon(name, _color("button"), _ICON_SIZE_BUTTON))
        except RuntimeError:
            pass


def _patch_misc_text():
    orig_tip = QWidget.setToolTip
    orig_placeholder = QLineEdit.setPlaceholderText
    orig_progress_label = QProgressDialog.setLabelText
    orig_progress_cancel = QProgressDialog.setCancelButtonText
    orig_tray_message = QSystemTrayIcon.showMessage
    orig_tray_tooltip = QSystemTrayIcon.setToolTip
    orig_tooltip_show = QToolTip.showText
    orig_message_text = QMessageBox.setText
    orig_message_info = QMessageBox.setInformativeText
    orig_message_detail = QMessageBox.setDetailedText

    def set_tooltip(self, text):
        return orig_tip(self, strip_inline_emoji(text))

    def set_placeholder(self, text):
        return orig_placeholder(self, strip_inline_emoji(text))

    def set_progress_label(self, text):
        return orig_progress_label(self, strip_inline_emoji(text))

    def set_progress_cancel(self, text):
        return orig_progress_cancel(self, strip_inline_emoji(text))

    def show_tray_message(self, title, message, *args, **kwargs):
        return orig_tray_message(
            self, strip_inline_emoji(title), strip_inline_emoji(message), *args, **kwargs)

    def set_tray_tooltip(self, text):
        return orig_tray_tooltip(self, strip_inline_emoji(text))

    def show_tooltip(*args, **kwargs):
        values = list(args)
        if len(values) > 1:
            values[1] = strip_inline_emoji(values[1])
        elif "text" in kwargs:
            kwargs["text"] = strip_inline_emoji(kwargs["text"])
        return orig_tooltip_show(*values, **kwargs)

    def set_message_text(self, text):
        return orig_message_text(self, strip_inline_emoji(text))

    def set_message_info(self, text):
        return orig_message_info(self, strip_inline_emoji(text))

    def set_message_detail(self, text):
        return orig_message_detail(self, strip_inline_emoji(text))

    QWidget.setToolTip = set_tooltip
    QLineEdit.setPlaceholderText = set_placeholder
    QProgressDialog.setLabelText = set_progress_label
    QProgressDialog.setCancelButtonText = set_progress_cancel
    QSystemTrayIcon.showMessage = show_tray_message
    QSystemTrayIcon.setToolTip = set_tray_tooltip
    QToolTip.showText = staticmethod(show_tooltip)
    QMessageBox.setText = set_message_text
    QMessageBox.setInformativeText = set_message_info
    QMessageBox.setDetailedText = set_message_detail
    for method_name in ("information", "question", "warning", "critical"):
        original = getattr(QMessageBox, method_name)

        def show_message(parent, title, text, *args, _original=original, **kwargs):
            return _original(parent, strip_inline_emoji(title),
                             strip_inline_emoji(text), *args, **kwargs)

        setattr(QMessageBox, method_name, staticmethod(show_message))

    for method_name in ("getText", "getMultiLineText", "getInt", "getDouble",
                        "getItem"):
        original = getattr(QInputDialog, method_name)

        def show_input(parent, title, label, *args, _original=original, **kwargs):
            return _original(parent, strip_inline_emoji(title),
                             strip_inline_emoji(label), *args, **kwargs)

        setattr(QInputDialog, method_name, staticmethod(show_input))

    orig_error_message = QErrorMessage.showMessage

    def show_error_message(self, message, *args, **kwargs):
        return orig_error_message(self, strip_inline_emoji(message), *args, **kwargs)

    QErrorMessage.showMessage = show_error_message


def _log_color(text: str) -> str:
    if any(k in text for k in ("❌", "失败", "错误", "异常")):
        return theme.color("danger")
    if any(k in text for k in ("⚠️", "⚠", "警告", "注意", "跳过", "已存在", "标准命名")):
        return theme.color("warning")
    if any(k in text for k in ("✅", "完成", "成功")):
        return theme.color("success")
    if any(k in text for k in ("📂", "📁", "扫描", "正在", "信息")):
        return theme.color("link")
    return ""


def _colored_log(text) -> str:
    text = str(text)
    clean = strip_emoji(text)
    color = _log_color(text)
    if not color or "<" in text:
        return clean
    return f'<span style="color:{color}">{html_lib.escape(clean)}</span>'


def _patch_text_edit():
    orig_append = QTextEdit.append
    orig_set = QTextEdit.setPlainText
    orig_set_html = QTextEdit.setHtml

    def append_text(self, text):
        if isinstance(self, QTextBrowser):
            text = _clean_display_text(text)
        elif self.isReadOnly():
            text = _colored_log(text)
        return orig_append(self, text)

    def set_text(self, text):
        if isinstance(self, QTextBrowser) or self.isReadOnly():
            text = strip_inline_emoji(text)
        return orig_set(self, text)

    def set_html(self, text):
        if isinstance(self, QTextBrowser) or self.isReadOnly():
            text = strip_html_emoji(text)
        return orig_set_html(self, text)

    QTextEdit.append = append_text
    QTextEdit.setPlainText = set_text
    QTextEdit.setHtml = set_html


def _patch_plain_text():
    orig_append = QPlainTextEdit.appendPlainText
    orig_set = QPlainTextEdit.setPlainText
    orig_status = QStatusBar.showMessage

    def append_plain(self, text):
        return orig_append(self, strip_emoji(text))

    def set_plain(self, text):
        if self.isReadOnly():
            text = strip_emoji(text)
        return orig_set(self, text)

    def show_message(self, message, timeout=0):
        return orig_status(self, strip_inline_emoji(message), timeout)

    QPlainTextEdit.appendPlainText = append_plain
    QPlainTextEdit.setPlainText = set_plain
    QStatusBar.showMessage = show_message


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
    _patch_item_types()
    _patch_item_widgets()
    _patch_group_boxes()
    _patch_actions()
    _patch_window_titles()
    _patch_plain_text()
    _patch_text_edit()
    _patch_misc_text()
    _patch_set_property()
