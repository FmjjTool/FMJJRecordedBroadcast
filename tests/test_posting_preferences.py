"""投稿模板、北京时间默认日期、工作目录复选框与统一自动验证的隔离回归。"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from toolbox import config_manager as cm, bili_upload as BU
from toolbox.bili_upload import BiliUploader, UploadCancelled
from gui_qt import common
from gui_qt.bili_upload_dialog import BiliUploadDialog
from gui_qt.video_selection_dialog import VideoSelectionDialog
from gui_qt.pages.base import BasePage

REAL_TODAY = BU.today_compact


class PostingPreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-posting-preferences-")
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        self.patches = [patch.object(cm, "get_config_dir", return_value=self.config_dir),
                        patch.object(BU, "today_compact", return_value="20261116"),
                        patch.object(BU, "credential_identity", return_value="isolated-identity")]
        for item in self.patches:
            item.start()
        self.parent = BasePage(SimpleNamespace(work_dir=str(self.work)))
        self.dialogs = []
        BU.save_settings({"sources": ["preserve-source"], "auto_upload": True, "upload": {
            "title": "原有文案{日期}", "desc": "原有简介", "tid": 266, "copyright": 1}})
        self.posted, self.checks = [], []
        def check():
            self.checks.append(True)
            return {"mid": 1, "name": "测试账号"}
        def run(paths, config):
            self.posted.append((paths, config))
            return {"state": "published", "bvid": "BV-isolated"}
        self.actor = SimpleNamespace(auth_identity="isolated-identity", check_account=check,
            creator_info=lambda: {"typelist": [{"name": "音乐", "children": [{"name": "音乐粉丝饭拍", "id": 266}]}]},
            close=lambda: None, run=run)
        self.actor_patch = patch.object(BU, "BiliUploader", return_value=self.actor)
        self.actor_patch.start()

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
        self.wait(lambda: not common.TASKS.has_owner(self.parent))
        for dialog in self.dialogs:
            dialog.deleteLater()
        self.parent.deleteLater()
        self.qt.processEvents()
        self.actor_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while not condition():
            self.qt.processEvents()
            time.sleep(.005)
            self.assertLess(time.monotonic(), deadline)
        self.qt.processEvents()

    def dialog(self, **kwargs):
        result = BiliUploadDialog(self.parent, **kwargs)
        self.dialogs.append(result)
        return result

    def video(self, date="20261116", timecode="20-15-30", renamed=True):
        folder = self.work / date
        folder.mkdir(exist_ok=True)
        name = (f"{date[:4]}-{date[4:6]}-{date[6:]}_{timecode}_封茗囧菌_聊天_无弹幕_抖音.mp4"
                if renamed else date + "_尚未重命名.mp4")
        path = folder / name
        path.write_bytes(b"isolated-video")
        return str(path)

    def enter(self, dialog):
        dialog.show()
        self.wait(lambda: dialog._verified_identity and not common.TASKS.has_owner(self.parent))

    def test_named_templates_round_trip_without_date_files_or_credentials(self):
        dialog = self.dialog()
        self.assertEqual(dialog.title.text(), "原有文案{日期}")
        self.assertEqual(dialog.desc.toPlainText(), "原有简介")
        dialog.title.setText("封茗囧菌{日期}{平台}直播全程{分P说明}")
        dialog.desc.setPlainText("简介{平台}")
        self.assertTrue(dialog.save_template("抖子直播"))
        dialog.platform.setCurrentText("B站")
        dialog.tags.setText("封茗囧菌,B站直播")
        dialog.save_template("B站直播")
        dialog.templates.setCurrentText("抖子直播")
        self.assertEqual(dialog.platform.currentText(), "抖子")
        self.assertEqual(dialog.desc.toPlainText(), "简介{平台}")
        dialog.save_config()
        second = self.dialog()
        self.assertEqual(second.templates.currentText(), "抖子直播")
        self.assertEqual(second.date.text(), "20261116")
        self.assertEqual([second.platform.itemText(i) for i in range(second.platform.count())], ["抖子", "B站"])
        settings = BU.load_settings()
        self.assertEqual(settings["sources"], ["preserve-source"])
        self.assertTrue(settings["auto_upload"])
        self.assertEqual(set(settings["upload_templates"]), {"抖子直播", "B站直播"})
        self.assertEqual(settings["upload_templates"]["抖子直播"]["date"], "")
        self.assertNotIn("paths", settings["upload_templates"]["抖子直播"])
        self.assertNotIn("Cookie", str(settings))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            second.delete_template()
        self.assertEqual(set(BU.load_settings()["upload_templates"]), {"B站直播"})
        self.assertEqual(second.platform.currentText(), "B站")

    def test_enter_auto_verifies_and_checks_today_work_directory_videos(self):
        first, second = self.video(), self.video(timecode="21-00-00")
        old, pending = self.video("20261115"), self.video(renamed=False)
        (self.work / "not-video.txt").write_text("ignored")
        dialog = self.dialog(blocked_paths=[second])
        self.assertEqual(self.checks, [])
        self.enter(dialog)
        self.assertEqual(len(self.checks), 1)
        self.assertEqual(dialog.selected_paths(), [first])
        self.assertNotIn(old, [dialog.files.item(i).data(Qt.UserRole) for i in range(dialog.files.count())])
        self.assertEqual(dialog.files.count(), 3)
        self.assertIn("测试账号", dialog.account_label.text())
        self.assertEqual(dialog.tid.value(), 266)
        self.assertEqual(dialog.date.text(), "20261116")
        self.assertTrue(dialog.start_btn.isEnabled())
        for index in range(dialog.files.count()):
            item = dialog.files.item(index)
            if item.data(Qt.UserRole) in (pending, second):
                self.assertFalse(item.flags() & Qt.ItemIsUserCheckable)
        dialog.hide()
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(len(self.checks), 1)

    def test_checkbox_date_picker_preserves_deselection_and_all_dates(self):
        today, old = self.video(), self.video("20261115")
        dialog = self.dialog()
        self.enter(dialog)
        picker = VideoSelectionDialog(dialog, str(self.work), dialog._records, dialog.selected_paths(), "20261116")
        self.dialogs.append(picker)
        self.assertEqual(picker.selected_paths(), [today])
        picker.all_dates.setChecked(True)
        self.assertEqual(picker.files.count(), 2)
        self.assertEqual(picker.selected_paths(), [today])
        picker.all_dates.setChecked(False)
        picker.date.setDate(QDate(2026, 11, 15))
        self.assertEqual(picker.selected_paths(), [old])
        picker.set_checked(False)
        picker.date.setDate(QDate(2026, 11, 16))
        self.assertEqual(picker.selected_paths(), [today])
        picker.date.setDate(QDate(2026, 11, 15))
        self.assertEqual(picker.selected_paths(), [])

    def test_upload_uses_only_checked_videos_and_platform_template(self):
        first, second = self.video(), self.video(timecode="21-00-00")
        dialog = self.dialog()
        self.enter(dialog)
        for index in range(dialog.files.count()):
            if dialog.files.item(index).data(Qt.UserRole) == second:
                dialog.files.item(index).setCheckState(Qt.Unchecked)
        dialog.title.setText("封茗囧菌{日期}{平台}直播全程")
        dialog.platform.setCurrentText("B站")
        self.assertNotIn(" P", dialog.preview.toPlainText())
        self.assertIn("视频：1 个", dialog.preview.toPlainText())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            dialog.start_upload()
            self.wait(lambda: not common.TASKS.has_owner(self.parent))
        self.assertEqual(self.posted[0][0], [first])
        self.assertEqual(self.posted[0][1]["platform"], "B站")
        metadata = BU.build_metadata([first], self.posted[0][1])
        self.assertIn("20261116B站", metadata["title"])

    def test_multipart_only_today_same_recording_group_and_actual_selection(self):
        first, second = self.video(), self.video(timecode="21-00-00")
        dialog = self.dialog()
        self.enter(dialog)
        self.assertFalse(dialog.multipart_hint.isHidden())
        self.assertIn("2 P", dialog.preview.toPlainText())
        self.assertIn("分P", dialog.windowTitle())
        dialog.files.item(1).setCheckState(Qt.Unchecked)
        self.assertTrue(dialog.multipart_hint.isHidden())
        self.assertNotIn("分P", dialog.windowTitle())
        self.assertNotIn(" P", dialog.preview.toPlainText())
        config = {"tid": 266, "copyright": 1}
        another = Path(self.video(timecode="22-00-00"))
        other_anchor = another.with_name(another.name.replace("封茗囧菌", "另一主播"))
        another.rename(other_anchor)
        for paths in ([first, first], [first, self.video("20261115")],
                      [self.video("20261115"), self.video("20261115", "21-00-00")],
                      [first, str(other_anchor)]):
            self.assertEqual(BU.multipart_count(paths), 0)
            with self.assertRaises(ValueError):
                BU.build_metadata(paths, config)
        other = Path(self.video(timecode="23-00-00"))
        new = other.with_name(other.name.replace("抖音", "B站"))
        other.rename(new)
        self.assertEqual(BU.multipart_count([first, str(new)]), 0)
        new2 = new.with_name(new.name.replace("B站", "抖音").replace("无弹幕", "有弹幕"))
        new.rename(new2)
        self.assertEqual(BU.multipart_count([first, str(new2)]), 0)
        self.assertEqual(BU.multipart_count([first, second], "20261115"), 0)
        self.assertEqual(BU.multipart_count([first, second], "20261116"), 2)

    def test_disabled_or_cleared_login_never_creates_network_session(self):
        with patch("auth.get_cookie", return_value=""), patch("toolbox.bili_upload.requests.Session") as sessions:
            with self.assertRaises(BU.UploadError):
                BiliUploader()
            sessions.assert_not_called()
        with patch.object(BU, "BiliUploader", side_effect=BU.UploadError("Cookie 已关闭")):
            dialog = self.dialog()
            dialog.show()
            self.wait(lambda: "Cookie 已关闭" in dialog.account_label.text() and not common.TASKS.has_owner(self.parent))
        self.assertFalse(dialog.start_btn.isEnabled())
        self.assertIn("Cookie 管理", dialog.account_label.text())

    def test_account_change_discards_old_verification_and_rechecks(self):
        state = {"identity": "old"}
        entered, release = threading.Event(), threading.Event()
        def actor(**kwargs):
            identity = state["identity"]
            def creator():
                if identity == "old":
                    entered.set()
                    release.wait(5)
                return {}
            return SimpleNamespace(auth_identity=identity, check_account=lambda: {"mid": 1, "name": identity},
                                   creator_info=creator, close=lambda: None)
        dialog = self.dialog()
        with patch.object(BU, "credential_identity", side_effect=lambda: state["identity"]), \
             patch.object(BU, "BiliUploader", side_effect=actor):
            dialog.show()
            try:
                self.wait(entered.is_set)
                state["identity"] = "new"
            finally:
                release.set()
            self.wait(lambda: dialog._verified_identity == "new" and not common.TASKS.has_owner(self.parent))
        self.assertIn("new", dialog.account_label.text())
        self.assertNotIn("old", dialog.account_label.text())

    def test_close_during_validation_cancels_without_late_ui_write(self):
        entered, release = threading.Event(), threading.Event()
        def check():
            entered.set()
            release.wait(5)
            return {"mid": 1, "name": "late-account"}
        self.actor.check_account = check
        dialog = self.dialog()
        dialog.show()
        try:
            self.wait(entered.is_set)
            dialog.reject()
            self.assertTrue(dialog.account_cancel.is_set())
        finally:
            release.set()
        self.wait(lambda: not common.TASKS.has_owner(self.parent))
        self.assertNotIn("late-account", dialog.account_label.text())

    def test_managed_uploader_stops_before_requests_when_login_changes(self):
        cookie = {"text": "SESSDATA=isolated; bili_jct=isolated-csrf"}
        with patch("auth.get_cookie", side_effect=lambda platform: cookie["text"]), \
             patch.object(BU, "credential_identity", side_effect=lambda: BU.cookie_identity(cookie["text"])), \
             patch("toolbox.bili_upload.requests.Session") as sessions:
            actor = BiliUploader(history=SimpleNamespace())
            for changed in ("", "SESSDATA=different; bili_jct=different"):
                cookie["text"] = changed
                with self.assertRaises(UploadCancelled):
                    actor._json(actor.member, "GET", "https://api.bilibili.com/test")
                actor.member.request.assert_not_called()
            actor.close()

    def test_platform_placeholder_maps_legacy_douyin_to_douzi(self):
        video = self.video()
        config = {"title": "{平台}{日期}", "desc": "{平台}", "platform": "抖音", "tid": 266, "copyright": 1}
        self.assertEqual(BU.build_metadata([video], config)["title"], "抖子20261116")
        config["platform"] = "B站"
        self.assertEqual(BU.build_metadata([video], config)["desc"], "B站")

    def test_work_directory_change_discards_old_candidates(self):
        first = self.video()
        dialog = self.dialog(paths=[first])
        self.enter(dialog)
        other = self.root / "another-work"
        other.mkdir()
        second = other / "2026-11-16_21-00-00_封茗囧菌_无弹幕_B站.mp4"
        second.write_bytes(b"isolated-video")
        self.parent.app.work_dir = str(other)
        dialog.load_workspace()
        self.wait(lambda: not common.TASKS.has_owner(self.parent))
        self.assertEqual(dialog.selected_paths(), [str(second)])
        self.assertNotIn(first, [record["path"] for record in dialog._records])

    def test_beijing_today_uses_explicit_eight_hour_timezone(self):
        from datetime import datetime
        with patch("datetime.datetime") as fake:
            fake.now.return_value = datetime(2026, 11, 17, 0, 30)
            # 绕过测试夹具对日期函数的替身，调用真实实现。
            self.assertEqual(REAL_TODAY(), "20261117")
            self.assertEqual(fake.now.call_args.args[0].utcoffset(None).total_seconds(), 8 * 3600)


if __name__ == "__main__":
    unittest.main()
