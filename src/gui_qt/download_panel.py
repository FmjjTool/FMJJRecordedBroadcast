"""下载进度面板（全局下载中心内嵌使用）。

- 批量模式（parallel/serial）：每个下载槽位一行：序号 + 文件名 + 进度条 + 状态。
  槽位完成后自动被队列中的下一个文件占用（IDM 队列风格）；
- 单文件模式（segments）：文件名 + N 段分段进度条（每段一个下载线程）+ 统计行。

数据源：media_downloader.download_media / download_ops.do_bilibili_download 的
on_task_event 回调 → DownloadPanel.handle_event（线程安全，内部 post_to_main）。
"""

import threading
import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

from gui_qt import theme
from gui_qt.common import post_to_main

_STATE_STYLES = {
    "idle": "color: theme(muted);",
    "active": "color: theme(link);",
    "ok": "color: theme(success);",
    "fail": "color: theme(danger);",
}


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


class ElidedLabel(QLabel):
    """按控件实际宽度省略文本（完整文本进 tooltip），避免长文件名撑宽整行。"""

    def __init__(self, parent=None, elide: Qt.TextElideMode = Qt.ElideMiddle):
        super().__init__(parent)
        self._full_text = ""
        self._elide = elide
        # 不把文本长度计入最小宽度，布局才能把标签压缩到可用空间内
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def set_full_text(self, text: str):
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        self.setText(self.fontMetrics().elidedText(
            self._full_text, self._elide, max(24, self.width())))


class SegmentBar(QWidget):
    """IDM 风格分段进度条：N 个格子，每格独立填充比例（一格 = 一个下载线程）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(22)
        self._segs: list[tuple[int, int]] = []

    def set_count(self, n: int):
        self._segs = [(0, 0)] * max(1, n)
        self.update()

    def set_seg(self, i: int, done: int, total: int):
        if 0 <= i < len(self._segs):
            self._segs[i] = (done, total)
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._segs)
        if n == 0:
            return
        track = QColor(theme.color("accent_soft"))
        fill = QColor(theme.color("accent"))
        gap = 4
        cw = (w - gap * (n - 1)) / n
        bar_h = max(6, h - 4)
        for i, (done, total) in enumerate(self._segs):
            x = i * (cw + gap)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(QRectF(x, 2, cw, bar_h), 4, 4)
            if total > 0 and done > 0:
                frac = max(0.0, min(1.0, done / total))
                p.setBrush(fill)
                p.drawRoundedRect(QRectF(x, 2, max(2.0, cw * frac), bar_h), 4, 4)
        p.end()


class _SlotRow(QWidget):
    """批量模式的一行：序号 + 文件名 + 进度条 + 状态。"""

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.no_label = QLabel(f"{idx + 1}")
        self.no_label.setFixedWidth(18)
        self.no_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.no_label.setProperty("role", "caption")
        self.name_label = ElidedLabel()
        self.name_label.set_full_text("")
        self.bar = QProgressBar()
        self.bar.setFixedSize(180, 16)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setStyleSheet("QProgressBar { font-size: 11px; }")
        self.state_label = QLabel("未开始")
        self.state_label.setFixedWidth(52)
        self.state_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.state_label.setStyleSheet(
            f"font-size: 11px; {_STATE_STYLES['idle']}")
        lay.addWidget(self.no_label)
        lay.addWidget(self.name_label, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.state_label)

    def _set_state(self, state: str, text: str):
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"font-size: 11px; {_STATE_STYLES[state]}")

    def bind(self, name: str):
        self.name_label.set_full_text(name)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self._set_state("active", "下载中")

    def progress(self, done: int, total: int):
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(done * 100 / total))
        else:
            self.bar.setRange(0, 0)  # busy
        if self.state_label.text() != "下载中":
            self._set_state("active", "下载中")

    def finish(self, ok: bool):
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        if ok:
            self.bar.setValue(100)
        self._set_state("ok" if ok else "fail", "已完成" if ok else "失败")


class DownloadPanel(QFrame):
    """下载进度面板：单文件 IDM 分段模式 / 批量文件名槽位模式。

    面板高度始终贴合内容；超出下载中心可视高度时由外层滚动区接管，
    面板内部不再嵌套第二个滚动条。结构变化通过 content_changed 通知外层。

    页面用法：
        panel.begin()                     # 下载开始：显示面板并复位
        download(..., on_task_event=panel.handle_event)
        panel.finish(text)                # 结束后汇总文案
    handle_event(event, *args) 由下载线程直接调用（内部线程安全）。
    """

    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(6)
        # 单文件模式控件
        self._single_name_label = ElidedLabel(elide=Qt.ElideRight)
        self._single_name_label.setStyleSheet("font-weight: bold;")
        self._single_name_label.setWordWrap(False)
        self._seg_bar = SegmentBar()
        self._single_stats = QLabel("")
        self._single_stats.setProperty("role", "caption")
        # 批量模式控件
        self._batch_head = QLabel("")
        self._batch_head.setProperty("role", "caption")
        self._slots_host = QWidget()
        self._slots_lay = QVBoxLayout(self._slots_host)
        self._slots_lay.setContentsMargins(0, 0, 0, 0)
        self._slots_lay.setSpacing(4)
        self._rows: list[_SlotRow] = []
        # 空态
        self._placeholder = QLabel("等待下载任务…")
        self._placeholder.setProperty("role", "caption")
        self._root.addWidget(self._placeholder)
        self.setFixedHeight(max(24, self._placeholder.sizeHint().height() + 4))

    # ==================== 对外（主线程） ====================

    def begin(self):
        """下载开始：显示面板并复位到空态（主线程调用）。"""
        self._reset_state()
        self._placeholder.setText("等待下载任务…")
        post_to_main(self._build_placeholder)

    def finish(self, text: str):
        """下载结束：汇总文案（主线程调用）。"""
        post_to_main(self._set_summary, text)

    def set_status(self, text: str):
        """任意线程设置汇总文案（批量挂到状态行，单文件挂到统计行）。"""
        post_to_main(self._set_summary, text)

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
                nslots = 1 if mode == "serial" else max(1, min(threads, count))
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
            if self._mode is None:
                # 下载函数未先发 plan：按单槽位顺序下载兜底，不能只留空卡片
                self._mode = "serial"
                self._threads = 1
                self._total_cnt = 0
                self._free_slots = [0]
                post_to_main(lambda: self._build_ui("serial", 1, 1))
            slot = self._alloc_slot(name)
        if slot is not None:
            post_to_main(lambda: self._bind_row(slot, name))
            self._refresh_batch_head(self._done_cnt, self._total_cnt)

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
                    post_to_main(lambda: self._row_progress(slot, done, total))

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
                post_to_main(lambda: self._row_finish(slot, ok))
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
        if total > 0:
            text = f"已完成 {done}/{total}"
        elif done <= 0:
            text = "下载中"
        else:
            text = f"已完成 {done} 个"
        if mode == "parallel":
            text = f"并行下载 · {text} · {n} 线程"
        else:
            text = f"顺序下载 · {text}"
        post_to_main(lambda: self._batch_head.setText(text))

    # ==================== UI 构建（主线程） ====================

    def _clear_layout(self):
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)

    def _build_placeholder(self):
        self._clear_layout()
        self._root.addWidget(self._placeholder)
        self._placeholder.show()  # 被 _clear_layout hide 过，重新入局需显式复位
        self.setFixedHeight(max(24, self._placeholder.sizeHint().height() + 4))
        self.setVisible(True)
        self.content_changed.emit()

    def _reset_rows(self, nslots: int):
        while self._slots_lay.count():
            item = self._slots_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        for i in range(nslots):
            row = _SlotRow(i)
            self._slots_lay.addWidget(row)
            self._rows.append(row)

    def _build_ui(self, mode: str, nslots: int, threads: int):
        self._clear_layout()
        if mode == "segments":
            self._seg_bar.set_count(threads)
            self._single_name_label.set_full_text("")
            self._single_stats.setText("")
            for w in (self._single_name_label, self._seg_bar, self._single_stats):
                self._root.addWidget(w)
                w.show()  # _clear_layout 的 hide() 需显式复位，否则二次构建后永久不可见
            height = (self._single_name_label.sizeHint().height()
                      + self._seg_bar.minimumHeight()
                      + self._single_stats.sizeHint().height()
                      + self._root.spacing() * 2)
            self.setFixedHeight(max(72, height))
        else:
            self._reset_rows(nslots)
            self._batch_head.setText("准备下载")
            self._root.addWidget(self._batch_head)
            self._root.addWidget(self._slots_host)
            # 复用控件被 _clear_layout hide() 过，重新入局后必须 show()
            self._batch_head.show()
            self._slots_host.show()
            height = (self._batch_head.sizeHint().height()
                      + self._root.spacing()
                      + nslots * 30
                      + max(0, nslots - 1) * self._slots_lay.spacing())
            self.setFixedHeight(max(48, height))
        self.setVisible(True)
        self.content_changed.emit()

    def _bind_row(self, slot: int, name: str):
        if 0 <= slot < len(self._rows):
            self._rows[slot].bind(name)

    def _row_progress(self, slot: int, done: int, total: int):
        if 0 <= slot < len(self._rows):
            self._rows[slot].progress(done, total)

    def _row_finish(self, slot: int, ok: bool):
        if 0 <= slot < len(self._rows):
            self._rows[slot].finish(ok)

    def _single_started(self, name: str):
        self._single_name_label.set_full_text(f"{name}")
        self._seg_bar.set_count(self._threads)
        self._single_stats.setText(f"下载中 · 0% · {self._threads} 线程")

    def _single_progress(self, done: int, total: int, speed: float, threads: int):
        parts = ["下载中"]
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
            text = f"已完成 · {_fmt_bytes(done)} · {self._threads} 线程"
        else:
            text = f"失败（已下 {_fmt_bytes(done)}）"
        self._single_stats.setText(text)

    def _set_summary(self, text: str):
        # 批量模式把汇总挂到状态行；单文件模式挂到统计行（不覆盖文件名）
        with self._lock:
            mode = self._mode
        if mode is None:
            self._placeholder.setText(text or "等待下载任务…")
            self._build_placeholder()
        elif mode == "segments":
            self._single_stats.setText(text)
        else:
            self._batch_head.setText(text)
