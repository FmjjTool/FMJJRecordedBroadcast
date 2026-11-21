# -*- coding: utf-8 -*-
"""抖音表情包页：采集工具 → 第 4 个页签。

只做一件事：按直播间 / 账号拉全「会员表情栏」，勾选后导出成图片。

- 账号来源：顶部下拉列出「账号与登录 → 账号配置」里已存的抖音直播间
  （直播录制列表 / 房间缓存 / 账号列表 / 最近连接），也可直接填直播间号、
  19 位长号或 live.douyin.com 链接。
- 会员表情栏：房间进房接口 privilege/subscribe/info 的 emoji_list，开播中、
  已结束都能拉；主播 ID 由 douyin_emoji.fetch_vip_emojis 做三级兜底。
- 导出目录固定跟随全局统一导出目录（`抖音表情包` 子文件夹），保留原格式
  （webp/gif/png），文件名取表情名（接口没给名字时用资源 ID），同名加序号。

与弹幕姬完全独立：不读弹幕包、不跟随弹幕姬连接。
"""

from __future__ import annotations

import re
import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from toolbox import douyin_emoji as DE
from toolbox.config_manager import get_export_subdir

# 直播间链接 → web_rid（短号/纯数字都取自 live.douyin.com 后一段）
_ROOM_LINK = re.compile(r"live\.douyin\.com/([A-Za-z0-9_\-]+)")

_ROOM_HINT = "选择已存账号 / 直接填直播间号或链接"


# ==================== 缩略图（后台单线程，命中缓存直出） ====================

class _Thumbs(QObject):
    """按需下载缩略图：命中本地缓存直接返回，未命中排队后台下载。"""

    ready = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self._thread = None
        self.ready.connect(self._on_ready, Qt.ConnectionType.QueuedConnection)
        self._on_ready_cb = None

    def bind(self, cb):
        self._on_ready_cb = cb

    def request(self, url):
        from gui_qt.pages.douyin_danmaku import img_cache_lookup
        p = img_cache_lookup(url)
        if p:
            self.ready.emit(url, str(p))
            return
        with self._lock:
            if url in self._queue:
                return
            self._queue.append(url)
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()

    def _work(self):
        from gui_qt.pages.douyin_danmaku import img_cache_download
        while True:
            with self._lock:
                if not self._queue:
                    self._thread = None
                    return
                url = self._queue.popleft()
            p = img_cache_download(url)
            if p:
                self.ready.emit(url, str(p))

    def _on_ready(self, url, path):
        if self._on_ready_cb:
            self._on_ready_cb(url, path)


# ==================== 页面 ====================

class DouyinEmojiPage(BasePage):
    """抖音表情包：会员表情栏拉全 + 勾选导出。"""

    def __init__(self, app):
        super().__init__(app)
        self.items = []            # [{url,key,name,nick,ts}]
        self._keys = set()
        self._row_of = {}          # key → QListWidgetItem
        self._exporting = False
        self._out_dir = Path(get_export_subdir("douyin_emoji"))

        self._thumbs = _Thumbs(self)
        self._thumbs.bind(self._apply_thumb)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._build_source_card())
        layout.addWidget(self._build_grid_card(), 1)
        layout.addWidget(self._build_export_card())

        self._refresh_accounts()

    # ---------- 构建 ----------

    def _caption(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setProperty("role", "caption")
        return label

    @staticmethod
    def _card():
        box = QFrame()
        box.setStyleSheet("QFrame { background: palette(base); border-radius: 6px; }")
        return box

    def _build_source_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.vip_room = QComboBox()
        self.vip_room.setEditable(True)
        self.vip_room.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.vip_room.lineEdit().setPlaceholderText(_ROOM_HINT)
        row.addWidget(self.vip_room, 1)
        self.vip_btn = QPushButton("一键拉取全部")
        self.vip_btn.clicked.connect(self._fetch_vip)
        row.addWidget(self.vip_btn)
        v.addLayout(row)
        v.addWidget(self._caption(
            "会员表情栏就是房间进房时前端调的那条接口（privilege/subscribe/info），"
            "这里直接读它的 emoji_list，一次性拉全，不用等会员发表情。"
            "开播中、已结束的直播间都能拉。账号列表来自「账号与登录 → 账号配置」，"
            "也可以直接填直播间号或链接。需要抖音登录态。"))
        return card

    def _build_grid_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        head = QHBoxLayout()
        self.count_lbl = QLabel("已收集 0 个")
        head.addWidget(self.count_lbl)
        head.addStretch(1)
        for text, fn in (("全选", lambda: self._check_all(True)),
                         ("全不选", lambda: self._check_all(False)),
                         ("清空列表", self._clear_items)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            head.addWidget(b)
        v.addLayout(head)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(56, 56))
        self.list.setGridSize(QSize(104, 100))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.list, 1)
        return card

    def _build_export_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("导出到："))
        row.addWidget(self._caption(str(self._out_dir)), 1)
        open_btn = QPushButton("打开目录")
        open_btn.clicked.connect(self._open_out_dir)
        row.addWidget(open_btn)
        self.export_btn = QPushButton("导出选中")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.export_btn)
        v.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.status = self._caption("")
        v.addWidget(self.status)
        return card

    # ---------- 账号列表 ----------

    def _refresh_accounts(self):
        """账号来源：账号配置里已存的抖音直播间（直播录制 / 房间缓存 / 账号列表 / 最近连接）。"""
        try:
            from gui_qt.room_picker import load_local_entries
            entries = load_local_entries()
        except Exception:
            entries = []
        seen, rooms = set(), []
        for e in entries:
            if getattr(e, "platform", "") != "douyin" or not e.room:
                continue
            if e.room in seen:
                continue
            seen.add(e.room)
            rooms.append((e.name or "", e.room))

        current = self._vip_room_id()
        self.vip_room.blockSignals(True)
        self.vip_room.clear()
        for name, room in rooms[:80]:
            label = f"{name} · {room}" if name and name != room else room
            self.vip_room.addItem(label, room)
        self.vip_room.setCurrentIndex(-1)
        if current:
            # 刷新前选中的账号按房间号找回；手填内容原样保留
            idx = self.vip_room.findData(current)
            if idx >= 0:
                self.vip_room.setCurrentIndex(idx)
            else:
                self.vip_room.setEditText(current)
        self.vip_room.blockSignals(False)

    def on_show(self):
        # 账号可能刚在「账号配置」里改过 → 每次切回刷新（保留手填内容）
        self._refresh_accounts()

    # ---------- 数据 ----------

    def _vip_room_id(self):
        text = self.vip_room.currentText().strip()
        idx = self.vip_room.currentIndex()
        # 选中下拉项时文本是「名称 · 房间号」，真正的房间号存在 itemData；
        # 手填（文本与选中项不一致）则以输入框为准。
        if idx >= 0 and text == self.vip_room.itemText(idx):
            room = self.vip_room.itemData(idx)
            if room:
                return str(room)
        if not text or text == _ROOM_HINT:
            return ""
        m = _ROOM_LINK.search(text)
        return m.group(1) if m else text

    def _add_item(self, it):
        if it["key"] in self._keys:
            return
        self._keys.add(it["key"])
        self.items.append(it)
        li = QListWidgetItem(it["name"])
        # 图标是异步补上的：先定死格子尺寸，避免首行按「纯文字」量高后整列变小
        li.setSizeHint(QSize(96, 84))
        li.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        li.setCheckState(Qt.CheckState.Checked)
        li.setData(Qt.ItemDataRole.UserRole, it["key"])
        tip = it["name"]
        if it.get("nick"):
            tip += f"\n来自：{it['nick']}"
        li.setToolTip(tip + f"\n{it['url']}")
        self.list.addItem(li)
        self._row_of[it["key"]] = li
        self._thumbs.request(it["url"])
        self._update_count()

    def _apply_thumb(self, url, path):
        key = DE.url_key(url)
        li = self._row_of.get(key)
        if li is None:
            return
        pix = QPixmap(path)
        if not pix.isNull():
            li.setIcon(QIcon(pix))

    def _on_item_changed(self, _item):
        self._update_count()

    def _update_count(self):
        n = len(self.items)
        checked = sum(1 for i in range(self.list.count())
                      if self.list.item(i).checkState() == Qt.CheckState.Checked)
        self.count_lbl.setText(f"已收集 {n} 个 · 已选 {checked} 个")

    def _check_all(self, on):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self._update_count()

    def _clear_items(self):
        self.list.clear()
        self.items = []
        self._keys = set()
        self._row_of = {}
        self.status.setText("已清空列表")
        self._update_count()

    # ---------- 会员表情栏 ----------

    def _fetch_vip(self):
        room = self._vip_room_id()
        if not room:
            self.app.toast("先选择已存账号，或填直播间号 / 链接", "warning")
            return
        self.vip_btn.setEnabled(False)
        self.status.setText(f"正在拉取 {room} 的会员表情栏…")

        def work():
            return DE.fetch_vip_emojis(room)

        def done(res, err=None):
            self.vip_btn.setEnabled(True)
            if err:
                self.status.setText(f"拉取失败：{err}")
                return
            res = res or {}
            items = res.get("items") or []
            added = 0
            for it in items:
                before = len(self.items)
                self._add_item(it)
                if len(self.items) > before:
                    added += 1
            if not items:
                self.status.setText(res.get("error") or "接口没有返回表情")
                return
            tip = res.get("describe") or ""
            self.status.setText(f"会员表情栏：拉到 {len(items)} 个，新增 {added} 个"
                                + (f" · {tip}" if tip else ""))

        run_bg(work, done)

    # ---------- 导出 ----------

    def _open_out_dir(self):
        out = Path(get_export_subdir("douyin_emoji"))
        self._out_dir = out
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

    def _export(self):
        if self._exporting:
            return
        picked = []
        for i in range(self.list.count()):
            li = self.list.item(i)
            if li.checkState() != Qt.CheckState.Checked:
                continue
            key = li.data(Qt.ItemDataRole.UserRole)
            picked.extend([it for it in self.items if it["key"] == key])
        if not picked:
            self.app.toast("没有勾选要导出的表情", "warning")
            return
        out = Path(get_export_subdir("douyin_emoji"))
        self._out_dir = out
        self._exporting = True
        self.export_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(picked))
        self.progress.setValue(0)

        def prog(done, total, name, ok):
            post_to_main(self._on_export_progress, done, total, name, ok)

        def work():
            return DE.download_items(picked, out, on_progress=prog)

        def done(res, err=None):
            self._exporting = False
            self.export_btn.setEnabled(True)
            self.progress.setVisible(False)
            if err:
                self.status.setText(f"导出失败：{err}")
                return
            ok = len((res or {}).get("saved") or [])
            bad = len((res or {}).get("failed") or [])
            self.status.setText(f"已导出 {ok} 个到 {out}" + (f"，失败 {bad} 个" if bad else ""))
            self.app.toast(f"表情已导出 {ok} 个" + (f"（失败 {bad}）" if bad else ""),
                           "ok" if ok else "error")

        run_bg(work, done)

    def _on_export_progress(self, done, total, name, ok):
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"导出中 {done}/{total}：{name}" + ("" if ok else "（失败）"))