"""整行可点击的复选组件：点行内任意位置即切换勾选，不必精确点中小框。

- RowCheckList: QListWidget 子类，条目复选框整行触发（点复选框本身不双重切换）
- make_row_clickable: 自定义行容器（QFrame/QWidget + 内嵌 QCheckBox）整行触发
- GroupMultiSelect: 分组多选按钮（账号获取/账号追踪共用，一账号绑多组）
"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QMenu, QPushButton, QListWidget, QStyle, QStyleOptionViewItem, QWidget,
)


class RowCheckList(QListWidget):
    """点击整行任意位置切换复选框的列表（点复选框指示框本身由 Qt 默认处理）。"""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item is not None and (item.flags() & Qt.ItemIsUserCheckable):
                if not self._hit_indicator(item, pos):
                    item.setCheckState(
                        Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        super().mousePressEvent(event)

    def _hit_indicator(self, item, pos) -> bool:
        """判断点击位置是否落在该条目的复选框指示框上。"""
        opt = QStyleOptionViewItem()
        opt.initFrom(self)
        opt.decorationPosition = QStyleOptionViewItem.Left
        opt.features = QStyleOptionViewItem.HasCheckIndicator
        opt.state |= QStyle.State_Enabled
        if item.checkState() == Qt.Checked:
            opt.state |= QStyle.State_On
        else:
            opt.state |= QStyle.State_Off
        opt.rect = self.visualRect(self.indexFromItem(item))
        rect = self.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt, self)
        return rect.contains(pos)


class _RowClickHandler(QObject):
    """事件过滤器：点击行容器空白/文字区域时切换内嵌复选框。

    点击复选框本身的事件由复选框自己消费，不会传到行容器，天然避免双重切换。
    """

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            cb = watched.property("row_checkbox")
            if cb is not None:
                pos_cb = cb.mapFrom(watched, event.position().toPoint())
                if not cb.rect().contains(pos_cb):
                    cb.toggle()
        return False


_row_click_handler = _RowClickHandler()


def make_row_clickable(row: QWidget, checkbox):
    """让自定义行（含 QCheckBox 的容器）整行点击切换复选框。"""
    row.setProperty("row_checkbox", checkbox)
    row.installEventFilter(_row_click_handler)
    row.setCursor(Qt.PointingHandCursor)


class GroupMultiSelect(QPushButton):
    """分组多选按钮：点击弹出勾选菜单，一个账号可同时绑定多个组。

    - set_groups({组号: 组名}) 刷新可选组（保留已选交集）
    - set_selected(["11", "22"]) / selected() -> [组号]
    - 勾选变化发 changed 信号（用于启用「保存全部修改」）
    """

    changed = Signal()

    def __init__(self, width=126, parent=None):
        super().__init__(parent)
        self._groups: dict = {}       # {gid: gname}
        self._selected: list = []     # [gid]
        self.setFixedWidth(width)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("text-align: left; padding: 2px 8px;")
        self._menu = QMenu(self)
        self.clicked.connect(self._show_menu)
        self._refresh()

    def _show_menu(self):
        menu = self._menu
        menu.clear()
        if not self._groups:
            act = menu.addAction("（暂无分组）")
            act.setEnabled(False)
        for gid, gname in self._groups.items():
            act = menu.addAction(f"{gid} {gname}")
            act.setCheckable(True)
            act.setChecked(gid in self._selected)
            act.toggled.connect(lambda on, g=gid: self._toggle(g, on))
        menu.addSeparator()
        clear = menu.addAction("无分组（清除全部）")
        clear.triggered.connect(self._clear_all)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _toggle(self, gid, on):
        if on and gid not in self._selected:
            self._selected.append(gid)
        elif not on and gid in self._selected:
            self._selected.remove(gid)
        self._refresh()
        self.changed.emit()

    def _clear_all(self):
        if self._selected:
            self._selected = []
            self._refresh()
            self.changed.emit()

    def _refresh(self):
        """按钮文本：无组=无分组 / 多组=组号相加。"""
        if not self._selected:
            self.setText("无分组")
        else:
            self.setText("组" + "+".join(self._selected))
        self.setToolTip("已绑定：" + ("、".join(
            f"{g} {self._groups.get(g, '')}".strip() for g in self._selected) or "无"))

    # ---------- 对外 API ----------
    def set_groups(self, groups: dict):
        """刷新可选分组（保留仍存在的已选项）。"""
        self._groups = dict(groups or {})
        self._selected = [g for g in self._selected if g in self._groups]
        self._refresh()

    def set_selected(self, gids):
        self._selected = [g for g in (gids or []) if g in self._groups]
        self._refresh()

    def selected(self) -> list:
        return list(self._selected)
