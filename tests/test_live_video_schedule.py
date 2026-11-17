import unittest
from types import SimpleNamespace

from toolbox.live_video import VideoStreamPlayer


class LiveVideoScheduleTests(unittest.TestCase):
    def test_injected_scheduler_drives_polling(self):
        calls = []
        player = VideoStreamPlayer(fps=10, schedule=lambda ms, cb: calls.append((ms, cb)))
        player._proc = SimpleNamespace(poll=lambda: None)
        player._latest = None
        player._poll_status()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 100)
        self.assertEqual(calls[0][1], player._poll_status)

    def test_closed_player_does_not_schedule(self):
        calls = []
        player = VideoStreamPlayer(schedule=lambda ms, cb: calls.append(ms))
        player._closed = True
        player._poll_status()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
