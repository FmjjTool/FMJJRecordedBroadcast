# -*- coding: utf-8 -*-
"""抖音录播姬页：直播间 FLV 拉流录制（ffmpeg -c copy 无损转存）。

初版录播姬（原弹幕姬内置录制）独立成页：
- 房间号/链接 → 获取房间信息（标题/主播/开播状态/清晰度列表）
- 画质下拉（默认最高）→ ⏺ 录制；录制中显示时长/文件大小/输出路径
- 未开播自动轮询：开播后自动录制，下播自动停止并回到等待（学习
  BililiveRecorder 的房间监控-自动录制模型）
"""

import os
import re
import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

DEFAULT_ROOM = "421527298234"
QUALITY_NAMES = {"FULL_HD1": "1080P", "HD1": "720P", "SD1": "540P",
                 "SD2": "360P", "LD": "360P", "_origin": "原画", "_md": "蓝光"}
QUALITY_ORDER = {"_origin": 100, "_md": 95, "FULL_HD1": 90, "UHD": 85, "HD1": 80,
                "SD1": 70, "SD2": 60, "LD": 50}


def _parse_room(text: str) -> str:
    """输入清洗：纯数字房间号 / live.douyin.com 链接（含短号）。"""
    text = (text or "").strip()
    m = re.search(r"live\.douyin\.com/(\w+)", text)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)$", text)
    if m:
        return m.group(1)
    return text


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


class DouyinRecorderPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._room_info = None
        self._streams = {}
        self._fetching = False
        self.recorder = None
        self._recording = False
        self._auto_mode = False      # 自动录制链路（轮询开播/断流续录）
        self._manual_stop = False    # 手动停止后本 场直播 不再自动拉起
        self._poll_timer = None
        self._rec_tick_on = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🔴 抖音录播姬")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel(
            "输入抖音直播间房间号/链接，获取房间信息后录制直播流（FFmpeg 无损转存 FLV）。"
            "勾选「自动录制」后未开播会自动轮询检测，开播即自动开始录制、下播自动停止")
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
        self.quality_combo.setToolTip("录制清晰度（默认最高；拉不到流时换低档试试）")
        self.quality_combo.setMinimumWidth(90)
        info_row.addWidget(self.quality_combo)
        layout.addLayout(info_row)

        ctrl_row = QHBoxLayout()
        self.record_btn = QPushButton("⏺ 开始录制")
        self.record_btn.setStyleSheet("background: #c62828; color: white;")
        self.record_btn.setToolTip("录制选中清晰度的直播流（FFmpeg -c copy 无损转存）")
        self.record_btn.clicked.connect(self._toggle_record)
        self.record_btn.setEnabled(False)
        ctrl_row.addWidget(self.record_btn)
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
        """获取房间信息（游客态，不占用 Cookie）。auto=轮询链路（允许自动录制）。"""
        if self._fetching:
            return
        room = _parse_room(self.room_entry.text())
        if not room:
            room = DEFAULT_ROOM
            self.room_entry.setText(room)
        if not room.isdigit():
            self.app.toast("请输入有效的抖音房间号/直播间链接", "warning")
            return
        self._fetching = True
        self.fetch_btn.setEnabled(False)
        self._log("检测直播间中..." if auto else f"获取房间 {room} 信息...")

        def job():
            from toolbox.douyin_live import fetch_room_info
            return fetch_room_info(room, login=False)

        def done(info, err):
            self._fetching = False
            self.fetch_btn.setEnabled(True)
            if err or not info:
                self._log(f"❌ 获取失败: {err or '无返回'}")
                self._set_state("获取失败", "#ff5252")
                self._after_fetch(None, auto)
                return
            self._room_info = info
            self._render_info(info)
            self._after_fetch(info, auto)
        run_bg(job, done)

    def _render_info(self, info):
        anchor = info.get("anchor") or "—"
        t = info.get("title") or "—"
        status = int(info.get("status") or 0)
        state_txt = {2: "🔴 直播中", 4: "🔒 轮播中"}.get(status, "⚪ 未开播")
        self.info_lbl.setText(f"主播: {anchor} ｜ 标题: {t} ｜ 状态: {state_txt}")
        streams = info.get("streams") or {}
        self._streams = streams
        keys = sorted(streams, key=lambda k: -QUALITY_ORDER.get(k, 0))
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        for q in keys:
            self.quality_combo.addItem(QUALITY_NAMES.get(q, q), q)
        self.quality_combo.blockSignals(False)
        self.record_btn.setEnabled(bool(keys) or self._recording)
        self._log(f"✅ {anchor} · {t} · {state_txt} · 可用清晰度 {len(keys)} 个")

    def _after_fetch(self, info, auto):
        """获取结果联动：自动录制 / 轮询启停。"""
        if info is None:
            self._setup_poll()
            return
        live = int(info.get("status") or 0) == 2
        if live:
            if not self._recording and auto and not self._manual_stop:
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
        if self._recording:
            self._manual_stop = True   # 手动停止：本场直播不再自动拉起
            self._auto_mode = False
            self._stop_recording()
        else:
            self._manual_stop = False
            self._auto_mode = False
            self._start_recording()

    def _pick_stream(self) -> str:
        if not self._streams:
            return ""
        q = self.quality_combo.currentData()
        if q and q in self._streams:
            return self._streams[q]
        best = sorted(self._streams, key=lambda k: -QUALITY_ORDER.get(k, 0))[0]
        return self._streams[best]

    def _start_recording(self):
        url = self._pick_stream()
        if not url:
            self.app.toast("无可用直播流（请先「获取房间」且主播开播）", "warning")
            self._setup_poll()
            return
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir().parent / "直播录制" / "抖音录播姬"
        d.mkdir(parents=True, exist_ok=True)
        room = self.room_entry.text().strip() or DEFAULT_ROOM
        anchor = (self._room_info or {}).get("anchor") or ""
        base = re.sub(r'[\\/:*?"<>|]', "_", "_".join(x for x in (room, anchor) if x))[:60]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(d / f"{base}_{ts}.flv")
        n = 1
        while os.path.exists(path):
            path = str(d / f"{base}_{ts}_{n}.flv")
            n += 1

        from toolbox.live_video import StreamRecorder
        self.recorder = StreamRecorder()
        if not self.recorder.start(url, path):
            self.recorder = None
            self.app.toast("录制启动失败（FFmpeg 未找到）", "error")
            self._setup_poll()
            return
        self._recording = True
        self._stop_poll()
        self.record_btn.setEnabled(True)
        self.record_btn.setText("⏹ 停止录制")
        self._set_state("🔴 录制中", "#c62828")
        self.path_lbl.setText(f"输出: {path}")
        self._log(f"⏺ 开始录制 → {path}")
        if not self._rec_tick_on:
            self._rec_tick_on = True
            self._tick_record()

    def _stop_recording(self, quiet=False):
        self._recording = False
        path = None
        start_ts = 0
        if self.recorder:
            start_ts = self.recorder.start_ts
            path = self.recorder.path
            self.recorder.stop()
            self.recorder = None
        self.record_btn.setText("⏺ 开始录制")
        self.record_btn.setEnabled(bool(self._streams))
        size = _fmt_size(_file_size(path))
        if path and _file_size(path) < 1024:
            try:
                os.remove(path)   # 空文件清理
            except OSError:
                pass
        secs = int((time.time() * 1000 - start_ts) / 1000) if start_ts else 0
        if not quiet:
            self._log(f"⏹ 停止录制（时长 {_fmt_secs(secs)}，{size}）")
            self._set_state(f"已停止（本场 {_fmt_secs(secs)}，{size}）", "#888")
        self._setup_poll()

    def _tick_record(self):
        """录制中每秒刷新时长/大小；FFmpeg 进程退出 → 断流处理。"""
        if not self._recording:
            self._rec_tick_on = False
            return
        if self.recorder and not self.recorder.is_running():
            # 流断开（下播/网络断）：自动模式立即重试，否则回到待机
            self._log("直播流中断")
            self._stop_recording(quiet=True)
            if self._auto_mode and self.auto_chk.isChecked():
                self._set_state("流中断，自动重试中...", "#eab308")
                self._fetch(auto=True)
            else:
                self._set_state("录制已停止（直播流中断）", "#888")
            return
        if self.recorder:
            secs = int((time.time() * 1000 - self.recorder.start_ts) / 1000)
            size = _fmt_size(_file_size(self.recorder.path))
            self._set_state(f"🔴 录制中 {_fmt_secs(secs)} · {size}", "#c62828")
        QTimer.singleShot(1000, self._tick_record)

    # ==================== 其他 ====================

    def _open_dir(self):
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir().parent / "直播录制" / "抖音录播姬"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: PTH110 — Windows 资源管理器

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
        """软件退出：停录制（FFmpeg 优雅收尾）。"""
        if self._recording:
            self._stop_recording(quiet=True)
        self._stop_poll()
