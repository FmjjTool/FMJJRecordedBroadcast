import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from toolbox.tcdoc import bridge


class DurationTextTests(unittest.TestCase):
    def test_missing_duration_defaults_to_zero(self):
        for value in (None, "", 0, "0", "abc", -5):
            self.assertEqual(bridge._duration_text(value), "0")

    def test_duration_formatting(self):
        self.assertEqual(bridge._duration_text(15), "15秒")
        self.assertEqual(bridge._duration_text(65), "1分5秒")
        self.assertEqual(bridge._duration_text(3660), "1小时1分")


if __name__ == "__main__":
    unittest.main()
