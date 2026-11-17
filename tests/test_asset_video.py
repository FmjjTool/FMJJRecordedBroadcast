import unittest
from types import SimpleNamespace
from unittest.mock import patch

from toolbox import asset_video


class AssetVideoTests(unittest.TestCase):
    def test_douyin_video_and_note(self):
        item = {"platform": "douyin", "video_id": "123"}
        with patch("multi_downloader.parse_video",
                   return_value={"video_url": "fresh"}):
            result = asset_video.resolve_video_source(
                item, video_url="old", video_id="123")
        self.assertEqual(result["video_url"], "fresh")

        with patch("multi_downloader.parse_video",
                   return_value={"is_note": True, "images": ["a", "b"]}):
            result = asset_video.resolve_video_source(
                item, video_url="old", video_id="123")
        self.assertEqual(result["note_images"], ["a", "b"])

    def test_weibo_refreshes_url_and_raw_data(self):
        parser = SimpleNamespace(parse=lambda url: SimpleNamespace(
            video_url="fresh-weibo", raw_data={"page": 1}))
        item = {"platform": "weibo", "raw_url": "https://m.weibo.cn/detail/123"}
        with patch("platforms.get_parser", return_value=parser):
            result = asset_video.resolve_video_source(
                item, video_url="old", video_id="123")
        self.assertEqual(result["video_url"], "fresh-weibo")
        self.assertEqual(result["raw_data"], {"page": 1})

    def test_xiaohongshu_note_images(self):
        parser = SimpleNamespace(parse=lambda url: SimpleNamespace(
            video_url="", images=["img1"]))
        item = {"platform": "xiaohongshu", "raw_url": ""}
        with patch("platforms.get_parser", return_value=parser):
            result = asset_video.resolve_video_source(
                item, video_url="old", video_id="note1")
        self.assertEqual(result["note_images"], ["img1"])
        self.assertEqual(result["video_url"], "old")

    def test_parse_failure_keeps_original(self):
        item = {"platform": "douyin", "video_id": "123"}
        with patch("multi_downloader.parse_video", side_effect=RuntimeError("x")):
            result = asset_video.resolve_video_source(
                item, video_url="old", video_id="123")
        self.assertEqual(result["video_url"], "old")
        self.assertIsNone(result["note_images"])


if __name__ == "__main__":
    unittest.main()
