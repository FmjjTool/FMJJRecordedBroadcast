"""抖音弹幕实时数据接入页（PySide6 版，完整三栏布局）。"""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main
from gui_qt.toast import show_toast
from gui_qt.pages.base import BasePage


class TestPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.fetcher = None
        self.connected = False
        self._start_ts = None
        self._like_count = 0
        self._timer_running = False
        self._monitoring = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        # 顶部控制栏
        top = QFrame()
        top.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; padding: 4px; }")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(8, 4, 8, 4)
        for text, slot in [
            ("🔑 Cookie 登录", self._open_cookie),
            ("⚙ 设置", self._open_settings),
            ("💾 保存弹幕", self._save_danmaku),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            top_l.addWidget(b)
        top_l.addStretch(1)
        self.connect_btn = QPushButton("▶ 连接")
        self.connect_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.connect_btn.clicked.connect(self._connect)
        top_l.addWidget(self.connect_btn)
        self.disconnect_btn = QPushButton("■ 断开")
        self.disconnect_btn.setStyleSheet("background: #c62828; color: white;")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.disconnect_btn.setEnabled(False)
        top_l.addWidget(self.disconnect_btn)
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
        top_l.addWidget(self.status_dot)
        self.status_label = QLabel("已断开")
        self.status_label.setStyleSheet("color: #888;")
        top_l.addWidget(self.status_label)
        outer.addWidget(top)

        # 第二排：保存统计 / 蓝保存 / 开播监控
        bar2 = QFrame()
        bar2.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; padding: 4px; }")
        b2 = QHBoxLayout(bar2)
        b2.setContentsMargins(8, 4, 8, 4)
        self.save_stats = QCheckBox("📊 保存统计")
        self.save_stats.toggled.connect(lambda: self._log(f"保存统计: {'开' if self.save_stats.isChecked() else '关'}"))
        b2.addWidget(self.save_stats)
        self.monitor_btn = QPushButton("👁 开播监控")
        self.monitor_btn.clicked.connect(self._toggle_monitor)
        b2.addWidget(self.monitor_btn)
        self.monitor_status = QLabel("")
        self.monitor_status.setStyleSheet("color: #888;")
        b2.addWidget(self.monitor_status)
        b2.addStretch(1)
        self.login_btn = QPushButton("🔐 登录抖音")
        self.login_btn.setStyleSheet("background: #2a5a8c; color: white;")
        self.login_btn.clicked.connect(self._login_douyin)
        b2.addWidget(self.login_btn)
        outer.addWidget(bar2)

        # 主体三栏
        body = QFrame()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)

        # 左：主播信息
        self._build_anchor_panel(bl)
        # 中：弹幕信息（聊天+礼物）
        self._build_chat_gift_panel(bl)
        # 右：5 块垂直
        self._build_right_panel(bl)
        outer.addWidget(body, 1)

        # 底部：状态 + 计时 + 点赞
        bottom = QFrame()
        bottom.setStyleSheet("QFrame { background: #15171d; border-radius: 4px; padding: 4px; }")
        bot_l = QHBoxLayout(bottom)
        bot_l.setContentsMargins(8, 4, 8, 4)
        self.timer_label = QLabel("⏱ 00:00:00")
        self.timer_label.setStyleSheet("font-family: Consolas; font-weight: bold;")
        bot_l.addWidget(self.timer_label)
        bot_l.addStretch(1)
        self.like_label = QLabel("🛒 点赞: 0")
        self.like_label.setStyleSheet("color: #64b5f6; font-weight: bold;")
        bot_l.addWidget(self.like_label)
        outer.addWidget(bottom)

    def _build_anchor_panel(self, parent):
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(card)
        v.addWidget(QLabel("📺 主播信息"))
        v.addWidget(QLabel("（暂无封面）", styleSheet="color: #555;"))
        self.anchor_fields = {}
        for k in ("主播", "粉丝数", "在线观众", "累计观看", "本场点赞"):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{k}:", styleSheet="color: #888;"))
            v_lbl = QLabel("*****", styleSheet="color: #888;")
            row.addWidget(v_lbl, 1)
            self.anchor_fields[k] = v_lbl
            v.addLayout(row)
        v.addStretch(1)
        parent.addWidget(card, 1)

    def _build_chat_gift_panel(self, parent):
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(card)
        head = QHBoxLayout()
        head.addWidget(QLabel("💬 弹幕信息（聊天+礼物）"))
        head.addStretch(1)
        self.chat_var = QCheckBox("聊天")
        self.chat_var.setChecked(True)
        head.addWidget(self.chat_var)
        self.gift_var = QCheckBox("礼物")
        self.gift_var.setChecked(True)
        head.addWidget(self.gift_var)
        v.addLayout(head)
        self.chat_box = QTextEdit()
        self.chat_box.setReadOnly(True)
        v.addWidget(self.chat_box, 1)
        parent.addWidget(card, 2)

    def _build_right_panel(self, parent):
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(card)
        # 1. 房间号
        v.addWidget(QLabel("① 房间号"))
        row = QHBoxLayout()
        self.room_entry = QLineEdit()
        self.room_entry.setPlaceholderText("房间号")
        row.addWidget(self.room_entry, 1)
        c = QPushButton("连接")
        c.clicked.connect(self._connect)
        row.addWidget(c)
        v.addLayout(row)
        # 2. WS 地址
        v.addWidget(QLabel("② WS 地址（转发）"))
        row2 = QHBoxLayout()
        self.ws_entry = QLineEdit()
        self.ws_entry.setPlaceholderText("转发地址")
        row2.addWidget(self.ws_entry, 1)
        ws_btn = QPushButton("转发")
        row2.addWidget(ws_btn)
        v.addLayout(row2)
        # 3. sessionid
        v.addWidget(QLabel("③ sessionid 凭证"))
        row3 = QHBoxLayout()
        self.session_entry = QLineEdit()
        self.session_entry.setEchoMode(QLineEdit.Password)
        self.session_entry.setPlaceholderText("sessionid")
        row3.addWidget(self.session_entry, 1)
        v_btn = QPushButton("✓")
        v_btn.clicked.connect(self._verify_session)
        row3.addWidget(v_btn)
        v.addLayout(row3)
        # 4. 累计打赏榜
        v.addWidget(QLabel("④ 累计打赏榜"))
        self.gift_list = QListWidget()
        v.addWidget(self.gift_list, 1)
        # 5. 弹幕信息2
        head5 = QHBoxLayout()
        head5.addWidget(QLabel("⑤ 弹幕信息（点赞+进入+日志）"))
        head5.addStretch(1)
        self.like_var = QCheckBox("点赞")
        self.like_var.setChecked(True)
        head5.addWidget(self.like_var)
        self.enter_var = QCheckBox("进入")
        self.enter_var.setChecked(True)
        head5.addWidget(self.enter_var)
        self.log_var = QCheckBox("日志")
        self.log_var.setChecked(True)
        head5.addWidget(self.log_var)
        v.addLayout(head5)
        self.event_box = QTextEdit()
        self.event_box.setReadOnly(True)
        v.addWidget(self.event_box, 1)
        parent.addWidget(card, 2)

    def _open_cookie(self):
        self.app.toast("Cookie 登录请通过侧栏 🍪 Cookie 按钮管理", "info")

    def _open_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ 弹幕姬设置")
        dlg.resize(400, 200)
        v = QFormLayout(dlg)
        interval = QLineEdit("30")
        v.addRow("监控间隔(秒):", interval)
        v.addRow("(本设置仅占位，详细设置请用原版)", QLabel(""))
        ok = QPushButton("确定")
        ok.clicked.connect(dlg.accept)
        v.addRow(ok)
        dlg.exec()

    def _save_danmaku(self):
        chat = self.chat_box.toPlainText()
        event = self.event_box.toPlainText()
        if not chat and not event:
            self.app.toast("当前没有弹幕可保存", "warning")
            return
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir()
        d.mkdir(parents=True, exist_ok=True)
        room = self.room_entry.text().strip() or "unknown"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = d / f"{room}_{ts}.txt"
        path.write_text(chat + "\n" + event, encoding="utf-8")
        self.app.toast(f"已保存: {path}", "success")

    def _verify_session(self):
        sid = self.session_entry.text().strip()
        if not sid:
            self.app.toast("请输入 sessionid", "warning")
            return
        from toolbox.douyin_live import save_sessionid
        if save_sessionid(sid):
            self.app.toast("sessionid 已加密保存", "success")
        else:
            self.app.toast("保存失败", "error")

    def _login_douyin(self):
        def run():
            from auth import douyin_browser_login
            return douyin_browser_login()

        def done(result, err):
            if err:
                self.app.toast(f"登录失败: {err}", "error")
            elif result:
                self.app.toast("抖音登录成功", "success")
            else:
                self.app.toast("未检测到登录（3 分钟超时）", "warning")

        from gui_qt.common import run_bg
        run_bg(run, done)

    def _toggle_monitor(self):
        if self._monitoring:
            self._monitoring = False
            self.monitor_btn.setText("👁 开播监控")
            self.monitor_status.setText("")
            self.app.toast("开播监控已停止", "info")
            return
        room = self.room_entry.text().strip()
        if not room:
            self.app.toast("请先输入房间号", "warning")
            return
        self._monitoring = True
        self.monitor_btn.setText("👁 停止监控")
        self.monitor_status.setText(f"监控中 · 房间 {room}")
        self.app.toast("开播监控已启动", "success")
        from toolbox.douyin_live import LiveMonitor
        monitor = LiveMonitor(room, interval=1800, on_change=lambda *a: None)
        threading.Thread(target=monitor.start, daemon=True).start()

    def _connect(self):
        room = self.room_entry.text().strip()
        if not room:
            self.app.toast("请输入房间号", "warning")
            return
        from toolbox.douyin_live import DouyinLiveFetcher
        self.connected = True
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.status_dot.setStyleSheet("color: #eab308; font-size: 14px;")
        self.status_label.setText(f"连接中 · 房间 {room}")
        self._start_ts = datetime.now()
        self._like_count = 0
        self.like_label.setText("🛒 点赞: 0")
        self.fetcher = DouyinLiveFetcher(
            room, on_message=self._on_message, on_room_info=self._on_room_info, on_status=self._on_status)
        import threading
        threading.Thread(target=self.fetcher.start, daemon=True).start()
        if not self._timer_running:
            self._timer_running = True
            self._tick()

    def _disconnect(self):
        self.connected = False
        if self.fetcher:
            try:
                self.fetcher.stop()
            except Exception:
                pass
            self.fetcher = None
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
        self.status_label.setText("已断开")
        self._timer_running = False

    def _on_room_info(self, info):
        def apply():
            if info.get("主播"):
                self.anchor_fields["主播"].setText(info["主播"])
            for k, src in (("粉丝数", "fans"), ("在线观众", "online"), ("累计观看", "total_viewers")):
                if info.get(src):
                    self.anchor_fields[k].setText(f"{int(info[src]):,}")
        post_to_main(apply)

    def _on_message(self, name, text):
        def apply():
            ts = datetime.now().strftime("%H:%M:%S")
            if name == "礼物":
                if self.gift_var.isChecked():
                    self.chat_box.append(f"{ts} 🎁 {text}")
            elif name in ("弹幕", "表情弹幕"):
                if self.chat_var.isChecked():
                    self.chat_box.append(f"{ts} {text}")
            elif name == "进场":
                if self.enter_var.isChecked():
                    self.event_box.append(f"{ts} 💚 {text}")
            elif name == "点赞":
                self._like_count += 1
                self.like_label.setText(f"🛒 点赞: {self._like_count:,}")
                self.anchor_fields["本场点赞"].setText(f"{self._like_count:,}")
                if self.like_var.isChecked():
                    self.event_box.append(f"{ts} 👍 {text}")
            elif name == "关注":
                if self.log_var.isChecked():
                    self.event_box.append(f"{ts} ⭐ {text}")
            else:
                if self.log_var.isChecked():
                    self.event_box.append(f"{ts} [{name}] {text}")
        post_to_main(apply)

    def _on_status(self, status, msg):
        def apply():
            if status == "connected":
                self.status_dot.setStyleSheet("color: #4caf50; font-size: 14px;")
                self.status_label.setText(msg)
            elif status == "captcha":
                self.status_dot.setStyleSheet("color: #ff9800; font-size: 14px;")
                self.status_label.setText(msg)
            elif status in ("error", "disconnected"):
                self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
                self.status_label.setText(msg)
                if status == "error":
                    self._disconnect()
        post_to_main(apply)

    def _tick(self):
        if not self.connected:
            self._timer_running = False
            return
        if self._start_ts:
            elapsed = (datetime.now() - self._start_ts).total_seconds()
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            s = int(elapsed % 60)
            self.timer_label.setText(f"⏱ {h:02d}:{m:02d}:{s:02d}")
        QTimer.singleShot(1000, self._tick)
