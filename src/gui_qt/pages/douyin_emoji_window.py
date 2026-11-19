# -*- coding: utf-8 -*-
"""抖音表情收集 · 二级界面（非模态独立窗口）。

三种来源：
  ① 会员表情栏——房间进房接口 privilege/subscribe/info 一次返回全部
     emoji_list，直接拉全（需登录态）；
  ② 直播间实时——弹幕姬页把每条消息的 rtf 片段转喂进来（不额外开 WS）；
  ③ 本地弹幕包——.jsonl 实时记录 / 回放包 .zip 里已经存下的 rtfContent。

窗口独立（非模态），开着时仍能操作主界面；导出为图片文件（保留原格式），
文件名取表情名，同名自动加序号。
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.theme import color as theme_color
from toolbox import douyin_emoji as DE

A = Qt.AlignmentFlag


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


# ==================== 主窗口 ====================

class DouyinEmojiWindow(QDialog):
    def __init__(self, app, page=None):
        super().__init__(page or (app if isinstance(app, QWidget) else None))
        self.app = app
        self.page = page
        self.setWindowTitle("抖音表情收集 · 会员表情")
        self.setModal(False)
        self.resize(880, 640)
        self.setMinimumSize(720, 520)

        self.items = []            # [{url,key,name,nick,ts}]
        self._keys = set()
        self._row_of = {}          # url → QListWidgetItem
        self._pending = deque()    # 弹幕姬页后台线程喂入（加锁）
        self._plock = threading.Lock()
        self._collecting = True
        self._exporting = False
        self._out_dir = self._default_out_dir()

        self._thumbs = _Thumbs(self)
        self._thumbs.bind(self._apply_thumb)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        tip = QLabel("会员表情不在官方表情库里，只会随聊天消息下发——这里收集「实际收到过」的表情图。"
                     "直播间来源跟随弹幕姬页的连接（不额外开第二条 WS）。")
        tip.setWordWrap(True)
        tip.setProperty("role", "caption")
        outer.addWidget(tip)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_vip_tab(), "会员表情栏")
        self.tabs.addTab(self._build_live_tab(), "直播间实时")
        self.tabs.addTab(self._build_file_tab(), "本地弹幕包")
        outer.addWidget(self.tabs)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QPixmap(56, 56).size())
        self.list.setGridSize(QSize(104, 100))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self.list, 1)

        bar = QHBoxLayout()
        self.count_lbl = QLabel("已收集 0 个")
        bar.addWidget(self.count_lbl)
        bar.addStretch(1)
        for text, fn in (("全选", lambda: self._check_all(True)),
                         ("全不选", lambda: self._check_all(False)),
                         ("清空列表", self._clear_items)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        outer.addLayout(bar)

        foot = QFrame()
        foot.setStyleSheet("QFrame { background: palette(base); border-radius: 6px; }")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.addWidget(QLabel("导出到："))
        self.out_edit = QLineEdit(str(self._out_dir))
        fl.addWidget(self.out_edit, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        fl.addWidget(pick)
        self.export_btn = QPushButton("导出选中")
        self.export_btn.clicked.connect(self._export)
        fl.addWidget(self.export_btn)
        outer.addWidget(foot)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setProperty("role", "caption")
        outer.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_pending)
        self._timer.start(400)

        if page is not None:
            page.emoji_sink = self._feed

    # ---------- 构建 ----------

    def _build_vip_tab(self):
        """会员表情栏：房间级一次性接口，不用等有人发。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 6)
        v.setSpacing(6)
        row = QHBoxLayout()
        self.vip_room = QLineEdit()
        self.vip_room.setPlaceholderText("直播间号（留空则用弹幕姬页当前房间）")
        row.addWidget(self.vip_room, 1)
        self.vip_btn = QPushButton("一键拉取全部")
        self.vip_btn.clicked.connect(self._fetch_vip)
        row.addWidget(self.vip_btn)
        v.addLayout(row)
        hint = QLabel("会员表情栏就是房间进房时前端调的那条接口（privilege/subscribe/info），"
                      "这里直接读它的 emoji_list，一次性拉全，不用等会员发表情。"
                      "需要抖音登录态。")
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _vip_room_id(self):
        text = self.vip_room.text().strip()
        if text:
            return text
        if self.page is not None:
            for attr in ("room_entry", "room_input", "room_edit"):
                w = getattr(self.page, attr, None)
                try:
                    if w is not None and w.text().strip():
                        return w.text().strip()
                except Exception:
                    continue
        return ""

    def _fetch_vip(self):
        room = self._vip_room_id()
        if not room:
            if self.app:
                self.app.toast("先填直播间号", "warning")
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

    def _build_live_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 6)
        v.setSpacing(6)
        row = QHBoxLayout()
        self.live_state = QLabel("未连接")
        row.addWidget(self.live_state)
        row.addStretch(1)
        self.pause_btn = QPushButton("暂停收集")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause)
        row.addWidget(self.pause_btn)
        v.addLayout(row)
        hint = QLabel("先在弹幕姬页连接直播间；连上后本窗口自动收集聊天里的表情图"
                      "（会员/粉丝团/合并表情都会收进来），下播或断开即停止。")
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _build_file_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 6)
        v.setSpacing(6)
        row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("弹幕 .jsonl（实时记录 / 弹幕姬导出）或回放包 .zip，可多选")
        row.addWidget(self.file_edit, 1)
        b1 = QPushButton("选择文件…")
        b1.clicked.connect(self._pick_files)
        row.addWidget(b1)
        b2 = QPushButton("读取")
        b2.clicked.connect(self._read_files)
        row.addWidget(b2)
        v.addLayout(row)
        hint = QLabel("已保存的弹幕包里也存着 rtfContent，历史场次的会员表情可从"
                      "「导出目录/弹幕文件」里挑文件补收。")
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        v.addWidget(hint)
        v.addStretch(1)
        return w

    # ---------- 数据 ----------

    def _default_out_dir(self) -> Path:
        try:
            from toolbox.config_manager import get_export_subdir
            return Path(get_export_subdir("douyin_emoji"))
        except Exception:
            return Path.cwd() / "download" / "抖音表情包"

    def _feed(self, pieces, nick="", ts=0):
        """弹幕姬页回调（后台线程）：只入队，主线程定时消费。"""
        if not self._collecting:
            return
        rows = DE.piece_items(pieces, nick, ts)
        if not rows:
            return
        with self._plock:
            self._pending.extend(rows)

    def _drain_pending(self):
        if not self._pending:
            return
        with self._plock:
            rows = list(self._pending)
            self._pending.clear()
        for r in rows:
            self._add_item(r)
        self.live_state.setText(f"收集中 · 已收到 {len(self.items)} 个表情"
                                if self.items else "收集中 · 等待表情")

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
        self.live_state.setText("已清空")
        self._update_count()

    def _on_pause(self, paused):
        self._collecting = not paused
        self.pause_btn.setText("继续收集" if paused else "暂停收集")
        self.live_state.setText("已暂停" if paused else "收集中")
        if not paused and self.page is not None:
            self.page.emoji_sink = self._feed

    # ---------- 导入 / 导出 ----------

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择弹幕包", str(Path(self._out_dir).parent),
            "弹幕包 (*.jsonl *.zip *.json *.txt);;所有文件 (*.*)")
        if paths:
            self.file_edit.setText(";".join(paths))

    def _read_files(self):
        raw = self.file_edit.text().strip()
        paths = [p.strip().strip('"') for p in raw.replace("\n", ";").split(";") if p.strip()]
        if not paths:
            if self.app:
                self.app.toast("先选择弹幕包文件", "warning")
            return
        self.status.setText("正在读取弹幕包…")

        def work():
            return DE.collect_files(paths)

        def done(items, err=None):
            if err:
                self.status.setText(f"读取失败：{err}")
                return
            for it in items or []:
                self._add_item(it)
            self.status.setText(f"已从 {len(paths)} 个弹幕包补收 {len(items or [])} 个表情")

        run_bg(work, done)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录", str(self.out_edit.text() or ""))
        if d:
            self.out_edit.setText(d)

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
            if self.app:
                self.app.toast("没有勾选要导出的表情", "warning")
            return
        out = Path(self.out_edit.text().strip() or self._default_out_dir())
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
            if self.app:
                self.app.toast(f"表情已导出 {ok} 个" + (f"（失败 {bad}）" if bad else ""),
                               "ok" if ok else "error")

        run_bg(work, done)

    def _on_export_progress(self, done, total, name, ok):
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"导出中 {done}/{total}：{name}" + ("" if ok else "（失败）"))

    # ---------- 生命周期 ----------

    def closeEvent(self, event):
        if self.page is not None and getattr(self.page, "emoji_sink", None) == self._feed:
            self.page.emoji_sink = None
        self._timer.stop()
        super().closeEvent(event)


def open_emoji_window(app, page=None):
    """打开（或复用）表情收集窗口。"""
    win = getattr(app, "douyin_emoji_win", None) if app is not None else None
    if win is None:
        win = DouyinEmojiWindow(app, page=page)
        if app is not None:
            app.douyin_emoji_win = win
    elif page is not None:
        # 关窗时已解绑；复用窗口要重新接上当前页的消息流
        win.page = page
        page.emoji_sink = win._feed
    win.show()
    win.raise_()
    win.activateWindow()
    return win