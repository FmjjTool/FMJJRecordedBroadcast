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
        if item is not None:
            return item.text()
        # 单元格里放的是下拉框 / 标签时按控件当前文本计宽，否则自适应会把这类列压没
        widget = view.cellWidget(row, col)
        if widget is not None:
            for getter in ("currentText", "text"):
                func = getattr(widget, getter, None)
                if callable(func):
                    try:
                        return str(func() or "")
                    except Exception:  # noqa: BLE001 - 控件已销毁时按空文本
                        return ""
        return ""
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


def _proportional(total, weights):
    """按权重分配整数像素，余数按小数部分分配，保证总宽精确。"""
    weight_sum = sum(weights)
    if total <= 0 or weight_sum <= 0:
        return [0] * len(weights)
    values = [total * weight // weight_sum for weight in weights]
    remainder = total - sum(values)
    order = sorted(range(len(weights)),
                   key=lambda i: total * weights[i] % weight_sum, reverse=True)
    for index in order[:remainder]:
        values[index] += 1
    return values


def _configured_widths(view, desired):
    """显式配置的表格：固定列不动，主文本列保底，其余列按容量收缩。"""
    policy = view._table_column_policy
    header = _header_of(view)
    visible = [i for i in range(header.count()) if not view.isColumnHidden(i)]
    fixed = policy["fixed"]
    flexible = [i for i in visible if i not in fixed]
    minimums = {i: policy["minimums"].get(i, _MIN_WIDTH) for i in flexible}
    widths = {i: fixed[i] if i in fixed else max(desired[i], minimums[i])
              for i in visible}
    available = max(0, view.viewport().width() - 2)
    overflow = sum(widths.values()) - available
    preferred = [i for i in policy["stretch"] if i in flexible]
    if overflow > 0:
        # 先消化主文本列超出保底宽度的部分，再按其余列可收缩容量分摊。
        for columns in (preferred, [i for i in flexible if i not in preferred]):
            capacities = [max(0, widths[i] - minimums[i]) for i in columns]
            cut = min(overflow, sum(capacities))
            for i, amount in zip(columns, _proportional(cut, capacities)):
                widths[i] -= amount
            overflow -= cut
        if overflow > 0 and flexible:
            # 极窄视口放不下所有保底宽度时，只有弹性列按剩余空间缩小。
            room = max(len(flexible), available - sum(widths[i] for i in visible
                                                      if i in fixed))
            shares = _proportional(room - len(flexible),
                                   [widths[i] for i in flexible])
            for i, amount in zip(flexible, shares):
                widths[i] = 1 + amount
    elif overflow < 0 and flexible:
        columns = preferred or [max(flexible, key=lambda i: widths[i])]
        shares = _proportional(-overflow, [widths[i] for i in columns])
        for i, amount in zip(columns, shares):
            widths[i] += amount
    return widths


def _apply_configured_widths(view, desired=None):
    header = _header_of(view)
    if header is None or header.count() == 0 or view.viewport().width() <= 0:
        return
    controller = view._table_column_sizing
    controller.applying = True
    try:
        widths = _configured_widths(view, desired or _desired_widths(view))
        header.setStretchLastSection(False)
        for col in range(header.count()):
            header.setSectionResizeMode(
                col, QHeaderView.Fixed if col in view._table_column_policy["fixed"]
                else QHeaderView.Interactive)
        for col, width in widths.items():
            view.setColumnWidth(col, int(width))
        _capture_defaults(view)
        controller.initialized = True
    finally:
        controller.applying = False


class _ConfiguredColumnSizing(QObject):
    """只服务于主动接入的表格；用户调宽后暂停随窗口自动调整。"""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.applying = False
        self.initialized = False
        self.automatic = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fit)
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        _header_of(view).sectionResized.connect(self._resized)

    def resume(self):
        self.automatic = self.view._table_column_policy["auto_fit"]

    def _resized(self, _column, old_size, new_size):
        if not self.initialized or self.applying:
            return
        if old_size == 0 or new_size == 0:
            # 隐藏/重新显示列不算手动拖宽，重新分配剩余可见列。
            if self.automatic:
                self.timer.start(0)
            return
        self.automatic = False
        self.timer.stop()

    def _fit(self):
        if self.automatic and self.view.isVisible():
            _apply_configured_widths(self.view)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Show, QEvent.Resize) and self.automatic:
            self.timer.start(0)
        return False


def configure_table_columns(view, *, minimums=None, fixed=None, stretch=None,
                            auto_fit=False):
    """显式接入列宽策略；未调用的表格保持原行为。

    minimums/fixed 为「列编号: 像素宽度」，stretch 为吸收剩余空间的主文本列。
    auto_fit 随显示与窗口尺寸调整；手动调宽后暂停，点「自适应」恢复。
    """
    if not isinstance(view, _TABLE_TYPES):
        raise TypeError("需要表格控件")
    view._table_column_policy = {
        "minimums": {int(i): max(1, int(w)) for i, w in (minimums or {}).items()},
        "fixed": {int(i): max(1, int(w)) for i, w in (fixed or {}).items()},
        "stretch": list(dict.fromkeys(int(i) for i in (stretch or ()))),
        "auto_fit": bool(auto_fit),
    }
    view._table_column_defaults = None
    header = _header_of(view)
    # 保底算法已逐列约束；允许极窄窗口时弹性列进一步收缩。
    header.setMinimumSectionSize(1)
    controller = getattr(view, "_table_column_sizing", None)
    if controller is None:
        controller = _ConfiguredColumnSizing(view)
        view._table_column_sizing = controller
    controller.initialized = False
    controller.resume()
    if auto_fit and view.isVisible():
        controller.timer.start(0)
    return controller


def fit_table_columns(view):
    """按内容宽度分配列宽，并尽量填满视口；不改变表格数据与排序。"""
    if getattr(view, "_table_column_policy", None) is not None:
        view._table_column_sizing.resume()
        _apply_configured_widths(view)
        return
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
    if getattr(view, "_table_column_policy", None) is not None:
        view._table_column_sizing.resume()
        _apply_configured_widths(view, defaults)
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
    if view.property("tableColumnsSelfManaged"):
        # 页面自己管列宽（如映射窗口的「本地字段 / 腾讯字段」双列）：不挂全局按钮
        overlay = _alive(getattr(view, "_table_column_overlay", None))
        if overlay is not None:
            overlay.hide()
        return
    if _ensure_actions(view) is not None:
        overlay = _alive(getattr(view, "_table_column_overlay", None))
        if overlay is not None:
            overlay.hide()
        return
    _place_overlay(view)
