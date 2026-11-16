"""性能记录的边界、追踪归属与报告口径；不依赖 Qt。"""

import json
import sys
import tracemalloc
import unittest

from toolbox.performance import PerformanceRecorder, process_memory, source_location


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.recorder = PerformanceRecorder()

    def tearDown(self):
        self.recorder.stop()

    def test_disabled_and_bounded_recording(self):
        rec = self.recorder
        rec.event("mark", "ignored")
        self.assertIsNone(rec.sample("home", 0, [], []))
        self.assertFalse(rec.report()["samples"])
        rec.MAX_SAMPLES = 3
        rec.MAX_EVENTS = 4
        from collections import deque
        rec.samples = deque(maxlen=3)
        rec.events = deque(maxlen=4)
        rec.start()
        for i in range(6):
            rec.sample("home", i, [], [])
            rec.event("mark", "test")
        report = rec.report()
        self.assertEqual(len(report["samples"]), 3)
        self.assertEqual(report["dropped_samples"], 3)
        self.assertEqual(report["dropped_events"], 2)
        self.assertIsNone(report["samples"][-1]["python_bytes"])
        self.assertFalse(tracemalloc.is_tracing())
        json.dumps(report)

    def test_previous_session_callback_does_not_pollute_new_recording(self):
        rec = self.recorder
        rec.start()
        old = rec.task_started(lambda: None)
        rec.stop()
        rec.start()
        rec.task_finished(old, "worker", 99)
        self.assertFalse(rec.events)
        token = rec.task_started(lambda: None)
        rec.task_finished(token, "worker", 12)
        self.assertEqual(len(rec.running), 1)
        rec.task_finished(token, "callback", 4, True)
        self.assertFalse(rec.running)
        self.assertTrue(rec.events[-1]["failed"])

    def test_allocation_snapshot_growth_and_stop(self):
        rec = self.recorder
        rec.start(trace=True)
        first = rec.memory_snapshot(rec.session)
        self.assertTrue(first["baseline"])
        allocation = [bytearray(2048) for _ in range(600)]
        second = rec.memory_snapshot(rec.session)
        entries = [entry for entry in second["growth"] if "test_performance.py" in entry["location"]]
        self.assertGreater(sum(entry["bytes"] for entry in entries), 1_000_000)
        self.assertEqual(len(allocation), 600)
        rec.stop()
        self.assertFalse(tracemalloc.is_tracing())
        self.assertTrue(rec.report()["python_tracing"])
        with self.assertRaises(RuntimeError):
            rec.memory_snapshot(rec.session)

    def test_external_tracing_is_preserved(self):
        tracemalloc.start()
        try:
            with self.assertRaises(RuntimeError):
                self.recorder.start(trace=True)
            self.recorder.stop()
            self.assertTrue(tracemalloc.is_tracing())
        finally:
            tracemalloc.stop()

    def test_memory_and_source_labels(self):
        memory = process_memory()
        if sys.platform == "win32":
            self.assertGreater(memory["rss"], 0)
            self.assertGreater(memory["private"], 0)
        self.assertEqual(source_location(r"C:\Users\someone\project\src\toolbox\foo.py", 12),
                         "src/toolbox/foo.py:12")
        self.assertNotIn("someone", source_location(r"C:\Users\someone\libs\other.py", 5))


if __name__ == "__main__":
    unittest.main()
