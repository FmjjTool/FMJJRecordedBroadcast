"""账号配置页（Cookie 管理第二页）：统一添加/删除账号、分组，并勾选账号可用在哪些功能。

三个采集功能（信息获取 / 网媒追踪 / 音乐追踪）与粉丝群快照原有的设置入口已迁移到这里：
账号是否出现在某个功能的主数据里，就等于该功能能不能使用它，
写入统一走 `toolbox.account_registry`（各功能主数据仍是唯一事实来源）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import run_bg
from gui_qt.navigation import line_icon
from gui_qt.row_widgets import GroupMultiSelect
from toolbox import account_registry as registry

_KIND_OPTIONS = (("自动", ""), ("作品", "video"), ("动态", "dynamic"), ("全部", "all"))
_SEARCH_PLATFORMS = (("网易云", "netease"), ("QQ音乐", "qq"), ("酷狗", "kugou"), ("酷我", "kuwo"))


class AccountCenterWidget(QWidget):
    changed = Signal()

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app
        self._building = False
        self._groups: dict[str, str] = {}
        self._accounts: list[dict] = []
        self._build()
        self.reload()

    # ==================== 界面 ====================

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        intro = QLabel(
            "统一维护各功能的账号：勾选「可用于」即把账号写入对应功能，取消勾选即移除；"
            "「粉丝群快照」是订阅项，勾选后每天自动把该账号的群列表与人数存成快照。"
            "分组与采集类型会同步到该账号已启用的功能。")
        intro.setProperty("role", "caption")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        outer.addWidget(self._build_add_card())
        outer.addWidget(self._build_group_card())

        list_card = QFrame()
        list_card.setProperty("variant", "card")
        lv = QVBoxLayout(list_card)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("账号列表")
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "caption")
        head.addWidget(self.count_label)
        head.addStretch(1)
        refresh_btn = self._small_button("刷新", "重新读取各功能的账号", self.reload)
        head.addWidget(refresh_btn)
        lv.addLayout(head)

        # 列表不再自己滚动：整页由外层（弹窗页签）统一滚动，
        # 避免「页内 + 页外」两条并排的滚动条，也避免列表高度把弹窗最小尺寸顶大。
        # 二级页签：视频账号 / 音乐账号，各自一个列表。
        self.acc_tabs = QTabWidget()
        self.acc_tabs.setDocumentMode(True)
        self.acc_layouts: dict[str, QVBoxLayout] = {}
        for key in ("video", "music"):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(4, 6, 4, 4)
            layout.setSpacing(4)
            layout.addStretch(1)
            self.acc_layouts[key] = layout
            self.acc_tabs.addTab(page, "")
        lv.addWidget(self.acc_tabs)
        outer.addWidget(list_card)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "caption")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

    def _build_add_card(self) -> QWidget:
        card = QFrame()
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        title = QLabel("添加账号（账号名 + 链接 + 可用于）")
        title.setProperty("role", "sectionTitle")
        v.addWidget(title)

        row1 = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("账号名（留空自动获取，如 封茗囧菌抖音）")
        row1.addWidget(self.name_edit, 1)
        search_btn = QPushButton("搜索歌手")
        search_btn.setToolTip("按名称搜索音乐平台账号/歌手，选中后自动填入名称与链接")
        search_btn.clicked.connect(self._open_search)
        row1.addWidget(search_btn)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("账号主页链接（自动识别平台：视频号 / 音乐号）")
        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.returnPressed.connect(self._add)
        row2.addWidget(self.url_edit, 1)
        self.plat_label = QLabel("平台：未识别")
        self.plat_label.setProperty("role", "caption")
        self.plat_label.setMinimumWidth(96)
        row2.addWidget(self.plat_label)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("可用于:"))
        self.add_module_boxes: dict[str, QCheckBox] = {}
        for module in registry.MODULES:
            box = QCheckBox(registry.MODULE_NAMES[module])
            box.setEnabled(False)
            row3.addWidget(box)
            self.add_module_boxes[module] = box
        row3.addSpacing(10)
        row3.addWidget(QLabel("分组:"))
        self.add_group_sel = GroupMultiSelect(width=140)
        row3.addWidget(self.add_group_sel)
        row3.addStretch(1)
        self.add_btn = QPushButton("添加账号")
        self.add_btn.setProperty("variant", "primary")
        self.add_btn.clicked.connect(self._add)
        row3.addWidget(self.add_btn)
        v.addLayout(row3)
        return card

    def _build_group_card(self) -> QWidget:
        card = QFrame()
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        title = QLabel("分组管理（三个功能共用同一套组号）")
        title.setProperty("role", "sectionTitle")
        v.addWidget(title)

        self.group_scroll = QScrollArea()
        self.group_scroll.setWidgetResizable(True)
        self.group_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.group_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: 1px solid palette(shadow); border-radius: 6px; }")
        self.group_scroll.setFixedHeight(96)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self.group_layout = QVBoxLayout(container)
        self.group_layout.setContentsMargins(4, 4, 4, 4)
        self.group_layout.setSpacing(3)
        self.group_layout.addStretch(1)
        self.group_scroll.setWidget(container)
        v.addWidget(self.group_scroll)

        add_row = QHBoxLayout()
        self.gid_edit = QLineEdit()
        self.gid_edit.setPlaceholderText("组编号(11/22)")
        self.gid_edit.setFixedWidth(110)
        add_row.addWidget(self.gid_edit)
        self.gname_edit = QLineEdit()
        self.gname_edit.setPlaceholderText("组名")
        add_row.addWidget(self.gname_edit, 1)
        add_group_btn = self._small_button("", "新建分组", self._add_group)
        add_group_btn.setIcon(line_icon("plus", theme.color("text"), 14))
        add_group_btn.setFixedSize(34, 26)
        add_row.addWidget(add_group_btn)
        v.addLayout(add_row)
        return card

    def _small_button(self, text, tip, slot) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setFixedHeight(26)
        button.setStyleSheet("padding: 0 8px;")
        button.clicked.connect(lambda *_: slot())
        return button

    # ==================== 数据 ====================

    def reload(self):
        self._building = True
        try:
            self._groups = registry.groups()
            self._accounts = registry.list_accounts()
            self.add_group_sel.set_groups(self._groups)
            self._reload_groups()
            self._reload_accounts()
            video = sum(1 for a in self._accounts if a["platform"] in registry.MEDIA_PLATFORMS)
            music = sum(1 for a in self._accounts if a["platform"] in registry.MUSIC_PLATFORMS)
            self.count_label.setText(
                f"共 {len(self._accounts)} 个（视频账号 {video} / 音乐账号 {music}）")
        finally:
            self._building = False

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                AccountCenterWidget._clear_layout(item.layout())

    def _reload_groups(self):
        self._clear_layout(self.group_layout)
        self.group_layout.addStretch(1)
        if not self._groups:
            label = QLabel("（暂无分组，可在下方新建）")
            label.setProperty("role", "muted")
            self.group_layout.takeAt(self.group_layout.count() - 1)
            self.group_layout.addWidget(label)
            self.group_layout.addStretch(1)
            return
        gids = list(self._groups.keys())
        for pos, gid in enumerate(gids):
            row = QFrame()
            row.setProperty("variant", "card")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 3, 6, 3)
            h.setSpacing(6)
            gid_label = QLabel(gid)
            gid_label.setFixedWidth(40)
            gid_label.setStyleSheet(f"font-weight: bold; color: {theme.color('link')};")
            h.addWidget(gid_label)
            h.addWidget(QLabel(self._groups[gid]), 1)
            up_btn = self._small_button("↑", "上移一位", lambda g=gid: self._move_group(g, -1))
            up_btn.setEnabled(pos > 0)
            up_btn.setFixedSize(28, 24)
            h.addWidget(up_btn)
            down_btn = self._small_button("↓", "下移一位", lambda g=gid: self._move_group(g, 1))
            down_btn.setEnabled(pos < len(gids) - 1)
            down_btn.setFixedSize(28, 24)
            h.addWidget(down_btn)
            del_btn = self._small_button("×", "删除该分组（各功能中绑定该组的账号会解绑）",
                                         lambda g=gid: self._delete_group(g))
            del_btn.setFixedSize(28, 24)
            del_btn.setProperty("variant", "danger")
            h.addWidget(del_btn)
            self.group_layout.takeAt(self.group_layout.count() - 1)
            self.group_layout.addWidget(row)
            self.group_layout.addStretch(1)

    def _reload_accounts(self):
        video = [a for a in self._accounts if a["platform"] not in registry.MUSIC_PLATFORMS]
        music = [a for a in self._accounts if a["platform"] in registry.MUSIC_PLATFORMS]
        self.acc_tabs.setTabText(0, f"视频账号 {len(video)}")
        self.acc_tabs.setTabText(1, f"音乐账号 {len(music)}")
        self._fill_account_tab(self.acc_layouts["video"], video, "视频账号")
        self._fill_account_tab(self.acc_layouts["music"], music, "音乐账号")

    def _fill_account_tab(self, layout, accounts, title):
        self._clear_layout(layout)
        layout.addStretch(1)
        if not accounts:
            label = QLabel(f"（暂无{title}，先在上方添加）")
            label.setProperty("role", "muted")
            label.setAlignment(Qt.AlignCenter)
            layout.takeAt(layout.count() - 1)
            layout.addWidget(label)
            layout.addStretch(1)
            return
        for pos, account in enumerate(accounts):
            layout.takeAt(layout.count() - 1)
            layout.addWidget(self._build_account_row(account, pos, len(accounts)))
            layout.addStretch(1)

    def _build_account_row(self, account: dict, pos: int, group_size: int) -> QWidget:
        row = QFrame()
        row.setProperty("variant", "card")
        v = QVBoxLayout(row)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        name = QLabel(account["name"] or "（未命名）")
        name.setStyleSheet("font-weight: bold;")
        head.addWidget(name)
        # B 站动态与 B 站作品是两条账号，名字可能相同，这里加标记区分
        if account["platform"] == "bilibili" and \
                account["modules"].get("info", {}).get("kind") == "dynamic":
            dynamic_tag = QLabel("动态")
            dynamic_tag.setProperty("role", "caption")
            head.addWidget(dynamic_tag)
        platform = QLabel(f"[{account['platform_name']}]")
        platform.setProperty("role", "caption")
        head.addWidget(platform)
        url = QLabel(account["url"] if len(account["url"]) <= 56 else account["url"][:53] + "...")
        url.setProperty("role", "caption")
        url.setToolTip(account["url"])
        url.setMinimumWidth(0)
        url.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(url, 1)

        edit_btn = self._small_button("", "修改账号名/链接",
                                      lambda aid=account["id"]: self._edit_account(aid))
        edit_btn.setIcon(line_icon("edit", theme.color("text"), 13))
        edit_btn.setFixedSize(30, 24)
        head.addWidget(edit_btn)
        up_btn = self._small_button("↑", "上移一位",
                                    lambda aid=account["id"]: self._move_account(aid, -1))
        up_btn.setEnabled(pos > 0)
        up_btn.setFixedSize(28, 24)
        head.addWidget(up_btn)
        down_btn = self._small_button("↓", "下移一位",
                                      lambda aid=account["id"]: self._move_account(aid, 1))
        down_btn.setEnabled(pos < group_size - 1)
        down_btn.setFixedSize(28, 24)
        head.addWidget(down_btn)
        del_btn = self._small_button("×", "从所有功能中删除该账号",
                                     lambda aid=account["id"]: self._delete_account(aid))
        del_btn.setFixedSize(28, 24)
        del_btn.setProperty("variant", "danger")
        head.addWidget(del_btn)
        v.addLayout(head)

        opts = QHBoxLayout()
        opts.setSpacing(6)
        opts.addWidget(QLabel("可用于:"))
        for module in registry.MODULES:
            box = QCheckBox(registry.MODULE_NAMES[module])
            allowed = module in account["allowed"]
            box.setChecked(module in account["modules"])
            box.setEnabled(allowed)
            if not allowed:
                box.setToolTip(f"{account['platform_name']}账号不适用该功能")
            box.toggled.connect(
                lambda checked, aid=account["id"], m=module: self._toggle_module(aid, m, checked))
            opts.addWidget(box)

        opts.addSpacing(10)
        opts.addWidget(QLabel("分组:"))
        group_sel = GroupMultiSelect(width=124)
        group_sel.set_groups(self._groups)
        group_sel.set_selected(account["group_ids"])
        group_sel.changed.connect(
            lambda aid=account["id"], sel=group_sel: self._change_group(aid, sel.selected()))
        if not any(m in account["modules"] for m in ("info", "tracker", "music")):
            group_sel.setEnabled(False)
            group_sel.setToolTip("粉丝群快照不使用分组")
        opts.addWidget(group_sel)

        if account["platform"] == "bilibili":
            opts.addSpacing(8)
            kind_label = QLabel("采集类型:")
            opts.addWidget(kind_label)
            kind_combo = QComboBox()
            for label, _value in _KIND_OPTIONS:
                kind_combo.addItem(label, _value)
            current_kind = account["modules"].get("info", {}).get("kind", "")
            kind_combo.setCurrentIndex(max(0, kind_combo.findData(current_kind)))
            kind_combo.setFixedWidth(82)
            kind_combo.setEnabled("info" in account["modules"])
            if "info" not in account["modules"]:
                kind_combo.setToolTip("采集类型只在「信息获取」中生效")
            kind_combo.activated.connect(
                lambda _i, aid=account["id"], combo=kind_combo: self._change_kind(aid, combo))
            opts.addWidget(kind_combo)

        opts.addStretch(1)
        v.addLayout(opts)
        return row

    # ==================== 操作 ====================

    def _toast(self, text, level="info"):
        for candidate in (self.app, self.parent(), self.window()):
            handler = getattr(candidate, "toast", None)
            if callable(handler):
                try:
                    handler(text, level)
                    return
                except Exception:
                    continue
        self.status_label.setText(text)

    def _on_url_changed(self, _text=""):
        platform = registry.platform_of(self.url_edit.text().strip())
        allowed = registry.allowed_modules(platform)
        if platform:
            self.plat_label.setText(f"平台：{registry.platform_name(platform)}")
        elif self.url_edit.text().strip():
            self.plat_label.setText("平台：未识别")
        else:
            self.plat_label.setText("平台：未识别")
        for module, box in self.add_module_boxes.items():
            usable = module in allowed
            box.setEnabled(usable)
            # 粉丝群快照是订阅型使用位置：新录入账号默认不勾，避免自动订阅一堆名单
            if usable and module != "fan_groups":
                box.setChecked(True)
            else:
                box.setChecked(False)

    def _add(self):
        url = self.url_edit.text().strip()
        name = self.name_edit.text().strip()
        platform = registry.platform_of(url)
        if not url:
            self._toast("请填写账号链接", "warning")
            return
        if not platform:
            self._toast("无法识别链接所属平台，请检查链接", "warning")
            return
        modules = [m for m, box in self.add_module_boxes.items() if box.isChecked()]
        if not modules:
            self._toast("请至少勾选一个使用位置", "warning")
            return
        gids = self.add_group_sel.selected()
        if name:
            self._do_add(url, name, modules, gids)
            return
        self._toast("账号名留空，正在自动获取…", "info")
        self.add_btn.setEnabled(False)

        def work():
            from platforms.account_names import fetch_display_name
            return fetch_display_name(url)

        def done(result, err):
            self.add_btn.setEnabled(True)
            auto_name = ""
            if isinstance(result, tuple):
                auto_name = (result[0] or "").strip()
            if not auto_name:
                reason = str(err)[:60] if err else ""
                self._toast(f"自动获取账号名失败{('：' + reason) if reason else ''}，按「未命名」添加",
                            "warning")
            self._do_add(url, auto_name or "未命名", modules, gids)

        run_bg(work, done)

    def _do_add(self, url, name, modules, gids):
        ok, message = registry.add_account(name, url, modules, group_id=gids)
        self._toast(message, "success" if ok else "warning")
        if ok:
            self.name_edit.clear()
            self.url_edit.clear()
            self.reload()
            self.changed.emit()

    def _toggle_module(self, account_id, module, checked):
        if self._building:
            return
        account = registry.get_account(account_id)
        if account is None:
            self.reload()
            return
        if not checked and len(account["modules"]) <= 1:
            # 取消最后一个功能 = 从账号列表移除，先确认（历史采集数据不受影响）
            answer = QMessageBox.question(
                self, "移出账号列表",
                f"「{account['name'] or account['url'][:40]}」只用于"
                f"「{registry.MODULE_NAMES[module]}」。\n"
                "取消勾选后该账号会从账号列表移除（已采集的历史数据保留）。是否继续？",
                QMessageBox.Yes | QMessageBox.No)
            if answer != QMessageBox.Yes:
                self.reload()  # 恢复复选框状态
                return
        ok = registry.set_module(account_id, module, bool(checked))
        if not ok:
            self._toast("修改失败：账号可能已被删除", "warning")
        self.reload()
        self.changed.emit()

    def _change_group(self, account_id, gids):
        if not registry.set_group(account_id, gids):
            self._toast("分组保存失败", "error")
            return
        # 分组按钮已经显示新选择；这里不重建列表，避免销毁正在弹菜单的控件
        self.changed.emit()

    def _change_kind(self, account_id, combo):
        kind = combo.currentData() or ""
        ok, message = registry.update_account(account_id, kind=kind)
        self._toast(message, "success" if ok else "warning")
        self.reload()
        self.changed.emit()

    def _edit_account(self, account_id):
        account = registry.get_account(account_id)
        if account is None:
            self._toast("账号不存在（可能已被删除）", "warning")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("修改账号")
        dialog.resize(520, 170)
        v = QVBoxLayout(dialog)
        form = QFormLayout()
        name_edit = QLineEdit(account["name"])
        name_edit.setPlaceholderText("留空则显示链接")
        url_edit = QLineEdit(account["modules"].get("info", account["modules"].get(
            "tracker", account["modules"].get("music", {}))).get("url", account["url"]))
        url_edit.setPlaceholderText("https://…（修改链接即更换账号指向）")
        form.addRow("账号名", name_edit)
        form.addRow("链接", url_edit)
        v.addLayout(form)
        bar = QHBoxLayout()
        bar.addStretch(1)
        ok_btn = QPushButton("保存")
        ok_btn.setProperty("variant", "primary")
        cancel_btn = QPushButton("取消")
        bar.addWidget(ok_btn)
        bar.addWidget(cancel_btn)
        v.addLayout(bar)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ok, message = registry.update_account(
            account_id, name=name_edit.text().strip(), url=url_edit.text().strip())
        self._toast(message, "success" if ok else "warning")
        if ok:
            self.reload()
            self.changed.emit()

    def _delete_account(self, account_id):
        account = registry.get_account(account_id)
        if account is None:
            return
        label = account["name"] or account["url"][:48]
        used = "、".join(registry.MODULE_NAMES[m] for m in registry.MODULES
                        if m in account["modules"]) or "（未启用任何功能）"
        answer = QMessageBox.question(
            self, "删除账号",
            f"确定删除「{label}」？\n\n该账号将从这些功能中移除：{used}\n已采集的历史数据不会删除。",
            QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if registry.remove_account(account_id):
            self._toast("已删除该账号", "info")
            self.reload()
            self.changed.emit()

    def _move_account(self, account_id, delta):
        if registry.move_account(account_id, delta):
            self.reload()
            self.changed.emit()

    # ---------- 分组 ----------

    def _add_group(self):
        ok, message = registry.add_group(self.gid_edit.text(), self.gname_edit.text())
        self._toast(message, "success" if ok else "warning")
        if ok:
            self.gid_edit.clear()
            self.gname_edit.clear()
            self.reload()
            self.changed.emit()

    def _delete_group(self, gid):
        name = self._groups.get(gid, "")
        answer = QMessageBox.question(
            self, "删除分组",
            f"删除分组「{gid} {name}」？\n\n三个功能中绑定该组的账号会解绑（账号本身保留）。",
            QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        registry.delete_group(gid)
        self._toast(f"已删除分组 {gid}", "info")
        self.reload()
        self.changed.emit()

    def _move_group(self, gid, delta):
        if registry.move_group(gid, delta):
            self.reload()
            self.changed.emit()

    # ---------- 搜索（音乐平台） ----------

    def _open_search(self):
        _AccountSearchDialog(self).exec()

    def apply_search_result(self, platform: str, name: str, url: str):
        suffix = registry.platform_name(platform)
        self.name_edit.setText(f"{name}{suffix}" if name else "")
        self.url_edit.setText(url)


class _AccountSearchDialog(QDialog):
    """按名称搜索音乐平台账号/歌手，选中后回填到添加表单。"""

    def __init__(self, center: AccountCenterWidget):
        super().__init__(center)
        self.center = center
        self.setWindowTitle("搜索账号")
        self.resize(620, 420)
        self._results: list[dict] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        row = QHBoxLayout()
        self.platform_combo = QComboBox()
        for label, key in _SEARCH_PLATFORMS:
            self.platform_combo.addItem(label, key)
        self.platform_combo.setFixedWidth(100)
        row.addWidget(self.platform_combo)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("如 封茗囧菌（回车搜索）")
        self.keyword_edit.returnPressed.connect(self._search)
        row.addWidget(self.keyword_edit, 1)
        search_btn = QPushButton("搜索")
        search_btn.setProperty("variant", "primary")
        search_btn.clicked.connect(self._search)
        row.addWidget(search_btn)
        v.addLayout(row)

        self.hint = QLabel("输入名称后点「搜索」；5sing 暂无搜索通道，请直接粘贴主页链接。")
        self.hint.setProperty("role", "caption")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "类型", "粉丝数", "链接"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._fill())
        v.addWidget(self.table, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        fill_btn = QPushButton("填入")
        fill_btn.setProperty("variant", "primary")
        fill_btn.clicked.connect(self._fill)
        bar.addWidget(fill_btn)
        v.addLayout(bar)

    def _search(self):
        platform = self.platform_combo.currentData() or ""
        keyword = self.keyword_edit.text().strip()
        if not keyword:
            self.hint.setText("请输入要搜索的名称")
            return
        self.hint.setText(f"正在搜索「{keyword}」…")

        def work():
            from music_account.collectors import search_account
            return search_account(platform, keyword)

        def done(results, err):
            if err:
                self.hint.setText(f"搜索失败：{err}")
                return
            self._results = list(results or [])
            if not self._results:
                self.hint.setText(f"「{keyword}」没有搜到结果")
                self._fill_table()
                return
            self.hint.setText(f"共 {len(self._results)} 条结果（双击或选中后点「填入」）")
            self._fill_table()

        run_bg(work, done)

    def _fill_table(self):
        self.table.setRowCount(len(self._results))
        kind_names = {"user": "用户", "artist": "歌手", "singer": "歌手"}
        for i, item in enumerate(self._results):
            fans = item.get("fans")
            fans_text = f"{fans:,}" if isinstance(fans, int) else ""
            values = [item.get("name") or "", kind_names.get(item.get("kind"), item.get("kind") or ""),
                      fans_text, item.get("url") or ""]
            for column, value in enumerate(values):
                self.table.setItem(i, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _fill(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            self.hint.setText("请先选中一条结果")
            return
        item = self._results[min(rows)]
        self.center.apply_search_result(
            self.platform_combo.currentData() or "",
            str(item.get("name") or "").strip(),
            str(item.get("url") or "").strip())
        self.accept()