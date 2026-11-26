"""首页：数据概览 + 常用工具入口。

- 默认启动页；点击侧栏顶部标题可随时回到本页
- 指标卡：已获取总数 / 覆盖平台 / 近 7 天新增 / 今日新增
- 图表：平台分布环形图 + 近 7 天新增柱状图（QPainter 自绘，跟随深浅主题）
- 常用工具：原侧栏中的信息类页面迁到本页，避免信息分组过长
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QLinearGradient,
                           QPainter, QPalette, QPen)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import table_sort, theme
from gui_qt.common import run_bg
from gui_qt.navigation import line_icon, tool_icon_name
from gui_qt.page_index import HOME_COMMON, HOME_INFO, page_meta, page_title
from gui_qt.pages.base import BasePage
from toolbox.dashboard_stats import (
    load_account_history, load_dashboard_data, load_tcdoc_history, tcdoc_docs,
    tracked_accounts_snapshot as _tracked_accounts_snapshot,
    watched_account_keys as _watched_account_keys,
)

# 平台显示名与图表配色
PLATFORM_META = {
    "bilibili": ("B站", "#fb7299"),
    "douyin": ("抖音", "#00bcd4"),
    "weibo": ("微博", "#e6a23c"),
    "weibo_chaohua": ("微博超话", "#ff6b6b"),
    "tcdoc": ("腾讯文档", "#5b8def"),
    "assets": ("本地资源", "#26a69a"),
    "xiaohongshu": ("小红书", "#ff4d6d"),
    "kuaishou": ("快手", "#ff8f1f"),
    "acfun": ("AcFun", "#fd4c5b"),
}
OTHER_COLOR = "#8b93a7"

# 账号观察抽屉：键集合（网媒 + 音乐）
ACCOUNT_DRAWER_KEYS = {"tracked_accounts"}
TCDOC_DRAWER_KEYS = {"tcdoc_rows", "doc_views", "doc_members", "doc_readers"}
SITE_STAT_KEYS = {"site_visitors", "site_sessions", "site_pageviews", "site_online"}
# 下钻抽屉表格最多显示的行数，超出后表格内部滚动；高度按实际行数自适应
DRAWER_MAX_ROWS = 10

# 常用工具：(key, 标题, 说明)
# 常用工具卡片：顺序与文案来自 page_index（页面名单一事实来源）
def _tool_card(key):
    meta = page_meta(key)
    return (key, meta.card_title or meta.title, meta.card_note)


COMMON_TOOLS = [_tool_card(key) for key in HOME_COMMON]

INFO_TOOLS = [_tool_card(key) for key in HOME_INFO]
INFO_TOOL_KEYS = {key for key, *_ in INFO_TOOLS}














def _dist_items(by_source):
    """来源计数 → 环形图数据，未知来源合并为「其他」。"""
    rest = dict(by_source)
    items = []
    for key, (label, color) in PLATFORM_META.items():
        n = rest.pop(key, 0)
        if n:
            items.append((label, n, color))
    other = sum(rest.values())
    if other:
        items.append(("其他", other, OTHER_COLOR))
    items.sort(key=lambda item: item[1], reverse=True)
    return items


class _DonutChart(QWidget):
    """平台分布环形图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setMinimumHeight(210)
        self.setStyleSheet("background: transparent;")

    def set_items(self, items):
        self._items = list(items or [])
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        palette = theme.current_palette()
        text_color = palette.color(QPalette.WindowText)
        muted = palette.color(QPalette.PlaceholderText)
        ring_bg = palette.color(QPalette.AlternateBase)

        w, h = self.width(), self.height()
        total = sum(item[1] for item in self._items)
        legend_texts = [f"{label}  {value} · {value / total * 100 if total else 0:.0f}%"
                        for label, value, _color in self._items]
        legend_font = QFont(p.font())
        legend_font.setPointSize(9)
        p.setFont(legend_font)
        legend_width = max((p.fontMetrics().horizontalAdvance(text)
                            for text in legend_texts), default=110) + 22
        # 窄窗口先给平台名、数量和占比留足位置，再收小圆环。
        size = max(80.0, min(h - 26, w * 0.46, w - legend_width - 38))
        rect = QRectF(12, (h - size) / 2, size, size)
        thickness = max(16.0, size * 0.115)

        p.setPen(QPen(ring_bg, thickness, Qt.SolidLine, Qt.FlatCap))
        p.drawEllipse(rect.adjusted(thickness / 2, thickness / 2,
                                    -thickness / 2, -thickness / 2))

        if total:
            cursor = 90 * 16
            gap = 1.4 * 16
            for _label, value, color in self._items:
                span = -max(1, int(value / total * 360 * 16) - int(gap))
                p.setPen(QPen(QColor(color), thickness, Qt.SolidLine, Qt.FlatCap))
                p.drawArc(rect.adjusted(thickness / 2, thickness / 2,
                                        -thickness / 2, -thickness / 2),
                          cursor, span)
                cursor += span - int(gap)

        # 圆心总数
        p.setPen(text_color)
        f = QFont(p.font())
        f.setPointSize(20 if size >= 140 else 16)
        f.setBold(True)
        p.setFont(f)
        if self._items:
            p.drawText(rect.adjusted(0, -10, 0, -10), Qt.AlignCenter, f"{total:,}")
        f.setPointSize(9)
        f.setBold(False)
        p.setFont(f)
        p.setPen(muted)
        if self._items:
            p.drawText(rect.adjusted(0, 24, 0, 24), Qt.AlignCenter, "条")

        # 右侧图例
        lx = rect.right() + 16
        available = max(90.0, w - lx - 10)
        line_h = 24
        ly = max(14.0, (h - line_h * max(1, len(self._items))) / 2)
        for (_label, _value, color), text in zip(self._items, legend_texts):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(lx, ly + 7, 9, 9), 4.5, 4.5)
            p.setPen(text_color)
            f.setPointSize(9)
            p.setFont(f)
            metrics = p.fontMetrics()
            p.drawText(QRectF(lx + 16, ly, available - 16, line_h),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       metrics.elidedText(text, Qt.ElideRight, int(available - 16)))
            ly += line_h

        if not self._items:
            p.setPen(muted)
            f.setPointSize(10)
            f.setBold(False)
            p.setFont(f)
            p.drawText(rect.adjusted(0, size * 0.05, 0, 0),
                       Qt.AlignCenter, "暂无数据")


class _TrendChart(QWidget):
    """近 7 天新增柱状图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = []
        self._values = []
        self.setMinimumHeight(190)
        self.setStyleSheet("background: transparent;")

    def set_data(self, labels, values):
        self._labels = list(labels or [])
        self._values = list(values or [])
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        palette = theme.current_palette()
        text_color = palette.color(QPalette.WindowText)
        muted = palette.color(QPalette.PlaceholderText)
        grid = palette.color(QPalette.Shadow)
        accent = palette.color(QPalette.Highlight)

        w, h = self.width(), self.height()
        left, right, top, bottom = 34, 12, 18, 28
        plot_w = max(10.0, w - left - right)
        plot_h = max(10.0, h - top - bottom)
        values = self._values or [0]
        max_v = max(values) or 1
        # 纵轴至少到 3，避免全零时出现 1/1/0/0 的重复刻度
        axis_max = max(3, max_v)

        # 网格与刻度
        f = QFont(p.font())
        f.setPointSize(8)
        p.setFont(f)
        p.setPen(QPen(grid, 1))
        for i in range(4):
            y = top + plot_h * i / 3
            p.drawLine(int(left), int(y), int(left + plot_w), int(y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - 9, left - 6, 18),
                       Qt.AlignRight | Qt.AlignVCenter,
                       str(int(round(axis_max * (3 - i) / 3))))
            p.setPen(QPen(grid, 1))

        n = len(values)
        slot = plot_w / n if n else plot_w
        bar_w = min(34.0, slot * 0.52)
        for i, value in enumerate(values):
            cx = left + slot * i + slot / 2
            bar_h = plot_h * value / axis_max
            x = cx - bar_w / 2
            y = top + plot_h - bar_h
            if bar_h > 0:
                grad = QLinearGradient(0, y, 0, top + plot_h)
                grad.setColorAt(0, accent)
                grad.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 90))
                p.setPen(Qt.NoPen)
                p.setBrush(grad)
                p.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 5, 5)
                p.setPen(text_color)
                p.drawText(QRectF(cx - slot / 2, y - 18, slot, 16),
                           Qt.AlignCenter, str(value))

            if i < len(self._labels):
                p.setPen(muted)
                p.drawText(QRectF(cx - slot / 2, top + plot_h + 6, slot, 18),
                           Qt.AlignCenter, self._labels[i])

        if not any(self._values):
            p.setPen(muted)
            f.setPointSize(10)
            p.setFont(f)
            p.drawText(QRectF(left, top, plot_w, plot_h),
                       Qt.AlignCenter, "近 7 天暂无新增")


class _ToolCard(QPushButton):
    """整卡可点击的工具入口；支持键盘聚焦与回车/空格激活。"""

    def __init__(self, key, title, desc, callback, parent=None):
        super().__init__(parent)
        self._key = key
        self.setObjectName("workbenchToolCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setAccessibleName(title)
        self.setToolTip(f"{title}：{desc}")
        self.clicked.connect(callback)

        row = QHBoxLayout(self)
        row.setContentsMargins(15, 13, 13, 13)
        row.setSpacing(12)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("workbenchToolIcon")
        self.icon_label.setFixedSize(36, 36)
        self.icon_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.icon_label)
        text = QVBoxLayout()
        text.setSpacing(5)
        name = QLabel(title)
        name.setObjectName("workbenchToolTitle")
        text.addWidget(name)
        tip = QLabel(desc)
        tip.setObjectName("workbenchToolDesc")
        tip.setWordWrap(True)
        text.addWidget(tip)
        row.addLayout(text, 1)
        arrow = QLabel("›")
        arrow.setObjectName("workbenchToolArrow")
        row.addWidget(arrow)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.sync_icon()

    def sync_icon(self):
        color = theme.current_palette().color(QPalette.Link).name()
        self.icon_label.setPixmap(line_icon(tool_icon_name(self._key), color, 20).pixmap(20, 20))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class _AccountConfigDialog(QDialog):
    """配置要观察的网媒 / 音乐账号。"""

    def __init__(self, parent, accounts, selected):
        super().__init__(parent)
        self._checks = {}
        self.setWindowTitle("配置观察账号")
        self.resize(760, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        hint = QLabel("勾选要观察的账号；保存后首页抽屉展示前 5 个已观察账号的最新数据。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        row = 0
        for source, title in (("tracker", "网媒账号"), ("music", "音乐账号")):
            items = [item for item in accounts if item["source"] == source]
            if not items:
                continue
            group = QLabel(f"{title}（{len(items)}）")
            group.setStyleSheet("font-weight: bold;")
            grid.addWidget(group, row, 0, 1, 2)
            row += 1
            for index, account in enumerate(items):
                check = QCheckBox(f'{account["name"]} · {account["platform_label"]}')
                check.setChecked(account["key"] in selected)
                check.setToolTip(account.get("url") or account["name"])
                self._checks[account["key"]] = check
                grid.addWidget(check, row + index // 2, index % 2)
            row += (len(items) + 1) // 2
        grid.setRowStretch(row, 1)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)

        foot = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: self._set_all(True))
        foot.addWidget(all_btn)
        none_btn = QPushButton("全不选")
        none_btn.clicked.connect(lambda: self._set_all(False))
        foot.addWidget(none_btn)
        foot.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        foot.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 4px 16px; border-radius: 4px;")
        save_btn.clicked.connect(self.accept)
        foot.addWidget(save_btn)
        layout.addLayout(foot)

    def _set_all(self, checked):
        for check in self._checks.values():
            check.setChecked(bool(checked))

    def selected_keys(self):
        return [key for key, check in self._checks.items() if check.isChecked()]


class _StatCard(QFrame):
    """可点击的统计卡：先在卡片内展开摘要，完整详情按需打开。"""

    clicked = Signal(str)

    def __init__(self, key, tip="", clickable=False, parent=None):
        super().__init__(parent)
        self._key = key
        self._clickable = clickable
        self.preview = None
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setObjectName("workbenchStatCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("clickable", "true" if clickable else "false")
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.setToolTip(tip)

    def mouseReleaseEvent(self, event):
        if (self._clickable and event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())
                and (self.preview is None or not self.preview.isVisible()
                     or event.position().y() < self.preview.y())):
            self.clicked.emit(self._key)
        super().mouseReleaseEvent(event)


class HomePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._stat_values = {}
        self._stat_previews = {}
        self._refreshing = False
        self._tool_grids = []
        self._last_layout = None
        self._drawer_mode = ""
        self._drawer_key = ""
        self._account_seq = 0
        self._site_busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = self.scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(scroll)

        body = self.body = QWidget()
        body.setObjectName("workbenchHomeBody")
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setSizeConstraint(QLayout.SetMinimumSize)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        v.addWidget(self._build_hero())
        v.addLayout(self._build_stats())
        self.drawer = self._build_drawer()
        v.addWidget(self.drawer)
        self.drawer.hide()
        v.addWidget(self._section_title("常用工具", "采集、整理与制作"))
        v.addLayout(self._build_tool_grid(COMMON_TOOLS))
        v.addLayout(self._build_charts())
        v.addWidget(self._section_title("更多信息工具", "平台内容与文档监控"))
        v.addLayout(self._build_tool_grid(INFO_TOOLS))
        v.addStretch(1)

        self._apply_theme_styles()
        self._layout_for_width()

    # ==================== UI 构建 ====================

    def _build_hero(self):
        card = QWidget()
        card.setObjectName("workbenchHero")
        h = QHBoxLayout(card)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        self.date_label = QLabel("")
        self.date_label.setObjectName("workbenchHomeMuted")
        self.date_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.addWidget(self.date_label)
        h.addStretch(1)

        self.refresh_btn = QPushButton("刷新数据")
        self.refresh_btn.setObjectName("workbenchRefresh")
        self.refresh_btn.setIconSize(QSize(15, 15))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._refresh)
        h.addWidget(self.refresh_btn, 0, Qt.AlignRight)
        return card

    def _build_stats(self):
        row = self._stats_grid = QGridLayout()
        row.setSpacing(12)
        self._stat_cards = []
        cards = [
            ("platform_items", "平台内容", "B站 / 抖音 / 微博 / 小红书 / 快手内容条目"),
            ("chaohua_posts", "微博超话帖子", "config/weibo_chaohua.db 累计去重帖子"),
            ("tcdoc_rows", "腾讯文档行", "腾讯文档智能表格已同步的数据行"),
            ("asset_files", "资源文件", "asset_index 登记的本地资源文件"),
            ("doc_views", "文档浏览量", "腾讯文档各文档最新浏览量合计"),
            ("doc_members", "文档成员数", "腾讯文档各文档最新成员数合计"),
            ("doc_readers", "已查看人数", "腾讯文档各文档最新已查看人数合计"),
            ("tracked_accounts", "追踪账号", "网媒账号 + 音乐账号追踪的账号数量"),
            ("site_visitors", "站点访客", "mandisa.wiki 最近一次采集的访客"),
            ("site_sessions", "站点会话", "mandisa.wiki 最近一次采集的会话"),
            ("site_pageviews", "站点浏览量", "mandisa.wiki 最近一次采集的浏览量"),
            ("site_online", "站点在线", "最近一次采集时的在线人数"),
        ]
        for index, (key, caption, tip) in enumerate(cards):
            clickable = (key in ACCOUNT_DRAWER_KEYS or key in TCDOC_DRAWER_KEYS
                         or key in SITE_STAT_KEYS)
            if clickable:
                tip = f"{tip}\n点击在卡片内展开摘要，再次点击指标收起"
            card = _StatCard(key, tip, clickable=clickable)
            if clickable:
                card.clicked.connect(self._open_drawer)
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(4)
            value = QLabel("—")
            value.setObjectName("workbenchStatValue")
            value.setMinimumWidth(0)
            value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            value.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            value.setToolTip(tip)
            cap = QLabel(caption)
            cap.setObjectName("workbenchStatCaption")
            cap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            box.addWidget(cap)
            box.addWidget(value)
            if clickable:
                preview = QWidget()
                preview_box = QVBoxLayout(preview)
                preview_box.setContentsMargins(0, 8, 0, 0)
                preview_box.setSpacing(6)
                for _ in range(3):
                    line = QLabel()
                    line.setObjectName("workbenchStatPreviewLine")
                    line.setTextFormat(Qt.PlainText)
                    line.setWordWrap(True)
                    line.setMinimumWidth(0)
                    line.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                    preview_box.addWidget(line)
                details_btn = QPushButton("完整详情")
                details_btn.setToolTip("打开完整历史、筛选与操作")
                details_btn.clicked.connect(self._show_drawer)
                preview_box.addWidget(details_btn, 0, Qt.AlignLeft)
                box.addWidget(preview)
                preview.hide()
                card.preview = preview
                self._stat_previews[key] = preview
            self._stat_values[key] = value
            self._stat_cards.append(card)
            row.addWidget(card, index // 4, index % 4)
            row.setColumnStretch(index % 4, 1)
        return row

    # ==================== 卡片下钻抽屉 ====================
    def _build_drawer(self):
        frame = QFrame()
        frame.setObjectName("workbenchDrawer")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        # 高度由 _fit_drawer_height 按表格实际行数决定，不再固定留白
        frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        self.drawer_title = QLabel("详情")
        self.drawer_title.setObjectName("workbenchDrawerTitle")
        head.addWidget(self.drawer_title)
        self.drawer_sub = QLabel("")
        self.drawer_sub.setObjectName("workbenchHomeMuted")
        head.addWidget(self.drawer_sub)
        head.addStretch(1)
        layout.addLayout(head)

        self.drawer_stack = QStackedWidget()
        self.account_drawer_page = self._build_account_drawer_page()
        self.tcdoc_drawer_page = self._build_tcdoc_drawer_page()
        self.site_drawer_page = self._build_site_drawer_page()
        self.drawer_stack.addWidget(self.account_drawer_page)
        self.drawer_stack.addWidget(self.tcdoc_drawer_page)
        self.drawer_stack.addWidget(self.site_drawer_page)
        layout.addWidget(self.drawer_stack, 1)
        return frame

    def _build_account_drawer_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        bar = QHBoxLayout()
        hint = QLabel("展示已观察账号的最新采集数据（最多 5 个）；观察范围可在配置里调整。")
        hint.setObjectName("workbenchHomeMuted")
        hint.setWordWrap(True)
        bar.addWidget(hint, 1)
        config_btn = QPushButton("配置观察账号")
        config_btn.clicked.connect(self._open_account_config)
        bar.addWidget(config_btn)
        media_btn = QPushButton(f"{page_title('tracker')}追踪")
        media_btn.clicked.connect(lambda: self.app.show_page("tracker"))
        bar.addWidget(media_btn)
        music_btn = QPushButton(f"{page_title('music_tracker')}追踪")
        music_btn.clicked.connect(lambda: self.app.show_page("music_tracker"))
        bar.addWidget(music_btn)
        v.addLayout(bar)

        self.account_table = QTableWidget(0, 7)
        self.account_table.setHorizontalHeaderLabels(
            ["账号", "平台", "最新采集", "粉丝", "关注", "获赞/播放", "作品"])
        self.account_table.verticalHeader().setVisible(False)
        self.account_table.verticalHeader().setDefaultSectionSize(28)
        self.account_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.account_table.setSelectionMode(QTableWidget.NoSelection)
        self.account_table.setAlternatingRowColors(True)
        self.account_table.setWordWrap(False)
        header = self.account_table.horizontalHeader()
        for index, width in enumerate([185, 80, 150, 100, 90, 110]):
            header.resizeSection(index, width)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self._account_sorter = table_sort.install(
            self.account_table, skip_columns=())
        v.addWidget(self.account_table)

        self.account_status = QLabel("")
        self.account_status.setObjectName("workbenchHomeMuted")
        v.addWidget(self.account_status)
        return page

    def _build_tcdoc_drawer_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("文档"))
        self.tcdoc_doc_combo = QComboBox()
        self.tcdoc_doc_combo.setMinimumWidth(260)
        self.tcdoc_doc_combo.currentIndexChanged.connect(self._refresh_tcdoc_history)
        bar.addWidget(self.tcdoc_doc_combo, 1)
        self.tcdoc_status = QLabel("")
        self.tcdoc_status.setObjectName("workbenchHomeMuted")
        bar.addWidget(self.tcdoc_status)
        v.addLayout(bar)

        self.tcdoc_table = QTableWidget(0, 7)
        self.tcdoc_table.setHorizontalHeaderLabels(
            ["采集时间", "浏览量", "增量", "成员数", "增量", "已查看人数", "修订记录次数"])
        self.tcdoc_table.verticalHeader().setVisible(False)
        self.tcdoc_table.verticalHeader().setDefaultSectionSize(28)
        self.tcdoc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tcdoc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tcdoc_table.setAlternatingRowColors(True)
        self.tcdoc_table.setWordWrap(False)
        header = self.tcdoc_table.horizontalHeader()
        for index, width in enumerate([150, 90, 70, 80, 70, 100]):
            header.resizeSection(index, width)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        for column, tip in ((2, "相对上一条记录的浏览量增量"),
                            (4, "相对上一条记录的成员数增量"),
                            (6, "按采集日期统计的当天修订记录条数")):
            self.tcdoc_table.horizontalHeaderItem(column).setToolTip(tip)
        self._tcdoc_sorter = table_sort.install(
            self.tcdoc_table, skip_columns=())
        v.addWidget(self.tcdoc_table)
        return page

    def _build_site_drawer_page(self):
        from toolbox.webwiki import analytics, config as wiki_cfg

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("后台密码"))
        self.site_password_edit = QLineEdit()
        self.site_password_edit.setEchoMode(QLineEdit.Password)
        self.site_password_edit.setPlaceholderText(
            "已保存（输入新密码可替换）" if analytics.has_password()
            else "后台密码（与 /admin 登录同一个）")
        self.site_password_edit.setToolTip(
            "只加密保存在本机 config/webwiki_analytics.json；界面不回显密码。\n"
            "密码连续错 5 次会锁定 15 分钟。")
        self.site_password_edit.returnPressed.connect(self._save_site_password)
        row.addWidget(self.site_password_edit, 1)
        save_btn = QPushButton("保存密码")
        save_btn.clicked.connect(self._save_site_password)
        row.addWidget(save_btn)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("口径"))
        self.site_scope_combo = QComboBox()
        for key, label in analytics.SCOPES:
            self.site_scope_combo.addItem(label, key)
        self.site_scope_combo.setCurrentIndex(
            max(0, self.site_scope_combo.findData(wiki_cfg.analytics_scope())))
        self.site_scope_combo.setToolTip(
            "严格验证：只算通过 Turnstile 核验的访问（默认）\n"
            "严格验证 + 行为合格：加上有可信交互的候选\n"
            "全部分类：含待验证与疑似自动化")
        self.site_scope_combo.currentIndexChanged.connect(self._on_site_scope_changed)
        row2.addWidget(self.site_scope_combo)
        self.site_collect_btn = QPushButton("采集今日")
        self.site_collect_btn.setToolTip("向站点后台查询今日统计并写入本地库（按日期+口径去重）")
        self.site_collect_btn.clicked.connect(self._collect_site_analytics)
        row2.addWidget(self.site_collect_btn)
        admin_btn = QPushButton("打开后台")
        admin_btn.setToolTip("在浏览器打开 mandisa.wiki/admin")
        admin_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(analytics.ADMIN_URL)))
        row2.addWidget(admin_btn)
        refresh_btn = QPushButton("刷新历史")
        refresh_btn.clicked.connect(self._refresh_site_drawer)
        row2.addWidget(refresh_btn)
        row2.addStretch(1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(6)
        row3.addWidget(QLabel("补采日期"))
        self.site_date_edit = QDateEdit(QDate.currentDate().addDays(-1))
        self.site_date_edit.setCalendarPopup(True)
        self.site_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.site_date_edit.setMaximumDate(QDate.currentDate())
        self.site_date_edit.setFixedWidth(120)
        self.site_date_edit.setToolTip("选择要补采的历史日期（站点只保留近 90 天明细）")
        row3.addWidget(self.site_date_edit)
        self.site_date_btn = QPushButton("采集该日")
        self.site_date_btn.setToolTip("向站点查询所选日期的统计并写入本地库")
        self.site_date_btn.clicked.connect(self._collect_site_date)
        row3.addWidget(self.site_date_btn)
        self.site_backfill_btn = QPushButton("补采最近 7 天")
        self.site_backfill_btn.setToolTip("从昨天起往前补采 7 天；已有日期会按最新结果覆盖")
        self.site_backfill_btn.clicked.connect(self._backfill_site_analytics)
        row3.addWidget(self.site_backfill_btn)
        row3.addStretch(1)
        v.addLayout(row3)

        self.site_status = QLabel("")
        self.site_status.setObjectName("workbenchHomeMuted")
        self.site_status.setWordWrap(True)
        v.addWidget(self.site_status)

        self.site_schedule = QLabel("")
        self.site_schedule.setObjectName("workbenchHomeMuted")
        self.site_schedule.setWordWrap(True)
        v.addWidget(self.site_schedule)

        self.site_history_table = QTableWidget(0, 7)
        self.site_history_table.setHorizontalHeaderLabels(
            ["日期", "口径", "访客", "会话", "浏览量", "在线", "采集时间"])
        self.site_history_table.verticalHeader().setVisible(False)
        self.site_history_table.verticalHeader().setDefaultSectionSize(28)
        self.site_history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.site_history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.site_history_table.setAlternatingRowColors(True)
        self.site_history_table.setWordWrap(False)
        header = self.site_history_table.horizontalHeader()
        for index, width in enumerate([95, 120, 80, 80, 90, 70]):
            header.resizeSection(index, width)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.site_history_table.setToolTip("本地历史（最多 14 条）：每天一条，按日期+口径去重")
        self._site_history_sorter = table_sort.install(
            self.site_history_table, skip_columns=())
        v.addWidget(self.site_history_table)

        log_cap = QLabel("采集历史（最近 50 次）")
        log_cap.setObjectName("workbenchHomeMuted")
        v.addWidget(log_cap)
        self.site_log_table = QTableWidget(0, 5)
        self.site_log_table.setHorizontalHeaderLabels(
            ["时间", "日期", "口径", "结果", "说明"])
        self.site_log_table.verticalHeader().setVisible(False)
        self.site_log_table.verticalHeader().setDefaultSectionSize(26)
        self.site_log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.site_log_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.site_log_table.setAlternatingRowColors(True)
        self.site_log_table.setWordWrap(False)
        log_header = self.site_log_table.horizontalHeader()
        for index, width in enumerate([140, 95, 120, 60]):
            log_header.resizeSection(index, width)
        log_header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.site_log_table.setToolTip("每次采集尝试的记录：成功或失败原因（最多 50 条）")
        self._site_log_sorter = table_sort.install(
            self.site_log_table, skip_columns=())
        v.addWidget(self.site_log_table)
        return page

    def _save_site_password(self):
        from toolbox.webwiki import analytics

        text = self.site_password_edit.text().strip()
        if not text:
            self.site_status.setText("请输入 mandisa.wiki 后台密码")
            return
        try:
            encrypted = analytics.save_password(text)
        except OSError as exc:
            self.site_status.setText(f"密码写入失败：{exc}")
            return
        self.site_password_edit.clear()
        self.site_password_edit.setPlaceholderText("已保存（输入新密码可替换）")
        self.site_status.setText("后台密码已保存" if encrypted
                                 else "密码已保存（本机缺少 cryptography，暂为明文）")
        for button in (self.site_collect_btn, self.site_date_btn,
                       self.site_backfill_btn):
            button.setEnabled(not self._site_busy)

    def _on_site_scope_changed(self, _index=None):
        from toolbox.webwiki import config as wiki_cfg

        wiki_cfg.set_analytics_scope(self.site_scope_combo.currentData() or "human")
        self._refresh_site_drawer()
        self._refresh()

    def _ensure_site_password(self) -> bool:
        from toolbox.webwiki import analytics

        typed = self.site_password_edit.text().strip()
        if typed:
            try:
                analytics.save_password(typed)
            except OSError as exc:
                self.site_status.setText(f"密码写入失败：{exc}")
                return False
            self.site_password_edit.clear()
            self.site_password_edit.setPlaceholderText("已保存（输入新密码可替换）")
        if not analytics.has_password():
            self.site_status.setText("还缺后台密码：先填写 mandisa.wiki 后台密码并保存")
            return False
        return True

    def _site_scope(self) -> str:
        return self.site_scope_combo.currentData() or "human"

    def _begin_site_task(self, text: str) -> None:
        self._site_busy = True
        for button in (self.site_collect_btn, self.site_date_btn,
                       self.site_backfill_btn):
            button.setEnabled(False)
        self.site_status.setText(text)

    def _finish_site_task(self) -> None:
        self._site_busy = False
        self._refresh_site_drawer()
        self._refresh()

    def _collect_site_analytics(self):
        if self._site_busy:
            return
        if not self._ensure_site_password():
            return
        self._start_site_collect(None, "今日")

    def _collect_site_date(self):
        if self._site_busy:
            return
        if not self._ensure_site_password():
            return
        target = self.site_date_edit.date().toString("yyyy-MM-dd")
        self._start_site_collect(target, target)

    def _start_site_collect(self, date: str | None, label: str) -> None:
        from toolbox.webwiki import analytics

        scope = self._site_scope()
        self._begin_site_task(f"正在采集{label}站点统计…")

        def work():
            return analytics.collect(scope=scope, date=date)

        def done(row, err):
            self._finish_site_task()
            if err:
                self.site_status.setText(f"采集{label}失败：{err}")
                return
            row = row or {}
            self.site_status.setText(
                f"采集完成（{label}）：{row.get('stat_date') or ''} · "
                f"访客 {int(row.get('visitors') or 0):,} · "
                f"会话 {int(row.get('sessions') or 0):,} · "
                f"浏览量 {int(row.get('pageviews') or 0):,}")

        run_bg(work, done, owner=self)

    def _backfill_site_analytics(self):
        if self._site_busy:
            return
        if not self._ensure_site_password():
            return
        from toolbox.webwiki import analytics

        try:
            base = datetime.strptime(analytics.today(), "%Y-%m-%d").date()
        except ValueError:
            base = datetime.now().date()
        days = [(base - timedelta(days=offset)).strftime("%Y-%m-%d")
                for offset in range(1, 8)]
        scope = self._site_scope()
        self._begin_site_task(
            f"正在补采最近 7 天（{days[-1]} ~ {days[0]}）…")

        def work():
            ok, fail, errors = 0, 0, []
            for day in days:
                try:
                    analytics.collect(scope=scope, date=day)
                    ok += 1
                except Exception as exc:  # noqa: BLE001 - 单日失败继续补采
                    fail += 1
                    errors.append(f"{day}: {exc}")
                time.sleep(0.4)
            return ok, fail, errors

        def done(result, err):
            self._finish_site_task()
            if err:
                self.site_status.setText(f"补采失败：{err}")
                return
            ok, fail, errors = result
            message = f"补采完成：成功 {ok}/7，失败 {fail}"
            if errors:
                message += f"；{errors[0]}"
            self.site_status.setText(message)

        run_bg(work, done, owner=self)

    def _refresh_site_drawer(self):
        from toolbox.webwiki import analytics

        rows = analytics.load_history_ro(limit=14)
        self.site_history_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                str(row.get("stat_date") or ""),
                analytics.scope_label(row.get("scope")),
                f"{int(row.get('visitors') or 0):,}",
                f"{int(row.get('sessions') or 0):,}",
                f"{int(row.get('pageviews') or 0):,}",
                f"{int(row.get('online') or 0):,}",
                str(row.get("fetched_at") or ""),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if 2 <= column <= 5:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.site_history_table.setItem(index, column, item)
        logs = analytics.load_fetch_log_ro(limit=50)
        self.site_log_table.setRowCount(len(logs))
        for index, row in enumerate(logs):
            values = (
                str(row.get("at") or ""),
                str(row.get("stat_date") or ""),
                analytics.scope_label(row.get("scope")),
                "成功" if int(row.get("ok") or 0) else "失败",
                str(row.get("message") or ""),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.site_log_table.setItem(index, column, item)
        self._site_history_sorter.apply()
        self._site_log_sorter.apply()
        try:
            from toolbox.webwiki import analytics_task

            self.site_schedule.setText(
                f"定时采集：{analytics_task.next_run_text()}（计划在「任务」页修改）")
        except Exception:  # noqa: BLE001 - 计划任务查询失败不影响历史展示
            self.site_schedule.setText("定时采集：状态不可用（计划在「任务」页查看）")
        self._fit_table_height(self.site_log_table, 6)
        self._fit_drawer_height()
        for button in (self.site_collect_btn, self.site_date_btn,
                       self.site_backfill_btn):
            button.setEnabled(not self._site_busy)
        if rows:
            latest = rows[0]
            latest_at = str(latest.get("fetched_at") or latest.get("stat_date") or "")
            self.site_status.setText(
                f"本地历史 {len(rows)} 条 · 最近采集 {latest_at} · 采集记录 {len(logs)} 条".rstrip())
        else:
            self.site_status.setText(
                "尚无历史数据：保存后台密码后点「采集今日」，或等每日任务运行")

    def _open_drawer(self, key):
        if key in SITE_STAT_KEYS:
            mode = "site"
        elif key in ACCOUNT_DRAWER_KEYS:
            mode = "accounts"
        elif key in TCDOC_DRAWER_KEYS:
            mode = "tcdoc"
        else:
            return
        # 首次只展开所点卡片的摘要；完整抽屉由「完整详情」显式打开。
        if self._drawer_key == key:
            self._close_drawer()
            return
        self._close_drawer()
        self._drawer_mode = mode
        self._drawer_key = key
        self._stat_previews[key].show()
        if mode == "accounts":
            self.drawer_title.setText("追踪账号")
            self.drawer_sub.setText(
                "网媒 + 音乐账号 · 勾选观察范围并查看最新数据（再次点卡片收起）")
            self.drawer_stack.setCurrentWidget(self.account_drawer_page)
            self._refresh_account_drawer()
        elif mode == "tcdoc":
            self.drawer_title.setText("腾讯文档 · 成员名单历史")
            self.drawer_sub.setText(
                "浏览量 / 增量 / 成员数 / 增量 / 已查看人数 / 修订记录次数"
                "（再次点卡片收起）")
            self.drawer_stack.setCurrentWidget(self.tcdoc_drawer_page)
            self._refreshtcdoc_docs()
        else:
            self.drawer_title.setText("站点访问 · mandisa.wiki")
            self.drawer_sub.setText(
                "后台密码、采集今日与本地历史（再次点卡片收起）")
            self.drawer_stack.setCurrentWidget(self.site_drawer_page)
            self._refresh_site_drawer()
        self._refresh_stat_preview()
        card = next(card for card in self._stat_cards if card._key == key)
        self.scroll.ensureWidgetVisible(card, 0, 16)

    def _show_drawer(self):
        if not self._drawer_key:
            return
        self.drawer.show()
        self._fit_drawer_height()
        self.scroll.ensureWidgetVisible(self.drawer, 0, 16)

    def _close_drawer(self):
        self.drawer.hide()
        for preview in self._stat_previews.values():
            preview.hide()
        self._drawer_mode = ""
        self._drawer_key = ""

    def _refresh_stat_preview(self):
        """复用当前详情表的数据，只摘出所点指标的三条记录，不另外取数。"""
        key = self._drawer_key
        preview = self._stat_previews.get(key)
        if preview is None:
            return

        def cell(table, row, column):
            item = table.item(row, column)
            return item.text() if item is not None else "—"

        if key in SITE_STAT_KEYS:
            table = self.site_history_table
            column = {"site_visitors": 2, "site_sessions": 3,
                      "site_pageviews": 4, "site_online": 5}[key]
            lines = [f"{cell(table, row, 0)} · {cell(table, row, 1)}\n"
                     f"{cell(table, row, column)}"
                     for row in range(min(3, table.rowCount()))]
            empty = "尚无历史记录，可在完整详情中采集"
        elif key in TCDOC_DRAWER_KEYS:
            table = self.tcdoc_table
            column = {"doc_views": 1, "doc_members": 3, "doc_readers": 5,
                      "tcdoc_rows": 1}[key]
            metric = "浏览量 " if key == "tcdoc_rows" else ""
            name = self.tcdoc_doc_combo.currentText()
            lines = [f"{name} · {cell(table, row, 0)}\n"
                     f"{metric}{cell(table, row, column)}"
                     for row in range(min(3, table.rowCount()))]
            empty = self.tcdoc_status.text() or "尚无文档历史记录"
        else:
            table = self.account_table
            lines = [f"{cell(table, row, 0)} · {cell(table, row, 1)}\n"
                     f"粉丝 {cell(table, row, 3)}"
                     for row in range(min(3, table.rowCount()))]
            empty = self.account_status.text() or "尚无观察账号"
        for index, label in enumerate(preview.findChildren(QLabel)):
            text = lines[index] if index < len(lines) else (empty if index == 0 else "")
            label.setText(text)
            label.setToolTip(text)
            label.setVisible(bool(text))

    @staticmethod
    def _fit_table_height(table, max_rows: int = 6) -> None:
        rows = max(1, min(table.rowCount(), max_rows))
        row_h = table.verticalHeader().defaultSectionSize()
        header_h = max(22, table.horizontalHeader().sizeHint().height())
        table.setFixedHeight(header_h + row_h * rows + 2 * table.frameWidth() + 2)

    def _fit_drawer_height(self):
        """按当前页表格的实际行数收紧抽屉高度，避免数据少时留一大片空白。"""
        self._refresh_stat_preview()
        if self.drawer.isHidden():
            return
        stack = getattr(self, "drawer_stack", None)
        if stack is None or getattr(self, "drawer", None) is None:
            return
        page = stack.currentWidget()
        if page is self.account_drawer_page:
            table = self.account_table
        elif page is self.tcdoc_drawer_page:
            table = self.tcdoc_table
        elif page is self.site_drawer_page:
            table = self.site_history_table
        else:
            return
        rows = max(1, min(table.rowCount(), DRAWER_MAX_ROWS))
        row_h = table.verticalHeader().defaultSectionSize()
        header_h = max(22, table.horizontalHeader().sizeHint().height())
        table.setFixedHeight(header_h + row_h * rows + 2 * table.frameWidth() + 2)
        # QStackedWidget 的 sizeHint 取所有子页的最大值，数据少的那页会被
        # 别的页撑高，抽屉中间留下一大片空白。这里按当前页收紧，切页时重算。
        if page.layout() is not None:
            page.layout().activate()
        stack.setFixedHeight(page.sizeHint().height())
        layout = self.drawer.layout()
        if layout is None:
            return
        layout.activate()
        height = max(layout.sizeHint().height(), layout.minimumSize().height())
        # 自动换行的提示文字在窄窗口下会多占一行，sizeHint 算不到，按实际宽度补差
        inner = page.layout()
        if inner is not None:
            for widget in page.findChildren(QLabel):
                if not widget.wordWrap() or widget.width() <= 0:
                    continue
                need = widget.heightForWidth(widget.width())
                if need > 0:
                    height += max(0, need - widget.height())
        self.drawer.setFixedHeight(height)

    def _refresh_account_drawer(self):
        accounts = _tracked_accounts_snapshot()
        watched = _watched_account_keys()
        all_keys = {account["key"] for account in accounts}
        selected = all_keys if watched is None else (watched & all_keys)
        display = [account for account in accounts if account["key"] in selected][:5]

        table = self.account_table
        table.setRowCount(len(display))
        if not accounts:
            self.account_status.setText("未读取到账号配置，请到对应追踪页添加")
            self._fit_drawer_height()
            return
        if not display:
            self.account_status.setText(
                "当前未勾选观察账号，点「配置观察账号」选择")
            self._fit_drawer_height()
            return

        for row, account in enumerate(display):
            table.setItem(row, 0, QTableWidgetItem(account["name"]))
            table.setItem(row, 1, QTableWidgetItem(account["platform_label"]))
            table.setItem(row, 2, QTableWidgetItem("读取中…"))
            for column in range(3, 7):
                table.setItem(row, column, QTableWidgetItem("…"))
            tip = account.get("url") or account["name"]
            for column in range(0, 7):
                item = table.item(row, column)
                if item is not None:
                    item.setToolTip(tip)

        self._account_seq += 1
        seq = self._account_seq
        limit_note = " · 仅展示前 5 个" if len(selected) > 5 else ""
        self.account_status.setText(
            f"已观察 {len(selected)} / 共 {len(accounts)} 个账号{limit_note}"
            " · 正在读取最新数据…")
        self._fit_drawer_height()

        def work():
            result = {}
            for account in display:
                try:
                    result[account["key"]] = load_account_history(account)
                except Exception:
                    result[account["key"]] = []
            return result

        def done(histories, err):
            if seq != self._account_seq or self._drawer_mode != "accounts":
                return
            if err:
                self.account_status.setText(f"读取账号数据失败：{err}")
                return
            for row, account in enumerate(display):
                history = (histories or {}).get(account["key"]) or []
                self._fill_account_row(row, account, history)
            self._account_sorter.apply()
            self.account_status.setText(
                f"已观察 {len(selected)} / 共 {len(accounts)} 个账号{limit_note}")
            self._fit_drawer_height()

        run_bg(work, done)

    def _fill_account_row(self, row, account, history):
        latest = history[0] if history else {}
        previous = history[1] if len(history) > 1 else {}
        table = self.account_table
        time_text = (latest.get("_time") or "")[:16]
        table.setItem(row, 2, QTableWidgetItem(time_text or "尚未采集"))
        if account["source"] == "music":
            other_field, works_field = "listens", "works"
        else:
            other_field = "likes"
            works_field = ("weibo_count" if account["platform"] == "weibo"
                           else "video_count")
        self._set_metric(row, 3, latest, previous, "fans")
        self._set_metric(row, 4, latest, previous, "following")
        self._set_metric(row, 5, latest, previous, other_field)
        self._set_metric(row, 6, latest, previous, works_field)

    def _set_metric(self, row, column, latest, previous, field):
        value = latest.get(field)
        item = QTableWidgetItem(self._num_text(value))
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if previous:
            try:
                delta = int(float(value)) - int(float(previous.get(field)))
            except (TypeError, ValueError):
                delta = None
            if delta:
                item.setToolTip(
                    f"上次 {self._num_text(previous.get(field))} · 变化 {delta:+d}")
        self.account_table.setItem(row, column, item)

    def _open_account_config(self):
        accounts = _tracked_accounts_snapshot()
        if not accounts:
            self.app.toast("未读取到账号配置，请先到账号追踪页添加", "warning")
            return
        watched = _watched_account_keys()
        selected = ({account["key"] for account in accounts}
                    if watched is None else (watched & {a["key"] for a in accounts}))
        dialog = _AccountConfigDialog(self, accounts, selected)
        if dialog.exec() != QDialog.Accepted:
            return
        keys = dialog.selected_keys()
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            config = load_convert_config()
            config["home_watch_accounts"] = keys
            save_convert_config(config)
        except Exception as exc:
            self.app.toast(f"保存观察账号失败：{exc}", "warning")
            return
        label = self._stat_values.get("tracked_accounts")
        if label is not None:
            label.setText(f"{len(keys):,}")
            label.setToolTip(
                f"已观察 {len(keys)} / 共 {len(accounts)} 个账号\n点击配置观察账号")
        self.app.toast(
            f"观察账号已保存：{len(keys)}/{len(accounts)}", "success")
        self._refresh_account_drawer()

    def _refreshtcdoc_docs(self):
        current = self.tcdoc_doc_combo.currentData()
        docs = tcdoc_docs()
        self.tcdoc_doc_combo.blockSignals(True)
        self.tcdoc_doc_combo.clear()
        for doc in docs:
            self.tcdoc_doc_combo.addItem(doc.get("name") or doc.get("key") or "文档",
                                         doc.get("key") or "")
        if docs:
            index = self.tcdoc_doc_combo.findData(current) if current else 0
            self.tcdoc_doc_combo.setCurrentIndex(max(0, index))
        self.tcdoc_doc_combo.blockSignals(False)
        self._refresh_tcdoc_history()

    def _refresh_tcdoc_history(self):
        doc_key = self.tcdoc_doc_combo.currentData()
        if not doc_key:
            self.tcdoc_table.setRowCount(0)
            self.tcdoc_status.setText("暂无腾讯文档")
            self._fit_drawer_height()
            return
        try:
            result = load_tcdoc_history(doc_key)
        except Exception as exc:
            self.tcdoc_table.setRowCount(0)
            self.tcdoc_status.setText(f"读取失败：{exc}")
            self._fit_drawer_height()
            return
        rows = result.get("rows") or []
        revisions = result.get("revisions") or {}
        table = self.tcdoc_table
        table.setUpdatesEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            day = str(row.get("date") or str(row.get("ts") or "")[:10])
            values = [
                row.get("ts") or "",
                self._num_text(row.get("views")),
                self._delta_text(row.get("views_delta")),
                self._num_text(row.get("members")),
                self._delta_text(row.get("members_delta")),
                self._num_text(row.get("readers")),
                str(revisions.get(day, 0)),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column > 0:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column, item)
        table.setUpdatesEnabled(True)
        self._tcdoc_sorter.apply()
        if rows:
            latest = rows[0]
            total = int(result.get("total") or len(rows))
            self.tcdoc_status.setText(
                f"展示最近 {len(rows)} 条 / 共 {total} 条 ｜ "
                f"最新 浏览量 {self._num_text(latest.get('views'))}"
                f" / 成员 {self._num_text(latest.get('members'))}"
                f" / 已查看 {self._num_text(latest.get('readers'))}")
        else:
            self.tcdoc_status.setText("暂无历史记录，请先到「腾讯文档」采集")
        self._fit_drawer_height()

    @staticmethod
    def _num_text(value):
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _delta_text(value):
        if value is None:
            return "—"
        try:
            number = int(value)
        except (TypeError, ValueError):
            return "—"
        return f"+{number}" if number > 0 else str(number)

    def _build_charts(self):
        row = self._charts_grid = QGridLayout()
        row.setSpacing(12)

        donut_card, donut_box = self._panel(
            "内容分布", "平台内容、微博超话、腾讯文档、本地资源的数据量占比")
        self.donut = _DonutChart()
        donut_box.addWidget(self.donut, 1)
        row.addWidget(donut_card, 0, 1)

        trend_card, trend_box = self._panel("采集趋势", "最近 7 天 · 全功能新增")
        self.trend_sub = trend_card.subtitle_label
        self.trend = _TrendChart()
        trend_box.addWidget(self.trend, 1)
        row.addWidget(trend_card, 0, 0)
        self._chart_cards = [trend_card, donut_card]
        return row

    @staticmethod
    def _panel(title, subtitle):
        card = QFrame()
        card.setObjectName("workbenchPanelCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 16, 18, 12)
        box.setSpacing(4)
        name = QLabel(title)
        name.setObjectName("workbenchPanelTitle")
        box.addWidget(name)
        sub = QLabel(subtitle)
        sub.setObjectName("workbenchHomeMuted")
        box.addWidget(sub)
        card.subtitle_label = sub
        return card, box

    @staticmethod
    def _section_title(title, subtitle):
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(2, 4, 2, 0)
        h.setSpacing(8)
        name = QLabel(title)
        name.setObjectName("workbenchSectionTitle")
        h.addWidget(name)
        sub = QLabel(subtitle)
        sub.setObjectName("workbenchHomeMuted")
        h.addWidget(sub)
        h.addStretch(1)
        return wrap

    def _build_tool_grid(self, tools):
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        cards = []
        for i, (key, title, desc) in enumerate(tools):
            card = _ToolCard(key, title, desc, lambda checked=False, k=key: self._open_tool(k))
            cards.append(card)
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self._tool_grids.append((grid, cards))
        return grid

    @staticmethod
    def _arrange_grid(grid, widgets, columns):
        while grid.count():
            grid.takeAt(0)
        for column in range(4):
            grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, widget in enumerate(widgets):
            grid.addWidget(widget, index // columns, index % columns)

    def _layout_for_width(self):
        narrow = self.width() < 820
        layout_key = (2 if narrow else 3, 2 if self.width() < 740 else 4,
                      1 if self.width() < 880 else 2)
        if self._last_layout == layout_key:
            return
        self._last_layout = layout_key
        tools, stats, charts = layout_key
        for grid, cards in self._tool_grids:
            self._arrange_grid(grid, cards, tools)
        self._arrange_grid(self._stats_grid, self._stat_cards, stats)
        for card in self._stat_cards:
            self._stats_grid.setAlignment(card, Qt.AlignTop)
        self._arrange_grid(self._charts_grid, self._chart_cards, charts)
        self.body.layout().setContentsMargins(16, 16, 16, 16)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_chart_cards"):
            self._layout_for_width()

    # ==================== 数据刷新 ====================

    def _open_tool(self, key):
        """由主窗口统一路由，搜索、首页和页面内跳转共用同一实例。"""
        self.app.show_page(key)

    def on_show(self):
        self._refresh()

    def _refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.refresh_btn.setEnabled(False)
        run_bg(load_dashboard_data, self._on_data)

    def _on_data(self, data, err):
        self._refreshing = False
        self.refresh_btn.setEnabled(True)
        if err or not data:
            return
        stats = data.get("stats") or {}
        by_source = data.get("by_source") or {}
        counts = data.get("counts") or []
        labels = data.get("labels") or []

        for key, label in self._stat_values.items():
            label.setText(f"{int(stats.get(key, 0)):,}")
        self._stat_values["doc_views"].setToolTip(
            f"腾讯文档 {int(stats.get('doc_count', 0))} 个文档的最新浏览量合计")
        self._stat_values["doc_members"].setToolTip(
            f"腾讯文档 {int(stats.get('doc_count', 0))} 个文档的最新成员数合计")
        self._stat_values["doc_readers"].setToolTip(
            f"腾讯文档 {int(stats.get('doc_count', 0))} 个文档的最新已查看人数合计")
        self._stat_values["tracked_accounts"].setToolTip(
            f"已观察 {int(stats.get('tracked_accounts', 0))} / "
            f"共 {int(stats.get('tracked_total', 0))} 个账号\n点击配置观察账号")

        scope = str(stats.get("site_scope") or "严格验证")
        if int(stats.get("site_collected", 0)):
            fetched = str(stats.get("site_fetched_at") or "")
            stat_date = str(stats.get("site_date") or "")
            head = f"mandisa.wiki 站点访问 · {scope}"
            if stat_date:
                head += f"\n统计日期：{stat_date}"
            if fetched:
                head += f"\n采集时间：{fetched}"
            for key, label in (("site_visitors", "访客"),
                               ("site_sessions", "会话"),
                               ("site_pageviews", "浏览量"),
                               ("site_online", "在线")):
                self._stat_values[key].setToolTip(
                    f"{head}\n{label}：{int(stats.get(key, 0)):,}\n点击在卡片内展开摘要")
        else:
            for key in SITE_STAT_KEYS:
                self._stat_values[key].setText("—")
                self._stat_values[key].setToolTip(
                    f"尚无站点访问统计（口径：{scope}）\n点击在卡片内展开摘要")

        self.donut.set_items(_dist_items(by_source))
        self.trend.set_data(labels, counts)
        week_total = int(data.get("week_total") or sum(counts))
        today_total = int(data.get("today_total") or (counts[-1] if counts else 0))
        self.trend_sub.setText(
            f"最近 7 天 · 全功能新增 {week_total:,} 条 · 今日 {today_total:,} 条")

    # ==================== 主题 ====================

    def on_theme_changed(self):
        self._apply_theme_styles()
        self.donut.update()
        self.trend.update()

    def _apply_theme_styles(self):
        self.setStyleSheet("""
            QWidget#workbenchHomeBody { background: palette(window); }
            QWidget#workbenchHero { background: transparent; }
            QLabel#workbenchHomeMuted { color: palette(placeholder-text); background: transparent; font-size: 11px; }
            QPushButton#workbenchRefresh {
                background: palette(base); color: palette(text);
                border: 1px solid palette(shadow); border-radius: 7px;
                padding: 6px 11px; font-size: 11px;
            }
            QPushButton#workbenchRefresh:hover { background: palette(midlight); color: palette(link); border-color: palette(highlight); }
            QPushButton#workbenchRefresh:disabled { background: palette(alternate-base); color: palette(placeholder-text); border-color: palette(shadow); }
            QFrame#workbenchStatCard, QFrame#workbenchPanelCard {
                background: palette(base); border: 1px solid palette(shadow); border-radius: 12px;
            }
            QFrame#workbenchStatCard[clickable="true"]:hover {
                background: palette(alternate-base); border-color: palette(highlight);
            }
            QFrame#workbenchDrawer {
                background: palette(base); border: 1px solid palette(highlight); border-radius: 12px;
            }
            QLabel#workbenchDrawerTitle {
                color: palette(text); background: transparent; font-size: 15px; font-weight: bold;
            }
            QLabel#workbenchStatValue { color: palette(text); background: transparent; font-family: "Segoe UI", "Microsoft YaHei"; font-size: 27px; font-weight: bold; }
            QLabel#workbenchStatCaption { color: palette(placeholder-text); background: transparent; font-size: 11px; }
            QLabel#workbenchStatPreviewLine { color: palette(text); background: transparent; font-size: 12px; }
            QLabel#workbenchPanelTitle, QLabel#workbenchSectionTitle { color: palette(text); background: transparent; font-size: 14px; font-weight: bold; }
            QPushButton#workbenchToolCard {
                background: palette(base); border: 1px solid palette(shadow); border-radius: 10px;
                padding: 0; text-align: left;
            }
            QPushButton#workbenchToolCard:hover { background: palette(alternate-base); border-color: palette(highlight); }
            QPushButton#workbenchToolCard:pressed { background: palette(midlight); }
            QPushButton#workbenchToolCard:focus { border-color: palette(highlight); }
            QLabel#workbenchToolIcon { background: palette(midlight); border-radius: 9px; border: none; }
            QLabel#workbenchToolTitle { color: palette(text); background: transparent; border: none; font-size: 12px; font-weight: bold; }
            QLabel#workbenchToolArrow { color: palette(placeholder-text); background: transparent; border: none; font-size: 20px; }
            QLabel#workbenchToolDesc { color: palette(placeholder-text); background: transparent; border: none; font-size: 10px; }
        """)
        icon_color = theme.current_palette().color(QPalette.PlaceholderText).name()
        self.refresh_btn.setIcon(line_icon("refresh", icon_color, 16))
        for _grid, cards in self._tool_grids:
            for card in cards:
                card.sync_icon()
        self.date_label.setText(
            datetime.now().strftime("%Y-%m-%d")
            + "  周" + "一二三四五六日"[datetime.now().weekday()])
