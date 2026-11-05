"""粉丝群查询页（PySide6）：抖音 / 微博 / B站 三平台公开群 ID 与人数。

- 顶部选平台 + 粘贴主页链接/UID → 🔍 查询
- 表格：群名 / 群ID / 群号 / 人数 / 人数变化 / 进群条件 / 标签
- 每次查询自动存快照（download/粉丝群/<平台>/<UID>/），并与上次快照对比人数
- 导出 Excel / 打开保存目录 / 复制选中或全部群 ID

平台差异：
- 抖音：需要登录态；可列 UP 主全部公开粉丝群（含 VIP 群）与人数
- 微博：需要登录态；可列用户全部公开粉丝群（含群号/人数/上限）
- B站：需要登录态；网页端只返回当前账号已加入的应援团，无法列 UP 全部群
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from platforms import fan_groups as fg

_PLATFORMS = [
    ("douyin", "抖音"),
    ("weibo", "微博"),
    ("bilibili", "B站"),
]

_HINTS = {
    "douyin": "需要抖音登录态。可查该账号全部公开粉丝群（含 VIP 群）与人数。",
    "weibo": "需要微博登录态。可查该账号全部公开粉丝群（群号 / 人数 / 上限）。",
    "bilibili": "需要 B站登录态。网页端只列出当前账号已加入的应援团，无法列出 UP 全部群。",
}

_PLACEHOLDERS = {
    "douyin": "https://www.douyin.com/user/MS4wLjABAAAA...（或 sec_uid）",
    "weibo": "https://weibo.com/u/3056354293（或 UID）",
    "bilibili": "https://space.bilibili.com/1467772（或 UID）",
}

_COLUMNS = ["群名", "群ID", "群号", "人数", "变化", "进群条件"]

_DEFAULT_OWNER = "封茗囧菌"
_PLATFORM_HOSTS = {
    "douyin": "douyin.com",
    "weibo": "weibo.com",
    "bilibili": "bilibili.com",
}


def _default_owner_url(platform: str) -> str:
    """当前平台的默认主页链接：取「账号获取」里名字含封茗囧菌的已保存账号。"""
    host = _PLATFORM_HOSTS.get(platform, "")
    if not host:
        return ""
    try:
        from accounts_manager import load_accounts
        for a in load_accounts():
            url = (getattr(a, "url", "") or "").strip()
            if host in url and _DEFAULT_OWNER in (getattr(a, "name", "") or ""):
                return url
    except Exception:
        pass
    return ""


def _center(text):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(Qt.AlignCenter)
    return it


class FanGroupsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._platform = "douyin"
        self._owner_id = ""
        self._groups: list[fg.FanGroup] = []
        self._changes: dict[str, int | None] = {}
        self._prev_at = ""
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("👥 粉丝群查询")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 粉丝群查询 · 功能介绍", (
            "<p><b>查询抖音 / 微博 / B站账号的公开粉丝群 ID 与人数。</b></p>"
            "<p>· <b>抖音</b>：主页链接 → 全部公开粉丝群（含 VIP 群）。"
            "需要登录态，游客会被 blocked</p>"
            "<p>· <b>微博</b>：主页链接 → 全部公开粉丝群，附带群号 / 人数 / 人数上限</p>"
            "<p>· <b>B站</b>：输入 UP 主 UID → 当前账号<b>已加入</b>的应援团。"
            "网页端没有 UP 全部群的公开列表，未加入的群查不到</p>"
            "<p>· <b>人数变化</b>：每次查询自动存快照，与上一次快照对比；"
            "新出现的群标记为「新」</p>"
            "<p>· <b>保存位置</b>：download/粉丝群/&lt;平台&gt;/&lt;UID&gt;/"
            "（快照_时间.json + 最新.json + 导出的 Excel）</p>"
        )))
        layout.addLayout(title_row)

        top = QFrame()
        top.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(6)

        row = QHBoxLayout()
        self.platform_box = QComboBox()
        for key, label in _PLATFORMS:
            self.platform_box.addItem(label, key)
        self.platform_box.setFixedWidth(88)
        self.platform_box.currentIndexChanged.connect(self._on_platform_change)
        row.addWidget(self.platform_box)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(_PLACEHOLDERS["douyin"])
        self.input_edit.setText(_default_owner_url(self._platform))
        self.input_edit.returnPressed.connect(self._do_query)
        row.addWidget(self.input_edit, 1)

        self.query_btn = QPushButton("🔍 查询")
        self.query_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.query_btn.clicked.connect(self._do_query)
        row.addWidget(self.query_btn)
        tv.addLayout(row)

        row2 = QHBoxLayout()
        self.hint_label = QLabel(_HINTS["douyin"])
        self.hint_label.setStyleSheet("color: #666;")
        row2.addWidget(self.hint_label, 1)

        self.export_btn = QPushButton("📊 导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._do_export)
        row2.addWidget(self.export_btn)

        self.dir_btn = QPushButton("📂 打开保存目录")
        self.dir_btn.setEnabled(False)
        self.dir_btn.clicked.connect(self._open_dir)
        row2.addWidget(self.dir_btn)

        self.copy_one_btn = QPushButton("📋 复制选中ID")
        self.copy_one_btn.setEnabled(False)
        self.copy_one_btn.clicked.connect(self._copy_selected)
        row2.addWidget(self.copy_one_btn)

        self.copy_all_btn = QPushButton("📋 复制全部ID")
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.clicked.connect(self._copy_all)
        row2.addWidget(self.copy_all_btn)

        clear_btn = QPushButton("🗑 清空")
        clear_btn.clicked.connect(self._clear)
        row2.addWidget(clear_btn)
        tv.addLayout(row2)
        layout.addWidget(top)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate((170, 85, 80, 55, 150), 1):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
            self.table.setColumnWidth(i, w)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中一行查看群详情")
        self.detail.setFixedHeight(150)
        layout.addWidget(self.detail)

        self.status_label = QLabel("选平台 → 粘贴主页链接 → 🔍 查询")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

    # ---------- 状态 ----------
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.platform_box.setEnabled(not busy)
        self.query_btn.setEnabled(not busy)
        self.export_btn.setEnabled((not busy) and bool(self._groups))
        if text:
            self.status_label.setText(text)

    def _selected(self) -> fg.FanGroup | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._groups):
            return self._groups[row]
        return None

    def _on_platform_change(self):
        self._platform = self.platform_box.currentData() or "douyin"
        self.input_edit.setPlaceholderText(_PLACEHOLDERS[self._platform])
        self.input_edit.setText(_default_owner_url(self._platform))
        self.hint_label.setText(_HINTS[self._platform])
        self._clear()

    # ---------- 查询 ----------
    def _do_query(self):
        if self._busy:
            return
        text = self.input_edit.text().strip()
        if not text:
            self.status_label.setText("❌ 请先粘贴主页链接或输入 UID")
            return
        platform = self._platform
        self._set_busy(True, "⏳ 正在查询...")

        def prog(msg):
            post_to_main(self.status_label.setText, f"⏳ {msg}")

        def work():
            owner_id, groups = fg.fetch_groups(platform, text, progress=prog)
            previous = fg.load_latest(platform, owner_id)
            changes = fg.diff_members(groups, previous)
            snap = fg.save_snapshot(platform, owner_id, groups)
            return owner_id, groups, changes, (previous or {}).get("fetched_at", ""), snap

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ {err}")
                self.app.toast(str(err)[:80], "warning")
                return
            owner_id, groups, changes, prev_at, snap = result
            self._owner_id = owner_id
            self._groups = groups
            self._changes = changes
            self._prev_at = prev_at
            self._fill_table()
            if groups:
                self.table.selectRow(0)
            self.export_btn.setEnabled(bool(groups))
            self.dir_btn.setEnabled(True)
            self.copy_all_btn.setEnabled(bool(groups))
            self.status_label.setText(
                f"✅ {fg.PLATFORM_LABELS[platform]} {owner_id}：{len(groups)} 个群，"
                f"快照已保存（{os.path.basename(str(snap))}）")
            self.app.toast(f"查到 {len(groups)} 个群", "success")

        run_bg(work, done)

    def _fill_table(self):
        self.table.setRowCount(len(self._groups))
        for r, g in enumerate(self._groups):
            it = QTableWidgetItem(g.name)
            it.setToolTip(g.name)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, _center(g.group_id))
            self.table.setItem(r, 2, _center(g.group_no or "-"))
            self.table.setItem(r, 3, _center(g.member_text))
            self.table.setItem(r, 4, _center(self._delta_text(g)))
            self.table.setItem(r, 5, _center(g.entry_limit or "-"))
        self.export_btn.setEnabled(bool(self._groups))
        self.copy_all_btn.setEnabled(bool(self._groups))

    def _delta_text(self, g: fg.FanGroup) -> str:
        if g.member_count is None:      # B站应援团不返回人数，无法对比
            return "-"
        if g.group_id not in self._changes:
            return "-"
        d = self._changes[g.group_id]
        if d is None:
            return "新"
        if d > 0:
            return f"+{d}"
        return str(d)

    # ---------- 详情 ----------
    def _on_select(self):
        g = self._selected()
        if not g:
            self.copy_one_btn.setEnabled(False)
            return
        self.copy_one_btn.setEnabled(True)
        lines = [
            f"平台：{fg.PLATFORM_LABELS.get(g.platform, g.platform)}",
            f"群名：{g.name or '-'}",
            f"群ID：{g.group_id or '-'}",
        ]
        if g.group_no:
            lines.append(f"群号：{g.group_no}")
        lines.append(f"人数：{g.member_text}")
        delta = self._delta_text(g)
        if delta != "-":
            cmp_at = f"（对比 {self._prev_at}）" if self._prev_at else ""
            lines.append(f"人数变化：{delta} {cmp_at}".strip())
        if g.entry_limit:
            lines.append(f"进群条件：{g.entry_limit}")
        if g.tags:
            lines.append(f"标签：{g.tags}")
        if g.owner_uid:
            lines.append(f"群主UID：{g.owner_uid}")
        if g.notice:
            lines.append(f"公告/简介：{g.notice}")
        if g.url:
            lines.append(f"群链接：{g.url}")
        self.detail.setPlainText("\n".join(lines))

    # ---------- 导出 / 复制 ----------
    def _do_export(self):
        if not self._groups or self._busy:
            return
        platform, owner_id = self._platform, self._owner_id
        groups = list(self._groups)
        changes = dict(self._changes)
        self._set_busy(True, "⏳ 正在导出 Excel...")

        def work():
            return fg.export_xlsx(platform, owner_id, groups, changes)

        def done(path, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 导出失败：{err}")
                self.app.toast(f"导出失败：{err}", "warning")
                return
            self.status_label.setText(f"✅ 已导出：{path}")
            self.app.toast(f"已导出 {os.path.basename(str(path))}", "success")

        run_bg(work, done)

    def _open_dir(self):
        if not self._owner_id:
            return
        try:
            os.startfile(str(fg.group_dir(self._platform, self._owner_id)))
        except Exception:
            self.app.toast("保存目录尚未创建（先完成一次查询）", "info")

    def _copy_selected(self):
        g = self._selected()
        if not g or not g.group_id:
            return
        QGuiApplication.clipboard().setText(g.group_id)
        self.status_label.setText(f"✅ 已复制群 ID：{g.group_id}")
        self.app.toast("群 ID 已复制", "success")

    def _copy_all(self):
        ids = [g.group_id for g in self._groups if g.group_id]
        if not ids:
            return
        QGuiApplication.clipboard().setText("\n".join(ids))
        self.status_label.setText(f"✅ 已复制 {len(ids)} 个群 ID")
        self.app.toast(f"已复制 {len(ids)} 个群 ID", "success")

    def _clear(self):
        if self._busy:
            return
        self._owner_id = ""
        self._groups = []
        self._changes = {}
        self._prev_at = ""
        self.table.setRowCount(0)
        self.detail.clear()
        self.export_btn.setEnabled(False)
        self.dir_btn.setEnabled(False)
        self.copy_one_btn.setEnabled(False)
        self.copy_all_btn.setEnabled(False)
        self.status_label.setText("选平台 → 粘贴主页链接 → 🔍 查询")
