import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from toolbox import asset_media


class AssetMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _info(self, cover="https://i.example/a.jpg"):
        return SimpleNamespace(platform="douyin", title="标题", author="作者",
                               publish_time="2026-11-17 12:00:00",
                               video_id="v1", cover_url=cover)

    def test_save_cover_downloads_to_expected_dir(self):
        called = {}

        def fake_download(url, path, headers):
            called["url"] = url
            called["path"] = path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"cover")
            return path, True

        with patch("media_downloader._sanitize_filename", side_effect=lambda s, *a: s), \
                patch("media_downloader._PLATFORM_CN", {"douyin": "抖音"}), \
                patch("media_downloader.MEDIA_DIR_SUFFIX", "_媒体"), \
                patch("media_downloader._url_path", side_effect=lambda u: u), \
                patch("media_downloader._download_one", side_effect=fake_download), \
                patch("toolbox.utils.claim_output_stem", side_effect=lambda d, s, vid: s):
            path = asset_media.save_video_cover(self._info(), str(self.root))

        self.assertTrue(path.endswith(".jpg"))
        self.assertIn("抖音_媒体", path)
        self.assertEqual(called["url"], "https://i.example/a.jpg")
        self.assertTrue(Path(path).is_file())

    def test_missing_cover_returns_none(self):
        self.assertIsNone(asset_media.save_video_cover(self._info(cover=""), str(self.root)))

    def test_download_item_media_video_and_cover(self):
        seen = {}

        def fake_download_media(infos, **kwargs):
            seen["infos"] = infos
            seen["kwargs"] = kwargs
            return {"videos": 1, "paths": []}

        item = {"platform": "douyin", "video_id": "v1", "title": "标题",
                "author": "作者", "publish_time": "2026-11-17",
                "cover_url": "https://i.example/c.jpg"}
        with patch("media_downloader.download_media", side_effect=fake_download_media), \
                patch("media_downloader._is_direct_media_url", return_value=True), \
                patch("toolbox.asset_media.save_video_cover", return_value="/tmp/c.jpg"):
            result = asset_media.download_item_media(
                item, {"video_url": "https://v.example/v.mp4", "raw_data": {}},
                base_dir=str(self.root), threads=3)
        self.assertEqual(result["videos"], 1)
        self.assertEqual(result["covers"], 1)
        self.assertTrue(seen["kwargs"]["download_videos"])
        self.assertEqual(seen["kwargs"]["threads"], 3)

    def test_download_item_media_note_images(self):
        seen = {}

        def fake_download_media(infos, **kwargs):
            seen["kwargs"] = kwargs
            return {"images": 2, "paths": []}

        item = {"platform": "douyin", "video_id": "note1", "title": "图文"}
        with patch("media_downloader.download_media", side_effect=fake_download_media), \
                patch("media_downloader._is_direct_media_url", return_value=True):
            result = asset_media.download_item_media(
                item, {"video_url": "old", "note_images": ["a", "b"]},
                base_dir=str(self.root))
        self.assertEqual(result["images"], 2)
        self.assertTrue(seen["kwargs"]["download_images"])
        self.assertFalse(seen["kwargs"]["download_videos"])

    def test_download_item_media_rejects_missing_or_indirect_url(self):
        item = {"platform": "xiaohongshu", "video_id": "n1"}
        with patch("media_downloader._is_direct_media_url", return_value=False):
            with self.assertRaises(ValueError):
                asset_media.download_item_media(
                    item, {"video_url": "https://www.xiaohongshu.com/item/n1"},
                    base_dir=str(self.root))
        with self.assertRaises(ValueError):
            asset_media.download_item_media(item, {"video_url": ""},
                                            base_dir=str(self.root))


if __name__ == "__main__":
    unittest.main()
