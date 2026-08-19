"""账号追踪页（PySide6 版）：账号卡片 + 采集/导出/历史。"""

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from tracker.accounts import load_accounts, collect_account, load_history

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage

PLATFORM_CN = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博"}


class TrackerPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("📈 账号追踪")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        bar = QHBoxLayout()
        bar.addWidget(QPushButton("🔄 刷新", clicked=self._refresh))
        self.fetch_all = QPushButton("⬇ 获取全部")
        self.fetch_all.setStyleSheet("background: #2e7d32; color: white;")
        self.fetch_all.clicked.connect(lambda: self._fetch_all())
        bar.addWidget(self.fetch_all)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { background: #1a1d27; }")
        layout.addWidget(self.list, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        layout.addWidget(self.log)
        self._refresh()

    def _log(self, msg):
        self.log.append(msg)

    def _refresh(self):
        self.list.clear()
        for a in load_accounts():
            plat = PLATFORM_CN.get(a.get("platform", ""), a.get("platform") or "自动")
            w = QWidget()
            w.setStyleSheet("QWidget { background: #1a1d27; }")
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 4, 8, 4)
            h.addWidget(QLabel(f"👤 {a.get('name', '')}"))
            h.addWidget(QLabel(f"[{plat}]", styleSheet="color: #64b5f6;"))
            h.addStretch(1)
            h.addWidget(QPushButton("📥 采集", clicked=lambda checked, x=a: self._collect_one(x)))
            h.addWidget(QPushButton("📷 导出", clicked=lambda checked, x=a: self._export_one(x)))
            h.addWidget(QPushButton("📊 历史", clicked=lambda checked, x=a: self._open_history(x)))
            it = QListWidgetItem()
            it.setSizeHint(w.sizeHint())
            it.setData(Qt.UserRole, id(a))
            self.list.addItem(it)
            self.list.setItemWidget(it, w)

    def _collect_one(self, acc):
        self._log(f"⏳ 采集 {acc.name}...")
        def work():
            from auth import get_cookie
            platform = acc.platform or "bilibili"
            cookie = get_cookie(platform)
            return collect_account(acc, cookie=cookie)

        def done(info, err):
            if err:
                self._log(f"❌ {acc.name}: {err}")
            else:
                from tracker.accounts import save_history
                save_history(acc.name, info)
                self._log(f"✅ {acc.name} 采集完成")
                self._refresh()

        run_bg(work, done)

    def _fetch_all(self):
        for a in load_accounts():
            self._collect_one(a)

    def _export_one(self, acc):
        from tracker.export_png import export_diff_png
        history = load_history(acc.name)
        if len(history) < 2:
            self.app.toast("至少需要两次采集数据", "warning")
            return
        def work():
            return export_diff_png(acc, history)

        def done(path, err):
            if err:
                self._log(f"❌ 导出失败: {err}")
            else:
                self._log(f"✅ 已导出: {path}")

        run_bg(work, done)

    def _open_history(self, acc):
        from tracker.accounts import load_history
        history = load_history(acc.name)
        if not history:
            self.app.toast("暂无历史数据")
            return
        from PySide6.QtWidgets import QDialog, QTableWidget, QTableWidgetItem, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle(f"📊 {acc.name} 历史")
        dlg.resize(700, 360)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"共 {len(history)} 次采集（最新在前）"))
        t = QTableWidget(len(history), 4)
        t.setHorizontalHeaderLabels(["昵称", "粉丝", "关注", "获赞"])
        for i, r in enumerate(history):
            t.setItem(i, 0, QTableWidgetItem(r.get("name", "")))
            for j, f in enumerate(["fans", "following", "likes"]):
                t.setItem(i, j + 1, QTableWidgetItem(str(int(r.get(f, 0) or 0))))
        v.addWidget(t)
        dlg.exec()
