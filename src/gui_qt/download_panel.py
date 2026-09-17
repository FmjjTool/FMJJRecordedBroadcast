"""IDM 风格下载进度面板（单个解析 / 批量导入页共用）。

参考 Internet Download Manager 的下载对话视觉：
- 单文件模式（segments）：文件名 + 下方 N 段分段进度条（每段对应一个下载线程）
  + 统计行（已下/总大小 · 速度 · 百分比 · N 线程）；
- 批量模式（parallel/serial）：N 个「文件名槽位」行并行显示（每行文件名 +
  进度条 + 状态），槽位完成后自动被队列中的下一个文件占用（IDM 队列风格）。

数据源：media_downloader.download_media / download_ops.do_bilibili_download 的
on_task_event 回调 → DownloadPanel.handle_event（线程安全，内部 post_to_main）。
"""

import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main

_ACCENT = "#2e7d32"        # 与全局 QSS QProgressBar::chunk 同色
_FAIL = "#c62828"
_SKIP = "#9aa3b0"


def _fmt_bytes(n: float) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n:.0f} B"


def _fmt_speed(n: float) -> str:
    return _fmt_bytes(n) + "/s"


def _elide(name: str, limit: int = 46) -> str:
    return name if len(name) <= limit else name[:limit - 1] + "…"


class SegmentBar(QWidget):
    """IDM 风格分段进度条：N 个格子，每格独立填充比例（一格 = 一个下载线程）。

    单文件多线程下载时，文件名下方这 N 格与线程数一一对应。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(18)
        self._segs: list[tuple[int, int]] = []

    def set_count(self, n: int):
        self._segs = [(0, 0)] * max(1, n)
        self.update()

    def set_seg(self, i: int, done: int, total: int):
        if 0 <= i < len(self._segs):
            self._segs[i] = (done, total)
            self.update()

    def paintEvent(self, _ev):
        from gui_qt.theme import is_dark
        dark = is_dark()
        empty = QColor("#23262f" if dark else "#e4e8ee")
        edge = QColor("#333" if dark else "#ffffff")
        fill = QColor(_ACCENT)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._segs)
        if n == 0:
            return
        gap = 2
        cw = (w - gap * (n - 1)) / n
        bar_h = h - 4
        for i, (done, total) in enumerate(self._segs):
            x = i * (cw + gap)
            p.setPen(Qt.NoPen)
            p.setBrush(empty)
            p.drawRoundedRect(int(x + 0.5), 2, int(cw + 0.5), bar_h, 2, 2)
            if total > 0 and done > 0:
                frac = max(0.0, min(1.0, done / total))
                if frac > 0:
                    p.setBrush(fill)
                    p.drawRoundedRect(int(x + 0.5), 2,
                                      max(2, int(cw * frac)), bar_h, 2, 2)
        p.end()


class _SlotRow(QWidget):
    """批量模式的一行：序号 + 文件名 + 进度条 + 状态。"""

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(6)
        self.no_label = QLabel(f"{idx + 1}")
        self.no_label.setFixedWidth(18)
        self.no_label.setStyleSheet("color: #888; font-size: 11px;")
        self.name_label = QLabel("—")
        self.name_label.setStyleSheet("font-size: 12px;")
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        self.bar.setFixedWidth(110)
        self.bar.setStyleSheet(
            "QProgressBar { font-size: 10px; border-radius: 4px; }")
        self.state_label = QLabel("")
        self.state_label.setFixedWidth(70)
        self.state_label.setStyleSheet("font-size: 11px; color: #888;")
        lay.addWidget(self.no_label)
        lay.addWidget(self.name_label, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.state_label)

    def bind(self, name: str):
        self.name_label.setText(_elide(name))
        self.name_label.setToolTip(name)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.state_label.setText("")
        self.state_label.setStyleSheet("font-size: 11px; color: #888;")

    def progress(self, done: int, total: int):
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(done * 100 / total))
        else:
            self.bar.setRange(0, 0)  # busy

    def finish(self, ok: bool):
        self.bar.setRange(0, 100)
        self.bar.setValue(100 if ok else 100)
        self.state_label.setText("✅" if ok else "❌")
        self.state_label.setStyleSheet(
            f"font-size: 11px; color: {_ACCENT if ok else _FAIL};")


class DownloadPanel(QFrame):
    """下载进度面板：单文件 IDM 分段模式 / 批量文件名槽位模式。

    页面用法：
        panel.begin()                     # 下载开始：显示面板并复位
        download(..., on_task_event=panel.handle_event)
        # 结束后 stats 文本用 panel.finish(text) 或页面日志呈现
    handle_event(event, *args) 由下载线程直接调用（内部线程安全）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        self.setFrameShape(QFrame.StyledPanel)
        self._lock = threading.Lock()
        # ---- 事件状态（下载线程写，post 到主线程渲染）----
        self._mode = None          # "segments" / "parallel" / "serial"
        self._threads = 1
        self._total_cnt = 0
        self._done_cnt = 0
        self._single_name = ""
        self._single = {"done": 0, "total": 0, "last_done": 0, "t": None, "speed": 0.0}
        self._slot_by_name: dict[str, int] = {}
        self._free_slots: list[int] = []
        # ---- UI ----
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(10, 8, 10, 8)
        self._root.setSpacing(6)
        # 单文件模式控件
        self._single_name_label = QLabel("")
        self._single_name_label.setStyleSheet(
            "font-size: 14px; font-weight: bold;")
        self._single_name_label.setWordWrap(False)
        self._seg_bar = SegmentBar()
        self._single_stats = QLabel("")
        self._single_stats.setStyleSheet("color: #888; font-size: 12px;")
        # 批量模式控件
        self._batch_head = QLabel("")
        self._batch_head.setStyleSheet("color: #888; font-size: 12px;")
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._slots_host = QWidget()
        self._slots_lay = QVBoxLayout(self._slots_host)
        self._slots_lay.setContentsMargins(0, 0, 0, 0)
        self._slots_lay.setSpacing(2)
        self._scroll.setWidget(self._slots_host)
        self._rows: list[_SlotRow] = []
        # 空态
        self._placeholder = QLabel("⏳ 等待下载任务…")
        self._placeholder.setStyleSheet("color: #888;")
        self._root.addWidget(self._placeholder)

    # ==================== 对外（主线程） ====================

    def begin(self):
        """下载开始：显示面板并复位到空态（主线程调用）。"""
        self._reset_state()
        post_to_main(self._build_placeholder)

    def finish(self, text: str):
        """下载结束：底部汇总文案（保留面板，主线程调用）。"""
        post_to_main(lambda: self._set_summary(text))

    # ==================== 事件入口（任意线程） ====================

    def handle_event(self, event: str, *args):
        try:
            handler = getattr(self, f"_on_{event}", None)
            if handler is not None:
                handler(*args)
        except Exception:
            pass  # 面板渲染失败不影响下载

    def _reset_state(self):
        with self._lock:
            self._mode = None
            self._threads = 1
            self._total_cnt = 0
            self._done_cnt = 0
            self._single_name = ""
            self._single = {"done": 0, "total": 0, "last_done": 0,
                            "t": None, "speed": 0.0}
            self._slot_by_name.clear()
            self._free_slots.clear()

    # ---------- plan ----------

    def _on_plan(self, mode: str, count: int, threads: int):
        with self._lock:
            self._mode = mode
            self._threads = threads
            self._total_cnt = count
            self._done_cnt = 0
            if mode == "segments":
                nslots = 0
            else:
                # serial = 单槽位顺序；parallel = 线程数个槽位（IDM 队列）
                nslots = 1 if mode == "serial" else min(threads, count)
                self._free_slots = list(range(nslots))
        post_to_main(lambda: self._build_ui(mode, nslots, threads))

    # ---------- start ----------

    def _on_start(self, name: str):
        with self._lock:
            if self._mode == "segments":
                self._single_name = name
                self._single = {"done": 0, "total": 0, "last_done": 0,
                                "t": None, "speed": 0.0}
                post_to_main(lambda: self._single_started(name))
                return
            slot = self._alloc_slot(name)
        if slot is not None:
            post_to_main(lambda: self._rows[slot].bind(name))

    # ---------- seg（segments 模式） ----------

    def _on_seg(self, seg_idx: int, done: int, total: int):
        post_to_main(lambda: self._seg_bar.set_seg(seg_idx, done, total))

    # ---------- bytes ----------

    def _on_bytes(self, name: str, done: int, total: int):
        with self._lock:
            if self._mode == "segments":
                st = self._single
                now = time.monotonic()
                if st["t"] is not None:
                    dt = now - st["t"]
                    if dt >= 0.2:
                        inst = max(0.0, (done - st["last_done"]) / dt)
                        st["speed"] = 0.5 * st["speed"] + 0.5 * inst
                        st["last_done"] = done
                        st["t"] = now
                else:
                    st["t"] = now
                    st["last_done"] = done
                st["done"] = done
                st["total"] = total
                d, t, s = done, total, st["speed"]
                n = self._threads
                post_to_main(lambda: self._single_progress(d, t, s, n))
            else:
                slot = self._slot_by_name.get(name)
                if slot is not None:
                    post_to_main(lambda: self._rows[slot].progress(done, total))

    # ---------- done / skip ----------

    def _on_done(self, name: str, ok: bool):
        with self._lock:
            self._done_cnt += 1
            done, total = self._done_cnt, self._total_cnt
            if self._mode == "segments":
                d = self._single["done"]
                t = self._single["total"]
                post_to_main(lambda: self._single_finished(ok, d, t))
                return
            slot = self._slot_by_name.pop(name, None)
            if slot is not None:
                self._free_slots.append(slot)
                post_to_main(lambda: self._rows[slot].finish(ok))
        self._refresh_batch_head(done, total)

    def _on_skip(self, name: str):
        with self._lock:
            self._done_cnt += 1
            done, total = self._done_cnt, self._total_cnt
        self._refresh_batch_head(done, total)

    # ---------- 槽位分配（锁内） ----------

    def _alloc_slot(self, name: str) -> int | None:
        if not self._free_slots:
            # 并行窗口已满仍未收到 start？队列式分配兜底：不开新槽，忽略
            return None
        slot = self._free_slots.pop(0)
        self._slot_by_name[name] = slot
        return slot

    def _refresh_batch_head(self, done: int, total: int):
        with self._lock:
            n = self._threads
            mode = self._mode
        if mode == "segments":
            return
        text = (f"⬇ 多文件并行下载 · 完成 {done}/{total}"
                if mode == "parallel" else f"⬇ 下载中 · 完成 {done}/{total}")
        if mode == "parallel":
            text += f" · {n} 线程"
        post_to_main(lambda: self._batch_head.setText(text))

    # ==================== UI 构建（主线程） ====================

    def _clear_layout(self):
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0)

    def _build_placeholder(self):
        self._clear_layout()
        self._root.addWidget(self._placeholder)
        self.setFixedHeight(56)
        self.setVisible(True)

    def _build_ui(self, mode: str, nslots: int, threads: int):
        self._clear_layout()
        if mode == "segments":
            self._seg_bar.set_count(threads)
            self._single_name_label.setText("")
            self._single_stats.setText("")
            self._root.addWidget(self._single_name_label)
            self._root.addWidget(self._seg_bar)
            self._root.addWidget(self._single_stats)
            self.setFixedHeight(104)
        else:
            # 重建槽位行
            for w in (self._rows):
                w.setParent(None)
            self._rows = []
            for i in range(nslots):
                row = _SlotRow(i)
                self._slots_lay.addWidget(row)
                self._rows.append(row)
            self._batch_head.setText("⬇ 准备下载…")
            self._root.addWidget(self._batch_head)
            self._root.addWidget(self._scroll)
            self.setFixedHeight(min(46 + nslots * 30, 196))
        self.setVisible(True)

    def _single_started(self, name: str):
        self._single_name_label.setText(f"📄 {_elide(name, 60)}")
        self._single_name_label.setToolTip(name)
        self._seg_bar.set_count(self._threads)
        self._single_stats.setText(f"0% · {self._threads} 线程")

    def _single_progress(self, done: int, total: int, speed: float, threads: int):
        parts = []
        if total > 0:
            parts.append(f"{_fmt_bytes(done)} / {_fmt_bytes(total)}")
            parts.append(f"{done * 100 / total:.1f}%")
        else:
            parts.append(_fmt_bytes(done))
        if speed > 0:
            parts.append(_fmt_speed(speed))
        parts.append(f"{threads} 线程")
        self._single_stats.setText(" · ".join(parts))

    def _single_finished(self, ok: bool, done: int, total: int):
        if ok:
            self._seg_bar.set_count(self._threads)
            for i in range(self._threads):
                self._seg_bar.set_seg(i, 1, 1)
            text = f"✅ 下载完成 · {_fmt_bytes(done)} · {self._threads} 线程"
        else:
            text = f"❌ 下载失败（已下 {_fmt_bytes(done)}）"
        self._single_stats.setText(text)

    def _set_summary(self, text: str):
        # 批量模式把汇总挂到 head；单文件模式挂到 stats（不覆盖文件名）
        with self._lock:
            mode = self._mode
        if mode == "segments":
            self._single_stats.setText(text)
        else:
            self._batch_head.setText(text)
