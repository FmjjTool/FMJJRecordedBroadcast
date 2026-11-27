"""音乐账号追踪页：单账号单平台，每个账号独立卡片，采集结果存 xlsx 并读取长期缓存。

与「账号追踪」页同构（照搬 UI 换数据源）：
- 功能页：平台筛选 + 获取全部（回车快捷）+  按组导出 + 刷新（定时任务统一在「任务」页）
  每账号卡片：昵称/平台/状态 + 采集/导出/历史按钮 + 变化提示（涨绿跌红）+ 2行×4列预览
- 设置页：账号与分组已迁移到「Cookie 管理 → 账号配置」，此处只保留入口提示
- 历史窗口：指标当列、时间点当行，可横向滚动

数据源：网易云/QQ音乐/酷狗/酷我/5sing（游客HTTP→游客Playwright→登录态三级回退）。
"""

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QProgressBar, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import table_sort, theme
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from toolbox.platform_names import MUSIC_PLATFORM_NAMES as PLATFORM_NAMES
from toolbox.tracker_diff import diff_items, num_delta


def _status_style(name, size=None):
    color = theme.color(name)
    return f"color: {color};" + (f"font-size: {size}px;" if size else "")


# 指标行定义：(显示名, [字段名优先级列表])
METRICS = [
    ("昵称", ["name"]),
    ("粉丝数", ["fans"]),
    ("关注数", ["following"]),
    ("播放/听众", ["listens"]),
    ("歌曲数", ["songs"]),
    ("专辑数", ["albums"]),
    ("MV数", ["mvs"]),
    ("作品数", ["works"]),
    ("简介", ["sign"]),
    ("地区", ["region"]),
    ("生日", ["birthday"]),
]

_NUM_FIELDS = {"fans", "following", "listens", "songs", "albums", "mvs", "works"}


def _cell(rec, fields):
    for f in fields:
        v = rec.get(f)
        if v is None or v == "":
            continue
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


class MusicTrackerPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.cards = []
        self._collecting = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)
        layout.addWidget(self._build_func())
        self.reload_accounts()

    # ---------- 功能页 ----------
    def _build_func(self) -> QWidget:
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        head = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.reload_accounts)
        head.addWidget(refresh_btn)
        export_group_btn = QPushButton("按组导出")
        export_group_btn.setStyleSheet(
            "background: transparent; border: 1px solid palette(mid); color: palette(text);")
        export_group_btn.clicked.connect(self._export_group)
        head.addWidget(export_group_btn)
        fetch_all_btn = QPushButton("获取全部")
        fetch_all_btn.setStyleSheet("background: palette(highlight); color: palette(highlighted-text);")
        fetch_all_btn.clicked.connect(lambda: self._collect_all())
        head.addWidget(fetch_all_btn)
        self.batch_platform = QComboBox()
        self.batch_platform.addItems(["全部平台", "网易云", "QQ音乐", "酷狗", "酷我", "5sing"])
        self.batch_platform.setFixedWidth(92)
        head.addWidget(self.batch_platform)
        # 批量采集进度条
        self.batch_prog = QProgressBar()
        self.batch_prog.setFixedWidth(150)
        self.batch_prog.setFormat("采集 %v/%m（%p%）")
        self.batch_prog.setRange(0, 1)
        self.batch_prog.setValue(0)
        head.addWidget(self.batch_prog)
        head.addStretch(1)
        v.addLayout(head)

        # 卡片滚动区
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.addStretch(1)
        self.cards_scroll.setWidget(self.cards_container)
        v.addWidget(self.cards_scroll, 1)
        return f

    # ---------- 账号卡片 ----------
    def reload_accounts(self):
        self._accounts_loading = True
        self._accounts_refreshed_at = time.monotonic()
        self._reload_gen = getattr(self, "_reload_gen", 0) + 1
        gen = self._reload_gen
        self._clear_layout(self.cards_layout)
        self.cards_layout.addStretch(1)
        self.cards = []
        from music_account.accounts import load_accounts
        accounts = load_accounts()
        if not accounts:
            self._accounts_loading = False
            lbl = QLabel("（暂无可用账号）")
            lbl.setStyleSheet("color: palette(placeholder-text);")
            lbl.setAlignment(Qt.AlignCenter)
            self.cards_layout.takeAt(self.cards_layout.count() - 1)
            self.cards_layout.addWidget(lbl)
            self.cards_layout.addStretch(1)
            return
        # 骨架屏：先渲染灰色占位卡（点进页面秒开），
        # 后台批量读各账号 xlsx 历史缓存（openpyxl 逐个读是卡顿根源），
        # 完成后回主线程一次性替换为真实卡片
        skeletons = [self._make_skeleton() for _ in accounts]

        def work():
            from music_account.accounts import load_history
            return [load_history(acc.get("name", "")) for acc in accounts]

        def done(histories, err):
            if gen != getattr(self, "_reload_gen", 0):
                return  # 期间又触发了 reload，本轮结果作废
            self._accounts_loading = False
            if err:
                self._accounts_refreshed_at = 0
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
            "QFrame { background: palette(base); border-radius: 8px; }"
            "QLabel { background: palette(midlight); border-radius: 4px; }")
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
        grid_bar.setStyleSheet("background: palette(alternate-base); border-radius: 6px;")
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
        from music_account.accounts import parse_url, load_history
        card = QFrame()
        card.setStyleSheet("QFrame { background: palette(base); border-radius: 8px; }")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(8, 6, 8, 4)
        cv.setSpacing(2)

        platform = (acc.get("platform") or "").strip()
        if not platform:
            platform, _pid, _kind = parse_url(acc.get("url", ""))
        plat_name = PLATFORM_NAMES.get(platform, platform or "未知")

        # 头部：昵称 + 平台 + 状态 + 按钮
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        name_lbl = QLabel(f"{acc.get('name', '未命名')}")
        name_lbl.setStyleSheet("font-weight: bold;")
        left.addWidget(name_lbl)
        plat_lbl = QLabel(plat_name)
        plat_lbl.setStyleSheet(_status_style("link", 11))
        left.addWidget(plat_lbl)
        head.addLayout(left)
        head.addStretch(1)
        status = QLabel("")
        status.setStyleSheet(_status_style("muted"))
        head.addWidget(status)
        export_btn = QPushButton("导出")
        export_btn.setFixedSize(60, 26)
        export_btn.setStyleSheet(
            "background: transparent; border: 1px solid palette(mid); color: palette(text);")
        export_btn.clicked.connect(lambda _=False, a=acc: self._export_png(a))
        head.addWidget(export_btn)
        hist_btn = QPushButton("历史")
        hist_btn.setFixedSize(60, 26)
        hist_btn.setStyleSheet(
            "background: transparent; border: 1px solid palette(mid); color: palette(text);")
        hist_btn.clicked.connect(lambda _=False, a=acc: self._open_history(a))
        head.addWidget(hist_btn)
        btn = QPushButton("采集")
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
        preview_frame.setStyleSheet("background: palette(alternate-base); border-radius: 6px;")
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
            status.setText("缓存")
            if len(history) >= 2:
                self._render_diff(diff_frame, history[1], history[0])
        cv.addWidget(preview_frame)
        self.cards_layout.takeAt(self.cards_layout.count() - 1)
        self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)
        return {"account": acc, "preview": grid, "status": status,
                "btn": btn, "diff_frame": diff_frame}

    def _render_preview(self, grid, records):
        """2 行 × 4 列网格预览：每行 = 一个时间点，列 = [时间, 粉丝, 关注, 作品]。"""
        self._clear_layout(grid)
        if not records:
            lbl = QLabel("（尚未采集）")
            lbl.setStyleSheet(f"color: {theme.color('muted')};")
            grid.addWidget(lbl, 0, 0, 1, 4)
            return
        rows = [((records[0].get("_time") or "")[:16] or "记录1", records[0])]
        if len(records) >= 2:
            t = (records[1].get("_time") or "")[:16]
            rows.append((t or "记录2", records[1]))
        for r, (time_label, rec) in enumerate(rows):
            tl = QLabel(time_label)
            tl.setStyleSheet(f"color: {theme.color('link')}; font-weight: bold;")
            grid.addWidget(tl, r, 0)
            for c, field in enumerate(["fans", "following", "works"]):
                vl = QLabel(_cell(rec, [field]))
                vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(vl, r, c + 1)

    def _diff_items(self, prev, curr):
        return diff_items(prev, curr, [
            ("粉丝", "fans"), ("关注", "following"), ("播放", "listens"),
            ("歌曲", "songs"), ("专辑", "albums"), ("MV", "mvs")])

    def _render_diff(self, frame, prev, curr):
        """在 frame（QHBoxLayout）里渲染变化提示（每项独立颜色）。"""
        self._clear_layout(frame)
        colors = {
            "up": theme.color("success"),
            "down": theme.color("danger"),
            "note": theme.color("link"),
        }
        for item in self._diff_items(prev, curr):
            lbl = QLabel(item["text"])
            lbl.setStyleSheet(
                f"color: {colors.get(item.get('direction'), theme.color('muted'))};"
                " font-size: 11px;")
            frame.addWidget(lbl)
        frame.addStretch(1)

    def _export_png(self, acc):
        """导出该账号最近两次采集的变化对比 PNG（涨绿跌红）。"""
        from music_account.accounts import load_history
        history = load_history(acc.get("name", ""))
        if len(history) < 2:
            self.app.toast("该账号至少需要两次采集数据才能导出变化对比", "warning")
            return

        def work():
            from music_account.export_png import export_diff_png
            return export_diff_png(acc, history)

        def done(result, err):
            if err:
                self.app.toast(f"导出失败：{err}", "error")
                return
            self.app.toast(f"已导出 PNG：{result}", "success")
            try:
                os.startfile(str(result))
            except Exception:
                pass

        run_bg(work, done)

    def _open_history(self, acc):
        """打开二级窗口：完整历史数据（指标当列、时间点当行、可横向滚动）。"""
        from music_account.accounts import load_history
        history = load_history(acc.get("name", ""))
        if not history:
            self.app.toast("暂无历史数据", "info")
            return

        win = QDialog(self)
        win.setWindowTitle(f"{acc.get('name', '账号')} · 历史数据")
        # QDialog 默认只有关闭按钮，补上最小化/最大化（双击标题栏全屏也生效）
        win.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        win.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        win.resize(980, 520)
        v = QVBoxLayout(win)
        v.setContentsMargins(10, 12, 10, 10)
        head = QLabel(f"{acc.get('name', '')} 历史数据（共 {len(history)} 次采集，最新在前）")
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
                    cell.setForeground(QColor(theme.color(
                        "success" if delta > 0 else "danger")))
                table.setItem(i, j, cell)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        # 最后一列拉伸：最大化后不留右侧大片空白
        table.horizontalHeader().setStretchLastSection(True)
        # 表头点击排序：采集时间在行首（垂直表头），排序后按记录新顺序重贴
        table_sort.install(table, rows=history, skip_columns=())

        def _sync_row_labels(_col):
            if len(history) != table.rowCount():
                return
            table.setVerticalHeaderLabels([
                ((rec.get("_time") or "")[:16] or f"记录{i + 1}")
                for i, rec in enumerate(history)])

        table.horizontalHeader().sectionClicked.connect(_sync_row_labels)
        hint = QLabel("颜色说明：与上一条采集对比，绿色=数值增加，红色=数值减少（↑/↓ 后为增减量）")
        hint.setProperty("role", "caption")
        v.addWidget(hint)
        v.addWidget(table, 1)
        win.exec()

    @staticmethod
    def _num_delta(prev, curr, fields):
        return num_delta(prev, curr, fields)

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
        card["status"].setText("采集中...")
        card["status"].setStyleSheet(_status_style("warning"))

        def work():
            from music_account.accounts import collect_account, save_history
            record = collect_account(acc)
            save_history(acc.get("name", "未命名"), record)
            return record

        def done(result, err):
            card = self._card_for(acc)
            if card:
                card["btn"].setEnabled(True)
                if err:
                    card["status"].setText(f"{err}")
                    card["status"].setStyleSheet(_status_style("danger"))
                else:
                    from music_account.accounts import load_history
                    history = load_history(acc.get("name", "未命名"))
                    self._render_preview(card["preview"], history[:2])
                    if len(history) >= 2:
                        self._render_diff(card["diff_frame"], history[1], history[0])
                    if result.get("error"):
                        card["status"].setText(f"{result['error']}")
                        card["status"].setStyleSheet(_status_style("danger"))
                    else:
                        card["status"].setText("完成")
                        card["status"].setStyleSheet(_status_style("success"))
            if on_done:
                on_done()

        run_bg(work, done)

    def _collect_all(self):
        """批量采集：按平台筛选（全部/某平台），4 个一批并发采集。"""
        if self._collecting:
            self.app.toast("正在采集中，请稍候", "info")
            return
        plat_map = {"网易云": "netease", "QQ音乐": "qq", "酷狗": "kugou",
                    "酷我": "kuwo", "5sing": "fivesing"}
        target_plat = plat_map.get(self.batch_platform.currentText(), "")
        accounts = [
            c["account"] for c in self.cards
            if not target_plat or (c["account"].get("platform") or "").strip() == target_plat
        ]
        if not accounts:
            self.app.toast("没有符合条件的账号", "info")
            return
        self.app.toast(f"开始批量采集 {len(accounts)} 个账号（并发模式）...", "info")
        self.batch_prog.setRange(0, max(len(accounts), 1))
        self.batch_prog.setValue(0)

        # 并发波次：4 个一批（原串行逐个等待，账号多时总耗时=各账号之和）
        from collections import deque
        queue = deque(accounts)
        state = {"running": 0, "done": 0}

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
                self.app.toast("批量采集完成", "info")
                self._collecting = False

        self._collecting = True
        _next_wave()

    # ---------- 页面生命周期 ----------
    def on_show(self):
        if (not self._accounts_loading
                and time.monotonic() - self._accounts_refreshed_at > 30):
            self.reload_accounts()

    def refresh_accounts(self):
        """账号配置在「Cookie 管理 → 账号配置」里改动后，刷新卡片（不触发采集）。"""
        self.reload_accounts()

    # ---------- 按组导出 ----------
    def _export_group(self):
        """弹窗选择分组，导出该组所有账号的变化对比 PNG。"""
        from music_account.accounts import get_groups
        groups = get_groups()
        if not groups:
            self.app.toast("暂无可用分组", "warning")
            return
        win = QDialog(self)
        win.setWindowTitle("按组导出")
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
        export_btn = QPushButton("导出 PNG")
        export_btn.setStyleSheet("background: palette(highlight); color: palette(highlighted-text); padding: 6px;")
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
            from music_account.export_png import export_group_png
            return export_group_png(gid, gname)

        def done(result, err):
            if err:
                self.app.toast(f"导出失败：{err}", "error")
                return
            self.app.toast(f"已导出组 PNG：{result}", "success")
            try:
                os.startfile(str(result))
            except Exception:
                pass

        run_bg(work, done)
