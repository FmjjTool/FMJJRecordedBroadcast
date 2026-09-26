"""网媒账号追踪页（PySide6 版）：单账号单平台，每个账号独立卡片，采集结果存 xlsx 并读取长期缓存。

对应原版 gui/tracker_page.py：
- 功能页：平台筛选 + 获取全部（回车快捷）+ ⏰ 定时采集（状态圆点）+ 📷 按组导出 + 刷新
  每账号卡片：昵称/平台/状态 + 采集/导出/历史按钮 + 变化提示（涨绿跌红）+ 2行×4列预览
- 设置页：添加追踪账号（账号名/平台/链接/分组多选）、分组管理（组列表+新建）、已配置账号（分组多选+保存+删除）
- 历史窗口：指标当列、时间点当行，可横向滚动
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QProgressBar, QScrollArea,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import icon_btn_style, icon_button, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import GroupMultiSelect

# 指标行定义：(显示名, [字段名优先级列表])
METRICS = [
    ("昵称", ["name"]),
    ("粉丝数", ["fans"]),
    ("关注数", ["following"]),
    ("获赞数", ["likes"]),
    ("内容数", ["video_count", "weibo_count"]),
    ("简介", ["sign"]),
    ("认证", ["official_title", "verified_reason", "verified"]),
    ("等级", ["level"]),
]

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博",
                  "kuaishou": "快手", "xiaohongshu": "小红书"}
_NUM_FIELDS = {"fans", "following", "likes", "video_count", "weibo_count", "level"}


def _cell(rec, fields):
    for f in fields:
        v = rec.get(f)
        if v is None or v == "":
            continue
        if f == "verified":
            return "已认证" if v else "—"
        if f in _NUM_FIELDS:
            try:
                return f"{int(float(v)):,}"
            except (ValueError, TypeError):
                return str(v)
        if f == "sign":
            s = str(v)
            return s if len(s) <= 32 else s[:32] + "…"
        return str(v)
    return "—"


class TrackerPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.cards = []
        self._timer_running = False
        self._timer_interval = 3600
        self._collecting = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sub = QTabWidget()
        layout.addWidget(self.sub)
        self.sub.addTab(self._build_func(), "功能")
        self.sub.addTab(self._build_settings(), "设置")
        self.reload_accounts()
        self._reload_group_list()

    # ---------- 功能页 ----------
    def _build_func(self) -> QWidget:
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("📈 网媒账号追踪")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self.reload_accounts)
        head.addWidget(refresh_btn)
        export_group_btn = QPushButton("📷 按组导出")
        export_group_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c8cdd6; color: #2f3440;")
        export_group_btn.clicked.connect(self._export_group)
        head.addWidget(export_group_btn)
        self.timer_dot = QLabel("○")
        self.timer_dot.setStyleSheet("color: #888; font-size: 15px;")
        head.addWidget(self.timer_dot)
        timer_btn = QPushButton("⏰ 定时采集")
        timer_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c8cdd6; color: #2f3440;")
        timer_btn.clicked.connect(self._toggle_collect_timer)
        head.addWidget(timer_btn)
        fetch_all_btn = QPushButton("⬇ 获取全部")
        fetch_all_btn.setStyleSheet("background: #2e7d32; color: white;")
        fetch_all_btn.clicked.connect(lambda: self._collect_all())
        head.addWidget(fetch_all_btn)
        self.batch_platform = QComboBox()
        self.batch_platform.addItems(["全部平台", "B站", "抖音", "微博", "快手", "小红书"])
        self.batch_platform.setFixedWidth(92)
        head.addWidget(self.batch_platform)
        # 批量采集进度条
        self.batch_prog = QProgressBar()
        self.batch_prog.setFixedWidth(150)
        self.batch_prog.setFormat("采集 %v/%m（%p%）")
        self.batch_prog.setRange(0, 1)
        self.batch_prog.setValue(0)
        head.addWidget(self.batch_prog)
        v.addLayout(head)

        # 卡片滚动区
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(4, 4, 4, 4)
        self.cards_layout.setSpacing(6)
        self.cards_layout.addStretch(1)
        self.cards_scroll.setWidget(self.cards_container)
        v.addWidget(self.cards_scroll, 1)
        return f

    # ---------- 设置页 ----------
    def _build_settings(self) -> QWidget:
        f = QWidget()
        outer = QVBoxLayout(f)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)
        title = QLabel("⚙️ 追踪账号设置")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        outer.addWidget(title)

        # 添加追踪账号（账号名 + 链接，可选归属分组）
        add_card = QFrame()
        add_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")
        av = QVBoxLayout(add_card)
        av.setContentsMargins(10, 10, 10, 10)
        av.setSpacing(4)
        ah = QLabel("添加追踪账号（账号名 + 链接地址）")
        ah.setStyleSheet("font-weight: bold;")
        av.addWidget(ah)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("账号名:"))
        self.add_name = QLineEdit()
        self.add_name.setPlaceholderText("如 封茗囧菌抖音（留空自动获取）")
        r1.addWidget(self.add_name, 1)
        r1.addWidget(QLabel("平台:"))
        self.add_platform = QComboBox()
        self.add_platform.addItems(["自动识别", "抖音", "B站", "微博", "快手", "小红书"])
        self.add_platform.setFixedWidth(100)
        r1.addWidget(self.add_platform)
        av.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("链接:"))
        self.add_url = QLineEdit()
        self.add_url.setPlaceholderText("账号主页链接（自动识别平台）")
        self.add_url.returnPressed.connect(self._add_account)
        r2.addWidget(self.add_url, 1)
        r2.addWidget(QLabel("分组:"))
        self.add_group_sel = GroupMultiSelect(width=126)
        r2.addWidget(self.add_group_sel)
        self.add_acc_btn = icon_button("➕", "添加追踪账号", (40, 30), True, "font-size: 16px;", self._add_account)
        r2.addWidget(self.add_acc_btn)
        av.addLayout(r2)
        outer.addWidget(add_card)

        # 分组管理
        group_card = QFrame()
        group_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")
        gv = QVBoxLayout(group_card)
        gv.setContentsMargins(10, 10, 10, 10)
        gv.setSpacing(4)
        gh = QLabel("分组管理（组编号 + 组名，支持按组导出）")
        gh.setStyleSheet("font-weight: bold;")
        gv.addWidget(gh)
        self.group_list_frame = QScrollArea()
        self.group_list_frame.setWidgetResizable(True)
        self.group_list_frame.setStyleSheet(
            "QScrollArea { background: transparent; border: 1px solid #e3e7ee; border-radius: 6px; }")
        gcontainer = QWidget()
        gcontainer.setStyleSheet("background: transparent;")
        self.group_list_layout = QVBoxLayout(gcontainer)
        self.group_list_layout.setContentsMargins(2, 2, 2, 2)
        self.group_list_layout.setSpacing(3)
        self.group_list_layout.addStretch(1)
        self.group_list_frame.setWidget(gcontainer)
        gv.addWidget(self.group_list_frame)
        addg = QHBoxLayout()
        self.gid_entry = QLineEdit()
        self.gid_entry.setPlaceholderText("组编号(11/22)")
        self.gid_entry.setFixedWidth(110)
        addg.addWidget(self.gid_entry)
        self.gname_entry = QLineEdit()
        self.gname_entry.setPlaceholderText("组名")
        self.gname_entry.setFixedWidth(140)
        addg.addWidget(self.gname_entry)
        newg_btn = icon_button("➕", "新建分组", (40, 30), True, "font-size: 16px;", self._add_group)
        addg.addWidget(newg_btn)
        addg.addStretch(1)
        gv.addLayout(addg)
        outer.addWidget(group_card)

        # 已配置账号
        list_card = QFrame()
        list_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")
        lv = QVBoxLayout(list_card)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(4)
        lh = QLabel("已配置账号")
        lh.setStyleSheet("font-weight: bold;")
        lv.addWidget(lh)
        self.acc_list_frame = QScrollArea()
        self.acc_list_frame.setWidgetResizable(True)
        acontainer = QWidget()
        self.acc_list_layout = QVBoxLayout(acontainer)
        self.acc_list_layout.setContentsMargins(0, 0, 0, 0)
        self.acc_list_layout.setSpacing(3)
        self.acc_list_layout.addStretch(1)
        self.acc_list_frame.setWidget(acontainer)
        lv.addWidget(self.acc_list_frame, 1)
        outer.addWidget(list_card, 1)
        return f

    # ---------- 账号管理 ----------
    def reload_accounts(self):
        self._clear_layout(self.cards_layout)
        self.cards_layout.addStretch(1)
        self.cards = []
        from tracker.accounts import load_accounts
        accounts = load_accounts()
        self._reload_acc_list()
        if not accounts:
            lbl = QLabel("（暂无账号，请在「设置」里添加）")
            lbl.setStyleSheet("color: gray;")
            lbl.setAlignment(Qt.AlignCenter)
            self.cards_layout.takeAt(self.cards_layout.count() - 1)
            self.cards_layout.addWidget(lbl)
            self.cards_layout.addStretch(1)
            return
        # 骨架屏：先渲染灰色占位卡（点进页面秒开），
        # 后台批量读各账号 xlsx 历史缓存（openpyxl 逐个读是卡顿根源），
        # 完成后回主线程一次性替换为真实卡片
        skeletons = [self._make_skeleton() for _ in accounts]
        self._reload_gen = getattr(self, "_reload_gen", 0) + 1
        gen = self._reload_gen

        def work():
            from tracker.accounts import load_history
            return [load_history(acc.get("name", "")) for acc in accounts]

        def done(histories, err):
            if gen != getattr(self, "_reload_gen", 0):
                return  # 期间又触发了 reload，本轮结果作废
            self._clear_layout(self.cards_layout)
            self.cards_layout.addStretch(1)
            if err:
                self.app.toast(f"读取历史缓存失败：{err}", "error")
                histories = None
            for i, acc in enumerate(accounts):
                history = histories[i] if histories else []
                self.cards.append(self._make_card(acc, history=history))

        run_bg(work, done)

    def _make_skeleton(self):
        """骨架占位卡：与真实卡片同结构的灰色块，历史数据加载完成后被真实卡片替换。"""
        sk = QFrame()
        sk.setFixedHeight(112)
        sk.setStyleSheet(
            "QFrame { background: #ffffff; border-radius: 8px; }"
            "QLabel { background: #dfe3ea; border-radius: 4px; }")
        v = QVBoxLayout(sk)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(3)
        name_bar = QLabel()
        name_bar.setFixedSize(150, 16)
        left.addWidget(name_bar)
        plat_bar = QLabel()
        plat_bar.setFixedSize(72, 11)
        left.addWidget(plat_bar)
        top.addLayout(left)
        top.addStretch(1)
        btn_bar = QLabel()
        btn_bar.setFixedSize(70, 24)
        top.addWidget(btn_bar)
        v.addLayout(top)
        grid_bar = QLabel()
        grid_bar.setFixedHeight(54)
        grid_bar.setStyleSheet("background: #e8ebf0; border-radius: 6px;")
        v.addWidget(grid_bar)
        self.cards_layout.takeAt(self.cards_layout.count() - 1)
        self.cards_layout.addWidget(sk)
        self.cards_layout.addStretch(1)
        return sk

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _make_card(self, acc, history=None):
        from tracker.accounts import parse_url, load_history
        card = QFrame()
        card.setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(8, 6, 8, 4)
        cv.setSpacing(2)

        platform = (acc.get("platform") or "").strip()
        if not platform:
            platform, _ = parse_url(acc.get("url", ""))
        plat_name = PLATFORM_NAMES.get(platform, platform or "未知")

        # 头部：昵称 + 平台 + 状态 + 按钮
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        name_lbl = QLabel(f"👤 {acc.get('name', '未命名')}")
        name_lbl.setStyleSheet("font-weight: bold;")
        left.addWidget(name_lbl)
        plat_lbl = QLabel(plat_name)
        plat_lbl.setStyleSheet("color: #1976d2; font-size: 11px;")
        left.addWidget(plat_lbl)
        head.addLayout(left)
        head.addStretch(1)
        status = QLabel("")
        status.setStyleSheet("color: #888;")
        head.addWidget(status)
        export_btn = QPushButton("📷 导出")
        export_btn.setFixedSize(60, 26)
        export_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c8cdd6; color: #2f3440;")
        export_btn.clicked.connect(lambda _=False, a=acc: self._export_png(a))
        head.addWidget(export_btn)
        hist_btn = QPushButton("📊 历史")
        hist_btn.setFixedSize(60, 26)
        hist_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c8cdd6; color: #2f3440;")
        hist_btn.clicked.connect(lambda _=False, a=acc: self._open_history(a))
        head.addWidget(hist_btn)
        btn = QPushButton("📥 采集")
        btn.setFixedSize(70, 26)
        btn.clicked.connect(lambda _=False, a=acc: self._collect_account(a))
        head.addWidget(btn)
        cv.addLayout(head)

        # 变化提示（涨绿跌红）
        diff_frame = QHBoxLayout()
        diff_frame.setSpacing(10)
        cv.addLayout(diff_frame)

        # 预览：2 行 × 4 列网格
        preview_frame = QWidget()
        preview_frame.setStyleSheet("background: #e8ebf0; border-radius: 6px;")
        # 双击预览区快捷打开完整历史窗口
        preview_frame.setCursor(Qt.PointingHandCursor)
        preview_frame.setToolTip("双击查看完整历史数据")
        preview_frame.mouseDoubleClickEvent = (
            lambda ev, a=acc: self._open_history(a)
            if ev.button() == Qt.LeftButton else None)
        grid = QGridLayout(preview_frame)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setSpacing(4)

        # 预览只显示「当前」+「前一次」（骨架加载路径已预读 history，避免重复读 xlsx）
        if history is None:
            history = load_history(acc.get("name", ""))
        preview = history[:2]
        self._render_preview(grid, preview)
        if history:
            status.setText("📦 缓存")
            if len(history) >= 2:
                self._render_diff(diff_frame, history[1], history[0])
        cv.addWidget(preview_frame)
        self.cards_layout.takeAt(self.cards_layout.count() - 1)
        self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)
        return {"account": acc, "preview": grid, "status": status,
                "btn": btn, "diff_frame": diff_frame}

    def _render_preview(self, grid, records):
        """2 行 × 4 列网格预览：每行 = 一个时间点，列 = [时间, 粉丝, 关注, 获赞]。"""
        self._clear_layout(grid)
        if not records:
            lbl = QLabel("（尚未采集）")
            lbl.setStyleSheet("color: gray;")
            grid.addWidget(lbl, 0, 0, 1, 4)
            return
        rows = [((records[0].get("_time") or "")[:16] or "记录1", records[0])]
        if len(records) >= 2:
            t = (records[1].get("_time") or "")[:16]
            rows.append((t or "记录2", records[1]))
        for r, (time_label, rec) in enumerate(rows):
            tl = QLabel(time_label)
            tl.setStyleSheet("color: #1976d2; font-weight: bold;")
            grid.addWidget(tl, r, 0)
            for c, field in enumerate(["fans", "following", "likes"]):
                vl = QLabel(_cell(rec, [field]))
                vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(vl, r, c + 1)

    def _diff_items(self, prev, curr):
        """对比前后两次采集，返回 [(文本, 颜色), ...] 列表（涨绿跌红）。"""
        items = []
        if curr.get("name") and prev.get("name") and curr["name"] != prev["name"]:
            items.append((f"🔄 更名 {prev['name']}→{curr['name']}", "#1976d2"))
        for label, field in [("粉丝", "fans"), ("关注", "following"), ("获赞", "likes")]:
            try:
                c = curr.get(field)
                p = prev.get(field)
                if c is None or c == "" or p is None or p == "":
                    continue
                c, p = int(float(c)), int(float(p))
                if c != p:
                    delta = c - p
                    arrow = "↑" if delta > 0 else "↓"
                    color = "#4caf50" if delta > 0 else "#ff5252"  # 涨绿跌红
                    items.append((f"{label}{arrow}{abs(delta)}", color))
            except (ValueError, TypeError):
                continue
        return items

    def _render_diff(self, frame, prev, curr):
        """在 frame（QHBoxLayout）里渲染变化提示（每项独立颜色）。"""
        self._clear_layout(frame)
        for text, color in self._diff_items(prev, curr):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
            frame.addWidget(lbl)
        frame.addStretch(1)

    def _export_png(self, acc):
        """导出该账号最近两次采集的变化对比 PNG（涨绿跌红）。"""
        from tracker.accounts import load_history
        history = load_history(acc.get("name", ""))
        if len(history) < 2:
            self.app.toast("该账号至少需要两次采集数据才能导出变化对比", "warning")
            return

        def work():
            from tracker.export_png import export_diff_png
            return export_diff_png(acc, history)

        def done(result, err):
            if err:
                self.app.toast(f"导出失败：{err}", "error")
            else:
                self.app.toast(f"✅ 已导出 PNG：{result}", "success")

        run_bg(work, done)

    def _open_history(self, acc):
        """打开二级窗口：完整历史数据（指标当列、时间点当行、可横向滚动）。"""
        from tracker.accounts import load_history
        history = load_history(acc.get("name", ""))
        if not history:
            self.app.toast("暂无历史数据", "info")
            return

        win = QDialog(self)
        win.setWindowTitle(f"📊 {acc.get('name', '账号')} · 历史数据")
        # QDialog 默认只有关闭按钮，补上最小化/最大化（双击标题栏全屏也生效）
        win.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        win.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        win.resize(980, 520)
        v = QVBoxLayout(win)
        v.setContentsMargins(10, 12, 10, 10)
        head = QLabel(f"👤 {acc.get('name', '')} 历史数据（共 {len(history)} 次采集，最新在前）")
        head.setStyleSheet("font-size: 14px; font-weight: bold;")
        v.addWidget(head)

        # 列 = 指标，行 = 时间点（"当前" 或 "2026-08-13 14:30"）
        table = QTableWidget(len(history), len(METRICS))
        table.setHorizontalHeaderLabels([name for name, _f in METRICS])
        table.verticalHeader().setVisible(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, rec in enumerate(history):
            t = (rec.get("_time") or "")[:16]  # 行标直接显示采集时间
            title = t if t else f"记录{i + 1}"
            ver = QTableWidgetItem(title)
            table.setVerticalHeaderItem(i, ver)
            for j, (_name, fields) in enumerate(METRICS):
                cell = QTableWidgetItem(_cell(rec, fields))
                # 与上一条（时间更早的一条）对比：数值涨绿跌红，附增减量
                delta = self._num_delta(history[i + 1], rec, fields) if i + 1 < len(history) else None
                if delta:
                    arrow = "↑" if delta > 0 else "↓"
                    cell.setText(f"{cell.text()} {arrow}{abs(delta)}")
                    cell.setForeground(QColor("#4caf50" if delta > 0 else "#ff5252"))
                table.setItem(i, j, cell)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        # 最后一列拉伸：最大化后不留右侧大片空白
        table.horizontalHeader().setStretchLastSection(True)
        hint = QLabel("颜色说明：与上一条采集对比，绿色=数值增加，红色=数值减少（↑/↓ 后为增减量）")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(hint)
        v.addWidget(table, 1)
        win.exec()

    @staticmethod
    def _num_delta(prev, curr, fields):
        """数值指标对比：返回 curr - prev（涨正跌负），任一侧缺失/非数值返回 None。"""
        def _val(rec):
            for f in fields:
                v = rec.get(f)
                if v is None or v == "":
                    continue
                try:
                    return int(float(v))
                except (ValueError, TypeError):
                    return None
            return None
        c, p = _val(curr), _val(prev)
        if c is None or p is None:
            return None
        return c - p

    def _card_for(self, acc):
        for c in self.cards:
            if c["account"] is acc or c["account"] == acc:
                return c
        return None

    def _collect_account(self, acc, on_done=None):
        card = self._card_for(acc)
        if not card:
            if on_done:
                on_done()
            return
        card["btn"].setEnabled(False)
        card["status"].setText("⏳ 采集中...")
        card["status"].setStyleSheet("color: #eab308;")

        def work():
            from tracker.accounts import collect_account, save_history
            record = collect_account(acc)
            save_history(acc.get("name", "未命名"), record)
            return record

        def done(result, err):
            card = self._card_for(acc)
            if card:
                card["btn"].setEnabled(True)
                if err:
                    card["status"].setText(f"❌ {err}")
                    card["status"].setStyleSheet("color: #ff5252;")
                else:
                    from tracker.accounts import load_history
                    history = load_history(acc.get("name", "未命名"))
                    self._render_preview(card["preview"], history[:2])
                    if len(history) >= 2:
                        self._render_diff(card["diff_frame"], history[1], history[0])
                    card["status"].setText("✅ 完成")
                    card["status"].setStyleSheet("color: #4caf50;")
            if on_done:
                on_done()

        run_bg(work, done)

    def _collect_all(self, silent=False):
        """批量采集：按平台筛选（全部/某平台），4 个一批并发采集。silent=True 用于定时采集。"""
        if self._collecting:
            if not silent:
                self.app.toast("正在采集中，请稍候", "info")
            return
        plat_map = {"B站": "bilibili", "抖音": "douyin", "微博": "weibo",
                    "快手": "kuaishou", "小红书": "xiaohongshu"}
        target_plat = plat_map.get(self.batch_platform.currentText(), "")
        accounts = [
            c["account"] for c in self.cards
            if not target_plat or (c["account"].get("platform") or "").strip() == target_plat
        ]
        if not accounts:
            if not silent:
                self.app.toast("没有符合条件的账号", "info")
            return
        if not silent:
            self.app.toast(f"⬇ 开始批量采集 {len(accounts)} 个账号（并发模式）...", "info")
        self.batch_prog.setRange(0, max(len(accounts), 1))
        self.batch_prog.setValue(0)

        # 并发波次：4 个一批（原串行逐个等待，账号多时总耗时=各账号之和）
        from collections import deque
        queue = deque(accounts)
        state = {"running": 0, "done": 0}
        total = len(accounts)

        def _next_wave():
            while queue and state["running"] < 4:
                acc = queue.popleft()
                state["running"] += 1
                self._collect_account(acc, on_done=_finish_one)

        def _finish_one():
            # run_bg 的 done 回调在主线程执行，此处补位安全
            state["running"] -= 1
            state["done"] += 1
            self.batch_prog.setValue(state["done"])
            if queue:
                _next_wave()
            elif state["running"] == 0:
                if not silent:
                    self.app.toast("✅ 批量采集完成", "info")
                elif not self.isVisible():
                    # 定时采集在后台跑完（用户已切到其他页）：提示仍在生效
                    self.app.toast(f"⏰ 定时采集已在后台完成（{total} 个账号）", "info")
                self._collecting = False

        self._collecting = True
        _next_wave()

    # ---------- 定时采集 ----------
    def _toggle_collect_timer(self):
        if self._timer_running:
            self._stop_collect_timer()
        else:
            self._start_collect_timer()

    def _start_collect_timer(self):
        win = QDialog(self)
        win.setWindowTitle("⏰ 定时采集")
        win.setFixedSize(320, 200)
        v = QVBoxLayout(win)
        v.setContentsMargins(16, 14, 16, 14)
        t1 = QLabel("设置定时采集间隔")
        t1.setStyleSheet("font-size: 14px; font-weight: bold;")
        t1.setAlignment(Qt.AlignCenter)
        v.addWidget(t1)
        t2 = QLabel("按当前「全部平台」筛选自动采集所有账号")
        t2.setStyleSheet("color: gray; font-size: 11px;")
        t2.setAlignment(Qt.AlignCenter)
        v.addWidget(t2)
        row = QHBoxLayout()
        row.addWidget(QLabel("间隔(分钟):"))
        entry = QLineEdit()
        entry.setText("60")
        entry.setFixedWidth(80)
        row.addWidget(entry)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        start_btn = QPushButton("▶ 启动")
        start_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px;")
        start_btn.clicked.connect(lambda: do_start())
        v.addWidget(start_btn)

        def do_start():
            try:
                m = int(entry.text().strip() or "60")
            except ValueError:
                m = 60
            if m < 1:
                self.app.toast("间隔不能小于 1 分钟", "warning")
                return
            self._timer_interval = m * 60
            self._timer_running = True
            win.accept()
            self.timer_dot.setText("●")
            self.timer_dot.setStyleSheet("color: #4caf50; font-size: 15px;")
            self.app.toast(f"⏰ 定时采集已启动（每 {m} 分钟）", "success")
            self._begin_timer_loop()

        win.exec()

    def _begin_timer_loop(self):
        """后台定时循环：daemon 线程独立于页面可见性运行（切走页面后仍按时采集）。"""
        import threading

        def loop():
            from gui_qt.common import post_to_main
            # 启动后先执行一次，之后每隔 interval 执行一次
            post_to_main(lambda: self._collect_all(silent=True))
            while self._timer_running:
                time.sleep(self._timer_interval)
                if not self._timer_running:
                    break
                post_to_main(lambda: self._collect_all(silent=True))

        threading.Thread(target=loop, daemon=True).start()

    def _stop_collect_timer(self):
        self._timer_running = False
        self.timer_dot.setText("○")
        self.timer_dot.setStyleSheet("color: #888; font-size: 15px;")
        self.app.toast("定时采集已停止", "info")

    # ---------- 设置页：账号增删 ----------
    def _add_account(self):
        name = self.add_name.text().strip()
        url = self.add_url.text().strip()
        if not url:
            self.app.toast("请输入账号链接", "warning")
            return
        if name:
            self._do_add_account(url, name)
        else:
            # 名称留空 → 自动获取「昵称+平台」（如 封茗囧菌抖音），失败回退「未命名」
            self.app.toast("名称留空，正在自动获取账号名...", "info")
            self.add_acc_btn.setEnabled(False)

            def work():
                from platforms.account_names import fetch_display_name
                return fetch_display_name(url)

            def done(result, err):
                self.add_acc_btn.setEnabled(True)
                auto_name, _ferr = result or ("", "")
                if not auto_name:
                    reason = str(err)[:60] if err else ""
                    self.app.toast(f"自动获取失败{('：' + reason) if reason else ''}，已按「未命名」添加", "warning")
                self._do_add_account(url, auto_name or "未命名")

            run_bg(work, done)

    def _do_add_account(self, url: str, name: str):
        from tracker.accounts import parse_url, load_accounts, save_accounts
        plat_map = {"自动识别": "", "抖音": "douyin", "B站": "bilibili", "微博": "weibo",
                    "快手": "kuaishou", "小红书": "xiaohongshu"}
        platform = plat_map.get(self.add_platform.currentText(), "")
        if not platform:
            platform, _ = parse_url(url)
        gids = self.add_group_sel.selected()
        acc = {"name": name, "url": url, "platform": platform or "", "group_ids": gids}
        accounts = load_accounts()
        accounts.append(acc)
        save_accounts(accounts)
        self.add_name.clear()
        self.add_url.clear()
        self.reload_accounts()

    def _delete_account(self, index):
        from tracker.accounts import load_accounts, save_accounts
        accounts = load_accounts()
        if 0 <= index < len(accounts):
            accounts.pop(index)
            save_accounts(accounts)
            self.reload_accounts()

    def _move_account(self, index, delta):
        """上移/下移已配置账号（顺序即采集顺序，保存后立即生效）。"""
        from tracker.accounts import move_account
        if move_account(index, delta):
            self.reload_accounts()

    def _reload_acc_list(self):
        self._clear_layout(self.acc_list_layout)
        self.acc_list_layout.addStretch(1)
        from tracker.accounts import load_accounts, get_groups
        accounts = load_accounts()
        groups = get_groups()
        self._acc_rows = []  # (index, 分组多选控件, 初始已选组列表)
        if not accounts:
            lbl = QLabel("（暂无账号，先在上方添加）")
            lbl.setStyleSheet("color: gray;")
            self.acc_list_layout.takeAt(self.acc_list_layout.count() - 1)
            self.acc_list_layout.addWidget(lbl)
            self.acc_list_layout.addStretch(1)
            return
        for i, acc in enumerate(accounts):
            row = QFrame()
            row.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            plat = PLATFORM_NAMES.get(acc.get("platform"), acc.get("platform") or "自动")
            info = QLabel(f"{acc.get('name')}  [{plat}]  {acc.get('url', '')[:36]}")
            h.addWidget(info, 1)
            # 分组多选（当前已绑定组）
            gsel = GroupMultiSelect(width=126)
            gsel.set_groups(groups)
            gsel.set_selected(acc.get("group_ids", []))
            h.addWidget(gsel)
            # 记录行控件与初始值，改动后启用底部「保存全部修改」
            self._acc_rows.append((i, gsel, gsel.selected()))
            gsel.changed.connect(
                lambda b=None: self._acc_save_btn.setEnabled(True))
            # 上移/下移（直接改账号顺序，立即生效）
            up_btn = icon_button("⬆️", "上移一位", (28, 26), True, "font-size: 12px;",
                                 on_click=lambda idx=i: self._move_account(idx, -1))
            up_btn.setEnabled(i > 0)
            h.addWidget(up_btn)
            down_btn = icon_button("⬇️", "下移一位", (28, 26), True, "font-size: 12px;",
                                   on_click=lambda idx=i: self._move_account(idx, 1))
            down_btn.setEnabled(i < len(accounts) - 1)
            h.addWidget(down_btn)
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(30, 26)
            del_btn.setStyleSheet(icon_btn_style(False, "background: #c62828; color: white; font-size: 13px;"))
            del_btn.clicked.connect(lambda _=False, idx=i: self._delete_account(idx))
            h.addWidget(del_btn)
            self.acc_list_layout.takeAt(self.acc_list_layout.count() - 1)
            self.acc_list_layout.addWidget(row)
            self.acc_list_layout.addStretch(1)
        # 底部统一保存按钮（替代逐行 💾）
        bar = QHBoxLayout()
        self._acc_save_btn = QPushButton("💾 保存全部修改")
        self._acc_save_btn.setStyleSheet("background: #2e7d32; color: white; padding: 4px 14px;")
        self._acc_save_btn.setEnabled(False)
        self._acc_save_btn.clicked.connect(self._save_all_account_groups)
        bar.addWidget(self._acc_save_btn)
        bar.addStretch(1)
        hint = QLabel("修改组号后点此一次性保存")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        bar.addWidget(hint)
        self.acc_list_layout.takeAt(self.acc_list_layout.count() - 1)
        self.acc_list_layout.addLayout(bar)
        self.acc_list_layout.addStretch(1)

    def _save_all_account_groups(self):
        """一次性保存所有行的分组修改（替代逐行保存）。"""
        from tracker.accounts import load_accounts, save_accounts
        accounts = load_accounts()
        changed = 0
        for idx, gsel, orig in getattr(self, "_acc_rows", []):
            gids = gsel.selected()
            if gids == orig or not (0 <= idx < len(accounts)):
                continue
            accounts[idx]["group_ids"] = gids
            changed += 1
        if changed:
            save_accounts(accounts)
            self.app.toast(f"✅ 已保存 {changed} 项分组修改", "success")
        else:
            self.app.toast("没有需要保存的修改", "info")
        self.reload_accounts()

    # ---------- 分组管理 ----------
    def _reload_group_list(self):
        """刷新分组列表 + 添加账号/已配置账号的分组多选菜单。"""
        from tracker.accounts import get_groups
        groups = get_groups()
        # 刷新「添加账号」的分组多选（按文件序，⬆️⬇️ 排序即调整该顺序）
        self.add_group_sel.set_groups(groups)
        # 刷新分组列表
        self._clear_layout(self.group_list_layout)
        self.group_list_layout.addStretch(1)
        if not groups:
            lbl = QLabel("（暂无分组，先新建分组再给账号绑定组号）")
            lbl.setStyleSheet("color: gray;")
            self.group_list_layout.takeAt(self.group_list_layout.count() - 1)
            self.group_list_layout.addWidget(lbl)
            self.group_list_layout.addStretch(1)
        else:
            n = len(groups)
            for pos, (gid, gname) in enumerate(groups.items()):
                row = QFrame()
                row.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 4, 8, 4)
                gid_lbl = QLabel(gid)
                gid_lbl.setFixedWidth(44)
                gid_lbl.setStyleSheet("font-weight: bold; color: #1976d2;")
                h.addWidget(gid_lbl)
                h.addWidget(QLabel(gname), 1)
                up_btn = icon_button("⬆️", "上移一位", (28, 24), True, "font-size: 11px;")
                up_btn.setEnabled(pos > 0)
                up_btn.clicked.connect(lambda _=False, g=gid: self._move_group(g, -1))
                h.addWidget(up_btn)
                down_btn = icon_button("⬇️", "下移一位", (28, 24), True, "font-size: 11px;")
                down_btn.setEnabled(pos < n - 1)
                down_btn.clicked.connect(lambda _=False, g=gid: self._move_group(g, 1))
                h.addWidget(down_btn)
                del_btn = QPushButton("✕")
                del_btn.setFixedSize(30, 22)
                del_btn.setStyleSheet(icon_btn_style(False, "background: #c62828; color: white; font-size: 13px;"))
                del_btn.clicked.connect(lambda _=False, g=gid: self._delete_group(g))
                h.addWidget(del_btn)
                self.group_list_layout.takeAt(self.group_list_layout.count() - 1)
                self.group_list_layout.addWidget(row)
                self.group_list_layout.addStretch(1)
        # 刷新「已配置账号」列表（分组多选同步新增的分组）
        self._reload_acc_list()

    def _move_group(self, gid, delta):
        from tracker.accounts import move_group
        if move_group(gid, delta):
            self._reload_group_list()
            self.reload_accounts()

    def _add_group(self):
        gid = self.gid_entry.text().strip()
        gname = self.gname_entry.text().strip()
        from tracker.accounts import GROUP_ID_PATTERN, save_group, get_groups
        if not gid or not GROUP_ID_PATTERN.match(gid):
            self.app.toast("组编号必须为重复数字，如 11、22、33", "warning")
            return
        if not gname:
            self.app.toast("请输入组名", "warning")
            return
        if gid in get_groups():
            self.app.toast(f"组 {gid} 已存在，直接给账号绑定即可", "warning")
            return
        if save_group(gid, gname):
            self.gid_entry.clear()
            self.gname_entry.clear()
            self._reload_group_list()
            self.app.toast(f"✅ 已新建分组：{gid} {gname}", "success")

    def _delete_group(self, gid):
        from tracker.accounts import delete_group
        delete_group(gid)
        self._reload_group_list()
        self.reload_accounts()

    # ---------- 按组导出 ----------
    def _export_group(self):
        """弹窗选择分组，导出该组所有账号的变化对比 PNG。"""
        from tracker.accounts import get_groups
        groups = get_groups()
        if not groups:
            self.app.toast("暂无分组，请先在「设置」里新建分组", "warning")
            return
        win = QDialog(self)
        win.setWindowTitle("📷 按组导出")
        win.setFixedSize(360, 220)
        v = QVBoxLayout(win)
        v.setContentsMargins(16, 16, 16, 14)
        t1 = QLabel("选择要导出的分组")
        t1.setStyleSheet("font-size: 14px; font-weight: bold;")
        t1.setAlignment(Qt.AlignCenter)
        v.addWidget(t1)
        t2 = QLabel("将把该组内所有账号的「最近两次变化」合成一张 PNG")
        t2.setStyleSheet("color: gray; font-size: 11px;")
        t2.setWordWrap(True)
        t2.setAlignment(Qt.AlignCenter)
        v.addWidget(t2)
        opts = [f"{gid}:{gname}" for gid, gname in sorted(groups.items())]
        sel = QComboBox()
        sel.addItems(opts)
        sel.setFixedWidth(240)
        v.addWidget(sel, 0, Qt.AlignCenter)
        v.addStretch(1)
        export_btn = QPushButton("📷 导出 PNG")
        export_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px;")
        export_btn.clicked.connect(lambda: do_export())
        v.addWidget(export_btn)

        def do_export():
            v = sel.currentText()
            gid = v.split(":")[0].strip()
            gname = v.split(":", 1)[1].strip() if ":" in v else ""
            win.accept()
            self._run_group_export(gid, gname)

        win.exec()

    def _run_group_export(self, gid, gname):
        def work():
            from tracker.export_png import export_group_png
            return export_group_png(gid, gname)

        def done(result, err):
            if err:
                self.app.toast(f"导出失败：{err}", "error")
            else:
                self.app.toast(f"✅ 已导出组 PNG：{result}", "success")

        run_bg(work, done)
