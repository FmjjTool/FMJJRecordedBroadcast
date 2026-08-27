"""抖音弹幕实时数据接入页（PySide6 版，对应原版 gui/test_page.py）。

三栏布局：左主播信息（首行直播标题）/ 中弹幕聊天礼物 / 右4块垂直（房间号/WS/打赏榜/弹幕信息2）。
- 顶栏：轮询 / 模式 / 设置 / 清空内容 / 启动弹幕姬 / 连接 / 断开
- 工具栏：保存弹幕 / 导出导入回放包 / 保存统计 / 开播监控
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
    QTextBrowser, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage

# 回放包解压出的临时目录（recording.flv），进程退出兜底清理
_replay_tmp_dirs = []

# ==================== 表情/礼物图标本地缓存 ====================
# QTextBrowser 不会自动加载远程图片（<img src="https://..."> 只显示占位纸片图标），
# 需先下载到本地缓存再以本地路径内嵌；同一表情/图标反复出现直接套用缓存（零延迟）。
_IMG_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
_img_local = {}      # url -> Path（会话内映射，后台线程写/主线程读）
_img_miss = set()    # 目录里确认没有的 url（避免重复 glob）


def _webcast_img_dir():
    from pathlib import Path
    try:
        from toolbox.config_manager import get_config_dir
        d = get_config_dir() / "cache" / "webcast_img"
    except Exception:
        d = Path(__file__).resolve().parent.parent.parent / "config" / "cache" / "webcast_img"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _img_key(url):
    import hashlib
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _sniff_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:2] == b"BM":
        return ".bmp"
    return ".bin"


def img_cache_lookup(url):
    """查本地缓存路径（无缓存返回 None）。"""
    p = _img_local.get(url)
    if p and p.exists():
        return p
    if url in _img_miss:
        return None
    try:
        for cand in _webcast_img_dir().glob(_img_key(url) + ".*"):
            if cand.is_file() and cand.stat().st_size > 0:
                _img_local[url] = cand
                return cand
    except Exception:
        pass
    _img_miss.add(url)
    return None


def img_cache_download(url):
    """后台线程下载一张图到缓存（带 Referer 防盗链），成功返回本地路径。"""
    import httpx
    try:
        r = httpx.get(url, timeout=8, follow_redirects=True,
                      headers={"User-Agent": _IMG_UA, "Referer": "https://live.douyin.com/"})
        if r.status_code == 200 and r.content:
            p = _webcast_img_dir() / (_img_key(url) + _sniff_ext(r.content))
            p.write_bytes(r.content)
            _img_local[url] = p
            _img_miss.discard(url)
            return p
    except Exception:
        pass
    return None


def _img_src(url):
    """url → 本地缓存 posix 路径（供 <img src>），无缓存返回 None。"""
    p = img_cache_lookup(url)
    return p.as_posix() if p else None


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
        self._gift_sessions = {}
        self._gift_sessions = {}   # (user, gift) → {settled, t}：连击会话已结算数
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
        self._waiting_live = False      # 未开播/已下播（轮询开着则等开播自动接入）
        self._manual_stop = False       # 手动断开（下播后的 disconnected 不自动进入等待）
        self._rec_switched_away = False  # 录播中切走页面（切回时重新开始录制）
        self._rec_resume_pending = False  # 切回时未连上 → 待拿到直播流自动重开录制
        # 轮询：按设置间隔反复重访直播间（断开→重连，保留本场数据）
        self._poll_timer = None
        # 表情/礼物图标：待图片缓存就绪的富文本消息队列（保序）+ 后台下载状态
        self._img_pending = []      # [{box, urls, build, exp}]
        self._img_fetching = False
        self._img_tried = set()     # 本会话已尝试下载过的 URL（失败不重试风暴）
        # 弹幕搜索：完整聊天日志（含时间点，供关键字/按人筛选）+ 当前筛选关键字
        self._chat_log = []         # [{ts, kind, m, text}]
        self._chat_filter = ""
        self._last_banner_text = ""  # 横幅同内容连发去重（显示用）
        # ④框搜索：完整事件日志（点赞/进入/关注/横幅/系统）+ 当前筛选关键字
        self._event_log = []
        self._event_filter = ""

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
        top.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(10, 6, 10, 6)
        # 轮询按钮（点击才开始轮询）：按下=轮询进行中，再点=停止
        self.poll_btn = QPushButton("🔁 轮询")
        self.poll_btn.setCheckable(True)
        self.poll_btn.setChecked(bool(self.settings.get("poll_enabled", True)))
        self.poll_btn.setToolTip(
            "轮询 = 自动等待开播（点击本按钮开始/停止）：\n"
            "① 未开播/已下播：按「轮询间隔」自动重试，主播开播后自动连上；\n"
            "② 直播中不轮询（连接一直保持到下播，不打扰直播）；\n"
            "③ 下播后若本开关仍开着，自动恢复轮询等下次开播。\n"
            "按钮旁的倒计时显示距下次重试的秒数。")
        self.poll_btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; }"
            "QPushButton:checked { background: #e65100; color: white; }")
        self.poll_btn.clicked.connect(self._on_poll_toggle)
        top_l.addWidget(self.poll_btn)
        # 轮询倒计时（距下次重访的秒数；未在轮询时隐藏）
        self.poll_countdown = QLabel("")
        self.poll_countdown.setStyleSheet("color: #e65100; font-size: 11px;")
        self.poll_countdown.setMinimumWidth(34)
        top_l.addWidget(self.poll_countdown)
        self._poll_started_at = None  # 当前轮询周期起点（monotonic）
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._update_poll_countdown)
        self._countdown_timer.start(1000)
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
        clear_btn = QPushButton("🗑 清空内容")
        clear_btn.setToolTip("清空界面临时内容（弹幕/打赏榜/统计/主播信息/封面/计时）\n"
                             "保留房间号、WS 地址等配置")
        clear_btn.clicked.connect(self._clear_all)
        top_l.addWidget(clear_btn)
        self.dycast_btn = QPushButton("🚀 启动弹幕姬")
        self.dycast_btn.setStyleSheet("background: #e65100; color: white;")
        self.dycast_btn.setToolTip("启动原版弹幕姬（dycast-main，纳入进程树：本工具退出时一并关闭）")
        self.dycast_btn.clicked.connect(self._launch_dycast)
        top_l.addWidget(self.dycast_btn)
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
        toolbar.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
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
        merge_btn = QPushButton("🧩 合并回放包")
        merge_btn.setToolTip("选择多个回放包 zip（断线重连产生的多段），按时间排序去重后\n"
                             "合并成一个完整回放包（弹幕/礼物/统计数据；视频文件不参与合并）")
        merge_btn.clicked.connect(self._merge_replay_zips_dialog)
        tb.addWidget(merge_btn)
        self.save_stats_var = QCheckBox("📊 保存统计")
        self.save_stats_var.setChecked(bool(self.settings.get("save_stats", False)))
        self.save_stats_var.toggled.connect(self._on_save_stats_toggle)
        tb.addWidget(self.save_stats_var)
        # 自动录播：勾选后拿到直播流即自动开始录制（最高画质）；取消则只手动录
        # （放工具栏而非视频控制行：仅弹幕模式下视频行隐藏，工具栏各模式恒可见）
        self.auto_record_var = QCheckBox("⏺ 自动录播")
        self.auto_record_var.setToolTip(
            "勾选后：连接直播间拿到直播流即自动开始录制（默认最高画质，各模式均可）；\n"
            "取消勾选：仅点「⏺ 录播」按钮手动录制。")
        self.auto_record_var.setChecked(bool(self.settings.get("auto_record", False)))
        self.auto_record_var.toggled.connect(self._on_auto_record_toggle)
        tb.addWidget(self.auto_record_var)
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
        self.like_label.setStyleSheet("color: #1976d2; font-weight: bold;")
        bottom.addWidget(self.like_label)
        bottom.addStretch(1)
        outer.addLayout(bottom)

        # 恢复上次弹幕方式（持久化；面板已建好，切换信号会同步触发 _apply_mode）
        _mode = str(self.settings.get("mode", "") or "danmaku_only")
        _idx = self.mode_combo.findData(_mode)
        if _idx < 0:
            _idx = self.mode_combo.findData("danmaku_only")
        if _idx >= 0 and _idx != self.mode_combo.currentIndex():
            self.mode_combo.setCurrentIndex(_idx)

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
        card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.addWidget(QLabel("📺 主播信息", styleSheet="font-weight: bold;"))

        self.anchor_fields = {}
        # 直播标题（第一行：比正文大两号 + 加粗，不进日志）
        self.live_title_lbl = QLabel("未获取直播标题")
        self.live_title_lbl.setWordWrap(True)
        self.live_title_lbl.setStyleSheet(
            "font-size: 17px; font-weight: bold; padding: 2px 0 6px 0;")
        self.anchor_fields["live_title"] = self.live_title_lbl
        v.addWidget(self.live_title_lbl)

        # 封面 + 保存按钮
        cover_row = QHBoxLayout()
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setFixedSize(96, 96)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet(
            "background: #ffffff; border-radius: 6px; color: #555;")
        cover_row.addWidget(self.cover_label)
        cover_btn = QPushButton("💾 保存封面/数据")
        cover_btn.clicked.connect(self._save_cover)
        cover_row.addWidget(cover_btn)
        cover_row.addStretch(1)
        v.addLayout(cover_row)

        # 5 字段
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
        sep.setStyleSheet("color: #c9d1dc;")
        v.addWidget(sep)

        # 时间/来源
        self.time_label = QLabel("UTC+8 北京时间")
        self.time_label.setStyleSheet("font-weight: bold;")
        v.addWidget(self.time_label)
        self.now_label = QLabel("--")
        self.now_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.now_label)
        # 备注信息（弹幕姬「⚙ 设置」弹窗可改；保存/切页时刷新）
        self._remark_container = QWidget()
        self._remark_layout = QVBoxLayout(self._remark_container)
        self._remark_layout.setContentsMargins(0, 0, 0, 0)
        self._remark_layout.setSpacing(0)
        v.addWidget(self._remark_container)
        self.refresh_remark_info()
        v.addStretch(1)
        parent.addWidget(card, 1)

    def refresh_remark_info(self):
        """按配置重建主播信息卡底部文字（弹幕姬「⚙ 设置」弹窗可改；默认=来源四行）。"""
        from toolbox.config_manager import load_convert_config
        text = str(load_convert_config().get("remark_info") or "")
        lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")
                 if ln.strip()]
        while self._remark_layout.count():
            item = self._remark_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, ln in enumerate(lines):
            last = i == len(lines) - 1
            lbl = QLabel(ln)
            lbl.setStyleSheet("color: #666; font-size: 10px;" if last
                              else "color: #888; font-size: 11px;")
            lbl.setWordWrap(True)
            self._remark_layout.addWidget(lbl)

    # ======================== 中：视频预览 + 弹幕信息 ========================
    def _build_center_panel(self, parent):
        wrap = QVBoxLayout()
        wrap.setSpacing(4)

        # ---- 视频卡片 ----
        self.video_card = QFrame()
        self.video_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
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
        self.replay_slider.sliderPressed.connect(
            lambda: setattr(self, "_replay_dragging", True))
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
        self.chat_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        v = QVBoxLayout(self.chat_card)
        v.setContentsMargins(10, 10, 10, 10)
        head = QHBoxLayout()
        head.addWidget(QLabel("💬 弹幕信息（聊天+礼物）", styleSheet="font-weight: bold;"))
        head.addStretch(1)
        self.gift_var = QCheckBox("礼物")
        self.gift_var.setChecked(True)
        self.gift_var.stateChanged.connect(
            lambda _s=0: (self._apply_chat_filter(), self._update_search_count()))
        head.addWidget(self.gift_var)
        self.chat_var = QCheckBox("聊天")
        self.chat_var.setChecked(True)
        self.chat_var.stateChanged.connect(
            lambda _s=0: (self._apply_chat_filter(), self._update_search_count()))
        head.addWidget(self.chat_var)
        v.addLayout(head)
        # 搜索栏：关键字搜弹幕/昵称；点击弹幕里的昵称=按此人筛选全部发言及时间点
        search_row = QHBoxLayout()
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("🔍 搜索弹幕内容 / 昵称（点击昵称可筛选此人全部发言）")
        self.search_entry.setClearButtonEnabled(True)
        self._search_timer = QTimer(singleShot=True, interval=300)
        self._search_timer.timeout.connect(self._apply_chat_filter)
        self.search_entry.textChanged.connect(lambda: self._search_timer.start())
        search_row.addWidget(self.search_entry, 1)
        self.search_count = QLabel("")
        self.search_count.setStyleSheet("color:#6b7280; font-size:12px;")
        search_row.addWidget(self.search_count)
        v.addLayout(search_row)
        self.chat_gift_box = QTextBrowser()
        self.chat_gift_box.setOpenExternalLinks(False)
        self.chat_gift_box.setOpenLinks(False)
        self.chat_gift_box.anchorClicked.connect(self._on_chat_anchor)
        self.chat_gift_box.setStyleSheet(
            "QTextBrowser { background: #ffffff; border: none; color: #2f3440; }")
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
        # 弹幕方式持久化（下次启动恢复）
        self.settings["mode"] = mode
        self._save_settings()
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
                     "SD2": "360P", "LD": "360P", "_origin": "原画", "_md": "蓝光"}
    # 画质优先级：原画最高（录播默认自动录最高流）
    QUALITY_ORDER = {"_origin": 100, "_md": 95, "FULL_HD1": 90, "UHD": 85, "HD1": 80,
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
        # 自动录制：勾选「自动录播」或切页返回后待重开 → 拿到流立即录最高画质
        # （正常/仅视频/仅弹幕均可自动录；回放模式除外）
        if (self.mode != "replay" and not self._recording
                and (self.settings.get("auto_record", False) or self._rec_resume_pending)):
            self._rec_resume_pending = False
            self._start_recording()

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

    def _on_auto_record_toggle(self, checked):
        """自动录播开关：持久化；勾选时若已拿到流且未在录 → 立即开始录。"""
        self.settings["auto_record"] = bool(checked)
        self._save_settings()
        if checked and self.connected and self._streams and not self._recording:
            self._start_recording()

    def _toggle_record(self):
        if self._recording:
            # 手动停止：清掉切页自动重开标志（用户明确不录了）
            self._rec_switched_away = False
            self._rec_resume_pending = False
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
        n = 1
        while os.path.exists(path):  # 同秒内重开（切页返回）防同名覆盖
            path = str(d / f"{room}_{ts}_{n}.flv")
            n += 1
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

    def _export_replay_zip(self, video_path=None, rec_meta=None, quiet=False, sync=False):
        """打包为 dycast 全量格式 zip：data.json + 全量弹幕.json + 分类 TXT +
        cover + recording.flv（可被 dycast 与本工具回放直接导入）。
        sync=True 时在当前线程同步写完并返回路径（退出软件时后台线程可能来不及落盘）。"""
        casts = list(self._session_casts)
        if not casts:
            if not quiet:
                self.app.toast("当前没有弹幕数据可打包", "warning")
            return None
        from toolbox.douyin_live import _danmaku_dir
        d = _danmaku_dir()
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
            "title": self.anchor_fields["live_title"].text().strip(),
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

        if sync:
            result = work()
            if not quiet:
                done(result)
            return result if (result and not result.startswith("__error__")) else None
        run_bg(work, done if not quiet else lambda r, e=None: None)
        return None

    def _pick_replay_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择回放包", "", "回放包 (*.zip *.json)")
        if path:
            self.load_replay_zip(path)

    # ======================== 合并回放包（断线多段 → 一段完整） ========================

    def _merge_replay_zips_dialog(self):
        """多选回放包 zip → 合并弹幕内容为一个新的完整回放包。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要合并的回放包（可多选）", "", "回放包 (*.zip *.json)")
        if not paths:
            return
        if len(paths) == 1:
            self.app.toast("只选了一个包，合并至少需要两个", "warning")
            return
        self._merge_replay_zips(paths)

    def _merge_replay_zips(self, paths):
        """读取多个回放包 → casts 按毫秒时间戳排序、断线重叠去重 → 重建
        data.json/全量弹幕.json/分类 TXT/封面 → 打包新 zip 到「弹幕文件」目录。"""
        def work():
            all_casts = []
            bundles = []
            cover = None
            skipped = []
            for p in paths:
                try:
                    if p.lower().endswith(".json"):
                        d = json.loads(open(p, encoding="utf-8").read())
                        casts = d.get("casts") if isinstance(d, dict) else d
                        if isinstance(d, dict) and d.get("_bundle"):
                            bundles.append(d)
                    else:
                        with zipfile.ZipFile(p) as z:
                            names = z.namelist()
                            casts = None
                            if "全量弹幕.json" in names:
                                casts = json.loads(z.read("全量弹幕.json").decode("utf-8"))
                            # data.json：既可能是 casts 来源，也携带 bundle 元数据
                            # （主播名/房间号/点赞数）——与全量弹幕.json 并存时只取元数据
                            if "data.json" in names:
                                d = json.loads(z.read("data.json").decode("utf-8"))
                                if not casts:
                                    casts = d.get("casts") or []
                                if d.get("_bundle"):
                                    bundles.append(d)
                            if cover is None and "cover.jpg" in names:
                                cover = z.read("cover.jpg")
                    if not casts:
                        skipped.append(os.path.basename(p))
                        continue
                    all_casts.extend(c for c in casts
                                     if isinstance(c, dict) and c.get("timestamp"))
                except Exception:
                    skipped.append(os.path.basename(p))
            if len(all_casts) < 2:
                return "__error__no_data"
            # 断线重连会重复拉到重叠窗口的消息（服务器重放，毫秒时间戳相同）：
            # 按 (时间戳, 类型, 昵称, 内容, 礼物名) 去重
            all_casts.sort(key=lambda c: c.get("timestamp") or 0)
            seen = set()
            merged = []
            for c in all_casts:
                key = (c.get("timestamp"), c.get("method"),
                       (c.get("user") or {}).get("name"), c.get("content"),
                       (c.get("gift") or {}).get("name"))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(c)
            bundles.sort(key=lambda b: b.get("exportTime") or "")
            first = bundles[0] if bundles else {}
            anchor = (first.get("nickname") or "未知主播").strip() or "未知主播"
            room = str(first.get("roomId") or "room").strip() or "room"
            like = max((int(b.get("likeCount") or 0) for b in bundles), default=0)
            from toolbox.douyin_live import _danmaku_dir
            out_dir = _danmaku_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            out = out_dir / f"{anchor}-{room}-合并-{ts}-{len(merged)}条.zip"
            bundle = {
                "_bundle": True,
                "exportTime": datetime.now().isoformat(),
                "mergedFrom": [os.path.basename(p) for p in paths],
                "roomId": room,
                "nickname": anchor,
                "avatar": first.get("avatar") or "",
                "cover": "",
                "title": first.get("title") or "",
                "likeCount": like,
                "casts": merged,
            }
            txt_files = []
            for method, label in self.CAST_TXT_TYPES:
                lines = [ln for c in merged if c.get("method") == method
                         for ln in [self._cast_txt_line(c)] if ln]
                if lines:
                    txt_files.append((f"{label}.txt", "\n".join(lines)))
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
                z.writestr("data.json", json.dumps(bundle, ensure_ascii=False, indent=1))
                z.writestr("全量弹幕.json", json.dumps(merged, ensure_ascii=False, indent=1))
                for name, content in txt_files:
                    z.writestr(name, content)
                if cover:
                    z.writestr("cover.jpg", cover)
            return {"out": str(out), "total": len(all_casts), "merged": len(merged),
                    "skipped": skipped, "files": len(paths)}

        def done(result, err=None):
            if err or not isinstance(result, dict):
                self.app.toast("合并失败：包里没有可合并的弹幕数据", "error")
                return
            dup = result["total"] - result["merged"]
            msg = (f"已合并 {result['files']} 个包 → {os.path.basename(result['out'])}"
                   f"（{result['merged']} 条，去除断线重叠 {dup} 条）")
            if result["skipped"]:
                msg += f"；跳过 {len(result['skipped'])} 个无效包"
            self.app.toast(msg, "success")

        run_bg(work, done)

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
            if gift.get("repeatEnd"):
                m["repeatEnd"] = 1
        if m["type"] in ("WebcastGiftMessage", "WebcastBindingGiftMessage") and not m["content"]:
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
        self._gift_sessions = {}
        self._like_count = 0
        self.like_label.setText("0")
        if b.get("nickname"):
            self.anchor_fields["anchor_name"].setText(str(b["nickname"]))
        if b.get("title"):
            self.anchor_fields["live_title"].setText(str(b["title"]))
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
        if not getattr(self, "_replay_dragging", False):
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
        self._gift_sessions = {}
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
        at = float(r.get("paused_at") or 0.0)
        # 播放到结尾后再点播放 → 从头重播
        if at >= float(r["total_ms"]) - 1:
            at = 0.0
        # 与 dycast 一致：从暂停位置重新调度（含视频对位）
        self._seek_replay(at)

    def _on_replay_slider_released(self):
        self._replay_dragging = False
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
        card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)

        # 1. 房间号
        v.addWidget(QLabel("① 房间号", styleSheet="font-weight: bold;"))
        row1 = QHBoxLayout()
        self.room_entry = QLineEdit()
        self.room_entry.setPlaceholderText("582775732015")
        # 恢复上次房间号（进入页面自动重连用）；无效房号（如误输的测试值"123"）
        # 回退默认房号，保证每次启动都显示有效值
        _last_room = str(self.settings.get("last_room", "") or "")
        if not (_last_room.isdigit() and len(_last_room) >= 6):
            _last_room = "582775732015"
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

        # 3. 累计打赏榜（与弹幕姬一致：排名/昵称/礼物数/价值(抖币)/更新时间，前150名）
        # sessionid 不再单独录入：握手凭证直接套用「Cookie 管理」里的抖音 Cookie
        v.addWidget(QLabel("③ 累计打赏榜", styleSheet="font-weight: bold;"))
        self.gift_table = QTableWidget(0, 5)
        self.gift_table.setHorizontalHeaderLabels(["排名", "昵称", "礼物数", "价值(抖币)", "更新时间"])
        self.gift_table.verticalHeader().setVisible(False)
        self.gift_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gift_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.gift_table.setFocusPolicy(Qt.NoFocus)
        self.gift_table.setStyleSheet(
            "QTableWidget { background: #ffffff; color: #2f3440; border: none;"
            " gridline-color: #dfe4ec; font-size: 11px; }"
            "QTableWidget::item { padding: 2px 4px; }"
            "QHeaderView::section { background: #ffffff; color: #888; border: none;"
            " border-bottom: 1px solid #dfe4ec; padding: 3px 4px; font-size: 11px; }")
        hh = self.gift_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        v.addWidget(self.gift_table, 1)

        # 4. 弹幕信息2（点赞+进入直播间+日志提醒）
        head5 = QHBoxLayout()
        head5.addWidget(QLabel("④ 弹幕信息（点赞+进入+日志）", styleSheet="font-weight: bold;"))
        head5.addStretch(1)
        self.like_var = QCheckBox("点赞")
        self.like_var.setChecked(True)
        self.like_var.stateChanged.connect(lambda _s=0: self._apply_event_filter())
        head5.addWidget(self.like_var)
        self.enter_var = QCheckBox("进入直播间")
        self.enter_var.setChecked(True)
        self.enter_var.stateChanged.connect(lambda _s=0: self._apply_event_filter())
        head5.addWidget(self.enter_var)
        self.log_var = QCheckBox("日志")
        self.log_var.setChecked(True)
        self.log_var.stateChanged.connect(lambda _s=0: self._apply_event_filter())
        head5.addWidget(self.log_var)
        v.addLayout(head5)
        # 搜索栏：关键字筛点赞/进入/关注/横幅/日志（与中部弹幕搜索同款防抖）
        es_row = QHBoxLayout()
        self.event_search = QLineEdit()
        self.event_search.setPlaceholderText("🔍 搜索：点赞/进入/关注/横幅/日志")
        self.event_search.setClearButtonEnabled(True)
        self._event_search_timer = QTimer(singleShot=True, interval=300)
        self._event_search_timer.timeout.connect(self._apply_event_filter)
        self.event_search.textChanged.connect(lambda: self._event_search_timer.start())
        es_row.addWidget(self.event_search, 1)
        self.event_count = QLabel("")
        self.event_count.setStyleSheet("color:#6b7280; font-size:12px;")
        es_row.addWidget(self.event_count)
        v.addLayout(es_row)
        self.event_box = QPlainTextEdit()
        self.event_box.setReadOnly(True)
        v.addWidget(self.event_box, 1)
        parent.addWidget(card, 2)

    # ======================== 动作 ========================
    def _open_settings(self):
        s = self.settings
        dlg = QDialog(self)
        dlg.setWindowTitle("弹幕姬设置")
        dlg.resize(500, 660)
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

        e_monitor = row("开播监控检测间隔(秒):", s.get("monitor_interval", 30))
        e_stats = row("统计数据采集间隔(秒):", s.get("stats_interval", 1))
        e_poll = row("轮询间隔(秒):", s.get("poll_interval", 60))
        # 弹幕自动导出说明（默认导出回放包 zip，保存位置为 导出目录/弹幕文件/）
        from toolbox.douyin_live import _danmaku_dir
        v.addWidget(QLabel(
            f"自动导出回放包到：{_danmaku_dir()}"
            "（断开连接 / 退出软件时自动打包 zip，含全量弹幕与分类 TXT）",
            styleSheet="color: #888; font-size: 11px;"))
        v.addWidget(QLabel(
            "「开播监控检测间隔」供「👁 开播监控」按钮使用："
            "按该间隔检测房间开播状态并在开播时提醒。",
            styleSheet="color: #888; font-size: 11px;"))

        # 开关类
        sw_keep = QCheckBox("保持滚动（强制最新）")
        sw_keep.setChecked(bool(s.get("keep_scroll", False)))
        v.addWidget(sw_keep)
        sw_dark = QCheckBox("深色模式")
        sw_dark.setChecked(bool(s.get("dark_mode", False)))
        v.addWidget(sw_dark)
        sw_autosave = QCheckBox("自动导出回放包（断开连接 / 退出软件时打包 zip 到「弹幕文件」目录）")
        sw_autosave.setChecked(bool(s.get("auto_save_danmaku", True)))
        v.addWidget(sw_autosave)

        # 备注信息（主播信息卡 UTC+8 时间下方显示的来源文字，可自定义）
        from toolbox.config_manager import load_convert_config, save_convert_config
        v.addWidget(QLabel("📝 备注信息（显示在主播信息卡 UTC+8 时间下方，每行一条）:",
                           styleSheet="font-weight: bold;"))
        remark_edit = QPlainTextEdit()
        remark_edit.setPlaceholderText("抖音：歆慕\n微博：歆慕XINMU\nb站：歆慕XINMU\n使用信息请标明来源")
        remark_edit.setFixedHeight(88)
        remark_edit.setPlainText(
            str(load_convert_config().get("remark_info") or ""))
        v.addWidget(remark_edit)
        remark_row = QHBoxLayout()
        reset_remark_btn = QPushButton("↩ 恢复默认")
        reset_remark_btn.setToolTip("恢复为默认的来源备注四行")

        def reset_remark():
            from toolbox.config_manager import DEFAULT_CONVERT_CONFIG_DICT
            remark_edit.setPlainText(
                str(DEFAULT_CONVERT_CONFIG_DICT.get("remark_info", "")))

        reset_remark_btn.clicked.connect(reset_remark)
        remark_row.addWidget(reset_remark_btn)
        remark_row.addStretch(1)
        v.addLayout(remark_row)

        def save():
            self.settings["monitor_interval"] = int(e_monitor.text() or 30)
            self.settings["stats_interval"] = int(e_stats.text() or 1)
            self.settings["poll_interval"] = max(15, int(e_poll.text() or 60))
            self.settings.pop("room_list", None)  # 旧版遗留键，无消费者，保存时清掉
            self.settings["keep_scroll"] = bool(sw_keep.isChecked())
            self.settings["dark_mode"] = bool(sw_dark.isChecked())
            self.settings["auto_save_danmaku"] = bool(sw_autosave.isChecked())
            self._save_settings()
            # 备注信息存全局 config（独立于弹幕姬设置），保存后立即刷新主播信息卡
            remark = remark_edit.toPlainText().strip()
            if remark:
                cfg = load_convert_config()
                if cfg.get("remark_info") != remark:
                    cfg["remark_info"] = remark
                    save_convert_config(cfg)
                self.refresh_remark_info()
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

    def _connect(self):
        room = self.room_entry.text().strip()
        if not room:
            # 未填房间号：自动填入默认房号（上次使用的，无效则回退内置默认）
            room = str(self.settings.get("last_room", "") or "")
            if not (room.isdigit() and len(room) >= 6):
                room = "582775732015"
            self.room_entry.setText(room)
            self.app.toast(f"未填房间号，已自动填入默认 {room}", "info")
        self._manual_stop = False   # 发起新连接：清除手动停止标记
        self.connected = True
        self._start_ts = time.time()
        self._like_count = 0
        self._gift_rank = {}
        self._gift_sessions = {}
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
        if self.fetcher:
            self.fetcher.stop()   # 旧连接先停，避免快速重试时出现双 WS
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
        self._waiting_live = False
        self._manual_stop = True    # 手动断开：fetcher 异步回报 disconnected 时不进入自动等待
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
        self._stop_live_preview()
        self._auto_stopped = False
        self._quality_manual = False
        self.like_label.setText("0")
        self.connect_btn.setEnabled(True)
        self.connect_btn2.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.connect_btn.setText("▶ 连接")
        self.disconnect_btn.setText("■ 断开")
        self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
        self.status_label.setText("已断开")
        self.status_label.setStyleSheet("color: #888;")
        if self.settings.get("auto_save_danmaku", True):
            self._auto_save_danmaku()

    # ======================== 轮询（按设置间隔反复访问直播间） ========================
    def _on_poll_toggle(self, _checked=None):
        """轮询按钮：点开 → 立即生效（未连接则直接发起连接/等待开播）；点关 → 停止自动重访。"""
        on = self.poll_btn.isChecked()
        self.settings["poll_enabled"] = on
        self._save_settings()
        self._setup_poll_timer()
        if on:
            # 点击轮询即开始：未连接且未在等待 → 直接连接（未开播则进入自动等待）
            if not self.connected and not self._waiting_live:
                self._connect()
                return
        else:
            self.poll_countdown.setText("")
            if self.connected:
                self.app.toast("轮询已关闭（当前连接保持，不再自动重访）", "info")
        if not self._waiting_live:
            return
        # 未开播等待中途切换轮询 → 状态栏/按钮跟随，避免过期文案
        if on:
            interval = max(15, int(self.settings.get("poll_interval", 60) or 60))
            self.status_label.setText(
                f"主播未开播 · 每{interval}秒自动重试中，开播后自动连接")
            self.connect_btn.setText("▶ 立即重试")
            self.disconnect_btn.setText("■ 停止等待")
            self.disconnect_btn.setEnabled(True)
        else:
            self.status_label.setText("主播未开播 · 已停止自动重试（可手动点「连接」重试）")
            self.connect_btn.setText("▶ 连接")
            self.disconnect_btn.setText("■ 断开")
            self.disconnect_btn.setEnabled(False)

    def _update_poll_countdown(self):
        """每秒刷新轮询倒计时（距下次重访的秒数；未在轮询时清空）。"""
        if self._poll_timer and self._poll_started_at is not None:
            interval = max(15, int(self.settings.get("poll_interval", 60) or 60))
            remain = interval - (time.monotonic() - self._poll_started_at)
            self.poll_countdown.setText(f"⏱ {max(0, int(remain))}s")
        else:
            self.poll_countdown.setText("")

    def _setup_poll_timer(self):
        """等待开播计时器：轮询按钮按下且处于等待态（未开播/已下播、未连接）才跑。
        连接成功后停止（不打扰直播中）；断开后由 _on_status 恢复等待。"""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        if not (self.poll_btn.isChecked() and self._waiting_live and not self.connected):
            self._poll_started_at = None
            self.poll_countdown.setText("")
            return
        interval = max(15, int(self.settings.get("poll_interval", 60) or 60))
        self._poll_started_at = time.monotonic()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_reconnect)
        self._poll_timer.start(interval * 1000)

    def _poll_reconnect(self):
        """轮询到点：仍在等待开播 → 重试连接（开播即接入）。"""
        if not self.poll_btn.isChecked():
            return
        if self.connected:
            return
        if self._waiting_live:
            self._connect()

    # ======================== 保存弹幕 / 统计 / 开播监控 ========================
    def _save_danmaku_now(self):
        """点击保存：立即导出回放包（与断开/退出的自动导出同格式）。"""
        room = self.room_entry.text().strip()
        if not room:
            QMessageBox.warning(self, "提示", "请先输入房间号")
            return
        if not self._auto_save_danmaku():
            self.app.toast("当前没有弹幕数据可保存", "warning")

    def _auto_save_danmaku(self):
        """断开/退出时自动导出：默认导出回放包 zip（含全量弹幕/分类 TXT），
        而不是只导出一个 txt；同步写入保证退出软件前落盘。返回路径（无数据 None）。"""
        if not self._session_casts:
            return None
        return self._export_replay_zip(sync=True)

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
        self.monitor_status.setStyleSheet("color: #1976d2; font-size: 11px;")
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
                self.anchor_fields["live_title"].setText(str(info["title"]))
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
            g = {"name": m.get("giftName"), "count": m.get("giftCount") or 1,
                 "price": m.get("giftPrice") or 0, "icon": m.get("giftIcon") or ""}
            if m.get("repeatEnd"):
                g["repeatEnd"] = 1
            c["gift"] = g
        self._session_casts.append(c)

    def _apply_message(self, name, text, m, t, rank=True):
        """主线程：渲染一条消息到界面（直播与回放共用；rank=False 跳过打赏榜）。"""
        ts = t.strftime("%H:%M:%S") + f".{t.microsecond // 1000:03d}"

        def set_field(key, value):
            self.anchor_fields[key].setText(f"{int(value):,}")

        if name == "礼物":
            # 打赏榜：礼物数累计、价值=数量×抖币价；记录与计算永远进行，
            # 「礼物」复选框只是显示过滤器（取消=隐藏不丢弃）
            self._add_gift_rank(m, t, refresh=rank)
            if m.get("_delta") == 0:
                pass  # 纯重推帧（连击进度重复推送，榜单无增量）：不记录不显示
            else:
                self._log_chat(ts, "礼物", m, "")
                if self.gift_var.isChecked() and not self._chat_filter:
                    self._append_rich(self.chat_gift_box, self._chat_urls("礼物", m),
                                      lambda res: self._gift_html(ts, m, res))
        elif name in ("弹幕", "表情弹幕"):
            self._log_chat(ts, name, m, text)
            if self.chat_var.isChecked() and not self._chat_filter:
                self._append_rich(self.chat_gift_box, self._chat_urls(name, m),
                                  lambda res: self._chat_html(ts, m, text, res))
        elif name == "进场":
            if m.get("memberCount"):
                set_field("online_count", m["memberCount"])
            self._log_event(f"{ts} 💚 {text}")
        elif name == "点赞":
            # likeTotal 为本场累计点赞总数（弹幕姬行为），缺失时退化为 +1
            if m.get("likeTotal"):
                self._like_count = int(m["likeTotal"])
            else:
                self._like_count += 1
            self.like_label.setText(f"{self._like_count:,}")
            self.anchor_fields["like_count"].setText(f"{self._like_count:,}")
            self._log_event(f"{ts} 👍 {text}")
        elif name == "关注":
            if m.get("followCount"):
                set_field("anchor_fans", m["followCount"])
            self._log_event(f"{ts} ⭐ {text}")
        elif name == "在线人数":
            # WebcastRoomUserSeqMessage：静默更新左栏统计（真实数字，非"3万"文本）
            # 注意 set_field 内部自行格式化，必须传原始 int（传 "36,680" 字符串
            # 会 int() 抛异常，导致同分支的累计观看更新永远不执行——曾致"10万+"不变）
            if m.get("roomTotal"):
                set_field("online_count", m["roomTotal"])
            if m.get("roomTotalUser"):
                # 真实累计观看（varint），完整数字展示
                self.anchor_fields["total_viewers"].setText(f"{int(m['roomTotalUser']):,}")
        elif name == "直播数据":
            # WebcastRoomStatsMessage：优先真实在线人数（varint），
            # 避免高频的 display 文本（如"3万"）把真实数字冲掉
            if m.get("roomTotal"):
                self.anchor_fields["online_count"].setText(f"{int(m['roomTotal']):,}")
        elif name == "横幅":
            # WebcastInRoomBannerMessage：完整显示（活动名/规则/状态/任务进度，来自
            # f2 JSON 解析）。服务器同内容连发（实测 90 秒 176 条）只记录首条防刷屏，
            # 全量仍进导出快照
            if text and text != self._last_banner_text:
                self._last_banner_text = text
                self._log_event(f"{ts} 📢 {text}")
        else:
            if text:
                self._log_event(f"{ts} [{name}] {text}")

    def _log_event(self, text):
        """④框：入内存日志（供搜索）+ 按当前筛选状态决定是否实时显示。
        「点赞/进入直播间/日志」复选框是显示过滤器（取消=隐藏不丢弃）。"""
        self._event_log.append(text)
        if len(self._event_log) > 4000:
            self._event_log = self._event_log[-2000:]
        if self._event_visible(text):
            self._append(self.event_box, text)
        self._update_event_count()

    def _event_visible(self, text):
        """事件行是否显示：类别复选框（👍点赞/💚进入/其余=日志）+ 文字筛选。"""
        if "👍" in text:
            if not self.like_var.isChecked():
                return False
        elif "💚" in text:
            if not self.enter_var.isChecked():
                return False
        elif not self.log_var.isChecked():
            return False
        if self._event_filter and not self._event_match(text, self._event_filter):
            return False
        return True

    @staticmethod
    def _event_match(text, kw):
        return kw.lower() in text.lower()

    def _update_event_count(self):
        n = len(self._visible_event_rows())
        if not self._event_filter:
            self.event_count.setText(f"共 {n} 条")
        else:
            self.event_count.setText(f"{n} 条匹配")

    def _visible_event_rows(self):
        """当前过滤条件（类别复选框 + 文字筛选）下可见的事件行。"""
        return [t for t in self._event_log if self._event_visible(t)]

    def _apply_event_filter(self):
        """④搜索框变更（防抖后）/ 复选框切换：按可见行重渲染。"""
        kw = self.event_search.text().strip()
        self._event_filter = kw
        rows = self._visible_event_rows()
        self.event_box.setPlainText("")
        for t in rows[-500:]:
            self.event_box.appendPlainText(t)
        if self.settings.get("keep_scroll", True):
            sb = self.event_box.verticalScrollBar()
            sb.setValue(sb.maximum())
        if not kw:
            self.event_count.setText(f"共 {len(rows)} 条")
        else:
            note = f"筛选「{kw}」：{len(rows)} 条匹配"
            if len(rows) > 500:
                note += "（显示最近 500 条）"
            self.event_count.setText(note)

    def _log_chat(self, ts, kind, m, text):
        """聊天日志：弹幕/礼物（含时间点）入内存列表，供搜索与按人筛选。
        「礼物/聊天」复选框与文字筛选是显示过滤器（取消=隐藏不丢弃），
        图片无论显示与否都预下载缓存（筛选期间收到的表情/图标，回填时有图可渲）。"""
        self._chat_log.append({"ts": ts, "kind": kind, "m": m, "text": text})
        if len(self._chat_log) > 8000:
            self._chat_log = self._chat_log[-4000:]
        urls = self._chat_urls(kind, m)
        if self._chat_filter and self._chat_match(self._chat_log[-1], self._chat_filter) \
                and self._chat_kind_visible(kind):
            # 筛选中命中：走富文本队列（缺图后台下载，就绪/超时按序渲染）
            e = self._chat_log[-1]
            self._append_rich(self.chat_gift_box, urls,
                              lambda res, e=e: self._chat_row_html(e, res))
            self._scroll_chat_bottom()
            self._update_search_count()
        else:
            # 未显示（未命中/类别隐藏）：只预下载图片，不渲染
            self._prefetch_imgs(urls)

    def _prefetch_imgs(self, urls):
        """后台预下载未缓存图片（不渲染，仅供回填/后续显示有图）。"""
        todo = [u for u in urls
                if u and str(u).startswith("http")
                and u not in self._img_tried and not img_cache_lookup(u)]
        if not todo:
            return
        self._img_tried.update(todo)

        def work():
            for u in todo:
                img_cache_download(u)
            return None

        run_bg(work, lambda _r, _e=None: None)

    def _chat_kind_visible(self, kind):
        return self.gift_var.isChecked() if kind == "礼物" else self.chat_var.isChecked()

    def _visible_chat_entries(self):
        """当前过滤条件（类别复选框 + 文字筛选）下可见的聊天/礼物条目。"""
        out = []
        for e in self._chat_log:
            if not self._chat_kind_visible(e["kind"]):
                continue
            if self._chat_filter and not self._chat_match(e, self._chat_filter):
                continue
            out.append(e)
        return out

    @staticmethod
    def _chat_match(entry, kw):
        """关键字命中：@前缀=严格按发布者（昵称全等），否则昵称或内容包含。"""
        m = entry["m"] or {}
        if kw.startswith("@"):
            return (m.get("nickname") or "") == kw[1:]
        hay = " ".join(x for x in (m.get("nickname") or "", m.get("content") or "",
                                   entry.get("text") or "") if x)
        return kw.lower() in hay.lower()

    @staticmethod
    def _chat_urls(kind, m):
        """一条聊天/礼物消息引用的图片 URL（礼物图标 / 表情 rtf）。"""
        m = m or {}
        if kind == "礼物":
            icon = m.get("giftIcon") or ""
            return [icon] if icon else []
        return [p.get("url") for p in (m.get("rtf") or []) if p.get("url")]

    def _chat_row_html(self, entry, resolve=None):
        """单条日志 → 显示 HTML（图片走已缓存路径，缺图按文字占位）。"""
        m = entry["m"] or {}
        if resolve is None:
            resolve = _img_src
        if entry["kind"] == "礼物":
            return self._gift_html(entry["ts"], m, resolve)
        return self._chat_html(entry["ts"], m, entry.get("text") or "", resolve)

    def _render_chat_rows(self, entries, note=""):
        """批量重渲染聊天框（筛选/清除筛选时）。"""
        rows = [self._chat_row_html(e) for e in entries]
        if note:
            rows.insert(0, f'<div style="color:#1a73e8; font-size:12px">{html.escape(note)}</div>')
        self.chat_gift_box.clear()
        if rows:
            self._append_html(self.chat_gift_box, "<br>".join(rows))
            self._scroll_chat_bottom()

    def _scroll_chat_bottom(self):
        bar = self.chat_gift_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _update_search_count(self):
        n = len(self._visible_chat_entries())
        if not self._chat_filter:
            self.search_count.setText(f"共 {n} 条")
        else:
            self.search_count.setText(f"{n} 条匹配")

    def _apply_chat_filter(self):
        """搜索框变更（防抖后）/ 复选框切换：空=回直播视图（最近历史），非空=筛选匹配。"""
        kw = self.search_entry.text().strip()
        self._chat_filter = kw
        visible = self._visible_chat_entries()
        if not kw:
            self.search_count.setText(f"共 {len(visible)} 条")
            self._render_chat_rows(visible[-500:])
            return
        # 按发布者筛选（关键字=完整昵称）时提示总条数；一般关键字命中昵称即等价
        self._render_chat_rows(visible[-500:],
                               f"筛选「{kw}」：{len(visible)} 条匹配（显示最近 500 条）")
        self._update_search_count()

    def _on_chat_anchor(self, url):
        """点击昵称链接：@严格按此人筛选全部弹幕/礼物及时间点。"""
        target = url.toString()
        if target.startswith("user:"):
            nick = target[5:]
            self.search_entry.setText("@" + nick)   # textChanged → 防抖后自动筛选

    def _append_rich(self, box, urls, build, timeout=6.0):
        """带图片的富文本消息：全部图片已缓存 → 立即渲染（缓存套用零延迟）；
        有缺图 → 入队等后台下载，就绪/超时后按到达顺序渲染（缺图按文字占位）。"""
        urls = [u for u in urls if u and str(u).startswith("http")]
        missing = [u for u in urls if not img_cache_lookup(u)]
        if not missing:
            self._append_html(box, build(_img_src))
            return
        self._img_pending.append({"box": box, "urls": urls, "build": build,
                                  "exp": time.time() + timeout})
        self._img_fetch(missing)

    def _img_fetch(self, urls):
        """后台批量下载缺失图片（每 URL 本会话只试一次，防失败重试风暴）。"""
        if self._img_fetching:
            return
        todo = [u for u in urls if u not in self._img_tried]
        if not todo:
            return
        self._img_fetching = True
        self._img_tried.update(todo)

        def work():
            for u in todo:
                img_cache_download(u)
            return None

        run_bg(work, lambda _r, _e=None: self._img_fetch_done())

    def _img_fetch_done(self):
        self._img_fetching = False
        self._img_flush()

    def _img_flush(self):
        """按到达顺序渲染已就绪/已超时的排队消息；仍有缺口时安排下一轮下载/冲刷。"""
        now = time.time()
        while self._img_pending:
            it = self._img_pending[0]
            ready = all(img_cache_lookup(u) for u in it["urls"])
            if not ready and now < it["exp"]:
                break
            self._img_pending.pop(0)
            self._append_html(it["box"], it["build"](_img_src))
        if self._img_pending:
            todo = [u for it in self._img_pending for u in it["urls"]
                    if not img_cache_lookup(u) and u not in self._img_tried]
            if todo and not self._img_fetching:
                self._img_fetch(todo)
            elif not todo:
                # 全部试过仍未就绪 → 等队首到期再渲染
                delay = max(0, self._img_pending[0]["exp"] - time.time())
                QTimer.singleShot(int(delay * 1000) + 50, self._img_flush)

    def _add_gift_rank(self, m, t, refresh=True):
        """累计打赏榜：按用户累计礼物数与抖币价值。
        连击礼物去重：服务器每帧 repeatCount 是累计值（x7→x19→x34 推 3 条，
        实际共 34 个），直接累加会算成 60——按 (用户,礼物) 会话增量结算
        delta=count-settled；repeatEnd=1 结束会话，>15s 无新帧按超时重开
        （防 end 帧丢失后新一轮 x1 被旧 settled 吞掉）。
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
        try:
            now = t.timestamp()
        except Exception:
            now = time.time()
        key = (user, gift_name)
        # 捆绑礼物（66个小心心 等）与普通礼物同一套连击会话结算：
        # repeatCount=67 即送了 67 包，单价=整包抖币价(66)，
        # 价值 = 67×66 = 4422（用户确认口径：66个一毛 × 67次）
        sess = self._gift_sessions.get(key)
        if sess and now - sess["t"] > 15:
            sess = None
        settled = sess["settled"] if sess else 0
        delta = count - settled
        if delta < 0:
            delta = count
        if m.get("repeatEnd"):
            self._gift_sessions.pop(key, None)
        else:
            self._gift_sessions[key] = {"settled": count, "t": now}
        value_delta = delta * price
        m["_delta"] = delta  # 显示层用：本帧实际新增（+0=纯重推帧）
        m["_combo"] = count  # 显示层用：本帧连击累计值
        if delta > 0:
            cur = self._gift_rank.get(user)
            if cur is None:
                cur = {"giftCount": 0, "totalValue": 0, "lastTime": t}
                self._gift_rank[user] = cur
            cur["giftCount"] += delta
            cur["totalValue"] += value_delta
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

    def _nick_html(self, nick, color="#1f2328"):
        """昵称：黑色加粗 + 可点击链接（点击=按此人筛选全部弹幕及时间点）。"""
        if not nick:
            return ""
        href = html.escape(nick, quote=True)
        return (f'<a href="user:{href}" style="color:{color};'
                f'text-decoration:none"><b>{html.escape(nick)}</b></a>：')

    def _chat_html(self, ts, m, text, resolve=None):
        """弹幕行：富文本表情用本地缓存图片内嵌（QTextBrowser 不加载远程图，
        缓存未命中的按文字占位）；无 rtf 退回纯文本。"""
        if resolve is None:
            resolve = _img_src
        parts = []
        for p in (m.get("rtf") or []):
            if p.get("url"):
                alt = html.escape(p.get("text") or "表情")
                src = resolve(p["url"])
                if src:
                    parts.append(f'<img src="{html.escape(src)}" height="20" alt="[{alt}]">')
                else:
                    parts.append(f'<span style="color:#9aa0a6">[{alt}]</span>')
            elif p.get("text"):
                parts.append(html.escape(p["text"]))
        body = "".join(parts) or html.escape(m.get("content") or text or "")
        return f"{self._ts_html(ts)} {self._nick_html(m.get('nickname'))}{body}"

    def _gift_html(self, ts, m, resolve=None):
        """礼物行：礼物图标（本地缓存）+ 名称 xN。白底主题下用深金色保证可读。
        连击帧的 xN 是累计进度（x1→x2→x3 三帧实为 3 个），与打赏榜口径易混——
        本帧实际新增与累计不等时标注（+N），各行 +N 之和=榜单数量。"""
        if resolve is None:
            resolve = _img_src
        icon = m.get("giftIcon") or ""
        icon_h = ""
        if icon:
            alt = html.escape(m.get("giftName") or "礼物")
            src = resolve(icon)
            if src:
                icon_h = f'<img src="{html.escape(src)}" height="22" alt="{alt}"> '
            else:
                icon_h = f'<span style="color:#9aa0a6">[{alt}]</span> '
        body = html.escape(m.get("content") or "")
        d, c = m.get("_delta"), m.get("_combo")
        if d is not None and c is not None and d != c:
            body = f"{body}（+{d}）"
        return (f'{self._ts_html(ts)} 🎁 {self._nick_html(m.get("nickname"))}'
                f'{icon_h}<span style="color:#8a6d00; font-weight:bold">{body}</span>')

    def _on_status(self, status, msg):
        """后台线程回调：连接状态变化。"""
        def apply():
            if status == "connected":
                self.status_dot.setStyleSheet("color: #4caf50; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #4caf50;")
                self._waiting_live = False
                self.connect_btn.setText("▶ 连接")
                self.disconnect_btn.setText("■ 断开")
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
            elif status == "not_live":
                # 未开播：不硬建连接。轮询开着 → 进入自动重试等待（按钮明示当前状态）
                self.status_dot.setStyleSheet("color: #ff9800; font-size: 14px;")
                self.status_label.setStyleSheet("color: #ff9800;")
                was_waiting = self._waiting_live
                self._waiting_live = True
                self.connected = False
                self._timer_running = False
                # 未开播不采集统计
                if self.stats_collector:
                    self.stats_collector.stop()
                    self.stats_collector = None
                self.connect_btn2.setEnabled(True)
                if self.poll_btn.isChecked():
                    interval = max(15, int(self.settings.get("poll_interval", 60) or 60))
                    self.status_label.setText(
                        f"主播未开播 · 每{interval}秒自动重试中，开播后自动连接")
                    self.connect_btn.setText("▶ 立即重试")
                    self.connect_btn.setEnabled(True)
                    self.disconnect_btn.setText("■ 停止等待")
                    self.disconnect_btn.setEnabled(True)
                    if not was_waiting:
                        self.app.toast(
                            f"主播当前未开播：每{interval}秒自动重试中，开播后自动连接；"
                            "点「■ 停止等待」可取消", "info")
                else:
                    self.status_label.setText("主播未开播（点「🔁 轮询」可自动等待开播）")
                    self.connect_btn.setText("▶ 连接")
                    self.connect_btn.setEnabled(True)
                    self.disconnect_btn.setText("■ 断开")
                    self.disconnect_btn.setEnabled(False)
                    if not was_waiting:
                        self.app.toast("主播当前未开播，未建立连接", "info")
                self._setup_poll_timer()
            elif status == "error":
                self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
                self.status_label.setText(msg)
                self.status_label.setStyleSheet("color: #ff5252;")
                self.connected = False
                self._waiting_live = False
                self._timer_running = False
                if self.stats_collector:
                    self.stats_collector.stop()
                    self.stats_collector = None
                self._stop_live_preview()
                self.connect_btn.setEnabled(True)
                self.connect_btn2.setEnabled(True)
                self.disconnect_btn.setEnabled(False)
                self._setup_poll_timer()
            elif status == "disconnected":
                # 下播/连接断开：先清资源（统计采集/视频预览/录制）+ 自动导出本场弹幕，
                # 再按「手动停止 / 轮询开关」决定是否进入等待开播（等待检测为游客态，不占 Cookie）
                self.connected = False
                self._timer_running = False
                if self.stats_collector:
                    self.stats_collector.stop()
                    self.stats_collector = None
                self._stop_live_preview()
                if self._recording:
                    self._stop_recording()
                if not self._manual_stop and self.settings.get("auto_save_danmaku", True):
                    self._auto_save_danmaku()
                self.connect_btn2.setEnabled(True)
                if self._manual_stop:
                    # 手动断开：不自动重连（UI 已由 _disconnect 复位，此处仅兜底）
                    self._waiting_live = False
                    self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
                    self.status_label.setText(msg)
                    self.status_label.setStyleSheet("color: #888;")
                    self.connect_btn.setEnabled(True)
                    self.disconnect_btn.setEnabled(False)
                elif self.poll_btn.isChecked():
                    # 下播且轮询仍开着 → 恢复等待开播，下次开播自动接入
                    self._waiting_live = True
                    interval = max(15, int(self.settings.get("poll_interval", 60) or 60))
                    self.status_dot.setStyleSheet("color: #ff9800; font-size: 14px;")
                    self.status_label.setText(
                        f"直播已结束 · 每{interval}秒检测开播中，开播后自动连接")
                    self.status_label.setStyleSheet("color: #ff9800;")
                    self.connect_btn.setText("▶ 立即重试")
                    self.connect_btn.setEnabled(True)
                    self.disconnect_btn.setText("■ 停止等待")
                    self.disconnect_btn.setEnabled(True)
                    self.app.toast(
                        f"直播已结束：每{interval}秒自动检测开播，开播后自动连接", "info")
                else:
                    # 轮询关着：停在已断开，不自动重连
                    self._waiting_live = False
                    self.status_dot.setStyleSheet("color: #ff5252; font-size: 14px;")
                    self.status_label.setText("直播已结束（点「🔁 轮询」可自动等待下次开播）")
                    self.status_label.setStyleSheet("color: #888;")
                    self.connect_btn.setText("▶ 连接")
                    self.connect_btn.setEnabled(True)
                    self.disconnect_btn.setText("■ 断开")
                    self.disconnect_btn.setEnabled(False)
                self._setup_poll_timer()
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
        """清空内容：清除界面上所有临时内容（弹幕/打赏榜/统计/主播信息/封面/计时/本场快照），
        保留房间号、WS 地址、清晰度、轮询等常配置与连接状态。"""
        # 弹幕框（连同搜索状态与聊天日志）
        self.chat_gift_box.clear()
        self._chat_log = []
        self._chat_filter = ""
        self.search_entry.blockSignals(True)
        self.search_entry.clear()
        self.search_entry.blockSignals(False)
        self.search_count.setText("")
        self._img_pending.clear()
        self.event_box.setPlainText("")
        self._last_banner_text = ""
        self._event_log = []
        self._event_filter = ""
        self.event_search.blockSignals(True)
        self.event_search.clear()
        self.event_search.blockSignals(False)
        self.event_count.setText("")
        # 打赏榜
        self.gift_table.setRowCount(0)
        self._gift_rank = {}
        self._gift_sessions = {}
        # 统计：本场点赞 / 快照 / 计时
        self._like_count = 0
        self.like_label.setText("0")
        self.anchor_fields["like_count"].setText("0")
        self._session_casts = []
        self._start_ts = time.time()
        self.timer_label.setText("00:00:00")
        # 主播信息 + 直播标题 + 封面
        self.anchor_fields["live_title"].setText("未获取直播标题")
        self.anchor_fields["anchor_name"].setText("*****")
        self.anchor_fields["anchor_fans"].setText("*****")
        self.anchor_fields["online_count"].setText("*****")
        self.anchor_fields["total_viewers"].setText("*****")
        self._cover_bytes = None
        self.cover_label.clear()
        self.cover_label.setText("暂无封面")

    def _launch_dycast(self):
        """启动/停止原版弹幕姬（dycast-main，纳入进程树：本工具退出时一并关闭）。"""
        from gui_qt.pages.dycast import dycast_app_running, launch_dycast_app, stop_dycast_app
        if dycast_app_running():
            stop_dycast_app(self.app.toast)
            self.dycast_btn.setText("🚀 启动弹幕姬")
            return
        if launch_dycast_app(self.app.toast, linked=True):
            self.dycast_btn.setText("⏹ 停止弹幕姬")

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
        """切到本页：录制中恢复预览；录播中切走的重新开始录制；
        上次因切走被自动断开（且有房间号）→ 自动重连。"""
        # 备注信息可能已在弹幕姬设置弹窗里改过 → 每次切回刷新
        self.refresh_remark_info()
        if self.connected or self.fetcher:
            # 后台保持运作中（如录制中切走再切回）：恢复视频预览渲染
            if self._streams and self.mode in ("normal", "video_only") \
                    and not (self.video_player and self.video_player.is_running()):
                self._start_live_preview()
            # 录播中切走的 → 重新开始录制（切走期间的段落自动打包，起一个新文件）
            self._restart_recording_after_switch()
            return
        if self._rec_switched_away:
            # 切走期间录制已被断连停止（如主播下播）→ 待重连拿到流后自动重开
            self._rec_switched_away = False
            self._rec_resume_pending = True
        room = self.room_entry.text().strip()
        if room and self._auto_stopped:
            self._auto_stopped = False
            self._connect()

    def _restart_recording_after_switch(self):
        """返回页面：录播中切走过的 → 重新开始录制。

        后台仍在录的：停掉并自动打包切走期间的段落，再起一个新文件；
        切走期间因下播/断连已停录的：已重连就直接重开，否则待拿到流后自动重开。
        """
        if not self._rec_switched_away:
            return
        self._rec_switched_away = False
        if self.mode == "replay":
            return
        if self._recording:
            self._stop_recording()  # 切走期间的录制段自动打包（不足 1KB 的清理掉）
        if not (self.connected and self._streams):
            self._rec_resume_pending = True  # 等待重连/开播，拿到流自动重开
            return
        self._start_recording()
        if self._recording:
            self.app.toast("已重新开始录制（新文件）", "info")

    def on_hide(self):
        """切离本页：录制中保持后台运作（弹幕抓取+录制继续），仅停 UI 渲染；
        未录制则全停（录制自动打包回放包/回放/预览/抓取/监控）。"""
        if self._recording:
            # 录制中切页面：维持弹幕抓取与录制，只停视频预览渲染与回放；
            # 返回页面时重新开始录制（见 _restart_recording_after_switch）
            self._rec_switched_away = True
            self._stop_replay()
            self._stop_live_preview()
            return
        self.shutdown()

    def shutdown(self):
        """全停（切走且未录制 / 软件退出时调用）：停一切后台任务并自动保存。"""
        was_active = self.connected or self.fetcher or self.video_player or self._recording
        self._rec_switched_away = False
        self._rec_resume_pending = False
        if self._recording:
            self._stop_recording()
        self._stop_replay()
        self._stop_live_preview()
        if self.connected or self.fetcher or self.stats_collector:
            self._disconnect()
        if self._monitoring:
            self._toggle_monitor()
        self._auto_stopped = bool(was_active)
