"""已获取页（PySide6 版）：分平台展示 COLLECTED，按栏全选/反选/清除、导出、下载。

对应原版 gui/info_pages.py 的 InfoResultsPage：
- 顶部：画质下拉（全局共享）+ 🗑 全部清空 + 📤 全部导出 + 📥 全部下载 + 导出格式复选
  + 微博评论页数（导出微博帖子时附带评论区聊天记录，0 = 不抓）
- 三栏（抖音/B站/微博）：计数标签 + 全选/反选/清除/📤 导出/📥 下载 + 复选行 + 🔗 打开链接
- 每行显示发布日期，默认按发布日期倒序（最新在前）
- 下载：B站按画质直下（视频走 playurl、DRAW 动态走图片），非 B站走 media_downloader
"""

import re
import webbrowser
from datetime import datetime

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPalette, QPainter, QPen, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidgetItem,
    QLayout, QMenu, QMessageBox, QPushButton, QListView, QSpinBox, QStyledItemDelegate, QStyle,
    QSizePolicy, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from gui_qt import common
from gui_qt.common import _fmt, post_to_main, run_bg
from gui_qt import download_ops
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList
from gui_qt.toolbox_common import ToolPageHeader

PLATFORMS_3 = [
    ("douyin", "📕 抖音"),
    ("bilibili", "📺 B站"),
    ("weibo", "📝 微博"),
    ("other", "📦 其他"),
]

# 「其他」栏收纳的平台（快手/小红书/好看/微视等）；未列出的平台也归入 other
_KNOWN_COLS = {key for key, _ in PLATFORMS_3}

_PUB_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")

# 视频/动态标签配色：常规态 + 选中态（选中卡片为浅蓝底，使用较深的文字）。
TAG_COLORS = {"[视频]": ("#1976d2", "#1258a6"), "[动态]": ("#e65100", "#9a3f00"),
              "[图文]": ("#2e7d32", "#216326")}
# 深色主题用亮色变体（选中卡片底也是暗色，选中与否同色即可）
TAG_COLORS_DARK = {"[视频]": "#64b5f6", "[动态]": "#ffb74d", "[图文]": "#81c784"}


def colorize_tag_html(text: str, selected: bool = False, dark: bool = False) -> str:
    """把行文本里的 [视频]/[动态]/[图文] 标签替换为彩色加粗 span，其余文本原样转义（换行转 <br>）。"""
    esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
           .replace("\n", "<br>"))
    for tag, (normal, sel) in TAG_COLORS.items():
        color = TAG_COLORS_DARK[tag] if dark else (sel if selected else normal)
        esc = esc.replace(tag, f'<span style="color:{color}; font-weight:bold;">{tag}</span>')
    return esc


class TagColorDelegate(QStyledItemDelegate):
    """列表行渲染：卡片框 + 浅底色（条目范围一目了然），类型标签彩色加粗，按栏宽自动换行。"""

    # 卡片视觉参数：外边距（行间距）/ 内边距 / 圆角 / 配色（常规 + 选中态）
    CARD_MARGIN_X = 3
    CARD_MARGIN_Y = 2
    CARD_PAD_X = 8
    CARD_PAD_Y = 5
    CARD_RADIUS = 6
    CARD_BG = QColor("#f3f6fa")
    CARD_BORDER = QColor("#d4dde7")
    CARD_BG_SEL = QColor("#d8e8ff")
    CARD_BORDER_SEL = QColor("#5b9bff")
    # 深色主题卡片配色：底色比列表底 #23262f 略亮、边框更亮；选中态暗蓝 tint + 亮蓝边
    CARD_BG_DARK = QColor("#2b3140")
    CARD_BORDER_DARK = QColor("#3d4658")
    CARD_BG_SEL_DARK = QColor("#2a3b52")
    CARD_BORDER_SEL_DARK = QColor("#5b9bff")

    @staticmethod
    def _is_dark(opt) -> bool:
        """按调色板文字亮度判主题：深色主题全局 QSS 把文字换成浅色。"""
        return opt.palette.color(QPalette.Text).lightness() > 128

    def _card_colors(self, dark: bool, selected: bool):
        if selected:
            return ((self.CARD_BG_SEL_DARK, self.CARD_BORDER_SEL_DARK) if dark
                    else (self.CARD_BG_SEL, self.CARD_BORDER_SEL))
        return ((self.CARD_BG_DARK, self.CARD_BORDER_DARK) if dark
                else (self.CARD_BG, self.CARD_BORDER))

    def _text_width(self, opt):
        """当前栏内一行文本的可用宽度（视口宽 - 复选框指示框区域 - 卡片内边距）。"""
        view = self.parent()
        if not isinstance(view, QWidget):
            return 260
        style = opt.widget.style() if opt.widget else QApplication.style()
        probe = QStyleOptionViewItem(opt)
        probe.rect = QRect(0, 0, view.viewport().width(), 20)
        rect = style.subElementRect(QStyle.SE_ItemViewItemText, probe, view)
        inner = (rect.width() - 2 * self.CARD_MARGIN_X - 2 * self.CARD_PAD_X - 2)
        return max(inner, 60)

    def _build_doc(self, text, opt, selected, width):
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        doc.setTextWidth(max(width, 60))
        # 卡片的选中底色为主题内的浅/深蓝 tint，正文仍使用该主题的常规文字色。
        # 不设的话 QTextDocument 默认黑字，深色主题下整行不可读。
        base = opt.palette.color(QPalette.Text).name()
        doc.setHtml(f'<span style="color:{base};">'
                    f'{colorize_tag_html(text, selected, self._is_dark(opt))}</span>')
        return doc

    def sizeHint(self, option, index):
        text = index.data(Qt.DisplayRole)
        if not isinstance(text, str) or "\n" not in text:
            return super().sizeHint(option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = self._build_doc(text, opt, False, self._text_width(opt))
        h = int(doc.size().height()) + 2 * self.CARD_PAD_Y + 2 * self.CARD_MARGIN_Y
        return QSize(max(self._text_width(opt), 60), h)

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole)
        if not isinstance(text, str) or ("\n" not in text and not any(t in text for t in TAG_COLORS)):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.State_Selected)
        style = opt.widget.style() if opt.widget else QApplication.style()

        # 1. 卡片底 + 边框（整条范围可视化；选中态蓝底亮边框；深色主题用暗色卡片）
        card = QRect(option.rect).adjusted(self.CARD_MARGIN_X, self.CARD_MARGIN_Y,
                                           -self.CARD_MARGIN_X, -self.CARD_MARGIN_Y)
        bg, border = self._card_colors(self._is_dark(opt), selected)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(bg)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(card, self.CARD_RADIUS, self.CARD_RADIUS)
        painter.restore()

        # 2. 复选框（画在卡片上，位置与原生一致——点击热区由原生位置决定）
        #    勾选态必须读 opt.checkState：initStyleOption 不会把它映射成 State_On
        #    状态位（那是 CE_ItemViewItem 内部做的，此处手绘指示框需自行映射）
        opt_chk = QStyleOptionViewItem(opt)
        opt_chk.rect = option.rect
        opt_chk.state &= ~QStyle.State_Selected
        chk_rect = style.subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt_chk, opt.widget)
        opt_chk.rect = chk_rect
        opt_chk.state &= ~(QStyle.State_On | QStyle.State_Off | QStyle.State_NoChange)
        if opt.checkState == Qt.Checked:
            opt_chk.state |= QStyle.State_On
        elif opt.checkState == Qt.PartiallyChecked:
            opt_chk.state |= QStyle.State_NoChange
        else:
            opt_chk.state |= QStyle.State_Off
        style.drawPrimitive(QStyle.PE_IndicatorItemViewItemCheck, opt_chk, painter, opt.widget)

        # 3. 富文本（类型标签彩色 + 自动换行），画在卡片内边距里
        #    文本起点必须让开复选框指示框（否则换行文本从卡片左缘起画，盖住勾选框）
        painter.save()
        text_left = max(card.left() + self.CARD_PAD_X, chk_rect.right() + 6)
        text_rect = QRect(text_left, card.top() + self.CARD_PAD_Y,
                          card.right() - text_left - self.CARD_PAD_X,
                          card.height() - 2 * self.CARD_PAD_Y)
        doc = self._build_doc(text, opt, selected, text_rect.width())
        painter.setClipRect(text_rect)
        painter.translate(text_rect.left(), text_rect.top())
        doc.drawContents(painter)
        painter.restore()


class _SingleItemScrollList(RowCheckList):
    """滚轮逐条滚动：一格（120）恰好滚一条。

    QScrollBar 对滚轮按 angleDelta/40 计步，ScrollPerItem 模式下一格默认跳 3 条，
    条目卡片较高时一次闪过好几条看不清。改为累计角度、满一格滚一条，
    高分辨率滚轮/触控板的小步事件自然累计，不满一格不滚。
    """

    _WHEEL_UNIT = 120

    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        if dy == 0:
            super().wheelEvent(event)
            return
        acc = getattr(self, "_wheel_acc", 0) + dy
        steps = 0
        while abs(acc) >= self._WHEEL_UNIT:
            acc -= self._WHEEL_UNIT if acc > 0 else -self._WHEEL_UNIT
            steps += 1
        self._wheel_acc = acc
        if steps:
            sign = 1 if dy > 0 else -1
            bar = self.verticalScrollBar()
            # 上滚（角度为正）值减、下滚值加；setValue 自动夹在范围内
            bar.setValue(bar.value() - sign * steps)
        event.accept()


def _parse_pub(info):
    """解析发布时间 → (datetime, 显示文本)。无法解析时返回 (datetime.min, '无日期')。"""
    raw = (getattr(info, "publish_time", "") or "").strip()
    if raw:
        for fmt in _PUB_FORMATS:
            try:
                dt = datetime.strptime(raw[:19] if " " in raw else raw[:10], fmt)
                return dt, raw[:16] if " " in raw else raw[:10]
            except ValueError:
                continue
        # 纯数字时间戳
        if raw.isdigit():
            try:
                ts = int(raw)
                if ts > 10 ** 12:  # 毫秒
                    ts //= 1000
                dt = datetime.fromtimestamp(ts)
                return dt, dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                pass
    return datetime.min, "无日期"


class InfoResultsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.cols = {key: {"songs": [], "list": None, "count_label": None}
                     for key, _ in PLATFORMS_3}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        # 旧四列的布局最小宽度不能先夹住 resize，否则无法切换到窄版两列。
        layout.setSizeConstraint(QLayout.SetNoConstraint)

        # 标题与操作分开，短窗口也能完整显示所有控件。
        head = QHBoxLayout()
        head.addWidget(ToolPageHeader("已获取", "按平台整理作品与动态，最新发布的内容排在前面。"), 1)
        self.export_status = QLabel("")
        self.export_status.setProperty("role", "caption")
        self.export_status.setWordWrap(True)
        self.export_status.setMaximumWidth(260)
        self.export_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.export_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.export_status)
        layout.addLayout(head)

        toolbar = QFrame()
        self._toolbar = toolbar
        toolbar.setProperty("variant", "toolbar")
        tools = QVBoxLayout(toolbar)
        tools.setContentsMargins(12, 10, 12, 10)
        tools.setSpacing(10)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        export_all_btn = QPushButton("📤 导出所有勾选")
        export_all_btn.setProperty("variant", "secondary")
        export_all_btn.setToolTip("导出全部平台栏内的勾选条目")
        export_all_btn.clicked.connect(self._export_all)
        actions.addWidget(export_all_btn)
        dl_all_btn = QPushButton("📥 下载所有勾选")
        dl_all_btn.setProperty("variant", "primary")
        dl_all_btn.setToolTip("下载全部平台栏内的勾选条目")
        dl_all_btn.clicked.connect(self._download_all)
        actions.addWidget(dl_all_btn)
        actions.addStretch(1)
        del_sel_btn = QPushButton("🗑 删除所有勾选")
        del_sel_btn.setProperty("variant", "danger")
        del_sel_btn.setToolTip("从已获取列表移除全部平台栏内的勾选条目（不影响已下载的本地文件）")
        del_sel_btn.clicked.connect(self._delete_selected)
        actions.addWidget(del_sel_btn)
        clear_btn = QPushButton("🗑 全部清空")
        clear_btn.setProperty("variant", "danger")
        clear_btn.clicked.connect(self._clear)
        actions.addWidget(clear_btn)
        tools.addLayout(actions)

        options = QHBoxLayout()
        options.setSpacing(6)
        options.addWidget(QLabel("画质"))
        self.quality_sel = QComboBox()
        self.quality_sel.addItems(
            ["最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"])
        self.quality_sel.setFixedWidth(102)
        self.quality_sel.setCurrentText(common.CURRENT_QUALITY)
        self.quality_sel.currentTextChanged.connect(self._on_quality_change)
        options.addWidget(self.quality_sel)
        options.addSpacing(6)
        options.addWidget(QLabel("线程"))
        self.thread_sel = QComboBox()
        self.thread_sel.addItems(common.THREAD_OPTIONS)
        self.thread_sel.setToolTip(
            "下载线程数：多个文件按线程数并行下载（进度面板显示各文件槽位）；\n"
            "单个大文件按线程数分段并行下载（与信息获取页共享设置）")
        self.thread_sel.setCurrentText(str(common.CURRENT_THREADS))
        self.thread_sel.setFixedWidth(64)
        self.thread_sel.currentTextChanged.connect(self._on_threads_change)
        options.addWidget(self.thread_sel)
        options.addSpacing(10)

        # 导出格式（对所有栏生效；勾选状态持久化）+ 微博评论页数（帖子导出带聊天记录）
        saved_fmts = set()
        saved_comment_pages = 2
        try:
            from toolbox.config_manager import load_convert_config
            cfg = load_convert_config()
            saved_fmts = set(cfg.get("export_formats", ["json", "xlsx", "md"]))
            try:
                saved_comment_pages = int(cfg.get("weibo_comment_pages", 2))
            except (TypeError, ValueError):
                saved_comment_pages = 2
        except Exception:
            saved_fmts = {"json", "xlsx", "md"}
        options.addWidget(QLabel("格式"))
        self.fmt_json = QCheckBox("JSON")
        self.fmt_json.setChecked("json" in saved_fmts)
        self.fmt_xlsx = QCheckBox("XLSX")
        self.fmt_xlsx.setChecked("xlsx" in saved_fmts)
        self.fmt_md = QCheckBox("MD")
        self.fmt_md.setChecked("md" in saved_fmts)
        for cb in (self.fmt_json, self.fmt_xlsx, self.fmt_md):
            cb.toggled.connect(lambda _c: self._save_fmt_settings())
            options.addWidget(cb)
        options.addSpacing(8)
        comment_label = QLabel("微博评论")
        comment_label.setToolTip("导出微博帖子时抓取的评论页数，0 表示不抓取")
        options.addWidget(comment_label)
        self.comment_pages_spin = QSpinBox()
        self.comment_pages_spin.setRange(0, 50)
        self.comment_pages_spin.setValue(max(0, min(50, saved_comment_pages)))
        self.comment_pages_spin.setFixedWidth(56)
        self.comment_pages_spin.setToolTip(
            "导出微博帖子时附带评论区聊天记录：每帖抓取前 N 页（约 N×20 条），0 = 不抓。\n"
            "评论存为帖子同目录的 _comments.json / _comments.txt，并写入 JSON/XLSX/MD。")
        self.comment_pages_spin.valueChanged.connect(lambda _v: self._save_comment_pages())
        options.addWidget(self.comment_pages_spin)
        options.addStretch(1)
        tools.addLayout(options)
        layout.addWidget(toolbar)

        # 宽窗口并排四栏；窄窗口为两行两列，移动原卡片保留勾选与滚动状态。
        self._columns_layout = QGridLayout()
        self._columns_layout.setSpacing(10)
        self._column_widgets = []
        self._columns_per_row = 0
        for key, name in PLATFORMS_3:
            self._column_widgets.append(self._build_column(key, name))
        layout.addLayout(self._columns_layout, 1)
        self._reflow_columns(self.width())

        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_columns(event.size().width())

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        if getattr(self, "_column_widgets", None):
            margins = self.layout().contentsMargins()
            # 向父级报告本页可支持的两列最小宽度，而非当前四列宽度。
            columns_width = 2 * max(card.minimumSizeHint().width() for card in self._column_widgets)
            columns_width += self._columns_layout.horizontalSpacing()
            hint.setWidth(max(self._toolbar.minimumSizeHint().width(), columns_width)
                          + margins.left() + margins.right())
        return hint

    def _reflow_columns(self, width):
        per_row = 4 if width >= 980 else 2
        if per_row == self._columns_per_row:
            return
        self._columns_per_row = per_row
        for card in self._column_widgets:
            self._columns_layout.removeWidget(card)
        for index, card in enumerate(self._column_widgets):
            self._columns_layout.addWidget(card, index // per_row, index % per_row)
        for col in range(4):
            self._columns_layout.setColumnStretch(col, int(col < per_row))
        for row in range(2):
            self._columns_layout.setRowStretch(row, int(row == 0 or per_row == 2))

    def _save_fmt_settings(self):
        """导出格式勾选状态写入 config（下次启动保持）。"""
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            fmts = []
            if self.fmt_json.isChecked():
                fmts.append("json")
            if self.fmt_xlsx.isChecked():
                fmts.append("xlsx")
            if self.fmt_md.isChecked():
                fmts.append("md")
            cfg["export_formats"] = fmts
            save_convert_config(cfg)
        except Exception:
            pass

    def _save_comment_pages(self):
        """微博评论页数写入 config（下次启动保持）。"""
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            cfg["weibo_comment_pages"] = self.comment_pages_spin.value()
            save_convert_config(cfg)
        except Exception:
            pass

    def _build_column(self, key, name):
        col = QFrame()
        col.setProperty("variant", "card")
        v = QVBoxLayout(col)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        label = QLabel(name)
        label.setProperty("role", "sectionTitle")
        head.addWidget(label)
        count = QLabel("0")
        count.setProperty("role", "caption")
        head.addStretch(1)
        head.addWidget(count)
        self.cols[key]["count_label"] = count
        v.addLayout(head)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        for text, mode in (("全选", "all"), ("反选", "invert"), ("清除", "none")):
            b = QPushButton(text)
            b.setFixedSize(42, 26)
            b.setProperty("variant", "ghost")
            b.setStyleSheet("padding: 0px; font-size: 11px;")
            b.clicked.connect(lambda _=False, k=key, m=mode: self._col_select(k, m))
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        transfer = QHBoxLayout()
        transfer.setSpacing(6)
        export_btn = QPushButton("📤 导出")
        export_btn.setProperty("variant", "secondary")
        export_btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
        export_btn.clicked.connect(lambda _=False, k=key: self._col_export(k))
        transfer.addWidget(export_btn)
        dl_btn = QPushButton("📥 下载")
        dl_btn.setProperty("variant", "secondary")
        dl_btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
        dl_btn.clicked.connect(lambda _=False, k=key: self._col_download(k))
        transfer.addWidget(dl_btn)
        v.addLayout(transfer)

        lst = _SingleItemScrollList()
        lst.setMinimumWidth(0)
        lst.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        # 卡片式条目：行间距隔开卡片；按栏宽自动换行，窗口缩放时重新布局重算行高
        lst.setSpacing(4)
        lst.setWordWrap(True)
        lst.setResizeMode(QListView.Adjust)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # [视频]/[动态]/[图文] 标签彩色渲染（其余文字默认样式）
        lst.setItemDelegate(TagColorDelegate(lst))
        lst.itemDoubleClicked.connect(
            lambda item: self._open_url(item.data(Qt.UserRole) or ""))
        # 右键条目：删除该条目（从已获取列表移除）
        lst.setContextMenuPolicy(Qt.CustomContextMenu)
        lst.customContextMenuRequested.connect(
            lambda pos, k=key: self._row_menu(k, pos))
        self.cols[key]["list"] = lst
        v.addWidget(lst, 1)
        return col

    # ==================== 渲染 ====================

    def refresh(self):
        # 刷新前记住勾选与滚动位置：采集过程中逐账号刷新也保留用户当前操作
        checked = {key: self._checked_keys(key) for key, _ in PLATFORMS_3}
        scrolls = {key: self.cols[key]["list"].verticalScrollBar().value()
                   for key, _ in PLATFORMS_3}
        # 按平台分到四栏（快手/小红书等归入「其他」），默认按发布日期倒序（最新在前）
        grouped = {key: [] for key, _ in PLATFORMS_3}
        for info in common.COLLECTED:
            plat = info.platform or "other"
            if plat not in _KNOWN_COLS:
                plat = "other"
            grouped[plat].append(info)
        for key, _name in PLATFORMS_3:
            # 本次刷新内复用日期解析结果，排序与行文本不再各解析一遍。
            pub_dates = {id(info): _parse_pub(info) for info in grouped[key]}
            grouped[key].sort(key=lambda info: pub_dates[id(info)], reverse=True)
            self._render_column(key, grouped[key], checked[key], pub_dates)
            self.cols[key]["list"].verticalScrollBar().setValue(scrolls[key])

    def _checked_keys(self, key):
        """当前栏已勾选条目的去重键（刷新后用于还原勾选）。"""
        result = set()
        col = self.cols[key]
        for row in range(col["list"].count()):
            item = col["list"].item(row)
            if item.checkState() == Qt.Checked and row < len(col["songs"]):
                k = common._dedup_key(col["songs"][row])
                if k:
                    result.add(k)
        return result

    @staticmethod
    def _openable_url(info) -> str:
        """条目可打开链接：优先真实作品页（raw_url）；存量数据里时间线
        采集的主页链接（抖音 /user、微博裸 uid、B站 space）回退构造作品页。"""
        ru = (getattr(info, "raw_url", "") or "").strip()
        plat = (getattr(info, "platform", "") or "").strip()
        vid = str(getattr(info, "video_id", "") or "").strip()
        if plat == "douyin" and "/user/" in ru and vid:
            is_note = getattr(info, "kind", "") == "image" or (
                getattr(info, "images", None) and not info.video_url)
            return f"https://www.douyin.com/note/{vid}" if is_note \
                else f"https://www.douyin.com/video/{vid}"
        if plat == "weibo" and vid and re.fullmatch(r"https?://weibo\.com/u?\d+/?", ru):
            m = re.search(r"weibo\.com/u?(\d+)", ru)
            if m:
                return f"https://weibo.com/{m.group(1)}/{vid}"
        if plat == "bilibili" and vid and "space.bilibili.com" in ru:
            return f"https://www.bilibili.com/video/{vid}"
        return ru or (getattr(info, "video_url", "") or "")

    def _render_column(self, key, songs, checked_keys=None, pub_dates=None):
        col = self.cols[key]
        lst = col["list"]
        checked_keys = checked_keys or set()
        lst.clear()
        col["songs"] = songs
        col["count_label"].setText(f"{len(songs)} 条")
        if not songs:
            item = QListWidgetItem("（无数据）")
            item.setFlags(Qt.NoItemFlags)
            lst.addItem(item)
            return
        for i, info in enumerate(songs, 1):
            title = info.title or "-"
            author = info.author or "-"
            if getattr(info, "kind", "") == "dynamic":
                kind_tag = "动态"
            elif getattr(info, "kind", "") in ("image", "note") or (
                    getattr(info, "images", None) and not info.video_url):
                kind_tag = "图文"
            else:
                kind_tag = "视频"
            _dt, date_str = (pub_dates[id(info)] if pub_dates is not None
                             else _parse_pub(info))
            # 两行展示：标题一行（不截断），日期/作者/数据一行；超宽自动换行
            meta = f"📅 {date_str}  @{author}  播放{_fmt(info.play_count)} 赞{_fmt(info.like_count)}"
            url = self._openable_url(info)
            if url:
                meta += "  🔗"
            text = f"[{i}] [{kind_tag}] {title}\n{meta}"
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(
                Qt.Checked if common._dedup_key(info) in checked_keys else Qt.Unchecked)
            item.setData(Qt.UserRole, url or "")
            item.setToolTip(f"{title}\n{author}  {date_str}\n{url or ''}")
            lst.addItem(item)

    def refresh_count(self):
        # 账号采集每完成一个账号都会通知一次：正在看本页时立即刷新，
        # 不在本页则等切回时由 on_show 刷新，避免隐藏页反复重建列表
        if self.isVisible():
            self.refresh()

    def on_show(self):
        self.refresh()

    def _selected(self, key):
        col = self.cols[key]
        result = []
        for row in range(col["list"].count()):
            item = col["list"].item(row)
            if item.checkState() == Qt.Checked and row < len(col["songs"]):
                result.append(col["songs"][row])
        return result

    def _col_select(self, key, mode):
        lst = self.cols[key]["list"]
        for row in range(lst.count()):
            item = lst.item(row)
            if not (item.flags() & Qt.ItemIsUserCheckable):
                continue
            if mode == "all":
                item.setCheckState(Qt.Checked)
            elif mode == "none":
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    # ==================== 操作 ====================

    def _open_url(self, url):
        """双击行（行尾 🔗）打开对应链接。"""
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            self.app.toast(f"打开失败: {str(e)[:60]}", "error")

    def _get_formats(self):
        fmts = []
        if self.fmt_json.isChecked():
            fmts.append("json")
        if self.fmt_xlsx.isChecked():
            fmts.append("xlsx")
        if self.fmt_md.isChecked():
            fmts.append("md")
        return fmts

    def _all_selected(self):
        """聚合三栏内全部勾选条目。"""
        out = []
        for key, _name in PLATFORMS_3:
            out.extend(self._selected(key))
        return out

    def _export_items(self, items, warn_empty="暂无勾选条目"):
        if not items:
            self.app.toast(warn_empty, "warning")
            return
        fmts = self._get_formats()
        if not fmts:
            self.app.toast("请至少选择一种导出格式", "warning")
            return
        self.export_status.setText("⏳ 正在导出…")

        def progress(msg):
            post_to_main(self.export_status.setText, f"⏳ {msg}")

        def work():
            from exporters import export_per_video_flat
            from toolbox.config_manager import get_export_subdir
            import os
            out_dir = str(get_export_subdir("video_data"))
            pages = self.comment_pages_spin.value()
            paths, skipped = export_per_video_flat(items, output_dir=out_dir,
                                                   formats=fmts, skip_existing=True,
                                                   comments_pages=pages,
                                                   progress=progress)
            return paths, skipped, os.path.abspath(out_dir)

        def done(result, err):
            if err:
                self.export_status.setText(f"❌ {str(err)[:60]}")
                self.app.toast(str(err), "error")
                return
            paths, skipped, out_dir = result
            if skipped and not paths:
                self.export_status.setText("已存在，已跳过")
                self.app.toast("文件已存在，请勿重复保存", "info")
                return
            msg = f"已导出 {len(paths)} 个文件到 {out_dir}"
            if skipped:
                msg += f"（跳过 {len(skipped)} 个已存在）"
            self.export_status.setText(
                f"✅ 已导出 {len(paths)}" + (f"（跳过 {len(skipped)}）" if skipped else ""))
            self.app.toast(msg, "success")

        run_bg(work, done)

    def _col_export(self, key):
        self._export_items(self._selected(key), "该栏暂无勾选条目")

    def _export_all(self):
        """三平台一次导出：聚合三栏勾选项统一导出。"""
        self._export_items(self._all_selected(), "三栏均无勾选条目，请先勾选要导出的内容")

    def _col_download(self, key):
        items = self._selected(key)
        if not items:
            self.app.toast("该栏暂无勾选条目", "warning")
            return
        self._download_items(items)

    def _download_all(self):
        """三平台一次下载：聚合三栏勾选项统一下载。"""
        items = self._all_selected()
        if not items:
            self.app.toast("三栏均无勾选条目，请先勾选要下载的内容", "warning")
            return
        self._download_items(items)

    def _on_quality_change(self, value):
        """清晰度改变时同步到全局（供资产管理/视频下载页复用）。"""
        common.CURRENT_QUALITY = value

    def _on_threads_change(self, value):
        """线程数改变时同步到全局（账号获取页共用）。"""
        try:
            common.CURRENT_THREADS = int(value)
        except ValueError:
            pass

    def _threads(self) -> int:
        try:
            return int(self.thread_sel.currentText())
        except ValueError:
            return common.CURRENT_THREADS

    def _download_items(self, items):
        """按条目下载（不弹任何二级窗）：B站图文动态直接下图片，视频按 head 画质下拉下载；
        多条目按线程数并行，进度走全局下载浮窗。"""
        if not items:
            return
        quality = self.quality_sel.currentText()
        threads = self._threads()
        # 进度走全局下载浮窗（原页面内嵌面板不再占用页面 UI）
        from gui_qt.download_float import get_float
        card = get_float().add_batch("已获取")

        def work():
            return download_ops.download_items_mixed(
                items, quality_label=quality, threads=threads,
                on_task_event=card.handle_event)

        def done(result, err):
            if err:
                card.finish(f"❌ 下载失败: {str(err)[:60]}")
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
                return
            msg = result or "无内容下载"
            card.finish(msg if len(msg) <= 80 else msg[:79] + "…")
            self.app.toast(msg, "success")

        run_bg(work, done)

    def _clear(self):
        if not common.COLLECTED:
            return
        common.COLLECTED.clear()
        common.save_collected()
        self.refresh()

    # ==================== 删除条目 ====================

    def _row_menu(self, key, pos):
        """右键条目菜单：打开/复制作品链接、删除该条目（从已获取列表移除，不动本地文件）。"""
        lst = self.cols[key]["list"]
        row = lst.indexAt(pos).row()
        if row < 0 or row >= len(self.cols[key]["songs"]):
            return
        info = self.cols[key]["songs"][row]
        url = self._openable_url(info)
        menu = QMenu(self)
        act_open = act_copy = None
        if url:
            act_open = menu.addAction("🔗 打开链接")
            act_copy = menu.addAction("📋 复制链接")
            menu.addSeparator()
        act = menu.addAction("🗑 删除该条目…")
        chosen = menu.exec(lst.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._open_url(url)
        elif chosen is act_copy:
            QApplication.clipboard().setText(url)
            self.app.toast("链接已复制", "success")
        elif chosen is act:
            self._delete_items([info])

    def _delete_selected(self):
        """删除三栏内全部勾选条目。"""
        items = self._all_selected()
        if not items:
            self.app.toast("三栏均无勾选条目，请先勾选要删除的内容", "warning")
            return
        self._delete_items(items)

    def _delete_items(self, items):
        if not items:
            return
        if QMessageBox.question(
                self, "确认删除",
                f"从已获取列表删除 {len(items)} 个条目（不影响已下载的本地文件）：\n\n"
                + "\n".join((i.title or "-")[:40] for i in items[:8])
                + ("\n…等" if len(items) > 8 else ""),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        ids = {id(i) for i in items}
        common.COLLECTED[:] = [i for i in common.COLLECTED if id(i) not in ids]
        common.save_collected()
        self.refresh()
        self.app.toast(f"已删除 {len(items)} 个条目", "success")
