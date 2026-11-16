# -*- coding: utf-8 -*-
"""页面功能介绍浮层：覆盖式展开，不占布局、不推动界面。

背景：嵌入式展开（show/hide 进 QVBoxLayout）会把下方内容整体推下去，
本组件改为「手动定位的子控件」——绝不 addWidget 进任何布局，
因此展开/收起时页面其余部分纹丝不动，且不遮罩整个界面（其余区域仍可操作）。

用法（页面构建函数内）：
    from gui_qt.help_overlay import attach_help_overlay
    btn = attach_help_overlay(self, "❓ 功能介绍", "<p>…HTML…</p>")
    # 把 btn 加进标题行布局即可，点击在按钮下方弹出浮层面板。
    # 收起方式：再点按钮 / 点「✕ 收起」/ 点击浮层外的任意区域。
"""

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)


def refresh_browser_colors(browser: QTextBrowser, muted_colors=()):
    """原地更新链接/次级文字，保留正文、图片资源、选区和滚动位置。"""
    from gui_qt import theme

    muted_colors = set(muted_colors)
    if muted_colors:
        muted_colors.update((theme.color("muted", False), theme.color("muted", True)))
    link_color, muted_color = theme.color("link"), theme.color("muted")
    edits = []
    block = browser.document().firstBlock()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                current = fmt.foreground().color().name()
                target = link_color if fmt.isAnchor() else (
                    muted_color if current in muted_colors else None)
                if target is not None and target != current:
                    edits.append((fragment.position(), fragment.length(), target))
            iterator += 1
        block = block.next()
    if not edits:
        return
    selection = browser.textCursor()
    scroll_x = browser.horizontalScrollBar().value()
    scroll_y = browser.verticalScrollBar().value()
    cursor = QTextCursor(browser.document())
    cursor.beginEditBlock()
    for start, length, target in edits:
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(target))
        cursor.mergeCharFormat(fmt)
    cursor.endEditBlock()
    browser.setTextCursor(selection)
    browser.horizontalScrollBar().setValue(scroll_x)
    browser.verticalScrollBar().setValue(scroll_y)


class HelpOverlay(QFrame):
    """浮层面板本体：跟随页面显示/隐藏，页面缩放时自动重新定位。"""

    def __init__(self, page: QWidget, title: str, html: str):
        super().__init__(page)
        self._page = page
        self._anchor = None
        self._open = False
        self._app_filter_installed = False
        self._colors_pending = False
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
        self.browser.installEventFilter(self)
        refresh_browser_colors(self.browser)
        outer.addWidget(self.browser)

        self.hide()
        page.installEventFilter(self)
        # 应用级鼠标过滤器只在浮层展开时安装；全页预载后，几十个隐藏
        # 帮助浮层不能逐个处理整个应用的控件/主题事件。

    def set_anchor(self, btn: QWidget):
        self._anchor = btn

    def toggle(self) -> bool:
        """切换显示状态，返回切换后是否展开。"""
        self._open = not self._open
        self._sync_app_filter()
        self.setVisible(self._open)
        if self._open:
            self._reposition()
        return self._open

    def close_panel(self):
        self._open = False
        self._sync_app_filter()
        self.hide()
        if self._anchor is not None:
            try:
                self._anchor.setChecked(False)
            except RuntimeError:
                pass

    def _sync_app_filter(self):
        app = QApplication.instance()
        if app is None:
            return
        if self._open and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True
        elif not self._open and self._app_filter_installed:
            app.removeEventFilter(self)
            self._app_filter_installed = False

    def _refresh_colors(self):
        self._colors_pending = False
        if self.browser.isVisible():
            refresh_browser_colors(self.browser)

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
        t = ev.type()
        if t in (QEvent.PaletteChange, QEvent.Show) and obj is self.browser:
            # 不在 Qt 全局重设样式的调用栈中编辑 QTextDocument。
            if self.browser.isVisible() and not self._colors_pending:
                self._colors_pending = True
                QTimer.singleShot(0, self._refresh_colors)
        elif t == QEvent.Hide and obj is self._page:
            self.close_panel()
        elif t == QEvent.Resize and obj is self._page and self._open:
            self._reposition()
        elif t == QEvent.MouseButtonPress and self._open:
            # 点在浮层内部（含浏览器/滚动条/关闭按钮）或锚点按钮上：不处理，
            # 交给各自的点击逻辑（锚点按钮靠 clicked 信号正常切换开关）
            if (obj is not self and obj is not self._anchor
                    and isinstance(obj, QWidget) and not self.isAncestorOf(obj)):
                self.close_panel()
        return super().eventFilter(obj, ev)


def attach_help_overlay(page: QWidget, title: str, html: str,
                         btn_text: str = "❓ 功能介绍") -> QPushButton:
    """在 page 上挂功能介绍浮层，返回按钮（调用方放进标题行）。"""
    overlay = HelpOverlay(page, title, html)
    btn = QPushButton(btn_text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setCheckable(True)
    btn.setToolTip("点击展开/收起功能介绍（浮层显示，点空白处也可收起）")
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
