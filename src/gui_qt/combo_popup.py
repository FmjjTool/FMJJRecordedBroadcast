"""下拉弹层圆角：视图圆角 + 容器透明 + 滚动条缩进。

Qt 的 QComboBox 弹层是独立顶层窗口（类名 QComboBoxPrivateContainer）：
只给 QAbstractItemView 写 border-radius 时，容器仍按不透明矩形绘制——首帧
直角；滚动条又是子控件，不受父级圆角裁剪，会一直画到右下角。这里在弹层
Show 时补三件事：容器透明/无边框、视图 8px 圆角、滚动条上下缩进 8px。
"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication, QWidget

_POPUP_CLASS = "QComboBoxPrivateContainer"

_VIEW_QSS = (
    "QAbstractItemView { background: palette(base); color: palette(text);"
    " border: 1px solid palette(mid); border-radius: 8px; padding: 4px;"
    " outline: 0; selection-background-color: palette(highlight);"
    " selection-color: palette(highlighted-text); }"
    "QScrollBar:vertical { background: transparent; width: 8px; margin: 8px 4px 8px 0; }"
    "QScrollBar::handle:vertical { background: palette(mid); border-radius: 3px; min-height: 24px; }"
    "QScrollBar::handle:vertical:hover { background: palette(dark); }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
)


def _style_popup(popup):
    if not popup.testAttribute(Qt.WA_TranslucentBackground):
        popup.setAttribute(Qt.WA_TranslucentBackground, True)
        popup.setAutoFillBackground(False)
        popup.setWindowFlag(Qt.FramelessWindowHint, True)
        popup.setStyleSheet("background: transparent; border: none;")
    view = popup.findChild(QAbstractItemView)
    if view is not None and view.styleSheet() != _VIEW_QSS:
        view.setStyleSheet(_VIEW_QSS)


class _ComboPopupFilter(QObject):
    """应用级：弹层显示时套上圆角三件套。"""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Show or not isinstance(obj, QWidget):
            return False
        if obj.metaObject().className() != _POPUP_CLASS:
            return False
        _style_popup(obj)
        # 布局完成后再套一次：Show 时列表尺寸还没最终确定
        QTimer.singleShot(0, lambda o=obj: _style_popup(o))
        return False


_filter = None


def install():
    """装到 QApplication 上（幂等；无 QApplication 时静默跳过）。"""
    global _filter
    app = QApplication.instance()
    if app is None or _filter is not None:
        return
    _filter = _ComboPopupFilter(app)
    app.installEventFilter(_filter)