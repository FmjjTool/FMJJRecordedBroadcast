"""整行可点击的复选组件：点行内任意位置即切换勾选，不必精确点中小框。

- RowCheckList: QListWidget 子类，条目复选框整行触发（点复选框本身不双重切换）
- make_row_clickable: 自定义行容器（QFrame/QWidget + 内嵌 QCheckBox）整行触发
- GroupMultiSelect: 分组多选按钮（账号获取/账号追踪共用，一账号绑多组）
- ToggleSwitch: 左右滑动开关（开关型单选，替代外观笨重的复选框）
"""

from PySide6.QtCore import QEvent, QEasingCurve, QObject, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QLabel, QMenu, QPushButton, QListWidget, QSizePolicy, QStyle,
    QStyleOptionViewItem, QWidget,
)

from gui_qt import theme


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


class ElidedLabel(QLabel):
    """单行省略号标签：宽度不够时截断加「…」，完整文字进 tooltip。

    列表里用 QLabel 的自动换行会让每行做 height-for-width 布局计算，几百行时
    布局耗时明显（实测 300 行约多 45ms）；改成忽略水平 sizeHint 的单行标签，
    文字再长也不会撑出横向滚动条。
    """

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._apply_elide()

    def _apply_elide(self):
        width = self.contentsRect().width()
        if not self._full_text or width <= 0:
            super().setText(self._full_text)
            return
        text = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)
        super().setText(text)


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


class ToggleSwitch(QAbstractButton):
    """左右滑动开关：点整块切换，滑块带 120ms 过渡。

    与复选框接口一致（isChecked / toggled / clicked）。程序同步状态用
    `set_state()`：不播动画、不发 toggled，避免刷新逻辑与用户操作互相触发。
    颜色全部取自当前 QPalette（Mid / Highlight / HighlightedText），换主题后
    由调用方 update() 重绘即可。
    """

    def __init__(self, parent=None, width=36, height=20):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(width, height)
        self._offset = 0.0
        self._ring = False  # 只在键盘 Tab 聚焦时画焦点圈（鼠标点过不画，免得看着像已开启）
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def set_state(self, checked):
        """程序同步开关状态（不播动画、不发 toggled）。"""
        self._anim.stop()
        self.blockSignals(True)
        self.setChecked(bool(checked))
        self.blockSignals(False)
        self._offset = 1.0 if checked else 0.0
        self.update()

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_anim(self, value):
        self._offset = float(value)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._ring = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._ring = False
        self.update()

    def paintEvent(self, _event):
        pal = theme.current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 控件比轨道大 1px：焦点圈画在轨道外侧也不会被控件边界裁掉
        track_rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = track_rect.height() / 2
        off = pal.color(QPalette.Mid)
        on = pal.color(QPalette.Highlight)
        track = QColor(
            round(off.red() + (on.red() - off.red()) * self._offset),
            round(off.green() + (on.green() - off.green()) * self._offset),
            round(off.blue() + (on.blue() - off.blue()) * self._offset))
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(track_rect, radius, radius)
        knob = track_rect.height() - 4
        left = track_rect.left() + 2 + (track_rect.width() - knob - 4) * self._offset
        painter.setBrush(pal.color(QPalette.HighlightedText))
        painter.drawEllipse(QRectF(left, track_rect.top() + 2, knob, knob))
        if self._ring:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(pal.color(QPalette.Highlight), 1))
            focus = track_rect.adjusted(-1, -1, 1, 1)
            painter.drawRoundedRect(focus, radius + 1, radius + 1)
        painter.end()

