"""全局表格列宽：自适应 / 重置（表头右键 + 状态栏「列宽」按钮）。

- 所有 QTableWidget / QTableView / QTreeWidget 的水平表头右键：
  列宽自适应 / 重置列宽
- 主窗口状态栏「列宽」菜单按钮对当前（最后聚焦的）表格执行同样操作
- 列宽自适应按内容宽度分配并尽量填满视口；重置恢复表格首次显示时的宽度
- 页面不自写列宽菜单或第二套列宽逻辑

本模块只做 UI 表现，不依赖页面业务。
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHeaderView, QMenu, QTableView, QTableWidget, QToolButton,
    QTreeWidget,
)

_TABLE_TYPES = (QTableWidget, QTableView, QTreeWidget)
_MIN_WIDTH = 40
_MAX_WIDTH = 520
_controller: "_TableColumnController | None" = None


def _header_of(view):
    try:
        if isinstance(view, QTreeWidget):
            return view.header()
        return view.horizontalHeader()
    except Exception:
        return None


def _row_count(view) -> int:
    if isinstance(view, QTableWidget):
        return view.rowCount()
    if isinstance(view, QTreeWidget):
        return view.topLevelItemCount()
    model = view.model()
    return int(model.rowCount()) if model is not None else 0


def _header_text(view, col: int) -> str:
    if isinstance(view, QTableWidget):
        item = view.horizontalHeaderItem(col)
        return item.text() if item is not None else ""
    if isinstance(view, QTreeWidget):
        item = view.headerItem()
        return item.text(col) if item is not None else ""
    header = _header_of(view)
    model = view.model()
    if header is not None and model is not None:
        data = model.headerData(col, Qt.Horizontal, Qt.DisplayRole)
        return str(data or "")
    return ""


def _cell_text(view, row: int, col: int) -> str:
    if isinstance(view, QTableWidget):
        item = view.item(row, col)
        return item.text() if item is not None else ""
    if isinstance(view, QTreeWidget):
        item = view.topLevelItem(row)
        return item.text(col) if item is not None else ""
    model = view.model()
    if model is not None:
        data = model.index(row, col).data(Qt.DisplayRole)
        return str(data or "")
    return ""


def _capture_defaults(view):
    if getattr(view, "_table_column_defaults", None):
        return
    header = _header_of(view)
    if header is None or header.count() == 0:
        return
    widths = [int(header.sectionSize(i)) for i in range(header.count())]
    if sum(widths) <= 0:
        return
    view._table_column_defaults = widths


def _desired_widths(view) -> list[int]:
    header = _header_of(view)
    if header is None:
        return []
    header_fm = header.fontMetrics()
    cell_fm = view.fontMetrics()
    rows = min(_row_count(view), 300)
    desired = []
    for col in range(header.count()):
        width = header_fm.horizontalAdvance(_header_text(view, col)) + 28
        for row in range(rows):
            text = _cell_text(view, row, col)
            if text:
                width = max(width, cell_fm.horizontalAdvance(text) + 24)
        desired.append(min(max(width, _MIN_WIDTH), _MAX_WIDTH))
    return desired


def fit_table_columns(view):
    """按内容宽度分配列宽，并尽量填满视口；不改变表格数据与排序。"""
    header = _header_of(view)
    if header is None or header.count() == 0:
        return
    viewport = view.viewport().width()
    if viewport <= 0:
        return
    desired = _desired_widths(view)
    if not desired:
        return
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(False)
    total = sum(desired)
    if total < viewport:
        extra = viewport - total - 2
        main = max(range(len(desired)), key=lambda i: desired[i])
        desired[main] += max(0, extra)
    elif total > viewport - 2:
        overflow = total - (viewport - 2)
        while overflow > 0:
            idx = max(range(len(desired)), key=lambda i: desired[i])
            cut = min(overflow, desired[idx] - _MIN_WIDTH)
            if cut <= 0:
                break
            desired[idx] -= cut
            overflow -= cut
    for col, width in enumerate(desired):
        view.setColumnWidth(col, int(width))


def reset_table_columns(view):
    """恢复表格首次显示时的列宽；没有快照时退化为自适应。"""
    header = _header_of(view)
    if header is None or header.count() == 0:
        return
    defaults = getattr(view, "_table_column_defaults", None)
    if not defaults or len(defaults) != header.count():
        fit_table_columns(view)
        return
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(False)
    for col, width in enumerate(defaults):
        view.setColumnWidth(col, int(width))


def _show_header_menu(view, pos):
    menu = QMenu(view)
    act_fit = menu.addAction("列宽自适应")
    act_reset = menu.addAction("重置列宽")
    act_fit.triggered.connect(lambda: fit_table_columns(view))
    act_reset.triggered.connect(lambda: reset_table_columns(view))
    menu.exec(pos)


class _TableColumnController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._last = None

    def _remember(self, view):
        self._last = weakref.ref(view)

    def _view_of_header(self, header):
        if not isinstance(header, QHeaderView) or header.orientation() != Qt.Horizontal:
            return None
        parent = header.parentWidget()
        return parent if isinstance(parent, _TABLE_TYPES) else None

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.FocusIn:
            view = obj if isinstance(obj, _TABLE_TYPES) else self._view_of_header(obj)
            if view is not None:
                self._remember(view)
        elif etype == QEvent.Show and isinstance(obj, _TABLE_TYPES):
            QTimer.singleShot(300, lambda v=obj: _capture_defaults(v))
        elif etype == QEvent.ContextMenu:
            view = self._view_of_header(obj)
            if view is not None:
                self._remember(view)
                _capture_defaults(view)
                _show_header_menu(view, event.globalPos())
                return True
        return False

    def current(self):
        if self._last is None:
            return None
        view = self._last()
        if view is None or not view.isVisible():
            return None
        return view


def install():
    """装到 QApplication 上（幂等；无 QApplication 时静默跳过）。"""
    global _controller
    app = QApplication.instance()
    if app is None or _controller is not None:
        return
    _controller = _TableColumnController(app)
    app.installEventFilter(_controller)


def current_table():
    return _controller.current() if _controller is not None else None


def set_current_table(view):
    """显式指定当前表格（测试/嵌入场景）；正常由焦点与表头右键自动记录。"""
    install()
    if _controller is not None and isinstance(view, _TABLE_TYPES):
        _controller._remember(view)


class _ColumnButton(QToolButton):
    """状态栏窄按钮：切主题时重新解析内联 QSS 的 palette 角色。"""

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.style().unpolish(self)
            self.style().polish(self)
        super().changeEvent(event)


def make_button(parent) -> QToolButton:
    """创建状态栏「列宽」菜单按钮；对当前表格执行自适应/重置。"""
    install()
    button = _ColumnButton(parent)
    button.setObjectName("tableColumnButton")
    button.setText("列宽")
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setFixedHeight(22)
    button.setStyleSheet(
        "QToolButton#tableColumnButton { background: palette(base); color: palette(link); "
        "border: 1px solid palette(mid); border-radius: 6px; padding: 2px 8px; font-size: 11px; }"
        "QToolButton#tableColumnButton:hover { color: palette(link); border-color: palette(highlight); "
        "background: palette(midlight); }"
        "QToolButton#tableColumnButton:disabled { color: palette(placeholder-text); "
        "border-color: palette(shadow); }")
    menu = QMenu(button)
    act_fit = menu.addAction("列宽自适应")
    act_reset = menu.addAction("重置列宽")

    def run(action):
        view = current_table()
        if view is not None:
            action(view)

    act_fit.triggered.connect(lambda: run(fit_table_columns))
    act_reset.triggered.connect(lambda: run(reset_table_columns))

    def refresh():
        view = current_table()
        act_fit.setEnabled(view is not None)
        act_reset.setEnabled(view is not None)
        button.setToolTip(
            "对当前表格：列宽自适应 / 重置列宽" if view is not None
            else "先点击一个表格，再使用列宽操作")

    menu.aboutToShow.connect(refresh)
    refresh()
    button.setMenu(menu)
    return button
