"""启动骨架占位页（2026-10 性能重构）。

主窗口构造完成即显示本页（内容区灰块骨架 + 呼吸动画），
窗口秒开不白屏；真实首页懒加载完成后由 _deferred_boot 移除。
只占位右侧内容区——真实左侧导航在 __init__ 同步构建，骨架显示时已就位，
不再画导航灰块（否则假导航列会挤进内容区，视觉错位）。
颜色取自当前 QPalette 角色，天然跟随浅/深主题，无需进主题映射表。
"""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget)


class _Block(QFrame):
    """骨架灰块（圆角占位条）。"""

    def __init__(self, color: str, h: int = 14, radius: int = 6):
        super().__init__()
        self.setFixedHeight(h)
        self.setStyleSheet(
            f"background: {color}; border-radius: {radius}px; border: none;")


class SkeletonPage(QWidget):
    def __init__(self):
        super().__init__()
        pal = QApplication.instance().palette()
        block = pal.alternateBase().color().name()   # 灰块底
        block2 = pal.midlight().color().name()       # 次级灰块
        text = pal.windowText().color().name()       # 提示文字（placeholderText 在深色下对比不足）

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 骨架容器（整体做呼吸动画）
        body = QWidget()
        root.addWidget(body)
        cv = QVBoxLayout(body)
        cv.setContentsMargins(12, 12, 12, 12)
        cv.setSpacing(12)
        cv.addWidget(_Block(block2, 28, 8))
        row = QHBoxLayout()
        row.setSpacing(8)
        for _ in range(3):
            row.addWidget(_Block(block, 26, 6))
        row.addStretch(1)
        cv.addLayout(row)
        main_block = _Block(block, 60, 10)
        cv.addWidget(main_block, 1)

        # 中央提示（放动画组外，文字恒定清晰）
        tip = QLabel("⏳ 正在启动，请稍候…", self)
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet(
            f"color: {text}; font-size: 15px; font-weight: bold; background: transparent;")
        self._tip = tip

        # 整组骨架块呼吸动画（事件循环启动后自动播放，不占启动耗时）
        self._fx = QGraphicsOpacityEffect(body)
        self._fx.setOpacity(1.0)
        body.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 0.45)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1800)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.start()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._tip.setGeometry(self.rect())
