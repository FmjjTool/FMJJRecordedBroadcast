"""控件树导出：把当前页面渲染成 WebUI 需要的 JSON。

只读遍历 QWidget 树，必须在 GUI 线程调用；密码框不导出明文，独立窗口与
原生弹窗不在页面树内，无法镜像。布局用相对父控件的绝对坐标叠加，保证
嵌套滚动区与页签内容跟随 Qt 的实际几何。
"""

from __future__ import annotations

import base64
import re
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QPoint, Qt
from PySide6.QtGui import QFontInfo, QIcon, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDateTimeEdit,
    QDoubleSpinBox, QFrame, QGroupBox, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QProgressBar, QRadioButton, QScrollArea, QSlider, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTextEdit, QToolButton, QTreeWidget,
    QWidget,
)

from gui_qt import theme

_MAX_TEXT = 20000
_MAX_CELL = 500
_MAX_ROWS = 80
_MAX_ITEMS = 300
_DEPTH_LIMIT = 48

_SKIP_CLASSES = {
    "QScrollBar", "QHeaderView", "QTabBar", "QSplitterHandle",
    "QTableCornerButton", "QFocusFrame",
}

_DESKTOP_HINT = re.compile(
    r"浏览|选择文件|选择目录|打开文件|打开目录|另存为|导入|安装|卸载")

_ICON_CACHE: dict[tuple[int, int], str] = {}
_DEFAULT_FG = [""]

_THEME_NAMES = (
    "window", "surface", "surface_alt", "text", "muted", "border", "accent",
    "accent_soft", "on_accent", "link", "accent_hover", "success", "warning",
    "danger", "danger_soft", "danger_border",
)


def theme_tokens() -> dict[str, Any]:
    tokens = {name: theme.color(name) for name in _THEME_NAMES}
    tokens["dark"] = bool(theme.is_dark())
    try:
        palette = theme.current_palette()
        tokens["input_border"] = palette.color(QPalette.Mid).name()
        tokens["hover"] = palette.color(QPalette.Midlight).name()
        tokens["disabled_bg"] = palette.color(QPalette.AlternateBase).name()
        tokens["header_bg"] = palette.color(QPalette.AlternateBase).name()
        tokens["scroll"] = palette.color(QPalette.Mid).name()
    except Exception:
        tokens["input_border"] = tokens["border"]
        tokens["hover"] = tokens["accent_soft"]
        tokens["disabled_bg"] = tokens["surface_alt"]
        tokens["header_bg"] = tokens["surface_alt"]
        tokens["scroll"] = tokens["border"]
    return tokens


def _short_class(widget: QWidget) -> str:
    return widget.metaObject().className()


def _kind(widget: QWidget) -> str:
    if isinstance(widget, QTabWidget):
        return "tabs"
    if isinstance(widget, QTableWidget):
        return "table"
    if isinstance(widget, QTreeWidget):
        return "tree"
    if isinstance(widget, QListWidget):
        return "list"
    if isinstance(widget, QScrollArea):
        return "scroll"
    if isinstance(widget, QComboBox):
        return "combo"
    if isinstance(widget, QCheckBox):
        return "check"
    if isinstance(widget, QRadioButton):
        return "radio"
    if isinstance(widget, QToolButton):
        return "toolbutton"
    if isinstance(widget, QAbstractButton):
        return "button"
    if isinstance(widget, QLineEdit):
        return "line"
    if isinstance(widget, QPlainTextEdit):
        return "plain"
    if isinstance(widget, QTextEdit):
        return "text"
    if isinstance(widget, QDoubleSpinBox):
        return "dspin"
    if isinstance(widget, QSpinBox):
        return "spin"
    if isinstance(widget, QDateTimeEdit):
        return "datetime"
    if isinstance(widget, QSlider):
        return "slider"
    if isinstance(widget, QProgressBar):
        return "progress"
    if isinstance(widget, QGroupBox):
        return "group"
    if isinstance(widget, QSplitter):
        return "splitter"
    if isinstance(widget, QLabel):
        return "label"
    if isinstance(widget, QFrame):
        return "frame"
    return "widget"


def _icon_uri(icon: QIcon | None, size: int) -> str | None:
    if icon is None or icon.isNull():
        return None
    key = (int(icon.cacheKey()), int(size))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached or None
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        _ICON_CACHE[key] = ""
        return None
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        _ICON_CACHE[key] = ""
        return None
    data = base64.b64encode(bytes(buffer.data())).decode("ascii")
    uri = f"data:image/png;base64,{data}"
    if len(_ICON_CACHE) > 600:
        _ICON_CACHE.clear()
    _ICON_CACHE[key] = uri
    return uri


def _trim(text: str, limit: int = _MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（内容过长，已截断）"


_RICH_RE = re.compile(r"<[a-zA-Z/][^>]*>")


def _label_text(label: QLabel) -> str:
    text = label.text()
    if not text:
        return ""
    rich = label.textFormat() == Qt.RichText
    if not rich and label.textFormat() == Qt.AutoText:
        rich = bool(_RICH_RE.search(text))
    if rich:
        try:
            return QTextDocument(text).toPlainText()
        except Exception:
            return text
    return text


def _alignment(widget: QWidget) -> str | None:
    align = getattr(widget, "alignment", None)
    if not callable(align):
        return None
    try:
        value = int(align())
    except Exception:
        return None
    h = "c" if value & int(Qt.AlignHCenter) else (
        "r" if value & int(Qt.AlignRight) else "l")
    v = "c" if value & int(Qt.AlignVCenter) else (
        "b" if value & int(Qt.AlignBottom) else "t")
    return h + v


def _base_node(widget: QWidget, frame: QWidget, node_id: str) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": node_id,
        "k": _kind(widget),
        "cls": _short_class(widget),
    }
    try:
        pos = widget.mapTo(frame, QPoint(0, 0))
    except Exception:
        pos = QPoint(0, 0)
    node["x"] = pos.x()
    node["y"] = pos.y()
    node["w"] = max(0, widget.width())
    node["h"] = max(0, widget.height())
    if not widget.isEnabled():
        node["off"] = True
    tip = widget.toolTip()
    if tip:
        node["tip"] = tip
    try:
        info = QFontInfo(widget.font())
        node["fs"] = int(info.pixelSize())
        node["bold"] = bool(widget.font().bold())
    except Exception:
        pass
    role = widget.foregroundRole()
    if role != QPalette.NoRole and widget.isEnabled():
        try:
            fg = widget.palette().color(role).name()
            if fg != _DEFAULT_FG[0]:
                node["fg"] = fg
        except Exception:
            pass
    for prop in ("variant", "role"):
        value = widget.property(prop)
        if value:
            node[prop] = str(value)
    return node


def _visible_children(widget: QWidget) -> list[QWidget]:
    children: list[QWidget] = []
    for child in widget.children():
        if not isinstance(child, QWidget) or child.isHidden():
            continue
        name = _short_class(child)
        if name in _SKIP_CLASSES or "Popup" in name or "Private" in name:
            continue
        if child.width() <= 0 or child.height() <= 0:
            continue
        children.append(child)
    return children


def _build_children(
    widget: QWidget,
    node: dict[str, Any],
    node_id: str,
    frame: QWidget,
    depth: int,
    id_map: dict[str, QWidget],
) -> None:
    if depth >= _DEPTH_LIMIT:
        return
    children = []
    for index, child in enumerate(_visible_children(widget)):
        child_id = f"{node_id}.{index}"
        child_node = _build(child, frame, child_id, depth + 1, id_map)
        if child_node is not None:
            children.append(child_node)
    if children:
        node["kids"] = children


def _build(
    widget: QWidget,
    frame: QWidget,
    node_id: str,
    depth: int,
    id_map: dict[str, QWidget],
) -> dict[str, Any] | None:
    if depth >= _DEPTH_LIMIT:
        return None
    node = _base_node(widget, frame, node_id)
    kind = node["k"]
    id_map[node_id] = widget

    if kind == "label":
        node["t"] = _trim(_label_text(widget), 4000)
        node["wrap"] = bool(widget.wordWrap())
        align = _alignment(widget)
        if align:
            node["al"] = align
        pixmap = widget.pixmap()
        if pixmap is not None and not pixmap.isNull():
            if pixmap.width() <= 512 and pixmap.height() <= 512:
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                if pixmap.save(buffer, "PNG"):
                    data = base64.b64encode(bytes(buffer.data())).decode("ascii")
                    node["img"] = f"data:image/png;base64,{data}"
        return node

    if kind in ("button", "toolbutton", "check", "radio"):
        node["t"] = widget.text()
        if widget.isCheckable():
            node["chk"] = bool(widget.isChecked())
            node["checkable"] = True
        icon = widget.icon()
        if not icon.isNull():
            size = widget.iconSize().width() or 16
            uri = _icon_uri(icon, max(12, min(32, size)))
            if uri:
                node["icon"] = uri
        menu = getattr(widget, "menu", None)
        if callable(menu) and menu() is not None:
            node["menu"] = True
        if _DESKTOP_HINT.search(node.get("t", "")):
            node["hint"] = "desktop"
        return node

    if kind == "line":
        echo = widget.echoMode()
        node["ro"] = bool(widget.isReadOnly())
        node["ph"] = widget.placeholderText()
        if echo != QLineEdit.Normal:
            node["secret"] = True
            node["ro"] = True
            node["t"] = "•" * min(12, max(6, len(widget.text())))
        else:
            node["t"] = _trim(widget.text(), 4000)
        return node

    if kind in ("text", "plain"):
        node["t"] = _trim(widget.toPlainText())
        node["ro"] = bool(widget.isReadOnly())
        node["ph"] = widget.placeholderText()
        return node

    if kind == "combo":
        count = min(widget.count(), 300)
        node["items"] = [widget.itemText(i) for i in range(count)]
        node["cur"] = int(widget.currentIndex())
        node["editable"] = bool(widget.isEditable())
        return node

    if kind in ("spin", "dspin"):
        node["v"] = widget.value()
        node["min"] = widget.minimum()
        node["max"] = widget.maximum()
        node["step"] = widget.singleStep()
        node["suffix"] = widget.suffix()
        node["prefix"] = widget.prefix()
        return node

    if kind == "slider":
        node["v"] = int(widget.value())
        node["min"] = int(widget.minimum())
        node["max"] = int(widget.maximum())
        return node

    if kind == "datetime":
        node["t"] = widget.text()
        node["ro"] = True
        return node

    if kind == "progress":
        node["v"] = int(widget.value())
        node["min"] = int(widget.minimum())
        node["max"] = int(widget.maximum())
        node["text"] = widget.text()
        return node

    if kind == "group":
        node["t"] = widget.title()
        node["checkable"] = bool(widget.isCheckable())
        if widget.isCheckable():
            node["chk"] = bool(widget.isChecked())
        _build_children(widget, node, node_id, widget, depth, id_map)
        return node

    if kind == "tabs":
        tabs = []
        bar = widget.tabBar()
        for index in range(widget.count()):
            tabs.append({
                "t": widget.tabText(index),
                "off": not widget.isTabEnabled(index),
                "icon": _icon_uri(widget.tabIcon(index), 16),
            })
        node["tabs"] = tabs
        node["cur"] = int(widget.currentIndex())
        bar_height = bar.height() if bar is not None else 0
        if bar_height <= 0 and bar is not None:
            bar_height = bar.sizeHint().height()
        node["barh"] = max(24, int(bar_height))
        current = widget.currentWidget()
        if current is not None and not current.isHidden():
            content = _build(current, widget, f"{node_id}.p", depth + 1, id_map)
            if content is not None:
                node["kids"] = [content]
        return node

    if kind == "scroll":
        viewport = widget.viewport()
        vp = viewport.mapTo(widget, QPoint(0, 0))
        node["vp"] = [vp.x(), vp.y(), viewport.width(), viewport.height()]
        vbar = widget.verticalScrollBar()
        hbar = widget.horizontalScrollBar()
        node["vmax"] = int(vbar.maximum()) if vbar else 0
        node["v"] = int(vbar.value()) if vbar else 0
        node["page"] = int(vbar.pageStep()) if vbar else 0
        node["hmax"] = int(hbar.maximum()) if hbar else 0
        node["hv"] = int(hbar.value()) if hbar else 0
        content = widget.widget()
        if content is not None and not content.isHidden():
            child = _build(content, viewport, f"{node_id}.c", depth + 1, id_map)
            if child is not None:
                child["x"] += vp.x()
                child["y"] += vp.y()
                node["kids"] = [child]
        return node

    if kind == "splitter":
        _build_children(widget, node, node_id, widget, depth, id_map)
        return node

    if kind == "table":
        _fill_table(widget, node)
        return node

    if kind == "list":
        _fill_list(widget, node)
        return node

    if kind == "tree":
        _fill_tree(widget, node)
        return node

    _build_children(widget, node, node_id, widget, depth, id_map)
    return node


def _fill_table(table: QTableWidget, node: dict[str, Any]) -> None:
    viewport = table.viewport()
    vp = viewport.mapTo(table, QPoint(0, 0))
    header = table.horizontalHeader()
    vheader = table.verticalHeader()
    node["vp"] = [vp.x(), vp.y(), viewport.width(), viewport.height()]
    node["hh"] = int(header.height()) if header is not None else 24
    node["rhw"] = int(vheader.width()) if vheader is not None and vheader.isVisible() else 0
    rows_total = table.rowCount()
    cols_total = table.columnCount()
    vbar = table.verticalScrollBar()
    hbar = table.horizontalScrollBar()
    first_row = table.rowAt(0)
    if first_row < 0:
        first_row = int(vbar.value()) if vbar else 0
    visible_rows = max(1, int(vbar.pageStep())) if vbar else 10
    last_row = table.rowAt(viewport.height() - 1)
    if last_row < 0:
        last_row = first_row + visible_rows - 1
    last_row = min(last_row, first_row + _MAX_ROWS - 1, rows_total - 1)
    first_col = table.columnAt(0)
    if first_col < 0:
        first_col = 0
    last_col = table.columnAt(viewport.width() - 1)
    if last_col < 0:
        last_col = min(cols_total - 1, first_col + 12)
    columns = []
    for col in range(first_col, last_col + 1):
        if header is not None and header.isSectionHidden(col):
            continue
        item = table.horizontalHeaderItem(col)
        columns.append({
            "c": col,
            "t": item.text() if item else "",
            "w": int(table.columnWidth(col)),
        })
    rows = []
    for row in range(first_row, last_row + 1):
        if vheader is not None and vheader.isSectionHidden(row):
            continue
        cells = []
        for column in columns:
            item = table.item(row, column["c"])
            cell: dict[str, Any] = {"c": column["c"], "t": ""}
            if item is not None:
                cell["t"] = _trim(item.text(), _MAX_CELL)
                if item.data(Qt.CheckStateRole) is not None:
                    cell["cs"] = int(item.checkState().value)
            cells.append(cell)
        rows.append({"r": row, "h": int(table.rowHeight(row)), "cells": cells})
    node["cols"] = columns
    node["rows"] = rows
    node["total"] = [rows_total, cols_total]
    node["vmax"] = int(vbar.maximum()) if vbar else 0
    node["v"] = int(vbar.value()) if vbar else 0
    node["page"] = int(vbar.pageStep()) if vbar else 0
    node["hmax"] = int(hbar.maximum()) if hbar else 0
    node["hv"] = int(hbar.value()) if hbar else 0
    node["cur"] = [int(table.currentRow()), int(table.currentColumn())]


def _fill_list(widget: QListWidget, node: dict[str, Any]) -> None:
    viewport = widget.viewport()
    vp = viewport.mapTo(widget, QPoint(0, 0))
    node["vp"] = [vp.x(), vp.y(), viewport.width(), viewport.height()]
    count = widget.count()
    vbar = widget.verticalScrollBar()
    top_item = widget.itemAt(0, 0)
    first = 0
    if top_item is not None:
        first = max(0, widget.row(top_item))
    elif vbar is not None:
        first = max(0, int(vbar.value()))
    visible = max(1, int(vbar.pageStep())) if vbar else 10
    last_item = widget.itemAt(0, viewport.height() - 1)
    last = widget.row(last_item) if last_item is not None else first + visible - 1
    last = min(last, first + _MAX_ITEMS - 1, count - 1)
    row_height = 24
    if count:
        rect = widget.visualItemRect(widget.item(first))
        if rect.height() > 0:
            row_height = rect.height()
    items = []
    for row in range(first, last + 1):
        item = widget.item(row)
        if item is None or item.isHidden():
            continue
        entry: dict[str, Any] = {"r": row, "t": _trim(item.text(), _MAX_CELL)}
        if item.data(Qt.CheckStateRole) is not None:
            entry["cs"] = int(item.checkState().value)
        uri = _icon_uri(item.icon(), 16)
        if uri:
            entry["icon"] = uri
        items.append(entry)
    node["items"] = items
    node["rh"] = int(row_height)
    node["total"] = count
    node["vmax"] = int(vbar.maximum()) if vbar else 0
    node["v"] = int(vbar.value()) if vbar else 0
    node["page"] = int(vbar.pageStep()) if vbar else 0


def _fill_tree(widget: QTreeWidget, node: dict[str, Any]) -> None:
    viewport = widget.viewport()
    vp = viewport.mapTo(widget, QPoint(0, 0))
    node["vp"] = [vp.x(), vp.y(), viewport.width(), viewport.height()]
    entries: list[dict[str, Any]] = []
    counter = {"n": 0}

    def walk(item, path: str, level: int) -> None:
        if counter["n"] >= _MAX_ITEMS:
            return
        counter["n"] += 1
        entry: dict[str, Any] = {
            "p": path,
            "t": _trim(item.text(0), _MAX_CELL),
            "lv": level,
            "exp": bool(item.isExpanded()),
            "kids": item.childCount() > 0,
        }
        if item.data(0, Qt.CheckStateRole) is not None:
            entry["cs"] = int(item.checkState(0).value)
        uri = _icon_uri(item.icon(0), 16)
        if uri:
            entry["icon"] = uri
        entries.append(entry)
        if item.isExpanded():
            for index in range(item.childCount()):
                walk(item.child(index), f"{path}.{index}", level + 1)

    for index in range(widget.topLevelItemCount()):
        walk(widget.topLevelItem(index), str(index), 0)
    row_height = 22
    if entries:
        first_item = widget.topLevelItem(0)
        if first_item is not None:
            rect = widget.visualItemRect(first_item)
            if rect.height() > 0:
                row_height = rect.height()
    vbar = widget.verticalScrollBar()
    node["items"] = entries
    node["rh"] = int(row_height)
    node["vmax"] = int(vbar.maximum()) if vbar else 0
    node["v"] = int(vbar.value()) if vbar else 0
    node["page"] = int(vbar.pageStep()) if vbar else 0


def _structure(node: dict[str, Any], parts: list[str]) -> None:
    parts.append(f"{node['id']}:{node['k']}")
    for child in node.get("kids", []):
        _structure(child, parts)


def build_snapshot(
    page: QWidget,
    page_key: str,
    title: str,
    version: int,
) -> tuple[dict[str, Any], dict[str, QWidget]]:
    """返回 (快照, id→控件映射)；必须在 GUI 线程调用。"""
    app = QApplication.instance()
    _DEFAULT_FG[0] = (app.palette().color(QPalette.WindowText).name()
                      if app is not None else "")
    id_map: dict[str, QWidget] = {}
    root = _build(page, page, "r", 0, id_map)
    if root is None:
        root = {
            "id": "r", "k": "page", "cls": _short_class(page),
            "x": 0, "y": 0, "w": page.width(), "h": page.height(),
        }
    else:
        root["x"] = 0
        root["y"] = 0
        root["w"] = page.width()
        root["h"] = page.height()
    parts: list[str] = []
    _structure(root, parts)
    snapshot = {
        "v": int(version),
        "page": page_key,
        "title": title,
        "size": [page.width(), page.height()],
        "tokens": theme_tokens(),
        "skel": "|".join(parts),
        "root": root,
    }
    return snapshot, id_map
