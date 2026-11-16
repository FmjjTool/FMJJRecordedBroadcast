"""页面挂载、会话状态与回收；后台任务的寿命独立于导航。"""

import copy
import logging
from enum import Enum

from PySide6.QtCore import QObject, QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit,
    QSlider, QSpinBox, QSplitter, QTabWidget, QTextEdit, QWidget, QListWidget,
)
from shiboken6 import isValid

from gui_qt import common


class PagePolicy(Enum):
    DEMAND = "按需"
    PROTECTED = "保重"
    CORE = "底层"


PROTECTED_PAGES = frozenset({"test_page", "bili_danmaku", "localwiki"})


def page_policy(key):
    return PagePolicy.PROTECTED if key in PROTECTED_PAGES else PagePolicy.DEMAND


def _named_widgets(page):
    """以页面属性名定位输入控件，避免重建后 QObject 地址/子树序号变化。"""
    seen = set()

    def walk(value, path):
        if id(value) in seen:
            return
        if isinstance(value, QWidget):
            seen.add(id(value))
            yield path, value
        elif isinstance(value, dict):
            seen.add(id(value))
            for key, item in value.items():
                yield from walk(item, path + (key,))
        elif isinstance(value, (list, tuple)):
            seen.add(id(value))
            for i, item in enumerate(value):
                yield from walk(item, path + (i,))

    for name, value in vars(page).items():
        yield from walk(value, (name,))


def capture_page_state(page, _seen=None):
    """只保留输入和显式业务数据，不复制线程、控件、预览或图片缓存。"""
    _seen = set() if _seen is None else _seen
    _seen.add(id(page))
    page.prepare_session_state()
    data = {}
    for name in page.SESSION_FIELDS:
        if hasattr(page, name):
            data[name] = copy.deepcopy(getattr(page, name))
    inputs = {}
    for path, w in _named_widgets(page):
        if not isValid(w):
            continue
        if isinstance(w, QLineEdit):
            # 登录凭据只由现有凭据管理器恢复，不另存一份到会话快照。
            if w.echoMode() == QLineEdit.Normal:
                inputs[path] = ("text", w.text())
        elif isinstance(w, (QPlainTextEdit, QTextEdit)) and not w.isReadOnly():
            inputs[path] = ("text", w.toPlainText())
        elif isinstance(w, QComboBox):
            inputs[path] = ("combo", (w.currentData(), w.currentText(), w.currentIndex()))
        elif isinstance(w, QAbstractButton) and w.isCheckable():
            inputs[path] = ("check", w.isChecked())
        elif isinstance(w, (QSpinBox, QDoubleSpinBox, QSlider)):
            inputs[path] = ("value", w.value())
        elif isinstance(w, QTabWidget):
            inputs[path] = ("tab", w.currentIndex())
        elif isinstance(w, QSplitter):
            inputs[path] = ("split", w.sizes())
        elif isinstance(w, QListWidget):
            inputs[path] = ("list", [(w.item(i).text(), w.item(i).checkState(),
                                      w.item(i).isSelected()) for i in range(w.count())])
    children = dict(page.saved_child_states())
    from gui_qt.pages.base import BasePage
    for path, child in _named_widgets(page):
        if isinstance(child, BasePage) and id(child) not in _seen:
            children[path] = capture_page_state(child, _seen)
    return {"data": data, "inputs": inputs, "children": children}


def restore_page_state(page, state):
    if not state:
        return
    for name, value in state.get("data", {}).items():
        setattr(page, name, copy.deepcopy(value))
    page.restore_session_data()
    # 控件信号保持屏蔽：会话恢复不触发保存配置、采集或预览。
    widgets = dict(_named_widgets(page))
    for path, (kind, value) in state.get("inputs", {}).items():
        w = widgets.get(path)
        if w is None or not isValid(w):
            continue
        blocked = w.blockSignals(True)
        try:
            if kind == "text":
                (w.setText if isinstance(w, QLineEdit) else w.setPlainText)(value)
            elif kind == "check":
                w.setChecked(value)
            elif kind == "value":
                w.setValue(value)
            elif kind == "combo":
                data, text, old_index = value
                index = w.findData(data) if data is not None else -1
                if index < 0:
                    index = w.findText(text)
                if index >= 0:
                    w.setCurrentIndex(index)
                elif w.isEditable():
                    w.setEditText(text)
            elif kind == "tab" and 0 <= value < w.count():
                w.setCurrentIndex(value)
            elif kind == "split":
                w.setSizes(value)
            elif kind == "list":
                by_text = {text: (check, selected) for text, check, selected in value}
                for i in range(w.count()):
                    item = w.item(i)
                    if item.text() in by_text:
                        check, selected = by_text[item.text()]
                        if item.flags() & Qt.ItemIsUserCheckable:
                            item.setCheckState(check)
                        item.setSelected(selected)
        finally:
            w.blockSignals(blocked)
    for path, child_state in state.get("children", {}).items():
        child = widgets.get(path)
        if child is not None:
            restore_page_state(child, child_state)
        else:
            page.restore_child_state(path, child_state)
    page.session_restored()


class PageLifecycleManager(QObject):
    """仅挂载当前页面；必要任务宿主由主程序持有，完成后自动回收。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.states = {}
        self.retained = {}
        self._closed = False
        self._reaper = QTimer(self)
        self._reaper.setInterval(1000)
        self._reaper.timeout.connect(self.reap)

    @staticmethod
    def _page_tree(page):
        from gui_qt.pages.base import BasePage
        pages, seen = [], set()

        def visit(p):
            if id(p) in seen:
                return
            seen.add(id(p))
            pages.append(p)
            # B站资源等聚合页的逻辑宿主不一定有 Qt parent。
            children = p.findChildren(BasePage)
            children.extend(w for _path, w in _named_widgets(p) if isinstance(w, BasePage))
            for child in children:
                visit(child)

        visit(page)
        return pages

    def _busy(self, page):
        for child in self._page_tree(page):
            if common.TASKS.has_owner(child) or child.has_active_tasks():
                return True
            # QThread 不能在运行中随父控件删除；包括压缩和媒体扫描线程。
            threads = child.findChildren(QThread)
            threads.extend(v for v in vars(child).values() if isinstance(v, QThread))
            if any(isValid(t) and t.isRunning() for t in threads):
                return True
        return False

    def take(self, key):
        page = self.retained.pop(key, None)
        if not self.retained:
            self._reaper.stop()
        return page

    def leave(self, key, page):
        page.on_hide()
        page.release_view_resources()
        self.app.stack.removeWidget(page)
        self.app.pages.pop(key, None)
        self.app._theme_dirty.add(key)
        page.setParent(self.app)  # 独立于 QStackedWidget，由主程序持有。
        page.hide()
        protected = page_policy(key) == PagePolicy.PROTECTED and page.has_critical_services()
        if protected or self._busy(page):
            self.retained[key] = page
            self._reaper.start()
        else:
            self._dispose(key, page)

    def _dispose(self, key, page):
        try:
            self.states[key] = capture_page_state(page)
            page.shutdown()
            for child in self._page_tree(page):
                child._disposed = True
                child.on_unload()
                child.deleteLater()
        except Exception:
            # 无法安全收尾时保留宿主并记录原因，不在半清理状态下销毁。
            logging.getLogger(__name__).exception("页面回收失败: %s", key)
            self.retained[key] = page
            self._reaper.start()

    def reap(self):
        if self._closed:
            return
        for key, page in list(self.retained.items()):
            protected = page_policy(key) == PagePolicy.PROTECTED and page.has_critical_services()
            if protected or self._busy(page):
                continue
            self.retained.pop(key)
            self._dispose(key, page)
        if not self.retained:
            self._reaper.stop()

    def shutdown(self):
        self._closed = True
        self._reaper.stop()
        for page in list(self.retained.values()):
            try:
                page.shutdown()
                for child in self._page_tree(page):
                    child._disposed = True
                    child.on_unload()
            except Exception:
                logging.getLogger(__name__).exception("后台页面退出失败")
        self.retained.clear()
        self.states.clear()
