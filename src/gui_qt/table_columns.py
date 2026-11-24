"""全局表格列宽：自适应 / 重置（表头右键 + 每张表格自己的操作行）。

- 所有 QTableWidget / QTableView / QTreeWidget 的水平表头右键：
  列宽自适应 / 重置列宽
- 「自适应 / 重置」两个按钮放在每张表格自己的底部操作行右端（表格所在的
  卡片内），无其它右侧内容时贴到最右；找不到操作行时退化为表格右下角浮层
- 按钮只作用于所属表格，不再占用主窗口状态栏
- 列宽自适应按内容宽度分配并尽量填满视口；重置恢复表格首次显示时的宽度
- 页面不自写列宽菜单或第二套列宽逻辑

本模块只做 UI 表现，不依赖页面业务。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QHBoxLayout, QHeaderView, QMenu, QSizePolicy,
    QTableView, QTableWidget, QToolButton, QTreeWidget, QWidget,
)

from gui_qt import theme

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
    """应用级事件过滤器：表头右键菜单 + 每张表格的操作行按钮。"""

    def _view_of_header(self, header):
        if not isinstance(header, QHeaderView) or header.orientation() != Qt.Horizontal:
            return None
        parent = header.parentWidget()
        return parent if isinstance(parent, _TABLE_TYPES) else None

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Show and isinstance(obj, _TABLE_TYPES):
            QTimer.singleShot(300, lambda v=obj: _capture_defaults(v))
            # 页面可能先建表格、后建底部操作行，显示后补挂几次（重复调用幂等）
            for delay in (0, 120, 400):
                QTimer.singleShot(delay, lambda v=obj: _sync_actions(v))
        elif etype == QEvent.Resize:
            if isinstance(obj, _TABLE_TYPES):
                _sync_actions(obj)
            else:
                host = getattr(obj, "_table_column_host", None)
                if isinstance(host, _TABLE_TYPES):
                    _sync_actions(host)
        elif etype == QEvent.ContextMenu:
            view = self._view_of_header(obj)
            if view is not None:
                _capture_defaults(view)
                _show_header_menu(view, event.globalPos())
                return True
        return False


def install():
    """装到 QApplication 上（幂等；无 QApplication 时静默跳过）。"""
    global _controller
    app = QApplication.instance()
    if app is None or _controller is not None:
        return
    _controller = _TableColumnController(app)
    app.installEventFilter(_controller)


def current_table():
    """当前显示中的表格（测试与嵌入场景用，不参与按钮位置）。"""
    app = QApplication.instance()
    if app is None:
        return None
    focus = app.focusWidget()
    while focus is not None and not isinstance(focus, _TABLE_TYPES):
        focus = focus.parentWidget()
    return focus if isinstance(focus, _TABLE_TYPES) else None


class _ColumnButton(QToolButton):
    """表格操作行里的小按钮：切主题时按当前语义色重设样式。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._repolishing = False

    def apply_theme_style(self):
        try:
            self.setPalette(theme.current_palette())
            self.setStyleSheet(
                "QToolButton#tableColumnButton { background: %s; color: %s; "
                "border: 1px solid %s; border-radius: 6px; padding: 2px 9px; font-size: 11px; }"
                "QToolButton#tableColumnButton:hover { color: %s; border-color: %s; "
                "background: %s; }"
                "QToolButton#tableColumnButton:pressed { background: %s; }"
                "QToolButton#tableColumnButton:disabled { color: %s; border-color: %s; }" % (
                    theme.color("surface"), theme.color("link"), theme.color("border"),
                    theme.color("link"), theme.color("link"), theme.color("surface_alt"),
                    theme.color("surface_alt"),
                    theme.color("muted"), theme.color("border"),
                ))
        except Exception:
            pass

    def changeEvent(self, event):
        if (event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange)
                and not getattr(self, "_repolishing", False)):
            self._repolishing = True
            try:
                self.apply_theme_style()
                self.style().unpolish(self)
                self.style().polish(self)
            finally:
                self._repolishing = False
        super().changeEvent(event)


def _make_column_button(parent, text: str, tip: str, height: int = 24) -> _ColumnButton:
    button = _ColumnButton(parent)
    button.setObjectName("tableColumnButton")
    button.setText(text)
    button.setToolTip(tip)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setFixedHeight(height)
    button.apply_theme_style()
    return button


def _wire(view, widget):
    widget.fit_btn.clicked.connect(lambda: fit_table_columns(view))
    widget.reset_btn.clicked.connect(lambda: reset_table_columns(view))


class _TableColumnActions(QWidget):
    """表格底部操作行右端的「自适应 / 重置」两个按钮。"""

    def __init__(self, view):
        super().__init__()
        self.setObjectName("tableColumnActions")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.fit_btn = _make_column_button(self, "自适应", "本表格列宽自适应")
        self.reset_btn = _make_column_button(
            self, "重置", "重置本表格列宽（恢复首次显示时的宽度）")
        layout.addWidget(self.fit_btn)
        layout.addWidget(self.reset_btn)
        _wire(view, self)

    def apply_theme_style(self):
        self.fit_btn.apply_theme_style()
        self.reset_btn.apply_theme_style()

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.apply_theme_style()
        super().changeEvent(event)


class _TableColumnOverlay(QWidget):
    """兜底浮层：表格自身没有底部操作行时，贴表格右下角显示同一对按钮。"""

    _EDGE_MARGIN = 4

    def __init__(self, view):
        super().__init__(view)
        self.setObjectName("tableColumnOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(3)
        self.fit_btn = _make_column_button(self, "自适应", "本表格列宽自适应", height=20)
        self.reset_btn = _make_column_button(
            self, "重置", "重置本表格列宽（恢复首次显示时的宽度）", height=20)
        layout.addWidget(self.fit_btn)
        layout.addWidget(self.reset_btn)
        _wire(view, self)
        self.apply_theme_style()

    def table_view(self):
        parent = self.parentWidget()
        return parent if isinstance(parent, _TABLE_TYPES) else None

    def apply_theme_style(self):
        try:
            self.setPalette(theme.current_palette())
            self.setStyleSheet(
                "QWidget#tableColumnOverlay { background: %s; border: 1px solid %s; "
                "border-radius: 6px; }" % (theme.color("surface_alt"), theme.color("border")))
        except Exception:
            pass
        self.fit_btn.apply_theme_style()
        self.reset_btn.apply_theme_style()

    def changeEvent(self, event):
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.apply_theme_style()
        super().changeEvent(event)


def _alive(widget):
    """控件可能被页面重建布局时删掉，触碰 C++ 对象可判断是否还活着。"""
    if widget is None:
        return None
    try:
        widget.objectName()
    except RuntimeError:
        return None
    return widget


def _locate_layout(layout, widget):
    """在（可能嵌套的）布局里找到直接放着 widget 的那一层，返回 (布局, 下标)。"""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is widget:
            return layout, index
        sub = item.layout()
        if sub is not None:
            found = _locate_layout(sub, widget)
            if found is not None:
                return found
    return None


def _footer_row_of(view):
    """表格所在容器里紧跟表格之后的那一行（QHBoxLayout），没有就返回 None。"""
    parent = view.parentWidget()
    while parent is not None:
        layout = parent.layout()
        if layout is not None:
            found = _locate_layout(layout, view)
            if found is not None:
                holder, index = found
                follow = holder.itemAt(index + 1) if index + 1 < holder.count() else None
                row = follow.layout() if follow is not None else None
                return row if isinstance(row, QHBoxLayout) else None
        parent = parent.parentWidget()
    return None


def _insert_index(row):
    """右侧只有一个动作按钮时插到它左边（并排）；否则顶到最右。"""
    buttons = []
    for index in range(row.count()):
        item = row.itemAt(index)
        widget = item.widget() if item is not None else None
        if isinstance(widget, QAbstractButton):
            buttons.append((index, widget))
    if len(buttons) == 1 and buttons[0][0] == row.count() - 1:
        return row.count() - 1
    return row.count()


def _ensure_actions(view):
    """把「自适应 / 重置」挂到表格底部操作行右端；没有这一行返回 None。"""
    actions = _alive(getattr(view, "_table_column_actions", None))
    row = _footer_row_of(view)
    if row is None:
        return None
    if actions is not None and row.indexOf(actions) >= 0:
        return actions
    if actions is None:
        actions = _TableColumnActions(view)
        view._table_column_actions = actions
    try:
        row.insertWidget(_insert_index(row), actions)
    except RuntimeError:
        view._table_column_actions = None
        return None
    return actions


def _ensure_overlay(view) -> _TableColumnOverlay:
    overlay = _alive(getattr(view, "_table_column_overlay", None))
    if overlay is None:
        overlay = _TableColumnOverlay(view)
        view._table_column_overlay = overlay
        viewport = view.viewport()
        if viewport is not None:
            viewport._table_column_host = view
    return overlay


def _place_overlay(view):
    """表格没有底部操作行时，把兜底浮层贴到表格右下角；太小则隐藏。"""
    viewport = view.viewport()
    if viewport is None:
        return
    rect = viewport.geometry()
    if rect.width() <= 0 or rect.height() <= 0:
        return
    overlay = _ensure_overlay(view)
    overlay.adjustSize()
    hint = overlay.sizeHint()
    width = max(int(hint.width()), 88)
    height = max(int(hint.height()), 26)
    margin = _TableColumnOverlay._EDGE_MARGIN
    if rect.width() < width + margin * 3 or rect.height() < height + margin * 3:
        overlay.hide()
        return
    overlay.setGeometry(
        rect.x() + rect.width() - width - margin,
        rect.y() + rect.height() - height - margin,
        width, height)
    overlay.show()
    overlay.raise_()


def _sync_actions(view):
    """表格显示/尺寸变化后对齐按钮：优先底部操作行，其次右下角浮层。"""
    if not isinstance(view, _TABLE_TYPES) or not view.isVisible():
        return
    if _ensure_actions(view) is not None:
        overlay = _alive(getattr(view, "_table_column_overlay", None))
        if overlay is not None:
            overlay.hide()
        return
    _place_overlay(view)
