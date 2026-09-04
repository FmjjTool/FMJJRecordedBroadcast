"""媒体筛选页布局与缓存迁移回归测试。

覆盖：
- 文件列表 IconMode 窄面板两列换行（viewport 开销 2W+29+滚动条预留公式回归）
- 文件名标签两行换行 + 路径标签中段省略号（非全屏看不清问题）
- 筛选页缓存按钮已移除（清理入口迁移至全局设置）
- 全局设置「清理媒体筛选缓存」一键直清（无弹窗：扫描/缩略图/MD5 清空，
  已分类 MD5 记录保留，已加载页面的内存预览缓存联动清空）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import toolbox.config_manager as cm
import image_sorter.settings as st
from image_sorter.constants import FileStatus

_TMP = Path(tempfile.mkdtemp(prefix="fm_sorter_layout_"))
_orig_cm_dir = cm.get_config_dir
_orig_st_dir = st.get_config_dir
cm.get_config_dir = lambda: _TMP
st.get_config_dir = lambda: _TMP

_app = QApplication.instance() or QApplication(sys.argv)


def _pump(ms=100):
    """离屏事件泵（QEventLoop 版，禁 busy-loop：会淹没事件队列）。"""
    from PySide6.QtCore import QEventLoop

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class _FakeApp:
    def __init__(self):
        self.work_dir = _TMP
        self.pages = {}
        self.toasts = []

    def toast(self, msg, level="info", **kwargs):
        self.toasts.append((msg, level))

    def show_page(self, *args, **kwargs):
        pass


class TestFileListTwoColumns(unittest.TestCase):
    """窄面板下 IconMode 文件卡片必须排成两列（曾退化成单列）。"""

    PANEL_WIDTHS = (263, 330, 440)

    def _build(self, width):
        from gui_qt.pages.image_sorter_widgets import FileListPanel

        panel = FileListPanel()
        panel.resize(width, 600)
        panel.show()
        _app.processEvents()
        files = [_TMP / f"w{width}_img_{i}.png" for i in range(6)]
        for i, fp in enumerate(files):
            panel.add_file(fp, None, FileStatus.NORMAL, refresh=(i == len(files) - 1))
        _app.processEvents()
        panel._folder_collapsed = {}
        for i in range(panel.file_list.count()):
            panel.file_list.item(i).setHidden(False)
        _app.processEvents()
        panel.file_list.doItemsLayout()
        _app.processEvents()
        return panel, files

    def test_two_columns_at_typical_widths(self):
        for width in self.PANEL_WIDTHS:
            with self.subTest(width=width):
                panel, files = self._build(width)
                try:
                    xs = {
                        panel.file_list.visualItemRect(panel._file_items[fp]).x()
                        for fp in files
                    }
                    self.assertGreaterEqual(
                        len(xs), 2,
                        f"panel={width} 文件卡片退化为单列: item_width="
                        f"{panel._layout_metrics['item_width']}",
                    )
                finally:
                    panel.hide()
                    panel.deleteLater()
                    _app.processEvents()

    def test_name_label_two_lines(self):
        panel, files = self._build(330)
        try:
            lbl = panel._file_name_labels[files[0]]
            self.assertTrue(lbl.wordWrap())
            self.assertEqual(lbl.height(), 36)
            self.assertEqual(lbl.toolTip(), str(files[0]))
        finally:
            panel.hide()
            panel.deleteLater()
            _app.processEvents()


class TestElidedPathLabel(unittest.TestCase):
    def test_middle_elide_and_tooltip(self):
        from gui_qt.pages.image_sorter import ElidedPathLabel

        full = r"D:\some\very\long\path\to\a\folder\with\many\segments"
        lbl = ElidedPathLabel()
        try:
            lbl.set_full_text(full)
            lbl.resize(120, 20)
            lbl.show()
            _app.processEvents()
            self.assertIn("…", lbl.text())
            self.assertNotEqual(lbl.text(), full)
            self.assertEqual(lbl.toolTip(), full)

            lbl.resize(700, 20)
            _app.processEvents()
            self.assertEqual(lbl.text(), full)
        finally:
            lbl.hide()
            lbl.deleteLater()

    def test_set_full_text_same_value_noop(self):
        from gui_qt.pages.image_sorter import ElidedPathLabel

        lbl = ElidedPathLabel("abc")
        try:
            lbl.set_full_text("abc")
            self.assertEqual(lbl.text(), "abc")
        finally:
            lbl.deleteLater()


class TestSorterCacheUiMoved(unittest.TestCase):
    """筛选页不再有清理缓存/打开缓存目录按钮，方法一并移除。"""

    @classmethod
    def setUpClass(cls):
        from gui_qt.pages.image_sorter import ImageSorterPage

        cls.app = _FakeApp()
        cls.page = ImageSorterPage(cls.app)

    def test_cache_methods_removed(self):
        for name in ("_on_clear_cache", "_show_cache_clear_options_dialog",
                     "_on_open_cache_folder"):
            self.assertFalse(hasattr(self.page, name), f"{name} 应已移除")

    def test_cache_buttons_removed(self):
        from PySide6.QtWidgets import QPushButton as Btn

        texts = [b.text() for b in self.page.findChildren(Btn)]
        for text in texts:
            self.assertNotIn("清理缓存", text)
            self.assertNotIn("打开缓存目录", text)

    def test_clear_memory_caches(self):
        self.page._video_cover_cache["a"] = 1
        self.page._video_info_cache["b"] = 2
        self.page.clear_memory_caches()
        self.assertEqual(self.page._video_cover_cache, {})
        self.assertEqual(self.page._video_info_cache, {})

    @classmethod
    def tearDownClass(cls):
        cls.page.shutdown()


class TestSettingsMediaCacheClean(unittest.TestCase):
    """全局设置一键清理媒体筛选缓存（无弹窗直接清，已分类记录保留）。"""

    @classmethod
    def setUpClass(cls):
        from gui_qt.pages.settings import GlobalSettingsPage
        from gui_qt.pages.image_sorter import ImageSorterPage

        cls.app = _FakeApp()
        cls.sorter = ImageSorterPage(cls.app)
        cls.app.pages["image_sorter"] = cls.sorter
        cls.page = GlobalSettingsPage(cls.app)

        from image_sorter.core.sqlite_cache import SQLiteCacheStore

        cls.store = SQLiteCacheStore(cls._cache_root() / "cache.db")

    @staticmethod
    def _cache_root() -> Path:
        return st.get_cache_root_dir()

    @classmethod
    def _seed_caches(cls):
        root = cls._cache_root()
        (root / "scan" / "grp").mkdir(parents=True, exist_ok=True)
        (root / "scan" / "grp" / "entry.json").write_text("{}", encoding="utf-8")
        (root / "thumbnails").mkdir(parents=True, exist_ok=True)
        (root / "thumbnails" / "thumb.png").write_bytes(b"\x89PNG")
        cls.store.upsert_md5(_TMP / "a.png", 100, 1000, "a" * 32)
        cls.store.mark_classified_md5("b" * 32)

    def _find_media_cache_btn(self):
        btns = [b for b in self.page.findChildren(QPushButton)
                if "清理媒体筛选缓存" in b.text()]
        self.assertEqual(len(btns), 1, "全局设置应有且仅有一个「清理媒体筛选缓存」按钮")
        return btns[0]

    def test_clean_media_sorter_cache_direct(self):
        self._seed_caches()
        self.sorter._video_cover_cache["x"] = 1
        self.sorter._video_info_cache["y"] = 2

        root = self._cache_root()
        self.assertTrue((root / "scan" / "grp" / "entry.json").exists())
        self.assertTrue((root / "thumbnails" / "thumb.png").exists())

        btn = self._find_media_cache_btn()
        btn.click()

        ok = False
        for _ in range(50):
            _pump(100)
            if "已清理" in self.page.clean_result_label.text():
                ok = True
                break
        self.assertTrue(ok, f"清理未完成: {self.page.clean_result_label.text()}")

        # 磁盘缓存清空（目录会按惰性重建保留空壳）
        self.assertFalse(list((root / "scan").rglob("entry.json")))
        self.assertFalse(list((root / "thumbnails").rglob("thumb.png")))
        # MD5 计算缓存清空，已分类记录保留（get_cached_md5 未命中返回空串）
        self.assertEqual(self.store.get_cached_md5(_TMP / "a.png", 100, 1000), "")
        self.assertIn("b" * 32, self.store.load_classified_md5_set())
        # 已加载页面的内存预览缓存联动清空
        self.assertEqual(self.sorter._video_cover_cache, {})
        self.assertEqual(self.sorter._video_info_cache, {})
        # toast + 结果标签反馈
        self.assertIn("✅", self.page.clean_result_label.text())
        self.assertTrue(any("媒体筛选缓存已清理" in m and lv == "success"
                            for m, lv in self.app.toasts))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.store.close() if hasattr(cls.store, "close") else None
        except Exception:
            pass
        cls.sorter.shutdown()
        cls.page.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
