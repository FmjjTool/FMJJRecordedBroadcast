"""抖音弹幕实时数据接入页（PySide6 版，对应原版 gui/test_page.py）。

三栏布局：左主播信息 / 中弹幕聊天礼物 / 右5块垂直（房间号/WS/sessionid/打赏榜/弹幕信息2）。
- 顶栏：Cookie 登录 / 轮询 / 设置 / 连接 / 断开 / 清空弹幕
- 工具栏：保存弹幕 / 保存统计 / 开播监控 / 登录抖音
- 底栏：状态点 + 状态文本 + 计时 + 本场点赞
- 真实抓取：DouyinLiveFetcher / StatsCollector / LiveMonitor
"""

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage


class TestPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.connected = False
        self._timer_running = False
        self._start_ts = None
        self._like_count = 0
        self.fetcher = None
        self.stats_collector = None
        self.live_monitor = None
        self._monitoring = False
        self.settings = self._load_settings()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        # 标题
        outer.addWidget(QLabel("🎬 抖音弹幕 · 实时数据接入",
                               styleSheet="font-size: 18px; font-weight: bold;"))
        outer.addWidget(QLabel("真实连接抖音直播间，实时抓取弹幕 / 礼物 / 进场 / 点赞 / 关注",
                               styleSheet="color: #888; font-size: 11px;"))

        # ===== 顶栏：全局控制 =====
        top = QFrame()
        top.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(10, 6, 10, 6)
        cookie_btn = QPushButton("🔑 Cookie 登录")
        cookie_btn.clicked.connect(self._open_cookie)
        top_l.addWidget(cookie_btn)
        self.poll_var = QCheckBox("轮询")
        self.poll_var.setChecked(True)
        top_l.addWidget(self.poll_var)
        settings_btn = QPushButton("⚙ 设置")
        settings_btn.clicked.connect(self._open_settings)
        top_l.addWidget(settings_btn)
        top_l.addStretch(1)
        clear_btn = QPushButton("🗑 清空弹幕")
        clear_btn.clicked.connect(self._clear_all)
        top_l.addWidget(clear_btn)
        self.disconnect_btn = QPushButton("■ 断开")
        self.disconnect_btn.setStyleSheet("background: #c62828; color: white;")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.disconnect_btn.setEnabled(False)
        top_l.addWidget(self.disconnect_btn)
        self.connect_btn = QPushButton("▶ 连接")
        self.connect_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.connect_btn.clicked.connect(self._connect)
        top_l.addWidget(self.connect_btn)
        outer.addWidget(top)

        # ===== 工具栏 =====
        toolbar = QFrame()
        toolbar.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(10, 4, 10, 4)
        save_btn = QPushButton("💾 保存弹幕")
        save_btn.clicked.connect(self._save_danmaku_now)
        tb.addWidget(save_btn)
        self.save_stats_var = QCheckBox("📊 保存统计")
        self.save_stats_var.setChecked(bool(self.settings.get("save_stats", False)))
        self.save_stats_var.toggled.connect(self._on_save_stats_toggle)
        tb.addWidget(self.save_stats_var)
        self.monitor_btn = QPushButton("👁 开播监控")
        self.monitor_btn.clicked.connect(self._toggle_monitor)
        tb.addWidget(self.monitor_btn)
        self.monitor_status = QLabel("")
        self.monitor_status.setStyleSheet("color: #888; font-size: 11px;")
        tb.addWidget(self.monitor_status)
        tb.addStretch(1)
        login_btn = QPushButton("🔐 登录抖音")
        login_btn.setStyleSheet("background: #2a5a8c; color: white;")
        login_btn.clicked.connect(self._login_douyin)
        tb.addWidget(login_btn)
        outer.addWidget(toolbar)

        # ===== 主体三栏 =====
        body = QHBoxLayout()
        self._build_anchor_panel(body)
        self._build_chat_gift_panel(body)
        self._build_right_panel(body)
        outer.addLayout(body, 1)

        # ===== 底栏：状态 + 计时 + 点赞 =====
        bottom = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
        bottom.addWidget(self.status_dot)
        self.status_label = QLabel("已断开")
        self.status_label.setStyleSheet("color: #888;")
        bottom.addWidget(self.status_label)
        bottom.addWidget(QLabel("│"))
        bottom.addWidget(QLabel("⏱ 计时:"))
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setStyleSheet("font-family: Consolas; font-weight: bold; font-size: 13px;")
        bottom.addWidget(self.timer_label)
        bottom.addWidget(QLabel("│"))
        bottom.addWidget(QLabel("🛒 本场点赞:"))
        self.like_label = QLabel("0")
        self.like_label.setStyleSheet("color: #64b5f6; font-weight: bold;")
        bottom.addWidget(self.like_label)
        bottom.addStretch(1)
        outer.addLayout(bottom)

    # ======================== 设置 ========================
    def _load_settings(self):
        try:
            from toolbox.douyin_live import load_settings
            return load_settings()
        except Exception:
            return {}

    def _save_settings(self):
        try:
            from toolbox.douyin_live import save_settings
            save_settings(self.settings)
        except Exception:
            pass

    # ======================== 左：主播信息 ========================
    def _build_anchor_panel(self, parent):
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.addWidget(QLabel("📺 主播信息", styleSheet="font-weight: bold;"))

        # 封面 + 保存按钮
        cover_row = QHBoxLayout()
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setFixedSize(80, 80)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet(
            "background: #1a1d27; border-radius: 6px; color: #555;")
        cover_row.addWidget(self.cover_label)
        cover_btn = QPushButton("💾 保存封面/数据")
        cover_btn.clicked.connect(self._save_cover)
        cover_row.addWidget(cover_btn)
        cover_row.addStretch(1)
        v.addLayout(cover_row)

        # 5 字段
        self.anchor_fields = {}
        for text, key in [("主播:", "anchor_name"), ("主播粉丝数:", "anchor_fans"),
                          ("在线观众数:", "online_count"), ("累计观看人数:", "total_viewers"),
                          ("本场点赞数:", "like_count")]:
            row = QHBoxLayout()
            lbl = QLabel(text)
            lbl.setFixedWidth(86)
            lbl.setStyleSheet("color: #888;")
            row.addWidget(lbl)
            val = QLabel("*****")
            val.setStyleSheet("color: #888;")
            row.addWidget(val, 1)
            self.anchor_fields[key] = val
            v.addLayout(row)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2a2d3a;")
        v.addWidget(sep)

        # 时间/来源
        self.time_label = QLabel("UTC+8 北京时间")
        self.time_label.setStyleSheet("font-weight: bold;")
        v.addWidget(self.time_label)
        self.now_label = QLabel("--")
        self.now_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.now_label)
        for txt in ("抖音：欲墓", "微博：欲墓XINMU", "b站：欲墓XINMU"):
            v.addWidget(QLabel(txt, styleSheet="color: #888; font-size: 11px;"))
        v.addWidget(QLabel("使用信息请标明来源", styleSheet="color: #666; font-size: 10px;"))
        v.addStretch(1)
        parent.addWidget(card, 1)

    # ======================== 中：弹幕信息（聊天+礼物） ========================
    def _build_chat_gift_panel(self, parent):
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        head = QHBoxLayout()
        head.addWidget(QLabel("💬 弹幕信息（聊天+礼物）", styleSheet="font-weight: bold;"))
        head.addStretch(1)
        self.gift_var = QCheckBox("礼物")
        self.gift_var.setChecked(True)
        head.addWidget(self.gift_var)
        self.chat_var = QCheckBox("聊天")
        self.chat_var.setChecked(True)
        head.addWidget(self.chat_var)
        v.addLayout(head)
        self.chat_gift_box = QPlainTextEdit()
        self.chat_gift_box.setReadOnly(True)
        self.chat_gift_box.setPlainText("（暂无弹幕/礼物）")
        v.addWidget(self.chat_gift_box, 1)
        parent.addWidget(card, 2)

    # ======================== 右：5 块垂直 ========================
    def _build_right_panel(self, parent):
        card = QFrame()
        card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)

        # 1. 房间号
        v.addWidget(QLabel("① 房间号", styleSheet="font-weight: bold;"))
        row1 = QHBoxLayout()
        self.room_entry = QLineEdit()
        self.room_entry.setPlaceholderText("582775732015")
        self.room_entry.returnPressed.connect(self._connect)
        row1.addWidget(self.room_entry, 1)
        self.connect_btn2 = QPushButton("连接")
        self.connect_btn2.setStyleSheet("background: #2e7d32; color: white;")
        self.connect_btn2.clicked.connect(self._connect)
        row1.addWidget(self.connect_btn2)
        v.addLayout(row1)

        # 2. WS 地址（转发）
        v.addWidget(QLabel("② WS 地址（转发）", styleSheet="font-weight: bold;"))
        row2 = QHBoxLayout()
        self.ws_entry = QLineEdit()
        self.ws_entry.setPlaceholderText("请输入转发地址")
        row2.addWidget(self.ws_entry, 1)
        relay_btn = QPushButton("转发")
        relay_btn.clicked.connect(self._relay)
        row2.addWidget(relay_btn)
        v.addLayout(row2)

        # 3. sessionid 凭证
        v.addWidget(QLabel("③ sessionid 凭证", styleSheet="font-weight: bold;"))
        row3 = QHBoxLayout()
        self.session_entry = QLineEdit()
        self.session_entry.setPlaceholderText("请输入 sessionid")
        self.session_entry.setEchoMode(QLineEdit.Password)
        row3.addWidget(self.session_entry, 1)
        verify_btn = QPushButton("✓")
        verify_btn.setFixedWidth(36)
        verify_btn.setStyleSheet("background: #2e7d32; color: white;")
        verify_btn.clicked.connect(self._verify_session)
        row3.addWidget(verify_btn)
        help_btn = QPushButton("?")
        help_btn.setFixedWidth(30)
        help_btn.clicked.connect(self._session_help)
        row3.addWidget(help_btn)
        v.addLayout(row3)
        # 加载已加密保存的 sessionid（掩码显示，不暴露明文）
        try:
            from toolbox.douyin_live import load_sessionid
            _saved_sid = load_sessionid()
            if _saved_sid:
                self.session_entry.setText(_saved_sid)
        except Exception:
            pass

        # 4. 累计打赏榜
        v.addWidget(QLabel("④ 累计打赏榜", styleSheet="font-weight: bold;"))
        hdr = QHBoxLayout()
        for txt, w in [("排名", 50), ("昵称", 120), ("礼物数", 60), ("价值(抖币)", 80), ("更新时间", 90)]:
            lbl = QLabel(txt)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
            hdr.addWidget(lbl)
        hdr.addStretch(1)
        v.addLayout(hdr)
        self.gift_list = QPlainTextEdit()
        self.gift_list.setReadOnly(True)
        self.gift_list.setPlainText("（暂无礼物数据）")
        v.addWidget(self.gift_list, 1)

        # 5. 弹幕信息2（点赞+进入直播间+日志提醒）
        head5 = QHBoxLayout()
        head5.addWidget(QLabel("⑤ 弹幕信息（点赞+进入+日志）", styleSheet="font-weight: bold;"))
        head5.addStretch(1)
        self.like_var = QCheckBox("点赞")
        self.like_var.setChecked(True)
        head5.addWidget(self.like_var)
        self.enter_var = QCheckBox("进入直播间")
        self.enter_var.setChecked(True)
        head5.addWidget(self.enter_var)
        self.log_var = QCheckBox("日志")
        self.log_var.setChecked(True)
        head5.addWidget(self.log_var)
        v.addLayout(head5)
        self.event_box = QPlainTextEdit()
        self.event_box.setReadOnly(True)
        v.addWidget(self.event_box, 1)
        parent.addWidget(card, 2)

    # ======================== 动作 ========================
    def _open_cookie(self):
        """打开主窗口的 Cookie 管理对话框。"""
        if hasattr(self.app, "_open_cookie_manager"):
            self.app._open_cookie_manager()
        else:
            self.app.toast("Cookie 登录请通过侧栏 🍪 Cookie 按钮管理", "info")

    def _open_settings(self):
        s = self.settings
        dlg = QDialog(self)
        dlg.setWindowTitle("弹幕姬设置")
        dlg.resize(500, 520)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("⚙ 弹幕姬设置", styleSheet="font-weight: bold;"))

        def row(label, default):
            r = QHBoxLayout()
            r.addWidget(QLabel(label))
            e = QLineEdit(str(default))
            e.setFixedWidth(120)
            r.addWidget(e, 1)
            r.addStretch(1)
            v.addLayout(r)
            return e

        e_monitor = row("弹幕监控间隔(秒):", s.get("monitor_interval", 30))
        e_stats = row("统计数据采集间隔(秒):", s.get("stats_interval", 1))

        # 监控房间列表（多行）
        v.addWidget(QLabel("监控房间列表（每行一个房间号）:", styleSheet="color: #888;"))
        room_box = QPlainTextEdit()
        room_box.setPlainText(s.get("room_list", ""))
        room_box.setFixedHeight(70)
        v.addWidget(room_box)

        # 开关类
        sw_keep = QCheckBox("保持滚动（强制最新）")
        sw_keep.setChecked(bool(s.get("keep_scroll", False)))
        v.addWidget(sw_keep)
        sw_dark = QCheckBox("深色模式")
        sw_dark.setChecked(bool(s.get("dark_mode", False)))
        v.addWidget(sw_dark)
        sw_record = QCheckBox("启用直播录制（FLV，需视频模式）")
        sw_record.setChecked(bool(s.get("auto_record", False)))
        v.addWidget(sw_record)

        # 视频显示模式
        video_row = QHBoxLayout()
        video_row.addWidget(QLabel("视频显示模式:"))
        from PySide6.QtWidgets import QComboBox
        video_mode = QComboBox()
        video_mode.addItems(["正常(视频+弹幕)", "关闭视频(仅弹幕/数据)", "仅视频"])
        video_row.addWidget(video_mode, 1)
        v.addLayout(video_row)

        def save():
            self.settings["monitor_interval"] = int(e_monitor.text() or 30)
            self.settings["stats_interval"] = int(e_stats.text() or 1)
            self.settings["room_list"] = room_box.toPlainText().strip()
            self.settings["keep_scroll"] = bool(sw_keep.isChecked())
            self.settings["dark_mode"] = bool(sw_dark.isChecked())
            self.settings["auto_record"] = bool(sw_record.isChecked())
            self._save_settings()
            dlg.accept()
            self.app.toast("设置已保存", "success")

        save_btn = QPushButton("💾 保存")
        save_btn.setStyleSheet("background: #2e7d32; color: white;")
        save_btn.clicked.connect(save)
        v.addWidget(save_btn)
        dlg.exec()

    def _save_cover(self):
        self.app.toast("已保存主播封面与数据（模拟）", "success")

    def _relay(self):
        self.app.toast("WS 转发已启动（模拟）", "info")

    def _verify_session(self):
        sid = self.session_entry.text().strip()
        if not sid:
            self.app.toast("请先输入 sessionid", "warning")
            return
        from toolbox.douyin_live import save_sessionid
        if save_sessionid(sid):
            self.app.toast("sessionid 已加密保存到 src", "success")
        else:
            self.app.toast("sessionid 保存失败", "error")

    def _session_help(self):
        QMessageBox.information(self, "sessionid 帮助",
            "sessionid 是抖音直播间的登录凭证。\n\n获取方法：\n1. 浏览器登录 douyin.com\n"
            "2. F12 打开开发者工具\n3. 找到 Cookie 中的 sessionid 值\n4. 复制粘贴到此处")

    def _connect(self):
        room = self.room_entry.text().strip()
        if not room:
            QMessageBox.warning(self, "提示", "请输入房间号")
            return
        self.connected = True
        self._start_ts = time.time()
        self._like_count = 0
        self.like_label.setText("0")
        self.anchor_fields["like_count"].setText("0")
        self.connect_btn.setEnabled(False)
        self.connect_btn2.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.status_dot.setStyleSheet("color: #eab308; font-size: 14px;")
        self.status_label.setText(f"连接中 · 房间 {room}")
        self.status_label.setStyleSheet("color: #eab308;")
        # 清空两个弹幕框的初始占位
        self.chat_gift_box.setPlainText("")
        self.event_box.setPlainText("")
        # 重置主播信息占位
        for key in ("anchor_name", "anchor_fans", "online_count", "total_viewers"):
            self.anchor_fields[key].setText("…")
        if not self._timer_running:
            self._timer_running = True
            self._tick()
        # 启动真实数据抓取（start 内部自建线程，不阻塞 UI）
        from toolbox.douyin_live import DouyinLiveFetcher
        self.fetcher = DouyinLiveFetcher(
            room,
            on_message=self._on_message,
            on_room_info=self._on_room_info,
            on_status=self._on_status,
        )
        self.fetcher.start()
        # 启动统计数据采集（若开启保存统计）
        if self.save_stats_var.isChecked():
            self._start_stats_collector(room)

    def _disconnect(self):
        self.connected = False
        self._timer_running = False
        if self.fetcher:
            self.fetcher.stop()
            self.fetcher = None
        if self.stats_collector:
            self.stats_collector.stop()
            self.stats_collector = None
        self.connect_btn.setEnabled(True)
        self.connect_btn2.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
        self.status_label.setText("已断开")
        self.status_label.setStyleSheet("color: #888;")
        self.like_label.setText("0")

    # ======================== 保存弹幕 / 统计 / 开播监控 ========================
    def _save_danmaku_now(self):
        """点击保存：把当前两个弹幕框的内容一次性导出到文件。"""
        room = self.room_entry.text().strip()
        if not room:
            QMessageBox.warning(self, "提示", "请先输入房间号")
            return
        chat = self.chat_gift_box.toPlainText().strip()
        event = self.event_box.toPlainText().strip()
        if chat == "（暂无弹幕/礼物）":
            chat = ""
        if not chat and not event:
            self.app.toast("当前没有弹幕数据可保存", "warning")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        lines = []
        if chat:
            lines.append("===== 弹幕/礼物 =====")
            lines.append(chat)
        if event:
            lines.append("===== 进场/点赞/关注/日志 =====")
            lines.append(event)
        content = "\n".join(lines) + "\n"
        # 写入文件
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir()
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{room}_{ts}.txt"
        path = d / fname
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.app.toast(f"弹幕已保存到 {path}", "success")

    def _on_save_stats_toggle(self, checked):
        self.settings["save_stats"] = bool(checked)
        self._save_settings()
        if checked and self.connected:
            room = self.room_entry.text().strip()
            if room:
                self._start_stats_collector(room)
        elif not checked and self.stats_collector:
            self.stats_collector.stop()
            self.stats_collector = None

    def _start_stats_collector(self, room):
        if self.stats_collector:
            self.stats_collector.stop()
        from toolbox.douyin_live import StatsCollector
        interval = int(self.settings.get("stats_interval", 1))
        self.stats_collector = StatsCollector(room, interval, on_stats=self._on_stats)
        self.stats_collector.start()

    def _on_stats(self, info):
        """后台线程回调：统计数据快照。"""
        def apply():
            if info.get("online"):
                self.anchor_fields["online_count"].setText(info["online"])
            if info.get("fans"):
                self.anchor_fields["anchor_fans"].setText(info["fans"])
            if info.get("total_viewers"):
                self.anchor_fields["total_viewers"].setText(info["total_viewers"])
        post_to_main(apply)
        # 保存统计（后台线程写文件，无需回主线程）
        if self.save_stats_var.isChecked():
            ts = datetime.now().strftime("%H:%M:%S")
            room = self.room_entry.text().strip()
            line = f"{ts},{info.get('online', '')},{self._like_count},{info.get('fans', '')},{info.get('total_viewers', '')}"
            try:
                from toolbox.douyin_live import append_stats_file
                append_stats_file(room, line)
            except Exception:
                pass

    def _toggle_monitor(self):
        if self._monitoring:
            self._monitoring = False
            if self.live_monitor:
                self.live_monitor.stop()
                self.live_monitor = None
            self.monitor_btn.setText("👁 开播监控")
            self.monitor_status.setText("")
            return
        room = self.room_entry.text().strip()
        if not room:
            QMessageBox.warning(self, "提示", "请先在①房间号输入房间号")
            return
        self._monitoring = True
        self.monitor_btn.setText("👁 停止监控")
        self.monitor_btn.setStyleSheet("background: #2a5a8c; color: white;")
        self.monitor_status.setText(f"监控中 · 房间 {room}")
        self.monitor_status.setStyleSheet("color: #64b5f6; font-size: 11px;")
        from toolbox.douyin_live import LiveMonitor
        interval = max(10, int(self.settings.get("monitor_interval", 30)))
        self.live_monitor = LiveMonitor(room, interval, on_change=self._on_live_change)
        self.live_monitor.start()

    def _login_douyin(self):
        """复用 Cookie 管理器的浏览器登录（auth.browser_login，自动检测3分钟）。"""
        def work():
            from auth import douyin_browser_login
            return douyin_browser_login()

        def done(result, err):
            if err:
                self.app.toast(f"登录失败: {err}", "error")
            elif result:
                self.app.toast("抖音登录成功，Cookie 已保存", "success")
            else:
                self.app.toast("未检测到登录（3分钟超时），请重试", "warning")

        run_bg(work, done)

    def _show_captcha_dialog(self):
        """风控验证窗口：提示用户过完验证码后点「继续」。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("验证码")
        dlg.resize(420, 170)
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        tip = QLabel("检测到抖音风控\n请在弹出的浏览器窗口中拖动滑块完成验证码\n完成后点击下方按钮继续连接")
        tip.setAlignment(Qt.AlignCenter)
        v.addWidget(tip)
        cont_btn = QPushButton("✅ 验证完成，继续连接")
        cont_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px;")
        cont_btn.clicked.connect(lambda: self._continue_after_captcha(dlg))
        v.addWidget(cont_btn)
        dlg.show()

    def _continue_after_captcha(self, dlg):
        if self.fetcher:
            self.fetcher.continue_captcha()
        dlg.accept()

    def _on_live_change(self, is_live, info):
        def apply():
            if is_live:
                self.monitor_status.setText(f"🔴 已开播 · {info.get('anchor', '')}")
                self.monitor_status.setStyleSheet("color: #ff5252; font-size: 11px;")
                self.app.toast(f"主播 {info.get('anchor', '')} 已开播！", "success")
            else:
                self.monitor_status.setText("⚫ 未开播")
                self.monitor_status.setStyleSheet("color: #888; font-size: 11px;")
        post_to_main(apply)

    # ======================== 真实数据回调 ========================
    def _on_room_info(self, info):
        """后台线程回调：收到房间信息。"""
        def apply():
            if info.get("anchor"):
                self.anchor_fields["anchor_name"].setText(info["anchor"])
            if info.get("fans"):
                self.anchor_fields["anchor_fans"].setText(info["fans"])
            if info.get("online"):
                self.anchor_fields["online_count"].setText(info["online"])
            if info.get("total_viewers"):
                self.anchor_fields["total_viewers"].setText(info["total_viewers"])
            if info.get("like_count"):
                self.anchor_fields["like_count"].setText(info["like_count"])
            if info.get("title"):
                self._append(self.event_box, f"[房间] {info['title']}")
        post_to_main(apply)

    def _on_message(self, name, text):
        """后台线程回调：收到一条真实消息（text 已是 '[昵称]：内容' 格式）。"""
        t = datetime.now()
        ts = t.strftime("%H:%M:%S") + f".{t.microsecond // 1000:03d}"

        def apply():
            if name == "礼物":
                if self.gift_var.isChecked():
                    self._append(self.chat_gift_box, f"{ts} {text}")
            elif name in ("弹幕", "表情弹幕"):
                if self.chat_var.isChecked():
                    self._append(self.chat_gift_box, f"{ts} {text}")
            elif name == "进场":
                if self.enter_var.isChecked():
                    self._append(self.event_box, f"{ts} 💚 {text}")
            elif name == "点赞":
                self._like_count += 1
                self.like_label.setText(f"{self._like_count:,}")
                self.anchor_fields["like_count"].setText(f"{self._like_count:,}")
                if self.like_var.isChecked():
                    self._append(self.event_box, f"{ts} 👍 {text}")
            elif name == "关注":
                if self.log_var.isChecked():
                    self._append(self.event_box, f"{ts} ⭐ {text}")
            else:
                if self.log_var.isChecked():
                    self._append(self.event_box, f"{ts} [{name}] {text}")
        post_to_main(apply)

    def _on_status(self, status, msg):
        """后台线程回调：连接状态变化。"""
        def apply():
            if status == "connected":
                self.status_dot.setStyleSheet("color: #4caf50; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #4caf50;")
            elif status == "connecting":
                self.status_dot.setStyleSheet("color: #eab308; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #eab308;")
            elif status == "captcha":
                # 风控：弹出验证窗口 + 继续按钮，用户过完验证码点继续
                self.status_dot.setStyleSheet("color: #ff9800; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #ff9800;")
                self._show_captcha_dialog()
            elif status == "error":
                self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #ff5252;")
                self.connected = False
                self._timer_running = False
                self.connect_btn.setEnabled(True)
                self.connect_btn2.setEnabled(True)
                self.disconnect_btn.setEnabled(False)
            elif status == "disconnected":
                self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #888;")
                self.connected = False
                self._timer_running = False
                self.connect_btn.setEnabled(True)
                self.connect_btn2.setEnabled(True)
                self.disconnect_btn.setEnabled(False)
        post_to_main(apply)

    def _tick(self):
        if not self.connected:
            self._timer_running = False
            return
        elapsed = int(time.time() - self._start_ts)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")
        # 左侧时间实时同步（北京时间）
        self.now_label.setText(time.strftime("%Y-%m-%d %H:%M:%S"))
        QTimer.singleShot(1000, self._tick)

    def _clear_all(self):
        """总清除：清空中间 + 右侧两个弹幕框。"""
        self.chat_gift_box.setPlainText("")
        self.event_box.setPlainText("")

    def _append(self, box, text):
        box.appendPlainText(text)
        if self.settings.get("keep_scroll", True):
            sb = box.verticalScrollBar()
            sb.setValue(sb.maximum())
