"""抖音指数页：搜索达人 → 全量抓取（作者分析/作品分析/粉丝分析）→ 本地保存 JSON。

- 搜索：输入达人名称或抖音号 → 候选列表确认（两步确认，不直接抓）
- 抓取：核心指标（昨日/近7天/近15天/近30天）、稿均指标、每日趋势、
        达人视频（点赞/评论/分享/收藏/视频指数）、粉丝画像（地域/年龄/性别/手机/兴趣 + TGI）、
        相似达人
- 保存：完整指数快照；按视频 ID 回写已有资产的互动指标与指数来源信息。
"""

import os

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt import table_sort, theme
from gui_qt.common import post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from toolbox import douyin_index_service as dxi

# 核心指标行：(显示名, 数据段, 计数字段前缀)
_CORE_ROWS = [
    ("新增点赞量", "like_info", "new_like_count_by_"),
    ("净增粉丝量", "fans_info", "new_fans_count_by_"),
    ("新增分享量", "share_info", "new_share_count_by_"),
    ("新增评论量", "comment_info", "new_comment_count_by_"),
    ("新增作品量", "item_info", "new_item_count_by_"),
]
# 时间窗：(键后缀, 列名, 环比列名)
_WINDOWS = [
    ("day", "昨日", "日环比"),
    ("week", "近7天", "环比前7天"),
    ("half_month", "近15天", "环比前15天"),
    ("month", "近30天", "环比前30天"),
]
# 稿均指标行：(显示名, 数据段, 字段前缀)
_AVG_ROWS = [
    ("稿均点赞量", "like_info", "like_average_count_by_"),
    ("稿均涨粉量", "fans_info", "fans_average_count_by_"),
    ("稿均分享量", "share_info", "share_average_count_by_"),
    ("稿均评论量", "comment_info", "comment_average_count_by_"),
]


def _center(text):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(Qt.AlignCenter)
    return it


def _fmt_day(v) -> str:
    """YYYYMMDD → YYYY-MM-DD；其他原样返回。"""
    s = str(v or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def _num_tip(it, raw):
    it.setToolTip(str(raw))
    return it


def _separator(vertical: bool = False) -> QFrame:
    """卡片内的分组线：只用主题边框色，不写死 hex。"""
    line = QFrame()
    line.setStyleSheet("background: palette(shadow); border: none;")
    if vertical:
        line.setFixedWidth(1)
        line.setFixedHeight(20)
    else:
        line.setFixedHeight(1)
    return line


class _StackedBody(QWidget):
    """内容区 + 空数据引导：没有数据时只显示一条引导文案，不留大片空表。"""

    def __init__(self, content: QWidget, title: str, hint: str, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.stack = QStackedWidget(self)
        box.addWidget(self.stack)

        empty = QWidget(self)
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(12, 24, 12, 24)
        ev.setSpacing(4)
        empty_title = QLabel(title)
        empty_title.setAlignment(Qt.AlignCenter)
        empty_title.setWordWrap(True)
        empty_hint = QLabel(hint)
        empty_hint.setProperty("role", "caption")
        empty_hint.setAlignment(Qt.AlignCenter)
        empty_hint.setWordWrap(True)
        ev.addStretch(1)
        ev.addWidget(empty_title)
        ev.addWidget(empty_hint)
        ev.addStretch(1)

        self.stack.addWidget(empty)
        self.stack.addWidget(content)
        self.stack.setCurrentWidget(empty)

    def show_data(self, has_data: bool):
        self.stack.setCurrentIndex(1 if has_data else 0)


# 环比列下标：核心指标里每个时间窗「值 + 环比」两列，稿均指标为第 3、5 列
_EX_CORE_COLS = tuple(2 + i * 2 for i in range(len(_WINDOWS)))
_EX_AVG_COLS = (2, 4)


class _PickDialog(QDialog):
    """搜索结果两步确认：候选表 → 双击或「选择」填充当前账号。"""

    def __init__(self, parent, users):
        super().__init__(parent)
        self.setWindowTitle("选择达人")
        self.resize(720, 420)
        self.picked = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"找到 {len(users)} 个达人，双击或选择后点「确认」："))
        self.table = QTableWidget(len(users), 6)
        self.table.setHorizontalHeaderLabels(["达人", "抖音号", "粉丝", "总获赞", "作品", "标签"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        for i, u in enumerate(users):
            vals = [u.get("user_name"), u.get("aweme_id") or "-", dxi.fmt_count(u.get("follow_count")),
                    dxi.fmt_count(u.get("like_count")), u.get("item_count"),
                    f'{u.get("first_tag_name") or ""}/{u.get("second_tag_name") or ""}']
            for col, text in enumerate(vals):
                it = _center(text)
                if col in (2, 3):
                    _num_tip(it, u.get("follow_count") if col == 2 else u.get("like_count"))
                self.table.setItem(i, col, it)
        self.table.cellDoubleClicked.connect(self._pick)
        # 排序会同步重排 users，currentRow() 反查仍然正确
        self._pick_sorter = table_sort.install(
            self.table, rows=users, skip_columns=())
        v.addWidget(self.table, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        ok = QPushButton("确认")
        ok.setStyleSheet("background: palette(highlight); color: palette(highlighted-text); padding: 5px 18px; border-radius: 4px;")
        ok.clicked.connect(self._pick_ok)
        h.addWidget(ok)
        v.addLayout(h)

    def _pick_ok(self):
        row = self.table.currentRow()
        if row >= 0:
            self._pick(row, 0)

    def _pick(self, row, _col):
        self.picked = row
        self.accept()


class DouyinIndexPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._uid = ""
        self._name = ""
        self._data = None
        self._busy = False
        self._cancel_requested = False
        self._icons = []          # (按钮, 图标名, 颜色角色)：切主题时按主题色重绘

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_toolbar())

        # ---- 数据展示 Tabs ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_core_tab(), "核心指标")
        self.tabs.addTab(self._build_trend_tab(), "每日趋势")
        self.tabs.addTab(self._build_videos_tab(), "达人视频")
        self.tabs.addTab(self._build_fans_tab(), "粉丝分析")
        self.tabs.addTab(self._build_similar_tab(), "相似达人")
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("输入达人名称或抖音号 → 搜索 → 确认 → 抓取并回写")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.status_label)
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        progress_row.addWidget(self.progress_bar, 1)
        self.cancel_btn = self._icon_button("停止", "stop")
        self.cancel_btn.setToolTip("在当前步骤结束后停止；已保存的快照与已回写的数据保留")
        self.cancel_btn.clicked.connect(self._cancel_fetch)
        self.cancel_btn.hide()
        progress_row.addWidget(self.cancel_btn)
        layout.addLayout(progress_row)

    # ---------- 顶部工具栏 ----------
    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        v = QVBoxLayout(bar)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input_edit = QLineEdit(bar)
        self.input_edit.setPlaceholderText("输入达人名称或抖音号（如：封茗囧菌 / mandisasa）")
        self.input_edit.setClearButtonEnabled(True)
        self.input_edit.returnPressed.connect(self._do_search)
        row.addWidget(self.input_edit, 1)

        self.search_btn = self._icon_button("搜索", "search")
        self.search_btn.setToolTip("按达人名称或抖音号搜索候选，确认后再抓取")
        self.search_btn.clicked.connect(self._do_search)
        row.addWidget(self.search_btn)

        row.addWidget(_separator(vertical=True), 0, Qt.AlignVCenter)

        range_label = QLabel("统计范围")
        range_label.setProperty("role", "caption")
        row.addWidget(range_label)
        self.range_combo = QComboBox(bar)
        for days, name in ((30, "近30天"), (15, "近15天"), (7, "近7天")):
            self.range_combo.addItem(name, days)
        self.range_combo.setFixedWidth(96)
        self.range_combo.setToolTip("达人视频列表与视频指数的统计时间范围（核心指标各时间窗一次全部返回）")
        row.addWidget(self.range_combo)

        self.fetch_btn = self._icon_button("抓取并回写", "download", "primary")
        self.fetch_btn.setToolTip("保存完整指数快照，并按视频 ID 回写已有抖音资产的点赞、评论、分享、收藏与指数来源信息；不新建资产")
        self.fetch_btn.clicked.connect(self._do_fetch)
        row.addWidget(self.fetch_btn)

        self.open_btn = self._icon_button("打开保存目录", "folder")
        self.open_btn.setToolTip("打开 download/抖音指数/（每次抓取的完整数据都保存在这里）")
        self.open_btn.clicked.connect(self._open_dir)
        row.addWidget(self.open_btn)
        v.addLayout(row)

        v.addWidget(_separator())

        self.account_label = QLabel("当前账号：<b>未选择</b>（先搜索并确认达人）")
        self.account_label.setWordWrap(True)
        v.addWidget(self.account_label)

        # 涨粉里程碑 / 简介：辅助信息走 caption，不和账号主信息抢层级
        self.account_extra_label = QLabel("")
        self.account_extra_label.setProperty("role", "caption")
        self.account_extra_label.setWordWrap(True)
        self.account_extra_label.hide()
        v.addWidget(self.account_extra_label)
        return bar

    @staticmethod
    def _icon_color(role: str = "button") -> str:
        pal = theme.current_palette()
        if role == "primary":
            return pal.color(QPalette.ColorRole.HighlightedText).name()
        return pal.color(QPalette.ColorRole.ButtonText).name()

    def _icon_button(self, text: str, icon_name: str, variant: str = "secondary") -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("variant", variant)
        role = "primary" if variant == "primary" else "button"
        btn.setIcon(line_icon(icon_name, self._icon_color(role), 16))
        btn.setIconSize(QSize(16, 16))
        self._icons.append((btn, icon_name, role))
        return btn

    def on_theme_changed(self):
        """换主题不重建控件：只重绘线形图标与环比颜色。"""
        for btn, name, role in self._icons:
            btn.setIcon(line_icon(name, self._icon_color(role), 16))
        self._paint_exchange_colors()

    # ---------- UI 构建 ----------
    def _build_core_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        v.addWidget(self._core_content(w))
        return w

    def _core_content(self, parent):
        content = QFrame(parent)
        content.setProperty("variant", "card")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        cv.addWidget(self._section_label("核心指标（作者分析）"))
        self.core_table = QTableWidget(0, 1 + len(_WINDOWS) * 2)
        self.core_table.setHorizontalHeaderLabels(
            ["指标"] + [x for w2 in _WINDOWS for x in (w2[1], w2[2])])
        self._style_table(self.core_table, first_col_w=100)
        self._core_sorter = table_sort.install(self.core_table, skip_columns=(0,))
        cv.addWidget(self.core_table, 1)
        cv.addWidget(self._section_label("稿均指标（作品分析 · 每条作品的平均数据）"))
        self.avg_table = QTableWidget(0, 5)
        self.avg_table.setHorizontalHeaderLabels(
            ["指标", "近7天稿均", "环比前7天", "近30天稿均", "环比前30天"])
        self._style_table(self.avg_table, first_col_w=100)
        self._avg_sorter = table_sort.install(self.avg_table, skip_columns=(0,))
        cv.addWidget(self.avg_table, 1)
        self.core_body = _StackedBody(
            content, "还没有达人数据",
            "在上面输入达人名称或抖音号，搜索并确认后点「抓取并回写」", parent)
        return self.core_body

    def _build_trend_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        content = QFrame(w)
        content.setProperty("variant", "card")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        self.trend_label = self._section_label("每日趋势")
        cv.addWidget(self.trend_label)
        self.trend_table = QTableWidget(0, 6)
        self.trend_table.setHorizontalHeaderLabels(
            ["日期", "新增点赞", "净增粉丝", "新增分享", "新增评论", "新增作品"])
        self._style_table(self.trend_table, first_col_w=100)
        self._trend_sorter = table_sort.install(self.trend_table, skip_columns=())
        cv.addWidget(self.trend_table, 1)
        self.trend_body = _StackedBody(
            content, "暂无趋势数据", "抓取完成后按天列出新增点赞、净增粉丝等数据", w)
        v.addWidget(self.trend_body)
        return w

    def _build_videos_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        content = QFrame(w)
        content.setProperty("variant", "card")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        self.video_label = self._section_label("达人视频（双击行打开视频页）")
        cv.addWidget(self.video_label)
        self.video_table = QTableWidget(0, 7)
        self.video_table.setHorizontalHeaderLabels(
            ["发布日期", "内容", "点赞量", "评论量", "分享量", "收藏量", "视频指数"])
        self._style_table(self.video_table, first_col_w=96, stretch_col=1)
        self._videos: list = []
        self._video_sorter = table_sort.install(
            self.video_table, rows=self._videos, skip_columns=())
        self.video_table.cellDoubleClicked.connect(self._open_video)
        cv.addWidget(self.video_table, 1)
        self.videos_body = _StackedBody(
            content, "暂无作品数据",
            "抓取完成后显示每条作品的点赞、评论、分享、收藏与视频指数，双击可打开抖音视频页", w)
        v.addWidget(self.videos_body)
        return w

    def _build_fans_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        content = QFrame(w)
        content.setProperty("variant", "card")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        self.fans_label = self._section_label(
            "粉丝画像（占比 + TGI 指数，TGI>100 表示偏好度高于大盘）")
        cv.addWidget(self.fans_label)
        self.fans_table = QTableWidget(0, 4)
        self.fans_table.setHorizontalHeaderLabels(["维度", "项目", "占比", "TGI"])
        self._style_table(self.fans_table, first_col_w=100, stretch_col=1)
        self._fans_sorter = table_sort.install(self.fans_table, skip_columns=(0,))
        cv.addWidget(self.fans_table, 1)
        self.fans_body = _StackedBody(
            content, "暂无粉丝画像",
            "抓取完成后显示地域、年龄、性别、手机与兴趣的占比及 TGI", w)
        v.addWidget(self.fans_body)
        return w

    def _build_similar_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        content = QFrame(w)
        content.setProperty("variant", "card")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)
        self.similar_label = self._section_label("相似达人（双击行直接抓取该达人）")
        cv.addWidget(self.similar_label)
        self.similar_table = QTableWidget(0, 7)
        self.similar_table.setHorizontalHeaderLabels(
            ["排名", "达人", "抖音号", "粉丝", "总获赞", "作品", "标签"])
        self._style_table(self.similar_table, first_col_w=64, stretch_col=1)
        self._similar_users: list = []
        self._similar_sorter = table_sort.install(
            self.similar_table, rows=self._similar_users, skip_columns=())
        self.similar_table.cellDoubleClicked.connect(self._open_similar)
        cv.addWidget(self.similar_table, 1)
        self.similar_body = _StackedBody(
            content, "暂无相似达人",
            "抓取完成后列出同领域达人，双击可直接抓取该达人", w)
        v.addWidget(self.similar_body)
        return w

    @staticmethod
    def _section_label(text):
        lb = QLabel(text)
        lb.setProperty("role", "sectionTitle")
        return lb

    @staticmethod
    def _style_table(t: QTableWidget, first_col_w=70, stretch_col=None):
        """首列定宽、其余列铺满：避免整张表挤在左侧、右边留一大片空白。"""
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.setWordWrap(False)
        header = t.horizontalHeader()
        header.setHighlightSections(False)
        header.setMinimumSectionSize(56)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, first_col_w)
        for col in range(1, t.columnCount()):
            if stretch_col is None or col == stretch_col:
                header.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

    # ---------- 搜索（两步确认） ----------
    def _do_search(self):
        kw = self.input_edit.text().strip()
        if not kw:
            self.status_label.setText("请输入达人名称或抖音号")
            return
        self._set_busy(True, f"搜索「{kw}」...")

        def work():
            return dxi.search_daren(kw)

        def done(users, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"搜索失败：{err}")
                return
            if not users:
                self.status_label.setText(f"未找到「{kw}」相关的达人")
                return
            dlg = _PickDialog(self, users)
            if dlg.exec() == QDialog.Accepted and dlg.picked is not None:
                u = users[dlg.picked]
                self._uid = u.get("user_id", "")
                self._name = u.get("user_name", "")
                self.account_label.setText(
                    f"当前账号：<b>{self._name}</b>（抖音号: {u.get('aweme_id') or '-'}）"
                    f" · 粉丝 {dxi.fmt_count(u.get('follow_count'))}"
                    f" · 总获赞 {dxi.fmt_count(u.get('like_count'))}"
                    f" · 作品 {u.get('item_count')}")
                self.status_label.setText(f"已选择「{self._name}」→ 点「抓取并回写」")
                self.account_extra_label.hide()
                self.fetch_btn.setFocus()

        run_bg(work, done)

    # ---------- 抓取 ----------
    def _do_fetch(self):
        if not self._uid:
            self.status_label.setText("请先搜索并确认达人")
            return
        if self._busy:
            return
        days = self.range_combo.currentData() or 30
        uid, name = self._uid, self._name
        if not self._confirm_writeback(name):
            return
        self._set_busy(True, f"正在抓取「{name}」的抖音指数…", cancellable=True)

        def prog(msg):
            post_to_main(self.status_label.setText, f"{msg}")

        def work():
            try:
                return dxi.fetch_and_save(uid, days=days, progress=prog,
                                          is_cancelled=lambda: self._cancel_requested)
            except dxi.FetchCancelled:
                return {"cancelled": True}

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(str(err))
                return
            if result.get("cancelled"):
                self.status_label.setText("已取消，未回写资产")
                return
            data, path = result["data"], result["path"]
            self._data = data
            self._render(data)
            outcome = result["writeback"]
            text = f"「{name}」快照已保存；{outcome['summary']}。在资产库刷新查看。"
            if outcome.get("errors"):
                text += " " + "；".join(dict.fromkeys(outcome["errors"]))
            self.status_label.setText(text)
            self.status_label.setToolTip(f"快照：{os.path.basename(path)}")

        run_bg(work, done)

    def _confirm_writeback(self, name: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("回写资产")
        box.setIcon(QMessageBox.Question)
        box.setText(f"抓取「{name}」并回写已有资产？")
        box.setInformativeText(
            "将保存完整指数快照，并按视频 ID 更新本次结果中已有抖音条目的点赞、评论、分享、收藏与指数来源信息。\n"
            "不改标题、文件和人工信息；未匹配的作品不新建，旧快照不覆盖较新数据。")
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.addButton("抓取并回写", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        return clicked is not None and box.buttonRole(clicked) == QMessageBox.AcceptRole

    def _cancel_fetch(self):
        self._cancel_requested = True
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("已请求取消，将在当前步骤结束后停止；已完成的回写保留")

    # ---------- 渲染 ----------
    def _render(self, data: dict):
        core = data.get("core_metrics") or {}
        item_metrics = data.get("item_metrics") or {}
        self._render_core(core)
        self._render_trend(core)
        self._render_avg(item_metrics)
        self._render_videos(data.get("videos") or [])
        self._render_fans(data.get("fans_profile") or {})
        self._render_similar(data.get("similar_users") or [])
        self.core_body.show_data(
            any(core.get(seg) for _, seg, _ in _CORE_ROWS)
            or any(item_metrics.get(seg) for _, seg, _ in _AVG_ROWS))
        author = data.get("author") or {}
        intro = (author.get("user_introduction") or "").replace("\n", "· ")
        if len(intro) > 120:
            intro = intro[:120] + "..."
        ms = author.get("fans_milestone") or {}
        ms_txt = " · ".join(
            f"首{p}万粉 {_fmt_day(ms.get(k))}"
            for p, k in ((1, "first_1w_fans_date"), (10, "first_10w_fans_date"),
                         (30, "first_30w_fans_date"), (100, "first_100w_fans_date"))
            if ms.get(k))
        self.account_label.setText(
            f"当前账号：<b>{author.get('user_name') or self._name}</b>"
            f"（抖音号: {author.get('aweme_id') or '-'}）"
            f" · {author.get('first_tag_name') or ''}/{author.get('second_tag_name') or ''}"
            f" · 粉丝 {dxi.fmt_count(author.get('fans_count'))}"
            f" · 总获赞 {dxi.fmt_count(author.get('like_count'))}"
            f" · 作品 {author.get('item_count')}")
        extras = []
        if ms_txt:
            extras.append(f"涨粉里程碑：{ms_txt}")
        if intro:
            extras.append(f"简介：{intro}")
        self.account_extra_label.setText(" ｜ ".join(extras))
        self.account_extra_label.setVisible(bool(extras))

    def _render_core(self, core: dict):
        self.core_table.setRowCount(len(_CORE_ROWS))
        for r, (label, seg, prefix) in enumerate(_CORE_ROWS):
            info = core.get(seg) or {}
            self.core_table.setItem(r, 0, _center(label))
            for c, (suffix, _, _) in enumerate(_WINDOWS):
                v = info.get(prefix + suffix)
                it = _center(dxi.fmt_count(v) if v is not None else "-")
                self.core_table.setItem(r, 1 + c * 2, _num_tip(it, v))
                ex = info.get("exchange_by_" + suffix)
                it = _center(dxi.fmt_pct(ex) if ex is not None else "-")
                if isinstance(ex, (int, float)):
                    it.setData(table_sort.SORT_KEY_ROLE, ex)
                self.core_table.setItem(r, 2 + c * 2, it)
        self._core_sorter.apply()

    def _paint_exchange_colors(self):
        """环比列按涨跌着色：涨 success、跌 danger，0 与无数据保持正文色。"""
        up = QBrush(QColor(theme.color("success")))
        down = QBrush(QColor(theme.color("danger")))
        normal = QBrush(QColor(theme.color("text")))
        for table, cols in ((getattr(self, "core_table", None), _EX_CORE_COLS),
                            (getattr(self, "avg_table", None), _EX_AVG_COLS)):
            if table is None:
                continue
            for r in range(table.rowCount()):
                for c in cols:
                    item = table.item(r, c)
                    if item is None:
                        continue
                    value = item.data(table_sort.SORT_KEY_ROLE)
                    if not isinstance(value, (int, float)):
                        item.setForeground(normal)
                    elif value > 0:
                        item.setForeground(up)
                    elif value < 0:
                        item.setForeground(down)
                    else:
                        item.setForeground(normal)

    def _render_avg(self, item_metrics: dict):
        self.avg_table.setRowCount(len(_AVG_ROWS))
        for r, (label, seg, prefix) in enumerate(_AVG_ROWS):
            lst = item_metrics.get(seg) or []
            info = lst[0] if lst else {}
            self.avg_table.setItem(r, 0, _center(label))
            wk, mo = info.get(prefix + "week"), info.get(prefix + "month")
            it = _center(f"{wk:.2f}" if wk is not None else "-")
            self.avg_table.setItem(r, 1, _num_tip(it, wk))
            ex = info.get("exchange_by_week")
            it = _center(dxi.fmt_pct(ex))
            if isinstance(ex, (int, float)):
                it.setData(table_sort.SORT_KEY_ROLE, ex)
            self.avg_table.setItem(r, 2, it)
            it = _center(f"{mo:.2f}" if mo is not None else "-")
            self.avg_table.setItem(r, 3, _num_tip(it, mo))
            ex = info.get("exchange_by_month")
            it = _center(dxi.fmt_pct(ex))
            if isinstance(ex, (int, float)):
                it.setData(table_sort.SORT_KEY_ROLE, ex)
            self.avg_table.setItem(r, 4, it)
        self._avg_sorter.apply()
        self._paint_exchange_colors()

    def _render_trend(self, core: dict):
        by_date = {}
        for key in ("likelistday", "fanslistday", "sharelistday", "commentlistday", "itemlistday"):
            for it in core.get(key) or []:
                d = str(it.get("date") or "")
                if len(d) == 8:
                    by_date.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:]}", {})[key] = it.get("count")
        dates = sorted(by_date, reverse=True)
        self.trend_label.setText(f"每日趋势 · 共 {len(dates)} 天（接口返回的最大范围）")
        self.trend_body.show_data(bool(dates))
        self.trend_table.setRowCount(len(dates))
        for r, d in enumerate(dates):
            row = by_date[d]
            self.trend_table.setItem(r, 0, _center(d))
            for c, key in enumerate(("likelistday", "fanslistday", "sharelistday", "commentlistday", "itemlistday")):
                v = row.get(key)
                it = _center(dxi.fmt_count(v) if v is not None else "-")
                self.trend_table.setItem(r, 1 + c, _num_tip(it, v))
        self._trend_sorter.apply()

    def _render_videos(self, videos: list):
        videos = sorted(videos, key=lambda v: str(v.get("create_time") or ""), reverse=True)
        self._videos = videos
        self._video_sorter.attach(self._videos)
        self.video_label.setText(
            f"达人视频 · 共 {len(videos)} 条（双击行打开视频页）")
        self.videos_body.show_data(bool(videos))
        self.video_table.setRowCount(len(videos))
        for r, v in enumerate(videos):
            d = str(v.get("create_time") or "")
            date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else "-"
            self.video_table.setItem(r, 0, _center(date))
            it = QTableWidgetItem(str(v.get("video_text") or ""))
            it.setToolTip(str(v.get("video_text") or ""))
            self.video_table.setItem(r, 1, it)
            for key, c in (("like_cnt", 2), ("coment_cnt", 3), ("share_cnt", 4), ("follow_cnt", 5)):
                val = v.get(key)
                cell = _center(dxi.fmt_count(val) if val is not None else "-")
                self.video_table.setItem(r, c, _num_tip(cell, val))
            self.video_table.setItem(r, 6, _center(v.get("video_index") or "-"))
        self._video_sorter.apply()
        self.video_table.resizeRowsToContents()

    def _render_fans(self, fans: dict):
        rows = []
        for label in ("省份分布", "城市级别", "城市分布", "年龄分布", "性别分布",
                      "手机品牌", "手机价格", "兴趣标签"):
            items = fans.get(label) or []
            tgi = {x.get("name"): x.get("value") for x in fans.get(label + "TGI") or []}
            if not items:
                continue
            for x in items:
                v = x.get("value")
                tgi_value = tgi.get(x.get("name"))
                pct = f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "-"
                tgi_text = (f"{tgi_value:.2f}"
                            if isinstance(tgi_value, (int, float)) else "-")
                rows.append((label, str(x.get("name") or ""), pct, tgi_text,
                             v * 100 if isinstance(v, (int, float)) else None,
                             tgi_value if isinstance(tgi_value, (int, float)) else None))
        self.fans_label.setText(
            f"粉丝画像 · 共 {len(rows)} 项（占比 + TGI 指数，TGI>100 表示偏好度高于大盘）")
        self.fans_body.show_data(bool(rows))
        self.fans_table.setRowCount(len(rows))
        for r, (dim, name, pct, tgi_text, pct_value, tgi_value) in enumerate(rows):
            self.fans_table.setItem(r, 0, _center(dim))
            self.fans_table.setItem(r, 1, _center(name))
            cell = _center(pct)
            if pct_value is not None:
                cell.setData(table_sort.SORT_KEY_ROLE, pct_value)
            self.fans_table.setItem(r, 2, cell)
            cell = _center(tgi_text)
            if tgi_value is not None:
                cell.setData(table_sort.SORT_KEY_ROLE, tgi_value)
            self.fans_table.setItem(r, 3, cell)
        self._fans_sorter.apply()

    def _render_similar(self, users: list):
        self._similar_users = list(users)
        self._similar_sorter.attach(self._similar_users)
        self.similar_label.setText(
            f"相似达人 · 共 {len(users)} 位（双击行直接抓取该达人）")
        self.similar_body.show_data(bool(users))
        self.similar_table.setRowCount(len(users))
        for r, u in enumerate(users):
            vals = [u.get("rank"), u.get("user_name"), u.get("aweme_id") or "-",
                    u.get("follow_count"), u.get("like_count"), u.get("item_count"),
                    f'{u.get("first_tag_name") or ""}/{u.get("second_tag_name") or ""}']
            for col, v in enumerate(vals):
                if col in (3, 4):
                    it = _center(dxi.fmt_count(v))
                    self.similar_table.setItem(r, col, _num_tip(it, v))
                else:
                    self.similar_table.setItem(r, col, _center(v))
        self._similar_sorter.apply()

    # ---------- 交互 ----------
    def _open_video(self, row, _col):
        if not self._videos:
            return
        if 0 <= row < len(self._videos):
            vid = self._videos[row].get("item_id")
            if vid:
                QDesktopServices.openUrl(QUrl(f"https://www.douyin.com/video/{vid}"))

    def _open_similar(self, row, _col):
        if not self._data or self._busy:
            return
        users = self._similar_users
        if 0 <= row < len(users):
            u = users[row]
            self._uid = u.get("user_id", "")
            self._name = u.get("user_name", "")
            self.input_edit.setText(self._name)
            self.status_label.setText(f"已切换到「{self._name}」→ 正在抓取...")
            self._do_fetch()

    def _open_dir(self):
        try:
            os.startfile(str(dxi.index_dir()))
        except Exception:
            self.app.toast("保存目录尚未创建（先完成一次抓取）", "info")

    def _set_busy(self, busy: bool, text: str = "", *, cancellable: bool = False):
        self._busy = busy
        if busy:
            self._cancel_requested = False
        self.search_btn.setEnabled(not busy)
        self.fetch_btn.setEnabled(not busy)
        self.open_btn.setEnabled(not busy)
        self.range_combo.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.cancel_btn.setVisible(busy and cancellable)
        self.cancel_btn.setEnabled(busy and cancellable)
        if text:
            self.status_label.setText(text)
