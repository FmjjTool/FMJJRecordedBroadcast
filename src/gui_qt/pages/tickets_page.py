"""票务页（PySide6 版）：大麦 / 秀动 / B站会员购 演出解析。

- 输入票务链接（艺人页/演出页/搜索页）或关键词 → 演出列表
- 左列表选中演出 → 右侧详情（含场次、票档、购票须知）
- 「获取详情」对选中演出拉完整场次票档（大麦详情/秀动事件/B站项目）
- 勾选演出（全选/反选/清除）后「导出列表」为全量 JSON（未拉详情的自动逐场拉取）
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from gui_qt.common import first_number, post_to_main, run_bg, stable_sort
from gui_qt.pages.base import BasePage

import tickets as tickets_mod

COLS = ["选", "标题", "平台", "城市", "时间", "票价", "状态"]


class TicketsPage(BasePage):
    # 输入框留空直接回车时默认搜索的关键词
    DEFAULT_KEYWORD = "封茗囧菌"

    def __init__(self, app):
        super().__init__(app)
        self._events = []          # 当前演出列表（ShowEvent）
        self._events_original = [] # 解析时的原始顺序（排序三态回退用）
        self._detail_cache = {}    # event key → detail dict（获取过详情的）
        self._filling = False      # 表格填充中（抑制勾选计数刷新）
        self._sort_col = None      # 当前排序列（None=原始顺序）
        self._sort_state = 0       # 0=原顺序 1=A-Z 2=Z-A

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🎫 票务解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 票务解析 · 功能介绍", (
            "<p><b>大麦 / 秀动 / B站会员购 三平台演出检索与详情导出。</b></p>"
            "<p>· <b>搜索</b>：粘贴平台链接（大麦艺人/演出、秀动艺人/事件、B站会员购搜索/项目），"
            "或直接输入艺人名/演出名/场馆关键词——留空回车默认搜「封茗囧菌」，"
            "三平台同时搜索（大麦走真实浏览器约需 20 秒）</p>"
            "<p>· <b>表格</b>：勾选（全选/反选/清除）后点「获取选中演出详情」逐场拉取，"
            "并下载图片（封面/海报/宣传图/座位图/嘉宾头像/场馆图，存 download/票务演出/）</p>"
            "<p>· <b>💾 导出列表</b>：输出全量 JSON（未拉详情的自动逐场拉取+下载图片）</p>"
            "<p>· 单件失败自动原地重试，不中断整批；导出时按勾选逐件处理并显示进度</p>"
            "<p>· 采集链路游客优先，降低风控风险</p>")))
        layout.addLayout(title_row)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText(
            "留空直接回车 = 搜索「封茗囧菌」· 或输入 damai/showstart/bilibili 链接或任意关键词")
        self.url_entry.returnPressed.connect(self._parse)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        self.detail_btn = QPushButton("🎫 获取选中演出详情")
        self.detail_btn.setEnabled(False)
        self.detail_btn.setToolTip(
            "勾选了演出→逐场拉取详情+下载图片（封面/海报/宣传图/座位图/嘉宾头像/"
            "场馆图，存 download/票务演出/）；未勾选→仅当前选中行")
        self.detail_btn.clicked.connect(self._fetch_detail)
        row.addWidget(self.detail_btn)
        self.export_btn = QPushButton("💾 导出列表")
        self.export_btn.setEnabled(False)
        self.export_btn.setToolTip(
            "导出勾选的演出为全量 JSON（场次/票档/须知/嘉宾明细/图片本地路径）；"
            "未拉详情的自动逐场拉取并下载图片")
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.export_btn)
        layout.addLayout(row)

        # 勾选工具行：全选 / 反选 / 清除 + 已勾选计数（多选导出用）
        sel_row = QHBoxLayout()
        self.sel_all_btn = QPushButton("☑ 全选")
        self.sel_all_btn.setToolTip("勾选全部演出")
        self.sel_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        sel_row.addWidget(self.sel_all_btn)
        self.sel_invert_btn = QPushButton("⇄ 反选")
        self.sel_invert_btn.setToolTip("反转每行勾选状态")
        self.sel_invert_btn.clicked.connect(self._invert_checks)
        sel_row.addWidget(self.sel_invert_btn)
        self.sel_clear_btn = QPushButton("✖ 清除")
        self.sel_clear_btn.setToolTip("清除全部勾选")
        self.sel_clear_btn.clicked.connect(lambda: self._set_all_checks(False))
        sel_row.addWidget(self.sel_clear_btn)
        self.check_count_label = QLabel("已勾选 0 场")
        self.check_count_label.setStyleSheet("color: #888;")
        sel_row.addWidget(self.check_count_label)
        sel_row.addStretch(1)
        layout.addLayout(sel_row)

        # 左：演出列表 / 右：详情
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 36)   # 勾选列
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_select)
        # 点击表头排序：A-Z → Z-A → 原顺序（勾选与选中行保留）
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        splitter.addWidget(self.table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("演出详情"))
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText("选中左侧演出查看详情；点「获取选中演出详情」拉场次/票档")
        rv.addWidget(self.detail_text, 1)
        self.link_btn = QPushButton("🌐 打开演出页面")
        self.link_btn.setEnabled(False)
        self.link_btn.clicked.connect(self._open_link)
        rv.addWidget(self.link_btn)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    # ==================== 解析列表 ====================

    def _parse(self):
        url = self.url_entry.text().strip()
        if not url:
            # 留空直接回车 → 默认关键词
            self.url_entry.setText(self.DEFAULT_KEYWORD)
            url = self.DEFAULT_KEYWORD
        self.parse_btn.setEnabled(False)
        self.status_label.setText("⏳ 解析中...")

        def work():
            # URL 优先；识别不出但像纯关键词 → 三平台关键词搜索
            # （B站 API + 大麦 Playwright 游客搜索 + 秀动 HTTP SSR）
            kind, _ = tickets_mod.detect_ticket_platform(url)
            if kind:
                return tickets_mod.parse_ticket_url(url)
            # 纯关键词（不包含协议/path/数字 ID 之类）
            looks_like_url = ("://" in url or "/" in url or any(c.isdigit() for c in url[:5]))
            if looks_like_url:
                # 但 detect 没识别，给个友好提示
                raise ValueError("无法识别的票务链接（支持大麦艺人/演出、秀动艺人/事件、B站会员购搜索/项目）")
            events, diag = tickets_mod.search_tickets_by_keyword(url)
            return events, diag

        def done(result, err):
            self.parse_btn.setEnabled(True)
            if err:
                self.status_label.setText("")
                self.app.toast(f"解析失败: {str(err)[:80]}", "error")
                return
            # 兼容两种返回：list 或 (list, diag)
            if isinstance(result, tuple):
                self._events, diag = result
            else:
                self._events, diag = result, {}
            self._events_original = list(self._events)
            self._sort_col = None
            self._sort_state = 0
            self._detail_cache.clear()
            self._fill_table()
            tip = ""
            if diag:
                nice = []
                for p, v in diag.items():
                    if isinstance(v, int) and v > 0:
                        nice.append(f"{p}={v}")
                    elif isinstance(v, str):
                        nice.append(f"{p}: {v}")
                if nice:
                    tip = "｜" + " ".join(nice)
            self.status_label.setText(
                f"✅ 解析到 {len(self._events)} 场演出{tip}")
            self.export_btn.setEnabled(bool(self._events))

        run_bg(work, done)

    def _fill_table(self):
        self._filling = True
        self.table.setRowCount(0)
        for ev in self._events:
            row = self.table.rowCount()
            self.table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                         | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk)
            self.table.setItem(row, 1, QTableWidgetItem(ev.title or "-"))
            self.table.setItem(row, 2, QTableWidgetItem(ev.platform_cn or ev.platform))
            self.table.setItem(row, 3, QTableWidgetItem(ev.city or ""))
            self.table.setItem(row, 4, QTableWidgetItem(ev.show_time or ""))
            self.table.setItem(row, 5, QTableWidgetItem(ev.price_range or ""))
            self.table.setItem(row, 6, QTableWidgetItem(ev.status or ""))
        self._filling = False
        self._update_check_count()

    # ==================== 勾选（多选导出） ====================

    def _on_item_changed(self, item):
        """勾选状态变化 → 更新计数（填充表格时忽略）。"""
        if not self._filling and item.column() == 0:
            self._update_check_count()

    def _row_checked(self, row: int) -> bool:
        it = self.table.item(row, 0)
        return bool(it and it.checkState() == Qt.Checked)

    def _checked_rows(self) -> list:
        return [r for r in range(self.table.rowCount()) if self._row_checked(r)]

    def _set_all_checks(self, checked: bool):
        self._filling = True
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(state)
        self._filling = False
        self._update_check_count()

    def _invert_checks(self):
        self._filling = True
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked
                                 else Qt.Checked)
        self._filling = False
        self._update_check_count()

    def _update_check_count(self):
        n = len(self._checked_rows())
        self.check_count_label.setText(f"已勾选 {n} 场")

    # ==================== 表头排序（A-Z / Z-A / 原顺序） ====================

    _EVENT_KEYS = {
        1: lambda ev: (ev.title or "").lower(),
        2: lambda ev: (ev.platform_cn or ev.platform or "").lower(),
        3: lambda ev: (ev.city or "").lower(),
        4: lambda ev: (ev.show_time or ""),
        5: lambda ev: first_number(ev.price_range),
        6: lambda ev: (ev.status or "").lower(),
    }

    @staticmethod
    def _event_key(ev):
        return (ev.platform, ev.event_id)

    def _on_header_clicked(self, col):
        """点击表头循环切换：A-Z → Z-A → 原顺序；勾选与选中行保留。"""
        if col == 0 or not self._events:
            return
        # 先按当前顺序记录勾选/选中的 key（随后 _events 顺序改变，行号会错位）
        checked = {self._event_key(self._events[r]) for r in self._checked_rows()}
        sel_rows = self.table.selectionModel().selectedRows()
        sel_key = (self._event_key(self._events[sel_rows[0].row()])
                   if sel_rows and sel_rows[0].row() < len(self._events) else None)
        if self._sort_col != col:
            self._sort_col, self._sort_state = col, 1
        else:
            self._sort_state = (self._sort_state + 1) % 3
        header = self.table.horizontalHeader()
        if self._sort_state == 0:
            self._events = list(self._events_original)
            header.setSortIndicatorShown(False)
        else:
            key = self._EVENT_KEYS.get(col)
            if key:
                stable_sort(self._events, key, desc=(self._sort_state == 2))
            header.setSortIndicatorShown(True)
            header.setSortIndicator(
                col, Qt.DescendingOrder if self._sort_state == 2 else Qt.AscendingOrder)
        self._refill_preserving(checked, sel_key)

    def _refill_preserving(self, checked, sel_key):
        """按当前 self._events 重填表格，恢复勾选状态与选中行（key 在排序前采集）。"""
        self._fill_table()
        if checked:
            self._filling = True
            for r, ev in enumerate(self._events):
                if self._event_key(ev) in checked:
                    self.table.item(r, 0).setCheckState(Qt.Checked)
            self._filling = False
            self._update_check_count()
        if sel_key:
            for r, ev in enumerate(self._events):
                if self._event_key(ev) == sel_key:
                    self.table.selectRow(r)
                    break

    # ==================== 详情 ====================

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        has = bool(rows)
        self.detail_btn.setEnabled(has)
        self.link_btn.setEnabled(has)
        if not has:
            return
        ev = self._events[rows[0].row()]
        self.detail_text.setPlainText(self._format_detail(ev, None))
        self.link_btn.setEnabled(bool(ev.url))

    @staticmethod
    def _parse_one(ev):
        """单场演出 → ShowEvent 详情对象（按平台分发）。"""
        if ev.platform == "damai":
            return tickets_mod.parse_damai_item(ev.event_id)
        if ev.platform == "showstart":
            return tickets_mod.parse_showstart_event(ev.event_id)
        if ev.platform == "bilibili_show":
            return tickets_mod.parse_bilibili_project(ev.event_id)
        raise RuntimeError(f"平台 {ev.platform_cn} 暂不支持详情获取")

    def _fetch_detail(self):
        """「获取选中演出详情」：勾选了演出→逐场拉详情+下载图片；
        未勾选→仅当前选中行。已拉过的（缓存）自动跳过。"""
        if not self._events:
            return
        checked = self._checked_rows()
        if checked:
            rows = sorted(set(checked))
        else:
            rows = [r.row() for r in self.table.selectionModel().selectedRows()]
        rows = [r for r in rows if 0 <= r < len(self._events)]
        if not rows:
            return
        todo = [self._events[r] for r in rows
                if f"{self._events[r].platform}:{self._events[r].event_id}"
                not in self._detail_cache]
        if not todo:
            self._show_cached_or(rows)
            return
        total = len(todo)
        self.detail_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_label.setText(f"⏳ 获取 {total} 场演出详情（含图片下载）...")

        def work():
            import time
            from toolbox.config_manager import get_export_dir
            base = get_export_dir() / "票务演出"
            done_items, failed = [], []
            for n, ev in enumerate(todo, 1):
                self._log(f"详情进度 {n}/{total}：{ev.title[:26]}")
                if n > 1:
                    time.sleep(1.5)   # 件间隔，降低风控概率
                for attempt in (1, 2):
                    try:
                        obj = self._parse_one(ev)
                        tickets_mod.download_event_images(obj, base, log=self._log)
                        done_items.append(obj)
                        break
                    except Exception as e:
                        if attempt == 1:
                            self._log(f"  {ev.title[:16]} 失败，5 秒后重试...")
                            time.sleep(5)
                        else:
                            failed.append((ev.title, str(e)[:60]))
            return done_items, failed

        def done(result, err):
            self.parse_btn.setEnabled(True)
            self.export_btn.setEnabled(bool(self._events))
            self.detail_btn.setEnabled(
                bool(self.table.selectionModel().selectedRows()))
            if err:
                self.status_label.setText("")
                self.app.toast(f"详情获取失败: {str(err)[:80]}", "error")
                return
            done_items, failed = result
            for obj in done_items:
                self._detail_cache[f"{obj.platform}:{obj.event_id}"] = obj.to_dict()
            self._show_cached_or([r for r in rows])
            tip = f"，{len(failed)} 场失败" if failed else ""
            self.status_label.setText(
                f"✅ 已获取 {len(done_items)} 场详情并下载图片{tip}"
                f"（download/票务演出/，每场一个目录）")

        run_bg(work, done)

    def _show_cached_or(self, rows):
        """显示当前选中行（无选中则 rows 首行）的已缓存详情。"""
        show = None
        ev = None
        sel = self.table.selectionModel().selectedRows()
        if sel and 0 <= sel[0].row() < len(self._events):
            ev = self._events[sel[0].row()]
        if ev is None and rows and 0 <= rows[0] < len(self._events):
            ev = self._events[rows[0]]
        if ev:
            show = self._detail_cache.get(f"{ev.platform}:{ev.event_id}")
        if show is not None:
            self.detail_text.setPlainText(self._format_detail(ev, show))

    def _format_detail(self, ev, detail: dict) -> str:
        """演出 → 可读文本（detail 为获取详情后的完整 dict）。"""
        d = detail or ev.to_dict()
        lines = []
        order = ["平台", "标题", "副标题", "艺人", "城市", "场馆", "地址", "经纬度",
                 "时间", "演出时长", "开售时间", "票价", "状态", "想看", "想看人数",
                 "分类", "限购", "实名制", "演出时长", "主办方", "联系方式", "退票政策",
                 "销售标签", "项目标签", "关键词"]
        for k in order:
            if d.get(k):
                lines.append(f"{k}：{d[k]}")
        # 海报 / 场馆图
        for k in ("海报", "场馆图", "本地图片目录"):
            if d.get(k):
                lines.append(f"{k}：{d[k]}")
        # 演出介绍
        desc_text = d.get("演出介绍") or ""
        if desc_text:
            lines.append("")
            lines.append("═══ 演出介绍 ═══")
            text = desc_text[:1500] + ("…" if len(desc_text) > 1500 else "")
            lines.append(text)
        # 宣传图列表
        images = d.get("宣传图") or []
        if images:
            lines.append("")
            lines.append(f"═══ 宣传图（{len(images)} 张）═══")
            for i, u in enumerate(images[:10], 1):
                lines.append(f"  [{i}] {u}")
        # 座位图
        seat_images = d.get("座位图") or []
        if seat_images:
            lines.append("")
            lines.append(f"═══ 座位图（{len(seat_images)} 张）═══")
            for i, u in enumerate(seat_images[:5], 1):
                lines.append(f"  [{i}] {u}")
        # 购票须知（明细）
        notice_items = d.get("购票须知") or []
        if notice_items:
            lines.append("")
            lines.append(f"═══ 购票须知（{len(notice_items)} 条）═══")
            for n in notice_items:
                if isinstance(n, dict):
                    lines.append(f"【{n.get('标题','')}】")
                    lines.append(n.get("内容", "")[:300])
                    lines.append("")
                else:
                    lines.append(f"• {n}")
        # 常见问题
        faqs = d.get("常见问题") or []
        if faqs:
            lines.append("")
            lines.append(f"═══ 常见问题（{len(faqs)} 条）═══")
            for i, q in enumerate(faqs[:20], 1):
                lines.append(f"  Q{i}. {q}")
        # 标签
        tags = d.get("标签") or []
        if tags:
            lines.append("")
            lines.append(f"═══ 标签 ═══")
            lines.append(" / ".join(tags[:15]))
        # 嘉宾明细（头像+简介）
        guests = d.get("嘉宾明细") or []
        if guests:
            lines.append("")
            lines.append(f"═══ 嘉宾（{len(guests)} 位）═══")
            for g in guests[:30]:
                parts = [g.get("名字") or ""]
                if g.get("标签"):
                    parts.append(g.get("标签"))
                line = " / ".join(x for x in parts if x)
                if g.get("简介"):
                    intro = g["简介"]
                    line += f"：{intro[:60]}{'…' if len(intro) > 60 else ''}"
                lines.append(f"  · {line}")
            if len(guests) > 30:
                lines.append(f"  （还有 {len(guests) - 30} 位…）")
        # 场次明细
        sessions = d.get("场次明细") or []
        if sessions:
            lines.append("")
            lines.append("═══ 场次与票档 ═══")
            for i, s in enumerate(sessions, 1):
                name = s.get("场次") or f"场次{i}"
                st = s.get("开始时间") or ""
                lines.append(f"【{name}】{st}")
                tiers = s.get("票档") or []
                if not tiers:
                    lines.append("  （无票档数据）")
                for t in tiers:
                    parts = [t.get("名称") or "", t.get("价格") or ""]
                    if t.get("状态"):
                        parts.append(t["状态"])
                    if t.get("说明"):
                        parts.append(t["说明"])
                    lines.append("  · " + " | ".join(x for x in parts if x))
        # 其余 extra 字段
        known = set(order) | {"场次明细", "演出介绍", "演出介绍HTML", "宣传图",
                              "座位图", "购票须知", "常见问题", "标签", "嘉宾明细",
                              "海报", "场馆图", "本地图片目录",
                              "演出ID", "链接", "封面", "获取时间"}
        extras = []
        for k, v in d.items():
            if k not in known and v and k not in ("演出介绍HTML",):
                extras.append((k, v))
        if extras:
            lines.append("")
            lines.append("═══ 其他信息 ═══")
            for k, v in extras:
                v_str = str(v)
                if len(v_str) > 200:
                    v_str = v_str[:200] + "…"
                lines.append(f"{k}：{v_str}")
        if d.get("链接"):
            lines.append("")
            lines.append(f"链接：{d['链接']}")
        return "\n".join(lines)

    # ==================== 导出 / 打开 ====================

    def _open_link(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        ev = self._events[rows[0].row()]
        detail = self._detail_cache.get(f"{ev.platform}:{ev.event_id}") or {}
        url = detail.get("链接") or ev.url
        if not url:
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl as QU
        QDesktopServices.openUrl(QU(url))

    def _export(self):
        """多选导出 JSON：勾选的演出逐场拉全量详情，一次导出。"""
        if not self._events:
            return
        checked = self._checked_rows()
        if not checked:
            self.app.toast("请先勾选要导出的演出（表格第一列复选框），或点「☑ 全选」",
                           "warning")
            return
        total = len(checked)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出演出 JSON（全量）",
            f"票务演出_已选{total}场.json", "JSON (*.json)")
        if not path:
            return
        self.export_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.detail_btn.setEnabled(False)

        def work():
            import time
            from toolbox.config_manager import get_export_dir
            base = get_export_dir() / "票务演出"
            data, failed = [], []
            for n, r in enumerate(checked, 1):
                ev = self._events[r]
                self._log(f"导出进度 {n}/{total}：{ev.title[:26]}")
                if n > 1:
                    time.sleep(1.5)   # 件间隔，降低风控概率
                d = ev.to_dict()
                key = f"{ev.platform}:{ev.event_id}"
                detail = self._detail_cache.get(key)
                if detail is None:
                    for attempt in (1, 2):
                        try:
                            obj = self._parse_one(ev)
                            tickets_mod.download_event_images(
                                obj, base, log=self._log)
                            detail = obj.to_dict()
                            self._detail_cache[key] = detail
                            break
                        except Exception as e:
                            detail = None
                            err = str(e)[:60]
                            if attempt == 1:
                                self._log(f"  {ev.title[:16]} 失败，5 秒后重试...")
                                time.sleep(5)
                            else:
                                failed.append(f"{ev.title[:20]}：{err}")
                if detail:
                    d.update({k: v for k, v in detail.items()
                              if k not in ("获取时间",) and v})
                data.append(d)
            return data, failed

        def done(result, err):
            self.export_btn.setEnabled(True)
            self.parse_btn.setEnabled(True)
            self.detail_btn.setEnabled(
                bool(self.table.selectionModel().selectedRows()))
            if err:
                self.status_label.setText("")
                self.app.toast(f"导出失败: {str(err)[:80]}", "error")
                return
            data, failed = result
            n_export = len(data) if isinstance(data, list) else len(data.get("演出", []))
            if failed:
                data = {"演出": data, "导出告警": failed}
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self.app.toast(f"导出失败: {str(e)[:80]}", "error")
                return
            tip = f"，{len(failed)} 场详情获取失败" if failed else ""
            self.status_label.setText(
                f"✅ 已导出 {n_export} 场演出全量 JSON{tip}到 {path}")

        run_bg(work, done)

    def _log(self, msg):
        def apply():
            self.status_label.setText(f"⏳ {msg}")
        try:
            post_to_main(apply)
        except Exception:
            pass
