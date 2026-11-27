"""本地资料站：同步三个仓库，按需运行微信小程序开发工程或 Wiki 网站。

页面提供仓库同步与本地 Wiki 网站启停：
- 仓库可单独或全部更新；小程序在微信开发者工具打开，Wiki 由网页与 API 组成
- Wiki 网站由前端和 API 组成，启动时一起运行
- 一键 Wiki 部署更新全部三个仓库 → 安装依赖 → 重建并构建 Wiki → 启动 Wiki 网站
- 启动后自动打开系统浏览器，也可随时点「打开网站」
- 数据复用「腾讯文档监控」已同步的 tables.db，不重复联网拉取
- 站点访问监控：查询 mandisa.wiki 后台统计（内部 analytics API），按日期落本地库并支持每日定时
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from gui_qt import table_sort, theme
from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from toolbox.tcdoc import config as tcdoc_cfg
from toolbox.webwiki import (
    analytics, config as wiki_cfg, credentials as wiki_creds, deploy, gitops,
    localimages, projects, server,
)


def _signed(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"+{number}" if number > 0 else str(number)


class _DeployCancelled(Exception):
    """用户在部署过程中点了「取消」：停在当前步骤，已完成的部分保留。"""

    def __init__(self, done: int, total: int):
        super().__init__(f"已取消（完成 {done}/{total} 步）")
        self.done = done
        self.total = total


class LocalWikiPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.service = server.SiteService()
        self._busy = False
        self._deploy_cancel = False
        self._task_buttons: list[QPushButton] = []
        self._build_ui()
        self._refresh_status()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(5000)

    # ==================== UI ====================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(scroll)
        body = QWidget()
        body.setObjectName("wikiBody")
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        v.addWidget(self._build_hero())
        v.addWidget(self._build_status_card())
        v.addWidget(self._build_actions_card())
        v.addWidget(self._build_analytics_card())
        v.addWidget(self._build_docs_card())
        v.addWidget(self._build_path_card())
        v.addWidget(self._build_log_card())
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("wikiMuted")
        v.addWidget(self.status_label)
        v.addStretch(1)
        self._apply_theme_styles()

    def _card(self, title: str, subtitle: str = ""):
        card = QFrame()
        card.setObjectName("wikiCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 15, 18, 15)
        box.setSpacing(8)
        head = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("wikiCardTitle")
        head.addWidget(label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("wikiMuted")
            head.addWidget(sub)
        head.addStretch(1)
        box.addLayout(head)
        return card, box

    def _build_hero(self):
        card = QFrame()
        card.setObjectName("wikiHero")
        h = QHBoxLayout(card)
        h.setContentsMargins(0, 0, 0, 2)
        left = QVBoxLayout()
        left.setSpacing(5)
        note = QLabel("私有项目：拉取代码与一键部署需要有效的 GitHub 登录状态；"
                      "仓库本身请保持在 GitHub 私有。")
        note.setObjectName("wikiWarn")
        note.setWordWrap(True)
        left.addWidget(note)
        h.addLayout(left, 1)
        return card

    def _build_status_card(self):
        card, box = self._card("状态", "登录 / 代码 / 数据 / 服务")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.github_status = QLabel("—")
        self.repo_status = QLabel("—")
        self.data_status = QLabel("—")
        self.service_status = QLabel("—")
        for row, (caption, widget) in enumerate((
                ("GitHub", self.github_status),
                ("代码", self.repo_status),
                ("腾讯文档", self.data_status),
                ("本地服务", self.service_status))):
            cap = QLabel(caption)
            cap.setObjectName("wikiMuted")
            cap.setFixedWidth(64)
            grid.addWidget(cap, row, 0, Qt.AlignTop)
            widget.setWordWrap(True)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch(1)
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.setObjectName("wikiButton")
        self.refresh_btn.clicked.connect(self._refresh_status)
        row.addWidget(self.refresh_btn)
        box.addLayout(row)
        return card

    def _build_actions_card(self):
        card, box = self._card(
            "项目同步与网站启动",
            "仓库更新与项目启动分开；启动可选仅小程序、仅 Wiki 或两者")
        self.deploy_btn = QPushButton("一键更新全部并部署 Wiki 网站")
        self.deploy_btn.setObjectName("wikiPrimary")
        self.deploy_btn.setMinimumHeight(40)
        self.deploy_btn.setToolTip(
            "更新微信小程序主程序、Wiki API 和 Wiki 网站前端 → 安装依赖 → "
            "重建站点数据 → 构建前端 → 启动 Wiki 网站（网页 + API）；"
            "不会打开微信开发者工具或发布小程序")
        self.deploy_btn.clicked.connect(self._deploy)
        box.addWidget(self.deploy_btn)
        self._task_buttons.append(self.deploy_btn)

        scope_help = QLabel(
            "一键部署更新全部 3 个仓库并部署 Wiki 网站；不会打开微信开发者工具或发布小程序。")
        scope_help.setObjectName("wikiMuted")
        scope_help.setWordWrap(True)
        box.addWidget(scope_help)

        repo_row = QHBoxLayout()
        repo_row.setSpacing(8)
        repo_row.addWidget(QLabel("更新范围"))
        self.repo_scope = QComboBox()
        self.repo_scope.setMinimumWidth(160)
        self.repo_scope.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.repo_scope.setMinimumContentsLength(8)
        self.repo_scope.addItem("全部仓库（3）", "all")
        for option in wiki_cfg.REPO_OPTIONS:
            label = f"{option['label']}（{option['name']}）"
            self.repo_scope.addItem(label, option["name"])
        self.repo_scope.setToolTip(
            "单独更新只同步所选代码，不安装依赖、不重建数据，也不启动服务")
        repo_row.addWidget(self.repo_scope)
        self.pull_btn = QPushButton("更新所选仓库")
        self.pull_btn.setObjectName("wikiButton")
        self.pull_btn.setMinimumHeight(34)
        self.pull_btn.clicked.connect(self._pull)
        repo_row.addWidget(self.pull_btn)
        repo_row.addStretch(1)
        box.addLayout(repo_row)
        self._task_buttons.append(self.pull_btn)

        start_row = QHBoxLayout()
        start_row.setSpacing(8)
        start_row.addWidget(QLabel("启动范围"))
        self.start_scope = QComboBox()
        self.start_scope.setMinimumWidth(220)
        self.start_scope.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.start_scope.setMinimumContentsLength(8)
        for option in projects.PROJECT_OPTIONS:
            self.start_scope.addItem(option["label"], option["value"])
            self.start_scope.setItemData(
                self.start_scope.count() - 1, option["description"], Qt.ToolTipRole)
        self.start_scope.setToolTip(
            "选择只打开小程序、只启动 Wiki 网站，或两项都运行")
        start_row.addWidget(self.start_scope)
        self.start_selected_btn = QPushButton("启动所选项目")
        self.start_selected_btn.setObjectName("wikiButton")
        self.start_selected_btn.setMinimumHeight(34)
        self.start_selected_btn.clicked.connect(self._start_selected_projects)
        start_row.addWidget(self.start_selected_btn)
        start_row.addStretch(1)
        box.addLayout(start_row)
        start_note = QLabel(
            "仅小程序：在本机开发者工具打开；仅 Wiki：启动网页和 API；"
            "全部：执行以上两项。这里不会更新仓库，也不会上传或发布小程序。")
        start_note.setObjectName("wikiMuted")
        start_note.setWordWrap(True)
        box.addWidget(start_note)
        self._task_buttons.append(self.start_selected_btn)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.deploy_progress = QProgressBar()
        self.deploy_progress.setObjectName("wikiProgress")
        self.deploy_progress.setRange(0, 100)
        self.deploy_progress.setValue(0)
        self.deploy_progress.setFormat("准备中…")
        self.deploy_progress.setFixedHeight(16)
        self.deploy_progress.setVisible(False)
        progress_row.addWidget(self.deploy_progress, 1)
        self.deploy_cancel_btn = QPushButton("取消")
        self.deploy_cancel_btn.setObjectName("wikiButton")
        self.deploy_cancel_btn.setFixedWidth(64)
        self.deploy_cancel_btn.setVisible(False)
        self.deploy_cancel_btn.clicked.connect(self._cancel_deploy)
        progress_row.addWidget(self.deploy_cancel_btn)
        box.addLayout(progress_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        buttons = [
            ("安装依赖", self._install_deps),
            ("重建站点数据", self._sync_docs),
            ("构建 Wiki 前端", self._build_frontend),
            ("打开 Wiki 网站", self._open_site),
            ("停止 Wiki 网站 + API", self._stop_service),
            ("打开目录", self._open_dir),
        ]
        self.deps_btn = None
        self.sync_btn = None
        self.build_btn = None
        self.open_btn = None
        self.stop_btn = None
        self.dir_btn = None
        for index, (text, slot) in enumerate(buttons):
            btn = QPushButton(text)
            btn.setObjectName("wikiButton")
            btn.setMinimumHeight(34)
            btn.clicked.connect(slot)
            grid.addWidget(btn, index // 3, index % 3)
            self._task_buttons.append(btn)
            key = ("deps", "sync", "build", "open",
                   "stop", "dir")[index]
            setattr(self, f"{key}_btn", btn)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        box.addLayout(grid)

        opts = QHBoxLayout()
        self.auto_open = QCheckBox("启动后自动打开浏览器")
        self.auto_open.setChecked(wiki_cfg.auto_open_browser())
        self.auto_open.toggled.connect(wiki_cfg.set_auto_open_browser)
        opts.addWidget(self.auto_open)
        opts.addStretch(1)
        box.addLayout(opts)
        return card

    # ---------- 站点访问监控（mandisa.wiki 内部 analytics API） ----------

    def _build_analytics_card(self):
        card, box = self._card("站点访问监控", "mandisa.wiki 后台访问统计")
        self._analytics_has_password = analytics.has_password()

        row = QHBoxLayout()
        row.setSpacing(6)
        self.analytics_pwd = QLineEdit()
        self.analytics_pwd.setEchoMode(QLineEdit.Password)
        self.analytics_pwd.setPlaceholderText("后台密码（与 /admin 登录同一个）")
        self.analytics_pwd.setToolTip(
            "只加密保存在本机 config/webwiki_analytics.json。\n"
            "密码连续错 5 次会锁定 15 分钟，不确定时先到 /admin 试一次。")
        self.analytics_pwd.returnPressed.connect(self._save_analytics_password)
        row.addWidget(self.analytics_pwd, 1)
        save_btn = QPushButton("保存密码")
        save_btn.setObjectName("wikiButton")
        save_btn.setToolTip("保存后只做掩码显示，界面不会回显密码")
        save_btn.clicked.connect(self._save_analytics_password)
        row.addWidget(save_btn)
        box.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        scope_cap = QLabel("口径")
        scope_cap.setObjectName("wikiMuted")
        row2.addWidget(scope_cap)
        self.analytics_scope = QComboBox()
        for key, label in analytics.SCOPES:
            self.analytics_scope.addItem(label, key)
        self.analytics_scope.setCurrentIndex(
            max(0, self.analytics_scope.findData(wiki_cfg.analytics_scope())))
        self.analytics_scope.setToolTip(
            "严格验证：只算通过 Turnstile 核验的访问（默认）\n"
            "严格验证 + 行为合格：加上有可信交互的候选\n"
            "全部分类：含待验证与疑似自动化")
        self.analytics_scope.currentIndexChanged.connect(self._save_analytics_scope)
        row2.addWidget(self.analytics_scope)
        row2.addStretch(1)
        self.analytics_collect_btn = QPushButton("采集今日")
        self.analytics_collect_btn.setObjectName("wikiButton")
        self.analytics_collect_btn.setToolTip("向站点后台查询今日统计并写入本地库（按日期+口径去重）")
        self.analytics_collect_btn.clicked.connect(self._collect_analytics)
        row2.addWidget(self.analytics_collect_btn)
        admin_btn = QPushButton("打开后台")
        admin_btn.setObjectName("wikiButton")
        admin_btn.setToolTip("在浏览器打开 mandisa.wiki/admin")
        admin_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(analytics.ADMIN_URL)))
        row2.addWidget(admin_btn)
        dir_btn = QPushButton("数据目录")
        dir_btn.setObjectName("wikiButton")
        dir_btn.setToolTip("打开 download/站点监控数据（访问统计.db + 采集日志）")
        dir_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(analytics.data_dir()))))
        row2.addWidget(dir_btn)
        box.addLayout(row2)

        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        self.analytics_values: dict[str, QLabel] = {}
        for key, caption in (("visitors", "访客"), ("sessions", "打开次数"),
                             ("pageviews", "浏览量"), ("online", "实时在线")):
            tile = QFrame()
            tile.setObjectName("wikiStat")
            tile_box = QVBoxLayout(tile)
            tile_box.setContentsMargins(12, 10, 12, 10)
            tile_box.setSpacing(2)
            value = QLabel("—")
            value.setObjectName("wikiStatValue")
            cap = QLabel(caption)
            cap.setObjectName("wikiStatCap")
            tile_box.addWidget(value)
            tile_box.addWidget(cap)
            tiles.addWidget(tile, 1)
            self.analytics_values[key] = value
        box.addLayout(tiles)

        self.analytics_note = QLabel("尚未采集：保存密码后点「采集今日」。")
        self.analytics_note.setObjectName("wikiMuted")
        self.analytics_note.setWordWrap(True)
        box.addWidget(self.analytics_note)

        self.analytics_table = QTableWidget(0, 5)
        self.analytics_table.setObjectName("wikiTrend")
        self.analytics_table.setHorizontalHeaderLabels(
            ["日期", "口径", "访客", "打开", "浏览量"])
        self.analytics_table.verticalHeader().setVisible(False)
        self.analytics_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.analytics_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.analytics_table.setAlternatingRowColors(True)
        self.analytics_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.analytics_table.setMinimumHeight(150)
        self.analytics_table.setToolTip("本地历史（最多 14 条）：每天一条，按日期+口径去重")
        self._analytics_sorter = table_sort.install(
            self.analytics_table, skip_columns=())
        box.addWidget(self.analytics_table)

        sched_hint = QLabel("每日定时采集已统一到「任务」页面，可在那里修改计划与安装 / 删除任务。")
        sched_hint.setObjectName("wikiMuted")
        sched_hint.setWordWrap(True)
        box.addWidget(sched_hint)
        return card

    def _build_docs_card(self):
        card, box = self._card("腾讯文档预览", "站点数据与源文档同源")
        row = QHBoxLayout()
        self.doc_combo = QComboBox()
        self.doc_combo.setMinimumWidth(260)
        for doc in tcdoc_cfg.load_docs():
            self.doc_combo.addItem(doc.get("name") or doc.get("key"), doc.get("url") or "")
        row.addWidget(self.doc_combo, 1)
        preview = QPushButton("打开预览")
        preview.setObjectName("wikiButton")
        preview.clicked.connect(self._open_doc_preview)
        row.addWidget(preview)
        monitor = QPushButton("腾讯文档页")
        monitor.setObjectName("wikiButton")
        monitor.clicked.connect(lambda: self.app.show_page("tcdoc"))
        row.addWidget(monitor)
        box.addLayout(row)
        hint = QLabel(
            "预览用系统默认浏览器打开源文档；本站数据直接读取「腾讯文档」已同步的本地表格，"
            "点「重建站点数据」即可，不需要重新联网拉取。")
        hint.setObjectName("wikiMuted")
        hint.setWordWrap(True)
        box.addWidget(hint)
        return card

    def _build_path_card(self):
        card, box = self._card("目录与端口", "代码、数据库和构建产物都在这里")
        row = QHBoxLayout()
        self.path_label = QLabel(str(wiki_cfg.root_dir()))
        self.path_label.setObjectName("wikiMuted")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        row.addWidget(self.path_label, 1)
        change = QPushButton("更改目录")
        change.setObjectName("wikiButton")
        change.clicked.connect(self._change_dir)
        row.addWidget(change)
        box.addLayout(row)

        ports = QHBoxLayout()
        ports.addWidget(QLabel("API 端口"))
        self.api_spin = QSpinBox()
        self.api_spin.setRange(1024, 65535)
        self.api_spin.setValue(wiki_cfg.ports()[0])
        self.api_spin.editingFinished.connect(self._save_ports)
        ports.addWidget(self.api_spin)
        ports.addSpacing(12)
        ports.addWidget(QLabel("站点端口"))
        self.site_spin = QSpinBox()
        self.site_spin.setRange(1024, 65535)
        self.site_spin.setValue(wiki_cfg.ports()[1])
        self.site_spin.editingFinished.connect(self._save_ports)
        ports.addWidget(self.site_spin)
        ports.addStretch(1)
        self.url_label = QLabel("")
        self.url_label.setObjectName("wikiMuted")
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ports.addWidget(self.url_label)
        box.addLayout(ports)
        return card

    def _build_log_card(self):
        card, box = self._card("运行日志", "拉取、安装、重建与启动输出")
        bar = QHBoxLayout()
        bar.addStretch(1)
        clear = QPushButton("清空日志")
        clear.setObjectName("wikiButton")
        clear.clicked.connect(self._clear_log)
        bar.addWidget(clear)
        box.addLayout(bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("wikiLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setMinimumHeight(190)
        box.addWidget(self.log_view)
        return card

    # ==================== 主题 / 状态 ====================

    def _apply_theme_styles(self):
        self.setStyleSheet("""
            QWidget#wikiBody { background: palette(window); }
            QWidget#wikiHero { background: transparent; }
            QFrame#wikiCard {
                background: palette(base); border: 1px solid palette(shadow);
                border-radius: 12px;
            }
            QLabel#wikiTitle { color: palette(text); background: transparent;
                font-size: 26px; font-weight: bold; }
            QLabel#wikiMuted { color: palette(placeholder-text); background: transparent;
                font-size: 11px; }
            QLabel#wikiWarn { color: palette(link); background: transparent;
                font-size: 11px; }
            QLabel#wikiCardTitle { color: palette(text); background: transparent;
                font-size: 14px; font-weight: bold; }
            QPushButton#wikiButton {
                background: palette(base); color: palette(text);
                border: 1px solid palette(shadow); border-radius: 8px;
                padding: 6px 10px; font-size: 11px;
            }
            QPushButton#wikiButton:hover {
                background: palette(alternate-base); color: palette(link);
                border-color: palette(highlight);
            }
            QPushButton#wikiButton:disabled {
                background: palette(alternate-base); color: palette(placeholder-text);
                border-color: palette(shadow);
            }
            QPushButton#wikiPrimary {
                background: palette(highlight); color: palette(highlighted-text);
                border: none; border-radius: 9px; font-size: 13px; font-weight: bold;
            }
            QPushButton#wikiPrimary:hover { background: palette(link); }
            QPushButton#wikiPrimary:disabled {
                background: palette(midlight); color: palette(placeholder-text);
            }
            QProgressBar#wikiProgress {
                background: palette(alternate-base); color: palette(text);
                border: 1px solid palette(shadow); border-radius: 8px;
                font-size: 10px; text-align: center;
            }
            QProgressBar#wikiProgress::chunk {
                background: palette(highlight); border-radius: 7px;
            }
            QPlainTextEdit#wikiLog {
                background: palette(alternate-base); color: palette(text);
                border: 1px solid palette(shadow); border-radius: 8px;
                font-family: Consolas, "Microsoft YaHei"; font-size: 11px;
            }
            QFrame#wikiStat {
                background: palette(alternate-base); border: 1px solid palette(shadow);
                border-radius: 10px;
            }
            QLabel#wikiStatValue {
                color: palette(window-text); background: transparent;
                font-size: 22px; font-weight: bold;
                font-family: "Segoe UI", "Microsoft YaHei";
            }
            QLabel#wikiStatCap {
                color: palette(placeholder-text); background: transparent; font-size: 11px;
            }
            QTableWidget#wikiTrend { font-size: 11px; }
        """)

    def on_show(self):
        self._timer.start(5000)
        self._reload_docs()
        self._refresh_status()
        self._refresh_analytics()

    def on_hide(self):
        self._timer.stop()

    def has_critical_services(self):
        return self.service.running

    def release_view_resources(self):
        # 服务不依赖管理页日志；隐藏期间只保留有限文本，不继续轮询站点。
        self._timer.stop()
        self.log_view.clear()

    def _reload_docs(self):
        current = self.doc_combo.currentData()
        self.doc_combo.blockSignals(True)
        try:
            self.doc_combo.clear()
            for doc in tcdoc_cfg.load_docs():
                self.doc_combo.addItem(doc.get("name") or doc.get("key"),
                                       doc.get("url") or "")
            index = self.doc_combo.findData(current) if current else -1
            if index >= 0:
                self.doc_combo.setCurrentIndex(index)
        finally:
            self.doc_combo.blockSignals(False)

    def shutdown(self):
        try:
            self.service.stop(log=lambda _msg: None)
        except Exception:  # noqa: BLE001
            pass

    def _tick(self):
        if not self._busy:
            self._update_service_label()
            self._refresh_actions()

    def _refresh_status(self):
        if self._busy:
            return
        data = wiki_creds.load()
        if data.get("token"):
            login = data.get("login") or "已登录"
            ok_color = theme.color("success")
            self.github_status.setText(
                f"<span style='color:{ok_color}'>● 已登录</span> @{login}")
        else:
            warn_color = theme.color("danger")
            self.github_status.setText(
                f"<span style='color:{warn_color}'>● 未登录</span> 拉取与一键部署已锁定")

        lines = []
        for name in ("mandisa-miniprogram", "webwiki-api", "webwiki"):
            label = wiki_cfg.REPO_LABELS.get(name, name)
            try:
                state = gitops.repo_state(name)
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{label}：{exc}")
                continue
            if state.get("exists"):
                extra = "（有本地改动）" if state.get("dirty") else ""
                lines.append(f"{label}@{state.get('commit') or '?'}{extra}")
            else:
                lines.append(f"{label}：未拉取")
        self.repo_status.setText("　".join(lines))

        status = deploy.data_status()
        try:
            local_images = localimages.available_count()
        except Exception:  # noqa: BLE001
            local_images = 0
        if status.get("exists"):
            text = (f"已重建 {status.get('mtime')} · 歌曲 {status.get('songs', 0)}"
                    f" / 活动 {status.get('events', 0)} / 周边 {status.get('goods', 0)}"
                    f" / 动态 {status.get('posts', 0)}")
            if local_images:
                text += f" · 本地图片 {local_images} 张优先"
            self.data_status.setText(text)
        else:
            self.data_status.setText(
                "尚未重建（点「重建站点数据」或「一键更新全部并部署 Wiki 网站」）")
        self.path_label.setText(str(wiki_cfg.root_dir()))
        self._update_service_label()
        self._refresh_actions()

    def _update_service_label(self):
        if self.service.running:
            color = theme.color("success")
            self.service_status.setText(
                f"<span style='color:{color}'>● 运行中</span> "
                f"<a href='{self.service.url}'>{self.service.url}</a>")
            self.service_status.setOpenExternalLinks(True)
            self.url_label.setText(self.service.url)
        else:
            self.service_status.setText("● 未启动")
            self.url_label.setText("")

    def _refresh_actions(self):
        auth_ok = bool(wiki_creds.token())
        api_ok = (wiki_cfg.api_dir() / ".git").is_dir()
        web_ok = (wiki_cfg.web_dir() / ".git").is_dir()
        venv_ok = wiki_cfg.venv_python().is_file()
        dist_ok = (wiki_cfg.dist_dir() / "index.html").is_file()
        node_ok = (wiki_cfg.web_dir() / "node_modules").is_dir()
        running = self.service.running
        idle = not self._busy

        self.pull_btn.setEnabled(idle and auth_ok)
        self.repo_scope.setEnabled(idle and auth_ok)
        self.deps_btn.setEnabled(idle and api_ok and web_ok)
        self.sync_btn.setEnabled(idle and api_ok and venv_ok)
        self.build_btn.setEnabled(idle and web_ok and node_ok)
        self.start_selected_btn.setEnabled(idle)
        self.open_btn.setEnabled(idle and running)
        self.stop_btn.setEnabled(idle and running)
        self.dir_btn.setEnabled(idle)
        self.deploy_btn.setEnabled(idle and auth_ok)
        # 站点统计控件独立于部署状态：只要有密码就能手动采集
        self.analytics_collect_btn.setEnabled(idle and self._analytics_has_password)
        self.analytics_pwd.setEnabled(idle)
        self.analytics_scope.setEnabled(idle)

    # ==================== 日志 / 任务 ====================

    def _log(self, text):
        if text is None:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(text).splitlines():
            if line.strip():
                self.log_view.appendPlainText(f"[{stamp}] {line}")
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_log(self):
        self.log_view.clear()

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        if text:
            self.status_label.setText(text)
        self._refresh_actions()

    def _start_task(self, title: str, fn, on_ok=None, on_error=None):
        if self._busy:
            return
        self._set_busy(True, f"{title}…")
        self._log(f"===== {title} =====")

        def work():
            return fn(lambda msg: post_to_main(self._log, msg))

        def done(result, err):
            self._set_busy(False)
            if err:
                self._log(f"{title}：{err}")
                handled = False
                if on_error is not None:
                    try:
                        handled = bool(on_error(err))
                    except Exception as exc:  # noqa: BLE001 - 回调出错不能吞掉原错误
                        self._log(f"失败处理出错：{exc}")
                if not handled:
                    self.status_label.setText(f"{title}失败")
                    QMessageBox.warning(self, title, str(err))
                    try:
                        self.app.toast(f"{title}失败：{err}"[:120], "error")
                    except Exception:  # noqa: BLE001
                        pass
            else:
                message = f"{title}完成" + (f"：{result}" if result else "")
                self._log(message)
                self.status_label.setText(message)
                try:
                    self.app.toast(message[:120], "success")
                except Exception:  # noqa: BLE001
                    pass
                if on_ok:
                    try:
                        on_ok(result)
                    except Exception as exc:  # noqa: BLE001
                        self._log(f"后处理失败：{exc}")
            self._refresh_status()

        run_bg(work, done)

    # ==================== 登录状态 ====================

    def _require_auth(self) -> bool:
        if wiki_creds.token():
            return True
        QMessageBox.information(
            self, "需要登录 GitHub",
            "这是私有项目：拉取代码与一键部署前需要有效的 GitHub 登录状态。")
        return False

    # ==================== 各按钮 ====================

    def _pull(self):
        if not self._require_auth():
            return
        token = wiki_creds.token()
        root = wiki_cfg.root_dir()
        repositories = self.repo_scope.currentData()
        self._start_task(
            "更新仓库",
            lambda log: gitops.pull_repositories(repositories, token, log, root))

    def _install_deps(self):
        root = wiki_cfg.root_dir()

        def work(log):
            if not (wiki_cfg.api_dir(root) / ".git").is_dir():
                raise RuntimeError("请先拉取代码")
            deploy.ensure_python_env(root, log)
            deploy.ensure_node_deps(root, log, force=True)
            return "Python / Node 依赖已就绪"

        self._start_task("安装依赖", work)

    def _sync_docs(self):
        root = wiki_cfg.root_dir()
        self._start_task("重建站点数据",
                         lambda log: deploy.sync_docs(root, log))

    def _build_frontend(self):
        root = wiki_cfg.root_dir()
        self._start_task("构建前端", lambda log: str(deploy.build_frontend(root, log)))

    def _start_service(self):
        root = wiki_cfg.root_dir()
        if self.service.root != root:
            self.service = server.SiteService(root)

        def work(log):
            try:
                deploy.warm_covers(root, log)
            except Exception as exc:  # noqa: BLE001 - 预热失败不影响启动
                log(f"封面预热跳过：{exc}")
            return self.service.start(log)

        def after(url):
            if self.auto_open.isChecked():
                server.open_site(url)
            self._update_service_label()
            self._refresh_actions()

        self._start_task("启动 Wiki 网站 + API", work, on_ok=after)

    def _start_selected_projects(self):
        scope = self.start_scope.currentData()
        start_miniapp = scope in ("miniapp", "all")
        start_wiki = scope in ("wiki", "all")
        root = wiki_cfg.root_dir()
        if start_wiki and self.service.root != root:
            self.service = server.SiteService(root)

        def work(log):
            return projects.start_selected(scope, root, log, service=self.service)

        def after(_result):
            if start_wiki and self.auto_open.isChecked():
                server.open_site(self.service.url)
            self._update_service_label()
            self._refresh_actions()

        def failed(err):
            if (start_wiki and self.auto_open.isChecked()
                    and isinstance(err, projects.ProjectStartError)
                    and "Wiki 网站（网页 + API）" in err.completed
                    and self.service.running):
                server.open_site(self.service.url)
            return False

        title = "启动所选项目" if scope != "all" else "启动全部项目"
        self._start_task(title, work, on_ok=after, on_error=failed)

    def _open_site(self):
        if not self.service.running:
            QMessageBox.information(
                self, "打开 Wiki 网站",
                "Wiki 网站尚未启动，请先点「启动 Wiki 网站 + API」。")
            return
        server.open_site(self.service.url)

    def _stop_service(self):
        def work(log):
            self.service.stop(log)
            return "服务已停止"

        self._start_task("停止 Wiki 网站 + API", work)

    DEPLOY_RETRIES = 1  # 首次失败后自动重新部署的次数

    def _deploy(self):
        if self._busy or not self._require_auth():
            return
        root = wiki_cfg.root_dir()
        token = wiki_creds.token()
        self._deploy_cancel = False
        self._deploy_progress_show()

        def after(url):
            self._deploy_progress_finish("部署完成")
            if self.auto_open.isChecked():
                server.open_site(url)
            self._update_service_label()
            self._refresh_actions()

            self._start_task("一键更新全部并部署 Wiki 网站",
                         lambda log: self._deploy_with_retry(root, token, log),
                         on_ok=after, on_error=self._deploy_failed)

    def _deploy_with_retry(self, root, token, log):
        """整链部署；任何一步失败就从头重新部署，全部尝试都失败才上报。"""
        attempts = self.DEPLOY_RETRIES + 1
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                log(f"上一步部署中断，自动重新部署（第 {attempt - 1}/{self.DEPLOY_RETRIES} 次）")
                post_to_main(self._deploy_progress_restart, attempt)
            try:
                return self._deploy_once(root, token, log)
            except _DeployCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - 失败后整链重来
                last = exc
                log(f"部署中断：{exc}")
        raise RuntimeError(
            f"部署失败：已自动重新部署 {self.DEPLOY_RETRIES} 次仍未成功（{last}）。"
            f"请查看下方日志里中断的那一步再重试。")

    def _deploy_once(self, root, token, log):
        """更新全部仓库 → 安装依赖 → 重建数据 → 构建 Wiki → 启动网页 + API。"""
        if self.service.running:
            log("检测到服务正在运行，先停止旧服务…")
            self.service.stop(log)
        self.service = server.SiteService(root)
        steps = (
            ("更新全部仓库", lambda: self._deploy_pull(root, token, log)),
            ("安装依赖", lambda: (deploy.ensure_python_env(root, log),
                                 deploy.ensure_node_deps(root, log))),
            ("重建站点数据", lambda: deploy.sync_docs(root, log)),
            ("构建前端", lambda: deploy.build_frontend(root, log)),
        )
        total = len(steps) + 1
        for index, (label, run) in enumerate(steps, start=1):
            if self._deploy_cancel:
                raise _DeployCancelled(index - 1, total)
            post_to_main(self._deploy_progress_step, index, total, label)
            log(f"[{index}/{total}] {label}")
            run()
        if self._deploy_cancel:
            raise _DeployCancelled(total - 1, total)
        post_to_main(self._deploy_progress_step, total, total,
                     "启动 Wiki 网站 + API")
        log(f"[{total}/{total}] 启动 Wiki 网站 + API")
        return self._deploy_start(root, log)

    def _deploy_pull(self, root, token, log):
        gitops.pull_repositories("all", token, log, root)

    def _deploy_start(self, root, log):
        try:
            deploy.warm_covers(root, log)
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响部署
            log(f"封面预热跳过：{exc}")
        return self.service.start(log)

    # ==================== 部署进度 ====================

    def _deploy_progress_show(self):
        self.deploy_progress.setRange(0, 100)
        self.deploy_progress.setValue(0)
        self.deploy_progress.setFormat("准备中…")
        self.deploy_progress.setVisible(True)
        self.deploy_cancel_btn.setEnabled(True)
        self.deploy_cancel_btn.setVisible(True)

    def _deploy_progress_step(self, index: int, total: int, label: str):
        self.deploy_progress.setValue(int((index - 1) * 100 / total))
        self.deploy_progress.setFormat(f"{index}/{total} {label}")

    def _deploy_progress_restart(self, attempt: int):
        self.deploy_progress.setValue(0)
        self.deploy_progress.setFormat(f"重新部署 {attempt - 1}/{self.DEPLOY_RETRIES}…")

    def _deploy_progress_finish(self, text: str):
        if text == "部署完成":
            self.deploy_progress.setValue(100)
        self.deploy_progress.setFormat(text)
        self.deploy_cancel_btn.setVisible(False)
        QTimer.singleShot(3000, self._deploy_progress_hide)

    def _deploy_progress_hide(self):
        if self._busy:
            return
        self.deploy_progress.setVisible(False)
        self.deploy_cancel_btn.setVisible(False)

    def _cancel_deploy(self):
        if not self._busy:
            return
        self._deploy_cancel = True
        self.deploy_cancel_btn.setEnabled(False)
        self.status_label.setText("已请求取消，当前步骤结束后停止…")
        self._log("已请求取消部署：当前步骤结束后停止，已完成的部分保留。")

    def _deploy_failed(self, err) -> bool:
        """取消不弹窗；真正的失败交给 _start_task 弹窗并给下一步。"""
        if isinstance(err, _DeployCancelled):
            self.status_label.setText(f"部署已取消（完成 {err.done}/{err.total} 步）")
            self._deploy_progress_finish("已取消")
            return True
        self._deploy_progress_finish("部署失败")
        return False

    # ==================== 站点访问监控 ====================

    def _refresh_analytics(self):
        """读本地历史 + 定时任务状态（后台线程，不阻塞界面）。"""
        def work():
            return {
                "rows": analytics.load_history(limit=14),
                "password": analytics.has_password(),
            }

        def done(result, err):
            if err:
                self._log(f"站点统计读取失败：{err}")
                return
            self._apply_analytics(result or {})

        run_bg(work, done, owner=self)

    def _apply_analytics(self, state: dict):
        rows = state.get("rows") or []
        self._analytics_has_password = bool(state.get("password"))
        scope = self.analytics_scope.currentData() or "human"
        row = next((item for item in rows if item.get("scope") == scope), None)
        if row is None and rows:
            row = rows[0]

        totals = row or {}
        for key, label in self.analytics_values.items():
            label.setText(f"{int(totals.get(key) or 0):,}" if row else "—")

        self.analytics_note.setText(self._analytics_note_text(row, rows))
        self._fill_analytics_table(rows)

        self.analytics_pwd.setPlaceholderText(
            "已保存（要换密码直接输入新的再保存）" if self._analytics_has_password
            else "后台密码（与 /admin 登录同一个）")
        self.analytics_collect_btn.setEnabled(
            self._analytics_has_password and not self._busy)

    def _analytics_note_text(self, row: dict | None, rows: list[dict]) -> str:
        if not row:
            return ("尚未采集：保存密码后点「采集今日」，"
                    "或到「任务」页面安装每日定时。")
        prev, week = analytics.deltas(row, rows)
        fetched = str(row.get("fetched_at") or "-")
        generated = str(row.get("generated_at") or "-")
        parts = [f"口径 {analytics.scope_label(row.get('scope'))}",
                 f"采集 {fetched}", f"接口生成 {generated}"]
        if prev is not None:
            parts.append(f"较前一日 {_signed(prev)}")
        if week is not None:
            parts.append(f"较 7 日前 {_signed(week)}")
        note = " · ".join(parts)
        if prev is None and week is None:
            note += "（本地还没有前一日数据，定时采集几天后自动出现对比）"
        return note

    def _fill_analytics_table(self, rows: list[dict]):
        table = self.analytics_table
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(rows))
            for index, item in enumerate(rows):
                values = (str(item.get("stat_date") or "-"),
                          analytics.scope_label(item.get("scope")),
                          f"{int(item.get('visitors') or 0):,}",
                          f"{int(item.get('sessions') or 0):,}",
                          f"{int(item.get('pageviews') or 0):,}")
                for column, text in enumerate(values):
                    cell = QTableWidgetItem(text)
                    cell.setTextAlignment(Qt.AlignCenter)
                    cell.setToolTip(f"采集于 {item.get('fetched_at') or '-'}")
                    table.setItem(index, column, cell)
        finally:
            table.setUpdatesEnabled(True)
        self._analytics_sorter.apply()

    def _save_analytics_password(self):
        text = self.analytics_pwd.text().strip()
        if not text:
            QMessageBox.information(
                self, "保存后台密码",
                "请输入 mandisa.wiki 后台（/admin）当前使用的密码。\n"
                "后台改密后这里也要跟着改，旧密码会立即失效。")
            return
        try:
            encrypted = analytics.save_password(text)
        except OSError as exc:
            QMessageBox.warning(self, "保存后台密码", f"写入失败：{exc}")
            return
        self.analytics_pwd.clear()
        self._analytics_has_password = True
        self.analytics_pwd.setPlaceholderText("已保存（要换密码直接输入新的再保存）")
        if encrypted:
            self._log("站点后台密码已加密保存到 config/webwiki_analytics.json")
        else:
            self._log("站点后台密码已保存，但本机缺少 cryptography 库，当前是明文文件，"
                      "建议先安装依赖")
        self.status_label.setText("站点后台密码已保存")
        self._refresh_analytics()

    def _save_analytics_scope(self, _index=None):
        scope = self.analytics_scope.currentData() or "human"
        wiki_cfg.set_analytics_scope(scope)
        self._refresh_analytics()

    def _collect_analytics(self):
        typed = self.analytics_pwd.text().strip()
        if typed:
            # 刚填了密码就点采集：先自动保存，省一次「保存密码」
            try:
                analytics.save_password(typed)
            except OSError as exc:
                QMessageBox.warning(self, "保存后台密码", f"写入失败：{exc}")
                return
            self.analytics_pwd.clear()
            self._analytics_has_password = True
            self.analytics_pwd.setPlaceholderText("已保存（要换密码直接输入新的再保存）")
        if not self._analytics_has_password:
            QMessageBox.information(
                self, "采集站点统计",
                "还缺后台密码：先在上面填 mandisa.wiki 后台密码并保存。")
            return
        scope = self.analytics_scope.currentData() or "human"
        self._start_task("采集站点统计",
                         lambda log: analytics.collect(scope=scope, log=log),
                         on_ok=lambda _row: self._refresh_analytics())

    # ==================== 目录 / 端口 / 文档 ====================

    def _change_dir(self):
        if self.service.running:
            QMessageBox.information(self, "更换目录", "服务运行中，请先停止服务再更换目录。")
            return
        path = QFileDialog.getExistingDirectory(
            self, "选择资料站目录", str(wiki_cfg.root_dir()))
        if not path:
            return
        wiki_cfg.set_root_dir(path)
        self.service = server.SiteService(wiki_cfg.root_dir())
        self._log(f"资料站目录已切换到：{path}")
        self._refresh_status()

    def _open_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(wiki_cfg.root_dir())))

    def _save_ports(self):
        api, site = self.api_spin.value(), self.site_spin.value()
        if api == site:
            QMessageBox.warning(self, "端口重复", "API 端口与站点端口不能相同。")
            self.api_spin.setValue(wiki_cfg.ports()[0])
            self.site_spin.setValue(wiki_cfg.ports()[1])
            return
        if self.service.running:
            QMessageBox.information(self, "端口修改", "端口已保存，停止并重新启动服务后生效。")
        wiki_cfg.set_ports(api, site)
        if not self.service.running:
            self.service = server.SiteService(wiki_cfg.root_dir())
        self._update_service_label()

    def _open_doc_preview(self):
        url = self.doc_combo.currentData()
        if not url:
            QMessageBox.information(self, "腾讯文档预览", "文档清单为空，请先到腾讯文档页添加文档。")
            return
        QDesktopServices.openUrl(QUrl(str(url)))
