"""网络代理页：内置 mihomo 内核 + 用户自备提供商，只让本程序请求走代理。

- 内核与端口由本页管理，停止/退出后不留进程（Job Object 兜底）
- 提供商由用户自己提供：本地 YAML 或订阅链接，程序不内置节点
- 生效范围仅本程序（进程环境变量），不改 Windows 系统代理、不启用 TUN
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import ToggleSwitch
from gui_qt.toolbox_common import ElidedLabel, ToolSectionCard
from toolbox.proxy import core as proxy_core
from toolbox.proxy import environ, provider, settings


class ProxyPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._busy = False
        self._loading_nodes = False
        self._icons: list[tuple[object, str, str]] = []
        self._build_ui()
        self._refresh_all()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(5000)

    # ==================== 界面 ====================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(scroll)
        body = QWidget()
        body.setObjectName("proxyBody")
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        v.addWidget(self._build_core_card())
        v.addWidget(self._build_provider_card())
        v.addWidget(self._build_node_card())
        v.addWidget(self._build_program_card())
        v.addWidget(self._build_log_card())
        v.addStretch(1)

    def _card(self, title: str, description: str, icon: str) -> ToolSectionCard:
        card = ToolSectionCard(title, description, icon=icon)
        self._register_icon(card, icon, "title")
        return card

    def _build_core_card(self) -> ToolSectionCard:
        card = self._card(
            "代理内核",
            "程序自带 mihomo 内核的运行控制：内核只监听本机回环端口，"
            "停止后进程立即结束。",
            "chip")
        layout = card.content_layout

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.core_state = QLabel("未启动")
        self.core_state.setProperty("role", "subtitle")
        status_row.addWidget(self.core_state)
        status_row.addStretch(1)
        self.start_btn = QPushButton("启动内核")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.clicked.connect(lambda: self._start_core())
        status_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止内核")
        self.stop_btn.setProperty("variant", "secondary")
        self.stop_btn.clicked.connect(self._stop_core)
        status_row.addWidget(self.stop_btn)
        layout.addLayout(status_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.core_path = ElidedLabel("", role="caption")
        path_row.addWidget(self.core_path, 1)
        pick_btn = QPushButton("选择内核")
        pick_btn.setToolTip("指定已有的 mihomo 内核文件（例如随其他客户端安装的）")
        pick_btn.clicked.connect(self._pick_core)
        path_row.addWidget(pick_btn)
        refresh_btn = QPushButton("重新检测")
        refresh_btn.clicked.connect(self._refresh_all)
        path_row.addWidget(refresh_btn)
        layout.addLayout(path_row)

        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_row.addWidget(QLabel("混合端口"))
        self.mixed_spin = QSpinBox()
        self.mixed_spin.setRange(1024, 65535)
        self.mixed_spin.setKeyboardTracking(False)
        self.mixed_spin.setFixedWidth(96)
        port_row.addWidget(self.mixed_spin)
        port_row.addSpacing(4)
        port_row.addWidget(QLabel("控制端口"))
        self.api_spin = QSpinBox()
        self.api_spin.setRange(1024, 65535)
        self.api_spin.setKeyboardTracking(False)
        self.api_spin.setFixedWidth(96)
        port_row.addWidget(self.api_spin)
        self.port_save_btn = QPushButton("保存端口")
        self.port_save_btn.clicked.connect(self._save_ports)
        port_row.addWidget(self.port_save_btn)
        port_row.addStretch(1)
        layout.addLayout(port_row)
        return card

    def _build_provider_card(self) -> ToolSectionCard:
        card = self._card(
            "提供商",
            "提供商由你自己提供：本地 YAML（Clash / Mihomo 配置）或订阅链接。"
            "程序不内置任何节点，也不会代替你申请服务。",
            "cloud")
        layout = card.content_layout

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.provider_path = ElidedLabel("未导入本地配置", role="caption")
        file_row.addWidget(self.provider_path, 1)
        import_btn = QPushButton("导入 YAML")
        import_btn.clicked.connect(self._import_yaml)
        file_row.addWidget(import_btn)
        layout.addLayout(file_row)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.sub_entry = QLineEdit()
        self.sub_entry.setPlaceholderText("订阅链接（https://…），留空表示使用本地配置")
        url_row.addWidget(self.sub_entry, 1)
        sub_btn = QPushButton("导入订阅")
        sub_btn.clicked.connect(self._apply_subscription)
        url_row.addWidget(sub_btn)
        layout.addLayout(url_row)

        self.provider_state = QLabel("")
        self.provider_state.setProperty("role", "caption")
        layout.addWidget(self.provider_state)
        return card

    def _build_node_card(self) -> ToolSectionCard:
        card = self._card("节点与模式", "从内核读取提供商里的节点；测速与切换只影响本程序。",
                          "globe")
        layout = card.content_layout

        node_row = QHBoxLayout()
        node_row.setSpacing(8)
        self.node_combo = QComboBox()
        self.node_combo.setMinimumWidth(240)
        self.node_combo.setMinimumContentsLength(16)
        self.node_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.node_combo.currentIndexChanged.connect(self._select_node)
        node_row.addWidget(self.node_combo, 1)
        self.delay_btn = QPushButton("测延迟")
        self.delay_btn.clicked.connect(self._test_delay)
        node_row.addWidget(self.delay_btn)
        self.fast_btn = QPushButton("自动选最快")
        self.fast_btn.clicked.connect(self._auto_fastest)
        node_row.addWidget(self.fast_btn)
        layout.addLayout(node_row)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("代理模式"))
        self.mode_combo = QComboBox()
        for key in settings.MODES:
            self.mode_combo.addItem(settings.MODE_LABELS[key], key)
        self.mode_combo.setMinimumWidth(140)
        self.mode_combo.currentIndexChanged.connect(self._change_mode)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self.node_state = QLabel("")
        self.node_state.setProperty("role", "caption")
        layout.addWidget(self.node_state)
        return card

    def _build_program_card(self) -> ToolSectionCard:
        card = self._card(
            "程序代理",
            "打开后本程序的网络请求（接口、下载、Git 等）经内核发送；"
            "只影响本程序，不改 Windows 系统代理，退出程序自动恢复。",
            "antenna")
        layout = card.content_layout

        switch_row = QHBoxLayout()
        switch_row.setSpacing(6)
        self.proxy_switch = ToggleSwitch()
        self.proxy_switch.clicked.connect(self._toggle_proxy)
        switch_row.addWidget(self.proxy_switch)
        switch_row.addWidget(QLabel("让本程序请求走代理"))
        switch_row.addStretch(1)
        check_btn = QPushButton("检测出口")
        check_btn.setToolTip("经代理访问外网并显示出口地址")
        check_btn.clicked.connect(self._check_exit)
        switch_row.addWidget(check_btn)
        layout.addLayout(switch_row)

        self.proxy_state = QLabel(environ.describe())
        self.proxy_state.setProperty("role", "caption")
        layout.addWidget(self.proxy_state)
        return card

    def _build_log_card(self) -> ToolSectionCard:
        card = self._card("运行日志", "内核最近输出的内容（最多显示最后一段）。", "document")
        layout = card.content_layout
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(132)
        self.log_view.setPlaceholderText("内核还没有输出")
        self.log_view.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace;"
                                    "font-size: 11px;")
        layout.addWidget(self.log_view)
        row = QHBoxLayout()
        row.addStretch(1)
        reload_btn = QPushButton("刷新日志")
        reload_btn.clicked.connect(self._refresh_log)
        row.addWidget(reload_btn)
        layout.addLayout(row)
        return card

    # ==================== 图标 / 主题 ====================

    def _register_icon(self, widget, name: str, role: str):
        self._icons.append((widget, name, role))
        self._apply_icon(widget, name, role)

    def _apply_icon(self, widget, name: str, role: str):
        color = self._icon_color(role)
        if isinstance(widget, ToolSectionCard):
            widget.set_icon(name, color)
            return
        widget.setIcon(line_icon(name, color, 16))
        widget.setIconSize(QSize(16, 16))

    @staticmethod
    def _icon_color(role: str) -> str:
        pal = theme.current_palette()
        if role == "primary":
            return pal.color(QPalette.HighlightedText).name()
        if role == "danger":
            return pal.color(QPalette.BrightText).name()
        if role == "title":
            return pal.color(QPalette.Link).name()
        return pal.color(QPalette.ButtonText).name()

    def on_theme_changed(self):
        for widget, name, role in self._icons:
            self._apply_icon(widget, name, role)

    # ==================== 状态刷新 ====================

    def on_show(self):
        self._refresh_all()

    def _hint(self, label: QLabel, text: str, tone: str = "muted"):
        role = {"danger": QPalette.BrightText,
                "success": QPalette.LinkVisited,
                "muted": QPalette.PlaceholderText}.get(tone, QPalette.PlaceholderText)
        label.setText(text)
        label.setForegroundRole(role)

    def _refresh_all(self):
        cfg = settings.load()
        self.mixed_spin.setValue(int(cfg["mixed_port"]))
        self.api_spin.setValue(int(cfg["api_port"]))
        index = self.mode_combo.findData(cfg["mode"])
        if index >= 0 and index != self.mode_combo.currentIndex():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)
        if not self.sub_entry.text().strip() and cfg.get("provider_kind") == "url":
            self.sub_entry.setText(str(cfg.get("provider_url") or ""))
        self.sub_entry.setPlaceholderText(
            "订阅链接（https://…），留空表示使用本地配置"
            if cfg.get("provider_kind") != "url"
            else "当前使用订阅链接；清空并导入 YAML 可切回本地配置")
        self._refresh_status()
        self._refresh_provider()
        self._refresh_log()
        if proxy_core.is_running():
            self._load_nodes()
        else:
            self._loading_nodes = True
            self.node_combo.clear()
            self._loading_nodes = False
            self._hint(self.node_state, "内核未启动，暂无节点")

    def _refresh_status(self):
        status = proxy_core.status()
        running = bool(status["running"])
        self.core_state.setText(
            f"运行中 · {status['version'] or '版本未知'} · PID {status['pid']}"
            if running else "未启动")
        self.core_state.setForegroundRole(
            QPalette.LinkVisited if running else QPalette.PlaceholderText)
        self.core_path.setText(status["core_path"] or "未找到内核文件")
        self.start_btn.setEnabled(not running and not self._busy)
        self.stop_btn.setEnabled(running and not self._busy)
        self.port_save_btn.setEnabled(not running and not self._busy)
        if environ.is_enabled() and not running:
            self._hint(self.proxy_state, "代理开关已打开，但内核没有运行，请重新启动内核",
                       "danger")
        else:
            self._hint(self.proxy_state, environ.describe(),
                       "success" if environ.is_enabled() else "muted")
        self.proxy_switch.set_state(environ.is_enabled())
        self.proxy_switch.setEnabled(not self._busy)
        self.delay_btn.setEnabled(running and not self._busy)
        self.fast_btn.setEnabled(running and not self._busy)
        self.node_combo.setEnabled(running and not self._busy)
        self.mode_combo.setEnabled(running and not self._busy)
        self._refresh_ports_enabled()

    def _refresh_ports_enabled(self):
        running = proxy_core.is_running()
        self.mixed_spin.setEnabled(not running and not self._busy)
        self.api_spin.setEnabled(not running and not self._busy)

    def _refresh_provider(self):
        info = provider.summary()
        if info["kind"] == "url":
            self.provider_path.setText("当前使用订阅链接")
            if info.get("url"):
                self.sub_entry.setText(info["url"])
        else:
            self.provider_path.setText(info.get("path") or "未导入本地配置")
            if settings.load().get("provider_kind") != "url":
                # 本地配置就位后清掉订阅输入，避免误以为订阅仍生效
                if info["ready"]:
                    self.sub_entry.clear()
        self._hint(self.provider_state, info["text"],
                   "success" if info["ready"] else "muted")

    def _refresh_log(self):
        path = settings.log_path()
        try:
            data = path.read_bytes()[-4000:]
        except OSError:
            data = b""
        text = data.decode("utf-8", errors="replace").strip()
        self.log_view.setPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _tick(self):
        """定时只做本地状态核对，不轮询内核接口。"""
        running = proxy_core.is_running()
        if self.start_btn.isEnabled() == running:
            self._refresh_status()

    def _load_nodes(self):
        def work():
            return proxy_core.group_info()

        def done(result, err):
            self._loading_nodes = True
            try:
                self.node_combo.clear()
                if err or not result:
                    self._hint(self.node_state, f"读取节点失败：{err or '内核未响应'}", "danger")
                    return
                nodes = result.get("all") or []
                self.node_combo.addItems(nodes)
                now = result.get("now") or ""
                if now in nodes:
                    self.node_combo.setCurrentIndex(nodes.index(now))
                group = result.get("name") or ""
                self._hint(self.node_state,
                           f"共 {len(nodes)} 个节点 · 当前：{now or '未选择'}"
                           + (f" · 策略组 {group}" if group else ""))
            finally:
                self._loading_nodes = False

        run_bg(work, done)

    # ==================== 动作 ====================

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._refresh_status()

    def _start_core(self, then=None):
        if self._busy:
            return
        self._set_busy(True)
        self._hint(self.core_state, "正在启动内核…", "muted")

        def work():
            return proxy_core.start()

        def done(result, err):
            self._set_busy(False)
            if err:
                self._hint(self.core_state, f"启动失败：{err}", "danger")
                self.app.toast(f"代理内核启动失败：{err}", "error")
                self._refresh_log()
                return
            self._refresh_status()
            self._refresh_log()
            self.app.toast("代理内核已启动", "success")
            if then:
                then()

        run_bg(work, done)

    def _stop_core(self):
        if self._busy:
            return
        self._set_busy(True)

        def work():
            return proxy_core.stop()

        def done(_result, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"停止内核失败：{err}", "error")
            else:
                self.app.toast("代理内核已停止", "info")
            self._refresh_all()

        run_bg(work, done)

    def _pick_core(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择 mihomo 内核", "", "可执行文件 (*.exe)")
        if not path:
            return
        settings.update(core_path=path)
        self._refresh_all()
        self.app.toast("已记录内核位置", "success")

    def _save_ports(self):
        mixed = int(self.mixed_spin.value())
        api = int(self.api_spin.value())
        if mixed == api:
            self.app.toast("混合端口与控制端口不能相同", "warning")
            return
        if not proxy_core.port_free(mixed) or not proxy_core.port_free(api):
            self.app.toast("端口已被占用，请换一组", "warning")
            return
        settings.update(mixed_port=mixed, api_port=api)
        self._refresh_all()
        self.app.toast("端口已保存", "success")

    def _import_yaml(self):
        if self._busy:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择提供商配置", "", "配置文件 (*.yaml *.yml *.txt)")
        if not path:
            return
        self._set_busy(True)

        def work():
            cfg = settings.update(provider_kind="file", provider_url="")
            info = provider.import_local(path, cfg)
            if proxy_core.is_running():
                proxy_core.reload()
            return info

        def done(result, err):
            self._set_busy(False)
            if err:
                self._hint(self.provider_state, f"导入失败：{err}", "danger")
                self.app.toast(f"导入提供商失败：{err}", "error")
                return
            self.sub_entry.clear()
            self._refresh_provider()
            self.app.toast(
                f"提供商已导入：{result.get('nodes', 0)} 个节点", "success")
            if proxy_core.is_running():
                QTimer.singleShot(1200, self._load_nodes)

        run_bg(work, done)

    def _apply_subscription(self):
        if self._busy:
            return
        url = self.sub_entry.text().strip()
        if not url:
            self.app.toast("请先填写订阅链接", "warning")
            return
        self._set_busy(True)

        def work():
            cfg = settings.update(provider_kind="url", provider_url=url)
            provider.set_subscription(url, cfg)
            if proxy_core.is_running():
                proxy_core.reload()
            return True

        def done(_result, err):
            self._set_busy(False)
            if err:
                self._hint(self.provider_state, f"订阅导入失败：{err}", "danger")
                self.app.toast(f"订阅导入失败：{err}", "error")
                return
            self._refresh_provider()
            self.app.toast("订阅已保存，内核会按链接拉取节点", "success")
            if proxy_core.is_running():
                QTimer.singleShot(1800, self._load_nodes)

        run_bg(work, done)

    def _select_node(self, index: int):
        if self._loading_nodes or index < 0 or self._busy:
            return
        name = self.node_combo.itemText(index)
        if not name:
            return

        def work():
            proxy_core.select(name)
            return name

        def done(result, err):
            if err:
                self._hint(self.node_state, f"切换节点失败：{err}", "danger")
                return
            self._hint(self.node_state, f"已切换到：{result}", "success")

        run_bg(work, done)

    def _test_delay(self):
        if self._busy or not proxy_core.is_running():
            return
        self._set_busy(True)
        self._hint(self.node_state, "正在测速…", "muted")

        def work():
            return proxy_core.delay_all()

        def done(result, err):
            self._set_busy(False)
            if err:
                self._hint(self.node_state, f"测速失败：{err}", "danger")
                return
            data = result or {}
            if not data:
                self._hint(self.node_state, "测速没有返回可用节点", "warning")
                return
            current = self.node_combo.currentText()
            fastest = min(data, key=lambda name: data[name] or 10 ** 6)
            parts = [f"已测 {len(data)} 个节点"]
            if current in data:
                parts.append(f"当前 {data[current]} ms")
            parts.append(f"最快 {fastest}（{data[fastest]} ms）")
            self._hint(self.node_state, " · ".join(parts), "success")

        run_bg(work, done)

    def _auto_fastest(self):
        if self._busy or not proxy_core.is_running():
            return
        self._set_busy(True)
        self._hint(self.node_state, "正在测速并切换…", "muted")

        def work():
            data = proxy_core.delay_all()
            if not data:
                raise proxy_core.ProxyError("没有可用的节点")
            fastest = min(data, key=lambda name: data[name] or 10 ** 6)
            proxy_core.select(fastest)
            return fastest, data[fastest]

        def done(result, err):
            self._set_busy(False)
            if err:
                self._hint(self.node_state, f"自动选择失败：{err}", "danger")
                return
            name, delay = result
            self._load_nodes()
            self._hint(self.node_state, f"已选择最快节点：{name}（{delay} ms）", "success")

        run_bg(work, done)

    def _change_mode(self, _index: int):
        mode = self.mode_combo.currentData()
        if not mode or self._busy:
            return
        settings.update(mode=mode)
        if not proxy_core.is_running():
            return

        def work():
            proxy_core.set_mode(mode)
            return mode

        def done(_result, err):
            if err:
                self.app.toast(f"切换代理模式失败：{err}", "error")

        run_bg(work, done)

    def _toggle_proxy(self):
        if self._busy:
            return
        wanted = self.proxy_switch.isChecked()
        self._set_busy(True)

        from toolbox import proxy as proxy_service

        def work():
            return proxy_service.enable() if wanted else proxy_service.disable()

        def done(result, err):
            self._set_busy(False)
            if err:
                self.proxy_switch.set_state(not wanted)
                self._hint(self.proxy_state, f"启用代理失败：{err}", "danger")
                self.app.toast(f"启用代理失败：{err}", "error")
                return
            self._refresh_status()
            self._refresh_log()
            if wanted:
                self.app.toast(f"本程序已走代理：{environ.url()}", "success")
                self._load_nodes()
            else:
                self.app.toast("已关闭程序代理，请求恢复直连", "info")

        run_bg(work, done)

    def _check_exit(self):
        if self._busy:
            return
        if not environ.is_enabled():
            self.app.toast("请先打开程序代理开关", "warning")
            return
        self._set_busy(True)
        self._hint(self.proxy_state, "正在检测出口…", "muted")

        def work():
            return proxy_core.check_exit()

        def done(result, err):
            self._set_busy(False)
            if err:
                self._hint(self.proxy_state, f"出口检测失败：{err}", "danger")
                return
            self._hint(self.proxy_state, f"代理可用，出口地址：{result}", "success")

        run_bg(work, done)

    # ==================== 生命周期 ====================

    def shutdown(self):
        self._timer.stop()
        try:
            from toolbox import proxy as proxy_service
            proxy_service.shutdown()
        except Exception:
            pass
        super().shutdown()