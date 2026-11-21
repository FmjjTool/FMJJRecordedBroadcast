"""滚轮守卫：滚轮只滚页面，不偷偷改控件值。

Qt 里 `QComboBox` 默认 WheelFocus：鼠标停在上面滚一格就换选项，改完还看不出来；
`QTabBar` 会随滚轮切页签，数字框/滑条同理。项目规矩是「滚轮只滚页面」，
改值必须点开列表选或拖动滑块。

本模块装一个应用级事件过滤器：在下拉框/页签/数字框/滑条上收到滚轮时，把事件转给
最近的滚动区，控件自身不再改值。确有需要随滚轮改值的控件声明
`setProperty("wheelOk", True)` 放行（只能由用户单项授权后使用）。
注意：滚动条（QScrollBar）不能进名单，否则页面就滚不动了。
"""

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider, QTabBar,
)

_GUARDED = (QComboBox, QTabBar, QSlider, QAbstractSpinBox)


def _scroll_area_of(widget):
    """最近的滚动区（下拉框可能嵌在对话框/卡片/页签里）。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class _WheelGuard(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel or not isinstance(obj, _GUARDED):
            return False
        if obj.property("wheelOk"):
            return False
        area = _scroll_area_of(obj)
        if area is not None:
            viewport = area.viewport()
            local = viewport.mapFrom(obj, event.position().toPoint())
            forwarded = QWheelEvent(
                QPointF(local), QPointF(viewport.mapToGlobal(local)),
                event.pixelDelta(), event.angleDelta(), event.buttons(),
                event.modifiers(), event.phase(), event.inverted())
            QApplication.sendEvent(viewport, forwarded)
        # 吞掉原始事件：不然控件自己还会把值改掉
        return True


_guard = None


def install():
    """装到 QApplication 上（幂等；无 QApplication 时静默跳过）。"""
    global _guard
    app = QApplication.instance()
    if app is None or _guard is not None:
        return
    _guard = _WheelGuard(app)
    app.installEventFilter(_guard)
