"""账号获取页（PySide6 版，简化）。"""

import threading

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout,
)

from accounts_manager import load_accounts, add_account, remove_account
from gui_qt.pages.base import BasePage


class InfoAccountsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("👥 账号获取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("账号主页 URL")
        row.addWidget(self.url_entry, 1)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText("账号名")
        self.name_entry.setFixedWidth(120)
        row.addWidget(self.name_entry)
        add_btn = QPushButton("➕ 添加")
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)
        layout.addLayout(row)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        bar = QHBoxLayout()
        fetch_btn = QPushButton("📥 获取选中账号最新")
        fetch_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 14px; border-radius: 4px;")
        fetch_btn.clicked.connect(self._fetch)
        bar.addWidget(fetch_btn)
        del_btn = QPushButton("🗑 删除选中")
        del_btn.clicked.connect(self._delete)
        bar.addWidget(del_btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        layout.addWidget(self.log)

        self._reload()

    def _reload(self):
        self.accounts = load_accounts()
        self.list.clear()
        for a in self.accounts:
            self.list.addItem(f"{a.name or '(未命名)'}  [{a.kind}]  {a.url}")

    def _add(self):
        url = self.url_entry.text().strip()
        name = self.name_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入账号 URL")
            return
        add_account(url, name=name)
        self.url_entry.clear()
        self.name_entry.clear()
        self._reload()

    def _delete(self):
        row = self.list.currentRow()
        if row < 0:
            return
        remove_account(row)
        self._reload()

    def _fetch(self):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.accounts):
            QMessageBox.warning(self, "提示", "请先选中一个账号")
            return
        acc = self.accounts[row]
        self.log.clear()
        self.log.append(f"⏳ 抓取 {acc.name or acc.url} 最新内容...")

        def work():
            from tracker.accounts import collect_account
            return collect_account(acc)

        def done(result, err):
            if err:
                self.log.append(f"❌ {err}")
            else:
                self.log.append(f"✅ 抓取完成，共 {result} 条（已存入「已获取」）")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _bg(self, fn, on_done):
        try:
            r = fn()
            e = None
        except Exception as ex:
            r, e = None, str(ex)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: on_done(r, e))
