"""粉丝群查询页（PySide6）：抖音 / 微博 / B站 三平台公开群 ID 与人数。

- 顶部选平台 + 粘贴主页链接/UID → 查询
- 表格：头像 / 群名 / 群ID / 群号 / 人数 / 人数变化 / 进群条件
- 查询时自动把群头像下载到 download/粉丝群/<平台>/<UID>/头像/，
  表格首列显示缩略图，双击行打开原图；「刷新头像」强制重新下载
- 每次查询自动存快照，并与上次快照对比人数
- 导出 Excel / 打开保存目录 / 复制选中或全部群 ID

平台差异：
- 抖音：需要登录态；可列 UP 主全部公开粉丝群（含 VIP 群）与人数
- 微博：需要登录态；可列用户全部公开粉丝群（含群号/人数/上限）
- B站：需要登录态；网页端只返回当前账号已加入的应援团，无法列 UP 全部群
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from gui_qt import table_sort
from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from platforms import fan_groups as fg

_PLATFORMS = [
    ("douyin", "抖音"),
    ("weibo", "微博"),
    ("bilibili", "B站"),
    ("qqgroup", "QQ群"),
]

_HINTS = {
    "douyin": "需要抖音登录态。可查该账号全部公开粉丝群（含 VIP 群）与人数。",
    "weibo": "需要微博登录态。可查该账号全部公开粉丝群（群号 / 人数 / 上限）。",
    "bilibili": "需要 B站登录态。网页端只列出当前账号已加入的应援团，无法列出 UP 全部群。",
    "qqgroup": "可粘贴群号（需先完成 QQ 群登录）或群资料分享链接（免登录）；"
               "登录后可在「订阅列表 → 更多 → 按群号订阅全部」批量订阅预置的 8 个官方群。",
}

_PLACEHOLDERS = {
    "douyin": "https://www.douyin.com/user/MS4wLjABAAAA...（或 sec_uid）",
    "weibo": "https://weibo.com/u/3056354293（或 UID）",
    "bilibili": "https://space.bilibili.com/1467772（或 UID）",
    "qqgroup": "粘贴 QQ 群号或分享链接（群号需先完成 QQ 群登录）",
}

_COLUMNS = ["头像", "群名", "群ID", "群号", "人数", "变化", "进群条件"]
_QQ_COLUMNS = ["头像", "群名", "群号", "人数", "变化", "状态", "来源"]

_AVATAR_SIZE = 40

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
        self._qq_dialog = None
        self._qq_configured: set[str] = set()
        self._qq_sources: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top = QFrame()
        top.setObjectName("fanGroupsTop")
        top.setStyleSheet(
            "QFrame#fanGroupsTop { background: palette(base); "
            "border: 1px solid palette(shadow); border-radius: 8px; }")
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

        self.qq_btn = QPushButton("订阅列表")
        self.qq_btn.setToolTip(
            "打开 QQ群订阅列表，支持批量导入分享链接、删除订阅和导出")
        self.qq_btn.setVisible(False)
        self.qq_btn.clicked.connect(self._open_qq_groups)
        row.addWidget(self.qq_btn)

        self.query_btn = QPushButton("查询")
        self.query_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.query_btn.clicked.connect(self._do_query)
        row.addWidget(self.query_btn)
        tv.addLayout(row)

        row2 = QHBoxLayout()
        self.hint_label = QLabel(_HINTS["douyin"])
        self.hint_label.setStyleSheet("color: palette(placeholder-text);")
        self.hint_label.setWordWrap(True)

        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._do_export)
        row2.addWidget(self.export_btn)

        self.avatar_btn = QPushButton("刷新头像")
        self.avatar_btn.setToolTip("强制重新下载当前列表的群头像（查询时已有文件会自动复用）")
        self.avatar_btn.setEnabled(False)
        self.avatar_btn.clicked.connect(self._do_refresh_avatars)
        row2.addWidget(self.avatar_btn)

        # QQ群模式专用：一键把已订阅群的群头像补下来（不藏进菜单）
        self.qq_avatar_btn = QPushButton("下载头像")
        self.qq_avatar_btn.setToolTip("下载全部已订阅 QQ 群的群头像（已有文件直接复用）")
        self.qq_avatar_btn.setVisible(False)
        self.qq_avatar_btn.clicked.connect(self._do_download_qq_avatars)
        row2.addWidget(self.qq_avatar_btn)

        self.dir_btn = QPushButton("打开保存目录")
        self.dir_btn.setEnabled(False)
        self.dir_btn.clicked.connect(self._open_dir)
        row2.addWidget(self.dir_btn)

        self.copy_one_btn = QPushButton("复制选中ID")
        self.copy_one_btn.setEnabled(False)
        self.copy_one_btn.clicked.connect(self._copy_selected)
        row2.addWidget(self.copy_one_btn)

        self.copy_all_btn = QPushButton("复制全部ID")
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.clicked.connect(self._copy_all)
        row2.addWidget(self.copy_all_btn)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        row2.addWidget(self.clear_btn)
        row2.addStretch(1)
        tv.addLayout(row2)
        tv.addWidget(self.hint_label)
        self.task_label = QLabel("")
        self.task_label.setProperty("role", "caption")
        self.task_label.setWordWrap(True)
        tv.addWidget(self.task_label)
        self._refresh_task_label()
        layout.addWidget(top)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(_AVATAR_SIZE, _AVATAR_SIZE))
        self.table.verticalHeader().setDefaultSectionSize(_AVATAR_SIZE + 8)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, _AVATAR_SIZE + 14)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, w in enumerate((170, 85, 80, 55, 150), 2):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
            self.table.setColumnWidth(i, w)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.cellDoubleClicked.connect(self._open_avatar)
        # 表头点击排序（首列头像不参与）；行号与 self._groups 下标一致，走记录模式
        self._sorter = table_sort.install(self.table, skip_columns=(0,))
        layout.addWidget(self.table, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中一行查看群详情（双击行打开群头像）")
        self.detail.setFixedHeight(150)
        layout.addWidget(self.detail)

        self.status_label = QLabel("选平台 → 粘贴主页链接 → 查询")
        self.status_label.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.status_label)

    # ---------- 状态 ----------
    def _refresh_task_label(self):
        try:
            from toolbox import fan_groups_task as fgt
            targets = fgt.load_targets()
        except Exception:  # noqa: BLE001 - 读不到订阅名单时按未订阅显示
            targets = []
        qq_count = sum(1 for t in targets if t.get("platform") == "qqgroup")
        account_count = len(targets) - qq_count
        parts, hints = [], []
        if account_count:
            parts.append(f"{account_count} 个账号")
            hints.append("账号名单在「账号与登录」勾选「粉丝群快照」管理")
        if qq_count:
            parts.append(f"{qq_count} 个 QQ 群")
            hints.append("QQ 群名单在「QQ群 → 订阅列表」管理")
        self.task_label.setText(
            f"定时快照：已订阅 {'、'.join(parts)}（{'；'.join(hints)}；"
            "时间到「任务 → 粉丝群快照」设置）"
            if parts else
            "定时快照：查询成功后账号自动加入订阅名单；QQ 群请在平台下拉选「QQ群」后粘贴群号（需先完成 QQ 群登录）或分享链接")

    def _open_qq_groups(self):
        if self._qq_dialog is None:
            from gui_qt.pages.qq_groups_dialog import QQGroupsDialog
            self._qq_dialog = QQGroupsDialog(self)
        self._qq_dialog.show()
        self._qq_dialog.raise_()
        self._qq_dialog.activateWindow()

    def _apply_columns(self):
        if self._platform == "qqgroup":
            headers = _QQ_COLUMNS
            widths = ((2, 110), (3, 80), (4, 70), (5, 190), (6, 90))
        else:
            headers = _COLUMNS
            widths = ((2, 170), (3, 85), (4, 80), (5, 55), (6, 150))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, _AVATAR_SIZE + 14)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for index, width in widths:
            self.table.horizontalHeader().setSectionResizeMode(
                index, QHeaderView.Interactive)
            self.table.setColumnWidth(index, width)

    def _load_qq_groups(self):
        try:
            from toolbox import qq_groups_service as qgs
            rows = qgs.catalog()
        except Exception as exc:  # noqa: BLE001 - 列表读取失败留在页面提示
            self.status_label.setText(f"读取 QQ 群订阅失败：{exc}")
            return
        self._qq_configured = {row.no for row in rows if row.share_url}
        self._qq_sources = {row.no: row.source for row in rows}
        self._owner_id = "all"
        self._prev_at = ""
        self._groups = []
        self._changes = {}
        for row in rows:
            self._groups.append(fg.FanGroup(
                platform="qqgroup", group_id=row.no, name=row.name,
                member_count=row.member_count, group_no=row.no,
                avatar=row.avatar, avatar_path=row.avatar_path,
                entry_limit=row.status))
            if row.is_new:
                self._changes[row.no] = None
            elif row.change is not None:
                self._changes[row.no] = row.change
        self._fill_qq_table()
        if self._groups:
            self.table.selectRow(0)
        self.export_btn.setEnabled(bool(self._groups))
        self.dir_btn.setEnabled(bool(self._groups))
        self.copy_all_btn.setEnabled(bool(self._groups))
        self.avatar_btn.setEnabled(bool(self._groups))
        self.status_label.setText(
            f"QQ群：共 {len(rows)} 个，已配置群号/分享链接 "
            f"{len(self._qq_configured)} 个；粘贴群号或分享链接可添加或更新")

    def _refresh_qq_view(self):
        if self._platform == "qqgroup" and not self._busy:
            self._load_qq_groups()

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.platform_box.setEnabled(not busy)
        self.query_btn.setEnabled(not busy)
        self.qq_btn.setEnabled(not busy)
        self.export_btn.setEnabled((not busy) and bool(self._groups))
        self.avatar_btn.setEnabled((not busy) and bool(self._groups))
        self.qq_avatar_btn.setEnabled((not busy) and bool(self._groups))
        if text:
            self.status_label.setText(text)

    def _selected(self) -> fg.FanGroup | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._groups):
            return self._groups[row]
        return None

    def _on_platform_change(self):
        self._platform = self.platform_box.currentData() or "douyin"
        self._apply_columns()
        self.input_edit.setPlaceholderText(_PLACEHOLDERS[self._platform])
        if self._platform == "qqgroup":
            self.qq_btn.setVisible(True)
            self.input_edit.clear()
            self.avatar_btn.setText("刷新人数")
            self.avatar_btn.setToolTip("刷新全部已配置分享链接的 QQ 群人数")
            self.qq_avatar_btn.setVisible(True)
            self.clear_btn.setText("重载列表")
        else:
            self.qq_btn.setVisible(False)
            self.input_edit.setText(_default_owner_url(self._platform))
            self.avatar_btn.setText("刷新头像")
            self.avatar_btn.setToolTip(
                "强制重新下载当前列表的群头像（查询时已有文件会自动复用）")
            self.qq_avatar_btn.setVisible(False)
            self.clear_btn.setText("清空")
        self.hint_label.setText(_HINTS[self._platform])
        self._clear()
        if self._platform == "qqgroup":
            self._load_qq_groups()

    def _on_clear_clicked(self):
        if self._platform == "qqgroup":
            if not self._busy:
                self._load_qq_groups()
        else:
            self._clear()

    # ---------- 查询 ----------
    def _do_query(self):
        if self._busy:
            return
        text = self.input_edit.text().strip()
        if not text:
            self.status_label.setText(
                "请先粘贴 QQ 群分享链接" if self._platform == "qqgroup"
                else "请先粘贴主页链接或输入 UID")
            return
        if self._platform == "qqgroup":
            self._do_qq_query(text)
            return
        platform = self._platform
        self._set_busy(True, "正在查询...")

        def prog(msg):
            post_to_main(self.status_label.setText, f"{msg}")

        def work():
            owner_id, groups = fg.fetch_groups(platform, text, progress=prog)
            av_ok, av_total = fg.download_avatars(platform, owner_id, groups, progress=prog)
            previous = fg.load_latest(platform, owner_id)
            changes = fg.diff_members(groups, previous)
            snap = fg.save_snapshot(platform, owner_id, groups)
            return (owner_id, groups, changes, (previous or {}).get("fetched_at", ""),
                    snap, av_ok, av_total)

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"{err}")
                self.app.toast(str(err)[:80], "warning")
                return
            owner_id, groups, changes, prev_at, snap, av_ok, av_total = result
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
            self.avatar_btn.setEnabled(bool(groups))
            avatar_text = f"，头像 {av_ok}/{av_total}" if av_total else ""
            subscribed, sub_msg = False, ""
            try:
                from toolbox import account_registry as registry
                subscribed, sub_msg = registry.subscribe_fan_groups(platform, owner_id)
            except Exception:  # noqa: BLE001 - 订阅名单写入失败不阻断查询
                pass
            self._refresh_task_label()
            suffix = ""
            if subscribed:
                suffix = "，已加入定时快照名单"
            elif sub_msg and "已存在" in sub_msg:
                suffix = "，已在定时快照名单里"
            self.status_label.setText(
                f"{fg.PLATFORM_LABELS[platform]} {owner_id}：{len(groups)} 个群"
                f"{avatar_text}，快照已保存（{os.path.basename(str(snap))}）" + suffix)
            self.app.toast(f"查到 {len(groups)} 个群", "success")

        run_bg(work, done)

    def _do_qq_query(self, text: str):
        from toolbox import qq_groups_service as qgs

        self._set_busy(True, "正在读取 QQ 群分享链接...")

        def done(row, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"保存失败：{err}")
                self.app.toast(str(err)[:80], "warning")
                return
            self.input_edit.clear()
            self._load_qq_groups()
            if row.is_new:
                self._changes[row.no] = None
            elif row.change is not None:
                self._changes[row.no] = row.change
            for index, group in enumerate(self._groups):
                if group.group_id == row.no:
                    self.table.selectRow(index)
                    break
            self._fill_qq_table()
            self._refresh_task_label()
            self.status_label.setText(
                f"已保存并订阅：{row.name}（{row.member_text} 人）；"
                "可在「订阅列表」批量管理")
            self.app.toast(f"已更新 {row.name}", "success")

        run_bg(lambda: qgs.save_link(text), done)

    def _avatar_icon(self, g: fg.FanGroup) -> QIcon:
        path = g.avatar_path
        if path and os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                return QIcon(pm.scaled(_AVATAR_SIZE, _AVATAR_SIZE,
                                       Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return QIcon()

    def _fill_table(self):
        self._sorter.attach(self._groups)
        self.table.setRowCount(len(self._groups))
        for r, g in enumerate(self._groups):
            av = QTableWidgetItem()
            av.setIcon(self._avatar_icon(g))
            av.setToolTip(g.avatar or "无头像")
            self.table.setItem(r, 0, av)
            it = QTableWidgetItem(g.name)
            it.setToolTip(g.name)
            self.table.setItem(r, 1, it)
            self.table.setItem(r, 2, _center(g.group_id))
            self.table.setItem(r, 3, _center(g.group_no or "-"))
            self.table.setItem(r, 4, _center(g.member_text))
            self.table.setItem(r, 5, _center(self._delta_text(g)))
            self.table.setItem(r, 6, _center(g.entry_limit or "-"))
        self._sorter.apply()
        self.export_btn.setEnabled(bool(self._groups))
        self.copy_all_btn.setEnabled(bool(self._groups))
        self.avatar_btn.setEnabled(bool(self._groups))

    def _fill_qq_table(self):
        self._sorter.attach(self._groups)
        self.table.setRowCount(len(self._groups))
        for r, g in enumerate(self._groups):
            av = QTableWidgetItem()
            av.setIcon(self._avatar_icon(g))
            av.setToolTip(g.avatar_path or g.avatar or "无头像")
            self.table.setItem(r, 0, av)
            name = QTableWidgetItem(g.name or "-")
            name.setToolTip(g.name or "")
            self.table.setItem(r, 1, name)
            self.table.setItem(r, 2, _center(g.group_no or g.group_id))
            member = _center(g.member_text)
            if g.member_count is not None:
                member.setData(table_sort.SORT_KEY_ROLE, g.member_count)
            self.table.setItem(r, 3, member)
            delta = _center(self._delta_text(g))
            if g.group_id in self._changes and self._changes[g.group_id] is not None:
                delta.setData(table_sort.SORT_KEY_ROLE, self._changes[g.group_id])
            self.table.setItem(r, 4, delta)
            status = _center(g.entry_limit or "-")
            status.setToolTip(g.entry_limit or "")
            self.table.setItem(r, 5, status)
            source = self._qq_sources.get(g.group_id, "")
            if source == "share":
                link_text = "分享链接"
                tip = "已保存该群的资料分享链接（不显示链接内容）"
            elif source == "login":
                link_text = "登录查询"
                tip = "已按群号订阅，刷新时使用 QQ 群登录态读取群资料"
            else:
                link_text = "未配置"
                tip = "粘贴该群群号（需登录）或资料分享链接"
            link = _center(link_text)
            link.setToolTip(tip)
            self.table.setItem(r, 6, link)
        self._sorter.apply()
        self.export_btn.setEnabled(bool(self._groups))
        self.copy_all_btn.setEnabled(bool(self._groups))
        self.avatar_btn.setEnabled(bool(self._groups))

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
            label = "状态" if g.platform == "qqgroup" else "进群条件"
            lines.append(f"{label}：{g.entry_limit}")
        if g.tags:
            lines.append(f"标签：{g.tags}")
        if g.owner_uid:
            lines.append(f"群主UID：{g.owner_uid}")
        if g.avatar:
            local = g.avatar_path if g.avatar_path and os.path.exists(g.avatar_path) else ""
            lines.append(f"头像：{local or '未下载'}（双击行打开）")
            lines.append(f"头像链接：{g.avatar}")
        if g.notice:
            lines.append(f"公告/简介：{g.notice}")
        if g.url:
            lines.append(f"群链接：{g.url}")
        self.detail.setPlainText("\n".join(lines))

    def _open_avatar(self, row: int, _col: int):
        if not (0 <= row < len(self._groups)):
            return
        g = self._groups[row]
        if g.avatar_path and os.path.exists(g.avatar_path):
            try:
                os.startfile(g.avatar_path)
                return
            except Exception:
                pass
        if g.avatar:
            QDesktopServices.openUrl(QUrl(g.avatar))
        else:
            self.app.toast("该群没有头像", "info")

    # ---------- 头像 ----------
    def _do_refresh_avatars(self):
        if self._platform == "qqgroup":
            self._do_refresh_qq_counts()
            return
        if not self._groups or self._busy:
            return
        platform, owner_id = self._platform, self._owner_id
        groups = list(self._groups)
        self._set_busy(True, "正在重新下载群头像...")

        def prog(msg):
            post_to_main(self.status_label.setText, f"{msg}")

        def work():
            ok, total = fg.download_avatars(platform, owner_id, groups,
                                            progress=prog, force=True)
            fg.update_latest(platform, owner_id, groups, note="头像刷新")
            return ok, total

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"头像下载失败：{err}")
                self.app.toast(f"头像下载失败：{err}", "warning")
                return
            ok, total = result
            self._fill_table()
            self.status_label.setText(f"群头像已更新：{ok}/{total}")
            self.app.toast(f"群头像 {ok}/{total}", "success")

        run_bg(work, done)

    def _do_download_qq_avatars(self):
        """QQ群模式：一键下载全部已订阅群的头像（已有文件复用，不覆盖）。"""
        if self._busy:
            return
        if not self._qq_configured:
            self.status_label.setText("还没有订阅的群；先在「QQ群 → 订阅列表」按群号订阅")
            return
        from toolbox import qq_groups_service as qgs

        self._set_busy(True, "正在下载群头像...")

        def progress(index, total, label):
            post_to_main(self.status_label.setText,
                         f"正在下载头像 {index}/{total}：{label}")

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"下载头像失败：{err}")
                self.app.toast(str(err)[:80], "warning")
                return
            self._load_qq_groups()
            if result["errors"]:
                self.status_label.setText(
                    f"已下载 {result['ok']}/{result['total']} 个头像，"
                    f"失败 {len(result['errors'])} 个")
            else:
                self.status_label.setText(
                    f"已下载 {result['ok']}/{result['total']} 个群头像")
            self.app.toast("群头像已下载", "success")

        run_bg(lambda: qgs.refresh_avatars(force=False, progress=progress),
               done, owner=self)

    def _do_refresh_qq_counts(self):
        if not self._groups or self._busy:
            return
        if not self._qq_configured:
            self.status_label.setText(
                "还没有配置群号或分享链接；可登录后点「订阅列表 → 按群号订阅全部」。")
            return
        from toolbox import qq_groups_service as qgs

        self._set_busy(True, "正在刷新 QQ 群人数...")

        def progress(index, total, label):
            post_to_main(self.status_label.setText,
                         f"正在刷新 {index}/{total}：{label}")

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"刷新失败：{err}")
                self.app.toast(str(err)[:80], "warning")
                return
            self._load_qq_groups()
            for row in result["results"]:
                self._changes[row.no] = None if row.is_new else row.change
            self._fill_qq_table()
            errors = result["errors"]
            if errors:
                self.status_label.setText(
                    f"已刷新 {result['ok']}/{result['total']} 个群，"
                    f"失败 {len(errors)} 个：{errors[0][1]}")
            else:
                self.status_label.setText(
                    f"QQ群人数已刷新：{result['ok']}/{result['total']}")
            self.app.toast("QQ群人数已刷新", "success")

        run_bg(lambda: qgs.refresh_all(progress=progress), done, owner=self)

    # ---------- 导出 / 复制 ----------
    def _do_export(self):
        if not self._groups or self._busy:
            return
        platform, owner_id = self._platform, self._owner_id
        groups = list(self._groups)
        changes = dict(self._changes)
        self._set_busy(True, "正在导出 Excel...")

        def work():
            return fg.export_xlsx(platform, owner_id, groups, changes)

        def done(path, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"导出失败：{err}")
                self.app.toast(f"导出失败：{err}", "warning")
                return
            self.status_label.setText(f"已导出：{path}")
            self.app.toast(f"已导出 {os.path.basename(str(path))}", "success")

        run_bg(work, done)

    def _open_dir(self):
        # QQ群是一群一目录：优先打开选中群的目录（历史快照、头像都在里面）
        if self._platform == "qqgroup":
            g = self._selected()
            if g is not None and g.group_id:
                path = fg.group_dir("qqgroup", g.group_id, create=False)
                if path.exists():
                    try:
                        os.startfile(str(path))
                        return
                    except OSError:
                        pass
            path = fg.group_dir("qqgroup", "all", create=False)
            if path.exists():
                os.startfile(str(path))
                self.app.toast("单个群的历史快照在 download/粉丝群/QQ群/<群号>/", "info")
            else:
                self.app.toast("还没有快照（先订阅并刷新一次人数）", "info")
            return
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
        self.status_label.setText(f"已复制群 ID：{g.group_id}")
        self.app.toast("群 ID 已复制", "success")

    def _copy_all(self):
        ids = [g.group_id for g in self._groups if g.group_id]
        if not ids:
            return
        QGuiApplication.clipboard().setText("\n".join(ids))
        self.status_label.setText(f"已复制 {len(ids)} 个群 ID")
        self.app.toast(f"已复制 {len(ids)} 个群 ID", "success")

    def _clear(self):
        if self._busy:
            return
        self._owner_id = ""
        self._groups = []
        self._changes = {}
        self._prev_at = ""
        self._qq_configured = set()
        self._qq_sources = {}
        self.table.setRowCount(0)
        self.detail.clear()
        self.export_btn.setEnabled(False)
        self.avatar_btn.setEnabled(False)
        self.dir_btn.setEnabled(False)
        self.copy_one_btn.setEnabled(False)
        self.copy_all_btn.setEnabled(False)
        self.status_label.setText("选平台 → 粘贴主页链接 → 查询")
