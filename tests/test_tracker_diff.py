import unittest

from toolbox import tracker_diff


class TrackerDiffTests(unittest.TestCase):
    def test_diff_directions_and_rename(self):
        prev = {"name": "旧名", "fans": 100, "following": "20", "likes": 0}
        curr = {"name": "新名", "fans": 112, "following": 15, "likes": "x"}
        items = tracker_diff.diff_items(
            prev, curr, [("粉丝", "fans"), ("关注", "following"), ("获赞", "likes")])
        self.assertEqual(items[0], {"text": "🔄 更名 旧名→新名", "direction": "note"})
        self.assertEqual(items[1]["direction"], "up")
        self.assertEqual(items[1]["delta"], 12)
        self.assertEqual(items[2]["direction"], "down")
        self.assertEqual(items[2]["delta"], -5)
        self.assertEqual(len(items), 3)
        self.assertNotIn("获赞", "".join(item["text"] for item in items))

    def test_missing_values_are_skipped(self):
        items = tracker_diff.diff_items(
            {"fans": None}, {"fans": 10}, [("粉丝", "fans")])
        self.assertEqual(items, [])

    def test_num_delta_uses_field_priority(self):
        self.assertEqual(
            tracker_diff.num_delta({"video_count": 3}, {"weibo_count": 8},
                                   ["video_count", "weibo_count"]), 5)
        self.assertIsNone(
            tracker_diff.num_delta({"video_count": ""}, {"weibo_count": 8},
                                   ["video_count", "weibo_count"]))
        self.assertIsNone(
            tracker_diff.num_delta({"fans": 1}, {"fans": "x"}, ["fans"]))


if __name__ == "__main__":
    unittest.main()
