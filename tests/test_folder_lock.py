"""默认工作文件夹 / 统一导出文件夹「锁定」功能测试。

覆盖：
- 配置键注册与持久化（work_dir_locked / export_dir_locked）
- 全局设置页：锁定复选框改动即保存、on_show 恢复勾选不触发保存、状态行显示锁定标记
- 主窗口 dropEvent：锁定时拖入文件夹/文件不改变工作目录，解锁后恢复切换
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QMainWindow

import toolbox.config_manager as cm

_TMP = Path(tempfile.mkdtemp(prefix="fm_folder_lock_"))
cm.get_config_dir = lambda: _TMP

_app = QApplication.instance() or QApplication(sys.argv)


def _set_cfg(**kv):
    cfg = cm.load_convert_config()
    cfg.update(kv)
    cm.save_convert_config(cfg)


def _drop_paths(widget, paths):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, md,
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(ev)


class _FakeApp:
    def __init__(self):
        self.work_dir = str(_TMP)

    def toast(self, *args, **kwargs):
        pass

    def show_page(self, *args, **kwargs):
        pass


class TestFolderLockConfig(unittest.TestCase):
    def setUp(self):
        _set_cfg(work_dir_locked=False, export_dir_locked=False)

    def test_keys_registered_default_false(self):
        cfg = cm.load_convert_config()
        self.assertIn("work_dir_locked", cfg)
        self.assertIn("export_dir_locked", cfg)
        self.assertFalse(cfg["work_dir_locked"])
        self.assertFalse(cfg["export_dir_locked"])

    def test_round_trip(self):
        _set_cfg(work_dir_locked=True, export_dir_locked=True)
        cfg = cm.load_convert_config()
        self.assertTrue(cfg["work_dir_locked"])
        self.assertTrue(cfg["export_dir_locked"])
        _set_cfg(work_dir_locked=False, export_dir_locked=False)
        cfg = cm.load_convert_config()
        self.assertFalse(cfg["work_dir_locked"])
        self.assertFalse(cfg["export_dir_locked"])


class TestSettingsLockUI(unittest.TestCase):
    def setUp(self):
        _set_cfg(work_dir_locked=False, export_dir_locked=False, work_dir="")
        from gui_qt.pages.settings import GlobalSettingsPage
        self.page = GlobalSettingsPage(_FakeApp())

    def test_toggle_persists(self):
        self.assertFalse(self.page.workdir_lock_cb.isChecked())
        self.assertFalse(self.page.exportdir_lock_cb.isChecked())
        self.page.workdir_lock_cb.setChecked(True)
        self.assertTrue(cm.load_convert_config()["work_dir_locked"])
        self.page.exportdir_lock_cb.setChecked(True)
        self.assertTrue(cm.load_convert_config()["export_dir_locked"])
        self.page.workdir_lock_cb.setChecked(False)
        self.assertFalse(cm.load_convert_config()["work_dir_locked"])
        self.page.exportdir_lock_cb.setChecked(False)
        self.assertFalse(cm.load_convert_config()["export_dir_locked"])

    def test_on_show_restores_without_save(self):
        """on_show 恢复勾选须屏蔽信号：程序 setChecked 不应触发「改动即保存」。"""
        fired = []
        self.page.workdir_lock_cb.toggled.connect(lambda v: fired.append(v))
        _set_cfg(work_dir_locked=True, export_dir_locked=True)
        self.page.on_show()
        self.assertTrue(self.page.workdir_lock_cb.isChecked())
        self.assertTrue(self.page.exportdir_lock_cb.isChecked())
        self.assertEqual(fired, [])
        _set_cfg(work_dir_locked=False, export_dir_locked=False)
        self.page.on_show()
        self.assertFalse(self.page.workdir_lock_cb.isChecked())

    def test_status_shows_lock_prefix(self):
        self.page.workdir_entry.setText(str(_TMP))
        self.page.workdir_lock_cb.setChecked(True)
        self.assertIn("🔒", self.page.workdir_status.text())
        self.page.exportdir_lock_cb.setChecked(True)
        self.assertIn("🔒", self.page.exportdir_status.text())
        self.page.workdir_lock_cb.setChecked(False)
        self.assertNotIn("🔒", self.page.workdir_status.text())


class TestDropEventLock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui_qt.main_window import MainWindow

        class _Harness(MainWindow):
            """跳过 MainWindow 重型构造，仅驱动真实 dropEvent 分支。"""

            def __init__(self, work_dir):
                QMainWindow.__init__(self)
                self.pages = {}
                self.current_page = "info_parse"  # 非拖放白名单页 → 走切换工作目录分支
                self.work_dir = work_dir
                self.toasts = []
                self.refresh_count = 0

            def toast(self, msg, kind="info"):
                self.toasts.append(msg)

            def _refresh_all(self):
                self.refresh_count += 1

        cls.Harness = _Harness
        cls.dir_a = _TMP / "drop_a"
        cls.dir_b = _TMP / "drop_b"
        cls.dir_a.mkdir(parents=True, exist_ok=True)
        cls.dir_b.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        _set_cfg(work_dir_locked=False)

    def test_drop_folder_switches_when_unlocked(self):
        mw = self.Harness(str(self.dir_a))
        _drop_paths(mw, [self.dir_b])
        self.assertEqual(os.path.normpath(mw.work_dir), os.path.normpath(str(self.dir_b)))
        self.assertEqual(mw.refresh_count, 1)
        self.assertTrue(any("已切换" in t for t in mw.toasts))

    def test_drop_folder_blocked_when_locked(self):
        _set_cfg(work_dir_locked=True)
        mw = self.Harness(str(self.dir_a))
        _drop_paths(mw, [self.dir_b])
        self.assertEqual(os.path.normpath(mw.work_dir), os.path.normpath(str(self.dir_a)))
        self.assertEqual(mw.refresh_count, 0)
        self.assertTrue(any("已锁定" in t for t in mw.toasts))
        self.assertFalse(any("已切换" in t for t in mw.toasts))

    def test_drop_file_blocked_when_locked(self):
        _set_cfg(work_dir_locked=True)
        f = self.dir_b / "note.txt"
        f.write_text("x", encoding="utf-8")
        mw = self.Harness(str(self.dir_a))
        _drop_paths(mw, [f])
        self.assertEqual(os.path.normpath(mw.work_dir), os.path.normpath(str(self.dir_a)))
        self.assertEqual(mw.refresh_count, 0)

    def test_unlock_restores_drop_switch(self):
        """锁定 → 拖入不变；解锁 → 拖入恢复切换（直到解锁）。"""
        _set_cfg(work_dir_locked=True)
        mw = self.Harness(str(self.dir_a))
        _drop_paths(mw, [self.dir_b])
        self.assertEqual(os.path.normpath(mw.work_dir), os.path.normpath(str(self.dir_a)))
        _set_cfg(work_dir_locked=False)
        _drop_paths(mw, [self.dir_b])
        self.assertEqual(os.path.normpath(mw.work_dir), os.path.normpath(str(self.dir_b)))
        self.assertEqual(mw.refresh_count, 1)

    def test_whitelist_page_unaffected_by_lock(self):
        """白名单页（如音乐标签）拖放走页面本地列表，锁定不影响其分发。"""
        _set_cfg(work_dir_locked=True)
        mw = self.Harness(str(self.dir_a))
        mw.current_page = "music_tag"
        received = []
        mw.pages["music_tag"] = _FakePage(received)
        _drop_paths(mw, [self.dir_b])
        self.assertEqual([os.path.normpath(p) for p in received],
                         [os.path.normpath(str(self.dir_b))])
        self.assertEqual(mw.work_dir, str(self.dir_a))


class _FakePage:
    def __init__(self, received):
        self.received = received

    def add_dropped_folder(self, folder):
        self.received.append(folder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
