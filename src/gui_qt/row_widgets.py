"""整行可点击的复选组件：点行内任意位置即切换勾选，不必精确点中小框。

- RowCheckList: QListWidget 子类，条目复选框整行触发（点复选框本身不双重切换）
- make_row_clickable: 自定义行容器（QFrame/QWidget + 内嵌 QCheckBox）整行触发
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QListWidget, QStyle, QStyleOptionViewItem, QWidget,
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
    """让自定义行（含 QCheckBox 的容器）整行点击切换勾选。"""
    row.setProperty("row_checkbox", checkbox)
    row.installEventFilter(_row_click_handler)
    row.setCursor(Qt.PointingHandCursor)
