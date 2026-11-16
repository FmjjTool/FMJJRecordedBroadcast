"""页面缓存、逐步预载及后台宿主的 Qt 回归；使用临时配置与无业务测试页。"""

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout

from toolbox import config_manager as cm
from gui_qt import common, main_window
from gui_qt.pages.base import BasePage
from gui_qt.pages.containers import TabbedPage


class ProbePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.shows = self.hides = self.stops = self.preloads = 0
        self.input = QLineEdit()
        QVBoxLayout(self).addWidget(self.input)
        self.timer = QTimer(self)
        self.timer.start(1000)

    def on_show(self):
        self.shows += 1
        self.timer.start()

    def on_hide(self):
        self.hides += 1
        self.timer.stop()

    def shutdown(self):
        self.stops += 1
        self.on_hide()

    def preload_tasks(self):
        return (self.prepare,)

    def prepare(self):
        self.preloads += 1

    def open_convert_tab(self):
        self.convert_opened = True


class ProbeTools(TabbedPage):
    TAB_KEYS = list(main_window._EXTRA_TOOLS)

    def __init__(self, app):
        super().__init__(app, [(key, ProbePage) for key in self.TAB_KEYS], lazy=True)

    def open_tab(self, key):
        self.tabs.setCurrentIndex(self.TAB_KEYS.index(key))


class PagePreloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-preload-unit-")
        root = Path(self.temp.name)
        (root / "config").mkdir()
        self.patches = [
            patch.object(cm, "get_app_root", return_value=root),
            patch.object(cm, "get_config_dir", return_value=root / "config"),
            patch.object(cm, "get_export_dir", return_value=root / "download"),
            patch.object(main_window.MainWindow, "_refresh_cookie_status"),
            patch.object(common, "load_collected"),
            patch.object(main_window, "_page_class", side_effect=self.factory),
        ]
        for p in self.patches:
            p.start()
        cm.save_convert_config({"work_dir": str(root), "tcdocs": [], "tcdoc_legacy_migrated": True})
        self.window = main_window.MainWindow()
        self.window.show()
        self.qt.processEvents()
        self.window.page_preloader.timer.stop()

    @staticmethod
    def factory(key):
        return ProbeTools if key == "info_tools" else ProbePage

    def tearDown(self):
        self.window.close()
        self.qt.removeEventFilter(self.window)
        self.window.deleteLater()
        self.qt.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def drain(self):
        preloader = self.window.page_preloader
        while preloader.queue:
            before = preloader.completed
            preloader.step()
            preloader.timer.stop()
            self.assertEqual(preloader.completed, before + 1)
            self.qt.processEvents()

    def test_all_canonical_pages_preloaded_without_activation(self):
        self.assertEqual(list(self.window.pages), ["home"])
        self.drain()
        expected = set(main_window.PAGE_CLASSES) - set(main_window._EXTRA_TOOLS) - {"toolbox_convert"}
        self.assertEqual(set(self.window.pages), expected)
        self.assertEqual(self.window.current_page, "home")
        self.assertFalse(self.window.page_preloader.errors)
        for key, page in self.window.pages.items():
            if isinstance(page, ProbePage):
                self.assertEqual(page.preloads, 1)
                if key != "home":
                    self.assertEqual(page.shows, 0)
                    self.assertFalse(page.timer.isActive())
        tools = self.window.pages["info_tools"]
        self.assertTrue(all(p is not None and p.shows == 0 for p in tools._pages))

    def test_navigation_reuses_input_and_pending_preload(self):
        self.window.show_page("tickets")
        page = self.window.pages["tickets"]
        page.input.setText("keep input")
        self.window.show_page("home")
        self.window.page_lifecycle.reap()
        self.assertFalse(page._disposed)
        self.assertIs(self.window.pages["tickets"], page)
        self.drain()
        self.window.show_page("tickets")
        self.assertIs(self.window.pages["tickets"], page)
        self.assertEqual(page.input.text(), "keep input")
        self.assertFalse(self.window.page_lifecycle.states)
        shows = page.shows
        self.window.show_page("tickets")
        self.assertEqual(page.shows, shows)

    def test_aliases_and_hidden_child_tabs(self):
        self.drain()
        tools = self.window.pages["info_tools"]
        tools.tabs.setCurrentIndex(2)
        self.assertEqual(tools._pages[2].shows, 0)
        self.window.show_page("wechat_article")
        self.assertEqual(self.window.current_page, "info_tools")
        child = tools._pages[tools.TAB_KEYS.index("wechat_article")]
        self.assertIs(tools._current, child)
        self.assertEqual(child.shows, 1)
        self.assertNotIn("wechat_article", self.window.pages)
        self.window.show_page("toolbox_convert")
        self.assertTrue(self.window.pages["toolbox_editor"].convert_opened)
        self.assertNotIn("toolbox_convert", self.window.pages)

    def test_failed_preload_continues_and_navigation_retries(self):
        def factory(key):
            if key == "tickets":
                raise ImportError("test failure")
            return self.factory(key)
        with patch.object(main_window, "_page_class", side_effect=factory), self.assertLogs("gui_qt.page_preloader", "ERROR"):
            self.drain()
        self.assertIn("tickets", self.window.page_preloader.errors)
        self.assertIn("performance", self.window.pages)
        self.assertNotIn("tickets", self.window.pages)
        self.window.show_page("tickets")
        self.assertIn("tickets", self.window.pages)

    def test_hidden_background_host_and_shutdown_cancel_queue(self):
        self.window.show_page("tickets")
        page = self.window.pages["tickets"]
        gate = threading.Event()
        results = []
        common.run_bg(lambda: (gate.wait(2), 7)[1],
                      lambda result, err: results.append(result), owner=page)
        self.window.show_page("home")
        self.assertTrue(common.TASKS.has_owner(page))
        self.assertFalse(page._disposed)
        gate.set()
        deadline = time.monotonic() + 3
        while not results and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(.001)
        self.assertEqual(results, [7])
        self.assertFalse(common.TASKS.has_owner(page))
        self.window.close()
        self.assertTrue(page._disposed)
        self.assertEqual(page.stops, 1)
        self.assertFalse(self.window.page_preloader.queue)
        self.assertFalse(self.window.page_preloader.timer.isActive())
        self.window.page_preloader.step()
        self.window.show_page("asset")
        self.assertNotIn("asset", self.window.pages)

    def test_danmaku_documents_survive_hidden_cache(self):
        from gui_qt.pages.bili_danmaku import BiliDanmakuPage
        page = BiliDanmakuPage(self.window)
        self.window.pages["bili_danmaku"] = page
        self.window.stack.addWidget(page)
        document = page.chat_gift_box.document()
        for _ in range(3):
            self.window.show_page("bili_danmaku")
            self.window.show_page("home")
            self.assertIs(page.chat_gift_box.document(), document)
            self.assertFalse(page._countdown_timer.isActive())
            self.assertFalse(page.connected)

    def test_hidden_help_overlay_has_no_application_filter(self):
        from gui_qt.help_overlay import HelpOverlay
        page = self.window.pages["home"]
        overlay = HelpOverlay(page, "Help", "<p><a href='https://example.com'>Link</a></p>")
        anchor = QPushButton("Help", page)
        anchor.setCheckable(True)
        page.layout().addWidget(anchor)
        overlay.set_anchor(anchor)
        self.assertFalse(overlay._app_filter_installed)
        overlay.toggle()
        self.assertTrue(overlay._app_filter_installed)
        self.window.show_page("tickets")
        self.assertFalse(overlay._open)
        self.assertFalse(overlay._app_filter_installed)

    def test_editor_pauses_without_reloading_cached_source(self):
        from gui_qt.pages.editor import EditorPage
        page = EditorPage(self.window)
        self.window.pages["toolbox_editor"] = page
        self.window.stack.addWidget(page)
        page.current_path = str(Path(self.temp.name) / "video.mp4")
        source = QUrl.fromLocalFile(page.current_path)
        with patch.object(page.player, "source", return_value=source), \
             patch.object(page.player, "setSource") as set_source, \
             patch.object(page.player, "pause") as pause:
            page.on_show()
            page.on_hide()
            set_source.assert_not_called()
            self.assertEqual(pause.call_count, 2)


if __name__ == "__main__":
    unittest.main()
