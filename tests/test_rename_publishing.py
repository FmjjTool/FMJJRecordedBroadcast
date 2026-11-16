"""恢复交互重命名及改名后投稿的隔离回归，不调用真实上传接口。"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from toolbox import config_manager as cm, bili_upload as BU
from toolbox.bili_upload import BiliUploader
from gui_qt import common
from gui_qt.pages.rename import RenamePage
from gui_qt.bili_upload_dialog import BiliUploadDialog


class RenamePublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-rename-test-")
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.folder = self.work / "20261002"
        self.folder.mkdir(parents=True)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        self.patches = [
            patch.object(cm, "get_config_dir", return_value=self.config_dir),
            patch.object(cm, "get_app_root", return_value=self.root),
            patch.object(cm, "_rename_config", None),
            patch("gui_qt.pages.rename.get_video_info", return_value={"duration_str": "1秒"}),
            patch("gui_qt.pages.rename.capture_video_frame", return_value=None),
        ]
        for item in self.patches:
            item.start()
        cfg = cm.get_rename_config()
        cfg.account_list = ["封茗囧菌", "测试账户"]
        cfg.live_types = ["聊天", "游戏"]
        cfg.platform_list = ["抖音", "B站"]
        cfg.default_platform = "B站"
        cfg.append_extra_info = False
        cm.save_rename_config(cfg)
        BU.save_settings({"auto_upload": True, "upload": {"tid": 138, "copyright": 1}})
        self.posted = []
        def run(paths, config):
            self.posted.append(list(paths))
            self.assertTrue(all(Path(path).is_file() for path in paths))
            return {"state": "published", "bvid": "BV-isolated"}
        self.fake_uploader = patch.object(BU, "BiliUploader", return_value=SimpleNamespace(
            run=run, close=lambda: None))
        self.fake_uploader.start()
        self.host = SimpleNamespace(work_dir=str(self.work), toast=lambda *args: None)
        self.page = RenamePage(self.host)

    def tearDown(self):
        self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        self.page.shutdown()
        self.page.close()
        self.page.deleteLater()
        self.qt.processEvents()
        self.fake_uploader.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition():
            self.qt.processEvents()
            time.sleep(.005)
            self.assertLess(time.monotonic(), deadline, self.page.log_text.toPlainText())
        self.qt.processEvents()

    def video(self, timecode="201530", standard=False):
        name = (f"2026-10-02_{timecode[:2]}-{timecode[2:4]}-{timecode[4:]}_封茗囧菌_聊天_无弹幕_抖音.mp4"
                if standard else f"录制-1-20261002-{timecode}_直播标题.mp4")
        path = self.folder / name
        path.write_bytes(b"isolated-video")
        return path

    def open_folder(self):
        self.page.show()
        self.page.on_show()
        self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        self.page._on_type_click("聊天")

    def test_restored_original_controls_parameters_and_navigation(self):
        from gui_qt.main_window import PAGE_CLASSES, NAV_GROUPS
        self.video()
        self.open_folder()
        self.assertEqual(self.page.sub_tabs.count(), 2)
        self.assertEqual(self.page.account_list.count(), 2)
        self.assertEqual(self.page._current_platform(), "B站")
        QTest.keyClick(self.page, Qt.Key_2)
        self.page.ver_danmu_radio.setChecked(True)
        self.page._set_title_mode("manual")
        self.page.title_entry.setText("测试标题")
        name = self.page._build_filename()
        self.assertIn("_聊天+游戏_有弹幕_B站_测试标题.mp4", name)
        self.page._set_title_mode("auto")
        self.assertIn(self.page.auto_title, self.page._build_filename())
        self.page._set_title_mode("none")
        self.assertNotIn("测试标题", self.page._build_filename())
        self.page._do_add("account", "新增账户")
        self.page._move_item("account", 2, -1)
        self.assertEqual(cm.load_rename_config().account_list[1], "新增账户")
        self.page._rebuild_platform_radios()
        self.assertEqual(len(self.page.platform_group.buttons()), 2)
        self.assertEqual(PAGE_CLASSES["toolbox_rename"][1], "RenamePage")
        self.assertTrue(any(key == "toolbox_rename" for _, items in NAV_GROUPS for key, _ in items))
        self.assertIn("rename_batch", PAGE_CLASSES)  # 保留通用批量重命名

    def test_posts_final_paths_only_after_last_metadata_finishes(self):
        first, second = self.video(), self.video("202530")
        self.open_folder()
        self.page.rename_extra_check.setChecked(True)
        entered, release = threading.Event(), threading.Event()
        count = []
        def metadata(*args):
            count.append(args[0])
            if len(count) == 2:
                entered.set()
                release.wait(5)
            return True, "metadata-complete"
        with patch.object(self.page, "_write_copyright_metadata", side_effect=metadata):
            self.page.on_return_press()
            self.wait_until(lambda: not self.page._busy)
            self.assertEqual(self.posted, [])
            self.page.on_return_press()
            try:
                self.wait_until(entered.is_set)
                self.assertEqual(self.posted, [])
                self.assertTrue(self.page._busy)
            finally:
                release.set()
            self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0], self.page.current_videos)
        self.assertEqual(len(self.posted[0]), 2)
        self.page._on_folder_click()
        self.page.on_return_press()
        self.page.on_return_press()
        self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        self.assertEqual(len(self.posted), 1)

    def test_skipping_unrenamed_video_blocks_automatic_post(self):
        original = self.video()
        self.open_folder()
        self.page.on_escape_press()
        self.assertTrue(original.exists())
        self.assertEqual(self.posted, [])
        self.assertIn("先完成交互重命名", self.page.log_text.toPlainText())

    def test_conflict_preserves_both_files_and_prevents_post(self):
        original = self.video()
        self.open_folder()
        target = self.folder / self.page._build_filename()
        target.write_bytes(b"keep-destination")
        self.page.on_return_press()
        self.assertTrue(original.exists())
        self.assertEqual(target.read_bytes(), b"keep-destination")
        self.assertEqual(self.page.current_video_idx, 0)
        self.assertEqual(self.posted, [])

    def test_metadata_failure_preserves_renamed_video_and_enter_retries(self):
        original = self.video()
        self.open_folder()
        self.page.rename_extra_check.setChecked(True)
        with patch.object(self.page, "_write_copyright_metadata", return_value=(False, "模拟失败")):
            self.page.on_return_press()
            self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        renamed = Path(self.page.current_videos[0])
        self.assertFalse(original.exists())
        self.assertEqual(renamed.read_bytes(), b"isolated-video")
        self.assertEqual(self.posted, [])
        self.page.on_escape_press()
        self.assertEqual(self.posted, [])
        self.page.current_video_idx = 0
        self.page._load_current()
        self.page.rename_extra_check.setChecked(False)
        self.page.on_return_press()
        self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        self.assertEqual(self.page._metadata_failed, set())
        self.assertEqual(len(self.posted), 1)

    def test_new_video_in_folder_requires_remaining_rename(self):
        self.video(standard=True)
        self.open_folder()
        self.video("202530")
        self.page.on_return_press()
        self.assertEqual(self.posted, [])
        self.assertIn("文件夹的视频已变化", self.page.log_text.toPlainText())

    def test_metadata_replacement_syncs_and_failure_keeps_original(self):
        video = self.video(standard=True)
        before = video.read_bytes()
        stamp = video.stat().st_mtime_ns
        with patch("gui_qt.pages.rename.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            ok, _ = self.page._write_copyright_metadata(video, True, "测试版权")
        self.assertFalse(ok)
        self.assertEqual(video.read_bytes(), before)
        self.assertFalse(list(self.folder.glob(".fmjj-meta-*")))
        def write_output(command, **kwargs):
            Path(command[-1]).write_bytes(b"simulated-remux-with-copyright")
            return SimpleNamespace(returncode=0)
        with patch("gui_qt.pages.rename.subprocess.run", side_effect=write_output):
            ok, message = self.page._write_copyright_metadata(video, True, "测试版权")
        self.assertTrue(ok, message)  # Windows 实际 fsync 和 replace，不模拟文件句柄。
        self.assertEqual(video.read_bytes(), b"simulated-remux-with-copyright")
        self.assertEqual(video.stat().st_mtime_ns, stamp)
        self.assertFalse(list(self.folder.glob(".fmjj-meta-*")))

    def test_work_directory_change_during_rename_prevents_post(self):
        self.video()
        self.open_folder()
        entered, release = threading.Event(), threading.Event()
        def metadata(*args):
            entered.set()
            release.wait(5)
            return True, ""
        with patch.object(self.page, "_write_copyright_metadata", side_effect=metadata):
            self.page.on_return_press()
            try:
                self.wait_until(entered.is_set)
                new_work = self.root / "other-work"
                new_work.mkdir()
                self.host.work_dir = str(new_work)
                self.page.on_dir_changed(str(new_work))
            finally:
                release.set()
            self.wait_until(lambda: not common.TASKS.has_owner(self.page))
        self.assertEqual(self.posted, [])
        self.assertEqual(self.page.current_videos, [])
        self.assertEqual(self.page._loaded_root, str(new_work))

    def test_upload_engine_and_dialog_reject_unrenamed_and_invalid_dates(self):
        original = self.video()
        with self.assertRaises(ValueError):
            BU.validate_renamed_videos([original])
        with self.assertRaises(ValueError):
            BU.validate_renamed_videos(["2026-02-30_20-15-30_直播.mp4"])
        with patch.object(BiliUploader, "check_account") as check:
            # 调用真实引擎函数，命名校验在任何账号/网络请求之前。
            with self.assertRaises(ValueError):
                BiliUploader.run(SimpleNamespace(), [original], {})
            check.assert_not_called()
        dialog = BiliUploadDialog(self.page, [str(original)])
        dialog.start_upload()
        self.assertFalse(dialog.busy)
        self.assertEqual(dialog.selected_paths(), [])
        self.assertFalse(dialog.files.item(0).flags() & Qt.ItemIsUserCheckable)
        dialog.close()
        renamed = self.video("202530", standard=True)
        with patch.object(BU, "today_compact", return_value="20261002"):
            dialog = BiliUploadDialog(self.page, [str(renamed)])
        dialog._verified_identity = "isolated"
        with patch("gui_qt.bili_upload_dialog.QMessageBox.question",
                   return_value=QMessageBox.No), patch.object(BU, "credential_identity", return_value="isolated"):
            dialog.start_upload()
        self.assertNotIn("先完成交互重命名", dialog.status.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
