"""账号获取页（PySide6 版）：添加账号、批量抓取最新内容（结果自动存入「已获取」）。

对应原版 gui/info_pages.py 的 InfoAccountsPage：
- 功能页：添加账号行 / 账号勾选列表 / 采集模式（全部/选中/按组）/ 条数 /
  定时获取 / 增量模式 / 搜索限流 / 日志
- 设置页：添加账号（账号名+链接+分组多选）/ 分组管理 / 已配置账号
  （分组多选 + 采集类型下拉 + 保存/删除）
"""

import io
import json
import os
import threading
import time
from datetime import datetime

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidgetItem, QMessageBox, QPushButton, QProgressBar,
    QScrollArea, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import _collect, icon_btn_style, icon_button, post_to_main, refresh_results_count, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import GroupMultiSelect, RowCheckList


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _parse_publish_ts(text):
    """发布时间字符串 → epoch 秒。解析失败返回 None（对应条目保留不过滤）。

    覆盖平台常见格式：绝对时间（2026-09-27 14:30[:59]、2026/9/27、2026年9月27日）
    与相对时间（刚刚/X秒/分钟/小时/天/周/月前、今天/昨天/前天）。
    """
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    s = str(text or "").strip()
    if not s:
        return None
    m = _re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        try:
            hm = _re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s[m.end():])
            if hm:
                return _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           int(hm.group(1)), int(hm.group(2)),
                           int(hm.group(3) or 0)).timestamp()
            return _dt(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        except ValueError:
            pass
    now = _dt.now()
    if s.startswith(("刚刚", "刚才", "今天")):
        return now.timestamp()
    rm = _re.match(r"(\d+)\s*(秒|分钟|分|小时|时|天|日|周|月)前", s)
    if rm:
        n = int(rm.group(1))
        delta = {"秒": _td(seconds=n), "分钟": _td(minutes=n), "分": _td(minutes=n),
                 "小时": _td(hours=n), "时": _td(hours=n), "天": _td(days=n),
                 "日": _td(days=n), "周": _td(weeks=n),
                 "月": _td(days=30 * n)}[rm.group(2)]
        return (now - delta).timestamp()
    if s.startswith("昨天"):
        return (now - _td(days=1)).timestamp()
    if s.startswith("前天"):
        return (now - _td(days=2)).timestamp()
    return None


def _date_bound_ts(date_str, is_start):
    """'YYYY-MM-DD' → epoch 秒（开始=当天 00:00:00，结束=当天 23:59:59）；空/非法 → None。"""
    from datetime import datetime as _dt, timedelta as _td
    if not date_str:
        return None
    try:
        d = _dt.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    return d.timestamp() if is_start else (d + _td(days=1)).timestamp() - 1


def _filter_posts_by_date(posts, lo_ts, hi_ts):
    """保留发布时间落在 [lo_ts, hi_ts] 的条目；时间解析失败的条目保留（宁多勿漏）。"""
    if lo_ts is None and hi_ts is None:
        return posts
    keep = []
    for p in posts:
        ts = _parse_publish_ts(getattr(p, "publish_time", ""))
        if ts is None:
            keep.append(p)
            continue
        if lo_ts is not None and ts < lo_ts:
            continue
        if hi_ts is not None and ts > hi_ts:
            continue
        keep.append(p)
    return keep


class InfoAccountsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._timer_running = False
        self._timer_interval = 3600
        self._fetching = False
        self._timer = None
        # 暂停门闩：set=放行 / clear=暂停（采集线程在账号边界等待）
        self._pause_evt = threading.Event()
        self._pause_evt.set()
        # 取消标记：set 后未开始的账号直接跳过（在飞账号自然跑完不硬中断）
        self._cancel_evt = threading.Event()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        func_tab = QWidget()
        set_tab = QWidget()
        self.tabs.addTab(func_tab, "功能")
        self.tabs.addTab(set_tab, "设置")

        self._build_func(func_tab)
        self._build_settings(set_tab)
        self._reload_account_list()
        self._reload_group_list()
        self._reload_account_settings()

    def on_return_press(self):
        """主窗口回车统一入口 → 触发「获取最新」。"""
        self._fetch()

    # ---------- 工具 ----------
    def _groups(self):
        from accounts_manager import get_groups
        return get_groups()  # {组号: 组名}

    def _group_options(self):
        return ["无分组"] + [f"{gid}:{gname}" for gid, gname in self._groups().items()]

    def _refresh_gid_menus(self):
        opts = self._group_options()
        self.fetch_group.blockSignals(True)
        self.fetch_group.clear()
        self.fetch_group.addItems(opts)
        self.fetch_group.blockSignals(False)
        if hasattr(self, "set_group_sel"):
            self.set_group_sel.set_groups(self._groups())

    # ---------- 功能页 ----------
    def _build_func(self, f):
        layout = QVBoxLayout(f)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("👥 账号获取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 账号获取 · 功能介绍", (
            "<p><b>批量追踪账号主页、一键抓取最新作品/动态，结果自动存入「已获取」。</b></p>"
            "<p>· <b>添加账号</b>：主页 URL + 账号名 + 组号（可逗号分隔绑多组）；"
            "支持多平台（B站空间/抖音主页/微博等）</p>"
            "<p>· <b>获取最新</b>：勾选账号后批量抓取，右下角可⏸暂停 / ✕取消；"
            "抓取结果进入「已获取」与增量数据库</p>"
            "<p>· <b>增量模式</b>：只入库新内容（数据库去重），勾选后写入数据库</p>"
            "<p>· <b>⏰ 定时获取</b>：按间隔自动抓取全部账号</p>"
            "<p>· <b>📅 日期段</b>：日历选择发布时间范围（如 9月1日~9月20日、从头~9月20日），"
            "获取时只保留范围内的内容，范围持久保存</p>"
            "<p>· <b>📤 同步到追踪</b>：本页账号一键同步到「视频账号追踪」，"
            "已存在的自动跳过，账号名/分组一并带过去（免重复录入）</p>"
            "<p>· <b>设置</b>：账号分组管理（组号+组名）、账号编辑（名称/URL/分组/排序）</p>"
            "<p>· Cookie 统一由侧栏「Cookie 管理」维护，抓取时自动套用</p>")))
        layout.addLayout(title_row)

        # 添加账号行
        add_row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("账号主页 URL")
        self.url_entry.returnPressed.connect(self._add)
        add_row.addWidget(self.url_entry, 1)
        self.name_fetch_btn = icon_button(
            "🔍", "自动获取账号名（按链接识别平台并拉取昵称）", (32, 30), True,
            "font-size: 14px;",
            lambda: self._auto_fetch_name(self.url_entry, self.name_entry,
                                          self.name_fetch_btn))
        add_row.addWidget(self.name_fetch_btn)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText("账号名(留空自动获取)")
        self.name_entry.setFixedWidth(110)
        add_row.addWidget(self.name_entry)
        self.add_gid_entry = QLineEdit()
        self.add_gid_entry.setPlaceholderText("组号(逗号分隔多组)")
        self.add_gid_entry.setFixedWidth(130)
        add_row.addWidget(self.add_gid_entry)
        self.add_btn = icon_button("➕", "添加账号", (40, 30), True, "font-size: 16px;", self._add)
        add_row.addWidget(self.add_btn)
        layout.addLayout(add_row)

        # 全选/反选/清除
        sel_bar = QHBoxLayout()
        for text, mode, tip in (("☑️", "all", "全选"), ("🔄", "invert", "反选"), ("❌", "none", "清除选择")):
            b = icon_button(text, tip, (32, 24), True, "font-size: 12px;",
                            lambda _=False, m=mode: self._acc_select(m))
            sel_bar.addWidget(b)
        sel_bar.addStretch(1)
        self.sync_btn = icon_button(
            "📤", "同步到追踪：把本页账号一键同步到「视频账号追踪」（按平台+ID去重，已存在跳过；分组一并带过去）",
            (36, 24), True, "font-size: 13px;", self._sync_to_tracker)
        sel_bar.addWidget(self.sync_btn)
        layout.addLayout(sel_bar)

        # 账号列表（复选框，固定高度滚动）
        self.acc_list = RowCheckList()
        self.acc_list.setFixedHeight(170)
        layout.addWidget(self.acc_list)

        # 采集行
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("采集:"))
        self.fetch_mode = QComboBox()
        self.fetch_mode.addItems(["全部账号", "选中账号", "按组采集"])
        self.fetch_mode.setFixedWidth(110)
        self.fetch_mode.currentTextChanged.connect(self._on_fetch_mode_change)
        op_row.addWidget(self.fetch_mode)
        self.fetch_group = QComboBox()
        self.fetch_group.setFixedWidth(110)
        op_row.addWidget(self.fetch_group)
        cnt_lbl = QLabel("条数:")
        cnt_lbl.setStyleSheet("padding-left: 10px;")
        op_row.addWidget(cnt_lbl)
        self.count_entry = QLineEdit("10")
        self.count_entry.setFixedWidth(56)
        op_row.addWidget(self.count_entry)
        zero_lbl = QLabel("(0=无上限)")
        zero_lbl.setStyleSheet("color: #888; font-size: 10px;")
        op_row.addWidget(zero_lbl)
        self.fetch_btn = QPushButton("获取最新")
        self.fetch_btn.clicked.connect(self._fetch)
        op_row.addWidget(self.fetch_btn)
        self.pause_btn = icon_button(
            "⏸️", "暂停后当前账号采集完即停住，点「继续」恢复",
            (40, 30), True, "font-size: 15px;", self._toggle_pause)
        self.pause_btn.setEnabled(False)
        op_row.addWidget(self.pause_btn)
        self.cancel_btn = icon_button(
            "❌", "取消当前获取：未开始的账号直接跳过，正在采集的账号自然结束",
            (40, 30), True, "font-size: 15px;", self._cancel_fetch)
        self.cancel_btn.setEnabled(False)
        op_row.addWidget(self.cancel_btn)
        self.timer_btn = QPushButton("⏰ 定时获取")
        self.timer_btn.clicked.connect(self._toggle_timer)
        op_row.addWidget(self.timer_btn)
        self.timer_dot = QLabel("○")
        self.timer_dot.setStyleSheet("color: #888; font-size: 15px;")
        op_row.addWidget(self.timer_dot)
        op_row.addStretch(1)
        layout.addLayout(op_row)
        self._on_fetch_mode_change("全部账号")

        # 策略行：增量模式 + 搜索限流
        strat_row = QHBoxLayout()
        self.inc_check = QCheckBox("增量模式(写入数据库)")
        # 默认勾选 + 记住上次选择（写入 config，重启恢复）
        self.inc_check.setChecked(self._load_inc_checked())
        self.inc_check.toggled.connect(self._save_inc_checked)
        strat_row.addWidget(self.inc_check)
        strat_row.addSpacing(8)
        strat_row.addWidget(QLabel("限流:每"))
        self.rate_count_entry = QLineEdit("10")
        self.rate_count_entry.setFixedWidth(40)
        strat_row.addWidget(self.rate_count_entry)
        strat_row.addWidget(QLabel("个停"))
        self.rate_interval_entry = QLineEdit("10")
        self.rate_interval_entry.setFixedWidth(40)
        strat_row.addWidget(self.rate_interval_entry)
        sec_lbl = QLabel("秒")
        sec_lbl.setStyleSheet("font-size: 11px;")
        strat_row.addWidget(sec_lbl)
        self.date_btn = QPushButton("📅 日期段: 不限")
        self.date_btn.clicked.connect(self._show_date_range_dialog)
        strat_row.addSpacing(16)
        strat_row.addWidget(self.date_btn)
        self._load_date_range()
        strat_row.addStretch(1)
        layout.addLayout(strat_row)

        # 进度条行（实时显示当前抓到第几个账号）
        prog_row = QHBoxLayout()
        self.prog_label = QLabel("空闲")
        self.prog_label.setStyleSheet("color: #5a6072; font-size: 11px;")
        self.prog_label.setFixedWidth(260)
        prog_row.addWidget(self.prog_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("第 %v / %m 个账号（%p%）")
        self.progress_bar.setValue(0)
        prog_row.addWidget(self.progress_bar, 1)
        layout.addLayout(prog_row)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    # ---------- 设置页 ----------
    def _build_settings(self, f):
        outer = QVBoxLayout(f)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)

        title = QLabel("⚙️ 账号管理设置")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        outer.addWidget(title)

        # 添加账号卡片
        add_card = QWidget()
        add_card.setStyleSheet("QWidget { background: #ffffff; border-radius: 8px; }")
        ac = QVBoxLayout(add_card)
        ac.setContentsMargins(10, 10, 10, 10)
        ac.setSpacing(4)
        t1 = QLabel("添加账号（账号名 + 链接 + 组号）")
        t1.setStyleSheet("font-weight: bold; background: transparent;")
        ac.addWidget(t1)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("账号名:"))
        self.set_name_entry = QLineEdit()
        self.set_name_entry.setPlaceholderText("如 封茗囧菌抖音（留空自动获取）")
        r1.addWidget(self.set_name_entry, 1)
        r1.addWidget(QLabel("分组:"))
        self.set_group_sel = GroupMultiSelect(width=126)
        r1.addWidget(self.set_group_sel)
        ac.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("链接:"))
        self.set_url_entry = QLineEdit()
        self.set_url_entry.setPlaceholderText("账号主页链接（自动识别平台）")
        r2.addWidget(self.set_url_entry, 1)
        self.set_name_fetch_btn = icon_button(
            "🔍", "自动获取账号名（按链接识别平台并拉取昵称）", (32, 30), True,
            "font-size: 14px;",
            lambda: self._auto_fetch_name(self.set_url_entry, self.set_name_entry,
                                          self.set_name_fetch_btn))
        r2.addWidget(self.set_name_fetch_btn)
        self.set_add_btn = icon_button("➕", "添加账号", (40, 30), True, "font-size: 16px;", self._add_set)
        r2.addWidget(self.set_add_btn)
        ac.addLayout(r2)
        outer.addWidget(add_card)

        # 分组管理卡片（列表独立滚动，⬆⬇ 调整显示顺序）
        grp_card = QWidget()
        grp_card.setStyleSheet("QWidget { background: #ffffff; border-radius: 8px; }")
        gc = QVBoxLayout(grp_card)
        gc.setContentsMargins(10, 10, 10, 10)
        gc.setSpacing(4)
        t2 = QLabel("分组管理（组编号 + 组名，组号用于绑定账号）")
        t2.setStyleSheet("font-weight: bold; background: transparent;")
        gc.addWidget(t2)
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
        gc.addWidget(self.group_list_frame)
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
        gc.addLayout(addg)
        outer.addWidget(grp_card)

        # 已配置账号（列表独立滚动，底部保存栏固定不随列表滚动）
        acct_card = QWidget()
        acct_card.setStyleSheet("QWidget { background: #ffffff; border-radius: 8px; }")
        acc = QVBoxLayout(acct_card)
        acc.setContentsMargins(10, 10, 10, 10)
        acc.setSpacing(4)
        t3 = QLabel("已配置账号")
        t3.setStyleSheet("font-weight: bold; background: transparent;")
        acc.addWidget(t3)
        self.acct_set_frame = QScrollArea()
        self.acct_set_frame.setWidgetResizable(True)
        self.acct_set_frame.setStyleSheet(
            "QScrollArea { background: transparent; border: 1px solid #e3e7ee; border-radius: 6px; }")
        acontainer = QWidget()
        acontainer.setStyleSheet("background: transparent;")
        self.acct_set_layout = QVBoxLayout(acontainer)
        self.acct_set_layout.setContentsMargins(2, 2, 2, 2)
        self.acct_set_layout.setSpacing(3)
        self.acct_set_layout.addStretch(1)
        self.acct_set_frame.setWidget(acontainer)
        acc.addWidget(self.acct_set_frame, 1)
        # 底部保存栏（固定；由 _reload_account_settings 填充按钮）
        self.acct_save_bar = QHBoxLayout()
        acc.addLayout(self.acct_save_bar)
        outer.addWidget(acct_card, 1)

    # ---------- 账号列表 ----------
    # B站采集类型标签（其他平台无此概念）
    _KIND_TAG = {"video": "作品", "dynamic": "动态", "all": "作品+动态"}

    def _reload_account_list(self):
        from accounts_manager import load_accounts
        from platforms import URLRecognizer
        self.acc_list.clear()
        accounts = [a for a in load_accounts() if a.url]  # 过滤分组占位账号（url 为空）
        self.acc_accounts = accounts
        if not accounts:
            item = QListWidgetItem("（暂无账号，请在上方添加）")
            item.setFlags(Qt.NoItemFlags)
            self.acc_list.addItem(item)
            return
        for i, a in enumerate(accounts, 1):
            plat = URLRecognizer.recognize(a.url) or "?"
            if plat == "bilibili":
                from accounts_manager import infer_kind_from_url
                kind = getattr(a, "kind", "") or infer_kind_from_url(a.url)
                # 自动（无 /video /dynamic 后缀的主页链接）实际按「作品+动态」混合采集
                plat = f"B站·{self._KIND_TAG.get(kind, '作品+动态')}"
            gids = ",".join(a.group_ids) if a.group_ids else ""
            name = a.name or ""
            prefix = f"{name} " if name else ""
            text = f"[{i}] {prefix}[{plat}] {a.url[:40]}" + (f" [组{gids}]" if gids else "")
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            self.acc_list.addItem(item)

    def _acc_select(self, mode):
        for row in range(self.acc_list.count()):
            item = self.acc_list.item(row)
            if not (item.flags() & Qt.ItemIsUserCheckable):
                continue
            if mode == "all":
                item.setCheckState(Qt.Checked)
            elif mode == "none":
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def _on_fetch_mode_change(self, mode):
        self.fetch_group.setEnabled(mode == "按组采集")

    def _add(self):
        url = self.url_entry.text().strip()
        if not url:
            self.app.toast("请输入账号 URL", "warning")
            return
        name = self.name_entry.text().strip()
        gid = self.add_gid_entry.text().strip()
        if name:
            self._do_add(url, name, gid)
        else:
            # 名称留空 → 自动获取「昵称+平台」（如 封茗囧菌抖音）
            self.app.toast("名称留空，正在自动获取账号名...", "info")
            self.add_btn.setEnabled(False)

            def work():
                from platforms.account_names import fetch_display_name
                return fetch_display_name(url)

            def done(result, err):
                self.add_btn.setEnabled(True)
                if err:
                    self.app.toast(f"自动获取失败：{str(err)[:60]}，已按无名称添加", "warning")
                auto_name, _ferr = result or ("", "")
                self._do_add(url, auto_name, gid)

            run_bg(work, done)

    def _do_add(self, url: str, name: str, gid: str):
        from accounts_manager import add_account, get_groups
        groups = get_groups()
        gids = [g.strip() for g in gid.replace("，", ",").split(",") if g.strip()]
        gname = groups.get(gids[0], "") if gids else ""
        if add_account(url, name=name, group_id=gid, group_name=gname):
            self.url_entry.clear()
            self.name_entry.clear()
            self.add_gid_entry.clear()
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)
        else:
            self.app.toast("账号已存在，无需重复添加", "warning")

    def _add_set(self):
        """设置页添加账号（账号名 + 链接 + 分组多选）。"""
        name = self.set_name_entry.text().strip()
        url = self.set_url_entry.text().strip()
        if not url:
            self.app.toast("请输入账号链接", "warning")
            return
        gids = self.set_group_sel.selected()
        if name:
            self._do_add_set(url, name, gids)
        else:
            # 名称留空 → 自动获取「昵称+平台」（如 封茗囧菌抖音）
            self.app.toast("名称留空，正在自动获取账号名...", "info")
            self.set_add_btn.setEnabled(False)

            def work():
                from platforms.account_names import fetch_display_name
                return fetch_display_name(url)

            def done(result, err):
                self.set_add_btn.setEnabled(True)
                if err:
                    self.app.toast(f"自动获取失败：{str(err)[:60]}，已按无名称添加", "warning")
                auto_name, _ferr = result or ("", "")
                self._do_add_set(url, auto_name, gids)

            run_bg(work, done)

    def _do_add_set(self, url: str, name: str, gids: list):
        gid = ",".join(gids)
        from accounts_manager import add_account, get_groups
        gname = get_groups().get(gids[0], "") if gids else ""
        if add_account(url, name=name, group_id=gid, group_name=gname):
            self.set_name_entry.clear()
            self.set_url_entry.clear()
            self.app.toast(f"✅ 已添加账号「{name or url[:30]}」", "success")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)
        else:
            self.app.toast("账号已存在，无需重复添加", "warning")

    # ---------- 同步到「视频账号追踪」 ----------
    def _sync_to_tracker(self):
        """把本页账号一键同步到「视频账号追踪」：按平台+ID去重，已存在跳过；
        账号名/链接/分组一并带过去，分组不存在时按同组号+组名自动创建。"""
        from accounts_manager import load_accounts as load_fm_accounts, get_groups as fm_groups
        src_accounts = load_fm_accounts()
        if not src_accounts:
            self.app.toast("「账号获取」中暂无账号，先添加账号", "warning")
            return
        from tracker.accounts import (parse_url, load_accounts, save_accounts,
                                      get_groups as tk_groups, save_group)
        dst_accounts = load_accounts()

        def _key(url):
            plat, pid = parse_url(url)
            return (plat, pid) if plat and pid else ("__url__", (url or "").strip())

        existing = {_key(a.get("url", "")) for a in dst_accounts}
        new_items, skipped, new_gids = [], 0, set()
        for acc in src_accounts:
            key = _key(acc.url)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            gids = [str(g) for g in (acc.group_ids or []) if str(g).strip()]
            new_gids.update(gids)
            new_items.append({"name": acc.name or "", "url": acc.url,
                              "platform": _key(acc.url)[0] or "", "group_ids": gids})
        if not new_items:
            self.app.toast(f"共 {skipped} 个账号均已存在于「视频账号追踪」，无需同步", "info")
            return
        msg = (f"把「账号获取」的账号同步到「视频账号追踪」？\n\n"
               f"共 {len(src_accounts)} 个账号：新增 {len(new_items)} 个，已存在跳过 {skipped} 个。\n\n"
               + "\n".join(f"· {(i['name'] or i['url'][:36])}" for i in new_items[:8])
               + ("\n…等" if len(new_items) > 8 else ""))
        if QMessageBox.question(self, "同步到视频账号追踪", msg,
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        dst_accounts.extend(new_items)
        save_accounts(dst_accounts)
        # 分组带过去：追踪侧缺的组按同组号+组名补建
        src_groups = fm_groups()
        dst_groups = tk_groups()
        for gid in new_gids:
            if gid not in dst_groups and src_groups.get(gid):
                save_group(gid, src_groups[gid])
        self.app.toast(f"✅ 已同步 {len(new_items)} 个账号到「视频账号追踪」"
                       + (f"（跳过已存在 {skipped} 个）" if skipped else ""), "success")

    # ---------- 自动获取账号名 ----------
    def _auto_fetch_name(self, url_entry, name_entry, btn):
        """按链接识别平台并拉取昵称，填充到账号名输入框（后台线程，不卡 UI）。"""
        url = url_entry.text().strip()
        if not url:
            self.app.toast("请先输入链接", "warning")
            return
        btn.setEnabled(False)
        btn.setText("⏳")

        def work():
            from platforms.account_names import fetch_account_name
            return fetch_account_name(url)

        def done(result, err):
            btn.setEnabled(True)
            btn.setText("🔍")
            if err:
                self.app.toast(f"获取失败：{err[:60]}", "error")
                return
            name, ferr = result or ("", "")
            if name:
                name_entry.setText(name)
                self.app.toast(f"✅ 已获取账号名「{name}」", "success")
            else:
                self.app.toast(ferr or "未获取到账号名", "error")

        run_bg(work, done)

    # ---------- 暂停 ----------
    def _toggle_pause(self):
        if not self._fetching:
            return
        if self._pause_evt.is_set():
            self._pause_evt.clear()
            self.pause_btn.setText("▶️")
            self.pause_btn.setStyleSheet(icon_btn_style(
                True, "background: #eab308; color: white; font-size: 15px;"))
            self.log_text.append("⏸ 已暂停（当前账号采集完成后停住）")
            self.prog_label.setText("⏸ 已暂停，等待继续...")
        else:
            self._pause_evt.set()
            self.pause_btn.setText("⏸️")
            self.pause_btn.setStyleSheet(icon_btn_style(True, "font-size: 15px;"))
            self.log_text.append("▶ 已继续采集")
            self.prog_label.setText("继续采集中...")

    def _cancel_fetch(self):
        """取消当前获取：未开始的账号直接跳过，在飞账号自然跑完（不硬中断线程）。"""
        if not self._fetching:
            return
        self._cancel_evt.set()
        # 若正卡在暂停门闩，先放行让线程看到取消标记后快速退出
        self._pause_evt.set()
        self.cancel_btn.setEnabled(False)
        self.log_text.append("✕ 已请求取消（在飞账号自然结束，未开始的账号跳过）")
        self.prog_label.setText("✕ 正在取消...")

    def _set_fetch_ui(self, running: bool):
        """获取开始/结束时同步暂停/取消按钮状态（手动与定时获取共用）。"""
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running)
        if running:
            self._cancel_evt.clear()  # 每轮获取开始前复位取消标记
        else:
            self._pause_evt.set()  # 复位门闩，避免下轮启动即处于暂停态
            self.pause_btn.setText("⏸️")
            self.pause_btn.setStyleSheet(icon_btn_style(True, "font-size: 15px;"))

    # ---------- 获取 ----------
    def _read_int(self, edit, default):
        try:
            return int(edit.text().strip() or str(default))
        except ValueError:
            return default

    def _read_float(self, edit, default):
        try:
            return float(edit.text().strip() or str(default))
        except ValueError:
            return default

    def _fetch(self):
        if self._fetching:
            self.app.toast("正在获取中，请稍候", "warning")
            return
        accounts, err = self._resolve_target_accounts()
        if err:
            self.app.toast(err, "warning")
            return
        count = self._read_int(self.count_entry, 10)
        rate_count = self._read_int(self.rate_count_entry, 0)
        rate_interval = self._read_float(self.rate_interval_entry, 0)
        incremental = self.inc_check.isChecked()
        self.fetch_btn.setEnabled(False)
        self._set_fetch_ui(True)
        self.log_text.setPlainText(f"⏳ 开始获取 {len(accounts)} 个账号的最新内容...\n")
        self.progress_bar.setRange(0, max(len(accounts), 1))
        self.progress_bar.setValue(0)
        self.prog_label.setText("准备中...")

        def work():
            return self._collect_accounts_worker(
                accounts, count, rate_count=rate_count, rate_interval=rate_interval,
                incremental=incremental, on_event=self._make_event_bridge(),
                pause_event=self._pause_evt, cancel_event=self._cancel_evt,
                date_start=self._date_range.get("start", ""),
                date_end=self._date_range.get("end", ""))

        def done(result, err):
            self.fetch_btn.setEnabled(True)
            self._set_fetch_ui(False)
            self._fetching = False
            if err:
                self.log_text.append(f"❌ {err}")
                self.prog_label.setText("❌ 获取失败")
                return
            total, _log = result
            if self._cancel_evt.is_set():
                self.log_text.append(f"\n✕ 已取消（本次已获取 {total} 条，已存入「已获取」）")
                self.prog_label.setText("✕ 已取消")
            else:
                self.log_text.append(f"\n✅ 共获取 {total} 条（已存入「已获取」）")
                self.prog_label.setText("✅ 获取完成")
            refresh_results_count(self.app)

        self._fetching = True
        run_bg(work, done)

    def _make_event_bridge(self):
        """后台线程事件 → 主线程实时更新日志/进度条。"""
        def on_event(event):
            post_to_main(self._on_fetch_event, event)
        return on_event

    def _on_fetch_event(self, event):
        """主线程处理采集事件：log=追加一行日志，progress=更新进度条。"""
        t = event.get("type")
        if t == "log":
            text = event.get("text", "")
            if text:
                self.log_text.append(text)
        elif t == "progress":
            total = event.get("total", 0)
            self.progress_bar.setRange(0, max(total, 1))
            self.progress_bar.setValue(event.get("done", 0))
            label = event.get("label", "")
            if label:
                self.prog_label.setText(label)

    def _resolve_target_accounts(self):
        """根据采集模式解析目标账号列表。返回 (accounts, err_msg)。"""
        from accounts_manager import load_accounts, get_accounts_by_group
        mode = self.fetch_mode.currentText()
        accounts = [a for a in load_accounts() if a.url]  # 过滤分组占位账号
        if not accounts:
            return [], "暂无账号，请先添加"
        if mode == "选中账号":
            selected = []
            for row in range(self.acc_list.count()):
                item = self.acc_list.item(row)
                if item.checkState() == Qt.Checked and row < len(self.acc_accounts):
                    selected.append(self.acc_accounts[row])
            accounts = selected
        elif mode == "按组采集":
            gv = self.fetch_group.currentText()
            if gv == "无分组":
                return [], "请选择分组"
            gid = gv.split(":")[0].strip()
            accounts = [a for a in get_accounts_by_group(gid) if a.url]
        if not accounts:
            return [], "没有可采集的账号"
        return accounts, None

    def _collect_accounts_worker(self, accounts, count, rate_count=0, rate_interval=0, incremental=False, on_event=None, pause_event=None, cancel_event=None, date_start="", date_end=""):
        """采集账号最新内容，返回 (total, log)。供手动与定时获取共用。

        多账号并发采集（ThreadPoolExecutor，上限 4 线程）：原串行逐个等待，
        账号多时总耗时 ≈ 各账号耗时之和，并发后 ≈ 最慢一个账号的耗时。

        on_event(dict)：实时事件回调（后台线程调用），{"type": "log", "text": ...}
        或 {"type": "progress", "done": x, "total": y, "label": ...}。
        rate_count/rate_interval: 搜索限流（每网站累计抓到 rate_count 个暂停 rate_interval 秒）。
        incremental: 增量模式（True=追加到默认汇总文件，False=单独导出独立文件）。
        pause_event: threading.Event，clear 时在账号边界停住（在飞账号自然跑完）。
        cancel_event: threading.Event，set 后未开始的账号直接跳过（在飞账号自然跑完）。
        date_start/date_end: 日期段过滤（'YYYY-MM-DD'，空=该端不限），
            只保留发布时间落在范围内的条目（时间解析失败的条目保留）。
        """
        from concurrent.futures import ThreadPoolExecutor
        from accounts_manager import infer_kind_from_url
        from platforms import URLRecognizer, get_parser
        total = 0
        log = []

        def emit(line):
            # 逐行实时推送 + 保留完整日志（定时模式结束后打印汇总）
            log.append(line)
            if on_event is not None:
                on_event({"type": "log", "text": line})

        # 日期段边界（开始 00:00:00 ~ 结束 23:59:59），两端都空 = 不过滤
        date_lo = _date_bound_ts(date_start, True)
        date_hi = _date_bound_ts(date_end, False)
        if date_lo is not None or date_hi is not None:
            emit(f"🗓 日期段过滤已启用: {date_start or '从头'} ~ {date_end or '到现在'}（只保留该范围内发布的内容）")

        n = len(accounts)
        if on_event is not None:
            on_event({"type": "progress", "done": 0, "total": n, "label": "准备中..."})
        all_posts = []
        # 0 = 无上限（抓完所有分页）
        fetch_count = count if count > 0 else 9999

        # 线程安全 stdout 捕获：redirect_stdout 是全局替换，多线程并发采集会互相覆盖，
        # 改用 thread-local 代理——各采集线程的 print 只进自己的缓冲区
        import sys
        import threading

        class _TLStdout(io.TextIOBase):
            def __init__(self, orig):
                self._local = threading.local()
                self._orig = orig

            def write(self, s):
                buf = getattr(self._local, "buf", None)
                if buf is not None:
                    buf.write(s)
                else:
                    self._orig.write(s)
                return len(s)

            def flush(self):
                pass

        proxy = _TLStdout(sys.stdout)

        def _fetch_one(job):
            """单个账号采集任务（线程池内执行）。返回 (i, a, platform, posts, detail, error)。"""
            i, a = job
            # 暂停门闩：clear 时阻塞在账号边界，set 后继续（在飞账号自然跑完）
            if pause_event is not None:
                pause_event.wait()
            # 已取消：还没开始的账号直接跳过（线程池排队任务快速返回）
            if cancel_event is not None and cancel_event.is_set():
                return i, a, "", [], "", "__cancelled__"
            platform = URLRecognizer.recognize(a.url)
            name = a.name or (a.url[:36] if a.url else "未命名")
            if not platform:
                return i, a, "", [], "", "无法识别链接"
            parser = get_parser(platform)
            if not parser or not hasattr(parser, "fetch_user_posts"):
                return i, a, platform, [], "", "不支持时间线"
            # B站按账号 kind 决定只采视频/只采动态/混合（存量账号 kind 空时按 URL 后缀推断）；其他平台忽略
            account_kind = getattr(a, "kind", "") or infer_kind_from_url(a.url)
            if platform == "bilibili" and account_kind in ("video", "dynamic", "all"):
                kwargs = {"kind": account_kind}
            else:
                kwargs = {}
            buf = io.StringIO()
            proxy._local.buf = buf
            try:
                posts = parser.fetch_user_posts(a.url, count=fetch_count, **kwargs)
            except Exception as e:  # noqa: BLE001
                return i, a, platform, [], "", str(e)
            finally:
                proxy._local.buf = None
            return i, a, platform, posts, buf.getvalue(), ""

        old_stdout = sys.stdout
        sys.stdout = proxy
        try:
            # 并发上限 4：过高并发容易触发平台风控，且 Playwright 兜底时资源开销大
            workers = min(4, max(1, n))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                # map 按账号顺序产出结果（日志顺序与串行版一致），任务本身并发执行
                outcomes = list(ex.map(_fetch_one, list(enumerate(accounts, 1))))
        finally:
            sys.stdout = old_stdout

        per_platform = {}  # 平台 -> 已抓数量（用于限流）
        done_cnt = 0
        for i, a, platform, posts, detail, error in outcomes:
            name = a.name or (a.url[:36] if a.url else "未命名")
            done_cnt += 1
            if on_event is not None:
                on_event({"type": "progress", "done": done_cnt, "total": n,
                          "label": f"[{i}/{n}] {platform} · {name}"})
            if error == "__cancelled__":
                emit(f"[{i}/{n}] ⊘ 已取消，跳过")
                continue
            if error == "无法识别链接":
                emit(f"[{i}/{n}] 无法识别: {a.url[:50]}")
                continue
            if error == "不支持时间线":
                emit(f"[{i}/{n}] {platform} 不支持时间线")
                continue
            if error:
                emit(f"[{i}/{n}] {platform} 失败: {error}")
                continue
            # 日期段过滤（时间解析失败的条目保留）
            if date_lo is not None or date_hi is not None:
                before_n = len(posts)
                posts = _filter_posts_by_date(posts, date_lo, date_hi)
                if len(posts) != before_n:
                    emit(f"[{i}/{n}] 🗓 日期段过滤: {before_n} → {len(posts)} 条（范围外 {before_n - len(posts)} 条）")
            emit(f"[{i}/{n}] ⏳ {platform} · {name} → 获取到 {len(posts)} 条")
            _collect(posts)
            all_posts.extend(posts)
            total += len(posts)
            # 搜索限流：每网站累计抓到 rate_count 个后暂停 rate_interval 秒（暂停门闩优先）
            if (rate_count > 0 and rate_interval > 0 and posts
                    and not (cancel_event is not None and cancel_event.is_set())):
                per_platform[platform] = per_platform.get(platform, 0) + len(posts)
                if per_platform[platform] >= rate_count:
                    emit(f"    ⏸ 限流：{platform} 已抓 {per_platform[platform]} 个，暂停 {rate_interval}s")
                    if pause_event is not None:
                        pause_event.wait()
                    time.sleep(rate_interval)
                    per_platform[platform] = 0
            # 区分主页作品 / 动态统计
            dyns = sum(1 for p in posts if getattr(p, "kind", "") == "dynamic")
            videos = len(posts) - dyns
            kind_tag = {"video": "[作品] ", "dynamic": "[动态] "}.get(
                getattr(a, "kind", "") or infer_kind_from_url(a.url), "")
            if dyns and videos:
                emit(f"[{i}/{n}] ✅ {kind_tag}{platform} 获取到 {len(posts)} 条（作品 {videos} / 动态 {dyns}）")
            elif dyns:
                emit(f"[{i}/{n}] ✅ {kind_tag}{platform} 获取到 {len(posts)} 条（动态 {dyns}）")
            else:
                emit(f"[{i}/{n}] ✅ {kind_tag}{platform} 获取到 {len(posts)} 条（作品 {videos}）")
            if detail:
                for line in detail.splitlines():
                    line = line.strip()
                    if line:
                        emit(f"    {line}")
        # 自动导出（增量 or 单独）
        if all_posts:
            export_msg = self._auto_export(all_posts, incremental)
            if export_msg:
                emit(export_msg)
        if on_event is not None:
            cancelled = cancel_event is not None and cancel_event.is_set()
            on_event({"type": "progress", "done": n, "total": n,
                      "label": "✕ 已取消" if cancelled else "✅ 全部完成"})
        return total, log

    def _auto_export(self, posts, incremental):
        """抓取完成后自动导出。incremental=True 增量入库（分平台分表），False 单独导出独立文件。"""
        try:
            if incremental:
                # 增量：写入增量数据库（config/increment.db，分平台分表、按去重键忽略重复）
                from toolbox import increment_db
                added = increment_db.save_incremental(posts)
                if any(added.values()):
                    parts = "、".join(f"{p} +{n}" for p, n in added.items() if n)
                    return f"💾 已增量写入数据库: {increment_db.db_path()}（{parts}）"
                return f"💾 无新增（已全部在库中）: {increment_db.db_path()}"
            else:
                # 单独导出独立文件（已存在则跳过，用户移走/删除/改名后重新保存）
                from toolbox.config_manager import get_export_subdir
                out_dir = str(get_export_subdir("video_data"))
                from exporters import export_per_video_flat
                paths, skipped = export_per_video_flat(posts, output_dir=out_dir,
                                                       formats=["json", "md"], skip_existing=True)
                if skipped and not paths:
                    return f"⚠️ 文件已存在，请勿重复保存（{len(skipped)} 个）"
                msg = f"💾 已单独导出 {len(paths)} 个文件到 {out_dir}"
                if skipped:
                    msg += f"（跳过 {len(skipped)} 个已存在）"
                return msg
        except Exception as e:
            return f"⚠️ 自动导出失败: {e}"

    # ---------- 增量模式勾选持久化 ----------
    @staticmethod
    def _load_inc_checked() -> bool:
        try:
            from toolbox.config_manager import load_convert_config
            return bool(load_convert_config().get("inc_db_checked", True))
        except Exception:
            return True

    def _save_inc_checked(self, checked):
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            cfg["inc_db_checked"] = bool(checked)
            save_convert_config(cfg)
        except Exception:
            pass

    # ---------- 日期段获取（按发布时间过滤） ----------
    def _load_date_range(self):
        """从配置恢复日期段（start/end 为 "" 表示该端不限）。"""
        self._date_range = {"start": "", "end": ""}
        try:
            from toolbox.config_manager import load_convert_config
            cfg = load_convert_config()
            self._date_range = {
                "start": str(cfg.get("fetch_date_start", "") or ""),
                "end": str(cfg.get("fetch_date_end", "") or ""),
            }
        except Exception:
            pass
        self._update_date_btn()

    def _save_date_range(self):
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            cfg["fetch_date_start"] = self._date_range.get("start", "")
            cfg["fetch_date_end"] = self._date_range.get("end", "")
            save_convert_config(cfg)
        except Exception:
            pass

    def _update_date_btn(self):
        s = self._date_range.get("start", "")
        e = self._date_range.get("end", "")
        if not s and not e:
            self.date_btn.setText("📅 日期段: 不限")
        else:
            self.date_btn.setText(f"📅 日期段: {s or '从头'} ~ {e or '到现在'}")

    def _date_range_desc(self) -> str:
        s = self._date_range.get("start", "")
        e = self._date_range.get("end", "")
        if not s and not e:
            return "不限"
        return f"{s or '从头'} ~ {e or '到现在'}"

    def _show_date_range_dialog(self):
        """日历选择日期段：两个日历表 + 开始/结束不限勾选；确定后对之后的获取生效。"""
        win = QDialog(self)
        win.setWindowTitle("📅 日期段获取（按发布时间过滤）")
        win.resize(640, 460)
        v = QVBoxLayout(win)
        hint = QLabel("选择要保留的发布时间范围（在日历上点选日期；勾「不限」则该端不设边界，"
                      "如「从头 ~ 2026-09-20」= 从最早的到 9月20日 的全部内容）")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555; font-size: 11px; padding: 4px;")
        v.addWidget(hint)

        # 两个日历表并排
        cal_row = QHBoxLayout()
        left_v = QVBoxLayout()
        left_lbl = QLabel("开始日期")
        left_lbl.setStyleSheet("font-weight: bold;")
        left_v.addWidget(left_lbl)
        cal_start = QCalendarWidget()
        cal_start.setGridVisible(True)
        left_v.addWidget(cal_start)
        left_w = QWidget(); left_w.setLayout(left_v)
        cal_row.addWidget(left_w)
        right_v = QVBoxLayout()
        right_lbl = QLabel("结束日期")
        right_lbl.setStyleSheet("font-weight: bold;")
        right_v.addWidget(right_lbl)
        cal_end = QCalendarWidget()
        cal_end.setGridVisible(True)
        right_v.addWidget(cal_end)
        right_w = QWidget(); right_w.setLayout(right_v)
        cal_row.addWidget(right_w)
        v.addLayout(cal_row, 1)

        # 不限勾选（勾选后对应日历禁用）
        opt_row = QHBoxLayout()
        chk_start_any = QCheckBox("开始不限（从头）")
        chk_end_any = QCheckBox("结束不限（到现在）")
        chk_start_any.setChecked(not self._date_range.get("start"))
        chk_end_any.setChecked(not self._date_range.get("end"))
        today = QDate.currentDate()
        cal_start.setSelectedDate(QDate.fromString(self._date_range.get("start"), "yyyy-MM-dd")
                                  if self._date_range.get("start") else today)
        cal_end.setSelectedDate(QDate.fromString(self._date_range.get("end"), "yyyy-MM-dd")
                                if self._date_range.get("end") else today)

        def _sync_cal():
            cal_start.setEnabled(not chk_start_any.isChecked())
            cal_end.setEnabled(not chk_end_any.isChecked())
        chk_start_any.toggled.connect(lambda _c: _sync_cal())
        chk_end_any.toggled.connect(lambda _c: _sync_cal())
        _sync_cal()
        opt_row.addWidget(chk_start_any)
        opt_row.addStretch(1)
        opt_row.addWidget(chk_end_any)
        v.addLayout(opt_row)

        # 按钮行
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清除全部（不限）")
        clear_btn.setStyleSheet("color: #c62828;")
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("✅ 确定")
        ok_btn.setStyleSheet("background: #2e7d32; color: white; border-radius: 4px; padding: 4px 14px;")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        v.addLayout(btn_row)

        def _apply():
            s = "" if chk_start_any.isChecked() else cal_start.selectedDate().toString("yyyy-MM-dd")
            e = "" if chk_end_any.isChecked() else cal_end.selectedDate().toString("yyyy-MM-dd")
            if s and e and s > e:
                QMessageBox.warning(win, "日期有误", "开始日期不能晚于结束日期", QMessageBox.Ok)
                return
            self._date_range = {"start": s, "end": e}
            self._save_date_range()
            self._update_date_btn()
            if s or e:
                self.app.toast(f"📅 日期段已设为：{s or '从头'} ~ {e or '到现在'}（下次获取生效）", "success")
            else:
                self.app.toast("📅 日期段已清除，获取不再按时间过滤", "info")
            win.accept()

        def _clear():
            self._date_range = {"start": "", "end": ""}
            self._save_date_range()
            self._update_date_btn()
            self.app.toast("📅 日期段已清除，获取不再按时间过滤", "info")
            win.accept()

        ok_btn.clicked.connect(lambda *a: _apply())
        cancel_btn.clicked.connect(win.reject)
        clear_btn.clicked.connect(lambda *a: _clear())
        win.exec()

    # ---------- 定时获取 ----------
    def _toggle_timer(self):
        if self._timer_running:
            self._stop_timer()
        else:
            self._show_timer_dialog()

    def _show_timer_dialog(self):
        win = QDialog(self)
        win.setWindowTitle("⏰ 定时获取")
        win.setFixedSize(320, 210)
        v = QVBoxLayout(win)
        t = QLabel("设置定时获取间隔")
        t.setStyleSheet("font-size: 14px; font-weight: bold;")
        v.addWidget(t, 0, Qt.AlignHCenter)
        sub = QLabel("按当前采集模式自动获取最新内容")
        sub.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(sub, 0, Qt.AlignHCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(QLabel("间隔(分钟):"))
        entry = QLineEdit("60")
        entry.setFixedWidth(80)
        row.addWidget(entry)
        row.addStretch(1)
        v.addLayout(row)

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
            self.timer_btn.setText("⏹ 停止定时")
            self.timer_btn.setStyleSheet("background: #c62828; color: white;")
            self.timer_dot.setText("●")
            self.timer_dot.setStyleSheet("color: #4caf50; font-size: 15px;")
            self.app.toast(f"⏰ 定时获取已启动（每 {m} 分钟）", "success")
            # 启动后先执行一次，之后每隔 interval 执行一次（QTimer 替代后台 sleep 循环）
            self._run_timer_fetch()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._run_timer_fetch)
            self._timer.start(self._timer_interval * 1000)

        start_btn = QPushButton("▶ 启动")
        start_btn.setFixedSize(160, 32)
        start_btn.setStyleSheet("background: #2e7d32; color: white;")
        start_btn.clicked.connect(do_start)
        v.addWidget(start_btn, 0, Qt.AlignHCenter)
        win.exec()

    def _stop_timer(self):
        self._timer_running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.timer_btn.setText("⏰ 定时获取")
        self.timer_btn.setStyleSheet("")
        self.timer_dot.setText("○")
        self.timer_dot.setStyleSheet("color: #888; font-size: 15px;")
        self.app.toast("定时获取已停止", "info")

    def _run_timer_fetch(self):
        """定时获取：按当前采集模式（全部/选中/按组）采集最新内容。"""
        if not self._timer_running:
            return
        if self._fetching:
            return  # 上次获取未完成，跳过本轮
        accounts, err = self._resolve_target_accounts()
        if err:
            return  # 静默跳过（定时任务不弹窗）
        mode = self.fetch_mode.currentText()
        count = self._read_int(self.count_entry, 10)
        rate_count = self._read_int(self.rate_count_entry, 0)
        rate_interval = self._read_float(self.rate_interval_entry, 0)
        incremental = self.inc_check.isChecked()

        def work():
            return self._collect_accounts_worker(
                accounts, count, rate_count=rate_count, rate_interval=rate_interval,
                incremental=incremental, on_event=self._make_event_bridge(),
                pause_event=self._pause_evt, cancel_event=self._cancel_evt,
                date_start=self._date_range.get("start", ""),
                date_end=self._date_range.get("end", ""))

        def done(result, err):
            self._set_fetch_ui(False)
            self._fetching = False
            ts = datetime.now().strftime("%H:%M:%S")
            if err:
                self.log_text.append(f"[{ts}] ⏰ 定时获取失败: {err}")
                self.prog_label.setText("❌ 定时获取失败")
                return
            total, _log = result
            if self._cancel_evt.is_set():
                self.log_text.append(f"[{ts}] ⏰ 定时获取已取消（已获取 {total} 条）")
                self.prog_label.setText("✕ 已取消")
            else:
                self.log_text.append(f"[{ts}] ⏰ 定时获取[{mode}]完成：共 {total} 条（已存入「已获取」）")
            refresh_results_count(self.app)
            if not self.isVisible():
                # 用户已切到其他页：提示定时获取仍在后台生效
                self.app.toast(f"⏰ 定时获取已在后台完成（共 {total} 条）", "info")

        self._fetching = True
        self._set_fetch_ui(True)
        run_bg(work, done)

    # ---------- 分组管理（设置页） ----------
    def _reload_group_list(self):
        self._refresh_gid_menus()
        _clear_layout(self.group_list_layout)
        self.group_list_layout.addStretch(1)
        groups = self._groups()
        n = len(groups)
        if not groups:
            lbl = QLabel("（暂无分组）")
            lbl.setStyleSheet("color: #888;")
            self.group_list_layout.takeAt(self.group_list_layout.count() - 1)
            self.group_list_layout.addWidget(lbl)
            self.group_list_layout.addStretch(1)
        else:
            gids = list(groups.keys())
            for pos, gid in enumerate(gids):
                gname = groups[gid]
                row = QWidget()
                row.setStyleSheet("QWidget { background: #ffffff; border-radius: 6px; }")
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 4, 6, 4)
                h.setSpacing(4)
                gid_lbl = QLabel(f"{gid}")
                gid_lbl.setFixedWidth(44)
                gid_lbl.setStyleSheet("font-weight: bold; color: #1976d2; background: transparent;")
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
                del_btn = icon_button(
                    "✕", "删除该分组（绑定该组的账号自动解绑）", (30, 24), False,
                    "background: #c62828; color: white; font-size: 13px;",
                    lambda _=False, g=gid: self._delete_group(g))
                h.addWidget(del_btn)
                self.group_list_layout.takeAt(self.group_list_layout.count() - 1)
                self.group_list_layout.addWidget(row)
                self.group_list_layout.addStretch(1)
        # 分组少时紧凑、多时封顶滚动（每行约 34px）
        self.group_list_frame.setFixedHeight(min(206, max(84, n * 34 + 16)))
        # 同步刷新已配置账号列表的组号下拉
        self._reload_account_settings()

    def _move_group(self, gid, delta):
        """上移/下移分组（重排 groups.json 键顺序，立即生效）。"""
        from accounts_manager import move_group
        if move_group(gid, delta):
            self._reload_group_list()
            self.app.toast("✅ 分组顺序已更新", "success")

    def _add_group(self):
        gid = self.gid_entry.text().strip()
        gname = self.gname_entry.text().strip()
        from accounts_manager import GROUP_ID_PATTERN, save_group
        if not gid or not GROUP_ID_PATTERN.match(gid):
            self.app.toast("组号必须为重复数字，如 11、22", "warning")
            return
        if not gname:
            self.app.toast("请输入组名", "warning")
            return
        if save_group(gid, gname):
            self.gid_entry.clear()
            self.gname_entry.clear()
            QTimer.singleShot(150, self._reload_group_list)
            self.app.toast(f"✅ 已保存分组：{gid} {gname}", "success")

    def _delete_group(self, gid):
        from accounts_manager import delete_group
        delete_group(gid)
        self.app.toast(f"✅ 已删除分组 {gid}（绑定账号已解绑）", "info")
        QTimer.singleShot(150, self._reload_group_list)
        QTimer.singleShot(150, self._reload_account_list)
        QTimer.singleShot(150, self._reload_account_settings)

    def _reload_account_settings(self):
        from accounts_manager import load_accounts, get_groups, infer_kind_from_url
        from platforms import URLRecognizer
        kind_options = ["", "video", "dynamic", "all"]
        kind_label = {"": "自动", "video": "[作品]", "dynamic": "[动态]", "all": "[全部]"}
        _clear_layout(self.acct_set_layout)
        self.acct_set_layout.addStretch(1)
        self._acct_set_rows = []
        self.save_all_btn = QPushButton("💾 保存全部修改")
        self.save_all_btn.setStyleSheet("background: #2e7d32; color: white; padding: 4px 14px;")
        accounts = [a for a in load_accounts() if a.url]  # 过滤分组占位账号
        groups = get_groups()
        if not accounts:
            lbl = QLabel("（暂无账号）")
            lbl.setStyleSheet("color: #888;")
            self.acct_set_layout.takeAt(self.acct_set_layout.count() - 1)
            self.acct_set_layout.addWidget(lbl)
            self.acct_set_layout.addStretch(1)
            return
        for i, a in enumerate(accounts, 1):
            row = QWidget()
            row.setStyleSheet("QWidget { background: #ffffff; border-radius: 6px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 6, 4)
            h.setSpacing(4)
            plat = URLRecognizer.recognize(a.url) or "?"
            name = a.name or ""
            info = f"{name}  [{plat}]  {a.url[:40]}" if name else f"[{plat}]  {a.url[:50]}"
            h.addWidget(QLabel(info), 1)
            # 分组多选（当前绑定的全部组）
            gsel = GroupMultiSelect(width=110)
            gsel.set_groups(groups)
            gsel.set_selected(a.group_ids)
            h.addWidget(gsel)
            # 采集类型下拉（B站生效）
            cur_kind = a.kind or infer_kind_from_url(a.url)
            ksel = QComboBox()
            ksel.addItems([kind_label.get(k, k) for k in kind_options])
            ksel.setCurrentText(kind_label.get(cur_kind, "自动"))
            ksel.setFixedWidth(80)
            h.addWidget(ksel)
            # 记录行控件与初始值，改动后启用底部「保存全部修改」
            self._acct_set_rows.append((a.url, gsel, ksel, gsel.selected(), ksel.currentText()))
            gsel.changed.connect(lambda b=self.save_all_btn: self._mark_set_dirty(b))
            ksel.currentTextChanged.connect(lambda _t, b=self.save_all_btn: self._mark_set_dirty(b))
            # 上移/下移（直接改 accounts.txt 顺序，立即生效）
            up_btn = icon_button("⬆️", "上移一位", (28, 26), True, "font-size: 12px;")
            up_btn.setEnabled(i > 1)
            up_btn.clicked.connect(lambda _=False, u=a.url: self._move_account_row(u, -1))
            h.addWidget(up_btn)
            down_btn = icon_button("⬇️", "下移一位", (28, 26), True, "font-size: 12px;")
            down_btn.setEnabled(i < len(accounts))
            down_btn.clicked.connect(lambda _=False, u=a.url: self._move_account_row(u, 1))
            h.addWidget(down_btn)
            del_btn = icon_button(
                "✕", "删除该账号", (30, 26), False,
                "background: #c62828; color: white; font-size: 13px;",
                lambda _=False, u=a.url: self._delete_account_row(u))
            h.addWidget(del_btn)
            self.acct_set_layout.takeAt(self.acct_set_layout.count() - 1)
            self.acct_set_layout.addWidget(row)
            self.acct_set_layout.addStretch(1)
        # 底部统一保存按钮（固定在卡片底栏，不随列表滚动）
        _clear_layout(self.acct_save_bar)
        self.save_all_btn.setEnabled(False)
        self.save_all_btn.clicked.connect(self._save_all_account_rows)
        self.acct_save_bar.addWidget(self.save_all_btn)
        self.acct_save_bar.addStretch(1)
        hint = QLabel("修改组号/采集类型后点此一次性保存")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        self.acct_save_bar.addWidget(hint)

    @staticmethod
    def _mark_set_dirty(btn):
        btn.setEnabled(True)

    def _save_all_account_rows(self):
        """一次性保存所有行的分组/采集类型修改（替代逐行保存）。"""
        from accounts_manager import update_account, get_groups
        try:
            groups = get_groups()
            changed = 0
            for url, gsel, ksel, orig_g, orig_k in getattr(self, "_acct_set_rows", []):
                gids = gsel.selected()
                gid = ",".join(gids)
                kind_rev = {"自动": "", "[作品]": "video", "[动态]": "dynamic", "[全部]": "all"}
                kind = kind_rev.get(ksel.currentText(), "")
                if gids == orig_g and ksel.currentText() == orig_k:
                    continue  # 无变化
                gname = groups.get(gids[0], "") if gids else ""
                if update_account(url, group_id=gid, group_name=gname, kind=kind):
                    changed += 1
            if changed:
                self.app.toast(f"✅ 已保存 {changed} 项账号设置", "success")
            else:
                self.app.toast("没有需要保存的修改", "info")
        except Exception as e:
            self.app.toast(f"❌ 保存失败：{str(e)[:60]}", "error")
        self.save_all_btn.setEnabled(False)
        QTimer.singleShot(150, self._reload_account_list)
        QTimer.singleShot(150, self._reload_account_settings)
        QTimer.singleShot(150, self._reload_group_list)

    def _delete_account_row(self, url):
        from accounts_manager import remove_account
        if remove_account(url):
            self.app.toast("✅ 已删除该账号", "info")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)

    def _move_account_row(self, url, delta):
        """上移/下移已配置账号（顺序即采集顺序，保存后立即生效）。"""
        from accounts_manager import move_account
        if move_account(url, delta):
            self.app.toast("✅ 账号顺序已更新", "success")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
