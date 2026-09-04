"""媒体筛选/媒体压缩页面冒烟与回归测试。

覆盖：
- eventFilter 对 QWindow 对象不再抛 TypeError（拖放源路径报错回归）
- 深色主题下两页面强制渲染无异常（paintEvent 异常会直接抛出）
- 压缩产物 MD5 标记已分类 + 输出目录记忆（工作目录内才记忆）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QWindow
from PySide6.QtWidgets import QApplication, QWidget

import toolbox.config_manager as cm
import image_sorter.settings as st

_TMP = Path(tempfile.mkdtemp(prefix="fm_media_pages_"))
_orig_cm_dir = cm.get_config_dir
_orig_st_dir = st.get_config_dir
cm.get_config_dir = lambda: _TMP
st.get_config_dir = lambda: _TMP

_app = QApplication.instance() or QApplication(sys.argv)


class _FakeApp:
    def __init__(self):
        self.work_dir = _TMP

    def toast(self, *args, **kwargs):
        pass

    def show_page(self, *args, **kwargs):
        pass


class TestEventFilterQWindow(unittest.TestCase):
    """拖入源文件地址时，应用级事件过滤会收到 QWindow 为 obj 的 KeyPress。"""

    @classmethod
    def setUpClass(cls):
        from gui_qt.pages.image_sorter import ImageSorterPage

        cls.page = ImageSorterPage(_FakeApp())

    def test_qwindow_no_typeerror(self):
        w = QWindow()
        ke = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.KeyboardModifier.NoModifier)
        self.assertIs(self.page.eventFilter(w, ke), False)

    def test_child_widget_handled(self):
        child = QWidget(self.page)
        ke = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.KeyboardModifier.NoModifier)
        self.page.show()
        try:
            self.assertIsInstance(self.page.eventFilter(child, ke), bool)
        finally:
            self.page.hide()

    def test_foreign_widget_passthrough(self):
        other = QWidget()
        ke = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.KeyboardModifier.NoModifier)
        self.page.show()
        try:
            self.assertIs(self.page.eventFilter(other, ke), False)
        finally:
            self.page.hide()

    @classmethod
    def tearDownClass(cls):
        cls.page.shutdown()


class TestMediaCompressPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui_qt.pages.media_compress import MediaCompressPage

        cls.page = MediaCompressPage(_FakeApp())

    def test_mark_output_classified(self):
        work = _TMP / "sorter_work" / "默认"
        work.mkdir(parents=True, exist_ok=True)
        st.set_setting("work_dir", str(_TMP / "sorter_work"))
        st.set_list("classified_target_folders", [])
        try:
            out = work / "pic.webp"
            out.write_bytes(b"\x00" * 1024)
            self.page._mark_output_classified(out)
            folders = st.get_list("classified_target_folders")
            self.assertTrue(
                any(str(work.resolve()) == f for f in folders), f"输出目录未记忆: {folders}"
            )
        finally:
            st.set_setting("work_dir", "")
            st.set_list("classified_target_folders", [])

    def test_outside_workdir_not_remembered(self):
        outside = _TMP / "elsewhere"
        outside.mkdir(exist_ok=True)
        st.set_setting("work_dir", str(_TMP / "sorter_work"))
        st.set_list("classified_target_folders", [])
        try:
            self.page._remember_classified_target_folder(outside)
            self.assertEqual(st.get_list("classified_target_folders"), [])
        finally:
            st.set_setting("work_dir", "")
            st.set_list("classified_target_folders", [])

    def test_no_workdir_safe(self):
        st.set_setting("work_dir", "")
        self.page._remember_classified_target_folder(_TMP)

    @classmethod
    def tearDownClass(cls):
        cls.page.shutdown()


class TestDarkThemeRender(unittest.TestCase):
    """深色主题下 grab() 强制渲染，paintEvent 内异常会直接抛出。"""

    def test_render_both_pages_dark(self):
        from gui_qt import theme
        from gui_qt.pages.image_sorter import ImageSorterPage
        from gui_qt.pages.media_compress import MediaCompressPage

        saved = theme._state.get("dark", False)
        theme._state["dark"] = True
        try:
            theme.install_auto_remap()
            theme.apply_theme(_app)
            for page_cls in (ImageSorterPage, MediaCompressPage):
                page = page_cls(_FakeApp())
                page.resize(1200, 800)
                pix = page.grab()
                self.assertFalse(pix.isNull())
                page.shutdown()
        finally:
            theme._state["dark"] = saved
            theme.apply_theme(_app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
