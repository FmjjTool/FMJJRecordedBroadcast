"""资产展示清单的持久化、目录更新和数据库优先读取回归。"""

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui_qt import common, main_window
from gui_qt.pages.asset import AssetPage
from toolbox import asset_index, config_manager as cm


def entry(title, platform="weibo"):
    return {"title": title, "platform": platform, "description": "",
            "author": "", "video_id": title, "date_part": "20261116",
            "publish_time": "", "media_files": [], "file_path": "",
            "cover_url": "", "play_count": 0, "like_count": 0, "comment_count": 0,
            "expected_imgs": 0, "raw_data": {"large": "omitted"}}


class AssetIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-asset-index-")
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(asset_index, "_db_path", self.root / "index.db")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_round_trip_empty_and_independent_sources(self):
        source = self.root / "absent-source"
        self.assertIsNone(asset_index.fetch_view(source))
        self.assertTrue(asset_index.save_view(source, [entry("one"), entry("two")]))
        with patch("os.scandir", side_effect=AssertionError("should not scan")):
            cached = asset_index.fetch_view(source)
        self.assertEqual([it["title"] for it in cached["items"]], ["one", "two"])
        self.assertNotIn("raw_data", cached["items"][0])
        self.assertIsNone(asset_index.fetch_view(self.root / "other"))
        self.assertIsNone(asset_index.fetch_view(source, "files"))
        self.assertTrue(asset_index.save_view(source, []))
        self.assertEqual(asset_index.fetch_view(source)["items"], [])
        self.assertTrue(asset_index.clear())
        self.assertIsNone(asset_index.fetch_view(source))

    def test_failed_transaction_preserves_previous_complete_view(self):
        self.assertTrue(asset_index.save_view(self.root, [entry("old")]))
        with closing(sqlite3.connect(asset_index.db_path())) as conn, conn:
            conn.execute("CREATE TRIGGER fail_view BEFORE INSERT ON asset_view_row "
                         "WHEN NEW.ordinal=1 BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertFalse(asset_index.save_view(self.root, [entry("new"), entry("second")]))
        self.assertEqual(asset_index.fetch_view(self.root)["items"][0]["title"], "old")

    def test_corruption_and_version_change_are_cache_misses(self):
        for update in ("UPDATE asset_view_row SET payload='broken'",
                       "UPDATE asset_view_state SET count=99",
                       "UPDATE asset_view_state SET version=-1"):
            self.assertTrue(asset_index.save_view(self.root, [entry("one")]))
            with closing(sqlite3.connect(asset_index.db_path())) as conn, conn:
                conn.execute(update)
            self.assertIsNone(asset_index.fetch_view(self.root))

    def test_nested_media_updates_and_other_root_cache_survive(self):
        first = self.root / "first"
        leaf = first / "platform" / "author"
        leaf.mkdir(parents=True)
        image = leaf / "one.jpg"
        image.write_bytes(b"one")
        second = self.root / "second"
        second.mkdir()
        video = second / "video.mp4"
        video.write_bytes(b"two")
        self.assertEqual(AssetPage._iter_media_paths(first), [str(image)])
        self.assertEqual(AssetPage._iter_media_paths(second), [str(video)])
        parent_times = [(p, p.stat().st_mtime) for p in (first, leaf.parent)]
        old_leaf_time = leaf.stat().st_mtime
        added = leaf / "two.jpg"
        added.write_bytes(b"new")
        os.utime(leaf, (old_leaf_time + 10, old_leaf_time + 10))
        for p, stamp in parent_times:
            os.utime(p, (stamp, stamp))
        self.assertEqual(set(AssetPage._iter_media_paths(first)), {str(image), str(added)})
        self.assertIn(os.path.normcase(str(second)), asset_index.fetch_media_dirs())
        image.unlink()
        os.utime(leaf, (old_leaf_time + 20, old_leaf_time + 20))
        self.assertEqual(AssetPage._iter_media_paths(first), [str(added)])


class AssetPageCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-asset-page-")
        self.root = Path(self.temp.name)
        self.export = self.root / "download"
        self.export.mkdir()
        (self.root / "config").mkdir()
        self.patches = [
            patch.object(cm, "get_app_root", return_value=self.root),
            patch.object(cm, "get_config_dir", return_value=self.root / "config"),
            patch.object(cm, "get_export_dir", side_effect=lambda: self.export),
            patch.object(asset_index, "_db_path", self.root / "config" / "asset_index.db"),
            patch.object(main_window.MainWindow, "_refresh_cookie_status"),
            patch.object(common, "load_collected"),
            patch.object(AssetPage, "_backfill_bili_covers"),
        ]
        for p in self.patches:
            p.start()
        cm.save_convert_config({"work_dir": str(self.root), "tcdocs": [], "tcdoc_legacy_migrated": True})
        self.window = main_window.MainWindow()
        self.window.show()
        self.qt.processEvents()
        self.window.page_preloader.stop()
        self.page = self.window._ensure_page("asset")
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.wait(lambda: not self.page._collected_loading if hasattr(self.page, "_collected_loading") else True)
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def wait(self, check):
        deadline = time.monotonic() + 5
        while not check() and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(.001)
        self.assertTrue(check(), "timed out waiting for asset result")

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def test_cached_list_displays_before_scan_and_filters_use_memory(self):
        kind = f"entries:{asset_index.VER}"
        asset_index.save_view(self.export, [entry("old"), entry("keep")], kind)
        gate = self.gate()
        scanning = threading.Event()
        def load(root):
            scanning.set()
            gate.wait(4)
            return [entry("keep new"), entry("other")]
        with patch.object(self.page, "_load_disk_items", side_effect=load) as scan:
            self.page._preload_collected()
            self.wait(lambda: scanning.is_set() and self.page._disk_items)
            self.assertEqual([it["title"] for it in self.page._disk_items], ["old", "keep"])
            with patch("os.scandir", side_effect=AssertionError("filter must not scan")):
                self.page.search_entry.setText("keep")
                self.page._apply_search()
                self.page.sort_sel.setCurrentIndex(1)
            self.assertEqual([it["title"] for it in self.page._collected_all_items], ["keep"])
            self.assertEqual(scan.call_count, 1)
            gate.set()
            self.wait(lambda: not self.page._collected_loading)
            self.assertEqual([it["title"] for it in self.page._collected_all_items], ["keep new"])
            self.assertEqual(len(asset_index.fetch_view(self.export, kind)["items"]), 2)

    def test_failed_revalidation_keeps_cached_list_and_database(self):
        kind = f"entries:{asset_index.VER}"
        asset_index.save_view(self.export, [entry("old")], kind)
        with patch.object(self.page, "_load_disk_items", side_effect=PermissionError("test")):
            self.page._preload_collected()
            self.wait(lambda: hasattr(self.page, "_collected_refreshed_at") and not self.page._collected_loading)
        self.assertEqual(self.page._disk_items[0]["title"], "old")
        self.assertEqual(asset_index.fetch_view(self.export, kind)["items"][0]["title"], "old")

    def test_directory_cache_and_switch_during_background_read(self):
        folder = self.export / "字幕文件"
        folder.mkdir()
        groups = [{"title": "old subtitle", "files": [], "dirs": [], "n_img": 0,
                   "n_vid": 0, "n_aud": 0, "n_doc": 1, "size": 2,
                   "mtime": "2026-11-16", "cover": "", "root_dir": str(folder), "date": "20261116"}]
        kind = "files:字幕文件:20000"
        asset_index.save_view(folder, groups, kind, {"truncated": True})
        self.page._rebuild_folder_options([])
        gate = self.gate()
        scanning = threading.Event()
        def load(root, cat=""):
            scanning.set()
            gate.wait(4)
            self.page._dir_group_truncated = False
            return []
        with patch.object(self.page, "_load_dir_groups", side_effect=load), \
             patch.object(self.page, "_load_disk_items", return_value=[entry("new root")]):
            self.page.folder_sel.setCurrentIndex(self.page.folder_sel.findData("dir:字幕文件"))
            self.wait(lambda: scanning.is_set() and self.page._plain_groups)
            self.assertTrue(self.page._dir_group_truncated)
            self.page.folder_sel.setCurrentIndex(0)
            gate.set()
            self.wait(lambda: not self.page._collected_loading)
        self.assertEqual(self.page._view_mode, "entries")
        self.assertEqual(self.page._disk_items[0]["title"], "new root")

    def test_manual_refreshes_coalesce_and_work_directory_isolated(self):
        gate = self.gate()
        calls = []
        def load(root):
            calls.append(root)
            if len(calls) == 1:
                gate.wait(4)
            return [entry(Path(root).name)]
        with patch.object(self.page, "_load_disk_items", side_effect=load):
            self.page._refresh_collected()
            self.wait(lambda: len(calls) == 1)
            for _ in range(8):
                self.page._refresh_collected()
            self.export = self.root / "other-download"
            self.export.mkdir()
            self.page._refresh_collected()
            gate.set()
            self.wait(lambda: not self.page._collected_loading)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.page._disk_items[0]["title"], "other-download")
        kind = f"entries:{asset_index.VER}"
        self.assertEqual(asset_index.fetch_view(self.export, kind)["items"][0]["title"], "other-download")
        self.assertEqual(asset_index.fetch_view(self.root / "download", kind)["items"][0]["title"], "download")

    def test_missing_index_builds_and_deleted_files_are_removed(self):
        data_dir = self.export / "已获取数据" / "微博_文件"
        data_dir.mkdir(parents=True)
        source = data_dir / "20261116_测试_weibo.json"
        source.write_text(json.dumps({"标题": "测试资产", "视频ID": "1"}, ensure_ascii=False), encoding="utf-8")
        self.page._preload_collected()
        self.wait(lambda: hasattr(self.page, "_collected_refreshed_at") and not self.page._collected_loading)
        self.assertEqual(len(self.page._disk_items), 1)
        source.unlink()
        self.page._refresh_collected()
        self.wait(lambda: not self.page._collected_loading)
        self.assertEqual(self.page._disk_items, [])
        self.assertEqual(asset_index.fetch_view(self.export, f"entries:{asset_index.VER}")["items"], [])


if __name__ == "__main__":
    unittest.main()
