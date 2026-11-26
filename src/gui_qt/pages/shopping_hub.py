"""购物解析合并页：一个输入框，在勾选平台内自动识别并解析。

- 全页只有一份输入框，平台范围外置多选，默认全部勾选。
- 识别规则：
    淘宝      taobao.com / tmall.com / tb.cn / 淘口令
    抖音商城  v.douyin.com / haohuo.jinritemai.com / douyin.com / 【抖音商城】分享文案
    B站小店   b23.tv / mall.bilibili.com / space.bilibili.com / 纯数字 UID
- 识别后自动切到对应结果面板，并调用该子页原有解析流程；
  三个子页仍是原来的 BasePage，落盘目录与导出格式不变：
    download/淘宝商品/ · download/抖音商品/ · download/B站小店/
- 一次只解析一个平台：混合粘贴时按「首个勾选且识别出的平台」解析，其余条数在提示里说明；
  抖音本身就支持一行一条批量解析。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
    QVBoxLayout,
)

from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_shop_page import BiliShopPage
from gui_qt.pages.douyin_shop_page import DouyinShopPage
from gui_qt.pages.shopping_page import ShoppingPage
from gui_qt.table_columns import configure_table_columns
from toolbox.shopping_parse import (
    PLATFORM_LABELS, SHOPPING_PLATFORM_OPTIONS, detect_platform,
    plan_shopping_parse,
)

_TAB_INDEX = {"taobao": 0, "douyin": 1, "bili": 2}


class ShoppingHubPage(BasePage):
    """购物解析合并页：单输入框 + 自动识别 + 结果面板切换。"""

    def __init__(self, app):
        super().__init__(app)
        self._panels = {}
        self.platform_checks = {}
        self._syncing_controls = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top = QFrame()
        top.setProperty("variant", "toolbar")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(12, 12, 12, 12)
        tv.setSpacing(8)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "粘贴链接或分享文案，自动识别平台（抖音可一行一条批量）：\n"
            "淘宝 taobao.com / tmall.com · 抖音商城 v.douyin.com 分享文案 · "
            "B站小店 b23.tv / mall.bilibili.com / UP主 UID")
        self.input_edit.setFixedHeight(60)
        self.input_edit.textChanged.connect(self._update_hint)
        tv.addWidget(self.input_edit)

        platforms_row = QHBoxLayout()
        platforms_row.setSpacing(8)
        platforms_row.addWidget(QLabel("平台范围"))
        for key, label in SHOPPING_PLATFORM_OPTIONS:
            check = QCheckBox(label)
            check.setChecked(True)
            check.setObjectName(f"shoppingPlatform_{key}")
            check.setToolTip(f"仅解析已勾选平台的链接；取消后不请求{label}")
            check.toggled.connect(self._platforms_changed)
            self.platform_checks[key] = check
            platforms_row.addWidget(check)
        platforms_row.addStretch(1)
        tv.addLayout(platforms_row)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.parse_btn = QPushButton("解析")
        self.parse_btn.setProperty("variant", "primary")
        self.parse_btn.clicked.connect(self._parse)

        self.hint_label = QLabel("仅在勾选的平台内自动识别链接")
        self.hint_label.setStyleSheet("color: palette(placeholder-text);")
        self.hint_label.setWordWrap(True)
        row.addWidget(self.hint_label, 1)
        row.addWidget(self.parse_btn)
        tv.addLayout(row)
        layout.addWidget(top)

        # 结果区：三个子页原样托管，识别到哪个平台就显示哪个
        self.stack = QStackedWidget()
        self.taobao_page = ShoppingPage(app)
        self.douyin_page = DouyinShopPage(app)
        self.bili_page = BiliShopPage(app)
        configure_table_columns(self.taobao_page.table, fixed={0: 36},
                                minimums={1: 100}, stretch=(1,), auto_fit=True)
        for key, page in (("taobao", self.taobao_page),
                          ("douyin", self.douyin_page),
                          ("bili", self.bili_page)):
            page.embed_input()
            self.stack.addWidget(page)
            self._panels[key] = page
            # 三个原有子页在解析、详情和导出期间均禁用自己的解析按钮。
            # 监听显式禁用状态即可同步外置筛选，不增加第二套任务状态。
            page.parse_btn.installEventFilter(self)
        layout.addWidget(self.stack, 1)
        self._sync_platform_controls()
        self._update_hint()

    # ==================== 入口 ====================

    def _parse(self):
        if any(self._panel_busy(page) for page in self._panels.values()):
            return
        text = self.input_edit.toPlainText().strip()
        try:
            plan = plan_shopping_parse(text, self._selected_platforms())
        except ValueError as exc:
            self._set_hint(str(exc), warn=True)
            return
        self.stack.setCurrentIndex(_TAB_INDEX[plan["platform"]])
        self._set_hint(plan["summary"])
        self._panels[plan["platform"]].submit_text(plan["text"])
        self._sync_platform_controls()

    def _update_hint(self):
        text = self.input_edit.toPlainText().strip()
        selected = self._selected_platforms()
        if not selected:
            self._set_hint("请至少勾选一个平台", warn=True)
            return
        if not text:
            labels = " / ".join(PLATFORM_LABELS[key] for key in selected)
            self._set_hint(f"仅在勾选的平台内自动识别：{labels}")
            return
        try:
            plan = plan_shopping_parse(text, selected)
            self._set_hint(plan["summary"])
        except ValueError as exc:
            self._set_hint(str(exc), warn=True)

    def _selected_platforms(self):
        return [key for key, check in self.platform_checks.items() if check.isChecked()]

    @staticmethod
    def _panel_busy(page):
        return page.parse_btn.testAttribute(Qt.WA_ForceDisabled)

    def _platforms_changed(self):
        self._sync_platform_controls()
        self._update_hint()

    def _sync_platform_controls(self):
        if self._syncing_controls:
            return
        self._syncing_controls = True
        try:
            selected = self._selected_platforms()
            busy = any(self._panel_busy(page) for page in self._panels.values())
            self.parse_btn.setEnabled(not busy and bool(selected))
            for check in self.platform_checks.values():
                check.setEnabled(not busy)
            for key, page in self._panels.items():
                page.setEnabled(key in selected)
        finally:
            self._syncing_controls = False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.EnabledChange and any(
                watched is page.parse_btn for page in self._panels.values()):
            self._sync_platform_controls()
        return super().eventFilter(watched, event)

    def _set_hint(self, text: str, warn: bool = False):
        self.hint_label.setText(text)
        self.hint_label.setStyleSheet(
            "color: palette(placeholder-text);" if not warn else "color: palette(text);")

    # ==================== 生命周期转发 ====================

    def on_show(self):
        page = self._panels.get(self._current_key())
        if page is not None:
            page.on_show()

    def on_hide(self):
        for page in self._panels.values():
            page.on_hide()

    def release_view_resources(self):
        for page in self._panels.values():
            page.release_view_resources()

    def has_active_tasks(self):
        return any(page.has_active_tasks() for page in self._panels.values())

    def shutdown(self):
        for page in self._panels.values():
            page.shutdown()

    def on_dir_changed(self, work_dir):
        for page in self._panels.values():
            fn = getattr(page, "on_dir_changed", None)
            if fn is not None:
                fn(work_dir)

    def on_theme_changed(self):
        for page in self._panels.values():
            fn = getattr(page, "on_theme_changed", None)
            if fn is not None:
                fn()

    def _current_key(self) -> str:
        index = self.stack.currentIndex()
        for key, value in _TAB_INDEX.items():
            if value == index:
                return key
        return "taobao"
