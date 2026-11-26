"""主窗口的短时过渡；只处理外壳，不改变页面或任务的生命周期。"""

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRect, QTimer, Qt, QVariantAnimation
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget


class _WindowSnapshot(QWidget):
    """动画期间绘制窗口快照，避免每帧重排真实页面。"""

    def __init__(self, window, before):
        super().__init__(window, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.before = before
        self.after = before
        self.progress = 0.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.before)
        painter.setOpacity(self.progress)
        painter.drawPixmap(self.rect(), self.after)


class WindowTransitions(QObject):
    """集中持有动画；禁用、重复点击或中断后始终恢复可见的真实窗口。"""

    SHOW_MS = 180
    HIDE_MS = 140
    RESIZE_MS = 200

    def __init__(self, window, enabled=True):
        super().__init__(window)
        self.window = window
        self.enabled = bool(enabled)
        self.dismissing = False
        self._animation = None
        self._overlay = None
        self._dismiss_callback = None
        self._serial = 0

    def prepare_show(self):
        """首次 show 之前准备透明度，避免先完整闪出一帧。"""
        if self.enabled and not self.window.isVisible():
            self.window.setWindowOpacity(0.0)

    def _stop_animation(self):
        animation, self._animation = self._animation, None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _reveal(self):
        self._serial += 1
        self._stop_animation()
        self.window.setWindowOpacity(1.0)
        overlay, self._overlay = self._overlay, None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()

    def cancel(self):
        """托盘恢复等显式显示请求可取消尚未完成的隐藏。"""
        self.dismissing = False
        self._dismiss_callback = None
        self._reveal()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            if self.dismissing:
                self._finish_dismiss()
            else:
                self._reveal()

    def _fade(self, target, duration, finished):
        self._stop_animation()
        animation = QPropertyAnimation(self.window, b"windowOpacity", self)
        animation.setDuration(duration)
        animation.setStartValue(self.window.windowOpacity())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(finished)
        self._animation = animation
        animation.start()

    def appear(self):
        # showMaximized/showNormal 也可能产生 showEvent；不得打断状态切换快照。
        if self._overlay is not None or self.dismissing:
            return
        if not self.enabled or self.window.isMinimized():
            self._reveal()
            return
        self.window.setWindowOpacity(0.0)
        self._fade(1.0, self.SHOW_MS, self._reveal)

    def dismiss(self, callback):
        """确认通过后再淡出。返回 False 时由调用方立即执行原操作。"""
        if self.dismissing:
            return True
        if not self.enabled or not self.window.isVisible() or self.window.isMinimized():
            self._reveal()
            return False
        self._reveal()
        self.dismissing = True
        self._dismiss_callback = callback
        self._fade(0.0, self.HIDE_MS, self._finish_dismiss)
        return True

    def _finish_dismiss(self):
        callback, self._dismiss_callback = self._dismiss_callback, None
        self.dismissing = False
        self._stop_animation()
        # 先 hide/close，再复位透明度，避免淡出终点闪回完整窗口。
        if callback is not None:
            callback()
        self.window.setWindowOpacity(1.0)

    def toggle_max_restore(self):
        if self.dismissing:
            return
        self._reveal()
        maximize = not self.window.isMaximized()
        if (not self.enabled or not self.window.isVisible()
                or self.window.isMinimized() or self.window.isFullScreen()):
            self._set_maximized(maximize)
            return

        before = self.window.grab()
        if before.isNull():
            self._set_maximized(maximize)
            return
        start = QRect(self.window.frameGeometry())
        overlay = self._overlay = _WindowSnapshot(self.window, before)
        overlay.setGeometry(start)
        overlay.show()
        overlay.raise_()
        self.window.setWindowOpacity(0.0)
        # 只对快照做几何动画。真实窗口一次完成原生状态切换，normalGeometry
        # 仍由 Qt 保存，贴边、任务栏工作区和跨屏位置不会被逐帧 resize 覆盖。
        self._set_maximized(maximize)
        serial = self._serial
        QTimer.singleShot(0, lambda: self._animate_resize(serial, start))

    def _set_maximized(self, maximized):
        if maximized:
            self.window.showMaximized()
        else:
            self.window.showNormal()

    def _animate_resize(self, serial, start):
        overlay = self._overlay
        if serial != self._serial or overlay is None:
            return
        end = QRect(self.window.frameGeometry())
        overlay.after = self.window.grab()
        animation = QVariantAnimation(self)
        animation.setDuration(self.RESIZE_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def render(progress):
            overlay.setGeometry(QRect(
                round(start.x() + (end.x() - start.x()) * progress),
                round(start.y() + (end.y() - start.y()) * progress),
                round(start.width() + (end.width() - start.width()) * progress),
                round(start.height() + (end.height() - start.height()) * progress),
            ))
            overlay.progress = progress
            overlay.update()

        animation.valueChanged.connect(render)
        animation.finished.connect(self._reveal)
        self._animation = animation
        animation.start()
