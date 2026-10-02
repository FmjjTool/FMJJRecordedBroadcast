"""全局下载进度浮窗：多批次卡片式下载中心。

所有页面的批量下载进度统一到这里展示（不再占原页面 UI）：
- 无边框 Tool 窗口，悬浮于主窗口右下角，标题栏可拖动，✕ 仅隐藏不取消任务
- 每个下载批次一张卡片：头部（来源 + 汇总 + 关闭卡片）+ 内嵌 DownloadPanel
- 并发批次各自独立卡片，互不干扰；关闭卡片只隐藏进度，下载继续后台跑

页面用法（主线程调用）：
    from gui_qt.download_float import get_float
    card = get_float().add_batch("来源页", total=12, threads=8)  # total 未知传 None
    download(..., on_task_event=card.handle_event)              # 任意线程安全
    card.finish("✅ 完成 12 条")                                  # 头部显示汇总
事件协议与 DownloadPanel 一致：plan / start / bytes / seg / done / skip。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from gui_qt.common import icon_btn_style, post_to_main
from gui_qt.download_panel import DownloadPanel


class BatchCard(QFrame):
    """一个下载批次的卡片：头部（来源/汇总/取消/关闭）+ 内嵌 DownloadPanel。"""

    def __init__(self, source: str, cancel_cb=None, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self._cancel_cb = cancel_cb

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel(f"📦 {source}")
        title.setStyleSheet("font-weight: bold; font-size: 12px;")
        head.addWidget(title)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #888; font-size: 11px;")
        head.addWidget(self.summary_label, 1)
        if cancel_cb is not None:
            self.cancel_btn = QPushButton("取消")
            self.cancel_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
            self.cancel_btn.setToolTip("取消该批次下载（已完成的文件保留）")
            self.cancel_btn.clicked.connect(self._on_cancel)
            head.addWidget(self.cancel_btn)
        else:
            self.cancel_btn = None
        close_btn = QPushButton("✕")
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
        if self.cancel_btn is not None:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setVisible(False)

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
        win = self.window()
        if isinstance(win, DownloadFloatWindow):
            win.remove_card(self)


class DownloadFloatWindow(QWidget):
    """下载中心浮窗：标题栏（可拖动）+ 滚动卡片列表。"""

    def __init__(self, main_window=None):
        super().__init__(main_window)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self._main_window = main_window
        self._user_moved = False   # 用户拖动过则不再自动贴角
        self._drag_pos = None
        self.setFixedWidth(430)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # 标题栏（拖动区）
        self._title_bar = QWidget()
        self._title_bar.setFixedHeight(26)
        tlay = QHBoxLayout(self._title_bar)
        tlay.setContentsMargins(2, 0, 0, 0)
        tlay.setSpacing(6)
        tlabel = QLabel("⬇ 下载中心")
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
        tclose = QPushButton("✕")
        tclose.setFixedSize(24, 22)
        tclose.setStyleSheet(icon_btn_style(False))
        tclose.setToolTip("隐藏浮窗（下载继续后台进行）")
        tclose.clicked.connect(self.hide)
        tlay.addWidget(tclose)
        root.addWidget(self._title_bar)

        # 卡片滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setMaximumHeight(560)
        host = QWidget()
        self._cards_lay = QVBoxLayout(host)
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(8)
        self._cards_lay.addStretch(1)
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, 1)

        self.setStyleSheet(
            "DownloadFloatWindow { background: #f7f8fa; border: 1px solid #c8ceda;"
            " border-radius: 10px; }")

    # ==================== 对外（主线程） ====================

    def add_batch(self, source: str, total=None, threads: int = 1,
                  cancel_cb=None) -> BatchCard:
        """新建批次卡片并显示浮窗；返回卡片（handle_event 可跨线程调用）。"""
        card = BatchCard(source, cancel_cb=cancel_cb, parent=self)
        card.plan(total, threads)
        self._cards_lay.insertWidget(0, card)
        self._scroll.setVisible(True)
        self.show()
        self.raise_()
        if not self._user_moved:
            self._snap_to_corner()
        self._update_count()
        return card

    def remove_card(self, card: BatchCard):
        self._cards_lay.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self._update_count()

    # ==================== 布局 / 拖动 ====================

    def _update_count(self):
        n = max(0, self._cards_lay.count() - 1)  # 减去底部 stretch
        self._count_label.setText(f"{n} 个批次" if n else "")
        if n == 0:
            self.hide()

    def _toggle_collapse(self):
        vis = not self._scroll.isVisible()
        self._scroll.setVisible(vis)
        self._collapse_btn.setText("—" if vis else "▢")

    def _snap_to_corner(self):
        """贴主窗口右下角（主窗口隐藏/不存在时贴屏幕右下角）。"""
        host = self._main_window
        if host is None or not host.isVisible():
            screen = QApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else None
            if geo is None:
                return
            self.move(geo.right() - self.width() - 24,
                      geo.bottom() - self.height() - 24)
            return
        hg = host.geometry()
        self.move(hg.right() - self.width() - 20,
                  max(hg.top() + 10, hg.bottom() - self.height() - 20))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and ev.position().y() <= 34:
            self._drag_pos = ev.globalPosition().toPoint() - self.pos()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag_pos is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            self._user_moved = True
            ev.accept()

    def mouseReleaseEvent(self, ev):
        self._drag_pos = None
        ev.ignore()


# ==================== 全局单例 ====================

_FLOAT: DownloadFloatWindow | None = None


def get_float() -> DownloadFloatWindow:
    """获取全局下载浮窗（首次调用时创建，挂到主窗口上）。必须主线程调用。"""
    global _FLOAT
    if _FLOAT is None:
        main_win = None
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QMainWindow):
                main_win = w
                break
        _FLOAT = DownloadFloatWindow(main_win)
    return _FLOAT
