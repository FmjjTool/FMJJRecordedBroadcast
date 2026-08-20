"""抖音弹幕实时数据接入页（PySide6 版，对应原版 gui/test_page.py）。

三栏布局：左主播信息 / 中弹幕聊天礼物 / 右5块垂直（房间号/WS/sessionid/打赏榜/弹幕信息2）。
- 顶栏：Cookie 登录 / 轮询 / 设置 / 连接 / 断开 / 清空弹幕
- 工具栏：保存弹幕 / 保存统计 / 开播监控 / 登录抖音
- 底栏：状态点 + 状态文本 + 计时 + 本场点赞
- 真实抓取：DouyinLiveFetcher / StatsCollector / LiveMonitor
"""

import atexit
import html
import json
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSlider, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage

# 回放包解压出的临时目录（recording.flv），进程退出兜底清理
_replay_tmp_dirs = []


@atexit.register
def _cleanup_replay_tmp():
    for d in list(_replay_tmp_dirs):
        shutil.rmtree(d, ignore_errors=True)


class TestPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.connected = False
        self._timer_running = False
        self._start_ts = None
        self._like_count = 0
        self._gift_rank = {}
        self.fetcher = None
        self.stats_collector = None
        self.live_monitor = None
        self._monitoring = False
        self.settings = self._load_settings()
        # 视频预览 / 录制 / 模式 / 回放
        self.mode = "normal"            # normal / video_only / danmaku_only / replay
        self.video_player = None
        self.recorder = None
        self._recording = False
        self._streams = {}              # 清晰度 → FLV URL
        self._current_stream = None
        self._quality_manual = False    # 用户手动切换过清晰度（否则始终默认最高）
        self._session_casts = []        # 本场消息快照（含毫秒时间戳，用于导出回放包）
        self._replay = None             # 回放状态 dict
        self._replay_timer = None
        self._auto_stopped = False      # 因切走页面被自动断开（切回时自动重连）
        # 轮询：按设置间隔反复重访直播间（断开→重连，保留本场数据）
        self._poll_timer = None
        self._polling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        # 标题
        outer.addWidget(QLabel("🎬 抖音弹幕 · 实时数据接入",
                               styleSheet="font-size: 18px; font-weight: bold;"))
        outer.addWidget(QLabel("真实连接抖音直播间，实时抓取弹幕 / 礼物 / 进场 / 点赞 / 关注",
                               styleSheet="color: #888; font-size: 11px;"))

        # ===== 顶栏：全局控制 =====
        # Cookie 说明：凭证统一由侧栏「Cookie 管理」维护（总开关+账号池），
        # 连接时自动套用其中的抖音 Cookie，本页不再单独提供登录入口。
        top = QFrame()
        top.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(10, 6, 10, 6)
        self.poll_var = QCheckBox("轮询")
        self.poll_var.setChecked(True)
        self.poll_var.setToolTip(
            "轮询：按设置中的「轮询间隔」反复访问直播间（断开→重连），\n"
            "刷新房间信息与直播流，本场弹幕/统计/计时保持连续。")
        self.poll_var.stateChanged.connect(lambda _s: self._setup_poll_timer())
        top_l.addWidget(self.poll_var)
        # 模式切换：正常（视频+弹幕）/ 仅视频 / 仅弹幕 / 回放
        top_l.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("正常模式", "normal")
        self.mode_combo.addItem("仅视频模式", "video_only")
        self.mode_combo.addItem("仅弹幕模式", "danmaku_only")
        self.mode_combo.addItem("回放模式", "replay")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_l.addWidget(self.mode_combo)
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
        export_btn = QPushButton("📦 导出回放包")
        export_btn.clicked.connect(self._export_replay_zip)
        tb.addWidget(export_btn)
        import_btn = QPushButton("📂 导入回放包")
        import_btn.clicked.connect(self._pick_replay_zip)
        tb.addWidget(import_btn)
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
        cookie_hint = QLabel("Cookie 由侧栏「Cookie 管理」统一提供（自动套用）",
                             styleSheet="color: #666; font-size: 11px;")
        tb.addWidget(cookie_hint)
        outer.addWidget(toolbar)

        # ===== 主体三栏 =====
        body = QHBoxLayout()
        self._build_anchor_panel(body)
        self._build_center_panel(body)
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
        self.cover_label.setFixedSize(96, 96)
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
        for txt in ("抖音：歆慕", "微博：歆慕XINMU", "b站：歆慕XINMU"):
            v.addWidget(QLabel(txt, styleSheet="color: #888; font-size: 11px;"))
        v.addWidget(QLabel("使用信息请标明来源", styleSheet="color: #666; font-size: 10px;"))
        v.addStretch(1)
        parent.addWidget(card, 1)

    # ======================== 中：视频预览 + 弹幕信息 ========================
    def _build_center_panel(self, parent):
        wrap = QVBoxLayout()
        wrap.setSpacing(4)

        # ---- 视频卡片 ----
        self.video_card = QFrame()
        self.video_card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        vv = QVBoxLayout(self.video_card)
        vv.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.video_title = QLabel("📺 直播预览（仅播放，不录制）",
                                  styleSheet="font-weight: bold;")
        head.addWidget(self.video_title)
        head.addStretch(1)
        self.video_status_lbl = QLabel("", styleSheet="color: #888; font-size: 11px;")
        head.addWidget(self.video_status_lbl)
        vv.addLayout(head)

        self.video_label = QLabel("等待连接…")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumHeight(260)
        self.video_label.setStyleSheet(
            "background: #000; border-radius: 4px; color: #666; font-size: 14px;")
        vv.addWidget(self.video_label, 1)

        # 直播控制行：清晰度 + 录播
        self.live_ctrl_row = QHBoxLayout()
        self.live_ctrl_row.addWidget(QLabel("清晰度:", styleSheet="color: #888;"))
        self.quality_combo = QComboBox()
        self.quality_combo.setMinimumWidth(110)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self.live_ctrl_row.addWidget(self.quality_combo)
        self.record_btn = QPushButton("⏺ 录播")
        self.record_btn.setStyleSheet("background: #8e24aa; color: white;")
        self.record_btn.clicked.connect(self._toggle_record)
        self.live_ctrl_row.addWidget(self.record_btn)
        self.rec_time_lbl = QLabel("", styleSheet="color: #e91e63; font-size: 11px;")
        self.live_ctrl_row.addWidget(self.rec_time_lbl)
        self.live_ctrl_row.addStretch(1)
        self.live_ctrl_row_widget = self._wrap_h(self.live_ctrl_row)
        vv.addWidget(self.live_ctrl_row_widget)

        # 回放控制行：播放/暂停 + 进度条 + 时间 + 倍速
        self.replay_row = QHBoxLayout()
        self.replay_btn = QPushButton("⏸")
        self.replay_btn.setFixedWidth(40)
        self.replay_btn.clicked.connect(self._toggle_replay_pause)
        self.replay_row.addWidget(self.replay_btn)
        self.replay_slider = QSlider(Qt.Horizontal)
        self.replay_slider.setRange(0, 0)
        self.replay_slider.sliderReleased.connect(self._on_replay_slider_released)
        self.replay_row.addWidget(self.replay_slider, 1)
        self.replay_time_lbl = QLabel("00:00 / 00:00",
                                      styleSheet="color: #888; font-family: Consolas;")
        self.replay_row.addWidget(self.replay_time_lbl)
        self.replay_row.addWidget(QLabel("倍速:", styleSheet="color: #888;"))
        self.replay_speed = QComboBox()
        for s in ("1.0", "2.0", "4.0", "8.0", "16.0"):
            self.replay_speed.addItem(f"{s}x", float(s))
        self.replay_speed.currentIndexChanged.connect(self._on_replay_speed_changed)
        self.replay_row.addWidget(self.replay_speed)
        self.replay_row_widget = self._wrap_h(self.replay_row)
        self.replay_row_widget.hide()
        vv.addWidget(self.replay_row_widget)

        wrap.addWidget(self.video_card, 3)

        # ---- 弹幕卡片（聊天+礼物）----
        self.chat_card = QFrame()
        self.chat_card.setStyleSheet("QFrame { background: #1a1d27; border-radius: 6px; }")
        v = QVBoxLayout(self.chat_card)
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
        self.chat_gift_box = QTextBrowser()
        self.chat_gift_box.setOpenExternalLinks(False)
        self.chat_gift_box.setStyleSheet(
            "QTextBrowser { background: #14161f; border: none; color: #d7dae0; }")
        v.addWidget(self.chat_gift_box, 1)
        wrap.addWidget(self.chat_card, 2)

        parent.addLayout(wrap, 2)

    @staticmethod
    def _wrap_h(layout):
        w = QFrame()
        w.setLayout(layout)
        return w

    # ======================== 模式切换 ========================

    def _on_mode_changed(self, index):
        mode = self.mode_combo.itemData(index)
        if mode == self.mode:
            return
        # 回放模式切换出去时停止回放
        if self.mode == "replay" and mode != "replay":
            self._stop_replay()
        self.mode = mode
        self._apply_mode()

    def _apply_mode(self):
        """按当前模式刷新中部布局与视频状态。"""
        normal = self.mode == "normal"
        video_only = self.mode == "video_only"
        danmaku_only = self.mode == "danmaku_only"
        replay = self.mode == "replay"
        # 仅视频隐藏弹幕卡；仅弹幕隐藏视频卡（弹幕卡占满中部）
        self.chat_card.setVisible(not video_only)
        self.video_card.setVisible(normal or video_only or replay)
        self.live_ctrl_row_widget.setVisible(normal or video_only)
        self.replay_row_widget.setVisible(replay)
        if replay:
            self.video_title.setText("🎬 回放（拖入 zip 回放包，弹幕与视频同步）")
            self._stop_live_preview()
            if not self._replay:
                self.video_label.setText("将 zip 回放包拖入窗口\n或点击「📂 导入回放包」")
        elif danmaku_only:
            # 仅弹幕：不拉视频流，弹幕抓取照常
            self._stop_live_preview()
            self._stop_replay()
        else:
            self.video_title.setText("📺 直播预览（仅播放，不录制）")
            self._stop_replay()
            # 已连接且有流 → 自动起预览
            if self.connected and self._streams and not (self.video_player and self.video_player.is_running()):
                self._start_live_preview()
        self._refresh_record_ui()

    # ======================== 直播预览 / 清晰度 / 录播 ========================
    QUALITY_NAMES = {"FULL_HD1": "1080P", "HD1": "720P", "SD1": "540P",
                     "SD2": "360P", "LD": "360P", "_origin": "原画"}
    # 画质优先级：原画最高（录播默认自动录最高流）
    QUALITY_ORDER = {"_origin": 100, "FULL_HD1": 90, "UHD": 85, "HD1": 80,
                     "SD1": 70, "SD2": 60, "LD": 50}

    def _on_room_streams(self, streams):
        """房间信息带回清晰度列表 → 按画质排序填充，默认选中最高并起预览。"""
        self._streams = streams or {}
        keys = sorted(self._streams, key=lambda k: -self.QUALITY_ORDER.get(k, 0))
        # 手动切换过 → 记住用户选择；否则默认最高
        prev = self.quality_combo.currentData() if self._quality_manual else None
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        for q in keys:
            self.quality_combo.addItem(self.QUALITY_NAMES.get(q, q), q)
        idx = self.quality_combo.findData(prev) if prev in self._streams else 0
        self.quality_combo.setCurrentIndex(max(0, idx))
        self.quality_combo.blockSignals(False)
        if keys:
            q = self.quality_combo.currentData() or keys[0]
            self._current_stream = self._streams.get(q)
        if self._streams and self.mode in ("normal", "video_only"):
            self._start_live_preview()

    def _on_quality_changed(self, _index=None):
        """用户手动切换清晰度 → 预览与录制流跟随（除非手动切换，否则始终最高）。"""
        self._quality_manual = True
        q = self.quality_combo.currentData()
        if q and self._streams.get(q):
            self._current_stream = self._streams[q]
        if self.mode in ("normal", "video_only") and self._streams:
            self._start_live_preview()

    def _start_live_preview(self):
        if not self._streams or self.mode == "replay":
            return
        q = self.quality_combo.currentData()
        if not q:
            q = next(iter(self._streams))
        url = self._streams.get(q)
        if not url:
            return
        self._current_stream = url
        from toolbox.live_video import VideoStreamPlayer
        if self.video_player:
            self.video_player.stop()
        self.video_player = VideoStreamPlayer(
            on_frame=self._on_video_frame,
            on_status=self._on_video_status)
        self.video_player.start(url, is_live=True)

    def _stop_live_preview(self):
        if self.video_player:
            self.video_player.stop()
            self.video_player = None

    def _on_video_frame(self, w, h, data):
        img = QImage(data, w, h, w * 3, QImage.Format_RGB888)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img).scaled(
            self.video_label.width(), self.video_label.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.video_label.setPixmap(pix)

    def _on_video_status(self, state, detail):
        if state == "connecting":
            self.video_label.setText(detail or "正在获取直播流...")
            self.video_label.setPixmap(QPixmap())
        elif state == "error":
            self.video_label.setText(detail or "直播流获取失败")
            self.video_label.setPixmap(QPixmap())
        if self.mode == "replay":
            return
        self.video_status_lbl.setText(detail or "")

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        # 录制流跟随清晰度选择：默认最高画质；手动切换过则录切换后的流
        if not self._current_stream and self._streams:
            best = sorted(self._streams, key=lambda k: -self.QUALITY_ORDER.get(k, 0))[0]
            self._current_stream = self._streams[best]
        if not self._current_stream:
            self.app.toast("无可用直播流（需先连接并获取清晰度）", "warning")
            return
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir().parent / "直播录制"
        d.mkdir(parents=True, exist_ok=True)
        room = self.room_entry.text().strip() or "room"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(d / f"{room}_{ts}.flv")
        from toolbox.live_video import StreamRecorder
        self.recorder = StreamRecorder(on_status=lambda s, p: None)
        if not self.recorder.start(self._current_stream, path):
            self.app.toast("录制启动失败（FFmpeg 未找到）", "error")
            self.recorder = None
            return
        self._recording = True
        self._refresh_record_ui()

    def _stop_recording(self, package=True):
        self._recording = False
        path = None
        rec_meta = None
        if self.recorder:
            rec_meta = {"startTimestamp": self.recorder.start_ts,
                        "endTimestamp": int(time.time() * 1000),
                        "file": "recording.flv"}
            self.recorder.stop()
            path = self.recorder.path
            self.recorder = None
        self._refresh_record_ui()
        if package and path and os.path.exists(path) and os.path.getsize(path) > 1024:
            self._export_replay_zip(video_path=path, rec_meta=rec_meta, quiet=True)
        elif package and path:
            try:
                os.remove(path)
            except OSError:
                pass

    def _refresh_record_ui(self):
        if self._recording:
            self.record_btn.setText("⏹ 停止录播")
            self.record_btn.setStyleSheet("background: #c62828; color: white;")
            if not self._rec_timer_on:
                self._rec_timer_on = True
                self._tick_record()
        else:
            self.record_btn.setText("⏺ 录播")
            self.record_btn.setStyleSheet("background: #8e24aa; color: white;")
            self.rec_time_lbl.setText("")

    _rec_timer_on = False

    def _tick_record(self):
        if not self._recording or not self.recorder:
            self._rec_timer_on = False
            return
        secs = int((time.time() * 1000 - self.recorder.start_ts) / 1000)
        self.rec_time_lbl.setText(f"录制中 {secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}")
        QTimer.singleShot(1000, self._tick_record)

    # ======================== 导出 / 导入回放包 ========================
    # dycast castTypeMap 同款分类（method → 分类 TXT 文件名）
    CAST_TXT_TYPES = (
        ("WebcastChatMessage", "弹幕"),
        ("WebcastGiftMessage", "礼物"),
        ("WebcastSocialMessage", "关注"),
        ("WebcastLikeMessage", "点赞"),
        ("WebcastMemberMessage", "进入"),
    )

    @staticmethod
    def _cast_txt_line(c):
        """一条弹幕 → dycast TXT 行（HH:mm:ss.SSS|[昵称]：内容）。"""
        ts = c.get("timestamp") or 0
        time_str = (datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S.%f")[:-3]
                    if ts else "??:??:??.???")
        name = (c.get("user") or {}).get("name") or "unknown"
        if c.get("gift"):
            text = f"送出了 {c['gift'].get('name') or '礼物'} × {c['gift'].get('count') or 1}"
        else:
            text = c.get("content") or ""
        return f"{time_str}|[{name}]：{text}" if text else None

    def _export_replay_zip(self, video_path=None, rec_meta=None, quiet=False):
        """打包为 dycast 全量格式 zip：data.json + 全量弹幕.json + 分类 TXT +
        cover + recording.flv（可被 dycast 与本工具回放直接导入）。"""
        casts = list(self._session_casts)
        if not casts:
            if not quiet:
                self.app.toast("当前没有弹幕数据可打包", "warning")
            return
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir().parent / "回放包"
        d.mkdir(parents=True, exist_ok=True)
        room = self.room_entry.text().strip() or "room"
        anchor = (self.anchor_fields["anchor_name"].text() or "").strip() or "未知主播"
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out = d / f"{anchor}-{room}-{ts}-{len(casts)}条.zip"
        bundle = {
            "_bundle": True,
            "exportTime": datetime.now().isoformat(),
            "roomId": room,
            "nickname": anchor,
            "avatar": "",
            "cover": "",
            "title": "",
            "likeCount": self._like_count,
            "casts": casts,
        }
        has_video = bool(video_path and os.path.exists(video_path)
                         and os.path.getsize(video_path) > 1024)
        if has_video and not rec_meta:
            rec_meta = {"startTimestamp": casts[0].get("timestamp"), "file": "recording.flv"}
        meta_json = json.dumps(rec_meta, ensure_ascii=False, indent=1) if rec_meta else None
        # 分类 TXT（dycast castTypeMap 同款：弹幕/礼物/关注/点赞/进入）
        txt_files = []
        for method, label in self.CAST_TXT_TYPES:
            lines = [ln for c in casts if c.get("method") == method
                     for ln in [self._cast_txt_line(c)] if ln]
            if lines:
                txt_files.append((f"{label}.txt", "\n".join(lines)))
        cover = getattr(self, "_cover_bytes", None)
        vpath = video_path if has_video else None

        def work():
            try:
                with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
                    z.writestr("data.json",
                               json.dumps(bundle, ensure_ascii=False, indent=1))
                    z.writestr("全量弹幕.json",
                               json.dumps(casts, ensure_ascii=False, indent=1))
                    for name, content in txt_files:
                        z.writestr(name, content)
                    if cover:
                        z.writestr("cover.jpg", cover)
                    if vpath:
                        z.write(vpath, "recording.flv")
                        if meta_json:
                            z.writestr("recording_meta.json", meta_json)
                return str(out)
            except Exception as e:
                return f"__error__{e}"

        def done(result, err=None):
            if result and not result.startswith("__error__"):
                self.app.toast(f"回放包已导出: {os.path.basename(result)}", "success")
            else:
                self.app.toast("回放包导出失败", "error")

        run_bg(work, done if not quiet else lambda r, e=None: None)

    def _pick_replay_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择回放包", "", "回放包 (*.zip *.json)")
        if path:
            self.load_replay_zip(path)

    # ======================== 回放引擎（zip 导入 → 弹幕/视频时间同步） ========================

    @staticmethod
    def _fmt_ms(ms):
        s = int(ms // 1000)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    def load_replay_zip(self, path):
        """拖入/选择回放包（zip 或 json）→ 切回放模式并开始同步回放。"""
        if not os.path.isfile(path):
            return
        # 切到回放模式（blockSignals 避免 _on_mode_changed 二次触发 stop）
        if self.mode != "replay":
            idx = self.mode_combo.findData("replay")
            if idx >= 0:
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(idx)
                self.mode_combo.blockSignals(False)
            self.mode = "replay"
            self._apply_mode()

        def work():
            return self._parse_replay_file(path)

        def done(result, err=None):
            if err or not result or not result.get("casts"):
                self.video_label.setText("回放包解析失败\n（未找到 data.json 或 casts 为空）")
                self.app.toast("回放包解析失败", "error")
                return
            self._start_replay(result)

        self.video_label.setText("正在解析回放包…")
        run_bg(work, done)

    def _parse_replay_file(self, path):
        """解析回放包 → dict(casts/bundle/video/rec_meta/zip_dir/cover)，失败返回 None。"""
        try:
            if path.lower().endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    bundle = json.load(f)
                casts = (bundle or {}).get("casts") if isinstance(bundle, dict) else None
                if isinstance(casts, list) and casts:
                    return {"casts": casts, "bundle": bundle}
                return None
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "data.json" not in names:
                    return None
                bundle = json.loads(z.read("data.json").decode("utf-8"))
                casts = bundle.get("casts") if isinstance(bundle, dict) else None
                if not isinstance(casts, list) or not casts:
                    return None
                out = {"casts": casts, "bundle": bundle}
                if "recording_meta.json" in names:
                    try:
                        out["rec_meta"] = json.loads(z.read("recording_meta.json").decode("utf-8"))
                    except Exception:
                        pass
                for n in names:
                    if n.lower().startswith("cover."):
                        out["cover"] = z.read(n)
                        break
                if "recording.flv" in names:
                    # 流式解压录制视频到临时目录（ZIP_STORED 近似纯拷贝）
                    tmp = tempfile.mkdtemp(prefix="replay_")
                    vpath = os.path.join(tmp, "recording.flv")
                    with z.open("recording.flv") as src, open(vpath, "wb") as f:
                        shutil.copyfileobj(src, f, 1024 * 1024)
                    out["video"] = vpath
                    out["zip_dir"] = tmp
                    _replay_tmp_dirs.append(tmp)
                return out
        except Exception:
            return None

    @staticmethod
    def _norm_cast(c):
        """回放包消息（dycast DyMessage 或原始 type/ts 格式）→ 内部 m dict。"""
        if not isinstance(c, dict):
            return {}
        if c.get("type"):
            return dict(c)
        gift = c.get("gift") if isinstance(c.get("gift"), dict) else {}
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        m = {
            "type": c.get("method") or "",
            "ts": c.get("timestamp") or 0,
            "nickname": user.get("name") or "",
            "content": c.get("content") or "",
            "rtf": c.get("rtfContent") or [],
        }
        if gift:
            m["giftName"] = gift.get("name")
            m["giftCount"] = gift.get("count") or 1
            m["giftPrice"] = gift.get("price") or 0
            m["giftIcon"] = gift.get("icon") or ""
        if m["type"] == "WebcastGiftMessage" and not m["content"]:
            m["content"] = f"送出 {m.get('giftName') or '礼物'} x{m.get('giftCount') or 1}"
        return m

    def _start_replay(self, data):
        """初始化回放状态并从 0 开始播放（弹幕与视频时间同步）。"""
        self._stop_replay()
        casts = sorted(
            (c for c in data["casts"]
             if isinstance(c, dict) and (c.get("timestamp") or c.get("ts"))),
            key=lambda c: c.get("timestamp") or c.get("ts"))
        if not casts:
            self.app.toast("回放包中没有带时间戳的消息", "warning")
            return
        first = casts[0].get("timestamp") or casts[0].get("ts")
        last = casts[-1].get("timestamp") or casts[-1].get("ts")
        self._replay = {
            "casts": casts, "first": first,
            "total_ms": max(0, last - first),
            "video": data.get("video"), "rec_meta": data.get("rec_meta"),
            "zip_dir": data.get("zip_dir"),
            "idx": 0, "offset_ms": 0.0, "start_real": time.time(),
            "paused": False,
            "speed": float(self.replay_speed.currentData() or 1.0),
        }
        # 恢复主播信息 + 重置面板
        b = data.get("bundle") or {}
        self._clear_all()
        self._gift_rank = {}
        self._like_count = 0
        self.like_label.setText("0")
        if b.get("nickname"):
            self.anchor_fields["anchor_name"].setText(str(b["nickname"]))
        if b.get("roomId"):
            self.room_entry.setText(str(b["roomId"]))
        cover = data.get("cover")
        if cover:
            pix = QPixmap()
            if pix.loadFromData(cover):
                pix = pix.scaled(96, 96, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                if pix.width() > 96 or pix.height() > 96:
                    pix = pix.copy((pix.width() - 96) // 2, (pix.height() - 96) // 2, 96, 96)
                self.cover_label.setPixmap(pix)
        self.replay_slider.setRange(0, int(self._replay["total_ms"]))
        self.replay_slider.setValue(0)
        self._update_replay_time(0)
        # 视频同步播放（从弹幕零点对应的位置开始）
        if self._replay["video"]:
            from toolbox.live_video import VideoStreamPlayer
            self.video_player = VideoStreamPlayer(
                on_frame=self._on_video_frame, on_status=self._on_video_status)
            self.video_player.start(self._replay["video"], is_live=False,
                                    start_sec=self._video_base_sec(),
                                    speed=self._replay["speed"])
        self.replay_btn.setText("⏸")
        self._replay_timer = QTimer(self)
        self._replay_timer.timeout.connect(self._replay_tick)
        self._replay_timer.start(200)
        note = "（含录制视频，弹幕与视频同步）" if self._replay["video"] else ""
        self.app.toast(f"开始回放：{len(casts)} 条弹幕{note}", "success")

    def _video_base_sec(self):
        """录制起点到第一条弹幕的偏移（秒）：视频内弹幕时间轴的零点。"""
        r = self._replay or {}
        start = (r.get("rec_meta") or {}).get("startTimestamp")
        if start and r.get("first"):
            return max(0.0, (r["first"] - start) / 1000.0)
        return 0.0

    def _replay_tick(self):
        """200ms 心跳：按当前回放位置渲染到期弹幕 + 刷新进度条。"""
        r = self._replay
        if not r or r["paused"]:
            return
        cur = self._replay_cur_ms()
        target = r["first"] + cur
        n = 0
        while r["idx"] < len(r["casts"]) and n < 400:
            c = r["casts"][r["idx"]]
            ts = c.get("timestamp") or c.get("ts") or 0
            if ts > target:
                break
            self._render_cast(c)
            r["idx"] += 1
            n += 1
        self.replay_slider.setValue(int(cur))
        self._update_replay_time(cur)
        if cur >= r["total_ms"] and r["idx"] >= len(r["casts"]):
            self._finish_replay()

    def _render_cast(self, c, rank=True):
        """渲染一条回放消息（显示原始时间戳）。"""
        m = self._norm_cast(c)
        if not m:
            return
        from toolbox.douyin_live import MSG_TYPES
        mtype = m.get("type", "")
        name, _cat = MSG_TYPES.get(mtype, (mtype, "other"))
        nick = m.get("nickname") or ""
        content = m.get("content") or ""
        text = f"[{nick}]：{content}" if nick else content
        ts = m.get("ts") or 0
        t = datetime.fromtimestamp(ts / 1000) if ts else datetime.now()
        self._apply_message(name, text, m, t, rank=rank)

    def _seek_replay(self, target_ms):
        """进度条快进/回退：重建打赏榜 + 渲染之前的消息 + 视频对位重启。"""
        r = self._replay
        if not r:
            return
        target_ms = max(0.0, min(float(target_ms), float(r["total_ms"])))
        target_abs = r["first"] + target_ms
        # target 之前的消息（重建榜全量，面板仅渲染末尾）
        past = []
        idx = len(r["casts"])
        for i, c in enumerate(r["casts"]):
            ts = c.get("timestamp") or c.get("ts") or 0
            if ts > target_abs:
                idx = i
                break
            past.append(c)
        self._clear_all()
        self._gift_rank = {}
        self._like_count = 0
        for c in past:
            m = self._norm_cast(c)
            if m.get("giftName"):
                ts = m.get("ts") or 0
                t = datetime.fromtimestamp(ts / 1000) if ts else datetime.now()
                self._add_gift_rank(m, t, refresh=False)
        self._refresh_gift_rank()
        # 只渲染最近 250 条到面板（与 dycast 一致，避免大量 append 卡顿）
        for c in past[-250:]:
            self._render_cast(c, rank=False)
        r["idx"] = idx
        r["offset_ms"] = target_ms
        r["start_real"] = time.time()
        r["paused"] = False
        # 视频对位重启
        if r.get("video"):
            from toolbox.live_video import VideoStreamPlayer
            if self.video_player:
                self.video_player.stop()
            self.video_player = VideoStreamPlayer(
                on_frame=self._on_video_frame, on_status=self._on_video_status)
            self.video_player.start(r["video"], is_live=False,
                                    start_sec=self._video_base_sec() + target_ms / 1000.0,
                                    speed=r["speed"])
        self.replay_btn.setText("⏸")
        self.replay_slider.setValue(int(target_ms))
        self._update_replay_time(target_ms)
        if not self._replay_timer:
            self._replay_timer = QTimer(self)
            self._replay_timer.timeout.connect(self._replay_tick)
            self._replay_timer.start(200)

    def _toggle_replay_pause(self):
        r = self._replay
        if not r:
            return
        if r["paused"]:
            self._resume_replay()
        else:
            self._pause_replay()

    def _pause_replay(self):
        r = self._replay
        if not r or r["paused"]:
            return
        r["paused"] = True
        r["paused_at"] = self._replay_cur_ms()
        if self._replay_timer:
            self._replay_timer.stop()
        if self.video_player:
            self.video_player.stop()
            self.video_player = None
        self.replay_btn.setText("▶")

    def _resume_replay(self):
        r = self._replay
        if not r or not r["paused"]:
            return
        # 与 dycast 一致：从暂停位置重新调度（含视频对位）
        self._seek_replay(r.get("paused_at") or 0.0)

    def _on_replay_slider_released(self):
        if self._replay:
            self._seek_replay(self.replay_slider.value())

    def _on_replay_speed_changed(self, _index=None):
        r = self._replay
        if not r:
            return
        speed = float(self.replay_speed.currentData() or 1.0)
        r["speed"] = speed
        if not r["paused"]:
            # 与 dycast 一致：倍速变化时从当前位置重新调度
            self._seek_replay(self._replay_cur_ms())

    def _replay_cur_ms(self):
        r = self._replay
        if not r:
            return 0.0
        if r["paused"]:
            return float(r.get("paused_at") or r["offset_ms"])
        cur = r["offset_ms"] + (time.time() - r["start_real"]) * 1000.0 * r["speed"]
        return min(cur, float(r["total_ms"]))

    def _update_replay_time(self, cur_ms):
        total = self._replay["total_ms"] if self._replay else 0
        self.replay_time_lbl.setText(f"{self._fmt_ms(cur_ms)} / {self._fmt_ms(total)}")

    def _finish_replay(self):
        r = self._replay
        if not r:
            return
        r["paused"] = True
        r["paused_at"] = float(r["total_ms"])
        if self._replay_timer:
            self._replay_timer.stop()
        self.replay_btn.setText("▶")
        self.app.toast("回放完成", "success")

    def _stop_replay(self):
        """退出回放模式：停定时器/视频，清理临时目录。"""
        if self._replay_timer:
            self._replay_timer.stop()
            self._replay_timer = None
        if self.video_player:
            self.video_player.stop()
            self.video_player = None
        r, self._replay = self._replay, None
        if r and r.get("zip_dir"):
            shutil.rmtree(r["zip_dir"], ignore_errors=True)
            try:
                _replay_tmp_dirs.remove(r["zip_dir"])
            except ValueError:
                pass
        self.replay_btn.setText("⏸")
        self.replay_slider.setRange(0, 0)
        self.replay_slider.setValue(0)
        self.replay_time_lbl.setText("00:00 / 00:00")

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
        # 恢复上次房间号（进入页面自动重连用）
        _last_room = str(self.settings.get("last_room", "") or "")
        if _last_room:
            self.room_entry.setText(_last_room)
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

        # 4. 累计打赏榜（与弹幕姬一致：排名/昵称/礼物数/价值(抖币)/更新时间，前150名）
        v.addWidget(QLabel("④ 累计打赏榜", styleSheet="font-weight: bold;"))
        self.gift_table = QTableWidget(0, 5)
        self.gift_table.setHorizontalHeaderLabels(["排名", "昵称", "礼物数", "价值(抖币)", "更新时间"])
        self.gift_table.verticalHeader().setVisible(False)
        self.gift_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gift_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.gift_table.setFocusPolicy(Qt.NoFocus)
        self.gift_table.setStyleSheet(
            "QTableWidget { background: #1a1d27; color: #d7dae0; border: none;"
            " gridline-color: #242938; font-size: 11px; }"
            "QTableWidget::item { padding: 2px 4px; }"
            "QHeaderView::section { background: #1a1d27; color: #888; border: none;"
            " border-bottom: 1px solid #242938; padding: 3px 4px; font-size: 11px; }")
        hh = self.gift_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        v.addWidget(self.gift_table, 1)

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
        e_poll = row("轮询间隔(秒):", s.get("poll_interval", 60))
        # 弹幕自动保存说明（保存位置固定为 导出目录/弹幕文件/）
        from toolbox.douyin_live import _danmaku_dir
        v.addWidget(QLabel(
            f"自动保存弹幕到：{_danmaku_dir()}（断开连接 / 退出软件时自动导出）",
            styleSheet="color: #888; font-size: 11px;"))

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
        sw_autosave = QCheckBox("自动保存弹幕（断开连接 / 退出软件时导出到「弹幕文件」目录）")
        sw_autosave.setChecked(bool(s.get("auto_save_danmaku", True)))
        v.addWidget(sw_autosave)

        def save():
            self.settings["monitor_interval"] = int(e_monitor.text() or 30)
            self.settings["stats_interval"] = int(e_stats.text() or 1)
            self.settings["poll_interval"] = max(15, int(e_poll.text() or 60))
            self.settings["room_list"] = room_box.toPlainText().strip()
            self.settings["keep_scroll"] = bool(sw_keep.isChecked())
            self.settings["dark_mode"] = bool(sw_dark.isChecked())
            self.settings["auto_record"] = bool(sw_record.isChecked())
            self.settings["auto_save_danmaku"] = bool(sw_autosave.isChecked())
            self._save_settings()
            # 轮询间隔变更 → 已连接时立即按新间隔重排
            self._setup_poll_timer()
            dlg.accept()
            self.app.toast("设置已保存", "success")

        save_btn = QPushButton("💾 保存")
        save_btn.setStyleSheet("background: #2e7d32; color: white;")
        save_btn.clicked.connect(save)
        v.addWidget(save_btn)
        dlg.exec()

    def _set_cover(self, url):
        """后台下载直播间封面（无封面用主播头像），显示到左栏。"""
        if not url:
            return
        import httpx

        def work():
            try:
                r = httpx.get(url, timeout=15, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
                return r.content if r.status_code == 200 else None
            except Exception:
                return None

        def done(data, err=None):
            if err or not data:
                return
            self._cover_bytes = data
            pix = QPixmap()
            if not pix.loadFromData(data):
                return
            pix = pix.scaled(96, 96, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            if pix.width() > 96 or pix.height() > 96:
                pix = pix.copy((pix.width() - 96) // 2, (pix.height() - 96) // 2, 96, 96)
            self.cover_label.setPixmap(pix)

        run_bg(work, done)

    def _save_cover(self):
        """保存封面图 + 主播数据文本到导出目录。"""
        out_dir = self.app.export_dir if hasattr(self.app, "export_dir") else "."
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            out_dir = "."
        saved = []
        if getattr(self, "_cover_bytes", None):
            path = os.path.join(out_dir, f"{self.room_entry.text().strip() or 'room'}_cover.png")
            try:
                with open(path, "wb") as f:
                    f.write(self._cover_bytes)
                saved.append(os.path.basename(path))
            except OSError:
                pass
        lines = [f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                 f"房间号: {self.room_entry.text().strip()}"]
        for text, key in [("主播", "anchor_name"), ("粉丝数", "anchor_fans"),
                          ("在线观众数", "online_count"), ("累计观看人数", "total_viewers"),
                          ("本场点赞数", "like_count")]:
            lines.append(f"{text}: {self.anchor_fields[key].text()}")
        txt_path = os.path.join(out_dir, f"{self.room_entry.text().strip() or 'room'}_主播数据.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            saved.append(os.path.basename(txt_path))
        except OSError:
            pass
        if saved:
            self.app.toast(f"已保存: {', '.join(saved)} → {out_dir}", "success")
        else:
            self.app.toast("保存失败（封面未加载且数据写入失败）", "error")

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
        # 轮询重访：保留本场弹幕/统计/计时（仅刷新连接）
        keep = self._polling
        self.connected = True
        if not keep:
            self._start_ts = time.time()
            self._like_count = 0
            self._gift_rank = {}
            self._session_casts = []
            self.gift_table.setRowCount(0)
            self.like_label.setText("0")
            self.anchor_fields["like_count"].setText("0")
            # 记住本次房间号（切走自动断开后，切回可自动重连）
            self.settings["last_room"] = room
            self._save_settings()
            # 清空两个弹幕框的初始占位 + 封面/主播占位
            self.chat_gift_box.clear()
            self.event_box.setPlainText("")
            self._cover_bytes = None
            self.cover_label.setText("暂无封面")
            for key in ("anchor_name", "anchor_fans", "online_count", "total_viewers"):
                self.anchor_fields[key].setText("…")
        self.connect_btn.setEnabled(False)
        self.connect_btn2.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.status_dot.setStyleSheet("color: #eab308; font-size: 14px;")
        self.status_label.setText(f"连接中 · 房间 {room}")
        self.status_label.setStyleSheet("color: #eab308;")
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
        # 轮询重连竞态防护：旧抓取线程 stop 后仍会异步发 disconnected/error，
        # 若已是新实例则忽略，避免把新连接状态又改回“已断开”
        cur = self.fetcher
        orig_status = cur.on_status

        def guarded_status(status, msg):
            if self.fetcher is not cur and status in ("disconnected", "error"):
                return
            orig_status(status, msg)

        cur.on_status = guarded_status
        self.fetcher.start()
        # 启动统计数据采集（若开启保存统计）
        if self.save_stats_var.isChecked():
            self._start_stats_collector(room)
        self._setup_poll_timer()

    def _disconnect(self):
        self.connected = False
        self._timer_running = False
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        if self.fetcher:
            self.fetcher.stop()
            self.fetcher = None
        if self.stats_collector:
            self.stats_collector.stop()
            self.stats_collector = None
        # 手动断开：停视频预览（彻底断开房间），且不再自动重连
        if not self._polling:
            self._stop_live_preview()
            self._auto_stopped = False
            self._quality_manual = False
            self.like_label.setText("0")
            self.connect_btn.setEnabled(True)
            self.connect_btn2.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
            self.status_label.setText("已断开")
            self.status_label.setStyleSheet("color: #888;")
            if self.settings.get("auto_save_danmaku", True):
                self._auto_save_danmaku()

    # ======================== 轮询（按设置间隔反复访问直播间） ========================
    def _setup_poll_timer(self):
        """勾选轮询且已连接 → 按设置间隔周期性重访直播间。"""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        if not (self.poll_var.isChecked() and self.connected):
            return
        interval = max(15, int(self.settings.get("poll_interval", 60) or 60))
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_reconnect)
        self._poll_timer.start(interval * 1000)

    def _poll_reconnect(self):
        """轮询到点：断开 → 重连（反复访问直播间，刷新房间信息与直播流）。"""
        if not self.poll_var.isChecked() or not self.connected:
            self._setup_poll_timer()
            return
        self._polling = True
        try:
            self._disconnect()
            self._connect()
        finally:
            self._polling = False
        if self.connected:
            self.app.toast("轮询：已重新访问直播间", "info")

    # ======================== 保存弹幕 / 统计 / 开播监控 ========================
    def _save_danmaku_now(self):
        """点击保存：把当前两个弹幕框的内容一次性导出到文件。"""
        room = self.room_entry.text().strip()
        if not room:
            QMessageBox.warning(self, "提示", "请先输入房间号")
            return
        path = self._auto_save_danmaku()
        if path:
            self.app.toast(f"弹幕已保存到 {path}", "success")
        else:
            self.app.toast("当前没有弹幕数据可保存", "warning")

    def _auto_save_danmaku(self):
        """静默导出弹幕（断开/退出时自动调用），返回文件路径（无数据返回 None）。"""
        room = self.room_entry.text().strip() or "room"
        chat = self.chat_gift_box.toPlainText().strip()
        event = self.event_box.toPlainText().strip()
        if chat == "（暂无弹幕/礼物）":
            chat = ""
        if not chat and not event:
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        lines = []
        if chat:
            lines.append("===== 弹幕/礼物 =====")
            lines.append(chat)
        if event:
            lines.append("===== 进场/点赞/关注/日志 =====")
            lines.append(event)
        try:
            from toolbox.douyin_live import _danmaku_dir
            d = _danmaku_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{room}_{ts}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return str(path)
        except Exception:
            return None

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
            # 直播流清晰度 → 填充下拉并自动起预览
            if info.get("streams"):
                self._on_room_streams(info["streams"])
            # 封面：无封面时退回主播头像
            self._set_cover(info.get("cover") or info.get("avatar"))
        post_to_main(apply)

    def _on_message(self, name, text, m=None):
        """后台线程回调：收到一条真实消息。

        text 为 '[昵称]：内容' 格式；m 为原始字段 dict（礼物价格/点赞总数/
        在线人数/粉丝数等统计字段，与弹幕姬 DyMessage 对齐）。
        """
        m = m or {}
        self._record_cast(name, text, m)
        t = datetime.now()
        post_to_main(lambda: self._apply_message(name, text, m, t))

    def _record_cast(self, name, text, m):
        """记录本场消息快照（dycast DyMessage 格式，供导出回放包）。"""
        if len(self._session_casts) >= 50000:
            self._session_casts = self._session_casts[-25000:]
        c = {
            "method": m.get("type", ""),
            "timestamp": m.get("ts") or int(time.time() * 1000),
            "content": m.get("content") or text,
            "rtfContent": m.get("rtf") or [],
        }
        if m.get("nickname"):
            c["user"] = {"name": m["nickname"]}
        if m.get("giftName"):
            c["gift"] = {"name": m.get("giftName"), "count": m.get("giftCount") or 1,
                         "price": m.get("giftPrice") or 0, "icon": m.get("giftIcon") or ""}
        self._session_casts.append(c)

    def _apply_message(self, name, text, m, t, rank=True):
        """主线程：渲染一条消息到界面（直播与回放共用；rank=False 跳过打赏榜）。"""
        ts = t.strftime("%H:%M:%S") + f".{t.microsecond // 1000:03d}"

        def set_field(key, value):
            self.anchor_fields[key].setText(f"{int(value):,}")

        if name == "礼物":
            # 打赏榜：礼物数累计、价值=数量×抖币价，与弹幕姬一致
            self._add_gift_rank(m, t, refresh=rank)
            if self.gift_var.isChecked():
                self._append_html(self.chat_gift_box, self._gift_html(ts, m))
        elif name in ("弹幕", "表情弹幕"):
            if self.chat_var.isChecked():
                self._append_html(self.chat_gift_box, self._chat_html(ts, m, text))
        elif name == "进场":
            if m.get("memberCount"):
                set_field("online_count", m["memberCount"])
            if self.enter_var.isChecked():
                self._append(self.event_box, f"{ts} 💚 {text}")
        elif name == "点赞":
            # likeTotal 为本场累计点赞总数（弹幕姬行为），缺失时退化为 +1
            if m.get("likeTotal"):
                self._like_count = int(m["likeTotal"])
            else:
                self._like_count += 1
            self.like_label.setText(f"{self._like_count:,}")
            self.anchor_fields["like_count"].setText(f"{self._like_count:,}")
            if self.like_var.isChecked():
                self._append(self.event_box, f"{ts} 👍 {text}")
        elif name == "关注":
            if m.get("followCount"):
                set_field("anchor_fans", m["followCount"])
            if self.log_var.isChecked():
                self._append(self.event_box, f"{ts} ⭐ {text}")
        elif name == "在线人数":
            # WebcastRoomUserSeqMessage：静默更新左栏统计
            if m.get("roomTotal"):
                set_field("online_count", m["roomTotal"])
            if m.get("roomTotalUser"):
                # totalUserStr 为文本（如 "10万+"），原样展示
                self.anchor_fields["total_viewers"].setText(str(m["roomTotalUser"]))
        elif name == "直播数据":
            # WebcastRoomStatsMessage：displayMiddle 如 "1.2万"
            if text:
                self.anchor_fields["online_count"].setText(text)
        else:
            if self.log_var.isChecked() and text:
                self._append(self.event_box, f"{ts} [{name}] {text}")

    def _add_gift_rank(self, m, t, refresh=True):
        """累计打赏榜：按用户累计礼物数与抖币价值（弹幕姬同款逻辑）。
        refresh=False 用于回放 seek 批量重建（末尾统一刷新一次）。"""
        gift_name = m.get("giftName")
        if not gift_name:
            return
        user = (m.get("nickname") or "").strip() or "未知用户"
        try:
            count = int(m.get("giftCount") or 1)
        except (TypeError, ValueError):
            count = 1
        try:
            price = int(m.get("giftPrice") or 0)
        except (TypeError, ValueError):
            price = 0
        cur = self._gift_rank.get(user)
        if cur is None:
            cur = {"giftCount": 0, "totalValue": 0, "lastTime": t}
            self._gift_rank[user] = cur
        cur["giftCount"] += count
        cur["totalValue"] += count * price
        cur["lastTime"] = t
        if refresh:
            self._refresh_gift_rank()

    def _refresh_gift_rank(self):
        items = sorted(self._gift_rank.items(),
                       key=lambda kv: (-kv[1]["totalValue"], -kv[1]["giftCount"], kv[0]))
        self.gift_table.setRowCount(0)
        for i, (user, d) in enumerate(items[:150]):
            self.gift_table.insertRow(i)
            vals = [str(i + 1), user, f"{d['giftCount']:,}",
                    f"{d['totalValue']:,}", d["lastTime"].strftime("%H:%M:%S")]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c != 1:
                    item.setTextAlignment(Qt.AlignCenter)
                self.gift_table.setItem(i, c, item)

    @staticmethod
    def _ts_html(ts):
        return f'<span style="color:#6b7280">{ts}</span>'

    def _chat_html(self, ts, m, text):
        """弹幕行：富文本表情直接内嵌抖音 CDN 图片，无 rtf 退回纯文本。"""
        parts = []
        for p in (m.get("rtf") or []):
            if p.get("url"):
                alt = html.escape(p.get("text") or "表情")
                parts.append(f'<img src="{html.escape(p["url"])}" height="20" alt="[{alt}]">')
            elif p.get("text"):
                parts.append(html.escape(p["text"]))
        body = "".join(parts) or html.escape(m.get("content") or text or "")
        nick = m.get("nickname") or ""
        nick_h = f'<b style="color:#e8eaf0">{html.escape(nick)}：</b>' if nick else ""
        return f"{self._ts_html(ts)} {nick_h}{body}"

    def _gift_html(self, ts, m):
        """礼物行：抖音礼物图标 + 名称 xN。"""
        icon = m.get("giftIcon") or ""
        icon_h = ""
        if icon:
            alt = html.escape(m.get("giftName") or "礼物")
            icon_h = f'<img src="{html.escape(icon)}" height="22" alt="{alt}"> '
        nick = m.get("nickname") or ""
        nick_h = f'<b style="color:#ffd54f">{html.escape(nick)}：</b>' if nick else ""
        body = html.escape(m.get("content") or "")
        return f'{self._ts_html(ts)} 🎁 {nick_h}{icon_h}<span style="color:#ffd54f">{body}</span>'

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
        self.chat_gift_box.clear()
        self.event_box.setPlainText("")

    def _append(self, box, text):
        box.appendPlainText(text)
        if self.settings.get("keep_scroll", True):
            sb = box.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _append_html(self, box, html_str):
        sb = box.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8
        box.append(html_str)
        if self.settings.get("keep_scroll", True) or at_bottom:
            sb.setValue(sb.maximum())

    # ======================== 页面生命周期（全局同步：进自动启 / 出自动停） ========================
    def on_show(self):
        """切到本页：录制中恢复预览；上次因切走被自动断开（且有房间号）→ 自动重连。"""
        if self.connected or self.fetcher:
            # 后台保持运作中（如录制中切走再切回）：恢复视频预览渲染
            if self._streams and self.mode in ("normal", "video_only") \
                    and not (self.video_player and self.video_player.is_running()):
                self._start_live_preview()
            return
        room = self.room_entry.text().strip()
        if room and self._auto_stopped:
            self._auto_stopped = False
            self._connect()

    def on_hide(self):
        """切离本页：录制中保持后台运作（弹幕抓取+录制继续），仅停 UI 渲染；
        未录制则全停（录制自动打包回放包/回放/预览/抓取/监控）。"""
        if self._recording:
            # 录制中切页面：维持弹幕抓取与录制，只停视频预览渲染与回放
            self._stop_replay()
            self._stop_live_preview()
            return
        self.shutdown()

    def shutdown(self):
        """全停（切走且未录制 / 软件退出时调用）：停一切后台任务并自动保存。"""
        was_active = self.connected or self.fetcher or self.video_player or self._recording
        if self._recording:
            self._stop_recording()
        self._stop_replay()
        self._stop_live_preview()
        if self.connected or self.fetcher or self.stats_collector:
            self._disconnect()
        if self._monitoring:
            self._toggle_monitor()
        self._auto_stopped = bool(was_active)
