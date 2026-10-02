"""视频评论区下载页：抖音 / B站 / 微博 三平台统一。

- 平台切换：顶部下拉选择，链接/ID 自动按平台解析（短链自动跳转）
- 输入：视频链接/ID；抖音额外支持 作者模式（主页链接/sec_uid 抓最新 N 个视频）
- 筛选：关键词（评论文本）/ 昵称 / 带图 / 最低点赞 / 排序（默认·点赞·时间）
- 导出：🖼 评论区长图（PIL 绘制，含头像与评论图片）/ 💾 原始数据（json+txt+图片）
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from toolbox import bili_comments as bc
from toolbox import douyin_comments as dc
from toolbox import weibo_comments as wc

_PLATFORMS = [
    ("douyin", "📱 抖音"),
    ("bili", "📺 B站"),
    ("weibo", "🔴 微博"),
]
_PLATFORM_NAMES = dict(_PLATFORMS)
# 导出目录的平台分层名（评论区/{平台}/{视频ID}/）
_PLATFORM_DIRS = {"douyin": "抖音", "bili": "B站", "weibo": "微博"}
_BACKENDS = {"douyin": dc, "bili": bc, "weibo": wc}
# 平台是否支持作者模式（输入作者主页链接抓最新 N 个视频）
_AUTHOR_OK = {"douyin": True, "bili": False, "weibo": False}

_IMAGE_MODES = [("all", "全部"), ("has", "🖼 仅带图"), ("text", "✏️ 仅纯文字")]
_SORT_MODES = [("default", "默认"), ("likes", "♥ 点赞"), ("time", "🕘 时间")]
_IMAGE_NAMES = dict(_IMAGE_MODES)
_SORT_NAMES = dict(_SORT_MODES)

_PLACEHOLDERS = {
    ("douyin", False): "抖音视频链接 / aweme_id",
    ("douyin", True): "作者主页链接 / sec_uid（抓最新 N 个视频的评论）",
    ("bili", False): "B站视频链接 / BV号 / av号 / 动态链接（t.bilibili.com、opus）",
    ("weibo", False): "微博视频链接（tv/show） / 普通微博链接 / 视频号 1034:xxx",
}


class VideoCommentsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.videos = []          # [{id, desc, comments}]
        self.current_index = -1
        self._stop = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- 顶部：输入区 ----
        top = QFrame()
        top.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(6)

        row1 = QHBoxLayout()
        self.platform_combo = QComboBox()
        for key, name in _PLATFORMS:
            self.platform_combo.addItem(name, key)
        self.platform_combo.currentIndexChanged.connect(self._platform_changed)
        row1.addWidget(self.platform_combo)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["🎬 单视频", "👤 作者模式"])
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        row1.addWidget(self.mode_combo)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(_PLACEHOLDERS[("douyin", False)])
        self.input_edit.returnPressed.connect(self.start_fetch)
        row1.addWidget(self.input_edit, 1)
        self.video_count_spin = QSpinBox()
        self.video_count_spin.setRange(1, 10)
        self.video_count_spin.setValue(3)
        self.video_count_spin.setToolTip("作者模式：抓最新几个视频的评论（仅抖音）")
        row1.addWidget(QLabel("视频数"))
        row1.addWidget(self.video_count_spin)
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(1, 50)
        self.pages_spin.setValue(5)
        self.pages_spin.setToolTip("每个视频抓取的评论页数（抖音/B站每页 20 条，微博每页 10 条）")
        row1.addWidget(QLabel("页数"))
        row1.addWidget(self.pages_spin)
        self.fetch_btn = QPushButton("🔍 抓取评论")
        self.fetch_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.fetch_btn.clicked.connect(self.start_fetch)
        row1.addWidget(self.fetch_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._do_stop)
        row1.addWidget(self.stop_btn)
        tv.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("视频："))
        self.video_combo = QComboBox()
        self.video_combo.setMinimumWidth(320)
        self.video_combo.currentIndexChanged.connect(self._video_switched)
        row2.addWidget(self.video_combo, 1)
        self.export_btn = QPushButton("🖼 导出长图")
        self.export_btn.setToolTip("当前视频筛选后的评论导出为 PNG 长图（含头像与评论图片）")
        self.export_btn.setStyleSheet(
            "background: #1565c0; color: white; padding: 5px 14px; border-radius: 4px;")
        self.export_btn.clicked.connect(self.export_png)
        row2.addWidget(self.export_btn)
        self.save_btn = QPushButton("💾 保存原始数据")
        self.save_btn.setToolTip("保存 comments.json（原始数据）/ comments.txt / 图片原图")
        self.save_btn.clicked.connect(self.save_raw)
        row2.addWidget(self.save_btn)
        tv.addLayout(row2)
        layout.addWidget(top)

        # ---- 筛选栏 ----
        flt = QFrame()
        flt.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        fv = QHBoxLayout(flt)
        fv.setContentsMargins(10, 6, 10, 6)
        fv.setSpacing(6)
        fv.addWidget(QLabel("🔍 搜索"))
        self.kw_edit = QLineEdit()
        self.kw_edit.setPlaceholderText("评论文本关键词")
        self.kw_edit.setFixedWidth(150)
        self.kw_edit.textChanged.connect(self.apply_filter)
        fv.addWidget(self.kw_edit)
        fv.addWidget(QLabel("👤"))
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("昵称")
        self.user_edit.setFixedWidth(110)
        self.user_edit.textChanged.connect(self.apply_filter)
        fv.addWidget(self.user_edit)
        self.image_combo = QComboBox()
        for v, name in _IMAGE_MODES:
            self.image_combo.addItem(name, v)
        self.image_combo.currentIndexChanged.connect(self.apply_filter)
        fv.addWidget(self.image_combo)
        fv.addWidget(QLabel("♥ ≥"))
        self.likes_spin = QSpinBox()
        self.likes_spin.setRange(0, 999999)
        self.likes_spin.valueChanged.connect(self.apply_filter)
        fv.addWidget(self.likes_spin)
        fv.addWidget(QLabel("排序"))
        self.sort_combo = QComboBox()
        for v, name in _SORT_MODES:
            self.sort_combo.addItem(name, v)
        self.sort_combo.currentIndexChanged.connect(self.apply_filter)
        fv.addWidget(self.sort_combo)
        reset_btn = QPushButton("↺ 重置")
        reset_btn.clicked.connect(self.reset_filter)
        fv.addWidget(reset_btn)
        fv.addStretch(1)
        self.status_label = QLabel("选择平台，输入视频链接开始")
        self.status_label.setStyleSheet("color: #666;")
        fv.addWidget(self.status_label)
        layout.addWidget(flt)

        # ---- 评论表格 ----
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["用户", "评论内容", "♥", "图", "时间"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col, w in ((2, 60), (3, 45), (4, 110)):
            self.table.horizontalHeader().resizeSection(col, w)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)

    # ===================== 平台 / 模式 / 状态 =====================

    def _platform(self) -> str:
        return self.platform_combo.currentData() or "douyin"

    def _be(self):
        return _BACKENDS[self._platform()]

    def _platform_changed(self, *_):
        if not _AUTHOR_OK[self._platform()] and self.mode_combo.currentIndex() == 1:
            self.mode_combo.setCurrentIndex(0)
        self._mode_changed(self.mode_combo.currentIndex())

    def _mode_changed(self, index):
        is_author = index == 1 and _AUTHOR_OK[self._platform()]
        self.video_count_spin.setEnabled(is_author)
        self.input_edit.setPlaceholderText(
            _PLACEHOLDERS[(self._platform(), is_author)])

    def _do_stop(self):
        self._stop = True

    def _set_busy(self, busy: bool, text: str = ""):
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
        be = self._be()
        raw = self.input_edit.text().strip()
        if not raw:
            self.status_label.setText("❌ 请先输入视频链接或作者主页链接")
            return
        kind, vid = be.parse_input(raw)
        if not kind:
            self.status_label.setText("❌ 无法识别输入（需视频链接/ID）")
            return
        self._stop = False
        self._set_busy(True, "⏳ 抓取中...")
        self.videos = []
        self.current_index = -1
        self.video_combo.clear()
        self.table.setRowCount(0)
        pages = self.pages_spin.value()
        is_author = self.mode_combo.currentIndex() == 1 and _AUTHOR_OK[self._platform()]
        count = self.video_count_spin.value() if is_author else 1

        def progress(msg, cur=0, total=0):
            self._say(f"⏳ {msg}")

        def work():
            # plan 元素: (fetch_id, desc, ctype)。ctype=None 用各后端默认
            # （视频路径）；仅 B 站 fetch_comments 支持 ctype（动态评论坐标）
            if kind == "sec_uid":
                self._say("⏳ 获取作者视频列表...")
                targets = be.fetch_author_videos(vid, count)
                if not targets:
                    raise RuntimeError("未获取到作者视频列表（链接无效或被风控拦截）")
                plan = [(vid2, desc, None) for vid2, desc in targets]
            elif kind == "dynamic":
                # B站动态（t.bilibili.com/{id} / opus/{id}）：详情预检拿权威
                # 评论坐标——图文/转发动态的评论挂相簿（type=1/11）或源动态下，
                # 只有纯文字动态才是 type=17 + 动态 ID 本身
                self._say("⏳ 校验动态...")
                vinfo = be.fetch_dynamic_info(vid)
                if not vinfo.get("exists") and not vinfo.get("unknown"):
                    raise RuntimeError(f"动态不存在（ID {vid}），请检查链接是否完整")
                if vinfo.get("unknown"):
                    self._say("⚠️ 动态预检被风控跳过，直接尝试抓取评论...")
                plan = [(vinfo.get("comment_id") or vid,
                         vinfo.get("desc") or "",
                         vinfo.get("comment_type") or 17)]
            else:
                # 预检视频存在性（风控误判时不阻断，直接尝试抓评论）
                self._say("⏳ 校验视频...")
                vinfo = be.fetch_video_info(vid)
                if not vinfo.get("exists") and not vinfo.get("unknown"):
                    raise RuntimeError(f"视频不存在（ID {vid}），请检查链接是否完整")
                if vinfo.get("unknown"):
                    self._say("⚠️ 视频预检被风控跳过，直接尝试抓取评论...")
                plan = [(vid, vinfo.get("desc") or "", None)]
            results = []
            for i, (fetch_id, desc, ctype) in enumerate(plan):
                if self._stop:
                    break
                self._say(f"⏳ 视频 {i + 1}/{len(plan)}：{desc or fetch_id}")
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
                self.status_label.setText(f"❌ {err}")
                return
            self.videos = result or []
            if not self.videos:
                self.status_label.setText("❌ 未获取到评论（视频不存在或接口受限）")
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
                    "⚠️ 0 条评论：该视频可能无评论，或被风控拦截"
                    "（稍后重试，或到 Cookie 管理配置该平台 Cookie）")
            else:
                self.status_label.setText(
                    f"✅ {plat} · 共 {len(self.videos)} 个视频 / {total} 条评论 / {n_img} 张图片")

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
            return
        be = self._be()
        comments = self._filtered_comments(v)
        self.table.setRowCount(len(comments))
        for i, c in enumerate(comments):
            n_img = len(be.image_urls(c))
            likes = be.comment_likes(c)
            items = [
                be.comment_user(c),
                be.comment_text(c).replace("\n", " "),
                str(likes) if likes else "",
                str(n_img) if n_img else "",
                be.comment_time(c),
            ]
            for col, text in enumerate(items):
                it = QTableWidgetItem(text)
                if col in (2, 3):
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, col, it)
        self.table.resizeRowsToContents()
        if v["comments"]:
            self.status_label.setText(
                f"筛选后 {len(comments)}/{len(v['comments'])} 条"
                + f" · 视频：{v['desc'] or v['id']}")

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
            self.status_label.setText("❌ 请先抓取评论")
            return
        comments = self._filtered_comments(v)
        if not comments:
            self.status_label.setText("❌ 筛选后没有评论可导出")
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
        self._set_busy(True, "⏳ 导出长图中（下载头像与图片中）...")

        def progress(msg, cur=0, total=0):
            self._say(f"⏳ {msg}")

        def work():
            return self._be().export_comments_png(comments, meta, out, progress=progress)

        def done(path, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 导出失败：{err}")
                return
            self.status_label.setText(f"✅ 长图已导出（{len(comments)} 条评论）")
            self.app.toast(f"评论区长图已导出：{path}", "success")
            try:
                os.startfile(str(path))
            except Exception:
                pass

        run_bg(work, done)

    def save_raw(self):
        v = self._current_video()
        if not v:
            self.status_label.setText("❌ 请先抓取评论")
            return
        out = self._layered_dir(v["id"])
        self._set_busy(True, "⏳ 保存原始数据中（下载评论图片）...")
        # 下载浮窗（主线程创建；评论图片 4 线程并发下载）
        from gui_qt.download_float import get_float
        card = get_float().add_batch("评论区图片")

        def progress(msg, cur=0, total=0):
            self._say(f"⏳ {msg}")

        def work():
            return self._be().save_raw(v["comments"], out, progress=progress,
                                       task_event=card.handle_event, threads=4)

        def done(stats, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 保存失败：{err}")
                card.finish(f"❌ 保存失败: {str(err)[:50]}")
                return
            self.status_label.setText(
                f"✅ 已保存 {stats['comments']} 条评论 / {stats['downloaded']}/{stats['images']} 张图片")
            card.finish(f"✅ 图片 {stats['downloaded']}/{stats['images']}")
            self.app.toast(
                f"评论区原始数据已保存：{stats['comments']} 条 / 图片 {stats['downloaded']}/{stats['images']}",
                "success")
            try:
                os.startfile(str(out))
            except Exception:
                pass

        run_bg(work, done)
