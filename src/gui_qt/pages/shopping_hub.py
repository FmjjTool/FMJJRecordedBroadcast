"""购物解析合并页：一个输入框，按链接头自动识别淘宝 / 抖音商城 / B站小店。

- 全页只有一份输入框，不选平台、不切页签：粘贴后按每行链接头判断平台。
- 识别规则：
    淘宝      taobao.com / tmall.com / tb.cn / 淘口令
    抖音商城  v.douyin.com / haohuo.jinritemai.com / douyin.com / 【抖音商城】分享文案
    B站小店   b23.tv / mall.bilibili.com / space.bilibili.com / 纯数字 UID
- 识别后自动切到对应结果面板，并调用该子页原有解析流程；
  三个子页仍是原来的 BasePage，落盘目录与导出格式不变：
    download/淘宝商品/ · download/抖音商品/ · download/B站小店/
- 一次只解析一个平台：混合粘贴时按「第一行识别出的平台」解析，其余条数在提示里说明；
  抖音本身就支持一行一条批量解析。
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
    QVBoxLayout,
)

from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_shop_page import BiliShopPage
from gui_qt.pages.douyin_shop_page import DouyinShopPage
from gui_qt.pages.shopping_page import ShoppingPage

PLATFORM_LABELS = {"taobao": "淘宝", "douyin": "抖音商城", "bili": "B站小店"}
_TAB_INDEX = {"taobao": 0, "douyin": 1, "bili": 2}

# 只看链接头，够用即可：真正的合法性由各平台解析器负责兜底
_DOUYIN_RE = re.compile(
    r"(?:v\.douyin\.com|haohuo\.jinritemai\.com|(?:www\.|m\.)?douyin\.com/|【抖音商城】)",
    re.I)
_BILI_RE = re.compile(
    r"(?:b23\.tv|mall\.bilibili\.com|bilibili\.com/h5/mall|space\.bilibili\.com)", re.I)
_BILI_UID_RE = re.compile(r"^\d{3,}$")
_TAOBAO_RE = re.compile(r"(?:taobao\.com|tmall\.com|tb\.cn)", re.I)


def detect_platform(line: str) -> str:
    """按链接头判断平台：taobao / douyin / bili，识别不到返回空串。"""
    text = (line or "").strip()
    if not text:
        return ""
    if _DOUYIN_RE.search(text):
        return "douyin"
    if _BILI_RE.search(text) or _BILI_UID_RE.match(text):
        return "bili"
    if _TAOBAO_RE.search(text):
        return "taobao"
    return ""


class ShoppingHubPage(BasePage):
    """购物解析合并页：单输入框 + 自动识别 + 结果面板切换。"""

    def __init__(self, app):
        super().__init__(app)
        self._panels = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QFrame()
        top.setStyleSheet(
            "QFrame { background: palette(base); border: 1px solid palette(shadow);"
            " border-radius: 8px; }")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(6)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "粘贴链接或分享文案，自动识别平台（抖音可一行一条批量）：\n"
            "淘宝 taobao.com / tmall.com · 抖音商城 v.douyin.com 分享文案 · "
            "B站小店 b23.tv / mall.bilibili.com / UP主 UID")
        self.input_edit.setFixedHeight(60)
        self.input_edit.textChanged.connect(self._update_hint)
        tv.addWidget(self.input_edit)

        row = QHBoxLayout()
        self.parse_btn = QPushButton("解析")
        self.parse_btn.setStyleSheet(
            "background: palette(highlight); color: palette(highlighted-text);"
            " padding: 6px 16px; border-radius: 4px;")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)

        self.hint_label = QLabel("自动识别：淘宝 / 抖音商城 / B站小店（看链接头，不用选平台）")
        self.hint_label.setStyleSheet("color: palette(placeholder-text);")
        self.hint_label.setWordWrap(True)
        row.addWidget(self.hint_label, 1)
        tv.addLayout(row)
        layout.addWidget(top)

        # 结果区：三个子页原样托管，识别到哪个平台就显示哪个
        self.stack = QStackedWidget()
        self.taobao_page = ShoppingPage(app)
        self.douyin_page = DouyinShopPage(app)
        self.bili_page = BiliShopPage(app)
        for key, page in (("taobao", self.taobao_page),
                          ("douyin", self.douyin_page),
                          ("bili", self.bili_page)):
            page.embed_input()
            self.stack.addWidget(page)
            self._panels[key] = page
        layout.addWidget(self.stack, 1)

    # ==================== 入口 ====================

    def _parse(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            self._set_hint("先粘贴链接：淘宝店铺/商品 · 抖音商城分享文案 · B站小店链接或 UP主 UID",
                           warn=True)
            return

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        groups = {"taobao": [], "douyin": [], "bili": []}
        unknown = []
        first_platform = ""
        for line in lines:
            platform = detect_platform(line)
            if not platform:
                unknown.append(line)
                continue
            groups[platform].append(line)
            if not first_platform:
                first_platform = platform

        if not first_platform:
            self._set_hint(
                "没识别到平台：支持 taobao.com / tmall.com、v.douyin.com 分享文案、"
                "b23.tv / mall.bilibili.com / space.bilibili.com / 纯数字 UID", warn=True)
            self.app.toast("没识别到链接平台", "warning")
            return

        self.stack.setCurrentIndex(_TAB_INDEX[first_platform])
        page = self._panels[first_platform]
        label = PLATFORM_LABELS[first_platform]

        if first_platform == "douyin":
            # 抖音解析器本身就支持多行，一次全给它
            page.submit_text("\n".join(groups["douyin"]))
            self._set_hint(f"已识别 {label}：{len(groups['douyin'])} 条分享文案")
        else:
            page.submit_text(groups[first_platform][0])
            extra = len(groups[first_platform]) - 1
            msg = f"已识别 {label}：{groups[first_platform][0][:48]}"
            if extra > 0:
                msg += f"（另有 {extra} 条{label}链接未处理：{label}一次解析一条）"
            self._set_hint(msg)

        skipped = [f"{PLATFORM_LABELS[key]} {len(items)} 条"
                   for key, items in groups.items()
                   if key != first_platform and items]
        if skipped:
            self.hint_label.setText(
                self.hint_label.text() + "；另识别到 " + "、".join(skipped) +
                "，一次只解析一个平台")
        if unknown:
            self.hint_label.setText(
                self.hint_label.text() + f"；{len(unknown)} 条未识别平台，已跳过")

    def _update_hint(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            self._set_hint("自动识别：淘宝 / 抖音商城 / B站小店（看链接头，不用选平台）")
            return
        counts = {"taobao": 0, "douyin": 0, "bili": 0}
        unknown = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            platform = detect_platform(line)
            if platform:
                counts[platform] += 1
            else:
                unknown += 1
        found = [f"{PLATFORM_LABELS[key]} {n} 条" for key, n in counts.items() if n]
        if found:
            extra = f"，未识别 {unknown} 条" if unknown else ""
            self._set_hint("识别为：" + "、".join(found) + extra)
        else:
            self._set_hint("暂未识别到平台，确认链接头是否在支持列表内", warn=True)

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
