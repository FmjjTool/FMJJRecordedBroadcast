"""首页：数据概览 + 常用工具入口。

- 默认启动页；点击侧栏顶部标题可随时回到本页
- 指标卡：已获取总数 / 覆盖平台 / 近 7 天新增 / 今日新增
- 图表：平台分布环形图 + 近 7 天新增柱状图（QPainter 自绘，跟随深浅主题）
- 常用工具：原侧栏中的信息类页面迁到本页，避免信息分组过长
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

# 平台显示名与图表配色
PLATFORM_META = {
    "bilibili": ("B站", "#fb7299"),
    "douyin": ("抖音", "#00bcd4"),
    "weibo": ("微博", "#e6a23c"),
    "xiaohongshu": ("小红书", "#ff4d6d"),
    "kuaishou": ("快手", "#ff8f1f"),
}
OTHER_COLOR = "#8b93a7"

# 常用工具：(key, 图标, 标题, 说明)
COMMON_TOOLS = [
    ("info_accounts", "🔍", "信息获取", "批量链接解析与账号采集"),
    ("info_results", "📦", "已获取", "结果库、下载与导出"),
    ("asset", "🗂", "资产管理", "已获取资源与本地扫描"),
    ("toolbox_editor", "🎬", "视频剪辑", "合并 / 切割 / 压缩 / 轨道"),
    ("toolbox_subtitle", "📝", "字幕转换", "字幕与歌词格式转换"),
    ("music_search_download", "🎵", "音乐搜索下载", "五平台信息与歌曲下载"),
]

INFO_TOOLS = [
    ("douyin_index", "📈", "抖音指数", "达人指标、作品与粉丝画像"),
    ("wechat_article", "📰", "公众号解析", "文章导出 HTML / PDF / Word"),
    ("bili_resource", "🎁", "B站资源", "表情包、收藏集、空间资源"),
    ("video_comments", "💬", "视频评论区", "三平台评论抓取与长图"),
    ("tcdoc", "📄", "腾讯文档监控", "浏览量 / 成员数每日记录"),
]
INFO_TOOL_KEYS = {key for key, *_ in INFO_TOOLS}

INFO_TOOL_KEYS = {key for key, *_ in INFO_TOOLS}


def _load_dashboard_data():
    """读取 collected.db：总数、平台分布、近 7 天每日新增。"""
    from gui_qt.common import _db_path

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=6)
    labels, counts = [], []
    for i in range(7):
        d = start + timedelta(days=i)
        labels.append(d.strftime("%m-%d"))
        counts.append(0)

    data = {"total": 0, "by_platform": {}, "labels": labels, "counts": counts}
    path = _db_path()
    if not path.exists():
        return data
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        data["total"] = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        rows = conn.execute(
            "SELECT platform, COUNT(*) FROM videos GROUP BY platform").fetchall()
        data["by_platform"] = {str(p or ""): int(n) for p, n in rows}

        trend_rows = conn.execute(
            "SELECT date(created_at, 'unixepoch', 'localtime') AS d, COUNT(*) "
            "FROM videos WHERE created_at >= ? GROUP BY d",
            (start.timestamp(),)).fetchall()
        trend = {str(d): int(n) for d, n in trend_rows}
        data["counts"] = [
            trend.get((start + timedelta(days=i)).strftime("%Y-%m-%d"), 0)
            for i in range(7)
        ]
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return data


def _dist_items(by_platform):
    """平台计数 → 环形图数据，未知平台合并为「其他」。"""
    rest = dict(by_platform)
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
        self.setMinimumHeight(230)

    def set_items(self, items):
        self._items = list(items or [])
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = theme.is_dark()
        text_color = QColor("#c6cbd6" if dark else "#2f3440")
        muted = QColor("#7f8798" if dark else "#9aa0b0")
        ring_bg = QColor("#2b3040" if dark else "#eef1f6")

        w, h = self.width(), self.height()
        size = max(80.0, min(h - 26, w * 0.5))
        rect = QRectF(12, (h - size) / 2, size, size)
        thickness = max(16.0, size * 0.115)

        p.setPen(QPen(ring_bg, thickness, Qt.SolidLine, Qt.FlatCap))
        p.drawEllipse(rect.adjusted(thickness / 2, thickness / 2,
                                    -thickness / 2, -thickness / 2))

        total = sum(item[1] for item in self._items)
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
        f.setPointSize(20)
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect.adjusted(0, -10, 0, -10), Qt.AlignCenter, str(total))
        f.setPointSize(9)
        f.setBold(False)
        p.setFont(f)
        p.setPen(muted)
        p.drawText(rect.adjusted(0, 24, 0, 24), Qt.AlignCenter, "条")

        # 右侧图例
        lx = rect.right() + 20
        available = max(90.0, w - lx - 10)
        line_h = 24
        ly = max(14.0, (h - line_h * max(1, len(self._items))) / 2)
        for label, value, color in self._items:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(lx, ly + 7, 9, 9), 4.5, 4.5)
            p.setPen(text_color)
            f.setPointSize(9)
            p.setFont(f)
            pct = value / total * 100 if total else 0
            text = f"{label}  {value} · {pct:.0f}%"
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
        self.setMinimumHeight(230)

    def set_data(self, labels, values):
        self._labels = list(labels or [])
        self._values = list(values or [])
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = theme.is_dark()
        text_color = QColor("#c6cbd6" if dark else "#2f3440")
        muted = QColor("#7f8798" if dark else "#9aa0b0")
        grid = QColor("#2f3442" if dark else "#e8ecf3")
        accent = QColor("#64b5f6" if dark else "#1976d2")

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


class _ToolCard(QFrame):
    """首页工具入口卡片。"""

    def __init__(self, emoji, title, desc, callback, parent=None):
        super().__init__(parent)
        self._callback = callback
        self.setObjectName("toolCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(76)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 11, 14, 11)
        v.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(8)
        icon = QLabel(emoji)
        icon.setObjectName("toolIcon")
        top.addWidget(icon)
        name = QLabel(title)
        name.setObjectName("toolTitle")
        top.addWidget(name, 1)
        v.addLayout(top)

        tip = QLabel(desc)
        tip.setObjectName("toolDesc")
        tip.setWordWrap(True)
        v.addWidget(tip)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._callback:
            self._callback()
        super().mousePressEvent(event)


class HomePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._stat_values = {}
        self._refreshing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(18, 16, 18, 18)
        v.setSpacing(14)

        v.addWidget(self._build_hero())
        v.addLayout(self._build_stats())
        v.addLayout(self._build_charts())

        v.addWidget(self._section_title("🧰 常用工具", "高频入口，一键直达"))
        v.addLayout(self._build_tool_grid(COMMON_TOOLS))
        v.addWidget(self._section_title("🛰 信息工具", "归入「信息工具」页签，点击直达对应 tab"))
        v.addLayout(self._build_tool_grid(INFO_TOOLS))
        v.addStretch(1)

        self._apply_theme_styles()

    # ==================== UI 构建 ====================

    def _build_hero(self):
        card = QFrame()
        card.setObjectName("heroCard")
        h = QHBoxLayout(card)
        h.setContentsMargins(20, 16, 18, 16)
        h.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(4)
        title = QLabel("🏠 数据汇总库 · 工具箱")
        title.setObjectName("heroTitle")
        left.addWidget(title)
        sub = QLabel("常用入口、数据概览与最近采集趋势")
        sub.setObjectName("heroSub")
        left.addWidget(sub)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.date_label = QLabel("")
        self.date_label.setObjectName("heroDate")
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right.addWidget(self.date_label)
        self.refresh_btn = QPushButton("🔄 刷新数据")
        self.refresh_btn.setObjectName("refreshBtn")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._refresh)
        right.addWidget(self.refresh_btn, 0, Qt.AlignRight)
        h.addLayout(right)
        return card

    def _build_stats(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        cards = [
            ("total", "已获取条目", "#2e7d32"),
            ("platforms", "覆盖平台", "#1976d2"),
            ("week", "近 7 天新增", "#7b1fa2"),
            ("today", "今日新增", "#ef6c00"),
        ]
        for key, caption, accent in cards:
            card = QFrame()
            card.setObjectName("statCard")
            card.setAttribute(Qt.WA_StyledBackground, True)
            h = QHBoxLayout(card)
            h.setContentsMargins(12, 12, 14, 12)
            h.setSpacing(10)
            bar = QFrame()
            bar.setFixedWidth(3)
            bar.setStyleSheet(f"background: {accent}; border-radius: 1px;")
            h.addWidget(bar)
            box = QVBoxLayout()
            box.setSpacing(1)
            value = QLabel("—")
            value.setObjectName("statValue")
            cap = QLabel(caption)
            cap.setObjectName("statCap")
            box.addWidget(value)
            box.addWidget(cap)
            h.addLayout(box, 1)
            self._stat_values[key] = value
            row.addWidget(card, 1)
        return row

    def _build_charts(self):
        row = QHBoxLayout()
        row.setSpacing(12)

        donut_card, donut_box = self._panel("📊 平台分布", "已获取条目按平台占比")
        self.donut = _DonutChart()
        donut_box.addWidget(self.donut, 1)
        row.addWidget(donut_card, 1)

        trend_card, trend_box = self._panel("📈 近 7 天新增", "按入库日期统计")
        self.trend = _TrendChart()
        trend_box.addWidget(self.trend, 1)
        row.addWidget(trend_card, 1)
        return row

    @staticmethod
    def _panel(title, subtitle):
        card = QFrame()
        card.setObjectName("panelCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 13, 16, 12)
        box.setSpacing(2)
        name = QLabel(title)
        name.setObjectName("panelTitle")
        box.addWidget(name)
        sub = QLabel(subtitle)
        sub.setObjectName("panelSub")
        box.addWidget(sub)
        return card, box

    @staticmethod
    def _section_title(title, subtitle):
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(2, 4, 2, 0)
        h.setSpacing(8)
        name = QLabel(title)
        name.setObjectName("sectionTitle")
        h.addWidget(name)
        sub = QLabel(subtitle)
        sub.setObjectName("sectionSub")
        h.addWidget(sub)
        h.addStretch(1)
        return wrap

    def _build_tool_grid(self, tools):
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for i, (key, emoji, title, desc) in enumerate(tools):
            card = _ToolCard(emoji, title, desc, lambda k=key: self._open_tool(k))
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        return grid

    # ==================== 数据刷新 ====================

    def _open_tool(self, key):
        """信息工具走 tab 容器并切到对应页签，其余页面直接跳转。"""
        if key in INFO_TOOL_KEYS:
            self.app.show_page("info_tools")
            page = self.app.pages.get("info_tools")
            if page is not None:
                page.open_tab(key)
        else:
            self.app.show_page(key)

    def on_show(self):
        self._refresh()

    def _refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.refresh_btn.setEnabled(False)
        run_bg(_load_dashboard_data, self._on_data)

    def _on_data(self, data, err):
        self._refreshing = False
        self.refresh_btn.setEnabled(True)
        if err or not data:
            return
        by_platform = data.get("by_platform", {})
        counts = data.get("counts") or []
        labels = data.get("labels") or []

        self._stat_values["total"].setText(f"{data.get('total', 0):,}")
        self._stat_values["platforms"].setText(
            str(len([n for n in by_platform.values() if n])))
        self._stat_values["week"].setText(f"{sum(counts):,}")
        self._stat_values["today"].setText(f"{counts[-1] if counts else 0:,}")

        self.donut.set_items(_dist_items(by_platform))
        self.trend.set_data(labels, counts)

    # ==================== 主题 ====================

    def on_theme_changed(self):
        self._apply_theme_styles()
        self.donut.update()
        self.trend.update()

    def _apply_theme_styles(self):
        dark = theme.is_dark()
        text = "#c6cbd6" if dark else "#2f3440"
        muted = "#888888" if dark else "#9aa0b0"
        card = "#23262f" if dark else "#ffffff"
        border = "#333340" if dark else "#e3e9f2"
        hover = "#2a3040" if dark else "#f5f9ff"
        accent = "#64b5f6" if dark else "#1976d2"
        hero = ("qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                "stop:0 #1f2433, stop:1 #232a3d)" if dark else
                "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                "stop:0 #eef6ff, stop:1 #f7f0ff)")
        self.setStyleSheet(f"""
            QFrame#heroCard {{
                background: {hero};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QLabel#heroTitle {{ color: {text}; background: transparent; font-size: 20px; font-weight: bold; }}
            QLabel#heroSub {{ color: {muted}; background: transparent; font-size: 12px; }}
            QLabel#heroDate {{ color: {muted}; background: transparent; font-size: 12px; }}
            QPushButton#refreshBtn {{
                background: {card}; color: {accent};
                border: 1px solid {border}; border-radius: 7px;
                padding: 5px 12px; font-size: 12px;
            }}
            QPushButton#refreshBtn:hover {{ background: {hover}; }}
            QFrame#statCard {{
                background: {card}; border: 1px solid {border}; border-radius: 10px;
            }}
            QLabel#statValue {{ color: {text}; background: transparent; font-size: 24px; font-weight: bold; }}
            QLabel#statCap {{ color: {muted}; background: transparent; font-size: 11px; }}
            QFrame#panelCard {{
                background: {card}; border: 1px solid {border}; border-radius: 10px;
            }}
            QLabel#panelTitle {{ color: {text}; background: transparent; font-size: 14px; font-weight: bold; }}
            QLabel#panelSub {{ color: {muted}; background: transparent; font-size: 11px; }}
            QLabel#sectionTitle {{ color: {text}; background: transparent; font-size: 15px; font-weight: bold; }}
            QLabel#sectionSub {{ color: {muted}; background: transparent; font-size: 11px; }}
            QFrame#toolCard {{
                background: {card}; border: 1px solid {border}; border-radius: 10px;
            }}
            QFrame#toolCard:hover {{ background: {hover}; border-color: {accent}; }}
            QLabel#toolIcon {{ background: transparent; font-size: 19px; }}
            QLabel#toolTitle {{ color: {text}; background: transparent; font-size: 13px; font-weight: bold; }}
            QLabel#toolDesc {{ color: {muted}; background: transparent; font-size: 11px; }}
        """)
        self.date_label.setText(
            datetime.now().strftime("%Y-%m-%d")
            + "  周" + "一二三四五六日"[datetime.now().weekday()])
