"""视频评论区下载页：抖音 / B站 / 微博 三平台统一。

- 平台切换：顶部下拉选择，链接/ID 自动按平台解析（短链自动跳转）
- 输入：视频链接/ID；抖音额外支持 作者模式（主页链接/sec_uid 抓最新 N 个视频）
- 筛选：关键词（评论文本）/ 昵称 / 带图 / 最低点赞 / 排序（默认·点赞·时间）
- 导出： 评论区长图（PIL 绘制，含头像与评论图片）/  原始数据（json+txt+图片）
"""

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from gui_qt import table_sort
from gui_qt.common import post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.theme import current_palette
from toolbox import bili_comments as bc
from toolbox import douyin_comments as dc
from toolbox import weibo_comments as wc

_PLATFORMS = [
    ("douyin", "抖音"),
    ("bili", "B站"),
    ("weibo", "微博"),
]
_PLATFORM_NAMES = dict(_PLATFORMS)
# 导出目录的平台分层名（评论区/{平台}/{视频ID}/）
_PLATFORM_DIRS = {"douyin": "抖音", "bili": "B站", "weibo": "微博"}
_BACKENDS = {"douyin": dc, "bili": bc, "weibo": wc}
# 平台是否支持作者模式（输入作者主页链接抓最新 N 个视频）
_AUTHOR_OK = {"douyin": True, "bili": False, "weibo": False}

_IMAGE_MODES = [("all", "全部"), ("has", "仅带图"), ("text", "仅纯文字")]
_SORT_MODES = [("default", "默认"), ("likes", "点赞"), ("time", "时间")]
_IMAGE_NAMES = dict(_IMAGE_MODES)
_SORT_NAMES = dict(_SORT_MODES)

_PLACEHOLDERS = {
    ("douyin", False): "抖音视频链接 / aweme_id",
    ("douyin", True): "作者主页链接 / sec_uid（抓最新 N 个视频的评论）",
    ("bili", False): "B站视频链接 / BV号 / av号 / 动态链接（t.bilibili.com、opus）",
    ("weibo", False): "微博视频链接（tv/show） / 普通微博链接 / 视频号 1034:xxx",
}


class _ElidedLabel(QLabel):
    """按可用宽度中间省略的单行标签：完整内容进 tooltip，避免长提示撑宽布局。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = str(text or "")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(self._full_text)

    def setText(self, text: str):
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self.update()

    def text(self) -> str:
        return self._full_text

    def paintEvent(self, _event):
        p = QPainter(self)
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(16, self.width()))
        role = (QPalette.ColorRole.PlaceholderText
                if self.property("role") == "caption" else QPalette.ColorRole.WindowText)
        p.setPen(self.palette().color(role))
        p.drawText(self.rect(), int(self.alignment()), shown)
        p.end()


class VideoCommentsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []          # [{id, desc, comments}]
        self.current_index = -1
        self._stop = False
        self._busy = False
        self._icons: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._build_fetch_card())
        layout.addWidget(self._build_filter_bar())
        layout.addWidget(self._build_table_card(), 1)

    # ===================== 布局片段 =====================
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "caption")
        return label

    @staticmethod
    def _separator(vertical: bool = False) -> QFrame:
        line = QFrame()
        line.setStyleSheet("background: palette(shadow); border: none;")
        if vertical:
            line.setFixedWidth(1)
        else:
            line.setFixedHeight(1)
        return line

    @staticmethod
    def _icon_color(role: str = "button") -> str:
        pal = current_palette()
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
        for btn, name, role in self._icons:
            if btn is self.mode_btn and self.mode_btn.isChecked():
                role = "primary"
            btn.setIcon(line_icon(name, self._icon_color(role), 16))

    def _build_fetch_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("抓取", card)
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        self.status_label = _ElidedLabel("选择平台，输入视频链接开始", card)
        self.status_label.setProperty("role", "caption")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.status_label, 1)
        v.addLayout(head)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.platform_combo = QComboBox(card)
        for key, name in _PLATFORMS:
            self.platform_combo.addItem(name, key)
        self.platform_combo.setFixedWidth(92)
        self.platform_combo.setToolTip("评论来源平台")
        self.platform_combo.currentIndexChanged.connect(self._platform_changed)
        row1.addWidget(self.platform_combo)

        self.mode_btn = self._icon_button("视频", "swap")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setFixedWidth(80)
        self.mode_btn.toggled.connect(self._mode_changed)
        row1.addWidget(self.mode_btn)

        self.input_edit = QLineEdit(card)
        self.input_edit.setPlaceholderText(_PLACEHOLDERS[("douyin", False)])
        self.input_edit.setClearButtonEnabled(True)
        self.input_edit.returnPressed.connect(self.start_fetch)
        row1.addWidget(self.input_edit, 1)

        self.export_btn = self._icon_button("导出长图", "image")
        self.export_btn.setToolTip("当前视频筛选后的评论导出为 PNG 长图（含头像与评论图片）")
        self.export_btn.clicked.connect(self.export_png)
        row1.addWidget(self.export_btn)
        self.save_btn = self._icon_button("保存原始数据", "save")
        self.save_btn.setToolTip("保存 comments.json（原始数据）/ comments.txt / 图片原图")
        self.save_btn.clicked.connect(self.save_raw)
        row1.addWidget(self.save_btn)

        row1.addSpacing(4)
        self.fetch_btn = self._icon_button("抓取评论", "download", "primary")
        self.fetch_btn.setToolTip("按当前平台与链接抓取评论（回车同效）")
        self.fetch_btn.clicked.connect(self.start_fetch)
        self.stop_btn = self._icon_button("停止", "stop")
        self.stop_btn.setToolTip("停止当前抓取（已抓到的评论保留）")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._do_stop)
        row1.addWidget(self.stop_btn)
        row1.addWidget(self.fetch_btn)
        v.addLayout(row1)

        self.author_row = QWidget(card)
        row2 = QHBoxLayout(self.author_row)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)
        row2.addWidget(self._caption("视频"))
        self.video_combo = QComboBox(card)
        self.video_combo.setMinimumWidth(160)
        self.video_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.video_combo.setMinimumContentsLength(8)
        self.video_combo.setToolTip("已抓取的视频；切换即刷新下方评论")
        self.video_combo.currentIndexChanged.connect(self._video_switched)
        row2.addWidget(self.video_combo, 1)
        row2.addWidget(self._caption("视频数"))
        self.video_count_spin = QSpinBox(self.author_row)
        self.video_count_spin.setRange(1, 10)
        self.video_count_spin.setValue(3)
        self.video_count_spin.setFixedWidth(64)
        self.video_count_spin.setToolTip("抓取作者最新几个视频的评论")
        row2.addWidget(self.video_count_spin)
        row2.addWidget(self._caption("页数"))
        self.pages_spin = QSpinBox(self.author_row)
        self.pages_spin.setRange(1, 50)
        self.pages_spin.setValue(5)
        self.pages_spin.setFixedWidth(64)
        self.pages_spin.setToolTip("作者模式下每个视频抓取的评论页数（每页 20 条）")
        row2.addWidget(self.pages_spin)
        v.addWidget(self.author_row)
        self._mode_changed(False)
        return card

    def _build_filter_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)

        h.addWidget(self._caption("搜索"))
        self.kw_edit = QLineEdit(bar)
        self.kw_edit.setPlaceholderText("评论文本关键词")
        self.kw_edit.setClearButtonEnabled(True)
        self.kw_edit.setFixedWidth(180)
        self.kw_edit.textChanged.connect(self.apply_filter)
        h.addWidget(self.kw_edit)

        self.user_edit = QLineEdit(bar)
        self.user_edit.setPlaceholderText("昵称")
        self.user_edit.setClearButtonEnabled(True)
        self.user_edit.setFixedWidth(130)
        self.user_edit.textChanged.connect(self.apply_filter)
        h.addWidget(self.user_edit)

        self.image_combo = QComboBox(bar)
        for v, name in _IMAGE_MODES:
            self.image_combo.addItem(name, v)
        self.image_combo.setFixedWidth(96)
        self.image_combo.setToolTip("按评论是否带图筛选")
        self.image_combo.currentIndexChanged.connect(self.apply_filter)
        h.addWidget(self.image_combo)

        h.addWidget(self._caption("点赞 ≥"))
        self.likes_spin = QSpinBox(bar)
        self.likes_spin.setRange(0, 999999)
        self.likes_spin.setFixedWidth(96)
        self.likes_spin.setToolTip("只显示点赞数不低于该值的评论")
        self.likes_spin.valueChanged.connect(self.apply_filter)
        h.addWidget(self.likes_spin)

        h.addWidget(self._caption("排序"))
        self.sort_combo = QComboBox(bar)
        for v, name in _SORT_MODES:
            self.sort_combo.addItem(name, v)
        self.sort_combo.setFixedWidth(92)
        self.sort_combo.setToolTip("默认顺序 / 按点赞 / 按时间，表头点击也可排序")
        self.sort_combo.currentIndexChanged.connect(self.apply_filter)
        h.addWidget(self.sort_combo)

        reset_btn = self._icon_button("重置", "refresh")
        reset_btn.setToolTip("清空关键词、昵称、图片、点赞与排序条件")
        reset_btn.clicked.connect(self.reset_filter)
        h.addWidget(reset_btn)
        h.addStretch(1)
        return bar

    def _build_table_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("评论", card)
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.count_label = self._caption("0 条")
        head.addWidget(self.count_label)
        v.addLayout(head)

        self.table = QTableWidget(0, 5, card)
        self.table.setHorizontalHeaderLabels(["用户", "评论内容", "点赞", "图", "时间"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col in (2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.resizeSection(4, 110)
        header.setHighlightSections(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        # 表头点击排序（图片数量列不参与，点赞/时间按数值解析）
        self._sorter = table_sort.install(self.table, skip_columns=(3,))
        # 排序会重建行（行高回到默认），排完按换行内容重算行高
        self.table.horizontalHeader().sectionClicked.connect(
            lambda _col: self.table.resizeRowsToContents())
        v.addWidget(self.table, 1)
        return card

    # ===================== 平台 / 模式 / 状态 =====================

    def _platform(self) -> str:
        return self.platform_combo.currentData() or "douyin"

    def _be(self):
        return _BACKENDS[self._platform()]

    def _platform_changed(self, *_):
        self._mode_changed(self.mode_btn.isChecked())

    def _is_author(self) -> bool:
        return self.mode_btn.isChecked() and _AUTHOR_OK[self._platform()]

    def _mode_changed(self, checked):
        supported = _AUTHOR_OK[self._platform()]
        is_author = bool(checked) and supported
        self.mode_btn.setChecked(is_author)
        self.mode_btn.setText("作者" if is_author else "视频")
        self.mode_btn.setIcon(line_icon(
            "swap", self._icon_color("primary" if is_author else "button"), 16))
        self.mode_btn.setToolTip(
            ("当前为作者模式，点击切换为视频模式" if is_author
             else "当前为视频模式，点击切换为作者模式") if supported
            else "当前平台仅支持视频模式")
        self.mode_btn.setEnabled(supported and not self._busy)
        self.author_row.setVisible(is_author)
        self.input_edit.setPlaceholderText(
            _PLACEHOLDERS[(self._platform(), is_author)])

    def _do_stop(self):
        self._stop = True

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.platform_combo.setEnabled(not busy)
        self.mode_btn.setEnabled(not busy and _AUTHOR_OK[self._platform()])
        self.video_combo.setEnabled(not busy)
        self.video_count_spin.setEnabled(not busy)
        self.pages_spin.setEnabled(not busy)
        self.fetch_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.export_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        if text:
            self.status_label.setText(text)

    def _say(self, text: str):
        post_to_main(self.status_label.setText, text)

    # ===================== 抓取 =====================

    def start_fetch(self):
        if self._busy:
            return
        be = self._be()
        raw = self.input_edit.text().strip()
        if not raw:
            self.status_label.setText("请先输入视频链接或作者主页链接")
            return
        kind, vid = be.parse_input(raw)
        if not kind:
            self.status_label.setText("无法识别输入（需视频链接/ID）")
            return
        is_author = self._is_author()
        if kind == "sec_uid" and not is_author:
            self.status_label.setText("作者主页链接请切换为作者模式后抓取")
            return
        if is_author and kind != "sec_uid":
            self.status_label.setText("作者模式请输入作者主页链接或 sec_uid")
            return
        self._stop = False
        self._set_busy(True, "抓取中...")
        self.videos = []
        self.current_index = -1
        self.video_combo.clear()
        self.table.setRowCount(0)
        pages = self.pages_spin.value() if is_author else 5
        count = self.video_count_spin.value() if is_author else 1

        def progress(msg, cur=0, total=0):
            self._say(f"{msg}")

        def work():
            # plan 元素: (fetch_id, desc, ctype)。ctype=None 用各后端默认
            # （视频路径）；仅 B 站 fetch_comments 支持 ctype（动态评论坐标）
            if kind == "sec_uid":
                self._say("获取作者视频列表...")
                targets = be.fetch_author_videos(vid, count)
                if not targets:
                    raise RuntimeError("未获取到作者视频列表（链接无效或被风控拦截）")
                plan = [(vid2, desc, None) for vid2, desc in targets]
            elif kind == "dynamic":
                # B站动态（t.bilibili.com/{id} / opus/{id}）：详情预检拿权威
                # 评论坐标——图文/转发动态的评论挂相簿（type=1/11）或源动态下，
                # 只有纯文字动态才是 type=17 + 动态 ID 本身
                self._say("校验动态...")
                vinfo = be.fetch_dynamic_info(vid)
                if not vinfo.get("exists") and not vinfo.get("unknown"):
                    raise RuntimeError(f"动态不存在（ID {vid}），请检查链接是否完整")
                if vinfo.get("unknown"):
                    self._say("动态预检被风控跳过，直接尝试抓取评论...")
                plan = [(vinfo.get("comment_id") or vid,
                         vinfo.get("desc") or "",
                         vinfo.get("comment_type") or 17)]
            else:
                # 预检视频存在性（风控误判时不阻断，直接尝试抓评论）
                self._say("校验视频...")
                vinfo = be.fetch_video_info(vid)
                if not vinfo.get("exists") and not vinfo.get("unknown"):
                    raise RuntimeError(f"视频不存在（ID {vid}），请检查链接是否完整")
                if vinfo.get("unknown"):
                    self._say("视频预检被风控跳过，直接尝试抓取评论...")
                plan = [(vid, vinfo.get("desc") or "", None)]
            results = []
            for i, (fetch_id, desc, ctype) in enumerate(plan):
                if self._stop:
                    break
                self._say(f"视频 {i + 1}/{len(plan)}：{desc or fetch_id}")
                kwargs = {"pages": pages, "progress": progress,
                          "should_stop": lambda: self._stop}
                if ctype is not None:
                    kwargs["ctype"] = ctype
                comments = be.fetch_comments(fetch_id, **kwargs)
                # 展示/导出目录用用户输入的动态 ID（fetch_id 可能是相簿 ID）
                display_id = vid if kind == "dynamic" else fetch_id
                results.append({"id": str(display_id), "desc": desc,
                               "comments": comments})
            return results

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"{err}")
                return
            self.videos = result or []
            if not self.videos:
                self.status_label.setText("未获取到评论（视频不存在或接口受限）")
                return
            self.video_combo.blockSignals(True)
            self.video_combo.clear()
            for v in self.videos:
                self.video_combo.addItem(
                    f"{v['desc'] or v['id']}（{len(v['comments'])} 条）")
            self.video_combo.blockSignals(False)
            self.video_combo.setCurrentIndex(0)
            self.current_index = 0
            self.apply_filter()
            total = sum(len(v["comments"]) for v in self.videos)
            n_img = sum(len(be.image_urls(c))
                        for v in self.videos for c in v["comments"])
            plat = _PLATFORM_NAMES[self._platform()]
            if total == 0:
                self.status_label.setText(
                    "0 条评论：该视频可能无评论，或被风控拦截"
                    "（稍后重试，或确认该平台登录状态）")
            else:
                self.status_label.setText(
                    f"{plat} · 共 {len(self.videos)} 个视频 / {total} 条评论 / {n_img} 张图片")

        run_bg(work, done)

    # ===================== 表格渲染 / 筛选 =====================

    def _current_video(self) -> dict | None:
        if 0 <= self.current_index < len(self.videos):
            return self.videos[self.current_index]
        return None

    def _filtered_comments(self, v: dict) -> list:
        return self._be().filter_comments(
            v["comments"],
            keyword=self.kw_edit.text(),
            user=self.user_edit.text(),
            image_mode=self.image_combo.currentData(),
            min_likes=self.likes_spin.value(),
            sort_by=self.sort_combo.currentData(),
        )

    def _video_switched(self, index):
        self.current_index = index
        self.apply_filter()

    def apply_filter(self, *_):
        v = self._current_video()
        if not v:
            self.table.setRowCount(0)
            self.count_label.setText("0 条")
            return
        be = self._be()
        comments = self._filtered_comments(v)
        self.table.setRowCount(len(comments))
        for i, c in enumerate(comments):
            n_img = len(be.image_urls(c))
            likes = be.comment_likes(c)
            items = [
                be.comment_user(c),
                be.comment_text(c).replace("\n", ""),
                str(likes) if likes else "",
                str(n_img) if n_img else "",
                be.comment_time(c),
            ]
            for col, text in enumerate(items):
                it = QTableWidgetItem(text)
                if col in (2, 3):
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, col, it)
        self._sorter.apply()
        self.table.resizeRowsToContents()
        if v["comments"]:
            self.count_label.setText(
                f"筛选后 {len(comments)}/{len(v['comments'])} 条")

    def reset_filter(self):
        self.kw_edit.clear()
        self.user_edit.clear()
        self.image_combo.setCurrentIndex(0)
        self.likes_spin.setValue(0)
        self.sort_combo.setCurrentIndex(0)

    # ===================== 导出 =====================

    def _export_dir(self):
        from toolbox.config_manager import get_export_subdir
        return get_export_subdir("video_comments")

    def _layered_dir(self, video_id: str):
        """导出分层目录：评论区/{平台}/{视频ID}（长图与原始数据同层）。"""
        plat = _PLATFORM_DIRS.get(self._platform(), self._platform())
        return self._export_dir() / plat / str(video_id)

    def export_png(self):
        v = self._current_video()
        if not v:
            self.status_label.setText("请先抓取评论")
            return
        comments = self._filtered_comments(v)
        if not comments:
            self.status_label.setText("筛选后没有评论可导出")
            return
        desc_parts = []
        if self.kw_edit.text().strip():
            desc_parts.append(f"关键词「{self.kw_edit.text().strip()}」")
        if self.user_edit.text().strip():
            desc_parts.append(f"昵称「{self.user_edit.text().strip()}」")
        if self.image_combo.currentData() != "all":
            desc_parts.append(_IMAGE_NAMES[self.image_combo.currentData()])
        if self.likes_spin.value():
            desc_parts.append(f"点赞≥{self.likes_spin.value()}")
        if self.sort_combo.currentData() != "default":
            desc_parts.append(f"按{_SORT_NAMES[self.sort_combo.currentData()]}排序")
        meta = {
            "platform": _PLATFORM_NAMES[self._platform()],
            "video_desc": v["desc"] or v["id"],
            "filter_desc": "、".join(desc_parts) if desc_parts else "未筛选",
            "stats": {"total": len(v["comments"])},
        }
        out = self._layered_dir(v["id"]) / f"评论区_{v['id']}.png"
        self._set_busy(True, "导出长图中（下载头像与图片中）...")

        def progress(msg, cur=0, total=0):
            self._say(f"{msg}")

        def work():
            return self._be().export_comments_png(comments, meta, out, progress=progress)

        def done(path, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"导出失败：{err}")
                return
            self.status_label.setText(f"长图已导出（{len(comments)} 条评论）")
            self.app.toast(f"评论区长图已导出：{path}", "success")
            try:
                os.startfile(str(path))
            except Exception:
                pass

        run_bg(work, done)

    def save_raw(self):
        v = self._current_video()
        if not v:
            self.status_label.setText("请先抓取评论")
            return
        out = self._layered_dir(v["id"])
        self._set_busy(True, "保存原始数据中（下载评论图片）...")
        # 下载浮窗（主线程创建；评论图片 4 线程并发下载）
        from gui_qt.download_float import get_float
        card = get_float().add_batch("评论区图片")

        def progress(msg, cur=0, total=0):
            self._say(f"{msg}")

        def work():
            return self._be().save_raw(v["comments"], out, progress=progress,
                                       task_event=card.handle_event, threads=4)

        def done(stats, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"保存失败：{err}")
                card.finish(f"保存失败: {str(err)[:50]}")
                return
            self.status_label.setText(
                f"已保存 {stats['comments']} 条评论 / {stats['downloaded']}/{stats['images']} 张图片")
            card.finish(f"图片 {stats['downloaded']}/{stats['images']}")
            self.app.toast(
                f"评论区原始数据已保存：{stats['comments']} 条 / 图片 {stats['downloaded']}/{stats['images']}",
                "success")
            try:
                os.startfile(str(out))
            except Exception:
                pass

        run_bg(work, done)
