"""启动骨架占位页（2026-10 性能重构；2026-11-23 按首页结构重做视觉）。

主窗口构造完成即显示本页（与首页同构的静态骨架 + 启动提示），
窗口秒开不白屏；真实首页懒加载完成后由 _deferred_boot 移除。

骨架节奏与首页一致：状态行 → 统计卡两行 → 分组标题 → 工具卡两行 → 图表面板。
所有灰块都有明确宽度与容器层级，不再出现孤立的通栏条或居中漂浮的提示。
颜色全部走 QSS 的 palette 角色，随浅/深主题即时重算，无需进映射表。
"""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

_QSS = """
QWidget#startupSkeleton, QScrollArea#startupSkeletonScroll { background: palette(window); }
QWidget#startupSkeletonBody { background: palette(window); }
QFrame#skelCard {
    background: palette(base);
    border: 1px solid palette(shadow);
    border-radius: 12px;
}
QFrame#skelToolCard {
    background: palette(base);
    border: 1px solid palette(shadow);
    border-radius: 10px;
}
QFrame#skelBar {
    background: palette(alternate-base);
    border: none;
    border-radius: 6px;
}
QFrame#skelBarSmall {
    background: palette(alternate-base);
    border: none;
    border-radius: 4px;
}
QFrame#skelIcon {
    background: palette(midlight);
    border: none;
    border-radius: 9px;
}
QFrame#skelButton {
    background: palette(alternate-base);
    border: 1px solid palette(shadow);
    border-radius: 8px;
}
QFrame#skelChart {
    background: palette(alternate-base);
    border: 1px solid palette(shadow);
    border-radius: 8px;
}
QLabel#skelTip {
    color: palette(window-text);
    font-size: 14px;
    font-weight: bold;
    background: transparent;
}
QLabel#skelHint {
    color: palette(placeholder-text);
    font-size: 11px;
    background: transparent;
}
"""


class _Bar(QFrame):
    """骨架灰块：固定高度，可用 width 固定宽度或在行内按比例伸展。"""

    def __init__(self, height: int, radius: int = 6, width: int | None = None):
        super().__init__()
        self.setObjectName("skelBarSmall" if radius <= 4 else "skelBar")
        self.setFixedHeight(height)
        if width is not None:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _fraction_row(bar: _Bar, percent: int) -> QHBoxLayout:
    """一行内按百分比宽度放灰块，其余留白。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.addWidget(bar, max(1, percent))
    row.addStretch(max(0, 100 - percent))
    return row


class SkeletonPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("startupSkeleton")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("startupSkeletonScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        body = QWidget()
        body.setObjectName("startupSkeletonBody")
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        # 与首页 workbenchHomeBody 的可视节奏对齐，真实页切换时不跳动
        v.setContentsMargins(26, 14, 26, 28)
        v.setSpacing(14)
        v.addWidget(self._build_hero())
        v.addLayout(self._build_stats())
        v.addWidget(self._build_section_title())
        v.addLayout(self._build_tools())
        v.addLayout(self._build_charts())
        v.addWidget(self._build_section_title())
        v.addLayout(self._build_tools(count=4))
        v.addStretch(1)

    @staticmethod
    def _build_hero() -> QWidget:
        hero = QWidget()
        h = QHBoxLayout(hero)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        tip = QLabel("正在启动，请稍候…")
        tip.setObjectName("skelTip")
        hint = QLabel("正在加载首页数据与账户状态")
        hint.setObjectName("skelHint")
        text.addWidget(tip)
        text.addWidget(hint)
        h.addLayout(text)
        h.addStretch(1)
        h.addWidget(_Bar(28, 8, width=76), 0, Qt.AlignVCenter)
        return hero

    @staticmethod
    def _build_stats() -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        for index in range(8):
            card = QFrame()
            card.setObjectName("skelCard")
            box = QVBoxLayout(card)
            box.setContentsMargins(18, 13, 18, 13)
            box.setSpacing(6)
            box.addLayout(_fraction_row(_Bar(10, 4), 46))
            box.addLayout(_fraction_row(_Bar(22, 6), 60))
            box.addStretch(1)
            grid.addWidget(card, index // 4, index % 4)
            grid.setColumnStretch(index % 4, 1)
        return grid

    @staticmethod
    def _build_section_title() -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(2, 4, 2, 0)
        h.setSpacing(8)
        h.addWidget(_Bar(14, 4, width=128))
        h.addStretch(1)
        return wrap

    @staticmethod
    def _build_tools(count: int = 8) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        for index in range(count):
            card = QFrame()
            card.setObjectName("skelToolCard")
            card.setMinimumHeight(78)
            row = QHBoxLayout(card)
            row.setContentsMargins(15, 13, 13, 13)
            row.setSpacing(12)
            icon = QFrame()
            icon.setObjectName("skelIcon")
            icon.setFixedSize(36, 36)
            row.addWidget(icon)
            text = QVBoxLayout()
            text.setSpacing(5)
            text.addLayout(_fraction_row(_Bar(12, 6), 55))
            text.addLayout(_fraction_row(_Bar(8, 4), 88))
            text.addLayout(_fraction_row(_Bar(8, 4), 62))
            row.addLayout(text, 1)
            row.addWidget(_Bar(12, 4, width=8))
            grid.addWidget(card, index // 4, index % 4)
            grid.setColumnStretch(index % 4, 1)
        return grid

    @staticmethod
    def _build_charts() -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        for title_pct in (34, 40):
            card = QFrame()
            card.setObjectName("skelCard")
            box = QVBoxLayout(card)
            box.setContentsMargins(18, 16, 18, 12)
            box.setSpacing(6)
            box.addLayout(_fraction_row(_Bar(12, 6), title_pct))
            box.addLayout(_fraction_row(_Bar(8, 4), title_pct + 26))
            chart = QFrame()
            chart.setObjectName("skelChart")
            chart.setMinimumHeight(150)
            chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            box.addWidget(chart, 1)
            row.addWidget(card, 1)
        return row


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
