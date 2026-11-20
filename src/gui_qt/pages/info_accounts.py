"""信息获取页（PySide6 版，原「账号获取」）：批量输入链接（账号/作品自动识别）+ 账号采集。

「单个解析」「批量导入」已合并到本页：顶部批量输入框一行一个，
账号主页链接 → 添加账号；作品链接/分享文案/纯 BV 号 → 解析并存入「已获取」。
- 功能页：批量输入行 / 账号勾选列表 / 采集模式（全部/选中/按组）/ 条数 /
  会话定时 / 增量模式 / 搜索限流 / 日志
- 设置页：账号与分组已迁移到「Cookie 管理 → 账号配置」，此处只保留入口提示
"""

import json
import os
import re
import threading
from collections import deque
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtCore import QDate, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListView, QListWidgetItem, QMessageBox, QPushButton, QProgressBar,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt import download_ops, theme
from gui_qt.common import _collect, _db_insert, icon_button, post_to_main, refresh_results_count, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList


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

        self._build_func(self)
        self._refresh_gid_menus()
        self._reload_account_list()

    def on_return_press(self):
        """主窗口回车统一入口 → 触发「获取最新」。"""
        self._fetch()

    def refresh_accounts(self):
        """账号配置在「Cookie 管理 → 账号配置」里改动后刷新列表与分组下拉。"""
        self._reload_account_list()
        self._refresh_gid_menus()

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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_batch_card())
        layout.addWidget(self._build_accounts_card())
        layout.addWidget(self._build_settings_card())
        layout.addWidget(self._build_run_bar())
        layout.addWidget(self._build_log_card(), 1)

        # 没有显式语义的按钮统一次要样式（新建按钮在创建处已声明 variant）
        for button in f.findChildren(QPushButton):
            if not button.property("variant"):
                _set_button_variant(button, "secondary", button.maximumWidth() < 80)

    # ---------- 布局片段 ----------

    @staticmethod
    def _new_card(title: str):
        """卡片容器：标题行 + 内容区，返回 (卡片, 标题行, 内容布局)。"""
        card = QFrame()
        card.setProperty("variant", "card")
        body = QVBoxLayout(card)
        body.setContentsMargins(10, 8, 10, 10)
        body.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        label = QLabel(title)
        label.setProperty("role", "sectionTitle")
        head.addWidget(label)
        body.addLayout(head)
        return card, head, body

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

    def _icon_action(self, icon_name: str, tip: str, variant: str = "secondary") -> QPushButton:
        """固定 40x30 的仅图标按钮（必须带 tooltip）。"""
        btn = QPushButton()
        btn.setProperty("variant", variant)
        btn.setFixedSize(40, 30)
        btn.setIcon(line_icon(icon_name, theme.color("text"), 16))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tip)
        return btn

    def _set_pause_icon(self, button: QPushButton, paused: bool):
        """暂停/继续按钮：暂停中显示播放图标并转主色，运行中显示暂停图标。"""
        button.setIcon(line_icon("play" if paused else "pause", theme.color("text"), 16))
        _set_button_variant(button, "primary" if paused else "secondary", compact=True)

    def _build_batch_card(self) -> QFrame:
        card, head, body = self._new_card("批量添加 / 解析")
        head.addWidget(self._caption("一行一个，支持整段分享文案"))
        head.addStretch(1)
        head.addWidget(self._caption("Ctrl+Enter 处理"))

        # 批量输入：账号主页 → 添加账号，其余 → 解析存入「已获取」
        self.batch_text = QTextEdit()
        self.batch_text.setPlaceholderText(
            "一行一个，直接粘贴（支持整段分享文案）：\n"
            "· 账号主页链接（B站空间/抖音/微博/快手/小红书）→ 添加为账号\n"
            "· 作品/视频链接、纯 BV 号 → 解析并存入「已获取」")
        self.batch_text.setFixedHeight(88)
        batch_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.batch_text)
        batch_shortcut.activated.connect(self._process_batch)
        body.addWidget(self.batch_text)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.process_btn = QPushButton("开始处理")
        self.process_btn.setProperty("variant", "primary")
        self.process_btn.setIcon(line_icon("play", theme.color("on_accent"), 16))
        self.process_btn.setIconSize(QSize(16, 16))
        self.process_btn.setToolTip(
            "逐行识别：账号主页 → 添加账号；其余 → 解析作品并存入「已获取」（Ctrl+Enter）")
        self.process_btn.clicked.connect(self._process_batch)
        row.addWidget(self.process_btn)

        self.batch_pause_btn = self._icon_action(
            "pause", "暂停批量处理：当前行处理完即停住，点「继续」恢复")
        self.batch_pause_btn.setEnabled(False)
        self.batch_pause_btn.clicked.connect(self._toggle_batch_pause)
        row.addWidget(self.batch_pause_btn)

        self.batch_cancel_btn = self._icon_action(
            "close", "取消批量处理：未开始的行直接跳过", "danger")
        self.batch_cancel_btn.setEnabled(False)
        self.batch_cancel_btn.clicked.connect(self._cancel_batch)
        row.addWidget(self.batch_cancel_btn)

        import_btn = self._icon_action("folder", "从文本文件导入链接列表（每行一个）")
        import_btn.clicked.connect(self._import_file)
        row.addWidget(import_btn)
        row.addStretch(1)
        body.addLayout(row)
        return card

    def _build_accounts_card(self) -> QFrame:
        card, head, body = self._new_card("账号列表")
        self.acc_count_label = self._caption("0 个账号")
        head.addWidget(self.acc_count_label)
        head.addStretch(1)
        for text, mode, tip in (("全选", "all", "全选账号"),
                                ("反选", "invert", "反选已选账号"),
                                ("清除", "none", "清除选择")):
            b = QPushButton(text)
            b.setProperty("variant", "ghost")
            b.setStyleSheet("padding: 2px 8px; font-size: 12px;")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, m=mode: self._acc_select(m))
            head.addWidget(b)

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
        body.addWidget(self.acc_list)
        return card

    def _build_settings_card(self) -> QFrame:
        card, head, body = self._new_card("采集设置")
        body.setSpacing(10)
        head.addWidget(self._caption("范围 / 条数 / 限流 / 日期"))
        head.addStretch(1)

        # 左侧两行参数，右侧一列策略开关；窄窗口也不挤压
        columns = QHBoxLayout()
        columns.setSpacing(12)
        params = QVBoxLayout()
        params.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._caption("采集范围"))
        self.fetch_mode = QComboBox()
        self.fetch_mode.addItems(["全部账号", "选中账号", "按组采集"])
        self.fetch_mode.setFixedWidth(110)
        self.fetch_mode.currentTextChanged.connect(self._on_fetch_mode_change)
        row.addWidget(self.fetch_mode)
        row.addWidget(self._caption("分组"))
        self.fetch_group = QComboBox()
        self.fetch_group.setFixedWidth(110)
        row.addWidget(self.fetch_group)
        row.addSpacing(8)
        row.addWidget(self._caption("条数"))
        self.count_entry = QLineEdit("10")
        self.count_entry.setFixedWidth(56)
        self.count_entry.setToolTip("每个账号取多少条；0 = 不限")
        row.addWidget(self.count_entry)
        row.addSpacing(6)
        row.addWidget(self._caption("0 = 不限"))
        params.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(self._caption("限流"))
        row2.addWidget(self._caption("每"))
        self.rate_count_entry = QLineEdit("10")
        self.rate_count_entry.setFixedWidth(40)
        self.rate_count_entry.setToolTip("每采集多少个账号停顿一次")
        row2.addWidget(self.rate_count_entry)
        row2.addWidget(self._caption("个停"))
        self.rate_interval_entry = QLineEdit("10")
        self.rate_interval_entry.setFixedWidth(40)
        self.rate_interval_entry.setToolTip("停顿秒数")
        row2.addWidget(self.rate_interval_entry)
        row2.addWidget(self._caption("秒"))
        row2.addSpacing(12)
        self.date_btn = QPushButton("日期段: 不限")
        self.date_btn.setProperty("variant", "secondary")
        self.date_btn.setToolTip("只采集指定发布日期范围内的内容")
        self.date_btn.clicked.connect(self._show_date_range_dialog)
        row2.addWidget(self.date_btn)
        params.addLayout(row2)

        columns.addLayout(params)
        columns.addWidget(self._separator(vertical=True))

        strategy = QVBoxLayout()
        strategy.setSpacing(6)
        self.inc_check = QCheckBox("增量模式（写入数据库）")
        self.inc_check.setToolTip("只写入未入库的新内容，重复内容自动跳过")
        self.inc_check.setChecked(self._load_inc_checked())
        self.inc_check.toggled.connect(self._save_inc_checked)
        strategy.addWidget(self.inc_check)
        self.auto_dl_check = QCheckBox("获取后自动下载")
        self.auto_dl_check.setToolTip(
            "勾选后：获取完成 → 保存（已获取/数据库）→ 自动下载本次获取的内容，"
            "一次性完成。下载画质/线程数用「已获取」页的全局设置")
        self.auto_dl_check.setChecked(self._load_auto_dl_checked())
        self.auto_dl_check.toggled.connect(self._save_auto_dl_checked)
        strategy.addWidget(self.auto_dl_check)
        columns.addLayout(strategy)
        columns.addStretch(1)
        body.addLayout(columns)

        # 同一张卡片里的下拉/输入/按钮统一高度，两行基线一致
        for widget in (self.fetch_mode, self.fetch_group, self.count_entry,
                       self.rate_count_entry, self.rate_interval_entry, self.date_btn):
            widget.setFixedHeight(30)

        self._load_date_range()
        self.fetch_mode.setCurrentText("选中账号")
        self._on_fetch_mode_change("选中账号")
        return card

    def _build_run_bar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("variant", "toolbar")
        v = QVBoxLayout(bar)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.fetch_btn = QPushButton("获取最新")
        self.fetch_btn.setProperty("variant", "primary")
        self.fetch_btn.setIcon(line_icon("download", theme.color("on_accent"), 16))
        self.fetch_btn.setIconSize(QSize(16, 16))
        self.fetch_btn.setToolTip("按当前采集范围获取各账号最新内容（回车）")
        self.fetch_btn.clicked.connect(self._fetch)
        row.addWidget(self.fetch_btn)

        self.pause_btn = self._icon_action(
            "pause", "暂停后当前账号采集完即停住，点「继续」恢复")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        row.addWidget(self.pause_btn)

        self.cancel_btn = self._icon_action(
            "close", "取消当前获取：未开始的账号直接跳过，正在采集的账号自然结束",
            "danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_fetch)
        row.addWidget(self.cancel_btn)

        row.addWidget(self._separator(vertical=True))
        self.timer_btn = QPushButton("会话定时")
        self.timer_btn.setProperty("variant", "secondary")
        self.timer_btn.setToolTip("按固定间隔自动执行一轮「获取最新」")
        self.timer_btn.clicked.connect(self._toggle_timer)
        row.addWidget(self.timer_btn)

        self.timer_dot = self._caption("定时未开启")
        row.addWidget(self.timer_dot)
        row.addStretch(1)
        v.addLayout(row)

        prog_row = QHBoxLayout()
        prog_row.setSpacing(8)
        self.prog_label = QLabel("空闲")
        self.prog_label.setProperty("role", "caption")
        self.prog_label.setMinimumWidth(150)
        self.prog_label.setMaximumWidth(220)
        self.prog_label.setWordWrap(True)
        prog_row.addWidget(self.prog_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setFormat("空闲")
        self.progress_bar.setValue(0)
        prog_row.addWidget(self.progress_bar, 1)
        v.addLayout(prog_row)
        return bar

    def _build_log_card(self) -> QFrame:
        card, head, body = self._new_card("运行日志")
        head.addWidget(self._caption("采集过程、错误与统计"))
        head.addStretch(1)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("等待采集任务…")
        body.addWidget(self.log_text, 1)
        return card

    # ---------- 账号列表 ----------
    # B站采集类型标签（其他平台无此概念）
    _KIND_TAG = {"video": "作品", "dynamic": "动态", "all": "作品+动态"}

    def _reload_account_list(self):
        from accounts_manager import load_accounts
        from platforms import URLRecognizer
        self.acc_list.clear()
        accounts = [a for a in load_accounts() if a.url]  # 过滤分组占位账号（url 为空）
        self.acc_accounts = accounts
        if hasattr(self, "acc_count_label"):
            self.acc_count_label.setText(f"{len(accounts)} 个账号")
        if not accounts:
            item = QListWidgetItem("（暂无账号，请在上方添加）")
            item.setFlags(Qt.NoItemFlags)
            self.acc_list.addItem(item)
            self._fit_account_list_height(0)
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
        self._fit_account_list_height(len(accounts))

    def _fit_account_list_height(self, count: int):
        """账号少时列表跟着收缩，避免卡片里空出一大片。"""
        if count <= 0:
            self.acc_list.setFixedHeight(96)
            return
        row_h = self.acc_list.sizeHintForRow(0)
        if row_h <= 0:
            row_h = 28
        visible = min(count, 6)
        self.acc_list.setFixedHeight(max(96, visible * row_h + 12))

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
            self._set_pause_icon(self.batch_pause_btn, paused=True)
            self._log("已暂停（当前行处理完成后停住）")
            self.prog_label.setText("已暂停，等待继续...")
        else:
            self._batch_pause_evt.set()
            self._set_pause_icon(self.batch_pause_btn, paused=False)
            self._log("已继续批量处理")
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
            from accounts_manager import add_account, load_accounts
            from platforms.account_names import fetch_display_name
            ok_acc = dup_acc = ok_post = new_posts = fail = 0
            cancelled = False
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
                    if add_account(url, name=name):
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
            self._set_pause_icon(self.batch_pause_btn, paused=False)
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
                self._refresh_gid_menus()

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

    # ---------- 暂停 ----------
    def _toggle_pause(self):
        if not self._fetching:
            return
        if self._pause_evt.is_set():
            self._pause_evt.clear()
            self._set_pause_icon(self.pause_btn, paused=True)
            self.log_text.append("已暂停（当前账号采集完成后停住）")
            self.prog_label.setText("已暂停，等待继续...")
        else:
            self._pause_evt.set()
            self._set_pause_icon(self.pause_btn, paused=False)
            self.log_text.append("已继续采集")
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
        """获取开始/结束时同步暂停/取消按钮状态（手动与会话定时共用）。"""
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running)
        if running:
            self._cancel_evt.clear()  # 每轮获取开始前复位取消标记
        else:
            self._pause_evt.set()  # 复位门闩，避免下轮启动即处于暂停态
            self._set_pause_icon(self.pause_btn, paused=False)

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

    # ---------- 会话定时 ----------
    def _toggle_timer(self):
        if self._timer_running:
            self._stop_timer()
        else:
            self._show_timer_dialog()

    def _show_timer_dialog(self):
        win = QDialog(self)
        win.setWindowTitle("会话定时")
        win.setFixedSize(320, 210)
        v = QVBoxLayout(win)
        t = QLabel("设置会话定时间隔")
        t.setProperty("role", "sectionTitle")
        v.addWidget(t, 0, Qt.AlignHCenter)
        sub = QLabel("按当前采集模式自动获取最新内容（仅在软件打开期间生效，不是 Windows 计划任务）")
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
            self.timer_btn.setText("停止会话定时")
            _set_button_variant(self.timer_btn, "danger")
            self.timer_dot.setText(f"定时中 · 每 {m} 分钟")
            self.timer_dot.setStyleSheet("color: palette(link);")
            self.app.toast(f"会话定时已启动（每 {m} 分钟）", "success")
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
        self.timer_btn.setText("会话定时")
        _set_button_variant(self.timer_btn)
        self.timer_dot.setText("定时未开启")
        self.timer_dot.setStyleSheet("")
        self.app.toast("会话定时已停止", "info")

    def _run_timer_fetch(self):
        """会话定时：按当前采集模式（全部/选中/按组）采集最新内容。"""
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
                self.log_text.append(f"[{ts}] 会话定时失败: {err}")
                self.prog_label.setText("会话定时失败")
                return
            total, _log, new_posts = result
            if cancel_evt.is_set():
                self.log_text.append(f"[{ts}] 会话定时已取消（已获取 {total} 条）")
                self.prog_label.setText("已取消")
            else:
                self.log_text.append(f"[{ts}] 会话定时[{mode}]完成：共 {total} 条（已存入「已获取」）")
            if not self._reveal_queue:
                refresh_results_count(self.app)
            # 获取后自动下载（勾选后会话定时也生效：保存+下载一次性）
            if self.auto_dl_check.isChecked() and new_posts and not cancel_evt.is_set():
                self._start_auto_download(new_posts)
                return
            if not self.isVisible():
                # 用户已切到其他页：提示会话定时仍在后台生效
                self.app.toast(f"会话定时已在后台完成（共 {total} 条）", "info")

        self._fetching = True
        self._set_fetch_ui(True)
        run_bg(work, done)

    def shutdown(self):
        """软件退出：标记取消当前采集轮（未开始的账号跳过；在飞账号的数据
        仍在后台入库，进程退出由 main.py 统一兜底，不会残留 CLI 窗口）。"""
        self._cancel_evt.set()
        self._pause_evt.set()

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

    def _move_account_row(self, url, delta):
        """上移/下移已配置账号（顺序即采集顺序，保存后立即生效）。"""
        from accounts_manager import move_account
        if move_account(url, delta):
            self.app.toast("账号顺序已更新", "success")
            QTimer.singleShot(150, self._reload_account_list)
