# -*- coding: utf-8 -*-
"""B站录播姬页：纯 HTTP FLV 流式录制 + 弹幕 XML 同步录制。

学习 BililiveRecorder（github.com/BililiveRecorder/BililiveRecorder）的核心模型：
- 房间监控：轮询 get_info，live_status 1=直播中 0=未开播 2=轮播
- 自动录制：检测到开播自动取流录制，下播自动停止、回到监控等待
- 无 ffmpeg 依赖：httpx 直接流式转存 FLV（engine 见 toolbox/bili_live.py）
- 弹幕：WebSocket 协议（auth/心跳/zlib 解压）→ BililiveRecorder 同款 XML 格式
- 断流自动重连：新文件续录（与 BililiveRecorder 的切割行为一致）
"""

import os
import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage

DEFAULT_ROOM = "6"   # B站直播办公屏（常开，方便测试）


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _fmt_secs(secs: int) -> str:
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _file_size(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class BiliRecorderPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._room_info = None
        self._qn_list = []
        self._fetching = False
        self._starting = False
        self.recorder = None
        self.danmaku = None
        self._recording = False
        self._auto_mode = False      # 自动录制链路（轮询开播/断流续录）
        self._manual_stop = False    # 手动停止后本场直播不再自动拉起
        self._dm_count = 0
        self._gift_count = 0
        self._poll_timer = None
        self._rec_tick_on = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🔴 B站录播姬")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel(
            "输入B站直播间房间号/链接，获取房间信息后录制直播流（纯 HTTP 转存 FLV，无需 "
            "FFmpeg）。勾选「同步录制弹幕」时弹幕/礼物/进场写入 BililiveRecorder 同款 "
            "XML；勾选「自动录制」后未开播自动轮询，开播即自动录制、下播自动停止")
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.room_entry = QLineEdit()
        self.room_entry.setPlaceholderText(f"房间号，如 {DEFAULT_ROOM}")
        self.room_entry.returnPressed.connect(lambda: self._fetch())
        row.addWidget(self.room_entry, 1)
        self.fetch_btn = QPushButton("🔍 获取房间")
        self.fetch_btn.clicked.connect(lambda: self._fetch())
        row.addWidget(self.fetch_btn)
        self.dir_btn = QPushButton("📂 录制目录")
        self.dir_btn.setToolTip("打开本次录制的输出目录")
        self.dir_btn.clicked.connect(self._open_dir)
        row.addWidget(self.dir_btn)
        layout.addLayout(row)

        info_row = QHBoxLayout()
        self.info_lbl = QLabel("主播: — ｜ 标题: — ｜ 状态: 未获取")
        self.info_lbl.setStyleSheet("color: #666;")
        self.info_lbl.setWordWrap(True)
        info_row.addWidget(self.info_lbl, 1)
        self.quality_combo = QComboBox()
        self.quality_combo.setToolTip("录制画质（默认最高）")
        self.quality_combo.setMinimumWidth(90)
        info_row.addWidget(self.quality_combo)
        layout.addLayout(info_row)

        ctrl_row = QHBoxLayout()
        self.record_btn = QPushButton("⏺ 开始录制")
        self.record_btn.setStyleSheet("background: #c62828; color: white;")
        self.record_btn.setToolTip("拉取直播流地址并开始 FLV 流式录制")
        self.record_btn.clicked.connect(self._toggle_record)
        self.record_btn.setEnabled(False)
        ctrl_row.addWidget(self.record_btn)
        self.dm_chk = QCheckBox("同步录制弹幕")
        self.dm_chk.setChecked(True)
        self.dm_chk.setToolTip("录制期间弹幕/礼物/进场同步写入 XML（BililiveRecorder 格式）")
        ctrl_row.addWidget(self.dm_chk)
        self.auto_chk = QCheckBox("自动录制")
        self.auto_chk.setChecked(True)
        self.auto_chk.setToolTip("未开播时自动轮询检测，开播即自动录制；下播自动停止")
        self.auto_chk.stateChanged.connect(self._on_auto_toggle)
        ctrl_row.addWidget(self.auto_chk)
        self.interval_lbl = QLabel("检测间隔(秒):")
        ctrl_row.addWidget(self.interval_lbl)
        self.interval_entry = QLineEdit("60")
        self.interval_entry.setFixedWidth(60)
        self.interval_entry.setToolTip("未开播时自动检测开播的间隔（≥15 秒）")
        ctrl_row.addWidget(self.interval_entry)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

        self.status_lbl = QLabel("待机中")
        self.status_lbl.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #888;")
        layout.addWidget(self.status_lbl)
        self.path_lbl = QLabel("")
        self.path_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self.path_lbl.setWordWrap(True)
        layout.addWidget(self.path_lbl)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    # ==================== 房间获取 ====================

    def _fetch(self, auto=False):
        """获取房间信息 + （直播中时）画质列表。auto=轮询链路（允许自动录制）。"""
        if self._fetching:
            return
        from toolbox.bili_live import parse_room_id_input
        room = parse_room_id_input(self.room_entry.text())
        if not room:
            room = DEFAULT_ROOM
            self.room_entry.setText(room)
        if not room.isdigit():
            self.app.toast("请输入有效的B站房间号/直播间链接", "warning")
            return
        self._fetching = True
        self.fetch_btn.setEnabled(False)
        self._log("检测直播间中..." if auto else f"获取房间 {room} 信息...")

        def job():
            from toolbox.bili_live import fetch_play_url, fetch_room_info
            info = fetch_room_info(room)
            play = fetch_play_url(room) if info.get("live_status") == 1 else None
            return {"info": info, "play": play}

        def done(res, err):
            self._fetching = False
            self.fetch_btn.setEnabled(True)
            if err or not res:
                self._log(f"❌ 获取失败: {err or '无返回'}")
                self._set_state("获取失败", "#ff5252")
                self._after_fetch(None, auto)
                return
            self._room_info = res["info"]
            self._render_info(res["info"], res["play"])
            self._after_fetch(res["info"], auto)
        run_bg(job, done)

    def _render_info(self, info, play):
        uname = info.get("uname") or "—"
        t = info.get("title") or "—"
        status = int(info.get("live_status") or 0)
        state_txt = {1: "🔴 直播中", 2: "📺 轮播中"}.get(status, "⚪ 未开播")
        online = info.get("watch_num") or 0
        self.info_lbl.setText(
            f"主播: {uname} ｜ 标题: {t} ｜ 状态: {state_txt} ｜ 在线 {online}")
        qn_list = (play or {}).get("qn_list") or []
        self._qn_list = qn_list
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        if qn_list:
            cur = (play or {}).get("quality_desc") or ""
            for qn, desc in qn_list:
                self.quality_combo.addItem(desc or str(qn), qn)
            if cur:
                idx = self.quality_combo.findText(cur)
                if idx >= 0:
                    self.quality_combo.setCurrentIndex(idx)
        else:
            self.quality_combo.addItem("原画", 10000)
        self.quality_combo.blockSignals(False)
        self.record_btn.setEnabled(status == 1 or self._recording)
        self._log(f"✅ {uname} · {t} · {state_txt} · 画质档位 {len(qn_list)} 个")

    def _after_fetch(self, info, auto):
        """获取结果联动：自动录制 / 轮询启停。"""
        if info is None:
            self._setup_poll()
            return
        live = int(info.get("live_status") or 0) == 1
        if live:
            if not self._recording and not self._starting and auto \
                    and not self._manual_stop:
                self._auto_mode = True
                self._log("检测到开播，自动开始录制")
                self._start_recording()
                if not self._recording:
                    self._setup_poll()
            elif not self._recording:
                self._set_state("直播中（点「开始录制」手动录制）", "#eab308")
                self._stop_poll()
            else:
                self._stop_poll()
        else:
            # 下播：手动停止标志复位，下一场直播可再次自动录制
            self._manual_stop = False
            if not self._recording and self.danmaku:
                self.danmaku.stop()
                self.danmaku = None
            self._set_state("未开播，自动检测中" if self.auto_chk.isChecked()
                            else "未开播", "#888")
            self._setup_poll()

    # ==================== 轮询（未开播检测） ====================

    def _poll_interval(self) -> int:
        try:
            return max(15, int(self.interval_entry.text() or 60))
        except ValueError:
            return 60

    def _on_auto_toggle(self, _state=None):
        if self.auto_chk.isChecked():
            self._setup_poll()
        else:
            self._stop_poll()

    def _setup_poll(self):
        """未开播 + 自动录制勾选 → 启动轮询定时器。"""
        if self._recording or not self.auto_chk.isChecked():
            self._stop_poll()
            return
        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(lambda: self._fetch(auto=True))
        if not self._poll_timer.isActive():
            self._poll_timer.start(self._poll_interval() * 1000)

    def _stop_poll(self):
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer.deleteLater()
            self._poll_timer = None

    # ==================== 录制 ====================

    def _toggle_record(self):
        if self._recording or self._starting:
            self._manual_stop = True   # 手动停止：本场直播不再自动拉起
            self._auto_mode = False
            self._stop_recording()
        else:
            self._manual_stop = False
            self._auto_mode = False
            self._start_recording()

    def _start_recording(self):
        """取流（带画质）→ 后台拉直链 → 启动 FLV 转存（+弹幕录制）。"""
        if self._starting:
            return
        room = self.room_entry.text().strip() or DEFAULT_ROOM
        qn = self.quality_combo.currentData()
        self._starting = True
        self.record_btn.setEnabled(False)

        def job():
            from toolbox.bili_live import fetch_play_url
            return fetch_play_url(room, qn)

        def done(play, err):
            self._starting = False
            self._start_segment(play, err, room)
        run_bg(job, done)

    def _start_segment(self, play, err, room):
        if err or not play or not play.get("flv"):
            msg = (play or {}).get("error") or err or "未获取到直播流"
            self._log(f"❌ 取流失败: {msg}")
            self._set_state("取流失败", "#ff5252")
            self.record_btn.setEnabled(bool(self._qn_list))
            self._setup_poll()
            return
        from toolbox.bili_live import BiliFlvRecorder
        title = (self._room_info or {}).get("title") or ""
        uname = (self._room_info or {}).get("uname") or ""
        label = f"{room}_{uname}" if uname else room
        self.recorder = BiliFlvRecorder(on_event=self._on_flv_event)
        self.recorder.start(play["flv"], label, title)
        self._recording = True
        self._stop_poll()
        fresh_dm = self.danmaku is None or self.danmaku._thread is None
        if fresh_dm:
            self._dm_count = 0
            self._gift_count = 0
        self.record_btn.setEnabled(True)
        self.record_btn.setText("⏹ 停止录制")
        self._set_state("🔴 录制中", "#c62828")
        self._log(f"⏺ 开始录制（{play.get('quality_desc') or ''}）→ {self.recorder.path}")
        self.path_lbl.setText(
            f"输出: {self.recorder.path}"
            + (f" ｜ 弹幕: {self.danmaku.path}" if self.danmaku else ""))
        if self.dm_chk.isChecked() and fresh_dm:
            self._start_danmaku(room)
        if not self._rec_tick_on:
            self._rec_tick_on = True
            self._tick_record()

    def _start_danmaku(self, room):
        from toolbox.bili_live import BiliDanmakuRecorder
        uid = (self._room_info or {}).get("uid") or 0
        self.danmaku = BiliDanmakuRecorder(room, uid, on_event=self._on_dm_event)
        self.danmaku.start()
        self._log(f"💬 弹幕录制 → {self.danmaku.path}")

    def _stop_recording(self, quiet=False):
        self._recording = False
        start_ts = 0
        size = 0
        path = None
        if self.recorder:
            start_ts = self.recorder.start_ts
            path = self.recorder.path
            size = self.recorder.bytes_written
            self.recorder.stop()
            self.recorder = None
        if self.danmaku:
            self.danmaku.stop()
            self.danmaku = None
        self.record_btn.setText("⏺ 开始录制")
        self.record_btn.setEnabled(bool(self._qn_list))
        secs = int((time.time() * 1000 - start_ts) / 1000) if start_ts else 0
        if not quiet:
            self._log(f"⏹ 停止录制（时长 {_fmt_secs(secs)}，{_fmt_size(size)}，"
                      f"弹幕 {self._dm_count} 条/礼物 {self._gift_count}）")
            self._set_state(
                f"已停止（本场 {_fmt_secs(secs)}，{_fmt_size(size)}，"
                f"弹幕 {self._dm_count}）", "#888")
        self._setup_poll()

    def _tick_record(self):
        """录制中每秒刷新时长/大小/弹幕计数。"""
        if not self._recording:
            self._rec_tick_on = False
            return
        if self.recorder:
            secs = int((time.time() * 1000 - self.recorder.start_ts) / 1000)
            size = _fmt_size(self.recorder.bytes_written
                             or _file_size(self.recorder.path))
            self._set_state(
                f"🔴 录制中 {_fmt_secs(secs)} · {size} · 弹幕 {self._dm_count}"
                + (f" · 礼物 {self._gift_count}" if self._gift_count else ""),
                "#c62828")
        QTimer.singleShot(1000, self._tick_record)

    # ---------- 引擎事件（后台线程 → 主线程） ----------

    def _on_flv_event(self, ev, detail=""):
        post_to_main(self._handle_flv_event, ev, detail)

    def _handle_flv_event(self, ev, detail=""):
        if ev == "recording":
            return   # 起始事件（_start_segment 已处理）
        if ev == "error":
            self._log(f"⚠️ 流错误: {detail}")
            return
        if ev != "stopped":
            return
        if not self._recording:
            return   # 手动停止的收尾事件
        # 意外断流：自动模式 → 立即重试续录（新文件段）；否则回待机
        self._recording = False
        self.record_btn.setText("⏺ 开始录制")
        if self._auto_mode and self.auto_chk.isChecked():
            self._log("直播流中断，自动重连续录...")
            self._set_state("流中断，自动重连中...", "#eab308")
            self._fetch(auto=True)
        else:
            self._set_state("录制已停止（直播流中断）", "#888")
            self._setup_poll()

    def _on_dm_event(self, ev, detail=""):
        if ev == "danmaku":
            self._dm_count += 1   # WS 线程内仅计数（GIL 下安全），UI 由秒级定时器刷新
        elif ev == "gift":
            self._gift_count += 1
        elif ev in ("stopped", "error"):
            post_to_main(self._log, f"💬 弹幕录制{ev}: {detail}")

    # ==================== 其他 ====================

    def _open_dir(self):
        from toolbox.bili_live import _rec_dir
        os.startfile(str(_rec_dir()))  # noqa: PTH110 — Windows 资源管理器

    def _set_state(self, text, color):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def on_hide(self):
        """切走页面：录制后台继续（录播姬核心诉求）。"""

    def shutdown(self):
        """软件退出：停 FLV 转存与弹幕录制。"""
        if self._recording:
            self._stop_recording(quiet=True)
        self._stop_poll()
