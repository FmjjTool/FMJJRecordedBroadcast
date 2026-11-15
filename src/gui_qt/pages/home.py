"""首页：数据概览 + 常用工具入口。

- 默认启动页；点击侧栏顶部标题可随时回到本页
- 指标卡：已获取总数 / 覆盖平台 / 近 7 天新增 / 今日新增
- 图表：平台分布环形图 + 近 7 天新增柱状图（QPainter 自绘，跟随深浅主题）
- 常用工具：原侧栏中的信息类页面迁到本页，避免信息分组过长
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import run_bg
from gui_qt.navigation import line_icon, tool_icon_name
from gui_qt.pages.base import BasePage

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
}
OTHER_COLOR = "#8b93a7"

# 账号观察抽屉：平台显示名（网媒 + 音乐）
ACCOUNT_PLATFORM_LABELS = {
    "bilibili": "B站", "douyin": "抖音", "weibo": "微博",
    "kuaishou": "快手", "xiaohongshu": "小红书",
    "netease": "网易云", "qq": "QQ音乐", "kugou": "酷狗",
    "kuwo": "酷我", "fivesing": "5sing",
}
ACCOUNT_DRAWER_KEYS = {"tracked_accounts"}
TCDOC_DRAWER_KEYS = {"tcdoc_rows", "doc_views", "doc_members", "doc_readers"}

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
    ("localwiki", "🌐", "本地资料站", "本地 Wiki + 腾讯文档预览"),
]
INFO_TOOL_KEYS = {key for key, *_ in INFO_TOOLS}


def _load_dashboard_data():
    """汇总全功能看板数据：平台内容 / 微博超话 / 腾讯文档 / 资源文件 / 追踪账号。

    - 平台内容：config/increment.db 各平台表
    - 微博超话：config/weibo_chaohua.db posts（按帖子 ID 去重后的累计）
    - 腾讯文档：download/腾讯文档数据/tables.db 行数 + tcdoc.db 最新浏览量/成员数
    - 资源文件：config/asset_index.db asset_json
    - 追踪账号：download/账号追踪数据、音乐账号数据下的 xlsx 数量
    """
    from toolbox.config_manager import get_config_dir, get_export_subdir

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=6)
    labels = [(start + timedelta(days=i)).strftime("%m-%d") for i in range(7)]
    start_str = start.strftime("%Y-%m-%d 00:00:00")

    data = {
        "stats": {
            "platform_items": 0,
            "chaohua_posts": 0,
            "tcdoc_rows": 0,
            "asset_files": 0,
            "doc_views": 0,
            "doc_members": 0,
            "doc_readers": 0,
            "doc_count": 0,
            "tracked_accounts": 0,
            "tracked_total": 0,
        },
        "by_source": {},
        "labels": labels,
        "counts": [0] * 7,
        "week_total": 0,
        "today_total": 0,
    }

    def connect_ro(path):
        """只读连接；库不存在或打不开时返回 None，看板其余部分照常展示。"""
        try:
            if not path.exists():
                return None
            return sqlite3.connect(
                f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
        except Exception:
            return None

    def add_daily(day, count):
        try:
            index = (datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
                     - start.date()).days
        except (TypeError, ValueError):
            return
        if 0 <= index < 7:
            data["counts"][index] += int(count or 0)

    def add_daily_rows(rows):
        for day, count in rows:
            add_daily(day, count)

    # ---- 平台内容（increment.db 各平台表）----
    conn = connect_ro(get_config_dir() / "increment.db")
    if conn is not None:
        try:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for table in tables:
                if table.startswith("_") or table.startswith("sqlite_"):
                    continue
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    data["stats"]["platform_items"] += int(count)
                    data["by_source"][table] = int(count)
                    add_daily_rows(conn.execute(
                        f'SELECT date(fetched_at) AS d, COUNT(*) FROM "{table}"'
                        " WHERE fetched_at >= ? GROUP BY d", (start_str,)).fetchall())
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 微博超话（去重帖子 + 首次入库日期）----
    conn = connect_ro(get_config_dir() / "weibo_chaohua.db")
    if conn is not None:
        try:
            count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            data["stats"]["chaohua_posts"] = int(count)
            data["by_source"]["weibo_chaohua"] = int(count)
            add_daily_rows(conn.execute(
                "SELECT date(first_seen, 'unixepoch', 'localtime') AS d, COUNT(*)"
                " FROM posts WHERE first_seen >= ? GROUP BY d",
                (start.timestamp(),)).fetchall())
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 腾讯文档（表格行数 + 最新浏览量/成员数/已查看人数）----
    tcdoc_dir = get_export_subdir("tcdoc")
    conn = connect_ro(tcdoc_dir / "tables.db")
    if conn is not None:
        try:
            count = conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
            data["stats"]["tcdoc_rows"] = int(count)
            data["by_source"]["tcdoc"] = int(count)
            add_daily_rows(conn.execute(
                "SELECT date(ts) AS d, COUNT(*) FROM changes"
                " WHERE kind='新增' AND ts >= ? GROUP BY d", (start_str,)).fetchall())
        except Exception:
            pass
        finally:
            conn.close()

    conn = connect_ro(tcdoc_dir / "tcdoc.db")
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT views, members, readers FROM daily d WHERE id = ("
                " SELECT id FROM daily x WHERE x.doc_key = d.doc_key"
                " ORDER BY ts DESC, id DESC LIMIT 1)").fetchall()
            data["stats"]["doc_count"] = len(rows)
            data["stats"]["doc_views"] = sum(int(row[0] or 0) for row in rows)
            data["stats"]["doc_members"] = sum(int(row[1] or 0) for row in rows)
            data["stats"]["doc_readers"] = sum(int(row[2] or 0) for row in rows)
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 资源文件（asset_index 登记的本地资源）----
    conn = connect_ro(get_config_dir() / "asset_index.db")
    if conn is not None:
        try:
            count = int(conn.execute(
                "SELECT COUNT(*) FROM asset_json").fetchone()[0])
            data["stats"]["asset_files"] = count
            data["by_source"]["assets"] = count
            add_daily_rows(conn.execute(
                "SELECT date(CAST(mtime AS REAL), 'unixepoch', 'localtime') AS d,"
                " COUNT(*) FROM asset_json WHERE CAST(mtime AS REAL) >= ? GROUP BY d",
                (start.timestamp(),)).fetchall())
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 追踪账号（按用户配置的观察范围统计）----
    accounts = _tracked_accounts_snapshot()
    if accounts:
        watched = _watched_account_keys()
        selected = (set(a["key"] for a in accounts)
                    if watched is None else watched & set(a["key"] for a in accounts))
        data["stats"]["tracked_accounts"] = len(selected)
        data["stats"]["tracked_total"] = len(accounts)
    else:
        # 账号配置不可读时回退到已下载的账号 xlsx 数量
        for category in ("tracker", "music_tracker"):
            try:
                folder = get_export_subdir(category)
                data["stats"]["tracked_accounts"] += sum(
                    1 for path in folder.glob("*.xlsx") if not path.name.startswith("~$"))
            except Exception:
                continue
        data["stats"]["tracked_total"] = data["stats"]["tracked_accounts"]

    data["week_total"] = sum(data["counts"])
    data["today_total"] = data["counts"][-1] if data["counts"] else 0
    return data


def _tracked_accounts_snapshot():
    """读取网媒 + 音乐账号配置，生成抽屉/看板共用的账号快照。"""
    accounts = []
    sources = []
    try:
        from tracker.accounts import load_accounts as load_media
        sources.append(("tracker", "网媒", load_media))
    except Exception:
        pass
    try:
        from music_account.accounts import load_accounts as load_music
        sources.append(("music", "音乐", load_music))
    except Exception:
        pass
    for source, source_label, loader in sources:
        try:
            items = loader() or []
        except Exception:
            continue
        for item in items:
            name = str(item.get("name") or "").strip() or "未命名账号"
            platform = str(item.get("platform") or "")
            url = str(item.get("url") or "")
            accounts.append({
                "key": f"{source}|{platform}|{url or name}",
                "name": name,
                "platform": platform,
                "platform_label": ACCOUNT_PLATFORM_LABELS.get(platform, platform or "未知"),
                "source": source,
                "source_label": source_label,
                "url": url,
            })
    return accounts


def _load_account_history(account):
    """读取单个账号最新两次采集记录（最新在前）。"""
    if account["source"] == "tracker":
        from tracker.accounts import load_history
    else:
        from music_account.accounts import load_history
    return (load_history(account["name"]) or [])[:2]


def _watched_account_keys():
    """已保存的观察账号 key 集合；None 表示尚未配置（默认全部观察）。"""
    try:
        from toolbox.config_manager import load_convert_config
        raw = load_convert_config().get("home_watch_accounts")
        if isinstance(raw, list):
            return {str(item) for item in raw}
    except Exception:
        pass
    return None


def _tcdoc_docs():
    """腾讯文档清单；配置缺失时回退到 tcdoc.db 中已采集过的 doc_key。"""
    try:
        from toolbox.tcdoc.config import load_docs
        docs = load_docs()
        if docs:
            return docs
    except Exception:
        pass
    docs = []
    try:
        from toolbox.config_manager import get_export_subdir
        path = get_export_subdir("tcdoc") / "tcdoc.db"
        if path.exists():
            conn = sqlite3.connect(
                f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
            try:
                docs = [{"key": row[0], "name": row[0]}
                        for row in conn.execute(
                            "SELECT DISTINCT doc_key FROM daily ORDER BY doc_key")]
            finally:
                conn.close()
    except Exception:
        pass
    return docs


def _load_tcdoc_history(doc_key):
    """读取单文档的历史记录，并统计每天修订记录次数。"""
    from toolbox.config_manager import get_export_subdir
    base = get_export_subdir("tcdoc")
    rows = []
    revisions = {}
    total = 0
    path = base / "tcdoc.db"
    if path.exists():
        conn = sqlite3.connect(
            f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
        try:
            total = int(conn.execute(
                "SELECT COUNT(*) FROM daily WHERE doc_key = ?", (doc_key,)).fetchone()[0])
            rows = [dict(zip(
                ("ts", "date", "views", "views_delta", "members",
                 "members_delta", "readers"), row))
                for row in conn.execute(
                    "SELECT ts, date, views, views_delta, members, members_delta,"
                    " readers FROM daily WHERE doc_key = ?"
                    " ORDER BY ts DESC, id DESC LIMIT 5", (doc_key,))]
        finally:
            conn.close()
    rev_path = base / "revisions.db"
    if rev_path.exists():
        conn = sqlite3.connect(
            f"file:{str(rev_path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
        try:
            revisions = {str(day): int(count) for day, count in conn.execute(
                "SELECT date(ts_ms / 1000, 'unixepoch', 'localtime') AS d, COUNT(*)"
                " FROM revision_rows WHERE doc_key = ? GROUP BY d", (doc_key,))}
        finally:
            conn.close()
    return {"rows": rows, "revisions": revisions, "total": total}


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
        save_btn = QPushButton("💾 保存")
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
    """可点击的统计卡：点击后在首页展开二级抽屉。"""

    clicked = Signal(str)

    def __init__(self, key, tip="", clickable=False, parent=None):
        super().__init__(parent)
        self._key = key
        self._clickable = clickable
        self.setObjectName("workbenchStatCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("clickable", "true" if clickable else "false")
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.setToolTip(tip)

    def mouseReleaseEvent(self, event):
        if (self._clickable and event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit(self._key)
        super().mouseReleaseEvent(event)


class HomePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._stat_values = {}
        self._refreshing = False
        self._tool_grids = []
        self._last_layout = None
        self._drawer_mode = ""
        self._account_seq = 0

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
        v.setContentsMargins(26, 22, 26, 28)
        v.setSpacing(16)

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
        h.setContentsMargins(0, 0, 0, 4)
        h.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        title = QLabel("工作台")
        title.setObjectName("workbenchHeroTitle")
        left.addWidget(title)
        sub = QLabel("内容采集、录播处理与资料归档，一处完成。")
        sub.setObjectName("workbenchHomeMuted")
        sub.setWordWrap(True)
        left.addWidget(sub)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.date_label = QLabel("")
        self.date_label.setObjectName("workbenchHomeMuted")
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right.addWidget(self.date_label)
        self.refresh_btn = QPushButton("刷新数据")
        self.refresh_btn.setObjectName("workbenchRefresh")
        self.refresh_btn.setIconSize(QSize(15, 15))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._refresh)
        right.addWidget(self.refresh_btn, 0, Qt.AlignRight)
        h.addLayout(right)
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
            ("doc_views", "文档浏览量", "腾讯文档监控各文档最新浏览量合计"),
            ("doc_members", "文档成员数", "腾讯文档监控各文档最新成员数合计"),
            ("doc_readers", "已查看人数", "腾讯文档监控各文档最新已查看人数合计"),
            ("tracked_accounts", "追踪账号", "网媒账号 + 音乐账号追踪的账号数量"),
        ]
        for index, (key, caption, tip) in enumerate(cards):
            clickable = key in ACCOUNT_DRAWER_KEYS or key in TCDOC_DRAWER_KEYS
            if clickable:
                tip = f"{tip}\n点击展开详情抽屉"
            card = _StatCard(key, tip, clickable=clickable)
            if clickable:
                card.clicked.connect(self._open_drawer)
            box = QVBoxLayout(card)
            box.setContentsMargins(18, 13, 18, 13)
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
        frame.setMinimumHeight(360)
        frame.setMaximumHeight(430)
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
        self.drawer_stack.addWidget(self.account_drawer_page)
        self.drawer_stack.addWidget(self.tcdoc_drawer_page)
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
        config_btn = QPushButton("⚙ 配置观察账号")
        config_btn.clicked.connect(self._open_account_config)
        bar.addWidget(config_btn)
        media_btn = QPushButton("网媒账号追踪")
        media_btn.clicked.connect(lambda: self.app.show_page("tracker"))
        bar.addWidget(media_btn)
        music_btn = QPushButton("音乐账号追踪")
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
        v.addWidget(self.account_table, 1)

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
        v.addWidget(self.tcdoc_table, 1)
        return page

    def _open_drawer(self, key):
        if key in ACCOUNT_DRAWER_KEYS:
            mode = "accounts"
        elif key in TCDOC_DRAWER_KEYS:
            mode = "tcdoc"
        else:
            return
        # 同一张卡片再点一次 = 收起；切换到另一张卡片 = 直接换抽屉内容
        if self.drawer.isVisible() and self._drawer_mode == mode:
            self._close_drawer()
            return
        self._drawer_mode = mode
        if mode == "accounts":
            self.drawer_title.setText("追踪账号")
            self.drawer_sub.setText(
                "网媒 + 音乐账号 · 勾选观察范围并查看最新数据（再次点卡片收起）")
            self.drawer_stack.setCurrentWidget(self.account_drawer_page)
            self._refresh_account_drawer()
        else:
            self.drawer_title.setText("腾讯文档 · 成员名单历史")
            self.drawer_sub.setText(
                "浏览量 / 增量 / 成员数 / 增量 / 已查看人数 / 修订记录次数"
                "（再次点卡片收起）")
            self.drawer_stack.setCurrentWidget(self.tcdoc_drawer_page)
            self._refresh_tcdoc_docs()
        self.drawer.show()
        self.scroll.ensureWidgetVisible(self.drawer, 0, 16)

    def _close_drawer(self):
        self.drawer.hide()
        self._drawer_mode = ""

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
            return
        if not display:
            self.account_status.setText(
                "当前未勾选观察账号，点「⚙ 配置观察账号」选择")
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

        def work():
            result = {}
            for account in display:
                try:
                    result[account["key"]] = _load_account_history(account)
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
            self.account_status.setText(
                f"已观察 {len(selected)} / 共 {len(accounts)} 个账号{limit_note}")

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

    def _refresh_tcdoc_docs(self):
        current = self.tcdoc_doc_combo.currentData()
        docs = _tcdoc_docs()
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
            return
        try:
            result = _load_tcdoc_history(doc_key)
        except Exception as exc:
            self.tcdoc_table.setRowCount(0)
            self.tcdoc_status.setText(f"读取失败：{exc}")
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
        if rows:
            latest = rows[0]
            total = int(result.get("total") or len(rows))
            self.tcdoc_status.setText(
                f"展示最近 {len(rows)} 条 / 共 {total} 条 ｜ "
                f"最新 浏览量 {self._num_text(latest.get('views'))}"
                f" / 成员 {self._num_text(latest.get('members'))}"
                f" / 已查看 {self._num_text(latest.get('readers'))}")
        else:
            self.tcdoc_status.setText("暂无历史记录，请先到「腾讯文档监控」采集")

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
        for i, (key, _emoji, title, desc) in enumerate(tools):
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
        self._arrange_grid(self._charts_grid, self._chart_cards, charts)
        self.body.layout().setContentsMargins(18 if narrow else 26, 22,
                                             18 if narrow else 26, 28)

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
        run_bg(_load_dashboard_data, self._on_data)

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
            QLabel#workbenchHeroTitle { color: palette(text); background: transparent; font-size: 27px; font-weight: bold; }
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
