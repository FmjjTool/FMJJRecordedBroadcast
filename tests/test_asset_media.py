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


if __name__ == "__main__":
    unittest.main()
