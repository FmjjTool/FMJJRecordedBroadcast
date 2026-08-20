"""账号追踪页（PySide6 版）：单账号单平台，每个账号独立卡片，采集结果存 xlsx 并读取长期缓存。

对应原版 gui/tracker_page.py：
- 功能页：平台筛选 + 获取全部（回车快捷）+ ⏰ 定时采集（状态圆点）+ 📷 按组导出 + 刷新
  每账号卡片：昵称/平台/状态 + 采集/导出/历史按钮 + 变化提示（涨绿跌红）+ 2行×4列预览
- 设置页：添加追踪账号（账号名/平台/链接/组号）、分组管理（组列表+新建）、已配置账号（组号下拉+保存+删除）
- 历史窗口：指标当列、时间点当行，可横向滚动
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

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

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博"}
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
        title = QLabel("📈 账号追踪")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self.reload_accounts)
        head.addWidget(refresh_btn)
        export_group_btn = QPushButton("📷 按组导出")
        export_group_btn.setStyleSheet(
            "background: transparent; border: 1px solid #3a3d4a; color: #e1e4ed;")
        export_group_btn.clicked.connect(self._export_group)
        head.addWidget(export_group_btn)
        self.timer_dot = QLabel("○")
        self.timer_dot.setStyleSheet("color: #888; font-size: 15px;")
        head.addWidget(self.timer_dot)
        timer_btn = QPushButton("⏰ 定时采集")
        timer_btn.setStyleSheet(
            "background: transparent; border: 1px solid #3a3d4a; color: #e1e4ed;")
        timer_btn.clicked.connect(self._toggle_collect_timer)
        head.addWidget(timer_btn)
        fetch_all_btn = QPushButton("⬇ 获取全部")
        fetch_all_btn.setStyleSheet("background: #2e7d32; color: white;")
        fetch_all_btn.clicked.connect(lambda: self._collect_all())
        head.addWidget(fetch_all_btn)
        self.batch_platform = QComboBox()
        self.batch_platform.addItems(["全部平台", "B站", "抖音", "微博"])
        self.batch_platform.setFixedWidth(92)
        head.addWidget(self.batch_platform)
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
        add_card.setStyleSheet("QFrame { background: #23262f; border-radius: 8px; }")
        av = QVBoxLayout(add_card)
        av.setContentsMargins(10, 10, 10, 10)
        av.setSpacing(4)
        ah = QLabel("添加追踪账号（账号名 + 链接地址）")
        ah.setStyleSheet("font-weight: bold;")
        av.addWidget(ah)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("账号名:"))
        self.add_name = QLineEdit()
        self.add_name.setPlaceholderText("如 封茗囧菌抖音")
        r1.addWidget(self.add_name, 1)
        r1.addWidget(QLabel("平台:"))
        self.add_platform = QComboBox()
        self.add_platform.addItems(["自动识别", "抖音", "B站", "微博"])
        self.add_platform.setFixedWidth(100)
        r1.addWidget(self.add_platform)
        av.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("链接:"))
        self.add_url = QLineEdit()
        self.add_url.setPlaceholderText("账号主页链接（自动识别平台）")
        self.add_url.returnPressed.connect(self._add_account)
        r2.addWidget(self.add_url, 1)
        r2.addWidget(QLabel("组号:"))
        self.add_group_sel = QComboBox()
        self.add_group_sel.setFixedWidth(120)
        r2.addWidget(self.add_group_sel)
        add_btn = QPushButton("➕ 添加")
        add_btn.clicked.connect(self._add_account)
        r2.addWidget(add_btn)
        av.addLayout(r2)
        outer.addWidget(add_card)

        # 分组管理
        group_card = QFrame()
        group_card.setStyleSheet("QFrame { background: #23262f; border-radius: 8px; }")
        gv = QVBoxLayout(group_card)
        gv.setContentsMargins(10, 10, 10, 10)
        gv.setSpacing(4)
        gh = QLabel("分组管理（组编号 + 组名，支持按组导出）")
        gh.setStyleSheet("font-weight: bold;")
        gv.addWidget(gh)
        self.group_list_frame = QScrollArea()
        self.group_list_frame.setWidgetResizable(True)
        self.group_list_frame.setFixedHeight(110)
        gcontainer = QWidget()
        self.group_list_layout = QVBoxLayout(gcontainer)
        self.group_list_layout.setContentsMargins(0, 0, 0, 0)
        self.group_list_layout.setSpacing(2)
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
        newg_btn = QPushButton("➕ 新建分组")
        newg_btn.clicked.connect(self._add_group)
        addg.addWidget(newg_btn)
        addg.addStretch(1)
        gv.addLayout(addg)
        outer.addWidget(group_card)

        # 已配置账号
        list_card = QFrame()
        list_card.setStyleSheet("QFrame { background: #23262f; border-radius: 8px; }")
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
        if not accounts:
            lbl = QLabel("（暂无账号，请在「设置」里添加）")
            lbl.setStyleSheet("color: gray;")
            lbl.setAlignment(Qt.AlignCenter)
            self.cards_layout.takeAt(self.cards_layout.count() - 1)
            self.cards_layout.addWidget(lbl)
            self.cards_layout.addStretch(1)
        for acc in accounts:
            self.cards.append(self._make_card(acc))
        self._reload_acc_list()

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _make_card(self, acc):
        from tracker.accounts import parse_url, load_history
        card = QFrame()
        card.setStyleSheet("QFrame { background: #23262f; border-radius: 8px; }")
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
        plat_lbl.setStyleSheet("color: #64b5f6; font-size: 11px;")
        left.addWidget(plat_lbl)
        head.addLayout(left)
        head.addStretch(1)
        status = QLabel("")
        status.setStyleSheet("color: #888;")
        head.addWidget(status)
        export_btn = QPushButton("📷 导出")
        export_btn.setFixedSize(60, 26)
        export_btn.setStyleSheet(
            "background: transparent; border: 1px solid #3a3d4a; color: #e1e4ed;")
        export_btn.clicked.connect(lambda _=False, a=acc: self._export_png(a))
        head.addWidget(export_btn)
        hist_btn = QPushButton("📊 历史")
        hist_btn.setFixedSize(60, 26)
        hist_btn.setStyleSheet(
            "background: transparent; border: 1px solid #3a3d4a; color: #e1e4ed;")
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
        preview_frame.setStyleSheet("background: #15171d; border-radius: 6px;")
        grid = QGridLayout(preview_frame)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setSpacing(4)

        # 预览只显示「当前」+「前一次」
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
        rows = [("当前", records[0])]
        if len(records) >= 2:
            t = (records[1].get("_time") or "")[:16]
            rows.append((t or "上次", records[1]))
        for r, (time_label, rec) in enumerate(rows):
            tl = QLabel(time_label)
            tl.setStyleSheet("color: #64b5f6; font-weight: bold;")
            grid.addWidget(tl, r, 0)
            for c, field in enumerate(["fans", "following", "likes"]):
                vl = QLabel(_cell(rec, [field]))
                vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(vl, r, c + 1)

    def _diff_items(self, prev, curr):
        """对比前后两次采集，返回 [(文本, 颜色), ...] 列表（涨绿跌红）。"""
        items = []
        if curr.get("name") and prev.get("name") and curr["name"] != prev["name"]:
            items.append((f"🔄 更名 {prev['name']}→{curr['name']}", "#64b5f6"))
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
            if i == 0:
                title = "当前"
            else:
                t = (rec.get("_time") or "")[:16]  # 带时间
                title = t if t else f"历史{i}"
            ver = QTableWidgetItem(title)
            table.setVerticalHeaderItem(i, ver)
            for j, (_name, fields) in enumerate(METRICS):
                table.setItem(i, j, QTableWidgetItem(_cell(rec, fields)))
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        v.addWidget(table, 1)
        win.exec()

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
        """批量采集：按平台筛选（全部/某平台），逐个串行采集。silent=True 用于定时采集。"""
        if self._collecting:
            if not silent:
                self.app.toast("正在采集中，请稍候", "info")
            return
        plat_map = {"B站": "bilibili", "抖音": "douyin", "微博": "weibo"}
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
            self.app.toast(f"⬇ 开始批量采集 {len(accounts)} 个账号...", "info")

        def step(idx):
            if idx >= len(accounts):
                if not silent:
                    self.app.toast("✅ 批量采集完成", "info")
                self._collecting = False
                return
            self._collect_account(accounts[idx], on_done=lambda i=idx: step(i + 1))

        self._collecting = True
        step(0)

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
                QMessageBox.warning(win, "提示", "间隔不能小于 1 分钟")
                return
            self._timer_interval = m * 60
            self._timer_running = True
            win.accept()
            self.timer_dot.setText("●")
            self.timer_dot.setStyleSheet("color: #4caf50; font-size: 15px;")
            self.app.toast(f"⏰ 定时采集已启动（每 {m} 分钟）", "success")

            def loop():
                # 启动后先执行一次，之后每隔 interval 执行一次
                from gui_qt.common import post_to_main
                post_to_main(lambda: self._collect_all(silent=True))
                while self._timer_running:
                    time.sleep(self._timer_interval)
                    if not self._timer_running:
                        break
                    post_to_main(lambda: self._collect_all(silent=True))

            import threading
            threading.Thread(target=loop, daemon=True).start()

        win.exec()

    def _stop_collect_timer(self):
        self._timer_running = False
        self.timer_dot.setText("○")
        self.timer_dot.setStyleSheet("color: #888; font-size: 15px;")
        self.app.toast("定时采集已停止", "info")

    # ---------- 设置页：账号增删 ----------
    def _selected_group_id(self):
        """从组号下拉框解析当前选中的组号（"无分组" 或 "组号:组名"）。"""
        v = self.add_group_sel.currentText()
        if not v or v == "无分组":
            return ""
        return v.split(":")[0].strip()

    def _add_account(self):
        name = self.add_name.text().strip() or "未命名"
        url = self.add_url.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入账号链接")
            return
        from tracker.accounts import parse_url, load_accounts, save_accounts
        plat_map = {"自动识别": "", "抖音": "douyin", "B站": "bilibili", "微博": "weibo"}
        platform = plat_map.get(self.add_platform.currentText(), "")
        if not platform:
            platform, _ = parse_url(url)
        gid = self._selected_group_id()
        acc = {"name": name, "url": url, "platform": platform or "", "group_id": gid}
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

    def _reload_acc_list(self):
        self._clear_layout(self.acc_list_layout)
        self.acc_list_layout.addStretch(1)
        from tracker.accounts import load_accounts, get_groups
        accounts = load_accounts()
        groups = get_groups()
        group_opts = ["无分组"] + [f"{gid}:{gname}" for gid, gname in sorted(groups.items())]
        if not accounts:
            lbl = QLabel("（暂无账号，先在上方添加）")
            lbl.setStyleSheet("color: gray;")
            self.acc_list_layout.takeAt(self.acc_list_layout.count() - 1)
            self.acc_list_layout.addWidget(lbl)
            self.acc_list_layout.addStretch(1)
            return
        for i, acc in enumerate(accounts):
            row = QFrame()
            row.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            plat = PLATFORM_NAMES.get(acc.get("platform"), acc.get("platform") or "自动")
            info = QLabel(f"{acc.get('name')}  [{plat}]  {acc.get('url', '')[:36]}")
            h.addWidget(info, 1)
            # 组号下拉（当前所属组）
            cur_gid = acc.get("group_id", "")
            cur_val = "无分组"
            for opt in group_opts:
                if opt != "无分组" and opt.split(":")[0] == cur_gid:
                    cur_val = opt
                    break
            gsel = QComboBox()
            gsel.addItems(group_opts)
            gsel.setCurrentText(cur_val)
            gsel.setFixedWidth(126)
            h.addWidget(gsel)
            save_btn = QPushButton("💾")
            save_btn.setFixedSize(34, 26)
            save_btn.setStyleSheet("background: #2e7d32; color: white;")
            save_btn.clicked.connect(lambda _=False, idx=i, s=gsel: self._save_account_group(idx, s))
            h.addWidget(save_btn)
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(30, 26)
            del_btn.setStyleSheet("background: #c62828; color: white;")
            del_btn.clicked.connect(lambda _=False, idx=i: self._delete_account(idx))
            h.addWidget(del_btn)
            self.acc_list_layout.takeAt(self.acc_list_layout.count() - 1)
            self.acc_list_layout.addWidget(row)
            self.acc_list_layout.addStretch(1)

    def _save_account_group(self, index, gsel):
        """保存某个账号的分组归属（从下拉框读取组号）。"""
        v = gsel.currentText()
        gid = "" if v == "无分组" else v.split(":")[0].strip()
        from tracker.accounts import load_accounts, save_accounts
        accounts = load_accounts()
        if 0 <= index < len(accounts):
            accounts[index]["group_id"] = gid
            save_accounts(accounts)
            self.app.toast(f"✅ 已更新「{accounts[index].get('name', '')}」的分组", "success")
            self.reload_accounts()

    # ---------- 分组管理 ----------
    def _reload_group_list(self):
        """刷新分组列表 + 添加账号/已配置账号的组号下拉菜单。"""
        from tracker.accounts import get_groups
        groups = get_groups()
        # 刷新「添加账号」的组号下拉
        opts = ["无分组"] + [f"{gid}:{gname}" for gid, gname in sorted(groups.items())]
        cur = self.add_group_sel.currentText()
        self.add_group_sel.clear()
        self.add_group_sel.addItems(opts)
        if cur in opts:
            self.add_group_sel.setCurrentText(cur)
        else:
            self.add_group_sel.setCurrentText(opts[0])
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
            for gid, gname in sorted(groups.items()):
                row = QFrame()
                row.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 4, 8, 4)
                gid_lbl = QLabel(gid)
                gid_lbl.setFixedWidth(44)
                gid_lbl.setStyleSheet("font-weight: bold; color: #64b5f6;")
                h.addWidget(gid_lbl)
                h.addWidget(QLabel(gname), 1)
                del_btn = QPushButton("✕")
                del_btn.setFixedSize(30, 22)
                del_btn.setStyleSheet("background: #c62828; color: white;")
                del_btn.clicked.connect(lambda _=False, g=gid: self._delete_group(g))
                h.addWidget(del_btn)
                self.group_list_layout.takeAt(self.group_list_layout.count() - 1)
                self.group_list_layout.addWidget(row)
                self.group_list_layout.addStretch(1)
        # 刷新「已配置账号」列表（组号下拉同步新增的分组）
        self._reload_acc_list()

    def _add_group(self):
        gid = self.gid_entry.text().strip()
        gname = self.gname_entry.text().strip()
        from tracker.accounts import GROUP_ID_PATTERN, save_group, get_groups
        if not gid or not GROUP_ID_PATTERN.match(gid):
            QMessageBox.warning(self, "提示", "组编号必须为重复数字，如 11、22、33")
            return
        if not gname:
            QMessageBox.warning(self, "提示", "请输入组名")
            return
        if gid in get_groups():
            QMessageBox.warning(self, "提示", f"组 {gid} 已存在，直接给账号绑定即可")
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
