"""账号获取页（PySide6 版）：添加账号、批量抓取最新内容（结果自动存入「已获取」）。

对应原版 gui/info_pages.py 的 InfoAccountsPage：
- 功能页：添加账号行 / 账号勾选列表 / 采集模式（全部/选中/按组）/ 条数 /
  定时获取 / 增量模式 / 搜索限流 / 日志
- 设置页：添加账号（账号名+链接+组号下拉）/ 分组管理 / 已配置账号
  （组号下拉 + 采集类型下拉 + 保存/删除）
"""

import io
import json
import os
import threading
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidgetItem, QMessageBox, QPushButton, QProgressBar, QScrollArea,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import _collect, post_to_main, refresh_results_count, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


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
        return ["无分组"] + [f"{gid}:{gname}" for gid, gname in sorted(self._groups().items())]

    def _refresh_gid_menus(self):
        opts = self._group_options()
        self.fetch_group.blockSignals(True)
        self.fetch_group.clear()
        self.fetch_group.addItems(opts)
        self.fetch_group.blockSignals(False)
        if hasattr(self, "set_group_sel"):
            cur = self.set_group_sel.currentText()
            self.set_group_sel.clear()
            self.set_group_sel.addItems(opts)
            if cur in opts:
                self.set_group_sel.setCurrentText(cur)

    # ---------- 功能页 ----------
    def _build_func(self, f):
        layout = QVBoxLayout(f)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("👥 账号获取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("添加账号、批量抓取最新内容（结果自动存入「已获取」）")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        # 添加账号行
        add_row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("账号主页 URL")
        self.url_entry.returnPressed.connect(self._add)
        add_row.addWidget(self.url_entry, 1)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText("账号名")
        self.name_entry.setFixedWidth(110)
        add_row.addWidget(self.name_entry)
        self.add_gid_entry = QLineEdit()
        self.add_gid_entry.setPlaceholderText("组号(逗号分隔多组)")
        self.add_gid_entry.setFixedWidth(130)
        add_row.addWidget(self.add_gid_entry)
        self.add_btn = QPushButton("➕ 添加")
        self.add_btn.clicked.connect(self._add)
        add_row.addWidget(self.add_btn)
        layout.addLayout(add_row)

        # 全选/反选/清除
        sel_bar = QHBoxLayout()
        for text, mode in (("☑ 全选", "all"), ("🔄 反选", "invert"), ("□ 清除", "none")):
            b = QPushButton(text)
            b.setFixedHeight(24)
            b.setStyleSheet("font-size: 11px;")
            b.clicked.connect(lambda _=False, m=mode: self._acc_select(m))
            sel_bar.addWidget(b)
        sel_bar.addStretch(1)
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
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setToolTip("暂停后当前账号采集完即停住，点「继续」恢复")
        self.pause_btn.clicked.connect(self._toggle_pause)
        op_row.addWidget(self.pause_btn)
        self.cancel_btn = QPushButton("✕ 取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip("取消当前获取：未开始的账号直接跳过，正在采集的账号自然结束")
        self.cancel_btn.clicked.connect(self._cancel_fetch)
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.set_layout = QVBoxLayout(inner)
        self.set_layout.setContentsMargins(0, 0, 8, 0)
        self.set_layout.setSpacing(6)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        title = QLabel("⚙️ 账号管理设置")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.set_layout.addWidget(title)

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
        self.set_name_entry.setPlaceholderText("如 封茗囧菌抖音")
        r1.addWidget(self.set_name_entry, 1)
        r1.addWidget(QLabel("组号:"))
        self.set_group_sel = QComboBox()
        self.set_group_sel.setFixedWidth(120)
        r1.addWidget(self.set_group_sel)
        ac.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("链接:"))
        self.set_url_entry = QLineEdit()
        self.set_url_entry.setPlaceholderText("账号主页链接（自动识别平台）")
        r2.addWidget(self.set_url_entry, 1)
        add_btn2 = QPushButton("➕ 添加")
        add_btn2.clicked.connect(self._add_set)
        r2.addWidget(add_btn2)
        ac.addLayout(r2)
        self.set_layout.addWidget(add_card)

        # 分组管理卡片
        grp_card = QWidget()
        grp_card.setStyleSheet("QWidget { background: #ffffff; border-radius: 8px; }")
        gc = QVBoxLayout(grp_card)
        gc.setContentsMargins(10, 10, 10, 10)
        gc.setSpacing(4)
        t2 = QLabel("分组管理（组编号 + 组名，组号用于绑定账号）")
        t2.setStyleSheet("font-weight: bold; background: transparent;")
        gc.addWidget(t2)
        self.group_list_host = QWidget()
        self.group_list_layout = QVBoxLayout(self.group_list_host)
        self.group_list_layout.setContentsMargins(0, 0, 0, 0)
        self.group_list_layout.setSpacing(3)
        gc.addWidget(self.group_list_host)
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
        gc.addLayout(addg)
        self.set_layout.addWidget(grp_card)

        # 已配置账号
        acct_card = QWidget()
        acct_card.setStyleSheet("QWidget { background: #ffffff; border-radius: 8px; }")
        acc = QVBoxLayout(acct_card)
        acc.setContentsMargins(10, 10, 10, 10)
        acc.setSpacing(4)
        t3 = QLabel("已配置账号")
        t3.setStyleSheet("font-weight: bold; background: transparent;")
        acc.addWidget(t3)
        self.acct_set_host = QWidget()
        self.acct_set_layout = QVBoxLayout(self.acct_set_host)
        self.acct_set_layout.setContentsMargins(0, 0, 0, 0)
        self.acct_set_layout.setSpacing(3)
        acc.addWidget(self.acct_set_host)
        self.set_layout.addWidget(acct_card)
        self.set_layout.addStretch(1)

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
        """设置页添加账号（账号名 + 链接 + 组号下拉）。"""
        name = self.set_name_entry.text().strip()
        url = self.set_url_entry.text().strip()
        if not url:
            self.app.toast("请输入账号链接", "warning")
            return
        v = self.set_group_sel.currentText()
        gid = "" if v == "无分组" else v.split(":")[0].strip()
        from accounts_manager import add_account, get_groups
        gname = get_groups().get(gid, "") if gid else ""
        if add_account(url, name=name, group_id=gid, group_name=gname):
            self.set_name_entry.clear()
            self.set_url_entry.clear()
            self.app.toast(f"✅ 已添加账号「{name or url[:30]}」", "success")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)
        else:
            self.app.toast("账号已存在，无需重复添加", "warning")

    # ---------- 暂停 ----------
    def _toggle_pause(self):
        if not self._fetching:
            return
        if self._pause_evt.is_set():
            self._pause_evt.clear()
            self.pause_btn.setText("▶ 继续")
            self.pause_btn.setStyleSheet("background: #eab308; color: white;")
            self.log_text.append("⏸ 已暂停（当前账号采集完成后停住）")
            self.prog_label.setText("⏸ 已暂停，等待继续...")
        else:
            self._pause_evt.set()
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setStyleSheet("")
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
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setStyleSheet("")

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
                pause_event=self._pause_evt, cancel_event=self._cancel_evt)

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

    def _collect_accounts_worker(self, accounts, count, rate_count=0, rate_interval=0, incremental=False, on_event=None, pause_event=None, cancel_event=None):
        """采集账号最新内容，返回 (total, log)。供手动与定时获取共用。

        多账号并发采集（ThreadPoolExecutor，上限 4 线程）：原串行逐个等待，
        账号多时总耗时 ≈ 各账号耗时之和，并发后 ≈ 最慢一个账号的耗时。

        on_event(dict)：实时事件回调（后台线程调用），{"type": "log", "text": ...}
        或 {"type": "progress", "done": x, "total": y, "label": ...}。
        rate_count/rate_interval: 搜索限流（每网站累计抓到 rate_count 个暂停 rate_interval 秒）。
        incremental: 增量模式（True=追加到默认汇总文件，False=单独导出独立文件）。
        pause_event: threading.Event，clear 时在账号边界停住（在飞账号自然跑完）。
        cancel_event: threading.Event，set 后未开始的账号直接跳过（在飞账号自然跑完）。
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
                pause_event=self._pause_evt, cancel_event=self._cancel_evt)

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
        groups = self._groups()
        if not groups:
            lbl = QLabel("（暂无分组）")
            lbl.setStyleSheet("color: #888;")
            self.group_list_layout.addWidget(lbl)
        else:
            for gid, gname in sorted(groups.items()):
                row = QWidget()
                row.setStyleSheet("QWidget { background: #ffffff; border-radius: 6px; }")
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 4, 6, 4)
                gid_lbl = QLabel(f"{gid}")
                gid_lbl.setFixedWidth(44)
                gid_lbl.setStyleSheet("font-weight: bold; color: #1976d2; background: transparent;")
                h.addWidget(gid_lbl)
                h.addWidget(QLabel(gname), 1)
                del_btn = QPushButton("✕")
                del_btn.setFixedSize(30, 24)
                del_btn.setStyleSheet("background: #c62828; color: white;")
                del_btn.clicked.connect(lambda _=False, g=gid: self._delete_group(g))
                h.addWidget(del_btn)
                self.group_list_layout.addWidget(row)
        # 同步刷新已配置账号列表的组号下拉
        self._reload_account_settings()

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
        QTimer.singleShot(150, self._reload_group_list)
        QTimer.singleShot(150, self._reload_account_list)
        QTimer.singleShot(150, self._reload_account_settings)

    def _reload_account_settings(self):
        from accounts_manager import load_accounts, get_groups, infer_kind_from_url
        from platforms import URLRecognizer
        kind_options = ["", "video", "dynamic", "all"]
        kind_label = {"": "自动", "video": "[作品]", "dynamic": "[动态]", "all": "[全部]"}
        _clear_layout(self.acct_set_layout)
        self._acct_set_rows = []
        self.save_all_btn = QPushButton("💾 保存全部修改")
        self.save_all_btn.setStyleSheet("background: #2e7d32; color: white; padding: 4px 14px;")
        accounts = [a for a in load_accounts() if a.url]  # 过滤分组占位账号
        groups = get_groups()
        group_opts = ["无分组"] + [f"{gid}:{gname}" for gid, gname in sorted(groups.items())]
        if not accounts:
            lbl = QLabel("（暂无账号）")
            lbl.setStyleSheet("color: #888;")
            self.acct_set_layout.addWidget(lbl)
            return
        for i, a in enumerate(accounts, 1):
            row = QWidget()
            row.setStyleSheet("QWidget { background: #ffffff; border-radius: 6px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 6, 4)
            plat = URLRecognizer.recognize(a.url) or "?"
            name = a.name or ""
            info = f"{name}  [{plat}]  {a.url[:40]}" if name else f"[{plat}]  {a.url[:50]}"
            h.addWidget(QLabel(info), 1)
            # 组号下拉（当前主组）
            cur_gid = a.group_id or ""
            cur_val = "无分组"
            for opt in group_opts:
                if opt != "无分组" and opt.split(":")[0] == cur_gid:
                    cur_val = opt
                    break
            gsel = QComboBox()
            gsel.addItems(group_opts)
            gsel.setCurrentText(cur_val)
            gsel.setFixedWidth(100)
            h.addWidget(gsel)
            # 采集类型下拉（B站生效）
            cur_kind = a.kind or infer_kind_from_url(a.url)
            ksel = QComboBox()
            ksel.addItems([kind_label.get(k, k) for k in kind_options])
            ksel.setCurrentText(kind_label.get(cur_kind, "自动"))
            ksel.setFixedWidth(80)
            h.addWidget(ksel)
            # 记录行控件与初始值，改动后启用底部「保存全部修改」
            self._acct_set_rows.append((a.url, gsel, ksel, cur_val, ksel.currentText()))
            gsel.currentTextChanged.connect(lambda _t, b=self.save_all_btn: self._mark_set_dirty(b))
            ksel.currentTextChanged.connect(lambda _t, b=self.save_all_btn: self._mark_set_dirty(b))
            # 上移/下移（直接改 accounts.txt 顺序，立即生效）
            up_btn = QPushButton("↑")
            up_btn.setFixedSize(28, 26)
            up_btn.setToolTip("上移一位")
            up_btn.setEnabled(i > 1)
            up_btn.clicked.connect(lambda _=False, u=a.url: self._move_account_row(u, -1))
            h.addWidget(up_btn)
            down_btn = QPushButton("↓")
            down_btn.setFixedSize(28, 26)
            down_btn.setToolTip("下移一位")
            down_btn.setEnabled(i < len(accounts))
            down_btn.clicked.connect(lambda _=False, u=a.url: self._move_account_row(u, 1))
            h.addWidget(down_btn)
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(30, 26)
            del_btn.setStyleSheet("background: #c62828; color: white;")
            del_btn.clicked.connect(lambda _=False, u=a.url: self._delete_account_row(u))
            h.addWidget(del_btn)
            self.acct_set_layout.addWidget(row)
        # 底部统一保存按钮（替代逐行 💾）
        bar = QHBoxLayout()
        self.save_all_btn.setEnabled(False)
        self.save_all_btn.clicked.connect(self._save_all_account_rows)
        bar.addWidget(self.save_all_btn)
        bar.addStretch(1)
        hint = QLabel("修改组号/采集类型后点此一次性保存")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        bar.addWidget(hint)
        self.acct_set_layout.addLayout(bar)

    @staticmethod
    def _mark_set_dirty(btn):
        btn.setEnabled(True)

    def _save_all_account_rows(self):
        """一次性保存所有行的组号/采集类型修改（替代逐行保存）。"""
        from accounts_manager import update_account, get_groups
        groups = get_groups()
        changed = 0
        for url, gsel, ksel, orig_g, orig_k in getattr(self, "_acct_set_rows", []):
            v = gsel.currentText()
            gid = "" if v == "无分组" else v.split(":")[0].strip()
            kind_rev = {"自动": "", "[作品]": "video", "[动态]": "dynamic", "[全部]": "all"}
            kind = kind_rev.get(ksel.currentText(), "")
            if v == orig_g and ksel.currentText() == orig_k:
                continue  # 无变化
            gname = groups.get(gid, "") if gid else ""
            if update_account(url, group_id=gid, group_name=gname, kind=kind):
                changed += 1
        if changed:
            self.app.toast(f"✅ 已保存 {changed} 项账号设置", "success")
        else:
            self.app.toast("没有需要保存的修改", "info")
        self.save_all_btn.setEnabled(False)
        QTimer.singleShot(150, self._reload_account_list)
        QTimer.singleShot(150, self._reload_account_settings)
        QTimer.singleShot(150, self._reload_group_list)

    def _delete_account_row(self, url):
        from accounts_manager import remove_account
        if remove_account(url):
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)

    def _move_account_row(self, url, delta):
        """上移/下移已配置账号（顺序即采集顺序，保存后立即生效）。"""
        from accounts_manager import move_account
        if move_account(url, delta):
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
