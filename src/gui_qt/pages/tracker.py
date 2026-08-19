"""账号追踪页（PySide6 版）：账号卡片 + 指标采集 + 历史对比。"""

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.pages.base import BasePage

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博"}
_NUM_FIELDS = {"fans", "following", "likes", "video_count", "weibo_count", "level"}


def _cell(rec, field):
    v = rec.get(field)
    if v is None or v == "":
        return "—"
    if field == "verified":
        return "已认证" if v else "—"
    if field in _NUM_FIELDS:
        try:
            return f"{int(float(v)):,}"
        except (ValueError, TypeError):
            return str(v)
    return str(v)


class TrackerPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.cards = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_func(), "功能")
        self.tabs.addTab(self._build_settings(), "设置")
        self.reload_accounts()

    def _build_func(self):
        w = QWidget()
        v = QVBoxLayout(w)
        head = QHBoxLayout()
        head.addWidget(QLabel("📈 账号追踪", styleSheet="font-size: 18px; font-weight: bold;"))
        head.addStretch(1)
        all_btn = QPushButton("⬇ 获取全部")
        all_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        all_btn.clicked.connect(self._collect_all)
        head.addWidget(all_btn)
        head.addWidget(QPushButton("🔄 刷新", clicked=self.reload_accounts))
        v.addLayout(head)

        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        self.cards_widget = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setAlignment(Qt.AlignTop)
        self.cards_area.setWidget(self.cards_widget)
        v.addWidget(self.cards_area, 1)
        return w

    def _build_settings(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("⚙️ 追踪账号设置", styleSheet="font-size: 16px; font-weight: bold;"))

        form = QFormLayout()
        self.add_name = QLineEdit()
        self.add_name.setPlaceholderText("如 封茗囧菌抖音")
        form.addRow("账号名:", self.add_name)
        self.add_url = QLineEdit()
        self.add_url.setPlaceholderText("账号主页链接（自动识别平台）")
        form.addRow("链接:", self.add_url)
        v.addLayout(form)
        add_btn = QPushButton("➕ 添加追踪账号")
        add_btn.clicked.connect(self._add_account)
        v.addWidget(add_btn)

        v.addWidget(QLabel("已配置账号:", styleSheet="font-weight: bold; margin-top: 8px;"))
        self.acc_list = QVBoxLayout()
        v.addLayout(self.acc_list)
        v.addStretch(1)
        self._acc_list_layout = self.acc_list
        return w

    def reload_accounts(self):
        from tracker.accounts import load_accounts
        # 清空卡片
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = []
        accounts = load_accounts()
        for acc in accounts:
            card = self._make_card(acc)
            self.cards_layout.addWidget(card)
            self.cards.append({"account": acc, "status": card.status_label})
        self._reload_acc_list()

    def _make_card(self, acc):
        from tracker.accounts import parse_url, load_history
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: #23262f; border-radius: 8px; }")
        fv = QVBoxLayout(frame)

        head = QHBoxLayout()
        platform = (acc.get("platform") or "").strip()
        if not platform:
            platform, _ = parse_url(acc.get("url", ""))
        plat_name = PLATFORM_NAMES.get(platform, platform or "未知")
        head.addWidget(QLabel(f"👤 {acc.get('name', '未命名')}"))
        plat_lbl = QLabel(plat_name)
        plat_lbl.setStyleSheet("color: #64b5f6; font-size: 11px;")
        head.addWidget(plat_lbl)
        head.addStretch(1)
        collect_btn = QPushButton("📥 采集")
        collect_btn.clicked.connect(lambda checked, a=acc: self._collect_account(a))
        head.addWidget(collect_btn)
        export_btn = QPushButton("📷 导出")
        export_btn.clicked.connect(lambda checked, a=acc: self._export_png(a))
        head.addWidget(export_btn)
        history_btn = QPushButton("📊 历史")
        history_btn.clicked.connect(lambda checked, a=acc: self._open_history(a))
        head.addWidget(history_btn)
        status_lbl = QLabel("")
        head.addWidget(status_lbl)
        fv.addLayout(head)
        frame.status_label = status_lbl

        # 预览
        history = load_history(acc.get("name", ""))
        if history:
            rec = history[0]
            prev = QLabel(f"粉丝 {_cell(rec, 'fans')}  ·  关注 {_cell(rec, 'following')}  ·  获赞 {_cell(rec, 'likes')}")
            prev.setStyleSheet("color: #888;")
            fv.addWidget(prev)
        else:
            fv.addWidget(QLabel("（尚未采集）", styleSheet="color: #555;"))
        return frame

    def _collect_account(self, acc):
        def work():
            from tracker.accounts import collect_account, save_history
            record = collect_account(acc)
            save_history(acc.get("name", "未命名"), record)
            return record

        def done(result, err):
            if err:
                self.app.toast(f"采集失败: {err}")
            else:
                self.app.toast(f"✅ 采集完成: {acc.get('name', '')}")
            self.reload_accounts()

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _collect_all(self):
        accounts = [c["account"] for c in self.cards]
        if not accounts:
            self.app.toast("没有账号")
            return
        self.app.toast(f"开始批量采集 {len(accounts)} 个账号...")
        for acc in accounts:
            self._collect_account(acc)

    def _export_png(self, acc):
        from tracker.accounts import load_history
        history = load_history(acc.get("name", ""))
        if len(history) < 2:
            self.app.toast("至少需要两次采集数据", "warning")
            return

        def work():
            from tracker.export_png import export_diff_png
            return export_diff_png(acc, history)

        def done(result, err):
            if err:
                self.app.toast(f"导出失败: {err}")
            else:
                self.app.toast(f"✅ 已导出 PNG: {result}")

        threading.Thread(target=lambda: self._bg(work, done), daemon=True).start()

    def _open_history(self, acc):
        from tracker.accounts import load_history
        history = load_history(acc.get("name", ""))
        if not history:
            self.app.toast("暂无历史数据")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"📊 {acc.get('name', '')} · 历史数据")
        dlg.resize(700, 400)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"共 {len(history)} 次采集（最新在前）"))
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
        metrics = [("昵称", "name"), ("粉丝数", "fans"), ("关注数", "following"),
                   ("获赞数", "likes"), ("内容数", "video_count")]
        table = QTableWidget(len(history), len(metrics))
        table.setHorizontalHeaderLabels([m[0] for m in metrics])
        for i, rec in enumerate(history):
            for j, (_n, f) in enumerate(metrics):
                table.setItem(i, j, QTableWidgetItem(_cell(rec, f)))
        v.addWidget(table)
        dlg.exec()

    def _add_account(self):
        name = self.add_name.text().strip() or "未命名"
        url = self.add_url.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入账号链接")
            return
        from tracker.accounts import parse_url, load_accounts, save_accounts
        platform, _ = parse_url(url)
        acc = {"name": name, "url": url, "platform": platform or "", "group_id": ""}
        accounts = load_accounts()
        accounts.append(acc)
        save_accounts(accounts)
        self.add_name.clear()
        self.add_url.clear()
        self.reload_accounts()

    def _reload_acc_list(self):
        from tracker.accounts import load_accounts
        while self.acc_list.count():
            item = self.acc_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, acc in enumerate(load_accounts()):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{acc.get('name')}  [{PLATFORM_NAMES.get(acc.get('platform'), acc.get('platform') or '自动')}]"))
            del_btn = QPushButton("✕")
            del_btn.setFixedWidth(30)
            del_btn.clicked.connect(lambda checked, idx=i: self._delete_account(idx))
            row.addWidget(del_btn)
            self.acc_list.addLayout(row)

    def _delete_account(self, index):
        from tracker.accounts import load_accounts, save_accounts
        accounts = load_accounts()
        if 0 <= index < len(accounts):
            accounts.pop(index)
            save_accounts(accounts)
            self.reload_accounts()

    def _bg(self, fn, on_done):
        try:
            r = fn()
            e = None
        except Exception as ex:
            r, e = None, str(ex)
        QTimer.singleShot(0, lambda: on_done(r, e))
