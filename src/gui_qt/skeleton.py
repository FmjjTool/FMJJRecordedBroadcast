"""启动骨架占位页（2026-10 性能重构）。

主窗口构造完成即显示本页（静态灰块骨架 + 启动提示），
窗口秒开不白屏；真实首页懒加载完成后由 _deferred_boot 移除。
只占位右侧内容区——真实左侧导航在 __init__ 同步构建，骨架显示时已就位，
不再画导航灰块（否则假导航列会挤进内容区，视觉错位）。
颜色取自当前 QPalette 角色，天然跟随浅/深主题，无需进主题映射表。
"""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)


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

        # 静态骨架容器，避免透明度动画反复重绘整个内容区。
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

        # 中央提示，文字恒定清晰。
        tip = QLabel("正在启动，请稍候…", self)
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet(
            f"color: {text}; font-size: 15px; font-weight: bold; background: transparent;")
        self._tip = tip

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._tip.setGeometry(self.rect())


class LoadingSkeleton(QWidget):
    """局部刷新占位：只在尺寸/主题变化时绘制，没有动画和重绘计时器。"""

    def __init__(self, parent=None, text="正在读取本地数据…"):
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.tip = QLabel(text, self)
        self.tip.setAlignment(Qt.AlignCenter)
        self.tip.setWordWrap(True)

    def setText(self, text):
        self.tip.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.tip.setGeometry(16, 12, max(0, self.width() - 32), 48)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.palette().alternateBase())
        width = max(0, self.width() - 32)
        for y in range(76, min(self.height() - 12, 340), 34):
            for col, fraction in enumerate((0.25, 0.34, 0.24)):
                x = 16 + width * (0, 0.29, 0.67)[col]
                painter.drawRoundedRect(QRectF(x, y, width * fraction, 18), 5, 5)
