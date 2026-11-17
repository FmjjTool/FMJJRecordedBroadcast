"""信息获取页（PySide6 版，原「账号获取」）：批量输入链接（账号/作品自动识别）+ 账号采集。

「单个解析」「批量导入」已合并到本页：顶部批量输入框一行一个，
账号主页链接 → 添加账号；作品链接/分享文案/纯 BV 号 → 解析并存入「已获取」。
- 功能页：批量输入行 / 账号勾选列表 / 采集模式（全部/选中/按组）/ 条数 /
  定时获取 / 增量模式 / 搜索限流 / 日志
- 设置页：添加账号（账号名+链接+分组多选）/ 分组管理 / 已配置账号
  （分组多选 + 采集类型下拉 + 保存/删除）
"""

import json
import os
import re
import threading
from collections import deque
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListView, QListWidgetItem, QMessageBox, QPushButton, QProgressBar,
    QScrollArea, QSizePolicy, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt import download_ops, theme
from gui_qt.common import _collect, _db_insert, icon_button, post_to_main, refresh_results_count, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import GroupMultiSelect, RowCheckList
from gui_qt.toolbox_common import ToolPageHeader


def _set_button_variant(button, variant="secondary", compact=False):
    """保留图标按钮尺寸，颜色交给全局主题；状态切换也使用同一语义样式。"""
    changed = button.property("variant") != variant
    button.setProperty("variant", variant)
    button.setStyleSheet("padding: 0px;" if compact else "")
    if changed and button.isVisible():
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()


def _is_account_url(url: str) -> bool:
    """账号主页链接判定：命中 → 按账号添加，其余一律按作品链接解析。

    覆盖 B站空间 / 抖音用户 / 微博用户（u/、n/、裸 UID）/ 快手主页 /
    小红书用户主页；作品页（/video/、/note/、/explore/ 等）不命中。
    """
    u = (url or "").strip().lower()
    if not u:
        return False
    if ("space.bilibili.com/" in u or "m.bilibili.com/space/" in u
            or "douyin.com/user/" in u or "iesdouyin.com/share/user/" in u
            or "xiaohongshu.com/user/profile/" in u
            or "kuaishou.com/profile/" in u):
        return True
    p = urlparse(u)
    host, path = p.netloc, p.path.strip("/")
    if host.endswith("weibo.com"):
        return (path.startswith(("u/", "n/"))
                or bool(re.fullmatch(r"\d{5,15}", path)))
    if host.endswith("weibo.cn"):
        return path.startswith(("u/", "profile/", "n/"))
    return False


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
        # 采集轮次号：每轮 +1。取消会立即释放 UI（在飞账号后台收尾），
        # 旧轮的 done()/事件流凭轮次号识别为过期，不再动新一轮的 UI
        self._fetch_round = 0
        # 暂停门闩：set=放行 / clear=暂停（采集线程在账号边界等待）
        # 每轮获取开始时重建，旧轮的暂停/取消标记不会被新轮复位
        self._pause_evt = threading.Event()
        self._pause_evt.set()
        # 取消标记：set 后未开始的账号直接跳过（在飞账号自然跑完不硬中断）
        self._cancel_evt = threading.Event()
        # 批量处理（账号添加 / 作品解析）状态：与账号采集互斥，按钮共享进度条
        self._batch_running = False
        self._batch_pause_evt = threading.Event()
        self._batch_pause_evt.set()
        self._batch_cancel_evt = threading.Event()
        # 结果揭示队列：采集线程先落库，主线程每 150ms 揭示一条作品，
        # 避免多个账号/一页数据同时完成时「已获取」一次性全部刷出
        self._reveal_queue = deque()
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._drain_reveal)

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
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        from gui_qt.help_overlay import attach_help_overlay
        help_btn = attach_help_overlay(self, "信息获取 · 功能介绍", (
            "<p><b>批量追踪账号主页、一键抓取最新作品/动态，结果自动存入「已获取」。</b></p>"
            "<p>· <b>批量输入</b>（一行 = 一个条目）：账号主页链接（B站空间/抖音/微博/快手/小红书）"
            "→ 自动添加为账号；作品链接/分享文案/纯 BV 号 → 解析并存入「已获取」，结果同时进日志</p>"
            "<p>· <b>获取最新</b>：勾选账号后批量抓取，右下角可暂停 / 取消；"
            "抓取结果进入「已获取」与增量数据库</p>"
            "<p>· <b>增量模式</b>：只入库新内容（数据库去重），勾选后写入数据库</p>"
            "<p>· <b>获取后自动下载</b>：勾选后获取→保存→下载一次性完成；"
            "下载画质/线程数用「已获取」页的全局设置</p>"
            "<p>· <b>限流</b>：每平台累计抓到设定条数后冷却（同平台后续账号等冷却结束才发请求，"
            "抖音账号内部翻页同样停顿），防止请求过频触发风控</p>"
            "<p>· <b>定时获取</b>：按间隔自动抓取全部账号</p>"
            "<p>· <b>日期段</b>：日历选择发布时间范围（如 9月1日~9月20日、从头~9月20日），"
            "获取时只保留范围内的内容，范围持久保存</p>"
            "<p>· <b>双向同步</b>：本页账号 ⇄「视频账号追踪」互相同步，"
            "已存在的自动跳过，账号名/分组一并带过去（免重复录入）</p>"
            "<p>· <b>设置</b>：账号分组管理（组号+组名）、账号编辑（名称/URL/分组/排序）</p>"
            "<p>· 登录态由软件统一管理，抓取时自动套用</p>"))

        # 批量输入：一行一个，账号主页 → 添加账号，其余 → 解析存入「已获取」
        self.batch_text = QTextEdit()
        self.batch_text.setPlaceholderText(
            "一行一个，直接粘贴（支持整段分享文案）：\n"
            "· 账号主页链接（B站空间/抖音/微博/快手/小红书）→ 添加为账号\n"
            "· 作品/视频链接、纯 BV 号 → 解析并存入「已获取」")
        self.batch_text.setFixedHeight(88)
        batch_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.batch_text)
        batch_shortcut.activated.connect(self._process_batch)
        layout.addWidget(self.batch_text)

        batch_row = QHBoxLayout()
        self.process_btn = QPushButton("开始处理")
        self.process_btn.setProperty("variant", "primary")
        self.process_btn.setToolTip(
            "逐行识别：账号主页 → 添加账号；其余 → 解析作品并存入「已获取」（Ctrl+Enter）")
        self.process_btn.clicked.connect(self._process_batch)
        batch_row.addWidget(self.process_btn)
        self.batch_pause_btn = icon_button(
            "▮▮", "暂停批量处理：当前行处理完即停住，点「继续」恢复",
            (40, 30), True, "font-size: 15px;", self._toggle_batch_pause)
        self.batch_pause_btn.setEnabled(False)
        batch_row.addWidget(self.batch_pause_btn)
        self.batch_cancel_btn = icon_button(
            "×", "取消批量处理：未开始的行直接跳过", (40, 30), True,
            "font-size: 15px;", self._cancel_batch)
        self.batch_cancel_btn.setEnabled(False)
        batch_row.addWidget(self.batch_cancel_btn)
        import_btn = icon_button(
            "", "从文本文件导入链接列表（每行一个）", (40, 30), True,
            "font-size: 15px;", self._import_file)
        import_btn.setIcon(line_icon("folder", theme.color("text"), 16))
        batch_row.addWidget(import_btn)
        batch_row.addStretch(1)
        batch_row.addWidget(QLabel("账号分组:"))
        self.add_gid_entry = QLineEdit()
        self.add_gid_entry.setPlaceholderText("组号(逗号分隔多组)")
        self.add_gid_entry.setFixedWidth(130)
        self.add_gid_entry.setToolTip("仅对「账号主页」行生效：添加账号时绑定这些分组")
        batch_row.addWidget(self.add_gid_entry)
        batch_row.addWidget(help_btn)
        layout.addLayout(batch_row)

        # 全选/反选/清除
        sel_bar = QHBoxLayout()
        for text, mode, tip in (("", "all", "全选"), ("", "invert", "反选"), ("", "none", "清除选择")):
            b = icon_button(text, tip, (32, 24), False, "font-size: 12px;",
                            lambda _=False, m=mode: self._acc_select(m))
            b.setIcon(line_icon({"all": "check", "invert": "refresh", "none": "close"}[mode],
                                theme.color("text"), 13))
            sel_bar.addWidget(b)
        sel_bar.addStretch(1)
        self.sync_btn = icon_button(
            "", "同步到追踪：把本页账号一键同步到「视频账号追踪」（按平台+ID去重，已存在跳过；分组一并带过去）",
            (36, 24), True, "font-size: 13px;", self._sync_to_tracker)
        self.sync_btn.setIcon(line_icon("upload", theme.color("text"), 15))
        sel_bar.addWidget(self.sync_btn)
        self.sync_from_btn = icon_button(
            "", "从追踪同步：把「视频账号追踪」的账号一键同步到本页（按平台+ID去重，已存在跳过；分组一并带过来）",
            (36, 24), True, "font-size: 13px;", self._sync_from_tracker)
        self.sync_from_btn.setIcon(line_icon("download", theme.color("text"), 15))
        sel_bar.addWidget(self.sync_from_btn)
        layout.addLayout(sel_bar)

        # 账号列表（复选框，固定高度滚动；右键 打开/复制链接、修改、删除）
        self.acc_list = RowCheckList()
        self.acc_list.setFixedHeight(170)
        self.acc_list.setMinimumWidth(0)
        self.acc_list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.acc_list.setWordWrap(True)
        self.acc_list.setResizeMode(QListView.Adjust)
        self.acc_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.acc_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.acc_list.customContextMenuRequested.connect(self._acc_list_context_menu)
        layout.addWidget(self.acc_list)

        # 范围与操作各占一行，窄窗口仍完整展示暂停、取消和定时入口。
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
        op_row.addWidget(cnt_lbl)
        self.count_entry = QLineEdit("10")
        self.count_entry.setFixedWidth(56)
        op_row.addWidget(self.count_entry)
        zero_lbl = QLabel("(0=无上限)")
        zero_lbl.setProperty("role", "caption")
        op_row.addWidget(zero_lbl)
        op_row.addStretch(1)
        layout.addLayout(op_row)
        fetch_row = QHBoxLayout()
        self.fetch_btn = QPushButton("获取最新")
        self.fetch_btn.setProperty("variant", "primary")
        self.fetch_btn.clicked.connect(self._fetch)
        fetch_row.addWidget(self.fetch_btn)
        self.pause_btn = icon_button(
            "▮▮", "暂停后当前账号采集完即停住，点「继续」恢复",
            (40, 30), True, "font-size: 15px;", self._toggle_pause)
        self.pause_btn.setEnabled(False)
        fetch_row.addWidget(self.pause_btn)
        self.cancel_btn = icon_button(
            "×", "取消当前获取：未开始的账号直接跳过，正在采集的账号自然结束",
            (40, 30), True, "font-size: 15px;", self._cancel_fetch)
        self.cancel_btn.setEnabled(False)
        fetch_row.addWidget(self.cancel_btn)
        self.timer_btn = QPushButton("定时获取")
        self.timer_btn.clicked.connect(self._toggle_timer)
        fetch_row.addWidget(self.timer_btn)
        self.timer_dot = QLabel("○")
        self.timer_dot.setProperty("role", "caption")
        self.timer_dot.setStyleSheet("font-size: 15px;")
        fetch_row.addWidget(self.timer_dot)
        fetch_row.addStretch(1)
        layout.addLayout(fetch_row)
        self.fetch_mode.setCurrentText("选中账号")
        self._on_fetch_mode_change("选中账号")

        # 策略与限流/日期分开，保存的长日期范围不再撑宽窗口。
        strat_row = QHBoxLayout()
        self.inc_check = QCheckBox("增量模式(写入数据库)")
        # 默认勾选 + 记住上次选择（写入 config，重启恢复）
        self.inc_check.setChecked(self._load_inc_checked())
        self.inc_check.toggled.connect(self._save_inc_checked)
        strat_row.addWidget(self.inc_check)
        strat_row.addSpacing(8)
        self.auto_dl_check = QCheckBox("获取后自动下载")
        self.auto_dl_check.setToolTip(
            "勾选后：获取完成 → 保存（已获取/数据库）→ 自动下载本次获取的内容，"
            "一次性完成。下载画质/线程数用「已获取」页的全局设置")
        self.auto_dl_check.setChecked(self._load_auto_dl_checked())
        self.auto_dl_check.toggled.connect(self._save_auto_dl_checked)
        strat_row.addWidget(self.auto_dl_check)
        strat_row.addStretch(1)
        layout.addLayout(strat_row)
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("限流:每"))
        self.rate_count_entry = QLineEdit("10")
        self.rate_count_entry.setFixedWidth(40)
        limit_row.addWidget(self.rate_count_entry)
        limit_row.addWidget(QLabel("个停"))
        self.rate_interval_entry = QLineEdit("10")
        self.rate_interval_entry.setFixedWidth(40)
        limit_row.addWidget(self.rate_interval_entry)
        sec_lbl = QLabel("秒")
        sec_lbl.setProperty("role", "caption")
        limit_row.addWidget(sec_lbl)
        self.date_btn = QPushButton("日期段: 不限")
        self.date_btn.clicked.connect(self._show_date_range_dialog)
        limit_row.addSpacing(12)
        limit_row.addWidget(self.date_btn)
        self._load_date_range()
        limit_row.addStretch(1)
        layout.addLayout(limit_row)

        # 进度条行（实时显示当前抓到第几个账号）
        prog_row = QHBoxLayout()
        self.prog_label = QLabel("空闲")
        self.prog_label.setProperty("role", "caption")
        self.prog_label.setMinimumWidth(150)
        self.prog_label.setMaximumWidth(220)
        self.prog_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.prog_label.setWordWrap(True)
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

        for button in f.findChildren(QPushButton):
            variant = ("primary" if button in (self.process_btn, self.fetch_btn)
                       else "danger" if button in (self.batch_cancel_btn, self.cancel_btn)
                       else "secondary")
            _set_button_variant(button, variant, button.maximumWidth() < 80)

    # ---------- 设置页 ----------
    def _build_settings(self, f):
        outer = QVBoxLayout(f)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)

        outer.addWidget(ToolPageHeader("账号管理设置", "添加账号、整理分组，统一保存分组与采集类型。"))

        # 添加账号卡片
        add_card = QFrame()
        add_card.setProperty("variant", "card")
        ac = QVBoxLayout(add_card)
        ac.setContentsMargins(10, 10, 10, 10)
        ac.setSpacing(4)
        t1 = QLabel("添加账号（账号名 + 链接 + 组号）")
        t1.setProperty("role", "sectionTitle")
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
            "", "自动获取账号名（按链接识别平台并拉取昵称）", (32, 30), True,
            "font-size: 14px;",
            lambda: self._auto_fetch_name(self.set_url_entry, self.set_name_entry,
                                          self.set_name_fetch_btn))
        self.set_name_fetch_btn.setIcon(line_icon("search", theme.color("text"), 15))
        r2.addWidget(self.set_name_fetch_btn)
        self.set_add_btn = icon_button("", "添加账号", (40, 30), True, "font-size: 16px;", self._add_set)
        self.set_add_btn.setIcon(line_icon("plus", theme.color("text"), 16))
        r2.addWidget(self.set_add_btn)
        ac.addLayout(r2)
        outer.addWidget(add_card)

        # 分组管理卡片（列表独立滚动，调整显示顺序）
        grp_card = QFrame()
        grp_card.setProperty("variant", "card")
        gc = QVBoxLayout(grp_card)
        gc.setContentsMargins(10, 10, 10, 10)
        gc.setSpacing(4)
        t2 = QLabel("分组管理（组编号 + 组名，组号用于绑定账号）")
        t2.setProperty("role", "sectionTitle")
        t2.setWordWrap(True)
        gc.addWidget(t2)
        self.group_list_frame = QScrollArea()
        self.group_list_frame.setWidgetResizable(True)
        self.group_list_frame.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.group_list_frame.setStyleSheet(
            "QScrollArea { background: transparent; border: 1px solid palette(shadow); border-radius: 6px; }")
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
        self.gname_entry.setMinimumWidth(80)
        addg.addWidget(self.gname_entry, 1)
        newg_btn = icon_button("", "新建分组", (40, 30), True, "font-size: 16px;", self._add_group)
        newg_btn.setIcon(line_icon("plus", theme.color("text"), 16))
        addg.addWidget(newg_btn)
        gc.addLayout(addg)
        outer.addWidget(grp_card)

        # 已配置账号（列表独立滚动，底部保存栏固定不随列表滚动）
        acct_card = QFrame()
        acct_card.setProperty("variant", "card")
        acc = QVBoxLayout(acct_card)
        acc.setContentsMargins(10, 10, 10, 10)
        acc.setSpacing(4)
        t3 = QLabel("已配置账号")
        t3.setProperty("role", "sectionTitle")
        acc.addWidget(t3)
        self.acct_set_frame = QScrollArea()
        self.acct_set_frame.setWidgetResizable(True)
        self.acct_set_frame.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.acct_set_frame.setStyleSheet(
            "QScrollArea { background: transparent; border: 1px solid palette(shadow); border-radius: 6px; }")
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

        for button in f.findChildren(QPushButton):
            _set_button_variant(button, "primary" if button is self.set_add_btn else "secondary",
                                button.maximumWidth() < 80)

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

    # ---------- 批量处理（账号添加 / 作品解析） ----------

    def _import_file(self):
        """从文本文件导入链接列表（每行一个）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择链接列表文件", "", "文本文件 (*.txt);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f
                         if ln.strip() and not ln.strip().startswith("#")]
        except Exception as e:
            self.app.toast(f"读取失败: {str(e)[:60]}", "error")
            return
        self.batch_text.setPlainText("\n".join(lines))

    def open_url(self, url: str):
        """供其他页跳转：填入批量框并立即处理（如 B站综合搜索的视频结果）。"""
        self.batch_text.setPlainText(url)
        self._process_batch()

    def _toggle_batch_pause(self):
        if not self._batch_running:
            return
        if self._batch_pause_evt.is_set():
            self._batch_pause_evt.clear()
            self.batch_pause_btn.setText("▶")
            _set_button_variant(self.batch_pause_btn, "primary", compact=True)
            self._log("已暂停（当前行处理完成后停住）")
            self.prog_label.setText("已暂停，等待继续...")
        else:
            self._batch_pause_evt.set()
            self.batch_pause_btn.setText("▮▮")
            _set_button_variant(self.batch_pause_btn, compact=True)
            self._log("▶ 已继续批量处理")
            self.prog_label.setText("继续批量处理中...")

    def _cancel_batch(self):
        if not self._batch_running:
            return
        self._batch_cancel_evt.set()
        self._batch_pause_evt.set()  # 放行暂停中的线程，使其能走到取消检查
        self._log("已取消：未处理的行直接跳过")

    def _process_batch(self):
        """逐行处理批量输入：账号主页→添加账号，其余→解析作品存入「已获取」。

        唯一识别方式：一行 = 一个条目（自动从分享文案中提取链接）。
        """
        if self._fetching:
            self.app.toast("正在获取账号内容，请稍候", "warning")
            return
        if self._batch_running:
            return
        lines = [ln.strip() for ln in self.batch_text.toPlainText().splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            self.app.toast("请输入账号主页或作品链接（一行一个）", "warning")
            return
        gid = self.add_gid_entry.text().strip()
        self._batch_running = True
        # 取消后仍在后台收尾的旧采集轮次立即判为过期，避免其日志混进批量处理
        self._fetch_round += 1
        self._batch_cancel_evt.clear()
        self._batch_pause_evt.set()
        self.process_btn.setEnabled(False)
        self.batch_pause_btn.setEnabled(True)
        self.batch_cancel_btn.setEnabled(True)
        self.fetch_btn.setEnabled(False)
        self.log_text.setPlainText(f"开始批量处理 {len(lines)} 行...\n")
        self.progress_bar.setFormat("%v / %m 行（%p%）")
        self._set_batch_progress(0, len(lines), "准备中...")

        def work():
            from accounts_manager import add_account, get_groups, load_accounts
            from platforms.account_names import fetch_display_name
            ok_acc = dup_acc = ok_post = new_posts = fail = 0
            cancelled = False
            groups = get_groups()
            gids = [g.strip() for g in gid.replace("，", ",").split(",") if g.strip()]
            gname = groups.get(gids[0], "") if gids else ""
            existing = {a.url for a in load_accounts()}
            for idx, raw in enumerate(lines, 1):
                # 暂停门闩：clear 时在行边界停住
                self._batch_pause_evt.wait()
                if self._batch_cancel_evt.is_set():
                    cancelled = True
                    break
                prefix = f"[{idx}/{len(lines)}]"
                post_to_main(self._set_batch_progress, idx - 1, len(lines),
                             f"{prefix} 处理中...")
                url = download_ops.extract_share_url(raw)
                if _is_account_url(url):
                    if url in existing:
                        dup_acc += 1
                        self._log(f"{prefix} 账号已存在，跳过：{url[:60]}")
                        continue
                    try:
                        name, _err = fetch_display_name(url)
                    except Exception:
                        name = ""
                    if add_account(url, name=name, group_id=gid, group_name=gname):
                        existing.add(url)
                        ok_acc += 1
                        self._log(f"{prefix} 已添加账号：{name or url[:60]}")
                    else:
                        dup_acc += 1
                        self._log(f"{prefix} 账号已存在，跳过：{url[:60]}")
                    continue
                try:
                    info = download_ops.parse_url_info(raw)
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    self._log(f"{prefix} {url[:70]}（{e}）")
                    continue
                added = _collect([info])
                ok_post += 1
                new_posts += added
                tag = "已存入「已获取」" if added else "已存在，未重复入库"
                self._log(f"{prefix} {tag}：{info.summary()}")
            return ok_acc, dup_acc, ok_post, new_posts, fail, cancelled

        def done(result, err):
            self._batch_running = False
            self.process_btn.setEnabled(True)
            self.batch_pause_btn.setEnabled(False)
            self.batch_cancel_btn.setEnabled(False)
            self.fetch_btn.setEnabled(True)
            self._batch_pause_evt.set()
            self.batch_pause_btn.setText("▮▮")
            _set_button_variant(self.batch_pause_btn, compact=True)
            self.progress_bar.setFormat("第 %v / %m 个账号（%p%）")
            if err:
                self._log(f"批量处理失败: {err}")
                self.prog_label.setText("批量处理失败")
                return
            ok_acc, dup_acc, ok_post, new_posts, fail, cancelled = result
            self._set_batch_progress(len(lines), len(lines),
                                     "已取消" if cancelled else "批量处理完成")
            head = "已取消" if cancelled else "批量处理完成"
            self._log(f"{head}：账号新增 {ok_acc}（重复 {dup_acc}） / "
                      f"作品解析成功 {ok_post}（新增入库 {new_posts}，失败 {fail}）")
            refresh_results_count(self.app)
            if ok_acc:
                # 账号有变化才重建列表/分组下拉（避免无谓重绘）
                self._reload_account_list()
                self._reload_group_list()

        run_bg(work, done)

    def _set_batch_progress(self, done, total, label):
        """批量处理进度（主线程调用；与账号采集共用进度条）。"""
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        if label:
            self.prog_label.setText(label)

    def _log(self, text):
        """线程安全地往日志框追加一行（后台线程经 post_to_main 回主线程）。"""
        def apply():
            try:
                self.log_text.append(text)
                self.log_text.ensureCursorVisible()
            except Exception:
                pass
        post_to_main(apply)

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
            self.app.toast(f"已添加账号「{name or url[:30]}」", "success")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)
        else:
            self.app.toast("账号已存在，无需重复添加", "warning")

    # ---------- 同步到「视频账号追踪」 ----------
    def _sync_to_tracker(self):
        """把本页账号一键同步到「视频账号追踪」：按平台+ID去重，已存在跳过；
        账号名/链接/分组一并带过去，分组不存在时按同组号+组名自动创建。"""
        from tracker.accounts import plan_sync_from_fm, apply_sync_from_fm
        new_items, skipped = plan_sync_from_fm()
        if not new_items:
            self.app.toast(f"共 {skipped} 个账号均已存在于「视频账号追踪」，无需同步", "info")
            return
        msg = (f"把「信息获取」的账号同步到「视频账号追踪」？\n\n"
               f"共 {len(new_items) + skipped} 个账号：新增 {len(new_items)} 个，已存在跳过 {skipped} 个。\n\n"
               + "\n".join(f"· {(i['name'] or i['url'][:36])}" for i in new_items[:8])
               + ("\n…等" if len(new_items) > 8 else ""))
        if QMessageBox.question(self, "同步到视频账号追踪", msg,
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        apply_sync_from_fm(new_items)
        # 追踪页若已实例化，同步刷新其卡片/分组
        page = (getattr(self.app, "pages", {}) or {}).get("tracker")
        if page is not None:
            page.reload_accounts()
            page._reload_group_list()
        self.app.toast(f"已同步 {len(new_items)} 个账号到「视频账号追踪」"
                       + (f"（跳过已存在 {skipped} 个）" if skipped else ""), "success")

    # ---------- 从「视频账号追踪」反向同步 ----------
    def _sync_from_tracker(self):
        """把「视频账号追踪」的账号一键同步到本页：按平台+ID去重，已存在跳过；
        分组一并带过来，缺失分组按同组号+组名自动创建（与「同步到追踪」构成双向）。"""
        from tracker.accounts import plan_sync_to_fm, apply_sync_to_fm
        new_items, skipped = plan_sync_to_fm()
        if not new_items:
            self.app.toast(f"共 {skipped} 个账号均已存在，无需同步", "info")
            return
        msg = (f"把「视频账号追踪」的账号同步到「信息获取」？\n\n"
               f"共 {len(new_items) + skipped} 个账号：新增 {len(new_items)} 个，已存在跳过 {skipped} 个。\n\n"
               + "\n".join(f"· {(i['name'] or i['url'][:36])}" for i in new_items[:8])
               + ("\n…等" if len(new_items) > 8 else ""))
        if QMessageBox.question(self, "从追踪同步", msg,
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        apply_sync_to_fm(new_items)
        self._reload_account_list()
        self._reload_group_list()
        self._reload_account_settings()
        self.app.toast(f"已从追踪同步 {len(new_items)} 个账号"
                       + (f"（跳过已存在 {skipped} 个）" if skipped else ""), "success")

    # ---------- 自动获取账号名 ----------
    def _auto_fetch_name(self, url_entry, name_entry, btn):
        """按链接识别平台并拉取昵称，填充到账号名输入框（后台线程，不卡 UI）。"""
        url = url_entry.text().strip()
        if not url:
            self.app.toast("请先输入链接", "warning")
            return
        btn.setEnabled(False)
        btn.setText("")

        def work():
            from platforms.account_names import fetch_account_name
            return fetch_account_name(url)

        def done(result, err):
            btn.setEnabled(True)
            btn.setText("")
            if err:
                self.app.toast(f"获取失败：{err[:60]}", "error")
                return
            name, ferr = result or ("", "")
            if name:
                name_entry.setText(name)
                self.app.toast(f"已获取账号名「{name}」", "success")
            else:
                self.app.toast(ferr or "未获取到账号名", "error")

        run_bg(work, done)

    # ---------- 暂停 ----------
    def _toggle_pause(self):
        if not self._fetching:
            return
        if self._pause_evt.is_set():
            self._pause_evt.clear()
            self.pause_btn.setText("▶")
            _set_button_variant(self.pause_btn, "primary", compact=True)
            self.log_text.append("已暂停（当前账号采集完成后停住）")
            self.prog_label.setText("已暂停，等待继续...")
        else:
            self._pause_evt.set()
            self.pause_btn.setText("▮▮")
            _set_button_variant(self.pause_btn, compact=True)
            self.log_text.append("▶ 已继续采集")
            self.prog_label.setText("继续采集中...")

    def _cancel_fetch(self):
        """取消当前获取：未开始的账号直接跳过，UI 立即释放——可马上开始
        新一轮获取，不必等在飞账号自然跑完（旧轮由后台线程收尾，数据仍
        正常入库/导出，不硬中断线程）。"""
        if not self._fetching:
            return
        self._cancel_evt.set()
        # 若正卡在暂停门闩，先放行让线程看到取消标记后快速退出
        self._pause_evt.set()
        # 立即释放获取锁与按钮：旧轮 done() 回来时凭轮次号识别为过期，
        # 只刷新计数不再动 UI；用户可立即开始新一轮（旧轮在飞账号继续
        # 后台收尾，两轮并发不超过 4+4 个请求线程）
        self._fetching = False
        self.fetch_btn.setEnabled(True)
        self._set_fetch_ui(False)
        self.log_text.append("已取消（未开始的账号跳过；在飞账号后台自然收尾，可立即开始新获取）")
        self.prog_label.setText("已取消")

    def _set_fetch_ui(self, running: bool):
        """获取开始/结束时同步暂停/取消按钮状态（手动与定时获取共用）。"""
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running)
        if running:
            self._cancel_evt.clear()  # 每轮获取开始前复位取消标记
        else:
            self._pause_evt.set()  # 复位门闩，避免下轮启动即处于暂停态
            self.pause_btn.setText("▮▮")
            _set_button_variant(self.pause_btn, compact=True)

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
        if self._batch_running:
            self.app.toast("正在批量处理中，请稍候", "warning")
            return
        if self._fetching:
            self.app.toast("正在获取中，请稍候", "warning")
            return
        self._flush_reveal_queue()
        accounts, err = self._resolve_target_accounts()
        if err:
            self.app.toast(err, "warning")
            return
        count = self._read_int(self.count_entry, 10)
        rate_count = self._read_int(self.rate_count_entry, 0)
        rate_interval = self._read_float(self.rate_interval_entry, 0)
        incremental = self.inc_check.isChecked()
        self.fetch_btn.setEnabled(False)
        # 每轮独立事件：上一轮取消后即使还有在飞账号在后台收尾，也不会被
        # 新一轮的启动复位（旧轮的取消标记保持 set，未开始的账号照样跳过）
        self._fetch_round += 1
        round_id = self._fetch_round
        pause_evt = threading.Event()
        pause_evt.set()
        cancel_evt = threading.Event()
        self._pause_evt = pause_evt
        self._cancel_evt = cancel_evt
        self._set_fetch_ui(True)
        self.log_text.setPlainText(f"开始获取 {len(accounts)} 个账号的最新内容...\n")
        self.progress_bar.setRange(0, max(len(accounts), 1))
        self.progress_bar.setValue(0)
        self.prog_label.setText("准备中...")

        def work():
            return self._collect_accounts_worker(
                accounts, count, rate_count=rate_count, rate_interval=rate_interval,
                incremental=incremental, on_event=self._make_event_bridge(round_id),
                pause_event=pause_evt, cancel_event=cancel_evt,
                date_start=self._date_range.get("start", ""),
                date_end=self._date_range.get("end", ""), round_id=round_id)

        def done(result, err):
            if round_id != self._fetch_round:
                # 过期轮次（取消后已提前释放 UI，或已被更新一轮顶替）：
                # 数据在 worker 内已入库/导出，这里只刷新计数，不再动 UI
                refresh_results_count(self.app)
                return
            self.fetch_btn.setEnabled(True)
            self._set_fetch_ui(False)
            self._fetching = False
            if err:
                self.log_text.append(f"{err}")
                self.prog_label.setText("获取失败")
                return
            total, _log, new_posts = result
            if cancel_evt.is_set():
                self.log_text.append(f"\n已取消（本次已获取 {total} 条，已存入「已获取」）")
                self.prog_label.setText("已取消")
            else:
                self.log_text.append(f"\n共获取 {total} 条（已存入「已获取」）")
                if not self.auto_dl_check.isChecked():
                    self.prog_label.setText("获取完成")
            if not self._reveal_queue:
                refresh_results_count(self.app)
            # 获取后自动下载（勾选后保存+下载一次性完成；已取消的轮次不触发）
            if not cancel_evt.is_set() and self.auto_dl_check.isChecked() and new_posts:
                self._start_auto_download(new_posts)

        self._fetching = True
        run_bg(work, done)

    def _make_event_bridge(self, round_id):
        """后台线程事件 → 主线程实时更新日志/进度条。

        轮次过期（取消后旧轮仍在后台收尾）的事件直接丢弃，避免旧轮的
        收尾输出覆盖新一轮的日志/进度条。"""
        def on_event(event):
            if round_id != self._fetch_round:
                return
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

    # ---------- 结果逐账号揭示 ----------
    def _enqueue_reveal(self, posts, round_id):
        """采集线程 → 主线程：把结果按单条作品排队，逐条揭示到「已获取」。

        数据已在采集线程落库；这里只负责 UI 节奏。旧轮次的结果仍进内存
        （不能丢），但不刷新当前轮 UI。
        """
        if not posts:
            return
        if round_id != self._fetch_round:
            _collect(posts, persist=False)
            return
        self._reveal_queue.extend(posts)
        if not self._reveal_timer.isActive():
            self._reveal_timer.start(0)

    def _drain_reveal(self):
        """每次只放一条作品进「已获取」，连续拿到多条时也能逐条出现。"""
        if not self._reveal_queue:
            return
        item = self._reveal_queue.popleft()
        added = _collect([item], persist=False)
        if added:
            refresh_results_count(self.app)
        if self._reveal_queue:
            # 重复条目（抖音 on_items 已提前抛过）不占 150ms 节奏
            self._reveal_timer.start(150 if added else 0)

    def _flush_reveal_queue(self):
        """新一轮开始前清空旧队列：全部并入内存，避免旧结果卡在队列里。"""
        self._reveal_timer.stop()
        pending = self._reveal_queue
        self._reveal_queue = deque()
        if not pending:
            return
        _collect(pending, persist=False)
        refresh_results_count(self.app)

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

    def _collect_accounts_worker(self, accounts, count, rate_count=0, rate_interval=0, incremental=False, on_event=None, pause_event=None, cancel_event=None, date_start="", date_end="", round_id=0):
        """采集账号最新内容；并发/限流/stdout/日期过滤由 L3 服务执行。

        本方法只把 UI 侧的落库、逐条揭示与导出回调接入服务。
        """
        from toolbox.account_collection import collect_accounts
        if not round_id:
            round_id = self._fetch_round
        return collect_accounts(
            accounts, count,
            rate_count=rate_count, rate_interval=rate_interval,
            incremental=incremental, on_event=on_event,
            pause_event=pause_event, cancel_event=cancel_event,
            date_start=date_start, date_end=date_end, round_id=round_id,
            insert_items=_db_insert,
            reveal_items=lambda items, rid: post_to_main(
                self._enqueue_reveal, items, rid),
            persist_batch=self._persist_batch,
        )

    def _persist_batch(self, posts, incremental):
        """单账号最终入库/导出：一次写入 collected 主数据，增量模式再投影 increment。

        流式解析的早期预览仍走 insert_items；这里只在账号完成时落最终结果。
        返回 (短消息, 增量模式=平台->新增数 dict / 导出模式=写出文件数 int)。
        """
        try:
            from toolbox.collected_store import ingest_items
            added = ingest_items(posts, incremental=incremental)
            if incremental:
                parts = "、".join(f"{p} +{n}" for p, n in added.items() if n)
                msg = f"已入库 {parts}" if parts else "无新增（已全部在库中）"
                return msg, added
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("video_data"))
            from exporters import export_per_video_flat
            paths, skipped = export_per_video_flat(posts, output_dir=out_dir,
                                                   formats=["json", "md"], skip_existing=True)
            if skipped and not paths:
                return f"文件已存在，跳过（{len(skipped)} 个）", len(paths)
            msg = f"已导出 {len(paths)} 个文件"
            if skipped:
                msg += f"（跳过 {len(skipped)} 个已存在）"
            return msg, len(paths)
        except Exception as e:
            return f"保存失败: {e}", {}

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

    # ---------- 获取后自动下载勾选持久化 ----------
    @staticmethod
    def _load_auto_dl_checked() -> bool:
        try:
            from toolbox.config_manager import load_convert_config
            return bool(load_convert_config().get("auto_dl_checked", False))
        except Exception:
            return False

    def _save_auto_dl_checked(self, checked):
        try:
            from toolbox.config_manager import load_convert_config, save_convert_config
            cfg = load_convert_config()
            cfg["auto_dl_checked"] = bool(checked)
            save_convert_config(cfg)
        except Exception:
            pass

    # ---------- 获取后自动下载 ----------
    def _start_auto_download(self, posts):
        """获取完成后自动下载本次内容（勾选「获取后自动下载」时触发，保存+下载一次性）。"""
        from gui_qt import common, download_ops
        quality = common.CURRENT_QUALITY
        threads = common.CURRENT_THREADS

        def _log(line):
            post_to_main(lambda t=line: self.log_text.append(t))

        self.log_text.append(f"自动下载开始：{len(posts)} 条"
                             f"（画质 {quality} · {threads} 线程）")
        self.prog_label.setText("自动下载中...")
        # 下载进度走全局下载浮窗（原页面只保留日志/状态）
        from gui_qt.download_float import get_float
        card = get_float().add_batch("信息获取·自动下载", total=len(posts),
                                     threads=threads)

        def work():
            return download_ops.download_items_mixed(
                posts, quality_label=quality, threads=threads, log=_log,
                on_task_event=card.handle_event)

        def done(result, err):
            if err:
                self.log_text.append(f"自动下载失败: {err}")
                self.prog_label.setText("自动下载失败")
                self.app.toast(f"自动下载失败: {str(err)[:60]}", "error")
                card.finish(f"下载失败: {str(err)[:60]}")
                return
            self.log_text.append(f"自动下载完成: {result}")
            self.prog_label.setText("获取+下载完成")
            self.app.toast(f"自动下载完成：{result}", "success")
            card.finish(f"{result}" if len(result) <= 80 else result[:79] + "…")

        run_bg(work, done)

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
            self.date_btn.setText("日期段: 不限")
        else:
            self.date_btn.setText(f"日期段: {s or '从头'} ~ {e or '到现在'}")

    def _show_date_range_dialog(self):
        """日历选择日期段：两个日历表 + 开始/结束不限勾选；确定后对之后的获取生效。"""
        win = QDialog(self)
        win.setWindowTitle("日期段获取（按发布时间过滤）")
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
        ok_btn = QPushButton("确定")
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
                self.app.toast(f"日期段已设为：{s or '从头'} ~ {e or '到现在'}（下次获取生效）", "success")
            else:
                self.app.toast("日期段已清除，获取不再按时间过滤", "info")
            win.accept()

        def _clear():
            self._date_range = {"start": "", "end": ""}
            self._save_date_range()
            self._update_date_btn()
            self.app.toast("日期段已清除，获取不再按时间过滤", "info")
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
        win.setWindowTitle("定时获取")
        win.setFixedSize(320, 210)
        v = QVBoxLayout(win)
        t = QLabel("设置定时获取间隔")
        t.setProperty("role", "sectionTitle")
        v.addWidget(t, 0, Qt.AlignHCenter)
        sub = QLabel("按当前采集模式自动获取最新内容（仅在软件打开期间生效）")
        sub.setProperty("role", "caption")
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
            self.timer_btn.setText("停止定时")
            _set_button_variant(self.timer_btn, "danger")
            self.timer_dot.setText("●")
            self.timer_dot.setStyleSheet("color: palette(link); font-size: 15px;")
            self.app.toast(f"定时获取已启动（每 {m} 分钟）", "success")
            # 启动后先执行一次，之后每隔 interval 执行一次（QTimer 替代后台 sleep 循环）
            self._run_timer_fetch()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._run_timer_fetch)
            self._timer.start(self._timer_interval * 1000)

        start_btn = QPushButton("▶ 启动")
        start_btn.setFixedSize(160, 32)
        start_btn.setProperty("variant", "primary")
        start_btn.clicked.connect(do_start)
        v.addWidget(start_btn, 0, Qt.AlignHCenter)
        win.exec()

    def _stop_timer(self):
        self._timer_running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.timer_btn.setText("定时获取")
        _set_button_variant(self.timer_btn)
        self.timer_dot.setText("○")
        self.timer_dot.setStyleSheet("font-size: 15px;")
        self.app.toast("定时获取已停止", "info")

    def _run_timer_fetch(self):
        """定时获取：按当前采集模式（全部/选中/按组）采集最新内容。"""
        if not self._timer_running:
            return
        if self._fetching or self._batch_running:
            return  # 上次获取/批量处理未完成，跳过本轮
        self._flush_reveal_queue()
        accounts, err = self._resolve_target_accounts()
        if err:
            return  # 静默跳过（定时任务不弹窗）
        mode = self.fetch_mode.currentText()
        count = self._read_int(self.count_entry, 10)
        rate_count = self._read_int(self.rate_count_entry, 0)
        rate_interval = self._read_float(self.rate_interval_entry, 0)
        incremental = self.inc_check.isChecked()
        # 与手动获取同款：每轮独立事件 + 轮次守卫（取消立即释放 UI 后，
        # 旧轮收尾的 done()/事件不会干扰新一轮）
        self._fetch_round += 1
        round_id = self._fetch_round
        pause_evt = threading.Event()
        pause_evt.set()
        cancel_evt = threading.Event()
        self._pause_evt = pause_evt
        self._cancel_evt = cancel_evt

        def work():
            return self._collect_accounts_worker(
                accounts, count, rate_count=rate_count, rate_interval=rate_interval,
                incremental=incremental, on_event=self._make_event_bridge(round_id),
                pause_event=pause_evt, cancel_event=cancel_evt,
                date_start=self._date_range.get("start", ""),
                date_end=self._date_range.get("end", ""), round_id=round_id)

        def done(result, err):
            if round_id != self._fetch_round:
                # 过期轮次：数据已在 worker 内入库/导出，只刷新计数
                refresh_results_count(self.app)
                return
            self._set_fetch_ui(False)
            self._fetching = False
            ts = datetime.now().strftime("%H:%M:%S")
            if err:
                self.log_text.append(f"[{ts}] 定时获取失败: {err}")
                self.prog_label.setText("定时获取失败")
                return
            total, _log, new_posts = result
            if cancel_evt.is_set():
                self.log_text.append(f"[{ts}] 定时获取已取消（已获取 {total} 条）")
                self.prog_label.setText("已取消")
            else:
                self.log_text.append(f"[{ts}] 定时获取[{mode}]完成：共 {total} 条（已存入「已获取」）")
            if not self._reveal_queue:
                refresh_results_count(self.app)
            # 获取后自动下载（勾选后定时获取也生效：保存+下载一次性）
            if self.auto_dl_check.isChecked() and new_posts and not cancel_evt.is_set():
                self._start_auto_download(new_posts)
                return
            if not self.isVisible():
                # 用户已切到其他页：提示定时获取仍在后台生效
                self.app.toast(f"定时获取已在后台完成（共 {total} 条）", "info")

        self._fetching = True
        self._set_fetch_ui(True)
        run_bg(work, done)

    def shutdown(self):
        """软件退出：标记取消当前采集轮（未开始的账号跳过；在飞账号的数据
        仍在后台入库，进程退出由 main.py 统一兜底，不会残留 CLI 窗口）。"""
        self._cancel_evt.set()
        self._pause_evt.set()

    # ---------- 分组管理（设置页） ----------
    def _reload_group_list(self):
        self._refresh_gid_menus()
        _clear_layout(self.group_list_layout)
        self.group_list_layout.addStretch(1)
        groups = self._groups()
        n = len(groups)
        if not groups:
            lbl = QLabel("（暂无分组）")
            lbl.setProperty("role", "muted")
            self.group_list_layout.takeAt(self.group_list_layout.count() - 1)
            self.group_list_layout.addWidget(lbl)
            self.group_list_layout.addStretch(1)
        else:
            gids = list(groups.keys())
            for pos, gid in enumerate(gids):
                gname = groups[gid]
                row = QFrame()
                row.setProperty("variant", "card")
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 4, 6, 4)
                h.setSpacing(4)
                gid_lbl = QLabel(f"{gid}")
                gid_lbl.setFixedWidth(44)
                gid_lbl.setProperty("role", "sectionTitle")
                h.addWidget(gid_lbl)
                name_label = QLabel(gname)
                name_label.setWordWrap(True)
                name_label.setMinimumWidth(0)
                name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                h.addWidget(name_label, 1)
                up_btn = icon_button("↑", "上移一位", (28, 24), False, "font-size: 11px;")
                up_btn.setEnabled(pos > 0)
                up_btn.clicked.connect(lambda _=False, g=gid: self._move_group(g, -1))
                h.addWidget(up_btn)
                down_btn = icon_button("↓", "下移一位", (28, 24), False, "font-size: 11px;")
                down_btn.setEnabled(pos < n - 1)
                down_btn.clicked.connect(lambda _=False, g=gid: self._move_group(g, 1))
                h.addWidget(down_btn)
                del_btn = icon_button(
                    "×", "删除该分组（绑定该组的账号自动解绑）", (30, 24), False,
                    "background: #c62828; color: white; font-size: 13px;",
                    lambda _=False, g=gid: self._delete_group(g))
                h.addWidget(del_btn)
                for button in (up_btn, down_btn, del_btn):
                    _set_button_variant(button, "danger" if button is del_btn else "ghost",
                                        compact=True)
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
            self.app.toast("分组顺序已更新", "success")

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
            self.app.toast(f"已保存分组：{gid} {gname}", "success")

    def _delete_group(self, gid):
        from accounts_manager import delete_group
        delete_group(gid)
        self.app.toast(f"已删除分组 {gid}（绑定账号已解绑）", "info")
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
        self.save_all_btn = QPushButton("保存全部修改")
        self.save_all_btn.setProperty("variant", "primary")
        # 保存栏始终保留在卡片底部，列表为空时也不会留下上一轮的控件。
        _clear_layout(self.acct_save_bar)
        self.save_all_btn.setEnabled(False)
        self.save_all_btn.clicked.connect(self._save_all_account_rows)
        self.acct_save_bar.addWidget(self.save_all_btn)
        self.acct_save_bar.addStretch(1)
        hint = QLabel("修改组号/采集类型后点此一次性保存")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        hint.setMaximumWidth(280)
        self.acct_save_bar.addWidget(hint)
        accounts = [a for a in load_accounts() if a.url]  # 过滤分组占位账号
        groups = get_groups()
        if not accounts:
            lbl = QLabel("（暂无账号）")
            lbl.setProperty("role", "muted")
            self.acct_set_layout.takeAt(self.acct_set_layout.count() - 1)
            self.acct_set_layout.addWidget(lbl)
            self.acct_set_layout.addStretch(1)
            return
        for i, a in enumerate(accounts, 1):
            row = QFrame()
            row.setProperty("variant", "card")
            v = QVBoxLayout(row)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(6)
            h = QHBoxLayout()
            h.setSpacing(4)
            plat = URLRecognizer.recognize(a.url) or "?"
            name = a.name or ""
            info = f"{name}  [{plat}]  {a.url[:40]}" if name else f"[{plat}]  {a.url[:50]}"
            identity = QLabel(info)
            identity.setWordWrap(True)
            identity.setMinimumWidth(0)
            identity.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            identity.setToolTip(f"{name}\n{a.url}")
            h.addWidget(identity, 1)
            v.addLayout(h)
            settings = QHBoxLayout()
            settings.setSpacing(6)
            settings.addWidget(QLabel("分组"))
            # 分组多选（当前绑定的全部组）
            gsel = GroupMultiSelect(width=110)
            gsel.set_groups(groups)
            gsel.set_selected(a.group_ids)
            settings.addWidget(gsel)
            settings.addSpacing(8)
            settings.addWidget(QLabel("采集类型"))
            # 采集类型下拉（B站生效）
            cur_kind = a.kind or infer_kind_from_url(a.url)
            ksel = QComboBox()
            ksel.addItems([kind_label.get(k, k) for k in kind_options])
            ksel.setCurrentText(kind_label.get(cur_kind, "自动"))
            ksel.setFixedWidth(80)
            settings.addWidget(ksel)
            settings.addStretch(1)
            v.addLayout(settings)
            # 记录行控件与初始值，改动后启用底部「保存全部修改」
            self._acct_set_rows.append((a.url, gsel, ksel, gsel.selected(), ksel.currentText()))
            gsel.changed.connect(lambda b=self.save_all_btn: self._mark_set_dirty(b))
            ksel.currentTextChanged.connect(lambda _t, b=self.save_all_btn: self._mark_set_dirty(b))
            # 修改账号名/链接（对话框；分组/采集类型仍用行内控件）
            edit_btn = icon_button("", "修改账号名/链接", (30, 26), True, "font-size: 12px;")
            edit_btn.setIcon(line_icon("edit", theme.color("text"), 14))
            edit_btn.clicked.connect(lambda _=False, u=a.url: self._edit_account_row(u))
            h.addWidget(edit_btn)
            # 上移/下移（直接改 accounts.txt 顺序，立即生效）
            up_btn = icon_button("↑", "上移一位", (28, 26), False, "font-size: 12px;")
            up_btn.setEnabled(i > 1)
            up_btn.clicked.connect(lambda _=False, u=a.url: self._move_account_row(u, -1))
            h.addWidget(up_btn)
            down_btn = icon_button("↓", "下移一位", (28, 26), False, "font-size: 12px;")
            down_btn.setEnabled(i < len(accounts))
            down_btn.clicked.connect(lambda _=False, u=a.url: self._move_account_row(u, 1))
            h.addWidget(down_btn)
            del_btn = icon_button(
                "×", "删除该账号", (30, 26), False,
                "background: #c62828; color: white; font-size: 13px;",
                lambda _=False, u=a.url: self._delete_account_row(u))
            h.addWidget(del_btn)
            for button in (gsel, edit_btn, up_btn, down_btn, del_btn):
                _set_button_variant(button, "danger" if button is del_btn else "secondary",
                                    compact=button is not gsel)
            self.acct_set_layout.takeAt(self.acct_set_layout.count() - 1)
            self.acct_set_layout.addWidget(row)
            self.acct_set_layout.addStretch(1)

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
                self.app.toast(f"已保存 {changed} 项账号设置", "success")
            else:
                self.app.toast("没有需要保存的修改", "info")
        except Exception as e:
            self.app.toast(f"保存失败：{str(e)[:60]}", "error")
        self.save_all_btn.setEnabled(False)
        QTimer.singleShot(150, self._reload_account_list)
        QTimer.singleShot(150, self._reload_account_settings)
        QTimer.singleShot(150, self._reload_group_list)

    def _edit_account_row(self, url):
        """编辑账号名/链接（改链接=替换账号主键，去重校验；平台变化时采集类型按新链接重推）。"""
        from PySide6.QtWidgets import QFormLayout
        from accounts_manager import load_accounts, update_account
        acc = next((a for a in load_accounts() if a.url == url), None)
        if not acc:
            self.app.toast("账号不存在（可能已被删除）", "warning")
            return
        win = QDialog(self)
        win.setWindowTitle("修改账号")
        win.resize(560, 190)
        v = QVBoxLayout(win)
        form = QFormLayout()
        name_ed = QLineEdit(acc.name or "")
        name_ed.setPlaceholderText("留空则显示链接")
        url_ed = QLineEdit(acc.url)
        url_ed.setPlaceholderText("https://…（修改链接即更换账号指向）")
        form.addRow("账号名", name_ed)
        form.addRow("链接", url_ed)
        v.addLayout(form)
        from platforms import URLRecognizer
        plat_lbl = QLabel(f"平台：{URLRecognizer.recognize(url) or '?'}")
        plat_lbl.setStyleSheet("color: #888; font-size: 11px;")

        def _sync_plat(_t=""):
            p = URLRecognizer.recognize(url_ed.text().strip())
            plat_lbl.setText(f"平台：{p or '?'}")
        url_ed.textChanged.connect(_sync_plat)
        v.addWidget(plat_lbl)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton("保存")
        ok_btn.setStyleSheet("background: #2e7d32; color: white; padding: 4px 16px;")
        cancel_btn = QPushButton("取消")
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        v.addLayout(btn_row)
        ok_btn.clicked.connect(win.accept)
        cancel_btn.clicked.connect(win.reject)
        if win.exec() != QDialog.DialogCode.Accepted:
            return
        new_url = url_ed.text().strip()
        new_name = name_ed.text().strip()
        if not new_url:
            self.app.toast("链接不能为空", "warning")
            return
        if new_url != url and any(a.url == new_url for a in load_accounts()):
            self.app.toast("该链接已存在于账号列表（链接是账号唯一标识）", "error")
            return
        if new_url == url and new_name == (acc.name or ""):
            self.app.toast("没有修改", "info")
            return
        if update_account(url, name=new_name, new_url=new_url):
            suffix = f"（链接：{new_url[:40]}）" if new_url != url else ""
            self.app.toast(f"已修改账号{suffix}", "success")
        else:
            self.app.toast("修改失败（账号不存在）", "error")
            return
        QTimer.singleShot(150, self._reload_account_list)
        QTimer.singleShot(150, self._reload_account_settings)
        QTimer.singleShot(150, self._reload_group_list)

    def _acc_list_context_menu(self, pos):
        """功能页账号列表右键：打开链接（浏览器）/ 复制链接 / 修改账号 / 删除账号。"""
        from PySide6.QtWidgets import QApplication, QMenu
        item = self.acc_list.itemAt(pos)
        if item is None:
            return
        row = self.acc_list.row(item)
        if not (0 <= row < len(getattr(self, "acc_accounts", []))):
            return
        acc = self.acc_accounts[row]
        url = (acc.url or "").strip()
        menu = QMenu(self)
        act_open = menu.addAction("打开链接（浏览器）")
        act_copy = menu.addAction("复制链接")
        menu.addSeparator()
        act_edit = menu.addAction("修改账号")
        act_del = menu.addAction("删除账号…")
        act = menu.exec(self.acc_list.viewport().mapToGlobal(pos))
        if act == act_open:
            if url:
                import webbrowser
                webbrowser.open(url)
        elif act == act_copy:
            if url:
                QApplication.clipboard().setText(url)
                self.app.toast("已复制链接", "success")
        elif act == act_edit:
            self._edit_account_row(url)
        elif act == act_del:
            label = acc.name or url[:40]
            if QMessageBox.question(self, "删除账号", f"确定删除「{label}」？",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self._delete_account_row(url)

    def _delete_account_row(self, url):
        from accounts_manager import remove_account
        if remove_account(url):
            self.app.toast("已删除该账号", "info")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)

    def _move_account_row(self, url, delta):
        """上移/下移已配置账号（顺序即采集顺序，保存后立即生效）。"""
        from accounts_manager import move_account
        if move_account(url, delta):
            self.app.toast("账号顺序已更新", "success")
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
