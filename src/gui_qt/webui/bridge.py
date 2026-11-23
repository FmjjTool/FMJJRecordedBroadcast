"""WebUI 事件桥：动作在 GUI 主线程执行，快照由主线程生成后推给 HTTP 服务。

HTTP 线程只调用线程安全的 post_action / request_refresh / wait_snapshot，
不直接访问 Qt 控件；动作先排队到主线程，避免跨线程操作控件。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget,
    QPlainTextEdit, QScrollArea, QSlider, QSpinBox, QTabWidget, QTableWidget,
    QTextEdit, QTreeWidget, QWidget,
)

from gui_qt.page_index import PAGE_CLASSES, page_title
from gui_qt.webui import serializer

try:
    from PySide6.QtTest import QTest
except Exception:  # 打包环境缺少 QtTest 时退回信号模拟
    QTest = None

_log = logging.getLogger(__name__)

_TICK_MS = 250
_REFRESH_INTERVAL = 0.9


class WebUiBridge(QObject):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._cond = threading.Condition()
        self._version = 0
        self._json = ""
        self._subscribers = 0
        self._forced = True
        self._last_build = 0.0
        self._last_signature = ""
        self._soon = False
        self._id_map: dict[str, QWidget] = {}
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------- 供 HTTP 线程调用 ----------

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self) -> None:
        with self._cond:
            self._subscribers += 1
        self.request_refresh()

    def unsubscribe(self) -> None:
        with self._cond:
            self._subscribers = max(0, self._subscribers - 1)

    def request_refresh(self) -> None:
        self._forced = True

    def post_action(self, action: dict[str, Any]) -> None:
        payload = json.dumps(action, ensure_ascii=False)
        QMetaObject.invokeMethod(
            self, "apply_action", Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, payload))

    def wait_snapshot(self, since: int, timeout: float) -> tuple[int, str] | None:
        """等待比 since 新的快照；超时返回 None。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._version <= since and not self._closed:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return None
                self._cond.wait(remain)
            if self._closed:
                return None
            return self._version, self._json

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        with self._cond:
            self._cond.notify_all()

    # ---------- GUI 线程 ----------

    def _page(self) -> tuple[str, QWidget] | None:
        key = getattr(self._window, "current_page", "")
        pages = getattr(self._window, "pages", {})
        page = pages.get(key)
        if page is None:
            return None
        return key, page

    def _tick(self) -> None:
        if self._closed:
            return
        now = time.monotonic()
        with self._cond:
            subscribers = self._subscribers
        if not self._forced:
            if subscribers <= 0:
                return
            if now - self._last_build < _REFRESH_INTERVAL:
                return
        self._publish()

    def _publish(self) -> None:
        current = self._page()
        if current is None:
            self._forced = False
            return
        key, page = current
        try:
            snapshot, id_map = serializer.build_snapshot(
                page, key, page_title(key), 0)
            signature = json.dumps(
                snapshot, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            _log.exception("WebUI 快照生成失败")
            self._forced = False
            return
        self._id_map = id_map
        self._forced = False
        self._last_build = time.monotonic()
        if signature == self._last_signature:
            return
        self._last_signature = signature
        snapshot["v"] = self._version + 1
        text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        with self._cond:
            self._version += 1
            self._json = text
            self._cond.notify_all()

    @Slot(str)
    def apply_action(self, payload: str) -> None:
        try:
            action = json.loads(payload)
        except Exception:
            return
        if not isinstance(action, dict):
            return
        try:
            self._dispatch(action)
        except Exception:
            _log.exception("WebUI 动作执行失败：%s", action.get("a"))
        if action.get("a") == "scroll":
            self._publish_soon()
        else:
            self._publish()

    def _publish_soon(self) -> None:
        if self._soon:
            return
        self._soon = True
        QTimer.singleShot(50, self._publish_after_scroll)

    def _publish_after_scroll(self) -> None:
        self._soon = False
        self._publish()

    def _dispatch(self, action: dict[str, Any]) -> None:
        name = str(action.get("a") or "")
        if name == "page":
            key = str(action.get("key") or "")
            if key in PAGE_CLASSES:
                self._window.show_page(key)
            return
        widget = self._id_map.get(str(action.get("id") or ""))
        if widget is None or not widget.isVisible() or not widget.isEnabled():
            return
        if name == "click":
            self._click(widget)
        elif name in ("text", "textCommit"):
            self._set_text(widget, str(action.get("value") or ""),
                           commit=name == "textCommit")
        elif name == "index":
            self._set_index(widget, action.get("value"))
        elif name == "value":
            self._set_value(widget, action.get("value"))
        elif name == "tab":
            self._set_tab(widget, action.get("value"))
        elif name == "scroll":
            self._scroll(widget, action.get("dy", 0), action.get("dx", 0))
        elif name == "cellClick":
            self._cell_click(widget, action, double=False)
        elif name == "cellDblClick":
            self._cell_click(widget, action, double=True)
        elif name == "headerClick":
            self._header_click(widget, action.get("c"))
        elif name in ("itemClick", "itemDblClick"):
            self._item_click(widget, action, double=name == "itemDblClick")
        elif name == "cellCheck":
            self._cell_check(widget, action)
        elif name == "itemCheck":
            self._item_check(widget, action)

    def _click(self, widget: QWidget) -> None:
        if not isinstance(widget, QAbstractButton):
            return
        menu = getattr(widget, "menu", None)
        if callable(menu) and menu() is not None:
            return
        if not widget.isEnabled():
            return
        widget.click()

    def _set_text(self, widget: QWidget, value: str, commit: bool) -> None:
        if isinstance(widget, QLineEdit):
            if widget.isReadOnly() or widget.echoMode() != QLineEdit.Normal:
                return
            if widget.text() != value:
                widget.setText(value)
                try:
                    widget.textEdited.emit(value)
                except Exception:
                    pass
            if commit:
                try:
                    widget.editingFinished.emit()
                except Exception:
                    pass
        elif isinstance(widget, (QPlainTextEdit, QTextEdit)):
            if widget.isReadOnly():
                return
            if widget.toPlainText() != value:
                widget.setPlainText(value)

    def _set_index(self, widget: QWidget, value: Any) -> None:
        if not isinstance(widget, QComboBox):
            return
        try:
            index = int(value)
        except (TypeError, ValueError):
            return
        if 0 <= index < widget.count():
            widget.setCurrentIndex(index)

    def _set_value(self, widget: QWidget, value: Any) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(number)
        elif isinstance(widget, QSlider):
            widget.setValue(int(number))

    def _set_tab(self, widget: QWidget, value: Any) -> None:
        if not isinstance(widget, QTabWidget):
            return
        try:
            index = int(value)
        except (TypeError, ValueError):
            return
        if 0 <= index < widget.count():
            widget.setCurrentIndex(index)

    def _scroll(self, widget: QWidget, dy: Any, dx: Any) -> None:
        try:
            delta_y = int(dy)
            delta_x = int(dx)
        except (TypeError, ValueError):
            return
        if isinstance(widget, QScrollArea):
            vbar = widget.verticalScrollBar()
            hbar = widget.horizontalScrollBar()
            if vbar is not None and delta_y:
                vbar.setValue(vbar.value() + delta_y)
            if hbar is not None and delta_x:
                hbar.setValue(hbar.value() + delta_x)
            return
        if not isinstance(widget, (QTableWidget, QListWidget, QTreeWidget)):
            return
        row_height = self._row_height(widget)
        vsteps = int(round(delta_y / row_height)) if row_height else 0
        if delta_y and not vsteps:
            vsteps = 1 if delta_y > 0 else -1
        hsteps = int(round(delta_x / 40)) if delta_x else 0
        if delta_x and not hsteps:
            hsteps = 1 if delta_x > 0 else -1
        vbar = widget.verticalScrollBar()
        hbar = widget.horizontalScrollBar()
        if vbar is not None and vsteps:
            vbar.setValue(vbar.value() + vsteps)
        if hbar is not None and hsteps:
            hbar.setValue(hbar.value() + hsteps)

    @staticmethod
    def _row_height(widget: QWidget) -> int:
        if isinstance(widget, QTableWidget):
            row = widget.rowAt(0)
            if row < 0:
                row = 0
            height = widget.rowHeight(row) if widget.rowCount() else 0
            if height <= 0:
                height = widget.verticalHeader().defaultSectionSize()
            return max(1, int(height))
        if isinstance(widget, QListWidget):
            item = widget.item(0)
            if item is not None:
                rect = widget.visualItemRect(item)
                if rect.height() > 0:
                    return int(rect.height())
        hint = widget.sizeHintForRow(0)
        return int(hint) if hint > 0 else 24

    def _cell_click(self, widget: QWidget, action: dict[str, Any], double: bool) -> None:
        if not isinstance(widget, QTableWidget):
            return
        try:
            row = int(action.get("r"))
            col = int(action.get("c"))
        except (TypeError, ValueError):
            return
        if not (0 <= row < widget.rowCount() and 0 <= col < widget.columnCount()):
            return
        rect = widget.visualRect(widget.model().index(row, col))
        if not rect.isValid():
            return
        viewport = widget.viewport()
        pos = rect.center()
        if QTest is not None:
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, pos)
            if double:
                QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton,
                                  Qt.KeyboardModifier.NoModifier, pos)
        else:
            widget.setCurrentCell(row, col)
            widget.cellClicked.emit(row, col)
            if double:
                widget.cellDoubleClicked.emit(row, col)

    def _cell_check(self, widget: QWidget, action: dict[str, Any]) -> None:
        if not isinstance(widget, QTableWidget):
            return
        try:
            row = int(action.get("r"))
            col = int(action.get("c"))
        except (TypeError, ValueError):
            return
        item = widget.item(row, col)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Checked if action.get("value") else Qt.CheckState.Unchecked)

    def _item_check(self, widget: QWidget, action: dict[str, Any]) -> None:
        state = (Qt.CheckState.Checked if action.get("value")
                 else Qt.CheckState.Unchecked)
        if isinstance(widget, QListWidget):
            try:
                row = int(action.get("r"))
            except (TypeError, ValueError):
                return
            item = widget.item(row)
            if item is not None:
                item.setCheckState(state)
            return
        if isinstance(widget, QTreeWidget):
            item = self._tree_item(widget, str(action.get("p") or ""))
            if item is not None:
                item.setCheckState(0, state)

    def _header_click(self, widget: QWidget, value: Any) -> None:
        if not isinstance(widget, QTableWidget):
            return
        try:
            col = int(value)
        except (TypeError, ValueError):
            return
        header = widget.horizontalHeader()
        if header is None or not (0 <= col < widget.columnCount()):
            return
        x = header.sectionViewportPosition(col) + header.sectionSize(col) // 2
        viewport = header.viewport()
        pos = QPoint(x, viewport.height() // 2)
        if QTest is not None:
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, pos)
        else:
            header.sectionClicked.emit(col)

    def _item_click(self, widget: QWidget, action: dict[str, Any], double: bool) -> None:
        if isinstance(widget, QListWidget):
            try:
                row = int(action.get("r"))
            except (TypeError, ValueError):
                return
            item = widget.item(row)
            if item is None or item.isHidden():
                return
            rect = widget.visualItemRect(item)
            viewport = widget.viewport()
            pos = rect.center()
            if QTest is not None:
                QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, pos)
                if double:
                    QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier, pos)
            else:
                widget.setCurrentItem(item)
                widget.itemClicked.emit(item)
                if double:
                    widget.itemDoubleClicked.emit(item)
            return
        if isinstance(widget, QTreeWidget):
            item = self._tree_item(widget, str(action.get("p") or ""))
            if item is None:
                return
            rect = widget.visualItemRect(item)
            viewport = widget.viewport()
            pos = rect.center()
            if QTest is not None:
                QTest.mouseClick(viewport, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier, pos)
                if double:
                    QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier, pos)
            else:
                widget.setCurrentItem(item)
                widget.itemClicked.emit(item, 0)
                if double:
                    widget.itemDoubleClicked.emit(item, 0)

    @staticmethod
    def _tree_item(tree: QTreeWidget, path: str):
        if not path:
            return None
        node = None
        for part in path.split("."):
            try:
                index = int(part)
            except ValueError:
                return None
            if node is None:
                if not (0 <= index < tree.topLevelItemCount()):
                    return None
                node = tree.topLevelItem(index)
            else:
                if not (0 <= index < node.childCount()):
                    return None
                node = node.child(index)
        return node
