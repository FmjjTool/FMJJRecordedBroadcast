"""账号获取页（PySide6 版）：账号列表 + 分组管理 + 采集。"""

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from accounts_manager import (
    load_accounts, save_accounts, add_account, remove_account, get_groups,
    save_group, delete_group, Account,
)
from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage


class InfoAccountsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_func(), "功能")
        self.tabs.addTab(self._build_settings(), "设置")
        self._reload()

    def _group_options(self):
        return ["无分组"] + [f"{gid}:{gname}" for gid, gname in sorted(get_groups().items())]

    def _build_func(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("👥 账号获取", styleSheet="font-size: 18px; font-weight: bold;"))

        bar = QHBoxLayout()
        bar.addWidget(QPushButton("☑ 全选", clicked=lambda: self._sel("all")))
        bar.addWidget(QPushButton("🔄 反选", clicked=lambda: self._sel("invert")))
        bar.addWidget(QPushButton("□ 清除", clicked=lambda: self._sel("none")))
        bar.addWidget(QLabel("采集:"))
        self.fetch_mode = QComboBox()
        self.fetch_mode.addItems(["全部账号", "选中账号", "按组采集"])
        bar.addWidget(self.fetch_mode)
        self.fetch_group = QComboBox()
        self.fetch_group.addItems(self._group_options())
        bar.addWidget(self.fetch_group)
        fetch_btn = QPushButton("📥 获取最新")
        fetch_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        fetch_btn.clicked.connect(self._fetch)
        bar.addWidget(fetch_btn)
        bar.addStretch(1)
        v.addLayout(bar)

        self.list = QListWidget()
        v.addWidget(self.list, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        v.addWidget(self.log)
        return w

    def _build_settings(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("添加账号", styleSheet="font-weight: bold;"))
        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("账号主页 URL")
        row.addWidget(self.url_entry, 1)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText("账号名")
        self.name_entry.setFixedWidth(120)
        row.addWidget(self.name_entry)
        row.addWidget(QLabel("组:"))
        self.add_group = QComboBox()
        self.add_group.addItems(self._group_options())
        row.addWidget(self.add_group)
        add_btn = QPushButton("➕ 添加")
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)
        v.addLayout(row)

        v.addWidget(QLabel("分组管理", styleSheet="font-weight: bold; margin-top: 8px;"))
        g_row = QHBoxLayout()
        self.gid_entry = QLineEdit()
        self.gid_entry.setPlaceholderText("组编号(11/22)")
        self.gid_entry.setFixedWidth(120)
        g_row.addWidget(self.gid_entry)
        self.gname_entry = QLineEdit()
        self.gname_entry.setPlaceholderText("组名")
        g_row.addWidget(self.gname_entry, 1)
        g_row.addWidget(QPushButton("➕ 新建分组", clicked=self._add_group))
        v.addLayout(g_row)

        self.group_list = QListWidget()
        v.addWidget(self.group_list)
        self.account_list = QListWidget()
        v.addWidget(self.account_list, 1)
        v.addStretch(1)
        self._reload_settings()
        return w

    def _reload(self):
        self.accounts = load_accounts()
        self.list.clear()
        for a in self.accounts:
            it = QListWidgetItem(f"{a.name or '(未命名)'}  [{a.kind}]  {a.url}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, a.name)
            self.list.addItem(it)

    def _reload_settings(self):
        self.group_list.clear()
        for gid, gname in sorted(get_groups().items()):
            self.group_list.addItem(f"{gid}  {gname}")
        self.account_list.clear()
        for a in load_accounts():
            gname = ""
            for gid, gn in get_groups().items():
                if gid == (a.group_id or ""):
                    gname = gn
                    break
            self.account_list.addItem(f"{a.name or '(未命名)'}  [{'组:' + gname if gname else '无分组'}]")
        # 刷新组号下拉
        opts = self._group_options()
        for cb in (self.fetch_group, self.add_group):
            cur = cb.currentText()
            cb.clear()
            cb.addItems(opts)
            if cur in opts:
                cb.setCurrentText(cur)

    def _sel(self, mode):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if mode == "all":
                it.setCheckState(Qt.Checked)
            elif mode == "none":
                it.setCheckState(Qt.Unchecked)
            else:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _add(self):
        url = self.url_entry.text().strip()
        name = self.name_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入账号 URL")
            return
        gid = ""
        gv = self.add_group.currentText()
        if gv != "无分组":
            gid = gv.split(":")[0].strip()
        add_account(url, name=name, group_id=gid)
        self.url_entry.clear()
        self.name_entry.clear()
        self._reload()
        self._reload_settings()

    def _add_group(self):
        gid = self.gid_entry.text().strip()
        gname = self.gname_entry.text().strip()
        import re
        if not re.match(r"^(\d)\1+$", gid):
            QMessageBox.warning(self, "提示", "组编号必须为重复数字，如 11、22、33")
            return
        if not gname:
            QMessageBox.warning(self, "提示", "请输入组名")
            return
        if gid in get_groups():
            QMessageBox.warning(self, "提示", f"组 {gid} 已存在")
            return
        save_group(gid, gname)
        self.gid_entry.clear()
        self.gname_entry.clear()
        self._reload_settings()

    def _fetch(self):
        mode = self.fetch_mode.currentText()
        accounts = self.accounts
        if mode == "选中账号":
            sel_names = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                         if self.list.item(i).checkState() == Qt.Checked}
            accounts = [a for a in accounts if a.name in sel_names]
        elif mode == "按组采集":
            gv = self.fetch_group.currentText()
            gid = "" if gv == "无分组" else gv.split(":")[0].strip()
            accounts = [a for a in accounts if (a.group_id or "") == gid]
        if not accounts:
            QMessageBox.warning(self, "提示", "没有符合条件的账号")
            return
        self.log.clear()
        self.log.append(f"⏳ 开始获取 {len(accounts)} 个账号的最新内容...")

        def work():
            from platforms import URLRecognizer, get_parser
            from gui_qt.common import COLLECTED, _collect
            ok = 0
            for a in accounts:
                try:
                    platform = URLRecognizer.recognize(a.url)
                    if not platform:
                        continue
                    parser = get_parser(platform)
                    if not parser:
                        continue
                    infos = parser.fetch_user_posts(a.url, count=10) if hasattr(parser, 'fetch_user_posts') else []
                    n = _collect(infos)
                    ok += n
                except Exception:
                    pass
            return ok

        def done(result, err):
            if err:
                self.log.append(f"❌ {err}")
            else:
                self.log.append(f"✅ 获取完成，新增 {result} 条（已存入「已获取」）")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _bg(self, fn, on_done):
        try:
            r = fn()
            e = None
        except Exception as ex:
            r, e = None, str(ex)
        post_to_main(on_done, r, e)
