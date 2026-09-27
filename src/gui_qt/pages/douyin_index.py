"""抖音指数页：搜索达人 → 全量抓取（作者分析/作品分析/粉丝分析）→ 本地保存 JSON。

- 搜索：输入达人名称或抖音号 → 候选列表确认（两步确认，不直接抓）
- 抓取：核心指标（昨日/近7天/近15天/近30天）、稿均指标、每日趋势、
        达人视频（点赞/评论/分享/收藏/视频指数）、粉丝画像（地域/年龄/性别/手机/兴趣 + TGI）、
        相似达人
- 保存：download/抖音指数/<达人名>/抖音指数_时间戳.json（+ latest.json）
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from platforms import douyin_index as dxi

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
        v.addWidget(self.table, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        ok = QPushButton("确认")
        ok.setStyleSheet("background: #2e7d32; color: white; padding: 5px 18px; border-radius: 4px;")
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- 标题行 + 帮助 ----
        title = QLabel("📈 抖音指数")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 抖音指数 · 功能介绍", (
            "<p><b>创作者中心「抖音指数」达人数据全量抓取（作者分析 / 作品分析 / 粉丝分析），"
            "每次抓取自动在本地存一份完整 JSON。</b></p>"
            "<p>· <b>需要抖音 Cookie</b>（创作者中心接口需登录态）：「Cookie 管理」→ 抖音 → 浏览器登录</p>"
            "<p>· <b>搜索</b>：输入达人名称或抖音号 → 🔍 搜索 → 候选列表中双击或确认选择（两步确认）</p>"
            "<p>· <b>核心指标</b>：新增点赞/净增粉丝/新增分享/新增评论/新增作品，"
            "昨日·近7天·近15天·近30天 四档 + 各档环比 + 涨粉里程碑</p>"
            "<p>· <b>稿均指标</b>：稿均点赞/涨粉/分享/评论（近7天、近30天 + 环比）</p>"
            "<p>· <b>每日趋势</b>：接口返回范围内每天的五个指标序列</p>"
            "<p>· <b>达人视频</b>：发布日期/内容/点赞/评论/分享/收藏/视频指数（双击行打开视频页）；"
            "「统计范围」下拉控制视频列表与视频指数的时间范围</p>"
            "<p>· <b>粉丝分析</b>：省份/城市级别/城市/年龄/性别/手机品牌/手机价格/兴趣标签，"
            "全部带占比 + TGI 偏好指数（TGI&gt;100 表示该人群偏好度高于大盘）</p>"
            "<p>· <b>相似达人</b>：同赛道达人榜（双击行直接抓取该达人）</p>"
            "<p>· <b>保存位置</b>：download/抖音指数/&lt;达人名&gt;/抖音指数_时间戳.json"
            "（另存 latest.json 为最新一份快照）</p>"
        )))
        layout.addLayout(title_row)

        # ---- 顶部输入区 ----
        top = QFrame()
        top.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(6)

        row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("输入达人名称或抖音号（如：封茗囧菌 / mandisasa）")
        self.input_edit.returnPressed.connect(self._do_search)
        row.addWidget(self.input_edit, 1)
        self.search_btn = QPushButton("🔍 搜索")
        self.search_btn.clicked.connect(self._do_search)
        row.addWidget(self.search_btn)
        row.addWidget(QLabel("统计范围"))
        self.range_combo = QComboBox()
        for days, name in ((30, "近30天"), (15, "近15天"), (7, "近7天")):
            self.range_combo.addItem(name, days)
        self.range_combo.setToolTip("达人视频列表与视频指数的统计时间范围（核心指标各时间窗一次全部返回）")
        row.addWidget(self.range_combo)
        self.fetch_btn = QPushButton("📊 获取全部数据并保存")
        self.fetch_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.fetch_btn.clicked.connect(self._do_fetch)
        row.addWidget(self.fetch_btn)
        open_btn = QPushButton("📂 打开保存目录")
        open_btn.setToolTip("打开 download/抖音指数/（每次抓取的完整数据都保存在这里）")
        open_btn.clicked.connect(self._open_dir)
        row.addWidget(open_btn)
        tv.addLayout(row)

        self.account_label = QLabel("当前账号：<b>未选择</b>（先搜索并确认达人）")
        self.account_label.setWordWrap(True)
        tv.addWidget(self.account_label)
        layout.addWidget(top)

        # ---- 数据展示 Tabs ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_core_tab(), "📊 核心指标")
        self.tabs.addTab(self._build_trend_tab(), "📅 每日趋势")
        self.tabs.addTab(self._build_videos_tab(), "🎬 达人视频")
        self.tabs.addTab(self._build_fans_tab(), "👥 粉丝分析")
        self.tabs.addTab(self._build_similar_tab(), "🔗 相似达人")
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("输入达人名称或抖音号 → 🔍 搜索 → 确认 → 获取全部数据并保存")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

    # ---------- UI 构建 ----------
    def _build_core_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        v.setSpacing(4)
        v.addWidget(self._section_label("核心指标（作者分析）"))
        self.core_table = QTableWidget(0, 1 + len(_WINDOWS) * 2)
        self.core_table.setHorizontalHeaderLabels(
            ["指标"] + [x for w2 in _WINDOWS for x in (w2[1], w2[2])])
        self._style_table(self.core_table, first_col_w=100)
        v.addWidget(self.core_table)
        v.addWidget(self._section_label("稿均指标（作品分析 · 每条作品的平均数据）"))
        self.avg_table = QTableWidget(0, 5)
        self.avg_table.setHorizontalHeaderLabels(
            ["指标", "近7天稿均", "环比前7天", "近30天稿均", "环比前30天"])
        self._style_table(self.avg_table, first_col_w=100)
        v.addWidget(self.avg_table)
        return w

    def _build_trend_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        self.trend_label = self._section_label("每日趋势")
        v.addWidget(self.trend_label)
        self.trend_table = QTableWidget(0, 6)
        self.trend_table.setHorizontalHeaderLabels(
            ["日期", "新增点赞", "净增粉丝", "新增分享", "新增评论", "新增作品"])
        self._style_table(self.trend_table, first_col_w=90)
        v.addWidget(self.trend_table, 1)
        return w

    def _build_videos_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        v.addWidget(self._section_label("达人视频（双击行打开视频页）"))
        self.video_table = QTableWidget(0, 7)
        self.video_table.setHorizontalHeaderLabels(
            ["发布日期", "内容", "点赞量", "评论量", "分享量", "收藏量", "视频指数"])
        self._style_table(self.video_table, first_col_w=88, stretch_col=1)
        self.video_table.cellDoubleClicked.connect(self._open_video)
        v.addWidget(self.video_table, 1)
        return w

    def _build_fans_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        v.addWidget(self._section_label("粉丝画像（占比 + TGI 指数，TGI>100 表示偏好度高于大盘）"))
        self.fans_table = QTableWidget(0, 4)
        self.fans_table.setHorizontalHeaderLabels(["维度", "项目", "占比", "TGI"])
        self._style_table(self.fans_table, first_col_w=80, stretch_col=1)
        v.addWidget(self.fans_table, 1)
        return w

    def _build_similar_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        v.addWidget(self._section_label("相似达人（双击行直接抓取该达人）"))
        self.similar_table = QTableWidget(0, 7)
        self.similar_table.setHorizontalHeaderLabels(
            ["排名", "达人", "抖音号", "粉丝", "总获赞", "作品", "标签"])
        self._style_table(self.similar_table, first_col_w=50, stretch_col=1)
        self.similar_table.cellDoubleClicked.connect(self._open_similar)
        v.addWidget(self.similar_table, 1)
        return w

    @staticmethod
    def _section_label(text):
        lb = QLabel(text)
        lb.setStyleSheet("font-weight: bold;")
        return lb

    @staticmethod
    def _style_table(t: QTableWidget, first_col_w=70, stretch_col=None):
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().resizeSection(0, first_col_w)
        if stretch_col is not None:
            t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)

    # ---------- 搜索（两步确认） ----------
    def _do_search(self):
        kw = self.input_edit.text().strip()
        if not kw:
            self.status_label.setText("❌ 请输入达人名称或抖音号")
            return
        self._set_busy(True, f"⏳ 搜索「{kw}」...")

        def work():
            return dxi.search_daren(kw)

        def done(users, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 搜索失败：{err}")
                return
            if not users:
                self.status_label.setText(f"❌ 未找到「{kw}」相关的达人")
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
                self.status_label.setText(f"✅ 已选择「{self._name}」→ 点「📊 获取全部数据并保存」")
                self.fetch_btn.setFocus()

        run_bg(work, done)

    # ---------- 抓取 ----------
    def _do_fetch(self):
        if not self._uid:
            self.status_label.setText("❌ 请先搜索并确认达人")
            return
        if self._busy:
            return
        days = self.range_combo.currentData() or 30
        uid, name = self._uid, self._name
        self._set_busy(True, f"⏳ 正在抓取「{name}」的抖音指数...")

        def prog(msg):
            post_to_main(self.status_label.setText, f"⏳ {msg}")

        def work():
            data = dxi.fetch_daren_all(uid, days=days, progress=prog)
            path = dxi.save_snapshot(data)
            return data, path

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 抓取失败：{err}")
                self.app.toast(f"抖音指数抓取失败：{err}", "warning")
                return
            data, path = result
            self._data = data
            self._render(data)
            self.status_label.setText(f"✅ 「{name}」数据已保存：{path}")
            self.app.toast(f"抖音指数已保存：{os.path.basename(path)}", "success")

        run_bg(work, done)

    # ---------- 渲染 ----------
    def _render(self, data: dict):
        self._render_core(data.get("core_metrics") or {})
        self._render_trend(data.get("core_metrics") or {})
        self._render_avg(data.get("item_metrics") or {})
        self._render_videos(data.get("videos") or [])
        self._render_fans(data.get("fans_profile") or {})
        self._render_similar(data.get("similar_users") or [])
        author = data.get("author") or {}
        intro = (author.get("user_introduction") or "").replace("\n", " · ")
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
            f" · 作品 {author.get('item_count')}"
            + (f"\n涨粉里程碑：{ms_txt}" if ms_txt else "")
            + (f"\n简介：{intro}" if intro else ""))

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
                self.core_table.setItem(r, 2 + c * 2, _center(dxi.fmt_pct(ex) if ex is not None else "-"))

    def _render_avg(self, item_metrics: dict):
        self.avg_table.setRowCount(len(_AVG_ROWS))
        for r, (label, seg, prefix) in enumerate(_AVG_ROWS):
            lst = item_metrics.get(seg) or []
            info = lst[0] if lst else {}
            self.avg_table.setItem(r, 0, _center(label))
            wk, mo = info.get(prefix + "week"), info.get(prefix + "month")
            it = _center(f"{wk:.2f}" if wk is not None else "-")
            self.avg_table.setItem(r, 1, _num_tip(it, wk))
            self.avg_table.setItem(r, 2, _center(dxi.fmt_pct(info.get("exchange_by_week"))))
            it = _center(f"{mo:.2f}" if mo is not None else "-")
            self.avg_table.setItem(r, 3, _num_tip(it, mo))
            self.avg_table.setItem(r, 4, _center(dxi.fmt_pct(info.get("exchange_by_month"))))

    def _render_trend(self, core: dict):
        by_date = {}
        for key in ("likelistday", "fanslistday", "sharelistday", "commentlistday", "itemlistday"):
            for it in core.get(key) or []:
                d = str(it.get("date") or "")
                if len(d) == 8:
                    by_date.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:]}", {})[key] = it.get("count")
        dates = sorted(by_date, reverse=True)
        self.trend_label.setText(f"每日趋势 · 共 {len(dates)} 天（接口返回的最大范围）")
        self.trend_table.setRowCount(len(dates))
        for r, d in enumerate(dates):
            row = by_date[d]
            self.trend_table.setItem(r, 0, _center(d))
            for c, key in enumerate(("likelistday", "fanslistday", "sharelistday", "commentlistday", "itemlistday")):
                v = row.get(key)
                it = _center(dxi.fmt_count(v) if v is not None else "-")
                self.trend_table.setItem(r, 1 + c, _num_tip(it, v))

    def _render_videos(self, videos: list):
        videos = sorted(videos, key=lambda v: str(v.get("create_time") or ""), reverse=True)
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
                rows.append((label, str(x.get("name") or ""),
                             f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "-",
                             f"{tgi.get(x.get('name'), 0):.2f}" if isinstance(tgi.get(x.get("name")), (int, float)) else "-"))
        self.fans_table.setRowCount(len(rows))
        for r, (dim, name, pct, tgi) in enumerate(rows):
            self.fans_table.setItem(r, 0, _center(dim))
            self.fans_table.setItem(r, 1, _center(name))
            self.fans_table.setItem(r, 2, _center(pct))
            self.fans_table.setItem(r, 3, _center(tgi))

    def _render_similar(self, users: list):
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

    # ---------- 交互 ----------
    def _open_video(self, row, _col):
        if not self._data:
            return
        videos = sorted(self._data.get("videos") or [],
                        key=lambda v: str(v.get("create_time") or ""), reverse=True)
        if 0 <= row < len(videos):
            vid = videos[row].get("item_id")
            if vid:
                QDesktopServices.openUrl(QUrl(f"https://www.douyin.com/video/{vid}"))

    def _open_similar(self, row, _col):
        if not self._data or self._busy:
            return
        users = self._data.get("similar_users") or []
        if 0 <= row < len(users):
            u = users[row]
            self._uid = u.get("user_id", "")
            self._name = u.get("user_name", "")
            self.input_edit.setText(self._name)
            self.status_label.setText(f"✅ 已切换到「{self._name}」→ 正在抓取...")
            self._do_fetch()

    def _open_dir(self):
        try:
            os.startfile(str(dxi.index_dir()))
        except Exception:
            self.app.toast("保存目录尚未创建（先完成一次抓取）", "info")

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.search_btn.setEnabled(not busy)
        self.fetch_btn.setEnabled(not busy)
        if text:
            self.status_label.setText(text)
