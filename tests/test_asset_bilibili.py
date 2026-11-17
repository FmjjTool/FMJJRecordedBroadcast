import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from toolbox import asset_bilibili


class AssetBilibiliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dynamic_type_and_bvid(self):
        item = {"file_path": str(self.root / "post.json")}
        (self.root / "post_raw.json").write_text(json.dumps({
            "type": "DYNAMIC_TYPE_DRAW"}), encoding="utf-8")
        self.assertEqual(asset_bilibili.dynamic_type(item), "draw")

        (self.root / "post_raw.json").write_text(json.dumps({
            "type": "DYNAMIC_TYPE_AV",
            "modules": {"module_dynamic": {"major": {"archive": {"bvid": "BV1xx"}}}}},
        ), encoding="utf-8")
        self.assertEqual(asset_bilibili.dynamic_type(item), "av")
        self.assertEqual(asset_bilibili.av_bvid(item), "BV1xx")

    def test_quality_to_qn(self):
        self.assertEqual(asset_bilibili.quality_to_qn("8K"), 127)
        self.assertEqual(asset_bilibili.quality_to_qn("1080P"), 80)
        self.assertIsNone(asset_bilibili.quality_to_qn("未知"))

    def test_download_video_and_cover(self):
        video = self.root / "out.mp4"
        calls = {}

        def fake_download(vid, **kwargs):
            calls["vid"] = vid
            calls["kwargs"] = kwargs
            video.write_bytes(b"video")
            return str(video)

        def fake_cover(url, path, headers):
            calls["cover"] = (url, path, headers)
            Path(path).write_bytes(b"cover")

        item = {"video_id": "BV1xx", "title": "标题", "author": "作者",
                "publish_time": "2026-11-17", "cover_url": "//i.example/cover.jpg"}
        with patch("bilibili_download.download_bilibili_by_bvid", side_effect=fake_download), \
                patch("media_downloader._download_one", side_effect=fake_cover):
            path = asset_bilibili.download_video(
                item, qn=80, cookie="fake", out_dir=str(self.root), threads=2)

        self.assertEqual(path, str(video))
        self.assertEqual(calls["vid"], "BV1xx")
        self.assertEqual(calls["kwargs"]["quality"], 80)
        self.assertEqual(calls["kwargs"]["threads"], 2)
        self.assertEqual(calls["cover"][0], "https://i.example/cover.jpg")
        self.assertTrue(calls["cover"][1].endswith(".jpg"))

    def test_download_video_without_bvid_raises(self):
        with self.assertRaises(ValueError):
            asset_bilibili.download_video({"video_id": "dyn_1"}, qn=None,
                                          cookie="", out_dir=str(self.root))


if __name__ == "__main__":
    unittest.main()
