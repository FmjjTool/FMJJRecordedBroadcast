# -*- coding: utf-8 -*-
"""页面功能介绍浮层：覆盖式展开，不占布局、不推动界面。

背景：嵌入式展开（show/hide 进 QVBoxLayout）会把下方内容整体推下去，
本组件改为「手动定位的子控件」——绝不 addWidget 进任何布局，
因此展开/收起时页面其余部分纹丝不动，且不遮罩整个界面（其余区域仍可操作）。

用法（页面构建函数内）：
    from gui_qt.help_overlay import attach_help_overlay
    btn = attach_help_overlay(self, "❓ 功能介绍", "<p>…HTML…</p>")
    # 把 btn 加进标题行布局即可，点击在按钮下方弹出浮层面板，再点收起。
"""

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)


class HelpOverlay(QFrame):
    """浮层面板本体：跟随页面显示/隐藏，页面缩放时自动重新定位。"""

    def __init__(self, page: QWidget, title: str, html: str):
        super().__init__(page)
        self._page = page
        self._anchor = None
        self._open = False
        self.setObjectName("helpOverlay")
        self.setStyleSheet(
            "#helpOverlay { background: #ffffff; border: 1.5px solid #d32f2f;"
            " border-radius: 8px; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head_lbl = QLabel(title)
        head_lbl.setStyleSheet(
            "color: #d32f2f; font-weight: bold; font-size: 13px;"
            " background: transparent; border: none;")
        head.addWidget(head_lbl)
        head.addStretch(1)
        close_btn = QPushButton("✕ 收起")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedHeight(20)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #d32f2f;"
            " border: 1px solid #d32f2f; border-radius: 3px; padding: 0 8px;"
            " font-size: 11px; }"
            "QPushButton:hover { background: #ffeaea; }")
        close_btn.clicked.connect(self.close_panel)
        head.addWidget(close_btn)
        outer.addLayout(head)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(html)
        self.browser.setStyleSheet(
            "QTextBrowser { background: #fffdf8; border: 1px solid #f1c0c0;"
            " border-radius: 4px; color: #333; font-size: 12px; }")
        outer.addWidget(self.browser)

        self.hide()
        page.installEventFilter(self)

    def set_anchor(self, btn: QWidget):
        self._anchor = btn

    def toggle(self) -> bool:
        """切换显示状态，返回切换后是否展开。"""
        self._open = not self._open
        self.setVisible(self._open)
        if self._open:
            self._reposition()
        return self._open

    def close_panel(self):
        self._open = False
        self.hide()

    def _reposition(self):
        """定位到锚点按钮下方（超界自动钳回页面内）。"""
        if self._anchor is None:
            return
        pw, ph = self._page.width(), self._page.height()
        w = int(min(540, max(340, pw - 24)))
        h = int(min(max(self.browser.document().size().height() + 64, 180),
                    ph * 0.7))
        self.resize(w, h)
        pos = self._anchor.mapTo(self._page, QPoint(0, self._anchor.height() + 6))
        x = min(max(8, pos.x()), max(8, pw - w - 8))
        y = min(max(8, pos.y()), max(8, ph - h - 8))
        self.move(x, y)
        self.raise_()

    def eventFilter(self, obj, ev):
        if obj is self._page and ev.type() == QEvent.Resize and self._open:
            self._reposition()
        return super().eventFilter(obj, ev)


def attach_help_overlay(page: QWidget, title: str, html: str,
                         btn_text: str = "❓ 功能介绍") -> QPushButton:
    """在 page 上挂功能介绍浮层，返回按钮（调用方放进标题行）。"""
    overlay = HelpOverlay(page, title, html)
    btn = QPushButton(btn_text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setCheckable(True)
    btn.setToolTip("点击展开/收起功能介绍（浮层显示，不挤压界面）")
    btn.setStyleSheet(
        "QPushButton { background: #fff7f7; color: #d32f2f;"
        " border: 1.5px solid #d32f2f; border-radius: 4px; padding: 3px 10px;"
        " font-size: 12px; }"
        "QPushButton:checked { background: #d32f2f; color: white; }"
        "QPushButton:hover { background: #ffeaea; }")
    overlay.set_anchor(btn)

    def _toggle(_=False):
        btn.setChecked(overlay.toggle())

    btn.clicked.connect(_toggle)
    return btn
