"""透明画布、礼物结算、持久设置及回放同步的隔离回归。"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QSpinBox
from gui_qt import danmaku_overlay as overlay
from toolbox import config_manager as cm
from gui_qt.pages.douyin_danmaku import DouyinDanmakuPage


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-overlay-test-")
        self.root = Path(self.temp.name)
        self.patches = [patch.object(overlay, "get_config_dir", return_value=self.root),
                        patch.object(cm, "get_config_dir", return_value=self.root),
                        patch.object(cm, "get_export_dir", return_value=self.root / "download")]
        for item in self.patches:
            item.start()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if isinstance(window, DouyinDanmakuPage):
                window.shutdown()
            window.close()
            window.deleteLater()
        self.qt.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def window(self):
        window = overlay.DanmakuOverlay()
        self.windows.append(window)
        window.show()
        self.qt.processEvents()
        return window

    def test_center_is_transparent_at_two_canvas_sizes(self):
        window = self.window()
        for width, height in ((1280, 720), (800, 450)):
            window.resize(width, height)
            self.qt.processEvents()
            picture = window.grab().toImage()
            self.assertEqual(picture.pixelColor(width // 2, height // 2).alpha(), 0)
            self.assertGreater(picture.pixelColor(width - 5, height // 2).alpha(), 0)
            self.assertEqual(window.canvas.geometry().right(), width - 1)
            self.assertFalse(window.findChildren(QSpinBox))
        window.hide()
        self.assertFalse(window.timer.isActive())

    def test_clock_uses_live_epoch_and_monotonic_then_freezes(self):
        window = self.window()
        self.assertEqual(window.clock_text(), "等待直播时间")
        with patch.object(overlay.time, "monotonic", return_value=10):
            window.set_active(True)
            window.add_message("弹幕", "你好", {"nickname": "观众", "content": "你好", "ts": 1778242602000})
        with patch.object(overlay.time, "monotonic", return_value=12):
            self.assertEqual(window.clock_text(), "2026-05-08 20:16:44")
            window.set_active(False)
        with patch.object(overlay.time, "monotonic", return_value=90):
            self.assertEqual(window.clock_text(), "2026-05-08 20:16:44")
        window.set_clock(1778242602000, external=True)
        window.set_active(True)
        self.assertEqual(window.clock_text(), "2026-05-08 20:16:42")

    def test_gift_combo_deduplicates_progress_end_and_restarts(self):
        ledger = overlay.GiftLedger()
        def gift(count, seconds, end=0):
            return dict(nickname="观众", giftName="小心心", giftPrice=10, giftCount=count,
                        ts=1778242602000 + seconds * 1000, repeatEnd=end)
        for message in (gift(7, 0), gift(19, 1), gift(19, 1), gift(34, 2), gift(34, 3, 1), gift(34, 4, 1)):
            ledger.add(message)
        self.assertEqual(ledger.rank["观众"]["giftCount"], 34)
        self.assertEqual(ledger.rank["观众"]["totalValue"], 340)
        ledger.add(gift(1, 5))
        ledger.add(gift(2, 6, 1))
        self.assertEqual(ledger.rank["观众"]["giftCount"], 36)
        ledger.add(gift(3, 30))
        self.assertEqual(ledger.rank["观众"]["giftCount"], 39)

    def test_bili_gift_batches_are_independent_and_unknown_price_is_zero(self):
        ledger = overlay.GiftLedger("bili")
        message = dict(nickname="观众", giftName="礼物", giftCount=3, giftPrice=1000, repeatEnd=1, ts=1778242602000)
        ledger.add(message)
        ledger.add(message)
        self.assertEqual(ledger.rank["观众"]["giftCount"], 6)
        ledger.add({**message, "message_id": "same-event"})
        ledger.add({**message, "message_id": "same-event"})
        self.assertEqual(ledger.rank["观众"]["giftCount"], 9)
        ledger.add(dict(nickname="未知价格", giftName="灯牌", giftCount=1))
        self.assertEqual(ledger.rank["未知价格"]["totalValue"], 0)

    def test_external_ranking_is_snapshot_and_chat_survives_pause(self):
        window = self.window()
        ranks = {"A": dict(totalValue=10, giftCount=5), "B": dict(totalValue=20, giftCount=1)}
        window.set_gift_rank(ranks)
        ranks["B"]["totalValue"] = 0
        self.assertEqual(window.top_gifts()[0][0], "B")
        window.add_message("弹幕", "<文字>", {"nickname": "<昵称>", "content": "<文字>"}, rank=False)
        window.set_active(False)
        self.assertEqual(len(window.canvas.messages), 1)
        window.reset_session()
        self.assertEqual(window.top_gifts(), [])
        self.assertEqual(len(window.canvas.messages), 0)

    def test_footer_and_display_options_persist_without_cookies(self):
        window = self.window()
        def save(dialog):
            dialog.findChild(QPlainTextEdit).setPlainText("b站：测试\n抖子：测试")
            dialog.findChildren(QSpinBox)[0].setValue(18)
            return QDialog.Accepted
        with patch.object(QDialog, "exec", save):
            window.edit_settings()
        second = self.window()
        self.assertEqual(second.options["footer"], "b站：测试\n抖子：测试")
        self.assertEqual(second.canvas.font_size, 18)
        self.assertEqual(set(self.root.iterdir()), {self.root / "danmaku_overlay.json"})

    def test_replay_seek_pause_finish_sync_clock_and_rank(self):
        page = DouyinDanmakuPage(SimpleNamespace(work_dir=str(self.root), toast=lambda *args: None))
        self.windows.append(page)
        first = 1778242602000
        casts = [dict(type="WebcastGiftMessage", nickname="观众", giftName="小心心",
                      giftPrice=10, giftCount=2, repeatEnd=1, ts=first),
                 dict(type="WebcastChatMessage", nickname="观众", content="十秒之后", ts=first + 10000)]
        page._start_replay(dict(casts=casts))
        page._show_danmaku_overlay()
        page._seek_replay(5000)
        self.assertEqual(page._overlay.clock_text(), "2026-05-08 20:16:47")
        self.assertEqual(page._overlay.top_gifts()[0][1]["giftCount"], 2)
        page._replay["start_real"] = time.time() - 1
        page._pause_replay()
        self.assertGreater(page._replay["paused_at"], 5900)
        frozen = page._overlay.clock_ms()
        page._replay["start_real"] -= 60
        self.assertEqual(page._overlay.clock_ms(), frozen)
        page._seek_replay(0)
        self.assertEqual(page._overlay.top_gifts()[0][1]["giftCount"], 2)
        self.assertEqual(page._overlay.clock_text(), "2026-05-08 20:16:42")
        page._finish_replay()
        self.assertEqual(page._overlay.clock_text(), "2026-05-08 20:16:52")


if __name__ == "__main__":
    unittest.main()
