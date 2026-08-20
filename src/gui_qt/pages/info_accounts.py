"""账号获取页（PySide6 版）：添加账号、批量抓取最新内容（结果自动存入「已获取」）。

对应原版 gui/info_pages.py 的 InfoAccountsPage：
- 功能页：添加账号行 / 账号勾选列表 / 采集模式（全部/选中/按组）/ 条数 /
  定时获取 / 增量模式 / 搜索限流 / 日志
- 设置页：添加账号（账号名+链接+组号下拉）/ 分组管理 / 已配置账号
  （组号下拉 + 采集类型下拉 + 保存/删除）
"""

import contextlib
import io
import json
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import _collect, refresh_results_count, run_bg
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
        self.inc_check = QCheckBox("增量模式(追加到默认文件)")
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
        add_card.setStyleSheet("QWidget { background: #23262f; border-radius: 8px; }")
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
        grp_card.setStyleSheet("QWidget { background: #23262f; border-radius: 8px; }")
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
        acct_card.setStyleSheet("QWidget { background: #23262f; border-radius: 8px; }")
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
            QMessageBox.warning(self, "提示", "请输入账号 URL")
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
            QMessageBox.warning(self, "提示", "账号已存在")

    def _add_set(self):
        """设置页添加账号（账号名 + 链接 + 组号下拉）。"""
        name = self.set_name_entry.text().strip()
        url = self.set_url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入账号链接")
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
            QMessageBox.warning(self, "提示", "账号已存在")

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
            QMessageBox.warning(self, "提示", "正在获取中，请稍候")
            return
        accounts, err = self._resolve_target_accounts()
        if err:
            QMessageBox.warning(self, "提示", err)
            return
        count = self._read_int(self.count_entry, 10)
        rate_count = self._read_int(self.rate_count_entry, 0)
        rate_interval = self._read_float(self.rate_interval_entry, 0)
        incremental = self.inc_check.isChecked()
        self.fetch_btn.setEnabled(False)
        self.log_text.setPlainText(f"⏳ 开始获取 {len(accounts)} 个账号的最新内容...\n")

        def work():
            return self._collect_accounts_worker(
                accounts, count, rate_count=rate_count, rate_interval=rate_interval,
                incremental=incremental)

        def done(result, err):
            self.fetch_btn.setEnabled(True)
            self._fetching = False
            if err:
                self.log_text.append(f"❌ {err}")
                return
            total, log = result
            self.log_text.append("\n".join(log) + f"\n\n✅ 共获取 {total} 条（已存入「已获取」）")
            refresh_results_count(self.app)

        self._fetching = True
        run_bg(work, done)

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

    def _collect_accounts_worker(self, accounts, count, rate_count=0, rate_interval=0, incremental=False):
        """采集账号最新内容，返回 (total, log)。供手动与定时获取共用。

        rate_count/rate_interval: 搜索限流（每网站累计抓到 rate_count 个暂停 rate_interval 秒）。
        incremental: 增量模式（True=追加到默认汇总文件，False=单独导出独立文件）。
        """
        from platforms import URLRecognizer, get_parser
        total = 0
        log = []
        all_posts = []
        # 0 = 无上限（抓完所有分页）
        fetch_count = count if count > 0 else 9999
        per_platform = {}  # 平台 -> 已抓数量（用于限流）
        for i, a in enumerate(accounts, 1):
            platform = URLRecognizer.recognize(a.url)
            if not platform:
                log.append(f"[{i}] 无法识别: {a.url[:50]}")
                continue
            parser = get_parser(platform)
            if not parser or not hasattr(parser, "fetch_user_posts"):
                log.append(f"[{i}] {platform} 不支持时间线")
                continue
            try:
                # 捕获解析器内部 print 日志（Cookie 状态 / Playwright / 访客通行证等），一并回显
                buf = io.StringIO()
                # B站按账号 kind 决定只采视频/只采动态/混合；其他平台忽略
                account_kind = getattr(a, "kind", "")
                if platform == "bilibili" and account_kind in ("video", "dynamic", "all"):
                    kwargs = {"kind": account_kind}
                else:
                    kwargs = {}
                with contextlib.redirect_stdout(buf):
                    posts = parser.fetch_user_posts(a.url, count=fetch_count, **kwargs)
                _collect(posts)
                all_posts.extend(posts)
                total += len(posts)
                # 搜索限流：每网站累计抓到 rate_count 个后暂停 rate_interval 秒
                if rate_count > 0 and rate_interval > 0:
                    per_platform[platform] = per_platform.get(platform, 0) + len(posts)
                    if per_platform[platform] >= rate_count:
                        log.append(f"    ⏸ 限流：{platform} 已抓 {per_platform[platform]} 个，暂停 {rate_interval}s")
                        time.sleep(rate_interval)
                        per_platform[platform] = 0
                # 区分主页作品 / 动态统计
                dyns = sum(1 for p in posts if getattr(p, "kind", "") == "dynamic")
                videos = len(posts) - dyns
                kind_tag = {"video": "[作品] ", "dynamic": "[动态] "}.get(account_kind, "")
                if dyns and videos:
                    log.append(f"[{i}] {kind_tag}{platform} 获取到 {len(posts)} 条（作品 {videos} / 动态 {dyns}）")
                elif dyns:
                    log.append(f"[{i}] {kind_tag}{platform} 获取到 {len(posts)} 条（动态 {dyns}）")
                else:
                    log.append(f"[{i}] {kind_tag}{platform} 获取到 {len(posts)} 条（作品 {videos}）")
                detail = buf.getvalue().strip()
                if detail:
                    for line in detail.splitlines():
                        line = line.strip()
                        if line:
                            log.append(f"    {line}")
            except Exception as e:
                log.append(f"[{i}] {platform} 失败: {e}")
        # 自动导出（增量 or 单独）
        if all_posts:
            export_msg = self._auto_export(all_posts, incremental)
            if export_msg:
                log.append(export_msg)
        return total, log

    def _auto_export(self, posts, incremental):
        """抓取完成后自动导出。incremental=True 追加到默认汇总文件，False 单独导出独立文件。"""
        from toolbox.config_manager import get_export_subdir
        out_dir = str(get_export_subdir("video_data"))
        try:
            if incremental:
                # 增量：追加到默认汇总 json（按去重键去重）
                summary_path = os.path.join(out_dir, "_增量汇总.json")
                existing = []
                if os.path.exists(summary_path):
                    try:
                        existing = json.load(open(summary_path, encoding="utf-8"))
                    except Exception:
                        existing = []
                seen = {self._exp_key(e) for e in existing if isinstance(e, dict)}
                added = 0
                for p in posts:
                    key = self._exp_key(p)
                    if key and key in seen:
                        continue
                    seen.add(key)
                    existing.append(p.to_dict())
                    added += 1
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                return f"💾 已增量写入默认文件（新增 {added} 条）: {summary_path}"
            else:
                # 单独导出独立文件（已存在则跳过，用户移走/删除/改名后重新保存）
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

    @staticmethod
    def _exp_key(info):
        """导出去重键：优先链接，其次 ID。兼容 VideoInfo 和 to_dict() 结果。"""
        if isinstance(info, dict):
            return (info.get("链接", "") or info.get("原始URL", "") or info.get("视频ID", "") or "").strip()
        return (getattr(info, "video_url", "") or getattr(info, "raw_url", "") or getattr(info, "video_id", "") or "").strip()

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
                QMessageBox.warning(win, "提示", "间隔不能小于 1 分钟")
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
                incremental=incremental)

        def done(result, err):
            self._fetching = False
            ts = datetime.now().strftime("%H:%M:%S")
            if err:
                self.log_text.append(f"[{ts}] ⏰ 定时获取失败: {err}")
                return
            total, log = result
            self.log_text.append(f"[{ts}] ⏰ 定时获取[{mode}]完成：共 {total} 条（已存入「已获取」）")
            if log:
                self.log_text.append("\n".join(log) + "\n")
            refresh_results_count(self.app)

        self._fetching = True
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
                row.setStyleSheet("QWidget { background: #1a1d27; border-radius: 6px; }")
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 4, 6, 4)
                gid_lbl = QLabel(f"{gid}")
                gid_lbl.setFixedWidth(44)
                gid_lbl.setStyleSheet("font-weight: bold; color: #64b5f6; background: transparent;")
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
            QMessageBox.warning(self, "提示", "组号必须为重复数字，如 11、22")
            return
        if not gname:
            QMessageBox.warning(self, "提示", "请输入组名")
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
            row.setStyleSheet("QWidget { background: #1a1d27; border-radius: 6px; }")
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
            save_btn = QPushButton("💾")
            save_btn.setFixedSize(34, 26)
            save_btn.setStyleSheet("background: #2e7d32; color: white;")
            save_btn.clicked.connect(lambda _=False, idx=i, s=gsel, k=ksel: self._save_account_row(idx, s, k))
            h.addWidget(save_btn)
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(30, 26)
            del_btn.setStyleSheet("background: #c62828; color: white;")
            del_btn.clicked.connect(lambda _=False, idx=i: self._delete_account_row(idx))
            h.addWidget(del_btn)
            self.acct_set_layout.addWidget(row)

    def _save_account_row(self, idx, gsel, ksel):
        v = gsel.currentText()
        gid = "" if v == "无分组" else v.split(":")[0].strip()
        kv = ksel.currentText()
        # 还原 kind 标识
        kind_rev = {"自动": "", "[作品]": "video", "[动态]": "dynamic", "[全部]": "all"}
        kind = kind_rev.get(kv, "")
        from accounts_manager import update_account, get_groups
        gname = get_groups().get(gid, "") if gid else ""
        if update_account(idx, group_id=gid, group_name=gname, kind=kind):
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)

    def _delete_account_row(self, idx):
        from accounts_manager import remove_account
        if remove_account(idx):
            QTimer.singleShot(150, self._reload_account_list)
            QTimer.singleShot(150, self._reload_account_settings)
            QTimer.singleShot(150, self._reload_group_list)
