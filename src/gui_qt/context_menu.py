# -*- coding: utf-8 -*-
"""统一的文本右键菜单：把 Qt 自带的英文菜单换成中文 + 线形图标菜单。

Qt 内建的 QLineEdit / QTextEdit / QPlainTextEdit / QTextBrowser / QLabel
上下文菜单不跟随应用语言与图标体系：中文界面里右键日志、链接或输入框，
弹出来的是英文 "&Copy / Select All"，没有图标、间距也和主题不一致。

这里装一个应用级事件过滤器，统一换成「中文 + 线形图标 + 快捷键右对齐 +
主题禁用态」的菜单，覆盖所有页面，页面源码无需逐个改写。

只接管仍使用默认菜单策略（DefaultContextMenu）的文本控件；页面自己
setContextMenuPolicy(CustomContextMenu) 或重写 contextMenuEvent 的菜单
保持原样，业务行为不受影响。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractScrollArea, QApplication, QLabel, QLineEdit, QMenu, QPlainTextEdit,
    QTextBrowser, QTextEdit, QWidget,
)

from gui_qt import theme
from gui_qt.navigation import line_icon

_ICON_SIZE = 16
_TEXT_EDITS = (QTextBrowser, QTextEdit, QPlainTextEdit)
_QT_BASES = (QTextBrowser, QTextEdit, QPlainTextEdit, QLineEdit, QLabel)


def _icon(name):
    return line_icon(name, theme.color("text"), _ICON_SIZE)


class _Builder:
    """菜单构造助手：自动跳过空分组之间的多余分隔线。"""

    def __init__(self, menu):
        self.menu = menu
        self._has_item = False
        self._pending_sep = False

    def add(self, name, text, shortcut=None, slot=None, enabled=True):
        if self._pending_sep:
            self.menu.addSeparator()
            self._pending_sep = False
        action = self.menu.addAction(text)
        action.setIcon(_icon(name))
        if shortcut:
            action.setShortcut(shortcut)
        if slot is not None:
            action.triggered.connect(lambda _checked=False, fn=slot: fn())
        action.setEnabled(bool(enabled))
        self._has_item = True
        return action

    def separate(self):
        if self._has_item:
            self._pending_sep = True


def _clipboard_can_paste():
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return False
    return bool(data.hasText() or data.hasHtml() or data.hasImage())


def _delete_selection(widget):
    cursor = widget.textCursor()
    cursor.removeSelectedText()
    widget.setTextCursor(cursor)


def _label_selected_text(label):
    try:
        return label.selectedText() or ""
    except Exception:  # noqa: BLE001 - 老版本 Qt 没有 selectedText
        return ""


def _fill_line_edit(menu, widget):
    read_only = widget.isReadOnly()
    secret = widget.echoMode() != QLineEdit.EchoMode.Normal
    has_selection = widget.hasSelectedText()
    build = _Builder(menu)

    if not read_only and not secret:
        build.add("undo", "撤销", "Ctrl+Z", widget.undo, widget.isUndoAvailable())
        build.add("redo", "重做", "Ctrl+Y", widget.redo, widget.isRedoAvailable())
        build.separate()

    if not secret:
        if not read_only:
            build.add("scissors", "剪切", "Ctrl+X", widget.cut, has_selection)
        build.add("copy", "复制", "Ctrl+C", widget.copy, has_selection)

    if not read_only:
        build.add("clipboard", "粘贴", "Ctrl+V", widget.paste, _clipboard_can_paste())
        if not secret:
            build.add("trash", "删除", "Del", widget.del_, has_selection)

    build.separate()
    build.add("select_all", "全选", "Ctrl+A", widget.selectAll, bool(widget.text()))


def _fill_text_edit(menu, widget, pos):
    document = widget.document()
    read_only = widget.isReadOnly()
    cursor = widget.textCursor()
    has_selection = cursor.hasSelection()
    build = _Builder(menu)

    if not read_only:
        build.add("undo", "撤销", "Ctrl+Z", widget.undo, document.isUndoAvailable())
        build.add("redo", "重做", "Ctrl+Y", widget.redo, document.isRedoAvailable())
        build.separate()

    if not read_only:
        build.add("scissors", "剪切", "Ctrl+X", widget.cut, has_selection)
    build.add("copy", "复制", "Ctrl+C", widget.copy, has_selection)

    link = ""
    if isinstance(widget, QTextBrowser) and pos is not None:
        try:
            link = widget.anchorAt(pos)
        except Exception:  # noqa: BLE001 - 位置不在视口内时 Qt 直接返回空串
            link = ""
    if link:
        build.add("link", "复制链接地址", None,
                  lambda: QGuiApplication.clipboard().setText(link))

    if not read_only:
        build.add("clipboard", "粘贴", "Ctrl+V", widget.paste, _clipboard_can_paste())
        build.add("trash", "删除", "Del", lambda: _delete_selection(widget),
                  has_selection)

    build.separate()
    build.add("select_all", "全选", "Ctrl+A", widget.selectAll,
              bool(document.toPlainText()))


def _select_label_all(label):
    try:
        label.setSelection(0, len(label.text() or ""))
    except Exception:  # noqa: BLE001 - 富文本标签没有可用的选区接口
        pass


def _fill_label(menu, widget):
    text = widget.text() or ""
    selected = _label_selected_text(widget)
    build = _Builder(menu)
    build.add("copy", "复制", "Ctrl+C",
              lambda: QGuiApplication.clipboard().setText(_label_selected_text(widget)),
              bool(selected))
    build.separate()
    build.add("select_all", "全选", "Ctrl+A", lambda: _select_label_all(widget),
              bool(text))


def build_text_menu(widget, pos=None):
    """按控件类型构造中文右键菜单；不支持的控件返回 None。"""
    menu = QMenu(widget)
    if isinstance(widget, QLineEdit):
        _fill_line_edit(menu, widget)
    elif isinstance(widget, _TEXT_EDITS):
        _fill_text_edit(menu, widget, pos)
    elif isinstance(widget, QLabel):
        _fill_label(menu, widget)
    else:
        menu.deleteLater()
        return None
    return menu


def _label_is_selectable(label):
    flags = label.textInteractionFlags()
    return bool(flags & (Qt.TextInteractionFlag.TextSelectableByMouse
                         | Qt.TextInteractionFlag.TextSelectableByKeyboard))


def _resolve_widget(obj):
    """事件目标可能是 QAbstractScrollArea 的 viewport，映射回文本控件本身。"""
    if isinstance(obj, QLineEdit):
        return obj
    if isinstance(obj, _TEXT_EDITS):
        return obj
    if isinstance(obj, QLabel):
        return obj if _label_is_selectable(obj) else None
    if isinstance(obj, QWidget):
        parent = obj.parentWidget()
        if isinstance(parent, _TEXT_EDITS) and parent.viewport() is obj:
            return parent
    return None


def _defining_class(cls):
    """沿 MRO 找到真正实现 contextMenuEvent 的那个类。"""
    for klass in cls.__mro__:
        if "contextMenuEvent" in klass.__dict__:
            return klass
    return None


def _has_custom_handler(widget):
    """页面自己重写了 contextMenuEvent 时保留原逻辑（含 Python 子类）。"""
    owner = _defining_class(type(widget))
    return owner is None or owner not in _QT_BASES


def _local_pos(widget, obj, event):
    pos = event.pos()
    if obj is widget:
        return pos
    if isinstance(widget, QAbstractScrollArea):
        return widget.viewport().mapFrom(obj, pos)
    return pos


class _TextMenuFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.ContextMenu:
            return False
        widget = _resolve_widget(obj)
        if widget is None or _has_custom_handler(widget):
            return False
        menu = build_text_menu(widget, _local_pos(widget, obj, event))
        if menu is None:
            return False
        menu.exec(event.globalPos())
        menu.deleteLater()
        return True


_filter = None


def install():
    """装到 QApplication 上（幂等；无 QApplication 时静默跳过）。"""
    global _filter
    app = QApplication.instance()
    if app is None or _filter is not None:
        return
    _filter = _TextMenuFilter(app)
    app.installEventFilter(_filter)
