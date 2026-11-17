"""全局下载进度浮窗：多批次卡片式下载中心。

所有页面的批量下载进度统一到这里展示（不再占原页面 UI）：
- 主窗口的**子控件**（不是独立顶层窗口）：随主窗口移动/缩放/最小化，
  拖标题栏可在窗口内移动，拖右下角手柄可调整大小；仅隐藏不取消任务
- 每个下载批次一张卡片：头部（来源 + 汇总 + 关闭卡片）+ 内嵌 DownloadPanel
- 并发批次各自独立卡片，互不干扰；关闭卡片只隐藏进度，下载继续后台跑

页面用法（主线程调用）：
    from gui_qt.download_float import get_float
    card = get_float().add_batch("来源页", total=12, threads=8)  # total 未知传 None
    download(..., on_task_event=card.handle_event)              # 任意线程安全
    card.finish("完成 12 条")                                  # 头部显示汇总
事件协议与 DownloadPanel 一致：plan / start / bytes / seg / done / skip。
"""

import threading
import weakref

from PySide6.QtCore import QEvent, Qt, QTimer
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from gui_qt.common import icon_btn_style, post_to_main
from gui_qt.download_panel import DownloadPanel


class DownloadBatch:
    """无控件的下载批次，线程只持有它；窗口卸载不影响下载和取消状态。"""

    def __init__(self, source, cancel_cb=None):
        self.source = source
        self._cancel_cb = cancel_cb
        self._finished = False
        self.summary = ""
        self.dismissed = False
        self._events = []
        self._progress = {}
        self._view = None
        self._lock = threading.RLock()
        self._version = 0

    def plan(self, total, threads):
        if total:
            self.handle_event("plan", "parallel", int(total), max(1, int(threads)))

    def handle_event(self, event, *args):
        with self._lock:
            if self._finished:
                return
            if event in ("bytes", "seg"):
                # 高频进度只保存每个文件/分段的最新值，不累积每个网络包。
                self._progress[(event, args[0] if args else None)] = (event, args)
            else:
                self._events.extend(self._progress.values())
                self._progress.clear()
                self._events.append((event, args))
            self._version += 1
            post_to_main(self._render_event, self._version, event, args)

    def _render_event(self, version, event, args):
        card = self._view() if self._view else None
        if card is not None and isValid(card) and version > card._batch_version:
            card._batch_version = version
            card.handle_event(event, *args)

    def attach(self, card):
        with self._lock:
            self._view = weakref.ref(card)
            events = [*self._events, *self._progress.values()]
            card._batch_version = self._version
        for event, args in events:
            card.handle_event(event, *args)
        if self._finished:
            card._set_summary(self.summary)

    def finish(self, text):
        with self._lock:
            self.summary = text
            self._finished = True
            self._cancel_cb = None
        post_to_main(self._render_finish)

    def _render_finish(self):
        card = self._view() if self._view else None
        if card is not None and isValid(card):
            card._set_summary(self.summary)

    def cancel(self):
        if self._cancel_cb is not None:
            self._cancel_cb()


_BATCHES = []


class BatchCard(QFrame):
    """一个下载批次的卡片：头部（来源/汇总/取消/关闭）+ 内嵌 DownloadPanel。"""

    def __init__(self, source: str, cancel_cb=None, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self._cancel_cb = cancel_cb
        self._finished = False   # finish() 后置 True（下载中心据此自动收起）
        self.batch = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel(f"{source}")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        head.addWidget(title)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #888; font-size: 12px;")
        head.addWidget(self.summary_label, 1)
        if cancel_cb is not None:
            self.cancel_btn = QPushButton("取消")
            self.cancel_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
            self.cancel_btn.setToolTip("取消该批次下载（已完成的文件保留）")
            self.cancel_btn.clicked.connect(self._on_cancel)
            head.addWidget(self.cancel_btn)
        else:
            self.cancel_btn = None
        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 20)
        close_btn.setStyleSheet(icon_btn_style(False))
        close_btn.setToolTip("关闭卡片（下载继续后台进行）")
        close_btn.clicked.connect(self._dismiss)
        head.addWidget(close_btn)
        root.addLayout(head)

        self.panel = DownloadPanel(self)
        root.addWidget(self.panel)

    # ---------- 对外（主线程） ----------

    def plan(self, total: int, threads: int):
        """预声明批量模式槽位（total 已知时；未知则等下载函数自己的 plan 事件）。"""
        if total:
            self.panel.handle_event("plan", "parallel", int(total), max(1, int(threads)))

    def handle_event(self, event: str, *args):
        """下载事件入口（任意线程调用，内部 post_to_main 渲染）。"""
        self.panel.handle_event(event, *args)

    def finish(self, text: str):
        """批次结束：头部显示汇总。"""
        post_to_main(lambda: self._set_summary(text))

    # ---------- 内部 ----------

    def _set_summary(self, text: str):
        self.summary_label.setText(text)
        self._finished = True
        if self.cancel_btn is not None:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setVisible(False)
        # 通知下载中心：所有批次都结束后自动收起（延迟一点，先让用户看到结果）
        parent = self.parent()
        while parent is not None and not isinstance(parent, DownloadFloatWindow):
            parent = parent.parent()
        if isinstance(parent, DownloadFloatWindow):
            parent._schedule_auto_close()

    def _on_cancel(self):
        if self._cancel_cb is not None:
            try:
                self._cancel_cb()
            except Exception:
                pass
        if self.cancel_btn is not None:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("取消中…")

    def _dismiss(self):
        # 浮窗现在是主窗口的子控件，card.window() 会返回 MainWindow，
        # 直接取父级（卡片由 add_batch 以浮窗为 parent 创建）
        parent = self.parent()
        while parent is not None and not isinstance(parent, DownloadFloatWindow):
            parent = parent.parent()
        if isinstance(parent, DownloadFloatWindow):
            if self.batch is not None:
                self.batch.dismissed = True
            parent.remove_card(self)


class DownloadFloatWindow(QWidget):
    """下载中心浮窗：标题栏（可拖动）+ 滚动卡片列表，四周边缘/四角均可缩放。

    作为主窗口的子控件（不是独立顶层窗口）：主窗口移动/缩放/最小化时
    自动跟随，观感上就是窗口的一部分，不会飘到窗口外面找不回来；
    全部批次结束后自动收起。
    """

    _MIN_W, _MIN_H = 420, 240
    _EDGE = 6        # 四周缩放热区（px）
    _AUTO_CLOSE_MS = 1500   # 全部批次结束后延迟收起，让用户看到完成状态

    def __init__(self, main_window=None):
        super().__init__(main_window)
        self._unloading = False
        if main_window is None:
            # 极少数没有主窗口的场景（离屏脚本）→ 退化为独立无边框浮窗
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self._main_window = main_window
        # 相对主窗口右下角的留白：移动/缩放主窗口后按它重新吸附
        self._off = (20, 20)
        self._drag_pos = None
        self._resize_state = None   # (方向, 按下时全局坐标, 按下时几何)
        self.setMinimumSize(self._MIN_W, self._MIN_H)
        self.setMouseTracking(True)   # 悬停边缘时切换缩放光标

        screen = QApplication.primaryScreen()
        avail_h = screen.availableGeometry().height() if screen else 1040
        w, h = 640, max(360, int(avail_h * 0.5))
        if main_window is not None:
            # 初始尺寸不超过主窗口（子控件超出部分会被裁掉）
            w = min(w, max(self._MIN_W, main_window.width() - 40))
            h = min(h, max(self._MIN_H, main_window.height() - 40))
        self.resize(w, h)
        if main_window is not None:
            main_window.installEventFilter(self)   # 主窗口缩放时重新吸附

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # 标题栏（拖动区）
        self._title_bar = QWidget()
        self._title_bar.setFixedHeight(26)
        tlay = QHBoxLayout(self._title_bar)
        tlay.setContentsMargins(2, 0, 0, 0)
        tlay.setSpacing(6)
        tlabel = QLabel("下载中心")
        tlabel.setStyleSheet("font-weight: bold; font-size: 13px;")
        tlay.addWidget(tlabel)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #888; font-size: 11px;")
        tlay.addWidget(self._count_label, 1)
        self._collapse_btn = QPushButton("—")
        self._collapse_btn.setFixedSize(24, 22)
        self._collapse_btn.setStyleSheet(icon_btn_style(False))
        self._collapse_btn.setToolTip("收起 / 展开卡片列表")
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        tlay.addWidget(self._collapse_btn)
        tclose = QPushButton("×")
        tclose.setFixedSize(24, 22)
        tclose.setStyleSheet(icon_btn_style(False))
        tclose.setToolTip("隐藏浮窗（下载继续后台进行）")
        tclose.clicked.connect(self.close)
        tlay.addWidget(tclose)
        root.addWidget(self._title_bar)

        # 卡片滚动区：高度由窗口大小决定（四周边缘/四角都能拖动调整）
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setMinimumHeight(120)
        host = QWidget()
        self._cards_lay = QVBoxLayout(host)
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(8)
        self._cards_lay.addStretch(1)
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, 1)

        # 实底：QWidget 子类默认不画 QSS 背景（顶层窗口有窗口底色，子控件
        # 会变成透明露出后面的页面）→ 显式开启样式背景
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "DownloadFloatWindow { background: palette(window);"
            " border: 1px solid palette(shadow); border-radius: 10px; }")
        for batch in _BATCHES:
            if not batch.dismissed:
                self._add_card(batch)

    def _add_card(self, batch):
        card = BatchCard(batch.source, cancel_cb=batch.cancel if batch._cancel_cb else None,
                         parent=self)
        card.batch = batch
        self._cards_lay.insertWidget(0, card)
        batch.attach(card)
        return card

    def closeEvent(self, event):
        global _FLOAT
        self._unloading = True
        if self._main_window is not None:
            self._main_window.removeEventFilter(self)
        if _FLOAT is self:
            _FLOAT = None
        # 批次只有弱引用，窗口和进度条可以真正删除。
        self.deleteLater()
        super().closeEvent(event)

    # ==================== 对外（主线程） ====================

    def add_batch(self, source: str, total=None, threads: int = 1,
                  cancel_cb=None) -> DownloadBatch:
        """新建批次卡片并显示浮窗；返回卡片（handle_event 可跨线程调用）。"""
        batch = DownloadBatch(source, cancel_cb=cancel_cb)
        # 已结束批次只留少量汇总；活动任务不会随窗口/卡片关闭被移除。
        finished = [b for b in _BATCHES if b._finished][-19:]
        active = [b for b in _BATCHES if not b._finished]
        _BATCHES[:] = [*finished, *active, batch]
        batch.plan(total, threads)
        self._add_card(batch)
        self._scroll.setVisible(True)
        self.show()
        self.raise_()
        self._apply_anchor()
        self._update_count()
        return batch

    def remove_card(self, card: BatchCard):
        self._cards_lay.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self._update_count()

    # ==================== 完成后自动收起 ====================

    def _schedule_auto_close(self):
        """有批次 finish 后调用：全部结束后延迟收起下载中心。"""
        QTimer.singleShot(self._AUTO_CLOSE_MS, self._auto_close_finished)

    def _auto_close_finished(self):
        if self._unloading:
            return
        cards = [self._cards_lay.itemAt(i).widget()
                 for i in range(self._cards_lay.count())]
        cards = [c for c in cards if isinstance(c, BatchCard)]
        # 期间又开了新批次（有未完成卡片）→ 不收起
        if not cards or not all(getattr(c, "_finished", False) for c in cards):
            return
        for c in cards:
            if c.batch is not None:
                c.batch.dismissed = True
            self.remove_card(c)

    # ==================== 布局 / 拖动 / 缩放 ====================

    def _update_count(self):
        n = max(0, self._cards_lay.count() - 1)  # 减去底部 stretch
        self._count_label.setText(f"{n} 个批次" if n else "")
        if n == 0:
            self.close()

    def _toggle_collapse(self):
        vis = not self._scroll.isVisible()
        self._scroll.setVisible(vis)
        self._collapse_btn.setText("—" if vis else "▢")
        self._apply_anchor()

    def _update_offsets(self):
        """记录窗口相对主窗口右下角的留白（拖完/缩放完调用）。"""
        host = self._main_window
        if host is not None and not self.isWindow():
            self._off = (max(0, host.width() - self.x() - self.width()),
                         max(0, host.height() - self.y() - self.height()))

    def _apply_anchor(self):
        """按留白贴到主窗口右下角（无主窗口时贴屏幕右下角）。"""
        host = self._main_window
        if host is not None and not self.isWindow():
            # 先保证不超出主窗口（四周至少留 20px），否则窗口顶到边缘时
            # 标题栏会贴死在窗口最上方，看起来像被截掉
            max_w = max(self.minimumWidth(), host.width() - 40)
            max_h = max(self.minimumHeight(), host.height() - 40)
            if self.width() > max_w or self.height() > max_h:
                self.resize(min(self.width(), max_w), min(self.height(), max_h))
            self.move(max(0, host.width() - self.width() - self._off[0]),
                      max(0, host.height() - self.height() - self._off[1]))
            return
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if geo is None:
            return
        self.move(geo.right() - self.width() - 24,
                  geo.bottom() - self.height() - 24)

    def eventFilter(self, obj, ev):
        # 子控件不会随父窗口缩放自动重新贴角 → 主窗口尺寸变化时重新吸附
        if obj is self._main_window and self.isVisible() \
                and ev.type() in (QEvent.Resize, QEvent.Show):
            self._apply_anchor()
        return super().eventFilter(obj, ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)

    # ---------- 四周边缘 / 四角缩放 ----------

    def _edge_at(self, pos) -> str:
        """命中哪条边/哪个角（"" = 不在缩放热区）。"""
        left = pos.x() <= self._EDGE
        right = pos.x() >= self.width() - self._EDGE
        top = pos.y() <= self._EDGE
        bottom = pos.y() >= self.height() - self._EDGE
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if top:
            return "t"
        if bottom:
            return "b"
        if left:
            return "l"
        if right:
            return "r"
        return ""

    def _update_cursor(self, pos):
        cursor = {
            "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
            "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        }.get(self._edge_at(pos), Qt.ArrowCursor)
        self.setCursor(cursor)

    def _apply_resize(self, global_pos):
        """按拖拽方向调整几何：拖左/上边时同步移动窗口，保证对边不动。"""
        edge, start, geo = self._resize_state
        d = global_pos - start
        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        host = self._main_window
        child = host is not None and not self.isWindow()
        if "l" in edge:
            # 右边固定：宽度最多撑到右边缘（x 不能变负）
            w = min(max(min_w, w - d.x()), geo.x() + geo.width())
            x = geo.x() + (geo.width() - w)
        if "r" in edge:
            # 左边固定：右边缘最多到主窗口内 20px
            limit = (host.width() - 20 - geo.x()) if child else (1 << 20)
            w = min(max(min_w, w + d.x()), max(min_w, limit))
        if "t" in edge:
            h = min(max(min_h, h - d.y()), geo.y() + geo.height())
            y = geo.y() + (geo.height() - h)
        if "b" in edge:
            limit = (host.height() - 20 - geo.y()) if child else (1 << 20)
            h = min(max(min_h, h + d.y()), max(min_h, limit))
        self.setGeometry(max(0, x), max(0, y), w, h)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            edge = self._edge_at(ev.position().toPoint())
            if edge:
                self._resize_state = (edge, ev.globalPosition().toPoint(),
                                      self.geometry())
                ev.accept()
                return
            # 标题栏拖动（子控件坐标：顶部留白 8 + 标题栏 26）
            if ev.position().y() <= 40:
                self._drag_pos = ev.globalPosition().toPoint() - self.pos()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._resize_state is not None:
            self._apply_resize(ev.globalPosition().toPoint())
            ev.accept()
            return
        if self._drag_pos is not None:
            p = ev.globalPosition().toPoint() - self._drag_pos
            host = self._main_window
            if host is not None and not self.isWindow():
                # 只在主窗口内拖动，避免拖出窗口后找不回来
                p.setX(min(max(0, p.x()), max(0, host.width() - self.width())))
                p.setY(min(max(0, p.y()), max(0, host.height() - self.height())))
            self.move(p)
            ev.accept()
            return
        self._update_cursor(ev.position().toPoint())
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._resize_state is not None:
            self._resize_state = None
            self._update_offsets()
            self._update_cursor(ev.position().toPoint())
            ev.accept()
            return
        if self._drag_pos is not None:
            self._drag_pos = None
            self._update_offsets()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


# ==================== 全局单例 ====================

_FLOAT: DownloadFloatWindow | None = None


def get_float() -> DownloadFloatWindow:
    """获取全局下载浮窗（首次调用时创建，挂到主窗口上）。必须主线程调用。"""
    global _FLOAT
    if _FLOAT is None or not isValid(_FLOAT):
        main_win = None
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QMainWindow):
                main_win = w
                break
        _FLOAT = DownloadFloatWindow(main_win)
    return _FLOAT
