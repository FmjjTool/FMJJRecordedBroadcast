"""录制状态、异常恢复、视频迁移和 UPOS 投稿的隔离回归，不访问网络。"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from toolbox import config_manager as cm, live_record as LR
from toolbox.bili_upload import BiliUploader, UploadError, UploadHistory, build_metadata, save_settings
from toolbox.durable_io import JsonlJournal, atomic_write_json, read_jsonl_tail
from toolbox.organize_engine import scan_sources, scan_files, execute_migration
from toolbox.danmaku_recorder import DanmakuRecorder
from gui_qt.pages.test_page import TestPage
from gui_qt.pages.organize import OrganizePage


class WorkflowFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-workflow-unit-")
        self.root = Path(self.temp.name)
        self.source = self.root / "A"
        self.target = self.root / "work"
        self.source.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def video(self, name="封茗囧菌20261002.mp4", data=b"video", folder=None):
        path = (folder or self.source) / name
        path.write_bytes(data)
        os.utime(path, (time.time() - 60, time.time() - 60))
        return path


class WorkflowTests(WorkflowFixture):
    def test_jsonl_partial_tail_repair_and_atomic_failure(self):
        path = self.root / "journal.jsonl"
        journal = JsonlJournal(path)
        journal.write({"text": "中文"})
        journal.close()
        with open(path, "ab") as stream:
            stream.write(b'{"partial":')
        journal = JsonlJournal(path)
        journal.write({"text": "继续"})
        journal.close()
        self.assertEqual([item["text"] for item in read_jsonl_tail(path)], ["中文", "继续"])
        config = self.root / "config.json"
        atomic_write_json(config, {"old": True})
        with patch("toolbox.durable_io.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                atomic_write_json(config, {"new": True})
        self.assertEqual(json.loads(config.read_text()), {"old": True})
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_video_only_multiple_sources_and_work_tree_exclusion(self):
        second = self.root / "B"
        second.mkdir()
        self.video()
        self.video("封茗囧菌20261002.MKV", folder=second)
        self.video("20261002.txt")
        self.video("20261002.ogg")
        self.video(folder=self.target)
        plan, _ = scan_sources([self.source, second, self.source, self.target], self.target, True)
        self.assertEqual(len(plan), 2)
        groups, _ = scan_files(self.source)
        self.assertEqual(sum(map(len, groups.values())), 1)

    def test_migration_preserves_bytes_time_and_existing_destination(self):
        path = self.video(data=b"a" * 65537)
        stamp = path.stat().st_mtime_ns
        destination = self.target / "20261002"
        destination.mkdir()
        existing = destination / path.name
        existing.write_bytes(b"keep")
        plan, _ = scan_sources([self.source], self.target)
        results = execute_migration(self.target, plan)
        self.assertTrue(results[0]["ok"], results)
        copied = Path(results[0]["target"])
        self.assertEqual(copied.read_bytes(), b"a" * 65537)
        self.assertEqual(copied.stat().st_mtime_ns, stamp)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertFalse(path.exists())
        self.assertFalse(list(destination.glob("*.part")))

    def test_changed_file_and_copy_failure_keep_source(self):
        path = self.video()
        plan, _ = scan_sources([self.source], self.target)
        path.write_bytes(b"changed")
        self.assertFalse(execute_migration(self.target, plan)[0]["ok"])
        self.assertTrue(path.exists())
        os.utime(path, (time.time() - 60, time.time() - 60))
        plan, _ = scan_sources([self.source], self.target)
        with patch("toolbox.organize_engine.hashlib.file_digest", side_effect=OSError("disk failure")):
            self.assertFalse(execute_migration(self.target, plan)[0]["ok"])
        self.assertEqual(path.read_bytes(), b"changed")
        self.assertFalse(list(self.target.rglob("*.part")))

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_active_recording_handle_blocks_migration(self):
        path = self.video()
        plan, _ = scan_sources([self.source], self.target)
        with open(path, "ab"):
            self.assertFalse(execute_migration(self.target, plan)[0]["ok"])
        self.assertTrue(path.exists())

    def test_current_writing_and_bad_target_excluded(self):
        path = self.video()
        os.utime(path, None)
        plan, skipped = scan_sources([self.source], self.target)
        self.assertFalse(plan)
        self.assertTrue(skipped)
        os.utime(path, (time.time() - 60, time.time() - 60))
        plan, _ = scan_sources([self.source], self.target)
        plan[0]["date"] = "../../escape"
        self.assertFalse(execute_migration(self.target, plan)[0]["ok"])
        self.assertTrue(path.exists())

    def test_bili_xml_is_readable_before_stop_and_journal_saves_gifts(self):
        import xml.etree.ElementTree as ET
        with patch("toolbox.bili_live.BiliLiveFetcher"):
            messages = []
            recorder = DanmakuRecorder("bili", "544820", self.temp.name, "record",
                                       on_message=lambda *args: messages.append(args))
            self.assertTrue(recorder.start())
            try:
                recorder._on_msg("弹幕", "测试", {"content": "<&中文", "type": "DANMU_MSG"})
                recorder._on_msg("礼物", "礼物", {"giftName": "花", "giftCount": 2, "type": "SEND_GIFT"})
                self.assertEqual(ET.parse(self.root / "record.xml").getroot()[0].text, "<&中文")
                self.assertEqual(len(ET.parse(self.root / "record.xml").getroot()), 1)
                self.assertEqual(len(read_jsonl_tail(self.root / "record_弹幕.jsonl")), 2)
                self.assertEqual(len(messages), 2)
            finally:
                recorder.stop()

    def test_record_start_stop_callbacks_and_intent_survive_reload(self):
        events, starts = [], []
        manager = LR.RecordManager("bili", {"save_cover": False, "render_danmaku": True, "save_danmaku": False},
            on_record_state=lambda anchor, active: events.append((anchor.key, active)))
        anchor = LR.Anchor(platform="bili", room="544820", monitoring=True)
        anchor._want_record = True
        manager.anchors = [anchor]
        task = SimpleNamespace(start=lambda: True, stop=lambda: None, elapsed=lambda: 10)
        with patch.object(manager, "out_dir", return_value=str(self.target)), \
             patch("toolbox.live_record.StreamDownloader", return_value=task), \
             patch.object(manager, "_start_danmaku", side_effect=lambda *args: starts.append(args)), \
             patch.object(manager, "record_event"):
            self.assertTrue(manager.start_record(anchor, {"stream": "https://example.com/stream"})[0])
            self.assertEqual(len(starts), 1)
            manager.stop_record(anchor)
        self.assertEqual(events, [(anchor.key, True), (anchor.key, False)])
        restored = LR.RecordManager("bili")
        restored.load_dict(manager.to_dict())
        self.assertTrue(restored.anchors[0]._want_record)
        self.assertFalse(restored.anchors[0].recording)

    def test_live_cycle_only_checks_end_and_keeps_recording_on_network_error(self):
        manager = LR.RecordManager("bili")
        anchor = LR.Anchor(platform="bili", room="544820", live=True, recording=True, monitoring=True)
        manager.anchors = [anchor]
        with patch("toolbox.live_record.fetch_status", return_value={"error": "timeout"}) as status, \
             patch("toolbox.live_record.fetch_info") as info, patch.object(manager, "poll_finished"):
            manager._cycle()
            self.assertEqual(status.call_count, 1)
            info.assert_not_called()
            self.assertTrue(anchor.recording)
        with patch("toolbox.douyin_live.fetch_room_info", return_value={"status": 2}) as room:
            self.assertTrue(LR.fetch_status("douyin", "123456")["live"])
            room.assert_called_once_with("123456", login=False)

    def test_picture_title_multi_p_and_validation(self):
        first = self.video("2026-10-02_20-15-30_封茗囧菌_聊天_无弹幕_抖音.mp4")
        second = self.video("2026-10-02_21-00-00_封茗囧菌_游戏_无弹幕_抖音.mp4")
        settings = {"tid": 138, "source": "https://live.douyin.com/123456"}
        self.assertEqual(build_metadata([first], settings)["title"], "封茗囧菌20261002直播全程无弹幕～")
        with patch("toolbox.bili_upload.today_compact", return_value="20261002"):
            self.assertIn("（分两 p）", build_metadata([first, second], settings)["title"])
        with self.assertRaises(ValueError):
            build_metadata([first], {"tid": 138})
        with self.assertRaises(ValueError):
            build_metadata([first], {"source": "直播"})
        with self.assertRaises(ValueError):
            build_metadata([first], {**settings, "title": "{unknown}"})


class UploadPipelineTests(WorkflowFixture):
    def test_today_recordings_submit_as_two_parts_of_one_video(self):
        first = self.video("2026-11-16_20-15-30_封茗囧菌_聊天_无弹幕_抖音.mp4")
        second = self.video("2026-11-16_21-00-00_封茗囧菌_游戏_无弹幕_抖音.mp4")
        uploader = BiliUploader(cookie="SESSDATA=fictional; bili_jct=fictional-csrf",
                                history=UploadHistory(self.root / "parts.db"))
        payloads = []
        def submit(*args, **kwargs):
            payloads.append(kwargs["json"])
            return {"code": 0, "data": {"bvid": "BV-fictional", "aid": 1}}
        with patch("toolbox.bili_upload.today_compact", return_value="20261116"), \
                patch.object(uploader, "check_account", return_value={"mid": 1, "name": "测试"}), \
                patch.object(uploader, "_upload_cover", return_value="fictional-cover"), \
                patch.object(uploader, "_upload_video", side_effect=[{"filename": "part-one"}, {"filename": "part-two"}]), \
                patch.object(uploader, "_json", side_effect=submit):
            result = uploader.run([first, second], {"tid": 138, "copyright": 1})
        self.assertEqual(result["state"], "published")
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["videos"], [{"filename": "part-one"}, {"filename": "part-two"}])
        self.assertIn("分两 p", payloads[0]["title"])
        uploader.close()

    # 测试响应按接口类别返回，真实传输另做小视频验证。
    def test_upos_chunks_csrf_and_duplicate_submission(self):
        video = self.video("2026-10-02_20-15-30_封茗囧菌_聊天_无弹幕_抖音.mp4", data=b"abcdefghijk")
        history = UploadHistory(self.root / "history.db")
        member, upos = SimpleNamespace(), SimpleNamespace()
        calls = []
        def response(data):
            return SimpleNamespace(status_code=200, json=lambda: data)
        def request(session, method, url, **kwargs):
            calls.append((session, method, url, kwargs))
            if url.endswith("/nav"):
                return response({"code": 0, "data": {"isLogin": True, "mid": 123, "uname": "测试账号"}})
            if url.endswith("/preupload"):
                return response({"OK": 1, "endpoint": "//upos-test.bilivideo.com", "upos_uri": "upos://ugc/video.mp4",
                                 "auth": "upload-only-auth", "chunk_size": 4, "biz_id": 99})
            if url.endswith("/add/v3"):
                return response({"code": 0, "data": {"bvid": "BVtest", "aid": 5}})
            if kwargs.get("params", {}).get("uploads") == "":
                return response({"OK": 1, "upload_id": "upload-id"})
            return response({"OK": 1, "key": "/ugc/video.mp4"})
        member.headers, upos.headers = {}, {}
        member.request = lambda method, url, **kw: request("member", method, url, **kw)
        upos.request = lambda method, url, **kw: request("upos", method, url, **kw)
        chunks = []
        def put(url, **kwargs):
            chunks.append(kwargs)
            return SimpleNamespace(status_code=200, text="MULTIPART_PUT_SUCCESS", headers={})
        upos.put = put
        member.close = upos.close = lambda: None
        with patch("toolbox.bili_upload.requests.Session", side_effect=[member, upos]):
            uploader = BiliUploader(cookie="SESSDATA=fake; bili_jct=csrf-test", history=history)
        config = {"tid": 138, "copyright": 1, "auto_publish": True}
        with patch.object(uploader, "_upload_cover", return_value="https://i0.hdslb.com/cover.jpg"):
            result = uploader.run([video], config)
            self.assertEqual(result["state"], "published")
            self.assertEqual([len(chunk["data"]) for chunk in chunks], [4, 4, 3])
            self.assertNotIn("Cookie", chunks[0]["headers"])
            submit = [call for call in calls if call[2].endswith("/add/v3")][0]
            self.assertEqual(submit[3]["json"]["csrf"], "csrf-test")
            self.assertEqual(submit[3]["json"]["videos"][0]["filename"], "ugc/video")
            with self.assertRaises(UploadError):
                uploader.run([video], config)
        stored = json.dumps(history.recent(), ensure_ascii=False)
        self.assertNotIn("csrf-test", stored)
        self.assertNotIn("fake", stored)

    def test_submit_response_loss_is_uncertain_and_cannot_retry(self):
        video = self.video("2026-10-02_20-15-30_封茗囧菌_聊天_无弹幕_抖音.mp4")
        history = UploadHistory(self.root / "history.db")
        uploader = BiliUploader(cookie="SESSDATA=fake; bili_jct=csrf", history=history)
        config = {"tid": 138, "copyright": 1}
        with patch.object(uploader, "check_account", return_value={"mid": 1, "name": "测试"}), \
             patch.object(uploader, "_upload_cover", return_value="cover"), \
             patch.object(uploader, "_upload_video", return_value={"filename": "uploaded"}), \
             patch.object(uploader, "_json", side_effect=UploadError("timeout")):
            with self.assertRaises(UploadError):
                uploader.run([video], config)
            self.assertEqual(history.recent()[0][1], "uncertain")
            with self.assertRaisesRegex(UploadError, "uncertain"):
                uploader.run([video], config)
        uploader.close()


class LivePollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_wait_connect_end_and_stale_status(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(cm, "get_config_dir", return_value=Path(folder)), \
             patch.object(cm, "get_export_dir", return_value=Path(folder) / "download"):
            page = TestPage(SimpleNamespace(work_dir=folder, toast=lambda *args: None))
            page._waiting_live = True
            page._setup_poll_timer()
            self.assertTrue(page._poll_timer.isActive())
            page._on_status("connected", "connected")
            self.qt.processEvents()
            self.assertTrue(page.connected)
            self.assertIsNone(page._poll_timer)
            old = object()
            page.fetcher = SimpleNamespace(stop=lambda: None)
            page._on_status("not_live", "stale", _source=old)
            self.qt.processEvents()
            self.assertTrue(page.connected)
            page._on_status("disconnected", "end")
            self.qt.processEvents()
            self.assertTrue(page._poll_timer.isActive())
            page.poll_btn.setChecked(False)
            page._on_poll_toggle()
            self.assertIsNone(page._poll_timer)
            page.shutdown()
            page.deleteLater()
            self.qt.processEvents()

    def test_organize_migrates_without_upload_even_when_auto_enabled(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(cm, "get_config_dir", return_value=Path(folder) / "config"):
            root = Path(folder)
            (root / "config").mkdir()
            source, work = root / "A", root / "work"
            source.mkdir()
            work.mkdir()
            for name in ("20261002_part1.mp4", "20261002_part2.mp4", "20261003.mp4"):
                path = source / name
                path.write_bytes(b"video")
                os.utime(path, (time.time() - 60, time.time() - 60))
            save_settings({"sources": [str(source)], "auto_upload": True,
                           "upload": {"tid": 138, "copyright": 1, "auto_publish": True}})
            page = OrganizePage(SimpleNamespace(work_dir=str(work), toast=lambda *args: None))
            def wait():
                deadline = time.monotonic() + 5
                while page.busy:
                    self.qt.processEvents()
                    time.sleep(.005)
                    self.assertLess(time.monotonic(), deadline)
                self.qt.processEvents()
            page._scan()
            wait()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                 patch("toolbox.bili_upload.BiliUploader") as uploader:
                page._execute()
                wait()
                uploader.assert_not_called()
            self.assertFalse(list(source.glob("*.mp4")))
            self.assertEqual(len(page._migrated_videos), 3)
            page.close()
            page.deleteLater()
            self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
