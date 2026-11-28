# -*- coding: utf-8 -*-
"""Qt 界面文字清理；结构图标由调用方显式设置。

本模块不推导、生成或刷新图标，不改资源数据、下载文件及正文预览。
只读日志保留级别颜色；可编辑内容与 QTextBrowser 用户正文保留原样。
"""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QComboBox, QErrorMessage, QGroupBox,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QRadioButton,
    QStatusBar, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QTextEdit, QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem,
    QWidget,
)

from gui_qt import theme

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

_installed = False


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


class _DisplayHTMLParser(HTMLParser):
    """仅清理文字节点，链接、图片、属性和脚本不作改写。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self._tokens = []
        self._raw_tag = None
        self.changed = False

    def _flush(self):
        if not self._tokens:
            return
        raw = "".join(token[0] for token in self._tokens)
        decoded = "".join(token[1] for token in self._tokens)
        clean = strip_emoji(decoded)
        self.changed = self.changed or clean != decoded
        self.parts.append(raw if clean == decoded else html_lib.escape(clean, quote=False))
        self._tokens.clear()

    def handle_starttag(self, tag, attrs):
        self._flush()
        self.parts.append(self.get_starttag_text())
        if tag in ("script", "style"):
            self._raw_tag = tag

    def handle_startendtag(self, tag, attrs):
        self._flush()
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self._flush()
        self.parts.append(f"</{tag}>")
        if self._raw_tag == tag:
            self._raw_tag = None

    def handle_data(self, data):
        if self._raw_tag:
            self.parts.append(data)
        else:
            self._tokens.append((data, data))

    def handle_entityref(self, name):
        raw = f"&{name};"
        self._tokens.append((raw, html_lib.unescape(raw)))

    def handle_charref(self, name):
        raw = f"&#{name};"
        self._tokens.append((raw, html_lib.unescape(raw)))

    def handle_comment(self, data):
        self._flush()
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self._flush()
        self.parts.append(f"<!{decl}>")

    def unknown_decl(self, data):
        self._flush()
        self.parts.append(f"<![{data}]>")

    def close(self):
        super().close()
        self._flush()


def strip_html_emoji(text: str) -> str:
    """保留富文本结构和所有图片，仅清理可见文字中的 emoji。"""
    if not text:
        return text
    parser = _DisplayHTMLParser()
    try:
        parser.feed(str(text))
        parser.close()
    except (AssertionError, ValueError):
        # Qt 可以接收不完整标记；解析失败不能阻断控件构造或丢掉图片属性。
        fragments = re.split(
            r'''(<!--.*?(?:-->|$)|<(?=[a-zA-Z!/?])(?:[^<>"']|"[^"]*"|'[^']*')*>)''',
            str(text), flags=re.DOTALL)
        raw_tag = None
        for index, raw in enumerate(fragments):
            if index % 2:
                tag = re.match(r"<(/?)(script|style)\b", raw, re.IGNORECASE)
                if tag:
                    name = tag.group(2).lower()
                    if tag.group(1) and raw_tag == name:
                        raw_tag = None
                    elif not tag.group(1) and raw_tag is None and not raw.endswith("/>"):
                        raw_tag = name
                continue
            if raw_tag:
                continue
            decoded = html_lib.unescape(raw)
            clean = strip_emoji(decoded)
            if clean != decoded:
                fragments[index] = html_lib.escape(clean, quote=False)
        return "".join(fragments)
    return "".join(parser.parts) if parser.changed else text


def _wrap_text_method(cls, name, index=0, *, cleaner=strip_inline_emoji,
                      keyword="text", overloaded=False):
    """只改显示文字参数；图标、数据、回调和返回值原样传递。"""
    original = getattr(cls, name)

    def wrapped(self, *args, **kwargs):
        values = list(args)
        position = index
        if overloaded and len(values) > position and not isinstance(values[position], str):
            position += 1
        if len(values) > position and isinstance(values[position], str):
            values[position] = cleaner(values[position])
        if keyword in kwargs and isinstance(kwargs[keyword], str):
            kwargs[keyword] = cleaner(kwargs[keyword])
        return original(self, *values, **kwargs)

    setattr(cls, name, wrapped)


def _wrap_text_constructor(cls, getter="text", setter="setText", *, cleaner=strip_inline_emoji):
    original_init = cls.__init__
    original_get = getattr(cls, getter)
    original_set = getattr(cls, setter)

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        value = original_get(self)
        clean = cleaner(value)
        if clean != value:
            original_set(self, clean)

    cls.__init__ = initialize


def _patch_controls():
    for cls in (QPushButton, QToolButton, QCheckBox, QRadioButton,
                QListWidgetItem, QTableWidgetItem, QAction):
        _wrap_text_constructor(cls)
    _wrap_text_constructor(QLabel, cleaner=strip_html_emoji)
    _wrap_text_constructor(QGroupBox, "title", "setTitle")
    _wrap_text_constructor(QMenu, "title", "setTitle")
    _wrap_text_constructor(QMessageBox, cleaner=strip_html_emoji)
    _wrap_text_constructor(QMessageBox, "windowTitle", "setWindowTitle")
    for cls in (QAbstractButton, QListWidgetItem, QTableWidgetItem, QAction):
        _wrap_text_method(cls, "setText")
    _wrap_text_method(QLabel, "setText", cleaner=strip_html_emoji)
    _wrap_text_method(QGroupBox, "setTitle", keyword="title")
    _wrap_text_method(QMenu, "setTitle", keyword="title")
    _wrap_text_method(QWidget, "setWindowTitle", keyword="title")
    for name in ("addItem", "insertItem"):
        _wrap_text_method(QComboBox, name, index=int(name == "insertItem"), overloaded=True)
    _wrap_text_method(QComboBox, "setItemText", index=1)
    for name, index in (("addTab", 1), ("insertTab", 2)):
        _wrap_text_method(QTabWidget, name, index=index, keyword="label", overloaded=True)
    _wrap_text_method(QTabWidget, "setTabText", index=1)
    _wrap_text_method(QTreeWidgetItem, "setText", index=1)

    original_tree_init = QTreeWidgetItem.__init__

    def tree_init(self, *args, **kwargs):
        original_tree_init(self, *args, **kwargs)
        for column in range(self.columnCount()):
            self.setText(column, self.text(column))

    QTreeWidgetItem.__init__ = tree_init

    for name in ("addAction", "addMenu"):
        original = getattr(QMenu, name)

        def add_menu_item(self, *args, _original=original, **kwargs):
            result = _original(self, *args, **kwargs)
            if isinstance(result, QAction):
                result.setText(result.text())
            elif isinstance(result, QMenu):
                result.setTitle(result.title())
            return result

        setattr(QMenu, name, add_menu_item)

    for name in ("addItem", "insertItem"):
        original = getattr(QListWidget, name)
        text_index = int(name == "insertItem")

        def list_item(self, *args, _original=original, _index=text_index, **kwargs):
            values = list(args)
            if len(values) > _index and isinstance(values[_index], str):
                values[_index] = strip_inline_emoji(values[_index])
            return _original(self, *values, **kwargs)

        setattr(QListWidget, name, list_item)
    for cls, names, keyword in (
        (QComboBox, ("addItems", "insertItems"), "texts"),
        (QListWidget, ("addItems", "insertItems"), "labels"),
        (QTableWidget, ("setHorizontalHeaderLabels", "setVerticalHeaderLabels"), "labels"),
        (QTreeWidget, ("setHeaderLabels",), "labels"),
    ):
        for name in names:
            original = getattr(cls, name)
            index = int(name == "insertItems")

            def items(self, *args, _original=original, _index=index,
                      _keyword=keyword, **kwargs):
                values = list(args)
                if len(values) > _index:
                    values[_index] = [strip_inline_emoji(text) for text in values[_index]]
                if _keyword in kwargs:
                    kwargs[_keyword] = [strip_inline_emoji(text) for text in kwargs[_keyword]]
                return _original(self, *values, **kwargs)

            setattr(cls, name, items)


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
        return orig_tip(self, strip_html_emoji(text))

    def set_placeholder(self, text):
        return orig_placeholder(self, strip_inline_emoji(text))

    def set_progress_label(self, text):
        return orig_progress_label(self, strip_html_emoji(text))

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
            values[1] = strip_html_emoji(values[1])
        elif "text" in kwargs:
            kwargs["text"] = strip_html_emoji(kwargs["text"])
        return orig_tooltip_show(*values, **kwargs)

    def set_message_text(self, text):
        return orig_message_text(self, strip_html_emoji(text))

    def set_message_info(self, text):
        return orig_message_info(self, strip_html_emoji(text))

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
                             strip_html_emoji(text), *args, **kwargs)

        setattr(QMessageBox, method_name, staticmethod(show_message))

    for method_name in ("getText", "getMultiLineText", "getInt", "getDouble",
                        "getItem"):
        original = getattr(QInputDialog, method_name)

        def show_input(parent, title, label, *args, _original=original, **kwargs):
            return _original(parent, strip_inline_emoji(title),
                             strip_html_emoji(label), *args, **kwargs)

        setattr(QInputDialog, method_name, staticmethod(show_input))

    orig_error_message = QErrorMessage.showMessage

    def show_error_message(self, message, *args, **kwargs):
        return orig_error_message(self, strip_html_emoji(message), *args, **kwargs)

    QErrorMessage.showMessage = show_error_message


def _log_color(text: str) -> str:
    if any(k in text for k in ("\u274c", "失败", "错误", "异常")):
        return theme.color("danger")
    if any(k in text for k in ("\u26a0\ufe0f", "\u26a0", "警告", "注意", "跳过", "已存在", "标准命名")):
        return theme.color("warning")
    if any(k in text for k in ("\u2705", "完成", "成功")):
        return theme.color("success")
    if any(k in text for k in ("\U0001f4c2", "\U0001f4c1", "扫描", "正在", "信息")):
        return theme.color("link")
    return ""


def _colored_log(text) -> str:
    text = str(text)
    clean = strip_html_emoji(text)
    color = _log_color(text)
    if not color or "<" in text:
        return clean
    return f'<span style="color:{color}">{html_lib.escape(clean)}</span>'


def _patch_logs():
    original_append = QTextEdit.append
    original_set = QTextEdit.setPlainText
    original_set_html = QTextEdit.setHtml
    original_set_auto = QTextEdit.setText
    original_plain_append = QPlainTextEdit.appendPlainText
    original_plain_set = QPlainTextEdit.setPlainText
    original_status = QStatusBar.showMessage

    def is_log(widget):
        return widget.isReadOnly() and not isinstance(widget, QTextBrowser)

    def append_text(self, text):
        return original_append(self, _colored_log(text) if is_log(self) else text)

    def set_text(self, text):
        return original_set(self, strip_emoji(text) if is_log(self) else text)

    def set_html(self, text):
        return original_set_html(self, strip_html_emoji(text) if is_log(self) else text)

    def set_auto(self, text):
        return original_set_auto(self, strip_html_emoji(text) if is_log(self) else text)

    def append_plain(self, text):
        return original_plain_append(self, strip_emoji(text) if self.isReadOnly() else text)

    def set_plain(self, text):
        return original_plain_set(self, strip_emoji(text) if self.isReadOnly() else text)

    def show_status(self, message, timeout=0):
        return original_status(self, strip_inline_emoji(message), timeout)

    QTextEdit.append = append_text
    QTextEdit.setPlainText = set_text
    QTextEdit.setHtml = set_html
    QTextEdit.setText = set_auto
    QPlainTextEdit.appendPlainText = append_plain
    QPlainTextEdit.setPlainText = set_plain
    QStatusBar.showMessage = show_status


def install():
    """一次性安装纯文字清理，不修改任何控件图标或主题刷新路径。"""
    global _installed
    if _installed:
        return
    _patch_controls()
    _patch_logs()
    _patch_misc_text()
    _installed = True
